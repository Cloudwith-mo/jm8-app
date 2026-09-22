export type AskJm8AnswerStatus =
  | "ANSWERED"
  | "INSUFFICIENT_CONTEXT";


export type AskJm8SourceType =
  | "typed"
  | "image"
  | "unknown";


export type AskJm8EvidenceRelevance =
  | "high"
  | "medium"
  | "low";


export type AskJm8Request = {
  question: string;
  startDate?: string;
  endDate?: string;
};


export type AskJm8Scope = {
  startDate: string | null;
  endDate: string | null;
  firstEntryAt: string | null;
  latestEntryAt: string | null;
};


export type AskJm8Coverage = {
  totalEntries: number;
  analyzedEntries: number;
  unanalyzedEntries: number;
  analysisCompletionPercent: number;
  sourceSignalsAvailable: number;
  sourceSignalsIncluded: number;
  contextTruncated: boolean;
};


export type AskJm8AnswerBody = {
  headline: string;
  summary: string;
  explanation: string;
};


export type AskJm8Metrics = {
  mentionCount: number;
  strongestPeriod: string;
  improvementPercent: number;
  topTrigger: string;
};


export type AskJm8Evidence = {
  sourceEntryId?: string;
  paraphrase: string;
  date: string;
  sourceType: AskJm8SourceType;
  relevance: AskJm8EvidenceRelevance;
};


export type AskJm8Answer = {
  answerVersion: string;
  generatedAt: string;
  status: AskJm8AnswerStatus;
  question: string;
  scope: AskJm8Scope;
  coverage: AskJm8Coverage;
  answer: AskJm8AnswerBody;
  metrics: AskJm8Metrics;
  evidence: AskJm8Evidence[];
  takeaways: string[];
  relatedThemes: string[];
  growthSignals: string[];
  limitations: string[];
  suggestedFollowUps: string[];
};


export type AskJm8HistoryReference = {
  historyVersion: string;
  historyId: string;
  createdAt: string;
};


export type AskJm8HistorySummary = {
  historyVersion: string;
  historyId: string;
  createdAt: string;
  answerVersion: string;
  question: string;
  status: AskJm8AnswerStatus;
  scope: AskJm8Scope;
  headline: string;
  summary: string;
  evidenceCount: number;
  takeawayCount: number;
};


export type AskJm8HistoryDetail = {
  historyVersion: string;
  historyId: string;
  createdAt: string;
  answer: AskJm8Answer;
};


export type AskJm8Response = {
  answer: AskJm8Answer;
  history: AskJm8HistoryReference;
};


export type AskJm8HistoryListResponse = {
  count: number;
  history: AskJm8HistorySummary[];
  nextCursor: string | null;
};


export type AskJm8HistoryDetailResponse = {
  history: AskJm8HistoryDetail;
};


export type AskJm8HistoryDeleteResponse = {
  deleted: boolean;
  historyId: string;
};
