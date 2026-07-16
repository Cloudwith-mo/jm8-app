import { useEffect, useMemo, useState } from "react";
import { ChevronDown, LayoutGrid, List, Menu, Sprout, X } from "lucide-react";
import ArchiveSidebar from "../components/layout/ArchiveSidebar";
import ArchiveTopbar from "../components/layout/ArchiveTopbar";
import SelectedEntryPanel from "../components/layout/SelectedEntryPanel";
import ArchiveChips, { type ArchiveChipFilter } from "../components/archive/ArchiveChips";
import EntryCard from "../components/archive/EntryCard";
import ActionModal from "../components/archive/ActionModal";
import ToastStack, { type ToastKind, type ToastMessage } from "../components/ui/ToastStack";
import AuthStatus from "../components/layout/AuthStatus";
import type { JournalEntry } from "../types/journal";
import {
  getCurrentUser,
  handleCognitoCallback,
  loginWithCognito,
  logoutFromCognito,
  type AuthUser,
} from "../auth/cognito";
import {
  analyzeEntry,
  createEntry,
  createUploadUrl,
  deleteEntry,
  getEntry,
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

  const [searchQuery, setSearchQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState("newest");
  const [chipFilter, setChipFilter] = useState<ArchiveChipFilter>({ type: "all", value: "all" });
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [authUser, setAuthUser] = useState<AuthUser | null>(getCurrentUser());
  const [isAuthReady, setIsAuthReady] = useState(false);

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

    setIsBusy(true);
    updateStatus("Analyzing selected entry...", "loading", "Analysis running");

    try {
      const result = await analyzeEntry(selectedEntry.entryId);
      await refreshEntries(result.entry.entryId);
      updateStatus("Analysis completed.", "success", "Analysis complete");
    } catch (error) {
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
        updateStatus("Sign in to load your private archive.", "info", "Login required");
        return;
      }

      try {
        await refreshEntries();
        updateStatus("Archive loaded.");
      } catch (error) {
        updateStatus(getErrorMessage(error, "Failed to load archive."), "error", "Archive failed");
      }
    }

    bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main className="jm8-archive-shell">
      <header className="mobile-app-header">
        <button onClick={() => setIsMobileNavOpen(true)} aria-label="Open navigation">
          <Menu size={20} />
        </button>
        <strong>JOURNALM8</strong>
        <button
          onClick={() => setIsSelectedPanelOpen(true)}
          aria-label="Open selected entry"
        >
          <Sprout size={20} />
        </button>
      </header>

      <div
        className={isMobileNavOpen ? "mobile-nav-backdrop visible" : "mobile-nav-backdrop"}
        onClick={() => setIsMobileNavOpen(false)}
      />

      <div className={isMobileNavOpen ? "mobile-sidebar-shell open" : "mobile-sidebar-shell"}>
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
        onNewEntry={() => setModalMode("write")}
        onUpload={() => setModalMode("upload")}
      />

      <section className="archive-main">
        <ArchiveTopbar
          searchQuery={searchQuery}
          sourceFilter={sourceFilter}
          statusFilter={statusFilter}
          sortOrder={sortOrder}
          resultCount={filteredEntries.length}
          totalCount={entries.length}
          onSearchChange={setSearchQuery}
          onSourceFilterChange={setSourceFilter}
          onStatusFilterChange={setStatusFilter}
          onSortOrderChange={setSortOrder}
          onClearFilters={clearFilters}
        />

        <AuthStatus
          user={authUser}
          isAuthReady={isAuthReady}
          onLogin={loginWithCognito}
          onLogout={logoutFromCognito}
        />

        {isAuthReady && !authUser && (
          <section className="auth-required-card">
            <h2>Sign in to access your private journal archive</h2>
            <p>
              JM8 now protects entries by Cognito user identity. Log in to upload,
              review, analyze, search, and manage your personal journal archive.
            </p>
            <button onClick={loginWithCognito}>Login with Cognito</button>
          </section>
        )}

        <ArchiveChips entries={entries} activeFilter={chipFilter} onFilterChange={setChipFilter} />

        <div className="archive-heading-row">
          <div>
            <p className="archive-kicker">
              <Sprout size={16} />
              Private journal archive
            </p>
            <h1>Your Journal Timeline</h1>
          </div>

          <div className="view-toggle">
            <button className="active">
              <LayoutGrid size={18} />
            </button>
            <button>
              <List size={18} />
            </button>
          </div>
        </div>

        {Object.keys(groupedEntries).length === 0 ? (
          <section className="empty-archive">
            <h2>No matching entries found</h2>
            <p>Try clearing filters or searching for another mood, theme, or keyword.</p>
          </section>
        ) : (
          Object.entries(groupedEntries).map(([group, groupEntries]) => (
            <section className="month-section" key={group}>
              <header className="month-heading">
                <button>
                  <ChevronDown size={18} />
                </button>
                <h2>{group}</h2>
                <span>{groupEntries.length} entries</span>
              </header>

              <div className="entry-grid">
                {groupEntries.map((entry) => (
                  <EntryCard
                    key={entry.entryId}
                    entry={entry}
                    isSelected={selectedEntry?.entryId === entry.entryId}
                    onClick={() => openEntry(entry.entryId)}
                  />
                ))}
              </div>
            </section>
          ))
        )}

        <div className="archive-status">
          {isBusy ? `Working: ${statusMessage}` : statusMessage}
        </div>
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
