import type {
  InsightsMoodMonth,
  InsightsMoods,
  InsightsMoodsResponse,
  InsightsOverview,
  InsightsOverviewCoverage,
  InsightsOverviewResponse,
  InsightsRankedItem,
  InsightsThemeItem,
  InsightsThemeMonth,
  InsightsThemes,
  InsightsThemesResponse,
  InsightsThemeTrend,
} from "../types/insights";

const MAX_ITEMS = 100;
const PERIOD_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;
const TIMESTAMP_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:0\d|1[0-4]):[0-5]\d)$/;
const TRENDS = new Set<InsightsThemeTrend>(["RISING", "COOLING", "STEADY"]);

function malformed(): never {
  throw new Error("JM8 received a malformed insights response.");
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) malformed();
  return value as Record<string, unknown>;
}

function text(value: unknown, allowEmpty = false): string {
  if (typeof value !== "string" || (!allowEmpty && !value.trim()) || value.length > 500) malformed();
  return value;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) malformed();
  return value;
}

function percent(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) malformed();
  return value;
}

function date(value: unknown, nullable = false): string | null {
  if (nullable && value === null) return null;
  const result = text(value);
  const parts = TIMESTAMP_PATTERN.exec(result);
  if (!parts || !Number.isFinite(Date.parse(result))) malformed();
  const calendarDate = new Date(0);
  calendarDate.setUTCHours(0, 0, 0, 0);
  calendarDate.setUTCFullYear(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
  if (calendarDate.getUTCFullYear() !== Number(parts[1])
    || calendarDate.getUTCMonth() !== Number(parts[2]) - 1
    || calendarDate.getUTCDate() !== Number(parts[3])) malformed();
  return result;
}

function list<T>(value: unknown, parser: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > MAX_ITEMS) malformed();
  return value.map(parser);
}

function rankedItem(value: unknown): InsightsRankedItem {
  const item = record(value);
  const identity = text(item.value);
  if (identity !== identity.trim()
    || [...identity].some((character) => character.charCodeAt(0) <= 31 || character.charCodeAt(0) === 127)) malformed();
  return {
    value: identity,
    count: count(item.count),
    sharePercent: percent(item.sharePercent),
  };
}

function rankedItems(value: unknown): InsightsRankedItem[] {
  const items = list(value, rankedItem);
  const identities = new Set(items.map((item) => item.value.trim().toLocaleLowerCase()));
  if (identities.size !== items.length) malformed();
  return items;
}

function optionalRankedItem(value: unknown): InsightsRankedItem | null {
  return value === null ? null : rankedItem(value);
}

function coverage(value: unknown): InsightsOverviewCoverage {
  const item = record(value);
  const totalEntries = count(item.totalEntries);
  const analyzedEntries = count(item.analyzedEntries);
  const unanalyzedEntries = count(item.unanalyzedEntries);
  const analysisCompletionPercent = percent(item.analysisCompletionPercent);
  const firstEntryAt = date(item.firstEntryAt, true);
  const latestEntryAt = date(item.latestEntryAt, true);

  if (analyzedEntries + unanalyzedEntries !== totalEntries) malformed();
  if ((firstEntryAt === null) !== (latestEntryAt === null)) malformed();
  if (firstEntryAt && latestEntryAt && Date.parse(firstEntryAt) > Date.parse(latestEntryAt)) malformed();

  return {
    totalEntries,
    analyzedEntries,
    unanalyzedEntries,
    analysisCompletionPercent,
    firstEntryAt,
    latestEntryAt,
  };
}

function orderedMonths<T extends { period: string }>(items: T[]): T[] {
  for (let index = 1; index < items.length; index += 1) {
    if (items[index - 1].period >= items[index].period) malformed();
  }
  return items;
}

function themeItem(value: unknown): InsightsThemeItem {
  const item = record(value);
  const trend = text(item.trend) as InsightsThemeTrend;
  if (!TRENDS.has(trend)) malformed();
  return {
    ...rankedItem(item),
    firstSeenAt: date(item.firstSeenAt, true),
    lastSeenAt: date(item.lastSeenAt, true),
    recentCount: count(item.recentCount),
    previousCount: count(item.previousCount),
    trend,
  };
}

function themeMonth(value: unknown): InsightsThemeMonth {
  const item = record(value);
  const period = text(item.period);
  if (!PERIOD_PATTERN.test(period)) malformed();
  return {
    period,
    analyzedEntries: count(item.analyzedEntries),
    topThemes: rankedItems(item.topThemes),
  };
}

function moodMonth(value: unknown): InsightsMoodMonth {
  const item = record(value);
  const period = text(item.period);
  if (!PERIOD_PATTERN.test(period)) malformed();
  return {
    period,
    analyzedEntries: count(item.analyzedEntries),
    dominantMood: optionalRankedItem(item.dominantMood),
    dominantSentiment: optionalRankedItem(item.dominantSentiment),
    moods: rankedItems(item.moods),
    sentiments: rankedItems(item.sentiments),
  };
}

export function parseInsightsOverviewResponse(value: unknown): InsightsOverviewResponse {
  const wrapper = record(value);
  const item = record(wrapper.overview);
  const overview: InsightsOverview = {
    overviewVersion: text(item.overviewVersion),
    generatedAt: date(item.generatedAt) as string,
    coverage: coverage(item.coverage),
    dominantMood: optionalRankedItem(item.dominantMood),
    dominantSentiment: optionalRankedItem(item.dominantSentiment),
    topThemes: rankedItems(item.topThemes),
    topChallenges: rankedItems(item.topChallenges),
    topGoals: rankedItems(item.topGoals),
    notableProgress: rankedItems(item.notableProgress),
    behaviorPatterns: rankedItems(item.behaviorPatterns),
    recentMindsetSignals: rankedItems(item.recentMindsetSignals),
    recentAnalyzedEntries: count(item.recentAnalyzedEntries),
    reflectionPrompt: text(item.reflectionPrompt, true),
  };
  return { overview };
}

export function parseInsightsThemesResponse(value: unknown): InsightsThemesResponse {
  const wrapper = record(value);
  const item = record(wrapper.themes);
  const themes: InsightsThemes = {
    themesVersion: text(item.themesVersion),
    generatedAt: date(item.generatedAt) as string,
    coverage: coverage(item.coverage),
    windowSize: count(item.windowSize),
    themes: list(item.themes, themeItem),
    emergingThemes: list(item.emergingThemes, themeItem),
    monthlyBreakdown: orderedMonths(list(item.monthlyBreakdown, themeMonth)),
  };
  if (themes.windowSize < 1) malformed();
  return { themes };
}

export function parseInsightsMoodsResponse(value: unknown): InsightsMoodsResponse {
  const wrapper = record(value);
  const item = record(wrapper.moods);
  const shift = record(item.moodShift);
  if (typeof shift.changed !== "boolean") malformed();
  const moods: InsightsMoods = {
    moodsVersion: text(item.moodsVersion),
    generatedAt: date(item.generatedAt) as string,
    coverage: coverage(item.coverage),
    windowSize: count(item.windowSize),
    dominantMood: optionalRankedItem(item.dominantMood),
    dominantSentiment: optionalRankedItem(item.dominantSentiment),
    moods: rankedItems(item.moods),
    sentiments: rankedItems(item.sentiments),
    recentDominantMood: optionalRankedItem(item.recentDominantMood),
    previousDominantMood: optionalRankedItem(item.previousDominantMood),
    moodShift: {
      changed: shift.changed,
      from: shift.from === null ? null : text(shift.from),
      to: shift.to === null ? null : text(shift.to),
    },
    monthlyBreakdown: orderedMonths(list(item.monthlyBreakdown, moodMonth)),
  };
  if (moods.windowSize < 1) malformed();
  return { moods };
}
