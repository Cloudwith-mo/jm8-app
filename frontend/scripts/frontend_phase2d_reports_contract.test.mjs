import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { isReportWindow, parseInsightsReportResponse } from "../src/api/reportsValidation.ts";

function source(relativePath) {
  return fs.readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

const page = source("src/pages/ArchivePage.tsx");
const reports = source("src/components/insights/ReportsPanel.tsx");
const styles = source("src/components/insights/ReportsPanel.css");
const validator = source("src/api/reportsValidation.ts");
const api = source("src/api/client.ts");
const sidebar = source("src/components/layout/ArchiveSidebar.tsx");
const packageJson = JSON.parse(source("package.json"));

const ranked = { value: "alpha", count: 2, sharePercent: 100 };

function reportFixture(type = "WEEKLY") {
  const weekly = type === "WEEKLY";
  return { report: {
    reportVersion: "1.0", reportType: type, generatedAt: "2026-07-21T16:00:00+00:00", status: "READY",
    period: weekly ? {
      key: "2026-W30", startDate: "2026-07-20", endDate: "2026-07-26",
      previousPeriod: "2026-W29", nextPeriod: null, isCurrentPeriod: true,
    } : {
      key: "2026-07", startDate: "2026-07-01", endDate: "2026-07-31",
      previousPeriod: "2026-06", nextPeriod: null, isCurrentPeriod: true,
    },
    coverage: {
      totalEntries: 2, analyzedEntries: 2, unanalyzedEntries: 0, analysisCompletionPercent: 100,
      firstEntryAt: "2026-07-20T10:00:00+00:00", latestEntryAt: "2026-07-21T10:00:00+00:00",
    },
    highlights: {
      dominantMood: ranked, dominantSentiment: ranked, topRecurringTheme: ranked,
      biggestChallenge: ranked, notableProgress: ranked, repeatedConcern: ranked,
    },
    topThemes: [ranked], topChallenges: [ranked], progressSignals: [ranked],
    goalsMentioned: [ranked], behaviorPatterns: [ranked], reflectionPrompt: "Consider the returned period.",
  } };
}

function emptyFixture() {
  const result = reportFixture("MONTHLY");
  result.report.status = "EMPTY";
  result.report.coverage = {
    totalEntries: 0, analyzedEntries: 0, unanalyzedEntries: 0, analysisCompletionPercent: 0,
    firstEntryAt: null, latestEntryAt: null,
  };
  result.report.highlights = {
    dominantMood: null, dominantSentiment: null, topRecurringTheme: null,
    biggestChallenge: null, notableProgress: null, repeatedConcern: null,
  };
  result.report.topThemes = [];
  result.report.topChallenges = [];
  result.report.progressSignals = [];
  result.report.goalsMentioned = [];
  result.report.behaviorPatterns = [];
  result.report.reflectionPrompt = "";
  return result;
}

test("Reports uses canonical URL-backed weekly and monthly routing", () => {
  assert.match(page, /period === "monthly" \? "MONTHLY" : "WEEKLY"/);
  assert.match(page, /searchParams\.set\("period", route\.type === "WEEKLY" \? "weekly" : "monthly"\)/);
  assert.match(page, /searchParams\.set\("window", route\.window\)/);
  assert.match(page, /setReportRoute\(routedReport, "replace"\)/);
  assert.match(page, /addEventListener\("popstate", handlePopState\)/);
  assert.match(reports, /onNavigate\(\{ type: "WEEKLY", window: null \}\)/);
  assert.match(reports, /onNavigate\(\{ type: "MONTHLY", window: null \}\)/);
  assert.doesNotMatch(JSON.stringify(packageJson.dependencies), /router/i);
});

test("invalid report periods and windows normalize to a safe current weekly or monthly request", () => {
  assert.match(page, /period === "weekly" \|\| period === "monthly"/);
  assert.match(page, /isReportWindow\(type, candidate\) \? candidate : null/);
  assert.equal(isReportWindow("WEEKLY", "2026-W30"), true);
  assert.equal(isReportWindow("WEEKLY", "2026-W54"), false);
  assert.equal(isReportWindow("MONTHLY", "2026-07"), true);
  assert.equal(isReportWindow("MONTHLY", "2026-13"), false);
});

test("entry detail retains precedence over report section routing", () => {
  assert.match(page, /if \(url\.searchParams\.get\("entry"\)\) return "archive"/);
  assert.match(page, /searchParams\.set\("entry", entryId\)/);
  assert.match(page, /url\.searchParams\.delete\("entry"\)[\s\S]*url\.searchParams\.set\("view", "reports"\)/);
});

test("weekly and monthly endpoint responses pass exact runtime validation", () => {
  assert.deepEqual(parseInsightsReportResponse(reportFixture("WEEKLY"), "WEEKLY"), reportFixture("WEEKLY"));
  assert.deepEqual(parseInsightsReportResponse(reportFixture("MONTHLY"), "MONTHLY"), reportFixture("MONTHLY"));
  assert.deepEqual(parseInsightsReportResponse(emptyFixture(), "MONTHLY"), emptyFixture());
});

test("missing is rejected while explicit zero remains valid in an empty report", () => {
  const missing = emptyFixture();
  delete missing.report.coverage.totalEntries;
  assert.throws(() => parseInsightsReportResponse(missing, "MONTHLY"), /malformed report response/);
  assert.equal(parseInsightsReportResponse(emptyFixture(), "MONTHLY").report.coverage.totalEntries, 0);
});

test("invalid numbers, percentages, dates, windows, status, and duplicates fail closed", () => {
  const invocations = [];
  const negative = reportFixture(); negative.report.coverage.totalEntries = -1; invocations.push(() => parseInsightsReportResponse(negative, "WEEKLY"));
  const percent = reportFixture(); percent.report.topThemes[0].sharePercent = 101; invocations.push(() => parseInsightsReportResponse(percent, "WEEKLY"));
  const date = reportFixture(); date.report.period.endDate = "2026-07-32"; invocations.push(() => parseInsightsReportResponse(date, "WEEKLY"));
  const generated = reportFixture(); generated.report.generatedAt = "1"; invocations.push(() => parseInsightsReportResponse(generated, "WEEKLY"));
  const window = reportFixture(); window.report.period.key = "2026-W54"; invocations.push(() => parseInsightsReportResponse(window, "WEEKLY"));
  const status = reportFixture(); status.report.status = "EMPTY"; invocations.push(() => parseInsightsReportResponse(status, "WEEKLY"));
  const duplicate = reportFixture(); duplicate.report.topThemes.push(ranked); invocations.push(() => parseInsightsReportResponse(duplicate, "WEEKLY"));
  const mismatch = reportFixture(); invocations.push(() => parseInsightsReportResponse(mismatch, "MONTHLY"));
  const wrongRequestedWindow = reportFixture(); invocations.push(() => parseInsightsReportResponse(wrongRequestedWindow, "WEEKLY", "2026-W29"));
  for (const invoke of invocations) assert.throws(invoke, (error) => error instanceof Error && error.message === "JM8 received a malformed report response.");
});

test("report requests are real, abortable, and protected against stale replacement", () => {
  assert.match(api, /getWeeklyReport\([\s\S]*signal\?: AbortSignal/);
  assert.match(api, /getMonthlyReport\([\s\S]*signal\?: AbortSignal/);
  assert.match(api, /parseInsightsReportResponse/);
  assert.match(reports, /controllerRef\.current\?\.abort\(\)/);
  assert.match(reports, /request !== requestRef\.current/);
  assert.match(reports, /return \(\) => controller\.abort\(\)/);
  assert.match(reports, /visibleReport = report && report\.reportType === reportType/);
});

test("weekly and monthly are dedicated truthful dashboards", () => {
  assert.match(reports, /function WeeklyDashboard/);
  assert.match(reports, /function MonthlyDashboard/);
  assert.match(reports, /Weekly Report/);
  assert.match(reports, /Monthly Report/);
  assert.match(reports, /report\.coverage\.totalEntries/);
  assert.match(reports, /report\.highlights\.biggestChallenge/);
  assert.match(reports, /report\.progressSignals/);
  assert.match(reports, /report\.reflectionPrompt/);
});

test("unsupported and empty modules are omitted instead of fabricated", () => {
  assert.match(reports, /if \(!items\.length\) return null/);
  assert.match(reports, /if \(!returned\.length\) return null/);
  assert.match(reports, /if \(!prompt\.trim\(\)\) return null/);
  assert.doesNotMatch(reports, /4,215|11,842|27-day|87%|Tuesday|Overthinking/);
  assert.doesNotMatch(reports, />\s*(?:Daily|Yearly|Custom|Export(?: PDF)?)\s*</i);
  assert.doesNotMatch(reports, /date picker|calendar activity|word count over time/i);
  assert.doesNotMatch(reports, /polyline|mood trend|writing activity/i);
});

test("sparse, loading, switching, retry, and safe error states are explicit", () => {
  assert.match(reports, /report\.status === "EMPTY"/);
  assert.match(reports, /report\.status === "PARTIAL"/);
  assert.match(reports, /Loading \{heading\.toLowerCase\(\)\}/);
  assert.match(reports, /Report could not be loaded/);
  assert.match(reports, />Try again<\/button>/);
  assert.match(reports, /setReport\(null\)/);
  assert.doesNotMatch(reports, /error instanceof Error|error\.message|response\.body/);
});

test("ranked report data is accessible without a misleading chart", () => {
  assert.match(reports, /<progress max="100" value=\{item\.sharePercent\} aria-label=/);
  assert.match(reports, /aria-label=\{`\$\{report\.reportType\.toLowerCase\(\)\} report coverage metrics`\}/);
  assert.match(reports, /aria-label="Report status"/);
  assert.match(reports, /aria-label=\{`\$\{heading\} period navigation`\}/);
  assert.doesNotMatch(reports, /<canvas|<polyline|<table className=".*chart/);
});

test("Phase 2D preserves prior routes, security, responsive behavior, and dependency scope", () => {
  for (const route of ["home", "archive", "insights", "themes", "reports", "askJm8", "ocrJobs", "analysisJobs"]) {
    assert.match(page, new RegExp(`"${route}"`));
  }
  assert.match(sidebar, /label: "Reports"[\s\S]*section: "reports"/);
  assert.match(styles, /overflow-x: clip/);
  assert.match(styles, /min-height: 44px/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /@media \(max-width: 1120px\)/);
  assert.match(styles, /@media \(max-width: 780px\)/);
  assert.match(styles, /@media \(max-width: 520px\)/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
  const combined = [page, reports, styles, validator].join("\n");
  assert.doesNotMatch(combined, /console\.|demo-user|x-user-id|\|\| true|\beval\b|set -x/i);
});
