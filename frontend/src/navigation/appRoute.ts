import type { InsightsReportType } from "../types/reports.ts";
import { isReportWindow } from "../api/reportsValidation.ts";

export type AppView =
  | "home"
  | "archive"
  | "insights"
  | "themes"
  | "reports"
  | "askJm8"
  | "ocrJobs"
  | "analysisJobs";

export type ReportRoute = {
  type: InsightsReportType;
  window: string | null;
};

export type AppRoute = {
  view: AppView;
  entryId: string | null;
  themeId: string | null;
  report: ReportRoute;
};

const APP_VIEWS = new Set<AppView>([
  "home", "archive", "insights", "themes", "reports", "askJm8", "ocrJobs", "analysisJobs",
]);

const ENTRY_ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/;
const THEME_ID_PATTERN = /^theme-[a-f0-9]{8}$/;

function one(params: URLSearchParams, name: string): string | null {
  const values = params.getAll(name);
  return values.length === 1 ? values[0] : null;
}

export function isSafeEntryId(value: string): boolean {
  return ENTRY_ID_PATTERN.test(value);
}

export function parseAppRoute(input: string | URL): AppRoute {
  const url = input instanceof URL ? input : new URL(input, "https://app.journalm8.com/");
  const entryValues = url.searchParams.getAll("entry");
  const entryId = entryValues.length === 1 && isSafeEntryId(entryValues[0]) ? entryValues[0] : null;
  const report: ReportRoute = { type: "WEEKLY", window: null };

  if (entryId) return { view: "archive", entryId, themeId: null, report };
  if (entryValues.length) return { view: "home", entryId: null, themeId: null, report };

  const rawView = one(url.searchParams, "view");
  const view = rawView && APP_VIEWS.has(rawView as AppView) ? rawView as AppView : "home";

  if (view === "themes") {
    const rawTheme = one(url.searchParams, "theme");
    return {
      view,
      entryId: null,
      themeId: rawTheme && THEME_ID_PATTERN.test(rawTheme) ? rawTheme : null,
      report,
    };
  }

  if (view === "reports") {
    const period = one(url.searchParams, "period");
    const type: InsightsReportType = period === "monthly" ? "MONTHLY" : "WEEKLY";
    const rawWindow = period === "weekly" || period === "monthly"
      ? one(url.searchParams, "window")
      : null;
    return {
      view,
      entryId: null,
      themeId: null,
      report: {
        type,
        window: rawWindow && isReportWindow(type, rawWindow) ? rawWindow : null,
      },
    };
  }

  return { view, entryId: null, themeId: null, report };
}

export function serializeAppRoute(route: AppRoute, input: string | URL): string {
  const url = input instanceof URL ? new URL(input.toString()) : new URL(input, "https://app.journalm8.com/");
  url.search = "";
  url.hash = "";

  if (route.entryId && isSafeEntryId(route.entryId)) {
    url.searchParams.set("entry", route.entryId);
    return url.toString();
  }

  if (route.view !== "home") url.searchParams.set("view", route.view);
  if (route.view === "themes" && route.themeId && THEME_ID_PATTERN.test(route.themeId)) {
    url.searchParams.set("theme", route.themeId);
  }
  if (route.view === "reports") {
    url.searchParams.set("period", route.report.type === "MONTHLY" ? "monthly" : "weekly");
    if (route.report.window && isReportWindow(route.report.type, route.report.window)) {
      url.searchParams.set("window", route.report.window);
    }
  }
  return url.toString();
}

export function replaceAppRoute(route: AppRoute): void {
  window.history.replaceState({}, "", serializeAppRoute(route, window.location.href));
}

export function pushAppRoute(route: AppRoute): void {
  window.history.pushState({}, "", serializeAppRoute(route, window.location.href));
}
