export type HistoricalReanalysisJobStatus =
  | "QUEUED"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED";

export type HistoricalReanalysisJobStatusFilter =
  | "ALL"
  | HistoricalReanalysisJobStatus;

export type HistoricalReanalysisJob = {
  jobId: string;
  retryOfJobId?: string;
  status: HistoricalReanalysisJobStatus;
  totalEntries?: number;
  eligibleEntries?: number;
  estimatedBedrockRequests?: number;
  processedEntries?: number;
  completedEntries?: number;
  failedEntries?: number;
  skippedEntries?: number;
  remainingEntries?: number;
  pageSize?: number;
  createdAt?: string;
  startedAt?: string;
  completedAt?: string;
  updatedAt?: string;
  failureCode?: string;
  failureMessage?: string;
};

export type HistoricalReanalysisInventory = {
  totalEntries?: number;
  entriesWithUsableText?: number;
  entriesWithoutUsableText?: number;
  alreadyVersionedEntries?: number;
  legacyAnalyzedEntries?: number;
  neverAnalyzedEntries?: number;
  failedOrIncompleteEntries?: number;
  eligibleEntries?: number;
  skippedEntries?: number;
  estimatedBedrockRequests?: number;
};

export type HistoricalReanalysisJobListResponse = {
  jobs: HistoricalReanalysisJob[];
  count: number;
  statusFilter: HistoricalReanalysisJobStatusFilter;
  limit: number;
};

export type HistoricalReanalysisDryRunResponse = {
  dryRun: true;
  readOnly: true;
  mutationsPerformed: number;
  bedrockInvocationsPerformed: number;
  inventory: HistoricalReanalysisInventory;
};

export type HistoricalReanalysisJobAcceptedResponse = {
  message: string;
  job: HistoricalReanalysisJob;
};
