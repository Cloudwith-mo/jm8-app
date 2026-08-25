import { useEffect, useMemo, useState } from "react";
import { BookOpen, CalendarDays, Image as ImageIcon, Menu, Upload, UserRound, X } from "lucide-react";
import ArchiveSidebar, {
  type ArchiveSection,
} from "../components/layout/ArchiveSidebar";
import ArchiveTopbar from "../components/layout/ArchiveTopbar";
import EntryCard from "../components/archive/EntryCard";
import EntryDetailView from "../components/archive/EntryDetailView";
import HomeDashboard from "../components/home/HomeDashboard";
import ActionModal from "../components/archive/ActionModal";
import ToastStack, { type ToastKind, type ToastMessage } from "../components/ui/ToastStack";
import HistoricalJobsPanel from "../components/analysis/HistoricalJobsPanel";
import OcrJobsPanel from "../components/ocr/OcrJobsPanel";
import InsightsOverviewPanel from "../components/insights/InsightsOverviewPanel";
import InsightsTrendsPanel from "../components/insights/InsightsTrendsPanel";
import ReportsPanel from "../components/insights/ReportsPanel";
import AskJm8Panel from "../components/insights/AskJm8Panel";
import AuthStatus from "../components/layout/AuthStatus";
import AuthLandingPage from "./AuthLandingPage";
import {
  BrandMark,
  EmptyState,
  ErrorState,
  IconButton,
  LoadingState,
} from "../components/ui/V2Primitives";
import UsageMeter from "../components/usage/UsageMeter";
import AccountPlanCard from "../components/account/AccountPlanCard";
import type { JournalEntry } from "../types/journal";
import type {
  UsageSnapshot,
} from "../types/usage";
import type {
  AccountEntitlement,
} from "../types/accountEntitlement";
import type { InsightsReportType } from "../types/reports";
import { isReportWindow } from "../api/reportsValidation";
import {
  getCurrentUser,
  handleCognitoCallback,
  loginWithCognito,
  logoutFromCognito,
  signupWithCognito,
  type AuthUser,
} from "../auth/cognito";
import {
  ApiRequestError,
  analyzeEntry,
  createBillingCheckout,
  createBillingPortal,
  createEntry,
  createUploadUrl,
  deleteEntry,
  getAccountEntitlement,
  getEntry,
  getUsage,
  listEntries,
  reviewEntry,
  retryOcrJob,
  runOcr,
  waitForOcrCompletion,
  uploadFileToS3,
} from "../api/client";

type ModalMode = "write" | "upload" | "review" | null;

function getEntrySearchText(entry: JournalEntry) {
  return [
    entry.entryId,
    entry.sourceType,
    entry.status,
    entry.analysisStatus,
    entry.ocrStatus,
    entry.reviewStatus,
    entry.rawText,
    entry.cleanText,
    entry.originalFileName,
    entry.analysis?.sentiment,
    entry.analysis?.mood,
    entry.analysis?.summary,
    entry.analysis?.nextStep,
    ...(entry.analysis?.themes || []),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function getValidEntryDate(entry: JournalEntry) {
  if (!entry.createdAt) return null;
  const date = new Date(entry.createdAt);
  return Number.isNaN(date.getTime()) ? null : date;
}

function groupEntries(entries: JournalEntry[]) {
  const groups = new Map<string, { label: string; months: Map<string, { label: string; entries: JournalEntry[] }> }>();

  for (const entry of entries) {
    const date = getValidEntryDate(entry);
    const yearKey = date ? String(date.getFullYear()) : "undated";
    const monthKey = date ? `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}` : "undated";
    const year = groups.get(yearKey) || { label: date ? String(date.getFullYear()) : "Date unavailable", months: new Map() };
    const month = year.months.get(monthKey) || { label: date ? date.toLocaleString([], { month: "long" }) : "Date unavailable", entries: [] };
    month.entries.push(entry);
    year.months.set(monthKey, month);
    groups.set(yearKey, year);
  }

  return Array.from(groups, ([key, year]) => ({ key, label: year.label, months: Array.from(year.months, ([monthKey, month]) => ({ key: monthKey, ...month })) }));
}

function getEntryRouteId() {
  return new URL(window.location.href).searchParams.get("entry");
}

function getThemeRouteId() {
  const value = new URL(window.location.href).searchParams.get("theme");
  if (value === null) return null;
  return /^theme-[a-f0-9]{8}$/.test(value) ? value : "invalid-theme-route";
}

type ReportRoute = { type: InsightsReportType; window: string | null };

function getReportRoute(): ReportRoute {
  const url = new URL(window.location.href);
  const period = url.searchParams.get("period");
  const type: InsightsReportType = period === "monthly" ? "MONTHLY" : "WEEKLY";
  const candidate = period === "weekly" || period === "monthly" ? url.searchParams.get("window") : null;
  return { type, window: candidate && isReportWindow(type, candidate) ? candidate : null };
}

const ROUTABLE_SECTIONS: ArchiveSection[] = [
  "home", "archive", "insights", "themes", "reports", "askJm8", "ocrJobs", "analysisJobs",
];

function getSectionRoute(): ArchiveSection {
  const url = new URL(window.location.href);
  if (url.searchParams.get("entry")) return "archive";
  const view = url.searchParams.get("view");
  return ROUTABLE_SECTIONS.find((section) => section === view) || "home";
}

function setSectionRoute(section: ArchiveSection, mode: "push" | "replace" = "push") {
  const url = new URL(window.location.href);
  url.searchParams.delete("entry");
  if (section !== "themes") url.searchParams.delete("theme");
  if (section !== "reports") {
    url.searchParams.delete("period");
    url.searchParams.delete("window");
  }
  if (section === "home") url.searchParams.delete("view");
  else url.searchParams.set("view", section);
  window.history[mode === "push" ? "pushState" : "replaceState"]({}, "", url);
}

function setReportRoute(route: ReportRoute, mode: "push" | "replace" = "push") {
  const url = new URL(window.location.href);
  url.searchParams.delete("entry");
  url.searchParams.delete("theme");
  url.searchParams.set("view", "reports");
  url.searchParams.set("period", route.type === "WEEKLY" ? "weekly" : "monthly");
  if (route.window && isReportWindow(route.type, route.window)) url.searchParams.set("window", route.window);
  else url.searchParams.delete("window");
  window.history[mode === "push" ? "pushState" : "replaceState"]({}, "", url);
}

function setThemeRoute(themeId: string, mode: "push" | "replace" = "push") {
  const url = new URL(window.location.href);
  url.searchParams.delete("entry");
  url.searchParams.set("view", "themes");
  url.searchParams.set("theme", themeId);
  window.history[mode === "push" ? "pushState" : "replaceState"]({}, "", url);
}

function setEntryRoute(entryId: string | null, mode: "push" | "replace" = "push") {
  const url = new URL(window.location.href);
  if (entryId) url.searchParams.set("entry", entryId);
  else url.searchParams.delete("entry");
  window.history[mode === "push" ? "pushState" : "replaceState"]({}, "", url);
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

export default function ArchivePage() {
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [selectedEntry, setSelectedEntry] = useState<JournalEntry | null>(null);
  const [statusMessage, setStatusMessage] = useState("Loading archive...");
  const [modalMode, setModalMode] = useState<ModalMode>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [isEntryDetailOpen, setIsEntryDetailOpen] = useState(false);
  const [isArchiveLoading, setIsArchiveLoading] = useState(true);
  const [archiveError, setArchiveError] = useState("");
  const [isEntryLoading, setIsEntryLoading] = useState(false);
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
  const [activeSection, setActiveSection] =
    useState<ArchiveSection>(() => getSectionRoute());
  const [selectedThemeRouteId, setSelectedThemeRouteId] =
    useState<string | null>(() => getThemeRouteId());
  const [reportRoute, setReportRouteState] = useState<ReportRoute>(() => getReportRoute());

  const [searchQuery, setSearchQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState("newest");
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [authUser, setAuthUser] = useState<AuthUser | null>(getCurrentUser());
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [
    usage,
    setUsage,
  ] = useState<UsageSnapshot | null>(
    null
  );
  const [
    isUsageLoading,
    setIsUsageLoading,
  ] = useState(false);
  const [
    usageError,
    setUsageError,
  ] = useState("");
  const [
    accountEntitlement,
    setAccountEntitlement,
  ] = useState<AccountEntitlement | null>(
    null
  );
  const [
    isEntitlementLoading,
    setIsEntitlementLoading,
  ] = useState(false);
  const [
    entitlementError,
    setEntitlementError,
  ] = useState("");
  const [
    isCheckoutLoading,
    setIsCheckoutLoading,
  ] = useState(false);
  const [
    isPortalLoading,
    setIsPortalLoading,
  ] = useState(false);

  const filteredEntries = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();

    return entries
      .filter((entry) => {
        if (sourceFilter !== "all" && entry.sourceType !== sourceFilter) {
          return false;
        }

        if (statusFilter !== "all") {
          if (statusFilter === "NOT_ANALYZED") {
            return entry.analysisStatus !== "COMPLETED";
          }
          if (statusFilter === "ANALYZED") return entry.analysisStatus === "COMPLETED";
          if (statusFilter === "REVIEWED") return entry.reviewStatus === "REVIEWED";
          if (statusFilter === "OCR_COMPLETED") return entry.ocrStatus === "COMPLETED";
          if (statusFilter === "OCR_FAILED") return entry.ocrStatus === "FAILED";
          return entry.status === statusFilter;
        }

        return true;
      })
      .filter((entry) => {
        if (!query) return true;
        return getEntrySearchText(entry).includes(query);
      })
      .sort((a, b) => {
        const aTime = getValidEntryDate(a)?.getTime() ?? 0;
        const bTime = getValidEntryDate(b)?.getTime() ?? 0;

        if (sortOrder === "oldest") {
          return aTime - bTime;
        }

        return bTime - aTime;
      });
  }, [entries, searchQuery, sourceFilter, statusFilter, sortOrder]);

  const groupedEntries = useMemo(() => groupEntries(filteredEntries), [filteredEntries]);
  const archiveStats = useMemo(() => {
    const datedYears = new Set(entries.map(getValidEntryDate).filter(Boolean).map((date) => date!.getFullYear()));
    return {
      loaded: entries.length,
      years: datedYears.size,
      analyzed: entries.filter((entry) => entry.analysisStatus === "COMPLETED").length,
      scanned: entries.filter((entry) => entry.sourceType === "image").length,
    };
  }, [entries]);

  function dismissToast(id: string) {
    setToasts((currentToasts) => currentToasts.filter((toast) => toast.id !== id));
  }

  function showToast(kind: ToastKind, title: string, message?: string) {
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;

    setToasts((currentToasts) => [
      { id, kind, title, message },
      ...currentToasts.slice(0, 3),
    ]);

    window.setTimeout(() => dismissToast(id), kind === "loading" ? 2600 : 4200);
  }

  function updateStatus(message: string, kind?: ToastKind, toastTitle?: string) {
    setStatusMessage(message);

    if (kind && toastTitle) {
      showToast(kind, toastTitle, message);
    }
  }

  function navigateToSection(
    section: ArchiveSection
  ) {
    if (section === activeSection && !isEntryDetailOpen) {
      setIsMobileNavOpen(false);
      return;
    }
    setActiveSection(section);
    setIsMobileNavOpen(false);
    setSelectedEntry(null);
    setIsEntryDetailOpen(false);
    if (section !== "themes") setSelectedThemeRouteId(null);
    if (section === "reports") {
      const nextReportRoute = getReportRoute();
      setReportRouteState(nextReportRoute);
      setReportRoute(nextReportRoute);
    } else {
      setSectionRoute(section);
    }
  }

  function selectTheme(themeId: string, mode: "push" | "replace" = "push") {
    if (!/^theme-[a-f0-9]{8}$/.test(themeId)) return;
    setSelectedThemeRouteId(themeId);
    setActiveSection("themes");
    setThemeRoute(themeId, mode);
  }

  function selectReport(route: ReportRoute, mode: "push" | "replace" = "push") {
    const safeRoute = {
      type: route.type,
      window: route.window && isReportWindow(route.type, route.window) ? route.window : null,
    };
    setReportRouteState(safeRoute);
    setActiveSection("reports");
    setReportRoute(safeRoute, mode);
  }

  async function refreshUsage({
    silent = false,
  }: {
    silent?: boolean;
  } = {}) {
    setIsUsageLoading(true);

    if (!silent) {
      setUsageError("");
    }

    try {
      const result = await getUsage();

      setUsage(result.usage);
      setUsageError("");
    } catch (error) {
      const message = getErrorMessage(
        error,
        "Monthly usage could not be loaded."
      );

      setUsageError(message);

      if (!silent) {
        showToast(
          "error",
          "Usage unavailable",
          message
        );
      }
    } finally {
      setIsUsageLoading(false);
    }
  }

  async function refreshEntitlement({
    silent = false,
  }: {
    silent?: boolean;
  } = {}) {
    setIsEntitlementLoading(true);

    if (!silent) {
      setEntitlementError("");
    }

    try {
      const result =
        await getAccountEntitlement();

      setAccountEntitlement(
        result.entitlement
      );
      setEntitlementError("");
    } catch (error) {
      const message = getErrorMessage(
        error,
        (
          "Account plan details "
          + "could not be loaded."
        )
      );

      setEntitlementError(message);

      if (!silent) {
        showToast(
          "error",
          "Plan unavailable",
          message
        );
      }
    } finally {
      setIsEntitlementLoading(false);
    }
  }

  async function handleUpgrade() {
    if (isCheckoutLoading) {
      return;
    }

    setIsCheckoutLoading(true);

    try {
      const requestToken =
        crypto.randomUUID();

      const result =
        await createBillingCheckout(
          requestToken
        );

      const checkoutUrl =
        result.checkout.checkoutUrl
          ?.trim();

      if (!checkoutUrl) {
        throw new Error(
          (
            "Checkout URL was missing "
            + "from the billing response."
          )
        );
      }

      window.location.assign(
        checkoutUrl
      );
    } catch (error) {
      const message = getErrorMessage(
        error,
        (
          "Could not start Stripe "
          + "checkout."
        )
      );

      showToast(
        "error",
        "Checkout failed",
        message
      );
    } finally {
      setIsCheckoutLoading(false);
    }
  }

  async function handleManageSubscription() {
    if (isPortalLoading) {
      return;
    }

    setIsPortalLoading(true);

    try {
      const result =
        await createBillingPortal();

      const portalUrl =
        result.portal.billingPortalUrl
          ?.trim();

      if (!portalUrl) {
        throw new Error(
          (
            "Portal URL was missing "
            + "from the billing response."
          )
        );
      }

      window.location.assign(
        portalUrl
      );
    } catch (error) {
      const message = getErrorMessage(
        error,
        (
          "Could not open the Stripe "
          + "customer portal."
        )
      );

      showToast(
        "error",
        "Portal failed",
        message
      );
    } finally {
      setIsPortalLoading(false);
    }
  }

  async function refreshEntries(nextSelectedEntryId?: string) {
    const result = await listEntries();
    setEntries(result.entries);

    if (nextSelectedEntryId) {
      const selected = await getEntry(nextSelectedEntryId);
      setSelectedEntry(selected.entry);
      setIsEntryDetailOpen(true);
      setActiveSection("archive");
      if (getEntryRouteId() !== nextSelectedEntryId) {
        setEntryRoute(nextSelectedEntryId, "replace");
      }
    }
  }

  async function openEntry(entryId: string, updateRoute = true) {
    setIsEntryLoading(true);
    try {
      const result = await getEntry(entryId);
      setSelectedEntry(result.entry);
      setIsEntryDetailOpen(true);
      setActiveSection("archive");
      if (updateRoute) setEntryRoute(entryId);
      updateStatus("Entry loaded.");
    } catch (error) {
      updateStatus(getErrorMessage(error, "Failed to open entry."), "error", "Could not open entry");
      if (!updateRoute) setEntryRoute(null, "replace");
    } finally {
      setIsEntryLoading(false);
    }
  }

  function closeEntryDetail() {
    setIsEntryDetailOpen(false);
    setSelectedEntry(null);
    setActiveSection("archive");
    setSectionRoute("archive", "push");
  }

  async function handleContinueWriting(entryId: string) {
    setIsBusy(true);
    try {
      const result = await getEntry(entryId);
      setSelectedEntry(result.entry);
      setModalMode("review");
    } catch (error) {
      updateStatus(getErrorMessage(error, "Failed to open the entry editor."), "error", "Could not continue entry");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleCreateText(text: string) {
    setIsBusy(true);
    updateStatus("Saving typed journal entry...", "loading", "Saving entry");

    try {
      const result = await createEntry(text);
      await refreshEntries(result.entry.entryId);
      updateStatus("Typed entry created.", "success", "Entry saved");
      setModalMode(null);
    } catch (error) {
      updateStatus(getErrorMessage(error, "Failed to create entry."), "error", "Entry failed");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleUploadImage(file: File) {
    setIsBusy(true);
    updateStatus("Creating secure S3 upload URL...", "loading", "Preparing upload");

    try {
      const upload = await createUploadUrl(file.name, file.type || "image/jpeg");

      updateStatus("Uploading image to S3...", "loading", "Uploading image");
      await uploadFileToS3(upload.upload.uploadUrl, file);

      updateStatus(
        "Starting background OCR...",
        "loading",
        "OCR starting"
      );

      const ocrJob = await runOcr(upload.upload.entryId);

      updateStatus(
        "OCR is processing securely in the background...",
        "loading",
        "OCR processing"
      );

      const ocrResult = await waitForOcrCompletion(
        ocrJob.job.entryId
      );

      await refreshEntries(ocrResult.entry.entryId);

      updateStatus(
        "Image uploaded and OCR completed.",
        "success",
        "OCR complete"
      );
      setModalMode(null);
    } catch (error) {
      updateStatus(getErrorMessage(error, "Upload/OCR failed."), "error", "Upload failed");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleSaveReview(entryId: string, cleanText: string) {
    setIsBusy(true);
    updateStatus("Saving reviewed transcript...", "loading", "Saving review");

    try {
      const result = await reviewEntry(entryId, cleanText);
      await refreshEntries(result.entry.entryId);
      updateStatus("Review saved.", "success", "Review saved");
      setModalMode(null);
    } catch (error) {
      updateStatus(getErrorMessage(error, "Failed to save review."), "error", "Review failed");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleAnalyzeSelected() {
    if (!selectedEntry) return;

    if (
      usage
      && !usage.operations
        .entryAnalysis.allowed
    ) {
      updateStatus(
        (
          "Your monthly entry-analysis "
          + "allowance has been reached."
        ),
        "error",
        "Analysis limit reached"
      );
      return;
    }

    setIsBusy(true);
    updateStatus("Analyzing selected entry...", "loading", "Analysis running");

    try {
      const result = await analyzeEntry(selectedEntry.entryId);

      await Promise.all([
        refreshEntries(
          result.entry.entryId
        ),
        refreshUsage({
          silent: true,
        }),
      ]);

      updateStatus("Analysis completed.", "success", "Analysis complete");
    } catch (error) {
      if (
        error instanceof ApiRequestError
        && error.status === 429
      ) {
        await refreshUsage({
          silent: true,
        });
      }

      updateStatus(getErrorMessage(error, "Analysis failed."), "error", "Analysis failed");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleRetrySelectedOcr() {
    if (!selectedEntry) return;
    setIsBusy(true);
    updateStatus("Retrying OCR for this journal page...", "loading", "OCR retrying");
    try {
      await retryOcrJob(selectedEntry.entryId);
      await refreshEntries(selectedEntry.entryId);
      updateStatus("OCR retry started.", "success", "OCR retry started");
    } catch (error) {
      updateStatus(getErrorMessage(error, "OCR retry failed."), "error", "OCR retry failed");
    } finally {
      setIsBusy(false);
    }
  }

  function clearFilters() {
    setSearchQuery("");
    setSourceFilter("all");
    setStatusFilter("all");
    setSortOrder("newest");
    updateStatus("Filters cleared.", "info", "Filters reset");
  }

  function getSelectedTranscript() {
    return selectedEntry?.cleanText || selectedEntry?.rawText || "";
  }

  async function handleCopyTranscript() {
    const transcript = getSelectedTranscript();

    if (!selectedEntry || !transcript.trim()) {
      updateStatus("No transcript available to copy.", "error", "Nothing to copy");
      return;
    }

    try {
      await navigator.clipboard.writeText(transcript);
      updateStatus("Transcript copied to clipboard.", "success", "Copied");
    } catch {
      updateStatus("Could not copy transcript.", "error", "Copy failed");
    }
  }

  function handleExportTranscript() {
    const transcript = getSelectedTranscript();

    if (!selectedEntry || !transcript.trim()) {
      updateStatus("No transcript available to export.", "error", "Nothing to export");
      return;
    }

    const createdDate = selectedEntry.createdAt
      ? new Date(selectedEntry.createdAt).toISOString().slice(0, 10)
      : "unknown-date";

    const filename = `jm8-entry-${createdDate}-${selectedEntry.entryId}.txt`;

    const fileBody = [
      "JournalM8 Entry Export",
      "======================",
      "",
      `Entry ID: ${selectedEntry.entryId}`,
      `Created: ${selectedEntry.createdAt || "Unknown"}`,
      `Source: ${selectedEntry.sourceType || "Unknown"}`,
      `Status: ${selectedEntry.status || "Unknown"}`,
      "",
      "Transcript",
      "----------",
      transcript,
    ].join("\n");

    const blob = new Blob([fileBody], { type: "text/plain;charset=utf-8" });
    const url = window.URL.createObjectURL(blob);

    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();

    window.URL.revokeObjectURL(url);
    updateStatus("Transcript exported as a text file.", "success", "Export ready");
  }

  function handleDownloadImage() {
    if (!selectedEntry?.imagePreviewUrl) {
      updateStatus("No image is available for this entry.", "error", "No image found");
      return;
    }

    const anchor = document.createElement("a");
    anchor.href = selectedEntry.imagePreviewUrl;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    anchor.download = `jm8-image-${selectedEntry.entryId}.jpg`;
    anchor.click();

    updateStatus("Image opened in a new tab for download.", "success", "Image ready");
  }

  async function handleDeleteSelected() {
    if (!selectedEntry) {
      updateStatus("No entry selected.", "error", "Delete failed");
      return;
    }

    const shouldDelete = window.confirm(
      "Delete this journal entry? This will remove the entry from your archive."
    );

    if (!shouldDelete) return;

    setIsBusy(true);
    updateStatus("Deleting selected entry...", "loading", "Deleting entry");

    try {
      await deleteEntry(selectedEntry.entryId);

      const remainingEntries = entries.filter(
        (entry) => entry.entryId !== selectedEntry.entryId
      );

      setEntries(remainingEntries);
      setSelectedEntry(null);
      setIsEntryDetailOpen(false);
      setActiveSection("archive");
      setSectionRoute("archive", "replace");

      updateStatus("Entry deleted from archive.", "success", "Entry deleted");
    } catch (error) {
      updateStatus(getErrorMessage(error, "Failed to delete entry."), "error", "Delete failed");
    } finally {
      setIsBusy(false);
    }
  }

  useEffect(() => {
    async function bootstrap() {
      let currentUser: AuthUser | null = null;

      try {
        const callbackUser = await handleCognitoCallback();
        currentUser = callbackUser || getCurrentUser();

        setAuthUser(currentUser);

        if (callbackUser) {
          updateStatus("Signed in with Cognito.", "success", "Login successful");
        }
      } catch (error) {
        updateStatus(getErrorMessage(error, "Cognito login failed."), "error", "Login failed");
      } finally {
        setIsAuthReady(true);
      }

      if (!currentUser) {
        setEntries([]);
        setSelectedEntry(null);
        setUsage(null);
        setUsageError("");
        setAccountEntitlement(null);
        setEntitlementError("");
        updateStatus("Sign in to load your private archive.", "info", "Login required");
        setIsArchiveLoading(false);
        return;
      }

      try {
        const routedEntryId = getEntryRouteId();
        const routedSection = getSectionRoute();
        const routedReport = getReportRoute();
        setActiveSection(routedSection);
        setSelectedThemeRouteId(getThemeRouteId());
        setReportRouteState(routedReport);
        if (routedSection === "reports") setReportRoute(routedReport, "replace");
        await Promise.all([
          refreshEntries(routedEntryId || undefined),
          refreshUsage({
            silent: true,
          }),
          refreshEntitlement({
            silent: true,
          }),
        ]);

        updateStatus("Archive loaded.");
        setArchiveError("");
      } catch (error) {
        const message = getErrorMessage(error, "Failed to load archive.");
        setArchiveError(message);
        updateStatus(message, "error", "Archive failed");
      } finally {
        setIsArchiveLoading(false);
      }
    }

    bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    function handlePopState() {
      const entryId = getEntryRouteId();
      const routedSection = getSectionRoute();
      const routedReport = getReportRoute();
      setActiveSection(routedSection);
      setSelectedThemeRouteId(getThemeRouteId());
      setReportRouteState(routedReport);
      if (routedSection === "reports") setReportRoute(routedReport, "replace");
      if (entryId) void openEntry(entryId, false);
      else {
        setSelectedEntry(null);
        setIsEntryDetailOpen(false);
      }
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isMobileNavOpen) return;

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsMobileNavOpen(false);
      }
    }

    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isMobileNavOpen]);

  if (!isAuthReady || !authUser) {
    return (
      <AuthLandingPage
        isReady={isAuthReady}
        onSignIn={() => {
          void loginWithCognito();
        }}
        onCreateAccount={() => {
          void signupWithCognito();
        }}
      />
    );
  }

  function renderAccountSurface() {
    return (
      <details className="phase2-account-surface">
        <summary aria-label="Open account, plan, and usage">
          <span className="phase2-account-avatar"><UserRound size={17} /></span>
          <span><strong>{accountEntitlement?.plan.label || "Account"}</strong><small>Plan & usage</small></span>
        </summary>
        <div className="phase2-account-popover">
          <AuthStatus user={authUser} isAuthReady={isAuthReady} onLogin={loginWithCognito} onLogout={logoutFromCognito} />
          <AccountPlanCard
            entitlement={accountEntitlement} isLoading={isEntitlementLoading} errorMessage={entitlementError}
            isCheckoutLoading={isCheckoutLoading} isPortalLoading={isPortalLoading}
            onRetry={() => void refreshEntitlement()} onUpgrade={() => void handleUpgrade()}
            onManageSubscription={() => void handleManageSubscription()}
          />
          <UsageMeter usage={usage} isLoading={isUsageLoading} errorMessage={usageError} onRetry={() => void refreshUsage()} />
        </div>
      </details>
    );
  }

  return (
    <main
      className={
        activeSection === "archive" || activeSection === "home"
          ? "jm8-archive-shell"
          : "jm8-archive-shell jobs-view"
      }
    >
      <header className="mobile-app-header">
        <IconButton
          label="Open navigation"
          icon={<Menu size={20} />}
          onClick={() => setIsMobileNavOpen(true)}
          aria-expanded={isMobileNavOpen}
          aria-controls="jm8-mobile-navigation"
        />
        <BrandMark compact />
        <IconButton
          icon={<Upload size={20} />}
          onClick={() => setModalMode("upload")}
          label="Upload journal"
        />
      </header>

      <button
        type="button"
        className={isMobileNavOpen ? "mobile-nav-backdrop visible" : "mobile-nav-backdrop"}
        onClick={() => setIsMobileNavOpen(false)}
        aria-label="Close navigation"
      />

      <div
        id="jm8-mobile-navigation"
        className={isMobileNavOpen ? "mobile-sidebar-shell open" : "mobile-sidebar-shell"}
        aria-hidden={!isMobileNavOpen}
        inert={!isMobileNavOpen}
      >
        <button
          className="mobile-sidebar-close"
          onClick={() => setIsMobileNavOpen(false)}
          aria-label="Close navigation"
        >
          <X size={20} />
        </button>

        <ArchiveSidebar
          user={authUser}
          entries={entries}
          activeSection={activeSection}
          onNavigate={navigateToSection}
          onNewEntry={() => {
            setIsMobileNavOpen(false);
            setModalMode("write");
          }}
          onUpload={() => {
            setIsMobileNavOpen(false);
            setModalMode("upload");
          }}
        />
      </div>

      <ArchiveSidebar
        user={authUser}
        entries={entries}
        activeSection={activeSection}
        onNavigate={navigateToSection}
        onNewEntry={() => setModalMode("write")}
        onUpload={() => setModalMode("upload")}
      />

      <section className="archive-main">
        {authUser && activeSection === "home" && (
          <HomeDashboard
            user={authUser}
            entries={entries}
            isLoading={isArchiveLoading}
            errorMessage={archiveError}
            accountSurface={renderAccountSurface()}
            onOpenEntry={(entryId) => void openEntry(entryId)}
            onContinueWriting={(entryId) => void handleContinueWriting(entryId)}
            onNewEntry={() => setModalMode("write")}
            onUpload={() => setModalMode("upload")}
            onAskJm8={() => navigateToSection("askJm8")}
            onViewArchive={() => navigateToSection("archive")}
          />
        )}

        {authUser &&
          activeSection ===
            "archive" && (
            <>
              {isEntryDetailOpen && selectedEntry ? (
                <EntryDetailView
                  entry={selectedEntry} isBusy={isBusy}
                  analysisAllowed={usage?.operations.entryAnalysis.allowed ?? true}
                  analysisRemaining={usage?.operations.entryAnalysis.remaining ?? null}
                  onBack={closeEntryDetail} onReview={() => setModalMode("review")}
                  onAnalyze={handleAnalyzeSelected} onRetryOcr={handleRetrySelectedOcr}
                  onCopyTranscript={handleCopyTranscript} onExportTranscript={handleExportTranscript}
                  onDownloadImage={handleDownloadImage} onDelete={handleDeleteSelected}
                />
              ) : isEntryLoading ? (
                <LoadingState label="Loading journal entry…" />
              ) : (
                <div className="phase2-archive-view">
                  <header className="phase2-archive-header">
                    <div><h1>Archive</h1><p>All your journals. Every page. Always yours.</p></div>
                    <div className="phase2-archive-header-actions">
                      <button type="button" className="phase2-upload-button" onClick={() => setModalMode("upload")}><Upload size={16} /> Upload</button>
                      {renderAccountSurface()}
                    </div>
                  </header>

                  <ArchiveTopbar
                    searchQuery={searchQuery} sourceFilter={sourceFilter} statusFilter={statusFilter}
                    sortOrder={sortOrder} resultCount={filteredEntries.length} totalCount={entries.length}
                    onSearchChange={setSearchQuery} onSourceFilterChange={setSourceFilter}
                    onStatusFilterChange={setStatusFilter} onSortOrderChange={setSortOrder} onClearFilters={clearFilters}
                  />

                  {isArchiveLoading ? <LoadingState label="Loading your private archive…" /> :
                    archiveError ? <ErrorState title="Archive could not be loaded" description={archiveError} /> :
                    groupedEntries.length === 0 ? <EmptyState
                      title={entries.length ? "No matching entries found" : "Your archive is ready for its first entry"}
                      description={entries.length ? "Try clearing filters or searching for another keyword." : "Upload a journal page or create a typed entry to begin."}
                    /> : (
                      <section className="phase2-archive-groups" aria-label="Journal entries grouped by year and month">
                        {groupedEntries.map((year) => (
                          <details className="phase2-year-group" key={year.key} open>
                            <summary><span>{year.label}</span><small>{year.months.reduce((count, month) => count + month.entries.length, 0)} entries</small></summary>
                            <div className="phase2-year-content">
                              {year.months.map((month) => (
                                <section className="phase2-month-group" key={month.key} aria-labelledby={`month-${year.key}-${month.key}`}>
                                  <h2 id={`month-${year.key}-${month.key}`}>{month.label}</h2>
                                  <div className="phase2-entry-grid">
                                    {month.entries.map((entry) => <EntryCard key={entry.entryId} entry={entry}
                                      isSelected={selectedEntry?.entryId === entry.entryId} onClick={() => void openEntry(entry.entryId)} />)}
                                  </div>
                                </section>
                              ))}
                            </div>
                          </details>
                        ))}
                      </section>
                    )}

                  {!isArchiveLoading && !archiveError && entries.length > 0 && (
                    <dl className="phase2-archive-stats" aria-label="Statistics for currently loaded archive entries">
                      <div><BookOpen size={19} /><dt>Loaded entries</dt><dd>{archiveStats.loaded}</dd></div>
                      <div><CalendarDays size={19} /><dt>Years represented</dt><dd>{archiveStats.years}</dd></div>
                      <div><ImageIcon size={19} /><dt>Scanned pages</dt><dd>{archiveStats.scanned}</dd></div>
                      <div><span className="phase2-stat-spark">✦</span><dt>Analyzed entries</dt><dd>{archiveStats.analyzed}</dd></div>
                    </dl>
                  )}
                  <div className="archive-status" role="status">{isBusy ? `Working: ${statusMessage}` : statusMessage}</div>
                </div>
              )}
            </>
          )}

        {authUser && activeSection !== "archive" && activeSection !== "home" && (
          <div className="phase2-secondary-account-row">{renderAccountSurface()}</div>
        )}

        {authUser &&
          activeSection ===
            "insights" && (
            <InsightsOverviewPanel
              onNotify={showToast}
            />
          )}

        {authUser &&
          activeSection ===
            "themes" && (
            <InsightsTrendsPanel
              onNotify={showToast}
              entries={entries}
              selectedThemeId={selectedThemeRouteId}
              onSelectTheme={selectTheme}
              onOpenEntry={(entryId) => void openEntry(entryId)}
            />
          )}

        {authUser &&
          activeSection ===
            "reports" && (
            <ReportsPanel
              onNotify={showToast}
              reportType={reportRoute.type}
              reportWindow={reportRoute.window}
              onNavigate={selectReport}
            />
          )}

        {authUser &&
          activeSection ===
            "askJm8" && (
            <AskJm8Panel
              onNotify={showToast}
              usage={
                usage?.operations.askJm8
              }
              onUsageChanged={() =>
                refreshUsage({
                  silent: true,
                })
              }
            />
          )}

        {authUser &&
          activeSection ===
            "ocrJobs" && (
            <OcrJobsPanel
              onNotify={showToast}
            />
          )}

        {authUser &&
          activeSection ===
            "analysisJobs" && (
            <HistoricalJobsPanel
              onNotify={showToast}
            />
          )}
      </section>

      <ActionModal
        mode={modalMode}
        entry={selectedEntry}
        isBusy={isBusy}
        onClose={() => setModalMode(null)}
        onCreateText={handleCreateText}
        onUploadImage={handleUploadImage}
        onSaveReview={handleSaveReview}
      />
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </main>
  );
}
