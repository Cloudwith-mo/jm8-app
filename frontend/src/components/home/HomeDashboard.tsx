import type { ReactNode } from "react";
import {
  Archive,
  BookOpen,
  CalendarDays,
  FilePenLine,
  PenLine,
  Moon,
  Sparkles,
  Sun,
  Sunrise,
  Upload,
} from "lucide-react";
import type { AuthUser } from "../../auth/cognito";
import type { JournalEntry } from "../../types/journal";
import EntryCard from "../archive/EntryCard";
import { EmptyState, ErrorState, LoadingState } from "../ui/V2Primitives";

type HomeDashboardProps = {
  user: AuthUser;
  entries: JournalEntry[];
  isLoading: boolean;
  errorMessage: string;
  accountSurface: ReactNode;
  onOpenEntry: (entryId: string) => void;
  onContinueWriting: (entryId: string) => void;
  onNewEntry: () => void;
  onUpload: () => void;
  onAskJm8: () => void;
  onViewArchive: () => void;
};

function validDate(entry: JournalEntry) {
  if (!entry.createdAt) return null;
  const date = new Date(entry.createdAt);
  return Number.isNaN(date.getTime()) ? null : date;
}

function entryText(entry: JournalEntry) {
  return entry.cleanText || entry.rawText || entry.analysis?.summary || "";
}

function sortedEntries(entries: JournalEntry[]) {
  return entries.map((entry, index) => ({ entry, index, date: validDate(entry) }))
    .sort((left, right) => {
      if (left.date && right.date) return right.date.getTime() - left.date.getTime();
      if (left.date) return -1;
      if (right.date) return 1;
      return left.index - right.index;
    })
    .map(({ entry }) => entry);
}

function greetingForHour(hour: number) {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function formatDate(entry: JournalEntry) {
  const date = validDate(entry);
  return date
    ? date.toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" })
    : "Date unavailable";
}

export default function HomeDashboard({ user, entries, isLoading, errorMessage, accountSurface,
  onOpenEntry, onContinueWriting, onNewEntry, onUpload, onAskJm8, onViewArchive }: HomeDashboardProps) {
  const now = new Date();
  const greeting = greetingForHour(now.getHours());
  const GreetingIcon = now.getHours() < 6 || now.getHours() >= 18
    ? Moon
    : now.getHours() < 12
      ? Sunrise
      : Sun;
  const displayName = user.name?.trim();
  const orderedEntries = sortedEntries(entries);
  const recentEntries = orderedEntries.slice(0, 4);
  const datedEntries = orderedEntries.filter((entry) => validDate(entry));
  const reflection = datedEntries.find((entry) => {
    const date = validDate(entry)!;
    return date.getFullYear() < now.getFullYear() &&
      date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
  });
  const latestDatedEntry = datedEntries[0];
  const featuredEntry = reflection || latestDatedEntry;
  const editableEntry = datedEntries.find((entry) => entry.sourceType === "typed" && Boolean(entry.cleanText || entry.rawText));
  const documentedYears = new Set(datedEntries.map((entry) => validDate(entry)!.getFullYear())).size;
  const availableWordCount = entries.reduce((total, entry) => {
    const count = entry.wordCount ?? entry.analysis?.wordCount ?? entry.ocrWordCount;
    return total + (typeof count === "number" && Number.isFinite(count) && count >= 0 ? count : 0);
  }, 0);
  const entriesWithWordCounts = entries.filter((entry) => {
    const count = entry.wordCount ?? entry.analysis?.wordCount ?? entry.ocrWordCount;
    return typeof count === "number" && Number.isFinite(count) && count >= 0;
  }).length;
  const analyzedEntries = entries.filter((entry) => entry.analysisStatus === "COMPLETED").length;

  return (
    <div className="phase2b-home">
      <header className="phase2b-home-header">
        <div className="phase2b-greeting">
          <span className="phase2b-greeting-icon" aria-hidden="true"><GreetingIcon size={30} /></span>
          <div>
            <h1>{greeting}{displayName ? `, ${displayName}` : ""}.</h1>
            <p>Your story is being written.</p>
          </div>
        </div>
        {accountSurface}
      </header>

      {isLoading ? <LoadingState label="Preparing your journal home…" /> :
        errorMessage ? <ErrorState title="Home could not be loaded" description={errorMessage} /> : (
          <>
            <div className={entries.length ? "phase2b-hero-layout has-stats" : "phase2b-hero-layout"}>
              <section className={`phase2b-reflection${featuredEntry?.imagePreviewUrl ? " has-image" : ""}`} aria-labelledby="home-reflection-title">
              {featuredEntry ? (
                <>
                  <div className="phase2b-reflection-copy">
                    <span className="phase2b-eyebrow">{reflection ? "On this day" : "Continue your journal"}</span>
                    <h2 id="home-reflection-title">
                      {reflection
                        ? `From ${formatDate(reflection)}`
                        : `Your latest dated entry · ${formatDate(featuredEntry)}`}
                    </h2>
                    {entryText(featuredEntry) && <p>{entryText(featuredEntry)}</p>}
                    <button type="button" onClick={() => onOpenEntry(featuredEntry.entryId)}>
                      <BookOpen size={17} /> Open entry
                    </button>
                  </div>
                  {featuredEntry.imagePreviewUrl ? (
                    <div className="phase2b-reflection-image"><img src={featuredEntry.imagePreviewUrl} alt="Journal entry preview" /></div>
                  ) : (
                    <div className="phase2b-reflection-empty-art" aria-hidden="true"><BookOpen size={56} /></div>
                  )}
                </>
              ) : (
                <div className="phase2b-home-empty-reflection">
                  <EmptyState title="Your journal story starts here" description="Create a typed entry or upload a journal page to begin your private archive." />
                </div>
              )}
              </section>

              {entries.length > 0 && <section className="phase2b-stat-section" aria-labelledby="loaded-statistics-title">
                <header><div><h2 id="loaded-statistics-title">Your loaded archive</h2><p>Calculated only from entries returned in this archive load.</p></div></header>
                <dl className="phase2b-stats">
                  <div><BookOpen /><dt>Loaded entries</dt><dd>{entries.length}</dd></div>
                  <div><CalendarDays /><dt>Documented years</dt><dd>{documentedYears}</dd></div>
                  <div><FilePenLine /><dt>Available words</dt><dd>{availableWordCount.toLocaleString()}<small>across {entriesWithWordCounts} entries</small></dd></div>
                  <div><Sparkles /><dt>Analyzed entries</dt><dd>{analyzedEntries}</dd></div>
                </dl>
              </section>}
            </div>

            <section className="phase2b-quick-actions" aria-labelledby="quick-actions-title">
              <h2 id="quick-actions-title" className="phase2b-visually-hidden">Quick actions</h2>
              {editableEntry && <button type="button" onClick={() => onContinueWriting(editableEntry.entryId)}><FilePenLine /><span><strong>Continue Writing</strong><small>Edit your latest typed entry</small></span></button>}
              <button type="button" onClick={onNewEntry}><PenLine /><span><strong>New Entry</strong><small>Start a typed journal entry</small></span></button>
              <button type="button" onClick={onUpload}><Upload /><span><strong>Upload Journal</strong><small>Scan or upload a page</small></span></button>
              <button type="button" onClick={onAskJm8}><Sparkles /><span><strong>Ask JM8</strong><small>Use your journal history</small></span></button>
            </section>

            <section className="phase2b-recent" aria-labelledby="recent-entries-title">
              <header><div><h2 id="recent-entries-title">Recent entries</h2><p>Most recent valid dates first; entries without dates remain marked unavailable.</p></div>
                {entries.length > 0 && <button type="button" onClick={onViewArchive}>View all <Archive size={16} /></button>}
              </header>
              {recentEntries.length ? <div className="phase2b-recent-grid">{recentEntries.map((entry) => (
                <EntryCard key={entry.entryId} entry={entry} isSelected={false} onClick={() => onOpenEntry(entry.entryId)} />
              ))}</div> : <EmptyState title="No entries yet" description="New and uploaded journal entries will appear here." />}
            </section>
          </>
        )}
      <div className="phase2b-home-live-status" role="status" aria-live="polite">
        {!isLoading && !errorMessage && `${entries.length} entries loaded.`}
      </div>
    </div>
  );
}
