import {
  Bot,
  Brain,
  CheckCircle2,
  Clipboard,
  Download,
  FileDown,
  FileText,
  HeartPulse,
  MoreHorizontal,
  Send,
  Sparkles,
  Target,
  Trash2,
  X,
} from "lucide-react";
import type { JournalEntry } from "../../types/journal";
import AnalysisHistoryCard from "./AnalysisHistoryCard";

type SelectedEntryPanelProps = {
  entry: JournalEntry | null;
  isBusy: boolean;
  isOpen: boolean;
  onClose: () => void;
  onReview: () => void;
  onAnalyze: () => void;
  onCopyTranscript: () => void;
  onExportTranscript: () => void;
  onDownloadImage: () => void;
  onDelete: () => void;
};

function formatDate(value?: string) {
  if (!value) return "Unknown date";

  return new Date(value).toLocaleString([], {
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function getPreviewText(entry: JournalEntry | null) {
  return (
    entry?.cleanText ||
    entry?.rawText ||
    "Select an entry to preview its transcript."
  );
}

function getThemes(entry: JournalEntry | null) {
  const themes = entry?.analysis?.themes || [];

  if (themes.length > 0) return themes;

  if (entry?.sourceType === "image") return ["ocr", "journal", "reflection"];

  return ["typed", "reflection"];
}

function getWordCount(entry: JournalEntry | null) {
  const text = entry?.cleanText || entry?.rawText || "";

  if (!text.trim()) return 0;

  return text.trim().split(/\s+/).length;
}

export default function SelectedEntryPanel({
  entry,
  isBusy,
  isOpen,
  onClose,
  onReview,
  onAnalyze,
  onCopyTranscript,
  onExportTranscript,
  onDownloadImage,
  onDelete,
}: SelectedEntryPanelProps) {
  const previewText = getPreviewText(entry);
  const tags = getThemes(entry);
  const hasAnalysis = Boolean(entry?.analysis);
  const wordCount = getWordCount(entry);
  const hasImage = Boolean(entry?.imagePreviewUrl);

  return (
    <aside className={isOpen ? "selected-panel open" : "selected-panel"}>
      <header className="selected-panel-header">
        <div>
          <strong>Selected Entry</strong>
          <span>{entry?.sourceType || "No source"}</span>
        </div>

        <button onClick={onClose} aria-label="Close selected entry panel">
          <X size={18} />
        </button>
      </header>

      <section className="selected-image-card">
        <button className="image-arrow">‹</button>

        <div className={entry?.imagePreviewUrl ? "selected-notebook-image has-real-image" : "selected-notebook-image"}>
          {entry?.imagePreviewUrl ? (
            <img src={entry.imagePreviewUrl} alt="Selected journal page" />
          ) : (
            <>
              <span>{entry?.sourceType === "typed" ? "TODAY I" : "GROWTH IS"}</span>
              <span>{entry?.sourceType === "typed" ? "REFLECTED." : "UNCOMFORTABLE."}</span>
            </>
          )}

          <em>{entry?.sourceType === "image" ? "Journal image" : "Typed note"}</em>
        </div>

        <button className="image-arrow">›</button>
      </section>

      <section className="selected-summary">
        <div className="selected-date-row">
          <span>{formatDate(entry?.createdAt)}</span>
          <em>{entry?.status || "NO ENTRY"}</em>
        </div>

        <h2>{entry ? "Journal reflection" : "No entry selected"}</h2>
        <p>{previewText}</p>

        <div className="selected-tags">
          {tags.slice(0, 6).map((tag) => (
            <span key={tag}>{tag}</span>
          ))}
        </div>

        <div className="selected-actions">
          <button className="dark-action" onClick={onReview} disabled={!entry || isBusy}>
            Review Text
          </button>

          <button className="dark-action" onClick={onAnalyze} disabled={!entry || isBusy}>
            Analyze
          </button>

          <button className="icon-action">
            <MoreHorizontal size={16} />
          </button>
        </div>
      </section>

      <section className="lifecycle-card">
        <div className="panel-card-heading">
          <h3>Manage Entry</h3>
        </div>

        <div className="lifecycle-grid">
          <button onClick={onCopyTranscript} disabled={!entry || isBusy}>
            <Clipboard size={16} />
            Copy Text
          </button>

          <button onClick={onExportTranscript} disabled={!entry || isBusy}>
            <FileDown size={16} />
            Export .txt
          </button>

          <button onClick={onDownloadImage} disabled={!entry || !hasImage || isBusy}>
            <Download size={16} />
            Image
          </button>

          <button className="danger-action" onClick={onDelete} disabled={!entry || isBusy}>
            <Trash2 size={16} />
            Delete
          </button>
        </div>
      </section>

      <section className={hasAnalysis ? "analysis-panel-card analyzed" : "analysis-panel-card"}>
        <div className="analysis-panel-heading">
          <div>
            <p>JM8 Analysis</p>
            <h3>{hasAnalysis ? "Insight generated" : "No analysis yet"}</h3>
          </div>

          <div className="analysis-icon">
            <Brain size={19} />
          </div>
        </div>

        {hasAnalysis ? (
          <>
            <div className="analysis-metric-grid">
              <div>
                <HeartPulse size={18} />
                <span>Mood</span>
                <strong>{entry?.analysis?.mood || "Reflective"}</strong>
              </div>

              <div>
                <Sparkles size={18} />
                <span>Sentiment</span>
                <strong>{entry?.analysis?.sentiment || "Neutral"}</strong>
              </div>

              <div>
                <FileText size={18} />
                <span>Words</span>
                <strong>{wordCount}</strong>
              </div>
            </div>

            <div className="analysis-summary-block">
              <h4>Summary</h4>
              <p>{entry?.analysis?.summary || "No summary returned yet."}</p>
            </div>

            <div className="analysis-summary-block next-step">
              <h4>
                <Target size={16} />
                Next Step
              </h4>
              <p>{entry?.analysis?.nextStep || "Review this entry and decide one small action."}</p>
            </div>
          </>
        ) : (
          <div className="no-analysis-state">
            <Sparkles size={22} />
            <p>
              Analyze this entry to generate mood, sentiment, themes, summary, and a next step.
            </p>
            <button onClick={onAnalyze} disabled={!entry || isBusy}>
              Run Analysis
            </button>
          </div>
        )}
      </section>

      <AnalysisHistoryCard
        entryId={entry?.entryId}
        versionCount={
          entry?.analysisVersionCount
        }
        disabled={isBusy}
      />

      <section className="entry-details-card">
        <h3>Entry Details</h3>

        <dl>
          <div>
            <dt>ID</dt>
            <dd>{entry?.entryId || "—"}</dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>{entry?.sourceType || "—"}</dd>
          </div>
          <div>
            <dt>OCR Status</dt>
            <dd>
              <CheckCircle2 size={14} />
              {entry?.ocrStatus || "—"}
            </dd>
          </div>
          <div>
            <dt>Review</dt>
            <dd>
              <CheckCircle2 size={14} />
              {entry?.reviewStatus || "—"}
            </dd>
          </div>
          <div>
            <dt>Analysis</dt>
            <dd>
              <CheckCircle2 size={14} />
              {entry?.analysisStatus || "—"}
            </dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{formatDate(entry?.createdAt)}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{formatDate(entry?.updatedAt)}</dd>
          </div>
        </dl>
      </section>

      <section className="ocr-jobs-card">
        <div className="panel-card-heading">
          <h3>OCR Jobs</h3>
          <button>View all</button>
        </div>

        {[
          ["Recent upload", entry?.ocrStatus || "Current"],
          ["Image processing", entry?.sourceType === "image" ? "Completed" : "N/A"],
          ["Transcript review", entry?.reviewStatus || "Pending"],
          ["AI analysis", entry?.analysisStatus || "Pending"],
        ].map(([name, status]) => (
          <div className="ocr-job-row" key={name}>
            <span>{name}</span>
            <em className={status.toLowerCase().replaceAll("_", "-")}>{status}</em>
          </div>
        ))}
      </section>

      <section className="ask-card">
        <div className="ask-heading">
          <Bot size={18} />
          <h3>Ask JM8 AI</h3>
          <span>Beta</span>
        </div>

        <button className="ask-chip">What pattern is showing up in this entry?</button>

        <p>
          Soon, this panel will let you ask questions across your entire journal archive.
        </p>

        <div className="ask-input">
          <input placeholder="Ask anything about your journal..." />
          <button>
            <Send size={16} />
          </button>
        </div>
      </section>
    </aside>
  );
}
