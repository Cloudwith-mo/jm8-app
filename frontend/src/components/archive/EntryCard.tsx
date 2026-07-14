import { Check, MoreHorizontal } from "lucide-react";
import type { JournalEntry } from "../../types/journal";

type EntryCardProps = {
  entry: JournalEntry;
  isSelected: boolean;
  onClick: () => void;
};

function getPreview(entry: JournalEntry) {
  return entry.cleanText || entry.rawText || "No transcript available yet.";
}

function getTitle(entry: JournalEntry) {
  if (entry.sourceType === "image") return "Scanned journal page";
  return "Typed reflection";
}

function getTags(entry: JournalEntry) {
  const themes = entry.analysis?.themes || [];

  if (themes.length > 0) return themes.slice(0, 3);

  if (entry.sourceType === "image") return ["ocr", "journal"];

  return ["reflection"];
}

function formatEntryDate(value?: string) {
  if (!value) return "No date";

  return new Date(value).toLocaleDateString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatEntryTime(value?: string) {
  if (!value) return "";

  return new Date(value).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function EntryCard({ entry, isSelected, onClick }: EntryCardProps) {
  const tags = getTags(entry);

  return (
    <article className={isSelected ? "entry-card selected" : "entry-card"} onClick={onClick}>
      <button className="entry-card-click-target" aria-label="Open entry" />

      <div className={entry.imagePreviewUrl ? "notebook-preview has-real-image" : "notebook-preview"}>
        {isSelected && (
          <div className="selected-check">
            <Check size={16} />
          </div>
        )}

        {entry.imagePreviewUrl ? (
          <img src={entry.imagePreviewUrl} alt="Journal page preview" />
        ) : (
          <div className="paper-lines">
            <span>{entry.sourceType === "image" ? "GROWTH IS" : "TODAY I"}</span>
            <span>{entry.sourceType === "image" ? "UNCOMFORTABLE." : "REFLECTED."}</span>
          </div>
        )}
      </div>

      <div className="entry-card-body">
        <div className="entry-meta-row">
          <strong>{formatEntryDate(entry.createdAt)}</strong>
          <span>•</span>
          <small>{formatEntryTime(entry.createdAt)}</small>
          <em className={entry.status?.includes("FAILED") ? "status-badge failed" : "status-badge"}>
            {entry.status || "NEW"}
          </em>
        </div>

        <h3>{getTitle(entry)}</h3>
        <p>{getPreview(entry)}</p>

        <div className="entry-tags">
          {tags.map((tag) => (
            <span key={tag}>{tag}</span>
          ))}
        </div>
      </div>

      <button className="entry-more">
        <MoreHorizontal size={17} />
      </button>
    </article>
  );
}
