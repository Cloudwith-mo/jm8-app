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
