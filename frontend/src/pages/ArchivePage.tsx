import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import AccountDataExport from "../components/account/AccountDataExport";
import AccountDeletionDangerZone from "../components/account/AccountDeletionDangerZone";
import type { JournalEntry } from "../types/journal";
import type {
  UsageSnapshot,
} from "../types/usage";
import type {
  AccountEntitlement,
} from "../types/accountEntitlement";
import {
  parseAppRoute,
  pushAppRoute,
  replaceAppRoute,
  type AppRoute,
  type ReportRoute,
} from "../navigation/appRoute";
import {
  AUTH_SESSION_EXPIRED_EVENT,
  clearAccountDeletionReturnIntent,
  consumeAccountDeletionAcknowledgement,
  consumeAccountDeletionReturnIntent,
  expireAuthSession,
  getAuthSessionExpiresAt,
  getCurrentUser,
  handleCognitoCallback,
  loginWithCognito,
  logoutFromCognito,
  rememberAccountDeletionAcknowledgement,
  rememberAccountDeletionReturnIntent,
  signupWithCognito,
  type AuthUser,
} from "../auth/cognito";
import { isTrustedStripeRedirect } from "../security/externalUrls";
import { isReportWindow } from "../api/reportsValidation";
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

function getAppRoute() {
  return parseAppRoute(window.location.href);
}

function getEntryRouteId() {
  return getAppRoute().entryId;
}

function getThemeRouteId() {
  return getAppRoute().themeId;
}

function getReportRoute(): ReportRoute {
  return getAppRoute().report;
}

function getSectionRoute(): ArchiveSection {
  return getAppRoute().view;
}

function routeForSection(section: ArchiveSection): AppRoute {
  return {
    view: section,
    entryId: null,
    themeId: section === "themes" ? getThemeRouteId() : null,
    report: section === "reports" ? getReportRoute() : { type: "WEEKLY", window: null },
  };
}

function setSectionRoute(section: ArchiveSection, mode: "push" | "replace" = "push") {
  const route = routeForSection(section);
  if (mode === "push") pushAppRoute(route); else replaceAppRoute(route);
}

function setReportRoute(route: ReportRoute, mode: "push" | "replace" = "push") {
  const nextRoute: AppRoute = { view: "reports", entryId: null, themeId: null, report: route };
  if (mode === "push") pushAppRoute(nextRoute); else replaceAppRoute(nextRoute);
}

function setThemeRoute(themeId: string, mode: "push" | "replace" = "push") {
  const route: AppRoute = {
    view: "themes", entryId: null, themeId, report: { type: "WEEKLY", window: null },
  };
  if (mode === "push") pushAppRoute(route); else replaceAppRoute(route);
}

function setEntryRoute(entryId: string | null, mode: "push" | "replace" = "push") {
  const route: AppRoute = entryId
    ? { view: "archive", entryId, themeId: null, report: { type: "WEEKLY", window: null } }
    : routeForSection("archive");
  if (mode === "push") pushAppRoute(route); else replaceAppRoute(route);
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
  const [isAccountPanelOpen, setIsAccountPanelOpen] = useState(false);
  const [focusAccountDeletion, setFocusAccountDeletion] = useState(false);
  const [deletionAcknowledgement, setDeletionAcknowledgement] = useState(
    () => consumeAccountDeletionAcknowledgement()
      ? "Your account deletion request has been processed. You have been signed out."
      : "",
  );
  const [activeSection, setActiveSection] =
    useState<ArchiveSection>(() => getSectionRoute());
  const [selectedThemeRouteId, setSelectedThemeRouteId] =
    useState<string | null>(() => getThemeRouteId());
  const [reportRoute, setReportRouteState] = useState<ReportRoute>(() => getReportRoute());
  const entryRequestRef = useRef(0);
  const entryControllerRef = useRef<AbortController | null>(null);
  const mobileNavTriggerRef = useRef<HTMLButtonElement | null>(null);
  const mobileNavCloseRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    setIsAccountPanelOpen(false);
  }, [activeSection]);

  const [searchQuery, setSearchQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState("newest");
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [authUser, setAuthUser] = useState<AuthUser | null>(getCurrentUser());
  const [isAuthReady, setIsAuthReady] = useState(false);
  const [authError, setAuthError] = useState("");
  const [isSigningIn, setIsSigningIn] = useState(false);
  const signInPending = useRef(false);

  async function startSignIn(signup = false) {
    if (signInPending.current) return;
    signInPending.current = true;
    setIsSigningIn(true);
    setAuthError("");
    try {
      await (signup ? signupWithCognito() : loginWithCognito());
    } catch (error) {
      setAuthError(getErrorMessage(error, "Sign-in could not be completed. Please try again."));
    } finally {
      signInPending.current = false;
      setIsSigningIn(false);
    }
  }

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

  const clearProtectedClientState = useCallback(() => {
    entryControllerRef.current?.abort();
    entryRequestRef.current += 1;
    setAuthUser(null);
    setEntries([]);
    setSelectedEntry(null);
    setIsEntryDetailOpen(false);
    setModalMode(null);
    setUsage(null);
    setUsageError("");
    setAccountEntitlement(null);
    setEntitlementError("");
    setIsMobileNavOpen(false);
    setIsAccountPanelOpen(false);
    setToasts([]);
    setArchiveError("");
    setStatusMessage("Sign in to load your private archive.");
  }, []);

  const closeMobileNavigation = useCallback(() => {
    setIsMobileNavOpen(false);
    window.requestAnimationFrame(() => mobileNavTriggerRef.current?.focus());
  }, []);

  function focusActiveViewHeading() {
    window.requestAnimationFrame(() => {
      const heading = document.querySelector<HTMLElement>(".archive-main h1");
      if (!heading) return;
      heading.tabIndex = -1;
      heading.focus();
    });
  }

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
      if (isMobileNavOpen) closeMobileNavigation();
      return;
    }
    entryControllerRef.current?.abort();
    entryRequestRef.current += 1;
    setIsEntryLoading(false);
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
    focusActiveViewHeading();
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

      if (!checkoutUrl || !isTrustedStripeRedirect(checkoutUrl, "checkout")) {
        throw new Error(
          (
            "Checkout URL was missing or invalid "
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

      if (!portalUrl || !isTrustedStripeRedirect(portalUrl, "portal")) {
        throw new Error(
          (
            "Portal URL was missing or invalid "
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
      await loadSelectedEntry(
        nextSelectedEntryId,
        getEntryRouteId() === nextSelectedEntryId ? null : "replace",
      );
    }
  }

  async function loadSelectedEntry(
    entryId: string,
    routeMode: "push" | "replace" | null,
  ) {
    entryControllerRef.current?.abort();
    const controller = new AbortController();
    entryControllerRef.current = controller;
    const request = ++entryRequestRef.current;
    setIsEntryLoading(true);
    try {
      const result = await getEntry(entryId, controller.signal);
      if (controller.signal.aborted || request !== entryRequestRef.current) return;
      setSelectedEntry(result.entry);
      setIsEntryDetailOpen(true);
      setActiveSection("archive");
      if (routeMode) setEntryRoute(entryId, routeMode);
      updateStatus("Entry loaded.");
    } catch (error) {
      if (controller.signal.aborted || request !== entryRequestRef.current) return;
      updateStatus(getErrorMessage(error, "Failed to open entry."), "error", "Could not open entry");
      if (!routeMode) setEntryRoute(null, "replace");
    } finally {
      if (!controller.signal.aborted && request === entryRequestRef.current) setIsEntryLoading(false);
    }
  }

  async function openEntry(entryId: string, updateRoute = true) {
    await loadSelectedEntry(entryId, updateRoute ? "push" : null);
  }

  function closeEntryDetail() {
    entryControllerRef.current?.abort();
    entryRequestRef.current += 1;
    setIsEntryLoading(false);
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
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, clearProtectedClientState);
    return () => window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, clearProtectedClientState);
  }, [clearProtectedClientState]);

  useEffect(() => {
    if (!authUser) return;
    const expiresAt = getAuthSessionExpiresAt();
    if (expiresAt === null || expiresAt <= Date.now()) {
      expireAuthSession();
      return;
    }
    const timeout = window.setTimeout(expireAuthSession, expiresAt - Date.now());
    return () => window.clearTimeout(timeout);
  }, [authUser]);

  useEffect(() => {
    async function bootstrap() {
      let currentUser: AuthUser | null = null;

      try {
        const callbackUser = await handleCognitoCallback();
        currentUser = callbackUser || getCurrentUser();

        setAuthUser(currentUser);

        if (callbackUser) {
          if (consumeAccountDeletionReturnIntent()) {
            setIsAccountPanelOpen(true);
            setFocusAccountDeletion(true);
          }
          updateStatus("Signed in with Cognito.", "success", "Login successful");
        }
      } catch (error) {
        clearAccountDeletionReturnIntent();
        setAuthError(getErrorMessage(error, "Could not restore your session."));
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
        const routedApp = getAppRoute();
        const routedEntryId = routedApp.entryId;
        const routedSection = routedApp.view;
        const routedReport = routedApp.report;
        setActiveSection(routedSection);
        setSelectedThemeRouteId(routedApp.themeId);
        setReportRouteState(routedReport);
        replaceAppRoute(routedApp);
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
      const routedApp = getAppRoute();
      const entryId = routedApp.entryId;
      const routedSection = routedApp.view;
      const routedReport = routedApp.report;
      setActiveSection(routedSection);
      setSelectedThemeRouteId(routedApp.themeId);
      setReportRouteState(routedReport);
      replaceAppRoute(routedApp);
      if (entryId) void openEntry(entryId, false);
      else {
        entryControllerRef.current?.abort();
        entryRequestRef.current += 1;
        setIsEntryLoading(false);
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

    const frame = window.requestAnimationFrame(() => mobileNavCloseRef.current?.focus());

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        closeMobileNavigation();
      }
    }

    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [closeMobileNavigation, isMobileNavOpen]);

  function handleLogout() {
    clearProtectedClientState();
    logoutFromCognito();
  }

  function handleAccountDeletionReauthentication() {
    rememberAccountDeletionReturnIntent();
    void startSignIn();
  }

  function handleAccountDeletionProcessed() {
    const acknowledgement = (
      "Your account deletion request has been processed. You have been signed out."
    );
    rememberAccountDeletionAcknowledgement();
    setDeletionAcknowledgement(acknowledgement);
    clearProtectedClientState();
    try {
      logoutFromCognito();
    } catch {
      expireAuthSession();
    }
  }

  if (!isAuthReady || !authUser) {
    return (
      <AuthLandingPage
        isReady={isAuthReady && !isSigningIn}
        error={authError}
        acknowledgement={deletionAcknowledgement}
        onSignIn={() => {
          void startSignIn();
        }}
        onCreateAccount={() => {
          void startSignIn(true);
        }}
      />
    );
  }

  function renderAccountSurface() {
    return (
      <details
        className="phase2-account-surface"
        open={isAccountPanelOpen}
        onToggle={(event) => setIsAccountPanelOpen(event.currentTarget.open)}
      >
        <summary aria-label="Open account, plan, and usage">
          <span className="phase2-account-avatar"><UserRound size={17} /></span>
          <span><strong>{accountEntitlement?.plan.label || "Account"}</strong><small>Plan & usage</small></span>
        </summary>
        <div className="phase2-account-popover">
          <AuthStatus user={authUser} isAuthReady={isAuthReady} onLogin={loginWithCognito} onLogout={handleLogout} />
          <AccountPlanCard
            entitlement={accountEntitlement} isLoading={isEntitlementLoading} errorMessage={entitlementError}
            isCheckoutLoading={isCheckoutLoading} isPortalLoading={isPortalLoading}
            onRetry={() => void refreshEntitlement()} onUpgrade={() => void handleUpgrade()}
            onManageSubscription={() => void handleManageSubscription()}
          />
          <UsageMeter usage={usage} isLoading={isUsageLoading} errorMessage={usageError} onRetry={() => void refreshUsage()} />
          <AccountDataExport isPanelOpen={isAccountPanelOpen} />
          <AccountDeletionDangerZone
            isPanelOpen={isAccountPanelOpen}
            focusOnOpen={focusAccountDeletion}
            onFocusHandled={() => setFocusAccountDeletion(false)}
            onReauthenticate={handleAccountDeletionReauthentication}
            onDeletionProcessed={handleAccountDeletionProcessed}
          />
          <nav className="phase2-account-policy-links" aria-label="Account policies">
            <a href="/privacy">Privacy Policy</a>
            <a href="/terms">Terms</a>
          </nav>
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
          ref={mobileNavTriggerRef}
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
        onClick={closeMobileNavigation}
        aria-label="Close navigation"
      />

      <div
        id="jm8-mobile-navigation"
        className={isMobileNavOpen ? "mobile-sidebar-shell open" : "mobile-sidebar-shell"}
        aria-hidden={!isMobileNavOpen}
        inert={!isMobileNavOpen}
      >
        <button
          ref={mobileNavCloseRef}
          className="mobile-sidebar-close"
          onClick={closeMobileNavigation}
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
