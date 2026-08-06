import type { JournalEntry } from "./journal";

export type OcrJobStatus = "PENDING" | "COMPLETED" | "FAILED";
export type OcrJobStatusFilter = "ALL" | OcrJobStatus;

export type OcrJob = {
  entryId: string;
  jobStatus: OcrJobStatus;
  status?: string | null;
  ocrStatus?: string | null;
  reviewStatus?: string | null;
  analysisStatus?: string | null;
  originalFileName?: string | null;
  contentType?: string | null;
  imagePreviewUrl?: string | null;
  ocrWordCount?: number | null;
  ocrLineCount?: number | null;
  failureReason?: string | null;
  workflowError?: string | null;
  workflowCause?: string | null;
  workflowFailedAt?: string | null;
  attemptCount: number;
  maxAttempts: number;
  remainingAttempts: number;
  canRetry: boolean;
  queuedAt?: string | null;
  lastAttemptAt?: string | null;
  processingStartedAt?: string | null;
  completedAt?: string | null;
  failedAt?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type OcrJobListResponse = {
  jobs: OcrJob[];
  count: number;
  statusFilter: OcrJobStatusFilter;
  limit: number;
  nextCursor: string | null;
};

export type OcrJobRetryResponse = {
  message: string;
  entry?: JournalEntry;
  job?: Partial<OcrJob> & { entryId: string };
};
