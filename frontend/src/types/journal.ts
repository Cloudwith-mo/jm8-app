export type Analysis = {
  status?: string;
  analyzedAt?: string;
  wordCount?: number;
  sentiment?: string;
  mood?: string;
  positiveSignalCount?: number;
  negativeSignalCount?: number;
  themes?: string[];
  summary?: string;
  nextStep?: string;
};

export type AnalysisHistoryVersion = {
  analysisVersionId: string;
  analysisSource?: string;
  analysisStatus?: string;
  analysisSchemaVersion?: string;
  analysisPromptVersion?: string;
  analysisModelId?: string;
  analysisCompletedAt?: string;
  createdAt?: string;
  analysis?: Analysis;
};

export type AnalysisHistoryResponse = {
  entryId: string;
  count: number;
  limit: number;
  versions: AnalysisHistoryVersion[];
};

export type JournalEntry = {
  PK?: string;
  SK?: string;
  GSI1PK?: string;
  GSI1SK?: string;
  entityType?: string;
  entryId: string;
  userId?: string;
  sourceType?: "typed" | "image";
  status?: string;
  analysisStatus?: string;
  analysisVersionId?: string;
  analysisVersionCount?: number;
  analysisSource?: string;
  analysisSchemaVersion?: string;
  analysisCompletedAt?: string;
  rawText?: string;
  cleanText?: string;
  wordCount?: number;
  createdAt?: string;
  updatedAt?: string;
  reviewStatus?: string;
  reviewedAt?: string;
  ocrStatus?: string;
  ocrCompletedAt?: string;
  ocrWordCount?: number;
  ocrLineCount?: number;
  s3RawBucket?: string;
  s3RawKey?: string;
  originalFileName?: string;
  contentType?: string;
  imagePreviewUrl?: string;
  ocrQueuedAt?: string;
  ocrStartedAt?: string;
  ocrLastAttemptAt?: string;
  ocrFailedAt?: string;
  ocrAttemptCount?: number;
  failureReason?: string;
  analysis?: Analysis;
};

export type UploadResponse = {
  message: string;
  upload: {
    entryId: string;
    bucket: string;
    s3Key: string;
    uploadUrl: string;
    expiresInSeconds: number;
  };
};
