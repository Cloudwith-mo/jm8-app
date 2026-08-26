import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  FileScan,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import {
  ApiRequestError,
  listOcrJobs,
  retryOcrJob,
} from "../../api/client";
import type {
  OcrJob,
  OcrJobStatusFilter,
} from "../../types/ocrJobs";
import type { ToastKind } from "../ui/ToastStack";

type Props = {
  onNotify: (kind: ToastKind, title: string, message?: string) => void;
};

const FILTERS: Array<{ label: string; value: OcrJobStatusFilter }> = [
  { label: "All", value: "ALL" },
  { label: "Processing", value: "PENDING" },
  { label: "Completed", value: "COMPLETED" },
  { label: "Failed", value: "FAILED" },
];

function formatDate(value?: string | null) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not recorded";
  return date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function errorMessage(error: unknown) {
  if (error instanceof ApiRequestError) return error.message;
  return error instanceof Error ? error.message : "OCR jobs could not be loaded.";
}

function StatusIcon({ status }: { status: OcrJob["jobStatus"] }) {
  if (status === "COMPLETED") return <CheckCircle2 size={20} />;
  if (status === "FAILED") return <AlertTriangle size={20} />;
  return <RefreshCw className="ocr-job-spin" size={20} />;
}

export default function OcrJobsPanel({ onNotify }: Props) {
  const controllerRef = useRef<AbortController | null>(null);
  const requestRef = useRef(0);
  const [jobs, setJobs] = useState<OcrJob[]>([]);
  const [filter, setFilter] = useState<OcrJobStatusFilter>("ALL");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [retryingEntryId, setRetryingEntryId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const load = useCallback(async (status: OcrJobStatusFilter, cursor?: string) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const request = ++requestRef.current;
    if (cursor) setIsLoadingMore(true);
    else setIsLoading(true);
    try {
      const result = await listOcrJobs(status, 20, cursor, controller.signal);
      if (controller.signal.aborted || request !== requestRef.current) return;
      setJobs((current) => cursor ? [...current, ...result.jobs] : result.jobs);
      setNextCursor(result.nextCursor);
      setError("");
      setLastUpdated(new Date());
    } catch (loadError) {
      if (controller.signal.aborted || request !== requestRef.current) return;
      setError(errorMessage(loadError));
    } finally {
      if (!controller.signal.aborted && request === requestRef.current) {
        setIsLoading(false);
        setIsLoadingMore(false);
      }
    }
  }, []);

  useEffect(() => {
    void load(filter);
    return () => controllerRef.current?.abort();
  }, [filter, load]);

  async function retry(job: OcrJob) {
    setRetryingEntryId(job.entryId);
    try {
      await retryOcrJob(job.entryId);
      onNotify("success", "OCR retry queued", job.originalFileName || "Journal page");
      await load(filter);
    } catch (retryError) {
      const message = errorMessage(retryError);
      setError(message);
      onNotify("error", "OCR retry failed", message);
    } finally {
      setRetryingEntryId(null);
    }
  }

  const completed = jobs.filter((job) => job.jobStatus === "COMPLETED").length;
  const pending = jobs.filter((job) => job.jobStatus === "PENDING").length;
  const failed = jobs.filter((job) => job.jobStatus === "FAILED").length;

  return (
    <section className="ocr-jobs-page">
      <header className="ocr-jobs-header">
        <div>
          <p className="ocr-jobs-kicker"><FileScan size={16} /> Document processing</p>
          <h1>OCR Jobs</h1>
          <p>Monitor every journal scan, inspect failures, and safely retry eligible jobs.</p>
        </div>
        <button className="ocr-jobs-refresh" onClick={() => void load(filter)} disabled={isLoading}>
          <RefreshCw size={17} /> Refresh
        </button>
      </header>

      <div className="ocr-jobs-overview">
        <article><span>Loaded</span><strong>{jobs.length}</strong><small>current results</small></article>
        <article><span>Processing</span><strong>{pending}</strong><small>in progress</small></article>
        <article><span>Completed</span><strong>{completed}</strong><small>ready to review</small></article>
        <article><span>Failed</span><strong>{failed}</strong><small>need attention</small></article>
      </div>

      <div className="ocr-jobs-toolbar">
        <div className="ocr-job-filters" role="group" aria-label="Filter OCR jobs">
          {FILTERS.map((item) => (
            <button key={item.value} className={filter === item.value ? "active" : ""} onClick={() => setFilter(item.value)}>
              {item.label}
            </button>
          ))}
        </div>
        <small>{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "Loading latest jobs"}</small>
      </div>

      {error && <div className="ocr-jobs-alert"><AlertTriangle size={18} /><span>{error}</span><button onClick={() => void load(filter)}>Try again</button></div>}

      {isLoading ? (
        <div className="ocr-jobs-empty"><RefreshCw className="ocr-job-spin" size={25} /><h2>Loading OCR jobs</h2></div>
      ) : jobs.length === 0 ? (
        <div className="ocr-jobs-empty"><FileScan size={28} /><h2>No {filter === "ALL" ? "OCR" : filter.toLowerCase()} jobs</h2><p>Uploaded journal pages will appear here as they move through OCR.</p></div>
      ) : (
        <div className="ocr-jobs-list">
          {jobs.map((job) => {
            const failure = job.failureReason || job.workflowError || job.workflowCause;
            return (
              <article className={`ocr-job-card status-${job.jobStatus.toLowerCase()}`} key={job.entryId}>
                <div className={`ocr-job-icon ${job.jobStatus.toLowerCase()}`}><StatusIcon status={job.jobStatus} /></div>
                <div className="ocr-job-content">
                  <header>
                    <div><h2>{job.originalFileName || "Journal page"}</h2><small>{job.entryId}</small></div>
                    <span className={`ocr-job-status ${job.jobStatus.toLowerCase()}`}>{job.jobStatus === "PENDING" ? "PROCESSING" : job.jobStatus}</span>
                  </header>
                  <dl className="ocr-job-metrics">
                    <div><dt>Attempts</dt><dd>{job.attemptCount} / {job.maxAttempts}</dd></div>
                    <div><dt>Words</dt><dd>{job.ocrWordCount ?? "—"}</dd></div>
                    <div><dt>Started</dt><dd>{formatDate(job.processingStartedAt || job.queuedAt)}</dd></div>
                    <div><dt>Updated</dt><dd>{formatDate(job.updatedAt)}</dd></div>
                  </dl>
                  {failure && <div className="ocr-job-failure"><AlertTriangle size={17} /><div><strong>Why it failed</strong><p>{failure}</p></div></div>}
                  <footer>
                    <span><Clock3 size={15} /> {job.remainingAttempts} retries remaining</span>
                    {job.canRetry && (
                      <button onClick={() => void retry(job)} disabled={retryingEntryId === job.entryId}>
                        <RotateCcw size={16} /> {retryingEntryId === job.entryId ? "Queuing…" : "Retry OCR"}
                      </button>
                    )}
                  </footer>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {nextCursor && !isLoading && (
        <button className="ocr-jobs-load-more" onClick={() => void load(filter, nextCursor)} disabled={isLoadingMore}>
          {isLoadingMore ? "Loading…" : "Load more jobs"}
        </button>
      )}
    </section>
  );
}
