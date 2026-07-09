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

export default function EntryCard({ entry, isSelected, onClick }: EntryCardProps) {
  const tags = getTags(entry);

  return (
    <article className={isSelected ? "entry-card selected" : "entry-card"} onClick={onClick}>
      <button className="entry-card-click-target" aria-label="Open entry" />

      <div className="notebook-preview">
        {isSelected && (
          <div className="selected-check">
            <Check size={16} />
          </div>
        )}

        <div className="paper-lines">
          <span>{entry.sourceType === "image" ? "GROWTH IS" : "TODAY I"}</span>
          <span>{entry.sourceType === "image" ? "UNCOMFORTABLE." : "REFLECTED."}</span>
        </div>
      </div>

      <div className="entry-card-body">
        <div className="entry-meta-row">
          <strong>{entry.createdAt ? new Date(entry.createdAt).toLocaleDateString() : "No date"}</strong>
          <span>•</span>
          <small>{entry.createdAt ? new Date(entry.createdAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""}</small>
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
