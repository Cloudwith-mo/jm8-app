export type AccountExportStatus =
  | "QUEUED"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "EXPIRED";

export type AccountExportError = {
  code: string;
  message: string;
  retryable: boolean;
};

export type AccountExportJob = {
  exportId: string;
  status: AccountExportStatus;
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
  expiresAt?: string;
  fileName?: string;
  fileSizeBytes?: number;
  entryCount?: number;
  imageCount?: number;
  askHistoryCount?: number;
  warningCount?: number;
  error?: AccountExportError;
  downloadUrl?: string;
};

export type AccountExportResponse = {
  export: AccountExportJob;
  replayed?: boolean;
};

export type AccountExportListResponse = {
  exports: AccountExportJob[];
};
