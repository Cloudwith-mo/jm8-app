export type InsightsRankedItem = {
  value: string;
  count: number;
  sharePercent: number;
};

export type InsightsOverviewCoverage = {
  totalEntries: number;
  analyzedEntries: number;
  unanalyzedEntries: number;
  analysisCompletionPercent: number;
  firstEntryAt: string | null;
  latestEntryAt: string | null;
};

export type InsightsOverview = {
  overviewVersion: string;
  generatedAt: string;
  coverage: InsightsOverviewCoverage;
  dominantMood: InsightsRankedItem | null;
  dominantSentiment: InsightsRankedItem | null;
  topThemes: InsightsRankedItem[];
  topChallenges: InsightsRankedItem[];
  topGoals: InsightsRankedItem[];
  notableProgress: InsightsRankedItem[];
  behaviorPatterns: InsightsRankedItem[];
  recentMindsetSignals: InsightsRankedItem[];
  recentAnalyzedEntries: number;
  reflectionPrompt: string;
};

export type InsightsOverviewResponse = {
  overview: InsightsOverview;
};
