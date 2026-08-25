import type {
  InsightsReport,
  InsightsReportHighlights,
  InsightsReportPeriod,
  InsightsReportResponse,
  InsightsReportStatus,
  InsightsReportType,
} from "../types/reports";
import type { InsightsOverviewCoverage, InsightsRankedItem } from "../types/insights";

const MAX_RANKED_ITEMS = 5;
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIMESTAMP_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:0\d|1[0-4]):[0-5]\d)$/;
const WEEK_PATTERN = /^(\d{4})-W(\d{2})$/;
const MONTH_PATTERN = /^(\d{4})-(\d{2})$/;

function malformed(): never {
  throw new Error("JM8 received a malformed report response.");
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) malformed();
  return value as Record<string, unknown>;
}

function text(value: unknown, maximum = 500, allowEmpty = false): string {
  if (typeof value !== "string" || value.length > maximum || (!allowEmpty && !value.trim())) malformed();
  return value;
}

function safeLabel(value: unknown): string {
  const result = text(value);
  if (result !== result.trim()
    || [...result].some((character) => character.charCodeAt(0) <= 31 || character.charCodeAt(0) === 127)) malformed();
  return result;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) malformed();
  return value;
}

function percentage(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) malformed();
  return value;
}

function calendarDate(value: unknown): string {
  const result = text(value);
  const parts = DATE_PATTERN.exec(result);
  if (!parts) malformed();
  const parsed = new Date(0);
  parsed.setUTCHours(0, 0, 0, 0);
  parsed.setUTCFullYear(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
  if (parsed.getUTCFullYear() !== Number(parts[1])
    || parsed.getUTCMonth() !== Number(parts[2]) - 1
    || parsed.getUTCDate() !== Number(parts[3])) malformed();
  return result;
}

function timestamp(value: unknown, nullable = false): string | null {
  if (nullable && value === null) return null;
  const result = text(value);
  const parts = TIMESTAMP_PATTERN.exec(result);
  if (!parts || !Number.isFinite(Date.parse(result))) malformed();
  calendarDate(`${parts[1]}-${parts[2]}-${parts[3]}`);
  return result;
}

function rankedItem(value: unknown, analyzedEntries: number): InsightsRankedItem {
  const item = record(value);
  const result = {
    value: safeLabel(item.value),
    count: count(item.count),
    sharePercent: percentage(item.sharePercent),
  };
  if (result.count > analyzedEntries) malformed();
  if (analyzedEntries === 0 || result.sharePercent !== Math.round((result.count / analyzedEntries) * 100)) malformed();
  return result;
}

function optionalRankedItem(value: unknown, analyzedEntries: number): InsightsRankedItem | null {
  return value === null ? null : rankedItem(value, analyzedEntries);
}

function rankedItems(value: unknown, analyzedEntries: number): InsightsRankedItem[] {
  if (!Array.isArray(value) || value.length > MAX_RANKED_ITEMS) malformed();
  const items = value.map((item) => rankedItem(item, analyzedEntries));
  const identities = new Set(items.map((item) => item.value.toLowerCase()));
  if (identities.size !== items.length) malformed();
  return items;
}

function shiftMonth(key: string, offset: number): string {
  const match = MONTH_PATTERN.exec(key);
  if (!match) malformed();
  const monthIndex = Number(match[1]) * 12 + Number(match[2]) - 1 + offset;
  const year = Math.floor(monthIndex / 12);
  const month = monthIndex % 12 + 1;
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`;
}

function isoWeekStart(key: string): Date {
  const match = WEEK_PATTERN.exec(key);
  if (!match) malformed();
  const year = Number(match[1]);
  const week = Number(match[2]);
  if (week < 1 || week > 53) malformed();
  const januaryFourth = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(januaryFourth);
  monday.setUTCDate(januaryFourth.getUTCDate() - ((januaryFourth.getUTCDay() + 6) % 7) + (week - 1) * 7);
  const thursday = new Date(monday);
  thursday.setUTCDate(monday.getUTCDate() + 3);
  if (thursday.getUTCFullYear() !== year) malformed();
  return monday;
}

function weekKey(date: Date): string {
  const thursday = new Date(date);
  thursday.setUTCDate(date.getUTCDate() + 3);
  const firstThursday = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 4));
  const firstMonday = new Date(firstThursday);
  firstMonday.setUTCDate(firstThursday.getUTCDate() - ((firstThursday.getUTCDay() + 6) % 7));
  const week = Math.floor((thursday.getTime() - firstMonday.getTime()) / 604800000) + 1;
  return `${thursday.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

function shiftWeek(key: string, days: number): string {
  const shifted = isoWeekStart(key);
  shifted.setUTCDate(shifted.getUTCDate() + days);
  return weekKey(shifted);
}

export function isReportWindow(type: InsightsReportType, value: string): boolean {
  try {
    if (type === "WEEKLY") {
      isoWeekStart(value);
      return true;
    }
    const match = MONTH_PATTERN.exec(value);
    return Boolean(match && Number(match[2]) >= 1 && Number(match[2]) <= 12);
  } catch {
    return false;
  }
}

function period(value: unknown, type: InsightsReportType): InsightsReportPeriod {
  const item = record(value);
  const key = text(item.key);
  const previousPeriod = text(item.previousPeriod);
  const nextPeriod = item.nextPeriod === null ? null : text(item.nextPeriod);
  const startDate = calendarDate(item.startDate);
  const endDate = calendarDate(item.endDate);
  if (typeof item.isCurrentPeriod !== "boolean") malformed();
  if (!isReportWindow(type, key) || !isReportWindow(type, previousPeriod)
    || (nextPeriod !== null && !isReportWindow(type, nextPeriod))) malformed();

  if (type === "WEEKLY") {
    const start = isoWeekStart(key);
    const expectedStart = start.toISOString().slice(0, 10);
    const end = new Date(start); end.setUTCDate(end.getUTCDate() + 6);
    if (startDate !== expectedStart || endDate !== end.toISOString().slice(0, 10)
      || previousPeriod !== shiftWeek(key, -7) || (nextPeriod !== null && nextPeriod !== shiftWeek(key, 7))) malformed();
  } else {
    const expectedStart = `${key}-01`;
    const end = new Date(`${shiftMonth(key, 1)}-01T00:00:00Z`); end.setUTCDate(0);
    if (startDate !== expectedStart || endDate !== end.toISOString().slice(0, 10)
      || previousPeriod !== shiftMonth(key, -1) || (nextPeriod !== null && nextPeriod !== shiftMonth(key, 1))) malformed();
  }
  if (item.isCurrentPeriod !== (nextPeriod === null)) malformed();
  return { key, startDate, endDate, previousPeriod, nextPeriod, isCurrentPeriod: item.isCurrentPeriod };
}

function coverage(value: unknown, reportPeriod: InsightsReportPeriod): InsightsOverviewCoverage {
  const item = record(value);
  const totalEntries = count(item.totalEntries);
  const analyzedEntries = count(item.analyzedEntries);
  const unanalyzedEntries = count(item.unanalyzedEntries);
  const analysisCompletionPercent = percentage(item.analysisCompletionPercent);
  const firstEntryAt = timestamp(item.firstEntryAt, true);
  const latestEntryAt = timestamp(item.latestEntryAt, true);
  if (analyzedEntries + unanalyzedEntries !== totalEntries
    || analysisCompletionPercent !== (totalEntries ? Math.round((analyzedEntries / totalEntries) * 100) : 0)) malformed();
  if ((firstEntryAt === null) !== (latestEntryAt === null) || (totalEntries === 0) !== (firstEntryAt === null)) malformed();
  if (firstEntryAt && latestEntryAt) {
    if (Date.parse(firstEntryAt) > Date.parse(latestEntryAt)
      || firstEntryAt.slice(0, 10) < reportPeriod.startDate || latestEntryAt.slice(0, 10) > reportPeriod.endDate) malformed();
  }
  return { totalEntries, analyzedEntries, unanalyzedEntries, analysisCompletionPercent, firstEntryAt, latestEntryAt };
}

function sameItem(item: InsightsRankedItem | null, items: InsightsRankedItem[]): boolean {
  if (item === null) return true;
  return items.some((candidate) => candidate.value === item.value
    && candidate.count === item.count && candidate.sharePercent === item.sharePercent);
}

export function parseInsightsReportResponse(
  value: unknown,
  expectedType: InsightsReportType,
  expectedWindow?: string,
): InsightsReportResponse {
  const wrapper = record(value);
  const item = record(wrapper.report);
  if (item.reportType !== expectedType) malformed();
  const reportPeriod = period(item.period, expectedType);
  if (expectedWindow && reportPeriod.key !== expectedWindow) malformed();
  const reportCoverage = coverage(item.coverage, reportPeriod);
  const analyzedEntries = reportCoverage.analyzedEntries;
  const topThemes = rankedItems(item.topThemes, analyzedEntries);
  const topChallenges = rankedItems(item.topChallenges, analyzedEntries);
  const progressSignals = rankedItems(item.progressSignals, analyzedEntries);
  const goalsMentioned = rankedItems(item.goalsMentioned, analyzedEntries);
  const behaviorPatterns = rankedItems(item.behaviorPatterns, analyzedEntries);
  const rawHighlights = record(item.highlights);
  const highlights: InsightsReportHighlights = {
    dominantMood: optionalRankedItem(rawHighlights.dominantMood, analyzedEntries),
    dominantSentiment: optionalRankedItem(rawHighlights.dominantSentiment, analyzedEntries),
    topRecurringTheme: optionalRankedItem(rawHighlights.topRecurringTheme, analyzedEntries),
    biggestChallenge: optionalRankedItem(rawHighlights.biggestChallenge, analyzedEntries),
    notableProgress: optionalRankedItem(rawHighlights.notableProgress, analyzedEntries),
    repeatedConcern: optionalRankedItem(rawHighlights.repeatedConcern, analyzedEntries),
  };
  if (!sameItem(highlights.topRecurringTheme, topThemes)
    || !sameItem(highlights.biggestChallenge, topChallenges)
    || !sameItem(highlights.notableProgress, progressSignals)
    || !sameItem(highlights.repeatedConcern, topChallenges)
    || (highlights.topRecurringTheme !== null && highlights.topRecurringTheme.count < 2)
    || (highlights.repeatedConcern !== null && highlights.repeatedConcern.count < 2)) malformed();

  const status = item.status as InsightsReportStatus;
  const expectedStatus: InsightsReportStatus = analyzedEntries === 0
    ? "EMPTY" : analyzedEntries < reportCoverage.totalEntries || analyzedEntries < 2 ? "PARTIAL" : "READY";
  if (status !== expectedStatus) malformed();

  const report: InsightsReport = {
    reportVersion: text(item.reportVersion), reportType: expectedType,
    generatedAt: timestamp(item.generatedAt) as string, status, period: reportPeriod,
    coverage: reportCoverage, highlights, topThemes, topChallenges, progressSignals,
    goalsMentioned, behaviorPatterns, reflectionPrompt: text(item.reflectionPrompt, 2000, true),
  };
  return { report };
}
