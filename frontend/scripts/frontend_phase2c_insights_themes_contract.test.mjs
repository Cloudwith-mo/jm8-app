import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  parseInsightsMoodsResponse,
  parseInsightsOverviewResponse,
  parseInsightsThemesResponse,
} from "../src/api/insightsValidation.ts";
import { parseAppRoute, serializeAppRoute } from "../src/navigation/appRoute.ts";

function source(relativePath) {
  return fs.readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

const page = source("src/pages/ArchivePage.tsx");
const appRoute = source("src/navigation/appRoute.ts");
const sidebar = source("src/components/layout/ArchiveSidebar.tsx");
const insights = source("src/components/insights/InsightsOverviewPanel.tsx");
const themes = source("src/components/insights/InsightsTrendsPanel.tsx");
const chart = source("src/components/insights/InsightsLineChart.tsx");
const validator = source("src/api/insightsValidation.ts");
const api = source("src/api/client.ts");
const styles = source("src/styles/phase2c-insights-themes.css");
const packageJson = JSON.parse(source("package.json"));

const coverage = {
  totalEntries: 3,
  analyzedEntries: 2,
  unanalyzedEntries: 1,
  analysisCompletionPercent: 67,
  firstEntryAt: "2026-01-01T00:00:00Z",
  latestEntryAt: "2026-02-01T00:00:00Z",
};
const ranked = { value: "alpha", count: 2, sharePercent: 100 };

function overviewFixture() {
  return { overview: {
    overviewVersion: "v1", generatedAt: "2026-02-02T00:00:00Z", coverage,
    dominantMood: ranked, dominantSentiment: null, topThemes: [ranked], topChallenges: [],
    topGoals: [], notableProgress: [], behaviorPatterns: [], recentMindsetSignals: [],
    recentAnalyzedEntries: 2, reflectionPrompt: "",
  } };
}

function themesFixture() {
  return { themes: {
    themesVersion: "v1", generatedAt: "2026-02-02T00:00:00Z", coverage, windowSize: 5,
    themes: [{ ...ranked, firstSeenAt: coverage.firstEntryAt, lastSeenAt: coverage.latestEntryAt, recentCount: 2, previousCount: 0, trend: "RISING" }],
    emergingThemes: [], monthlyBreakdown: [
      { period: "2026-01", analyzedEntries: 1, topThemes: [{ ...ranked, count: 1 }] },
      { period: "2026-02", analyzedEntries: 1, topThemes: [{ ...ranked, count: 1 }] },
    ],
  } };
}

function moodsFixture() {
  return { moods: {
    moodsVersion: "v1", generatedAt: "2026-02-02T00:00:00Z", coverage, windowSize: 5,
    dominantMood: ranked, dominantSentiment: null, moods: [ranked], sentiments: [],
    recentDominantMood: ranked, previousDominantMood: null,
    moodShift: { changed: false, from: null, to: null },
    monthlyBreakdown: [
      { period: "2026-01", analyzedEntries: 1, dominantMood: ranked, dominantSentiment: null, moods: [ranked], sentiments: [] },
      { period: "2026-02", analyzedEntries: 1, dominantMood: ranked, dominantSentiment: null, moods: [ranked], sentiments: [] },
    ],
  } };
}

test("Insights and Themes are separate URL-addressable sidebar destinations", () => {
  assert.match(sidebar, /label: "Insights"[\s\S]*section: "insights"/);
  assert.match(sidebar, /label: "Themes"[\s\S]*section: "themes"/);
  assert.match(appRoute, /"home", "archive", "insights", "themes"/);
  assert.match(appRoute, /route\.view === "themes"/);
  assert.match(appRoute, /searchParams\.set\("theme", route\.themeId\)/);
  assert.match(page, /addEventListener\("popstate", handlePopState\)/);
  assert.match(page, /setSelectedThemeRouteId\(routedApp\.themeId\)/);
  assert.doesNotMatch(page + sidebar, /insightsTrends/);
  assert.doesNotMatch(JSON.stringify(packageJson.dependencies), /router/i);
});

test("theme selections use opaque deterministic identifiers and preserve entry-detail navigation", () => {
  assert.match(themes, /getThemeRouteId\(value: string\)/);
  assert.match(themes, /Math\.imul\(hash, 0x01000193\)/);
  assert.match(themes, /onSelectTheme\(id\)/);
  assert.doesNotMatch(appRoute, /searchParams\.set\("theme", theme\.value\)/);
  assert.match(themes, /onOpenEntry\(entry\.entryId\)/);
  assert.match(page, /onOpenEntry=\{\(entryId\) => void openEntry\(entryId\)\}/);
  assert.match(appRoute, /searchParams\.set\("entry", route\.entryId\)/);
});

test("Insights uses only real overview and mood contracts without duplicate theme requests", () => {
  assert.match(insights, /getInsightsOverview\(controller\.signal\)/);
  assert.match(insights, /getInsightsMoods\(controller\.signal\)/);
  assert.doesNotMatch(insights, /getInsightsThemes/);
  assert.match(insights, /coverage\.totalEntries/);
  assert.match(insights, /overview\.topThemes/);
  assert.match(insights, /overview\.topChallenges/);
  assert.match(insights, /moods\?\.monthlyBreakdown/);
  assert.doesNotMatch(insights, /1,247|482,671|56|Growth Over Time|Greatest Growth Areas|Most Active Days/);
});

test("every mixed surface names its API or loaded-entry provenance", () => {
  assert.match(insights, /Overview API/);
  assert.match(insights, /Moods API/);
  assert.match(insights, /returned by the overview API/);
  assert.match(insights, /returned by the moods endpoint/);
  assert.match(themes, /themes endpoint|returned top-theme data|Supporting loaded entries/i);
  assert.match(themes, /currently loaded archive page/);
});

test("Themes uses its exact endpoint plus loaded analyzed entries and omits unsupported detail", () => {
  assert.match(themes, /getInsightsThemes\(controller\.signal\)/);
  assert.doesNotMatch(themes, /getInsightsOverview|getInsightsMoods/);
  assert.match(themes, /data\.themes\.map/);
  assert.match(themes, /data\.monthlyBreakdown/);
  assert.match(themes, /entry\.analysis\.themes\.some/);
  assert.match(themes, /Supporting loaded entries/);
  assert.doesNotMatch(themes, /Related keywords|Theme description|Supporting quote|Sample entries/);
});

test("ordered charts are code-native and provide an accessible data table", () => {
  assert.match(chart, /if \(points\.length < 2\) return null/);
  assert.match(chart, /<svg[\s\S]*role="img"/);
  assert.match(chart, /<polyline/);
  assert.match(chart, /<table className="phase2c-visually-hidden">/);
  assert.match(chart, /<caption>\{title\}<\/caption>/);
  assert.doesNotMatch(chart, /canvas|chart\.js|recharts/);
  assert.match(validator, /items\[index - 1\]\.period >= items\[index\]\.period/);
});

test("loading, empty, partial, retry, and protected-endpoint failures stay explicit", () => {
  assert.match(insights, /Promise\.allSettled/);
  assert.match(insights, /Some insight data is temporarily unavailable/);
  assert.match(insights, /Loading your private insights/);
  assert.match(insights, />Retry<\/button>/);
  assert.match(themes, /Loading your private themes/);
  assert.match(themes, /No themes have been returned/);
  assert.match(themes, />Retry<\/button>/);
  assert.match(api, /if \(!response\.ok\)[\s\S]*throw new ApiRequestError/);
  assert.match(api, /Authorization: `Bearer \$\{accessToken\}`/);
});

test("malformed theme URL identifiers cannot be accepted or written", () => {
  assert.match(appRoute, /\^theme-\[a-f0-9\]\{8\}\$/);
  assert.equal(parseAppRoute("https://app.test/?view=themes&theme=discipline").themeId, null);
  assert.match(page, /if \(!\/\^theme-\[a-f0-9\]\{8\}\$\/\.test\(themeId\)\) return/);
  const valid = parseAppRoute("https://app.test/?view=themes&theme=theme-0123abcd");
  assert.match(serializeAppRoute(valid, "https://app.test/"), /theme=theme-0123abcd/);
  assert.doesNotMatch(appRoute, /searchParams\.set\("theme", (?:value|theme\.value|selected\.value)\)/);
});

test("all insights response families accept canonical API shapes", () => {
  assert.deepEqual(parseInsightsOverviewResponse(overviewFixture()), overviewFixture());
  assert.deepEqual(parseInsightsThemesResponse(themesFixture()), themesFixture());
  assert.deepEqual(parseInsightsMoodsResponse(moodsFixture()), moodsFixture());
});

test("malformed analytics fail closed with sanitized errors", () => {
  const cases = [];
  const badCount = overviewFixture(); badCount.overview.coverage.totalEntries = "invalid-count"; cases.push(() => parseInsightsOverviewResponse(badCount));
  const badPercent = overviewFixture(); badPercent.overview.topThemes[0].sharePercent = 101; cases.push(() => parseInsightsOverviewResponse(badPercent));
  const badDate = overviewFixture(); badDate.overview.generatedAt = "not-a-date"; cases.push(() => parseInsightsOverviewResponse(badDate));
  const ambiguousDate = overviewFixture(); ambiguousDate.overview.generatedAt = "1"; cases.push(() => parseInsightsOverviewResponse(ambiguousDate));
  const impossibleDate = overviewFixture(); impossibleDate.overview.generatedAt = "2026-02-31T00:00:00Z"; cases.push(() => parseInsightsOverviewResponse(impossibleDate));
  const badArray = themesFixture(); badArray.themes.themes = {}; cases.push(() => parseInsightsThemesResponse(badArray));
  const badPeriod = moodsFixture(); badPeriod.moods.monthlyBreakdown[0].period = "2026-99"; cases.push(() => parseInsightsMoodsResponse(badPeriod));
  const unordered = themesFixture(); unordered.themes.monthlyBreakdown.reverse(); cases.push(() => parseInsightsThemesResponse(unordered));
  const badTrend = themesFixture(); badTrend.themes.themes[0].trend = "UNKNOWN"; cases.push(() => parseInsightsThemesResponse(badTrend));
  for (const invoke of cases) assert.throws(invoke, (error) => error instanceof Error && error.message === "JM8 received a malformed insights response.");
});

test("requests abort on replacement and unmount and suppress stale completion", () => {
  for (const component of [insights, themes]) {
    assert.match(component, /controllerRef\.current\?\.abort\(\)/);
    assert.match(component, /request !== requestRef\.current/);
    assert.match(component, /return \(\) => controllerRef\.current\?\.abort\(\)/);
  }
  assert.match(api, /getInsightsOverview\(signal\?: AbortSignal\)/);
  assert.match(api, /parseInsightsOverviewResponse/);
  assert.match(api, /parseInsightsThemesResponse/);
  assert.match(api, /parseInsightsMoodsResponse/);
});

test("Phase 2C is responsive, keyboard-visible, reduced-motion safe, and dependency-free", () => {
  assert.match(styles, /overflow-x: clip/);
  assert.doesNotMatch(styles, /overflow-x: auto/);
  assert.match(styles, /:focus-visible/);
  assert.match(styles, /min-height: 44px/);
  assert.match(styles, /@media \(max-width: 1180px\)/);
  assert.match(styles, /@media \(max-width: 760px\)/);
  assert.match(styles, /@media \(max-width: 480px\)/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
  const combined = [page, sidebar, insights, themes, chart, validator, styles].join("\n");
  assert.doesNotMatch(combined, /console\.|demo-user|x-user-id|\|\| true|\beval\b|set -x/i);
});
