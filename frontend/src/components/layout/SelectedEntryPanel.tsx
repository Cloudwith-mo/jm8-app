import {
  Bot,
  CheckCircle2,
  MoreHorizontal,
  Send,
  X,
} from "lucide-react";
import type { JournalEntry } from "../../types/journal";

type SelectedEntryPanelProps = {
  entry: JournalEntry | null;
  isBusy: boolean;
  isOpen: boolean;
  onClose: () => void;
  onReview: () => void;
  onAnalyze: () => void;
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

export default function SelectedEntryPanel({
  entry,
  isBusy,
  isOpen,
  onClose,
  onReview,
  onAnalyze,
}: SelectedEntryPanelProps) {
  const previewText =
    entry?.cleanText ||
    entry?.rawText ||
    "Select an entry to preview its transcript.";

  const tags = entry?.analysis?.themes || ["growth", "discipline", "mindset"];

  return (
    <aside className={isOpen ? "selected-panel open" : "selected-panel"}>
      <header className="selected-panel-header">
        <strong>Selected Entry</strong>
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

          <em>1 of 8</em>
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
          {tags.map((tag) => (
            <span key={tag}>{tag}</span>
          ))}
          <span>+</span>
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
          ["IMG_1305.JPG", "Completed"],
          ["IMG_1298.JPG", "Completed"],
          ["IMG_1290.JPG", "Failed"],
          ["IMG_1280.JPG", "Pending"],
        ].map(([name, status]) => (
          <div className="ocr-job-row" key={name}>
            <span>{name}</span>
            <em className={status.toLowerCase()}>{status}</em>
          </div>
        ))}
      </section>

      <section className="ask-card">
        <div className="ask-heading">
          <Bot size={18} />
          <h3>Ask JM8 AI</h3>
          <span>Beta</span>
        </div>

        <button className="ask-chip">What was my biggest challenge last year?</button>

        <p>
          Your biggest challenge was maintaining consistency while balancing ambition,
          self-doubt, and pressure.
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
