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


export type InsightsThemeTrend =
  | "RISING"
  | "COOLING"
  | "STEADY";

export type InsightsThemeItem =
  InsightsRankedItem & {
    firstSeenAt: string | null;
    lastSeenAt: string | null;
    recentCount: number;
    previousCount: number;
    trend: InsightsThemeTrend;
  };

export type InsightsThemeMonth = {
  period: string;
  analyzedEntries: number;
  topThemes: InsightsRankedItem[];
};

export type InsightsThemes = {
  themesVersion: string;
  generatedAt: string;
  coverage: InsightsOverviewCoverage;
  windowSize: number;
  themes: InsightsThemeItem[];
  emergingThemes: InsightsThemeItem[];
  monthlyBreakdown: InsightsThemeMonth[];
};

export type InsightsThemesResponse = {
  themes: InsightsThemes;
};

export type InsightsMoodShift = {
  changed: boolean;
  from: string | null;
  to: string | null;
};

export type InsightsMoodMonth = {
  period: string;
  analyzedEntries: number;
  dominantMood: InsightsRankedItem | null;
  dominantSentiment: InsightsRankedItem | null;
  moods: InsightsRankedItem[];
  sentiments: InsightsRankedItem[];
};

export type InsightsMoods = {
  moodsVersion: string;
  generatedAt: string;
  coverage: InsightsOverviewCoverage;
  windowSize: number;
  dominantMood: InsightsRankedItem | null;
  dominantSentiment: InsightsRankedItem | null;
  moods: InsightsRankedItem[];
  sentiments: InsightsRankedItem[];
  recentDominantMood:
    InsightsRankedItem | null;
  previousDominantMood:
    InsightsRankedItem | null;
  moodShift: InsightsMoodShift;
  monthlyBreakdown: InsightsMoodMonth[];
};

export type InsightsMoodsResponse = {
  moods: InsightsMoods;
};
