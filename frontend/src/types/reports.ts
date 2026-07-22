import type {
  InsightsOverviewCoverage,
  InsightsRankedItem,
} from "./insights";


export type InsightsReportType =
  | "WEEKLY"
  | "MONTHLY";

export type InsightsReportStatus =
  | "EMPTY"
  | "PARTIAL"
  | "READY";

export type InsightsReportPeriod = {
  key: string;
  startDate: string;
  endDate: string;
  previousPeriod: string;
  nextPeriod: string | null;
  isCurrentPeriod: boolean;
};

export type InsightsReportCoverage =
  InsightsOverviewCoverage;

export type InsightsReportHighlights = {
  dominantMood:
    InsightsRankedItem | null;
  dominantSentiment:
    InsightsRankedItem | null;
  topRecurringTheme:
    InsightsRankedItem | null;
  biggestChallenge:
    InsightsRankedItem | null;
  notableProgress:
    InsightsRankedItem | null;
  repeatedConcern:
    InsightsRankedItem | null;
};

export type InsightsReport = {
  reportVersion: string;
  reportType: InsightsReportType;
  generatedAt: string;
  status: InsightsReportStatus;
  period: InsightsReportPeriod;
  coverage: InsightsReportCoverage;
  highlights: InsightsReportHighlights;
  topThemes: InsightsRankedItem[];
  topChallenges: InsightsRankedItem[];
  progressSignals: InsightsRankedItem[];
  goalsMentioned: InsightsRankedItem[];
  behaviorPatterns: InsightsRankedItem[];
  reflectionPrompt: string;
};

export type InsightsReportResponse = {
  report: InsightsReport;
};
