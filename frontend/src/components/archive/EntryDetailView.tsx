import {
  ArrowLeft, Brain, Clipboard, Copy, Download, FilePenLine, FileText,
  Image as ImageIcon, RefreshCw, Sparkles, Tag, Trash2,
} from "lucide-react";
import AnalysisHistoryCard from "../layout/AnalysisHistoryCard";
import type { JournalEntry } from "../../types/journal";

type Props = {
  entry: JournalEntry; isBusy: boolean; analysisAllowed: boolean; analysisRemaining: number | null;
  onBack: () => void; onReview: () => void; onAnalyze: () => void; onRetryOcr: () => void;
  onCopyTranscript: () => void; onExportTranscript: () => void; onDownloadImage: () => void; onDelete: () => void;
};

function parseDate(value?: string) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function humanize(value: string) {
  return value.replaceAll("_", " ").toLowerCase().replace(/^./, (letter) => letter.toUpperCase());
}

export default function EntryDetailView({ entry, isBusy, analysisAllowed, analysisRemaining, onBack,
  onReview, onAnalyze, onRetryOcr, onCopyTranscript, onExportTranscript, onDownloadImage, onDelete }: Props) {
  const date = parseDate(entry.createdAt);
  const transcript = entry.cleanText || entry.rawText;
  const analysis = entry.analysis;
  const themes = analysis?.themes?.filter((theme) => theme.trim()) || [];
  const wordCount = entry.wordCount ?? analysis?.wordCount ?? entry.ocrWordCount;
  const hasAnalysis = Boolean(analysis?.summary || analysis?.mood || analysis?.sentiment || analysis?.nextStep ||
    themes.length || typeof wordCount === "number");
  const canRetryOcr = entry.sourceType === "image" &&
    (entry.ocrStatus === "FAILED" || entry.status?.includes("FAILED"));
  const title = entry.sourceType === "image"
    ? "Scanned Journal Page"
    : entry.sourceType === "typed"
      ? "Typed Journal Entry"
      : "Journal Entry";

  return (
    <article className="phase2-entry-detail" aria-labelledby="phase2-entry-title">
      <header className="phase2-entry-detail-header">
        <div>
          <button type="button" className="phase2-back-button" onClick={onBack}><ArrowLeft size={16} /> Back to Archive</button>
          <h1 id="phase2-entry-title">{title}</h1>
          <div className="phase2-detail-meta" aria-label="Entry details">
            {date && <><span>{date.toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" })}</span><span>{date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span></>}
            {entry.sourceType && <span>Source: {entry.sourceType === "image" ? "Image" : "Typed"}</span>}
            {entry.status && <span>Status: {humanize(entry.status)}</span>}
            {entry.ocrStatus && <span>OCR: {humanize(entry.ocrStatus)}</span>}
            {entry.reviewStatus && <span>Review: {humanize(entry.reviewStatus)}</span>}
            {entry.analysisStatus && <strong>Analysis: {humanize(entry.analysisStatus)}</strong>}
          </div>
        </div>
        <div className="phase2-detail-actions" aria-label="Entry actions">
          <button type="button" onClick={onReview} disabled={isBusy || !transcript}><FilePenLine size={16} /> Review</button>
          <button type="button" onClick={onCopyTranscript} disabled={isBusy || !transcript}><Copy size={16} /> Copy</button>
          <button type="button" onClick={onExportTranscript} disabled={isBusy || !transcript}><Download size={16} /> Export</button>
          <button type="button" className="danger" onClick={onDelete} disabled={isBusy}><Trash2 size={16} /> Delete</button>
        </div>
      </header>

      <div className={`phase2-entry-layout${entry.imagePreviewUrl ? " has-image" : ""}${hasAnalysis ? " has-analysis" : ""}`}>
        {entry.imagePreviewUrl && <section className="phase2-detail-column phase2-image-column" aria-labelledby="journal-image-title">
          <header><ImageIcon size={17} /><h2 id="journal-image-title">Journal image</h2></header>
          <div className="phase2-full-image"><img src={entry.imagePreviewUrl} alt="Uploaded journal page" /></div>
          <button type="button" onClick={onDownloadImage} disabled={isBusy}><Download size={16} /> Open original image</button>
        </section>}

        <section className="phase2-detail-column phase2-transcript-column" aria-labelledby="transcript-title">
          <header><FileText size={17} /><h2 id="transcript-title">Transcript</h2></header>
          {transcript ? <div className="phase2-transcript-text">{transcript}</div> :
            <div className="phase2-detail-empty">No transcript has been returned for this entry.</div>}
          <div className="phase2-column-actions">
            <button type="button" onClick={onReview} disabled={isBusy || !transcript}><FilePenLine size={16} /> Review transcript</button>
            {canRetryOcr && <button type="button" onClick={onRetryOcr} disabled={isBusy}><RefreshCw size={16} /> Retry OCR</button>}
          </div>
        </section>

        <aside className="phase2-detail-column phase2-analysis-column" aria-labelledby="analysis-title">
          <header><Sparkles size={17} /><h2 id="analysis-title">Analysis</h2></header>
          {hasAnalysis ? <div className="phase2-analysis-content">
            {analysis?.summary && <section><h3><Brain size={15} /> Summary</h3><p>{analysis.summary}</p></section>}
            {(analysis?.mood || analysis?.sentiment || typeof wordCount === "number") && <dl className="phase2-analysis-facts">
              {analysis?.mood && <div><dt>Mood</dt><dd>{analysis.mood}</dd></div>}
              {analysis?.sentiment && <div><dt>Sentiment</dt><dd>{analysis.sentiment}</dd></div>}
              {typeof wordCount === "number" && <div><dt>Word count</dt><dd>{wordCount.toLocaleString()}</dd></div>}
            </dl>}
            {themes.length > 0 && <section><h3><Tag size={15} /> Themes</h3><div className="phase2-theme-list">{themes.map((theme) => <span key={theme}>{theme}</span>)}</div></section>}
            {analysis?.nextStep && <section className="phase2-insight"><h3><Clipboard size={15} /> Key insight</h3><p>{analysis.nextStep}</p></section>}
          </div> : <div className="phase2-detail-empty">No analysis has been returned for this entry.</div>}
          <button type="button" className="phase2-analyze-button" onClick={onAnalyze} disabled={isBusy || !analysisAllowed || !transcript}>
            <Sparkles size={16} /> {hasAnalysis ? "Analyze again" : "Analyze entry"}
          </button>
          {!analysisAllowed && <p className="phase2-limit-note">Monthly analysis allowance reached.</p>}
          {analysisAllowed && analysisRemaining !== null && <p className="phase2-limit-note">{analysisRemaining} analyses remaining this month.</p>}
          {(entry.analysisVersionCount ?? 0) > 0 && <AnalysisHistoryCard entryId={entry.entryId} versionCount={entry.analysisVersionCount} disabled={isBusy} />}
        </aside>
      </div>
    </article>
  );
}
