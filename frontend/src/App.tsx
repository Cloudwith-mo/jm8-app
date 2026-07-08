import { useEffect, useState } from "react";
import type { JournalEntry } from "./types/journal";
import {
  analyzeEntry,
  createEntry,
  createUploadUrl,
  getEntry,
  listEntries,
  reviewEntry,
  runOcr,
  uploadFileToS3,
} from "./api/client";
import "./App.css";

function App() {
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [selectedEntry, setSelectedEntry] = useState<JournalEntry | null>(null);
  const [typedText, setTypedText] = useState("");
  const [reviewText, setReviewText] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [statusMessage, setStatusMessage] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  async function refreshEntries() {
    const result = await listEntries();
    setEntries(result.entries);
  }

  async function openEntry(entryId: string) {
    const result = await getEntry(entryId);
    setSelectedEntry(result.entry);
    setReviewText(result.entry.cleanText || result.entry.rawText || "");
  }

  async function handleCreateTypedEntry() {
    if (!typedText.trim()) return;

    setIsLoading(true);
    setStatusMessage("Creating entry...");

    try {
      const result = await createEntry(typedText);
      setTypedText("");
      setStatusMessage("Typed entry created.");
      await refreshEntries();
      await openEntry(result.entry.entryId);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Failed to create entry.");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleUploadImage() {
    if (!selectedFile) return;

    setIsLoading(true);
    setStatusMessage("Creating upload URL...");

    try {
      const upload = await createUploadUrl(selectedFile.name, selectedFile.type || "image/jpeg");

      setStatusMessage("Uploading image to S3...");
      await uploadFileToS3(upload.upload.uploadUrl, selectedFile);

      setStatusMessage("Image uploaded. Running OCR...");
      const ocrResult = await runOcr(upload.upload.entryId);

      setStatusMessage("OCR completed.");
      setSelectedFile(null);
      await refreshEntries();
      await openEntry(ocrResult.entry.entryId);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Upload/OCR failed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleSaveReview() {
    if (!selectedEntry || !reviewText.trim()) return;

    setIsLoading(true);
    setStatusMessage("Saving reviewed transcript...");

    try {
      const result = await reviewEntry(selectedEntry.entryId, reviewText);
      setSelectedEntry(result.entry);
      setStatusMessage("Review saved.");
      await refreshEntries();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Failed to save review.");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleAnalyze() {
    if (!selectedEntry) return;

    setIsLoading(true);
    setStatusMessage("Analyzing entry...");

    try {
      const result = await analyzeEntry(selectedEntry.entryId);
      setSelectedEntry(result.entry);
      setStatusMessage("Analysis completed.");
      await refreshEntries();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Analysis failed.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    refreshEntries().catch(() => {
      setStatusMessage("Failed to load entries.");
    });
  }, []);

  return (
    <main className="app-shell">
      <section className="hero">
        <p className="eyebrow">JournalM8 Backend MVP</p>
        <h1>Turn raw journal pages into reviewed, analyzed self-knowledge.</h1>
        <p>
          Upload a handwritten journal image, extract OCR text, review the transcript,
          and analyze mood, sentiment, and themes.
        </p>
      </section>

      {statusMessage && (
        <div className="status-box">
          {isLoading ? "Working: " : ""}
          {statusMessage}
        </div>
      )}

      <div className="grid">
        <section className="card">
          <h2>Create typed entry</h2>
          <textarea
            value={typedText}
            onChange={(event) => setTypedText(event.target.value)}
            placeholder="Write a quick journal entry..."
            rows={7}
          />
          <button onClick={handleCreateTypedEntry} disabled={isLoading || !typedText.trim()}>
            Save Typed Entry
          </button>
        </section>

        <section className="card">
          <h2>Upload journal image</h2>
          <input
            type="file"
            accept="image/jpeg,image/png"
            onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
          />
          <button onClick={handleUploadImage} disabled={isLoading || !selectedFile}>
            Upload + Run OCR
          </button>
          <p className="helper">
            This uses your Phase 2/3 backend: presigned S3 upload URL, S3 upload, Textract OCR,
            and DynamoDB status updates.
          </p>
        </section>
      </div>

      <div className="workspace">
        <section className="card timeline">
          <h2>Journal timeline</h2>
          {entries.length === 0 ? (
            <p>No entries yet.</p>
          ) : (
            <div className="entry-list">
              {entries.map((entry) => (
                <button
                  key={entry.entryId}
                  className={
                    selectedEntry?.entryId === entry.entryId
                      ? "entry-row active"
                      : "entry-row"
                  }
                  onClick={() => openEntry(entry.entryId)}
                >
                  <span>{entry.sourceType?.toUpperCase() || "ENTRY"}</span>
                  <strong>{entry.status}</strong>
                  <small>{entry.createdAt}</small>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="card detail">
          <h2>Entry detail</h2>

          {!selectedEntry ? (
            <p>Select an entry from the timeline.</p>
          ) : (
            <>
              <div className="meta-grid">
                <p><strong>Entry ID:</strong> {selectedEntry.entryId}</p>
                <p><strong>Status:</strong> {selectedEntry.status}</p>
                <p><strong>Source:</strong> {selectedEntry.sourceType}</p>
                <p><strong>Analysis:</strong> {selectedEntry.analysisStatus}</p>
                {selectedEntry.ocrStatus && <p><strong>OCR:</strong> {selectedEntry.ocrStatus}</p>}
                {selectedEntry.reviewStatus && <p><strong>Review:</strong> {selectedEntry.reviewStatus}</p>}
              </div>

              <label className="label">Transcript / entry text</label>
              <textarea
                value={reviewText}
                onChange={(event) => setReviewText(event.target.value)}
                rows={14}
              />

              <div className="button-row">
                <button onClick={handleSaveReview} disabled={isLoading || !reviewText.trim()}>
                  Save Review
                </button>
                <button onClick={handleAnalyze} disabled={isLoading}>
                  Analyze Entry
                </button>
              </div>

              {selectedEntry.analysis && (
                <section className="analysis-box">
                  <h3>Analysis</h3>
                  <p><strong>Sentiment:</strong> {selectedEntry.analysis.sentiment}</p>
                  <p><strong>Mood:</strong> {selectedEntry.analysis.mood}</p>
                  <p><strong>Themes:</strong> {selectedEntry.analysis.themes?.join(", ")}</p>
                  <p><strong>Summary:</strong> {selectedEntry.analysis.summary}</p>
                  <p><strong>Next Step:</strong> {selectedEntry.analysis.nextStep}</p>
                </section>
              )}
            </>
          )}
        </section>
      </div>
    </main>
  );
}

export default App;
