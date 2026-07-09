import { useEffect, useMemo, useState } from "react";
import { ChevronDown, LayoutGrid, List, Menu, Sprout, X } from "lucide-react";
import ArchiveSidebar from "../components/layout/ArchiveSidebar";
import ArchiveTopbar from "../components/layout/ArchiveTopbar";
import SelectedEntryPanel from "../components/layout/SelectedEntryPanel";
import ArchiveChips from "../components/archive/ArchiveChips";
import EntryCard from "../components/archive/EntryCard";
import ActionModal from "../components/archive/ActionModal";
import type { JournalEntry } from "../types/journal";
import {
  analyzeEntry,
  createEntry,
  createUploadUrl,
  getEntry,
  listEntries,
  reviewEntry,
  runOcr,
  uploadFileToS3,
} from "../api/client";

type ModalMode = "write" | "upload" | "review" | null;

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

export default function ArchivePage() {
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [selectedEntry, setSelectedEntry] = useState<JournalEntry | null>(null);
  const [statusMessage, setStatusMessage] = useState("Loading archive...");
  const [modalMode, setModalMode] = useState<ModalMode>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [isSelectedPanelOpen, setIsSelectedPanelOpen] = useState(true);
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);

  const groupedEntries = useMemo(() => groupEntries(entries), [entries]);

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
      setIsSelectedPanelOpen(true);
    }
  }

  async function openEntry(entryId: string) {
    try {
      setStatusMessage("Entry selected.");
      const result = await getEntry(entryId);
      setSelectedEntry(result.entry);
      setIsSelectedPanelOpen(true);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Failed to open entry.");
    }
  }

  async function handleCreateText(text: string) {
    setIsBusy(true);
    setStatusMessage("Creating typed entry...");

    try {
      const result = await createEntry(text);
      await refreshEntries(result.entry.entryId);
      setStatusMessage("Typed entry created.");
      setModalMode(null);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Failed to create entry.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleUploadImage(file: File) {
    setIsBusy(true);
    setStatusMessage("Creating S3 upload URL...");

    try {
      const upload = await createUploadUrl(file.name, file.type || "image/jpeg");

      setStatusMessage("Uploading image to S3...");
      await uploadFileToS3(upload.upload.uploadUrl, file);

      setStatusMessage("Running OCR...");
      const ocrResult = await runOcr(upload.upload.entryId);

      await refreshEntries(ocrResult.entry.entryId);
      setStatusMessage("Image uploaded and OCR completed.");
      setModalMode(null);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Upload/OCR failed.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleSaveReview(entryId: string, cleanText: string) {
    setIsBusy(true);
    setStatusMessage("Saving reviewed transcript...");

    try {
      const result = await reviewEntry(entryId, cleanText);
      await refreshEntries(result.entry.entryId);
      setStatusMessage("Review saved.");
      setModalMode(null);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Failed to save review.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleAnalyzeSelected() {
    if (!selectedEntry) return;

    setIsBusy(true);
    setStatusMessage("Analyzing selected entry...");

    try {
      const result = await analyzeEntry(selectedEntry.entryId);
      await refreshEntries(result.entry.entryId);
      setStatusMessage("Analysis completed.");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Analysis failed.");
    } finally {
      setIsBusy(false);
    }
  }

  useEffect(() => {
    refreshEntries()
      .then(() => setStatusMessage("Archive loaded."))
      .catch((error) => {
        setStatusMessage(error instanceof Error ? error.message : "Failed to load archive.");
      });
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
        onNewEntry={() => setModalMode("write")}
        onUpload={() => setModalMode("upload")}
      />

      <section className="archive-main">
        <ArchiveTopbar />
        <ArchiveChips />

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
            <h2>No entries found</h2>
            <p>Create or upload your first journal entry from the sidebar.</p>
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
    </main>
  );
}
