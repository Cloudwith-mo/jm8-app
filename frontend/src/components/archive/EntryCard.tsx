import { FileText, Image as ImageIcon } from "lucide-react";
import type { JournalEntry } from "../../types/journal";

type EntryCardProps = {
  entry: JournalEntry;
  isSelected: boolean;
  onClick: () => void;
};

function validDate(value?: string) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatEntryDate(value?: string) {
  const date = validDate(value);
  if (!date) return "Date unavailable";
  return date.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatEntryTime(value?: string) {
  const date = validDate(value);
  if (!date) return null;
  return date.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function EntryCard({ entry, isSelected, onClick }: EntryCardProps) {
  const transcript = entry.cleanText || entry.rawText;
  const time = formatEntryTime(entry.createdAt);
  const sourceLabel = entry.sourceType === "image"
    ? "Scanned journal"
    : entry.sourceType === "typed"
      ? "Typed entry"
      : "Source unavailable";
  const status = entry.analysisStatus || entry.reviewStatus || entry.ocrStatus || entry.status;

  return (
    <button
      type="button"
      className={isSelected ? "phase2-entry-card selected" : "phase2-entry-card"}
      onClick={onClick}
      aria-label={`Open ${sourceLabel.toLowerCase()} from ${formatEntryDate(entry.createdAt)}`}
    >
      <span className={entry.imagePreviewUrl ? "phase2-entry-visual has-image" : "phase2-entry-visual typed"}>
        {entry.imagePreviewUrl ? (
          <img src={entry.imagePreviewUrl} alt="" />
        ) : (
          <span className="phase2-typed-preview">
            <FileText size={24} aria-hidden="true" />
            {transcript ? <span>{transcript}</span> : <small>Transcript unavailable</small>}
          </span>
        )}
      </span>

      <span className="phase2-entry-card-copy">
        <span className="phase2-entry-card-date">{formatEntryDate(entry.createdAt)}</span>
        <strong>{sourceLabel}</strong>
        <span className="phase2-entry-card-meta">
          {entry.sourceType === "image" ? <ImageIcon size={13} /> : <FileText size={13} />}
          {time && <span>{time}</span>}
          {status && <span className="phase2-entry-status">{status.replaceAll("_", " ")}</span>}
        </span>
      </span>
    </button>
  );
}
