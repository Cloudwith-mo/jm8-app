import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Menu, Sprout, X } from "lucide-react";
import ArchiveSidebar, {
  type ArchiveSection,
} from "../components/layout/ArchiveSidebar";
import ArchiveTopbar from "../components/layout/ArchiveTopbar";
import SelectedEntryPanel from "../components/layout/SelectedEntryPanel";
import ArchiveChips, { type ArchiveChipFilter } from "../components/archive/ArchiveChips";
import EntryCard from "../components/archive/EntryCard";
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
  IconButton,
  PageHeader,
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

function getEntryDateValue(entry: JournalEntry) {
  return entry.createdAt ? new Date(entry.createdAt).getTime() : 0;
}

function groupEntries(entries: JournalEntry[]) {
  const groups: Record<string, JournalEntry[]> = {};

  for (const entry of entries) {
    const date = entry.createdAt ? new Date(entry.createdAt) : new Date();
    const label = date.toLocaleString([], { month: "long", year: "numeric" });

    if (!groups[label]) groups[label] = [];
    groups[label].push(entry);
  }

  return groups;
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
  const [isSelectedPanelOpen, setIsSelectedPanelOpen] = useState(false);
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
  const [activeSection, setActiveSection] =
    useState<ArchiveSection>("archive");

  const [searchQuery, setSearchQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState("newest");
  const [chipFilter, setChipFilter] = useState<ArchiveChipFilter>({ type: "all", value: "all" });
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

          if (!(entry.status === statusFilter || entry.analysisStatus === statusFilter)) {
            return false;
          }
        }

        if (chipFilter.type === "year") {
          const entryYear = entry.createdAt
            ? String(new Date(entry.createdAt).getFullYear())
            : "";

          return entryYear === chipFilter.value;
        }

        if (chipFilter.type === "theme") {
          const fallbackTheme = entry.sourceType === "image" ? "OCR" : "Typed";
          const themes = [...(entry.analysis?.themes || []), fallbackTheme];

          return themes.some(
            (theme) => theme.toLowerCase() === chipFilter.value.toLowerCase()
          );
        }

        return true;
      })
      .filter((entry) => {
        if (!query) return true;
        return getEntrySearchText(entry).includes(query);
      })
      .sort((a, b) => {
        const aTime = getEntryDateValue(a);
        const bTime = getEntryDateValue(b);

        if (sortOrder === "oldest") {
          return aTime - bTime;
        }

        return bTime - aTime;
      });
  }, [entries, searchQuery, sourceFilter, statusFilter, sortOrder, chipFilter]);

  const groupedEntries = useMemo(() => groupEntries(filteredEntries), [filteredEntries]);

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
    setActiveSection(section);
    setIsMobileNavOpen(false);

    if (
      section !== "archive"
    ) {
      setIsSelectedPanelOpen(false);
    }
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
      setIsSelectedPanelOpen(true);
      return;
    }

    if (result.entries.length > 0 && !selectedEntry) {
      setSelectedEntry(result.entries[0]);
      setIsSelectedPanelOpen(false);
    }
  }

  async function openEntry(entryId: string) {
    try {
      updateStatus("Entry selected.");
      const result = await getEntry(entryId);
      setSelectedEntry(result.entry);
      setIsSelectedPanelOpen(true);
    } catch (error) {
      updateStatus(getErrorMessage(error, "Failed to open entry."), "error", "Could not open entry");
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

  function clearFilters() {
    setSearchQuery("");
    setSourceFilter("all");
    setStatusFilter("all");
    setSortOrder("newest");
    setChipFilter({ type: "all", value: "all" });
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
      setSelectedEntry(remainingEntries[0] || null);
      setIsSelectedPanelOpen(Boolean(remainingEntries[0]));

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
        return;
      }

      try {
        await Promise.all([
          refreshEntries(),
          refreshUsage({
            silent: true,
          }),
          refreshEntitlement({
            silent: true,
          }),
        ]);

        updateStatus("Archive loaded.");
      } catch (error) {
        updateStatus(getErrorMessage(error, "Failed to load archive."), "error", "Archive failed");
      }
    }

    bootstrap();
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

  return (
    <main
      className={
        activeSection === "archive"
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
          icon={<Sprout size={20} />}
          onClick={() => {
            if (
              activeSection !==
              "archive"
            ) {
              navigateToSection(
                "archive"
              );
              return;
            }

            setIsSelectedPanelOpen(
              true
            );
          }}
          label={
            activeSection !==
            "archive"
              ? "Return to archive"
              : "Open selected entry"
          }
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
        <AuthStatus
          user={authUser}
          isAuthReady={isAuthReady}
          onLogin={loginWithCognito}
          onLogout={logoutFromCognito}
        />

        {authUser && (
          <AccountPlanCard
            entitlement={
              accountEntitlement
            }
            isLoading={
              isEntitlementLoading
            }
            errorMessage={
              entitlementError
            }
            isCheckoutLoading={
              isCheckoutLoading
            }
            isPortalLoading={
              isPortalLoading
            }
            onRetry={() => {
              void refreshEntitlement();
            }}
            onUpgrade={() => {
              void handleUpgrade();
            }}
            onManageSubscription={() => {
              void handleManageSubscription();
            }}
          />
        )}

        {authUser && (
          <UsageMeter
            usage={usage}
            isLoading={
              isUsageLoading
            }
            errorMessage={
              usageError
            }
            onRetry={() => {
              void refreshUsage();
            }}
          />
        )}

        {authUser &&
          activeSection ===
            "archive" && (
            <>
              <ArchiveTopbar
                searchQuery={
                  searchQuery
                }
                sourceFilter={
                  sourceFilter
                }
                statusFilter={
                  statusFilter
                }
                sortOrder={
                  sortOrder
                }
                resultCount={
                  filteredEntries.length
                }
                totalCount={
                  entries.length
                }
                onSearchChange={
                  setSearchQuery
                }
                onSourceFilterChange={
                  setSourceFilter
                }
                onStatusFilterChange={
                  setStatusFilter
                }
                onSortOrderChange={
                  setSortOrder
                }
                onClearFilters={
                  clearFilters
                }
              />

              <ArchiveChips
                entries={entries}
                activeFilter={
                  chipFilter
                }
                onFilterChange={
                  setChipFilter
                }
              />

              <PageHeader
                eyebrow={
                  <>
                    <Sprout size={16} />
                    Private journal archive
                  </>
                }
                title="Your journal timeline"
                description="A calm, searchable record of your entries, reflections, and analysis."
              />

              {Object.keys(
                groupedEntries
              ).length === 0 ? (
                <EmptyState
                  title="No matching entries found"
                  description="Try clearing filters or searching for another mood, theme, or keyword."
                />
              ) : (
                Object.entries(
                  groupedEntries
                ).map(
                  ([
                    group,
                    groupEntries,
                  ]) => (
                    <section
                      className="month-section"
                      key={group}
                    >
                      <header className="month-heading">
                        <button>
                          <ChevronDown
                            size={18}
                          />
                        </button>

                        <h2>
                          {group}
                        </h2>

                        <span>
                          {
                            groupEntries.length
                          }{" "}
                          entries
                        </span>
                      </header>

                      <div className="entry-grid">
                        {groupEntries.map(
                          (entry) => (
                            <EntryCard
                              key={
                                entry.entryId
                              }
                              entry={
                                entry
                              }
                              isSelected={
                                selectedEntry
                                  ?.entryId ===
                                entry.entryId
                              }
                              onClick={() =>
                                openEntry(
                                  entry.entryId
                                )
                              }
                            />
                          )
                        )}
                      </div>
                    </section>
                  )
                )
              )}

              <div className="archive-status">
                {isBusy
                  ? `Working: ${statusMessage}`
                  : statusMessage}
              </div>
            </>
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
            "insightsTrends" && (
            <InsightsTrendsPanel
              onNotify={showToast}
            />
          )}

        {authUser &&
          activeSection ===
            "reports" && (
            <ReportsPanel
              onNotify={showToast}
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

      <button
        className={isSelectedPanelOpen ? "selected-panel-backdrop visible" : "selected-panel-backdrop"}
        onClick={() => setIsSelectedPanelOpen(false)}
        aria-label="Close selected entry drawer"
      />

      <SelectedEntryPanel
        entry={selectedEntry}
        isBusy={isBusy}
        isOpen={isSelectedPanelOpen}
        analysisAllowed={
          usage?.operations
            .entryAnalysis.allowed
          ?? true
        }
        analysisRemaining={
          usage?.operations
            .entryAnalysis.remaining
          ?? null
        }
        onClose={() => setIsSelectedPanelOpen(false)}
        onReview={() => setModalMode("review")}
        onAnalyze={handleAnalyzeSelected}
        onCopyTranscript={handleCopyTranscript}
        onExportTranscript={handleExportTranscript}
        onDownloadImage={handleDownloadImage}
        onDelete={handleDeleteSelected}
      />

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
