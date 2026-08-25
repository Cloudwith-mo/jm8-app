import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowDownRight, ArrowUpRight, CircleMinus, Sparkles, Tag } from "lucide-react";
import { getInsightsThemes } from "../../api/client";
import type { JournalEntry } from "../../types/journal";
import type { InsightsThemeItem, InsightsThemes, InsightsThemeTrend } from "../../types/insights";
import EntryCard from "../archive/EntryCard";
import type { ToastKind } from "../ui/ToastStack";
import InsightsLineChart from "./InsightsLineChart";

type Props = {
  entries: JournalEntry[];
  selectedThemeId: string | null;
  onSelectTheme: (themeId: string, mode?: "push" | "replace") => void;
  onOpenEntry: (entryId: string) => void;
  onNotify: (kind: ToastKind, title: string, message?: string) => void;
};

function getThemeRouteId(value: string) {
  let hash = 0x811c9dc5;
  for (const character of value.trim().toLowerCase()) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 0x01000193);
  }
  return `theme-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function label(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function date(value: string | null) {
  return value ? new Date(value).toLocaleDateString([], { dateStyle: "medium" }) : null;
}

function Trend({ value }: { value: InsightsThemeTrend }) {
  const Icon = value === "RISING" ? ArrowUpRight : value === "COOLING" ? ArrowDownRight : CircleMinus;
  return <span className={`phase2c-trend ${value.toLowerCase()}`}><Icon size={15} />{label(value)}</span>;
}

export default function InsightsTrendsPanel({ entries, selectedThemeId, onSelectTheme, onOpenEntry, onNotify }: Props) {
  const notifyRef = useRef(onNotify);
  const controllerRef = useRef<AbortController | null>(null);
  const requestRef = useRef(0);
  const [data, setData] = useState<InsightsThemes | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => { notifyRef.current = onNotify; }, [onNotify]);

  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const request = ++requestRef.current;
    setIsLoading(true);
    setErrorMessage("");
    try {
      const result = await getInsightsThemes(controller.signal);
      if (controller.signal.aborted || request !== requestRef.current) return;
      setData(result.themes);
    } catch {
      if (controller.signal.aborted || request !== requestRef.current) return;
      const message = "JM8 could not load your themes. Try again.";
      setErrorMessage(message);
      notifyRef.current("error", "Themes unavailable", message);
    } finally {
      if (!controller.signal.aborted && request === requestRef.current) setIsLoading(false);
    }
  }, []);

  useEffect(() => { void load(); return () => controllerRef.current?.abort(); }, [load]);
  useEffect(() => {
    if (data?.themes.length && !selectedThemeId) {
      const routeIds = data.themes.map((theme) => getThemeRouteId(theme.value));
      if (new Set(routeIds).size === routeIds.length) onSelectTheme(routeIds[0], "replace");
    }
  }, [data, onSelectTheme, selectedThemeId]);

  const selected = data?.themes.find((theme) => getThemeRouteId(theme.value) === selectedThemeId) ?? null;
  const hasUniqueRouteIds = !data || new Set(data.themes.map((theme) => getThemeRouteId(theme.value))).size === data.themes.length;
  const supportingEntries = useMemo(() => {
    if (!selected) return [];
    const identity = selected.value.trim().toLowerCase();
    return entries.filter((entry) => Array.isArray(entry.analysis?.themes)
      && entry.analysis.themes.some((theme) => typeof theme === "string" && theme.trim().toLowerCase() === identity));
  }, [entries, selected]);
  const trendPoints = useMemo(() => {
    if (!data || !selected) return [];
    const identity = selected.value.trim().toLowerCase();
    return data.monthlyBreakdown.flatMap((month) => {
      const match = month.topThemes.find((theme) => theme.value.trim().toLowerCase() === identity);
      return match ? [{ label: month.period, value: match.count }] : [];
    });
  }, [data, selected]);

  return <section className="phase2c-view phase2c-themes" aria-labelledby="phase2c-themes-title">
    <header className="phase2c-page-header">
      <div><h1 id="phase2c-themes-title">Themes</h1><p>The topics returned most often across your analyzed journal entries.</p></div>
      {data && <small>API snapshot · {date(data.generatedAt)}</small>}
    </header>
    {errorMessage && <div className="phase2c-alert" role="alert"><AlertCircle size={18} /><span>{errorMessage}</span><button type="button" onClick={() => void load()}>Retry</button></div>}
    {isLoading && !data && <div className="phase2c-state" role="status">Loading your private themes…</div>}
    {!isLoading && data && data.themes.length === 0 && <div className="phase2c-state"><Sparkles size={25} />No themes have been returned for analyzed entries yet.</div>}

    {data?.themes.length && !hasUniqueRouteIds && <div className="phase2c-alert" role="alert"><AlertCircle size={18} /><span>JM8 could not safely distinguish the returned themes.</span></div>}
    {data?.themes.length && hasUniqueRouteIds ? <>
      <dl className="phase2c-theme-summary" aria-label="Theme API coverage">
        <div><dt>Ranked themes</dt><dd>{data.themes.length}</dd></div>
        <div><dt>Analyzed entries</dt><dd>{data.coverage.analyzedEntries}</dd></div>
        <div><dt>Analysis coverage</dt><dd>{data.coverage.analysisCompletionPercent}%</dd></div>
        <div><dt>Comparison window</dt><dd>{data.windowSize} entries</dd></div>
      </dl>
      <div className="phase2c-theme-layout">
        <section className="phase2c-theme-master" aria-labelledby="theme-ranking-title">
          <header><span id="theme-ranking-title">Theme</span><span>Entries</span><span>% of analyzed</span></header>
          <ol>{data.themes.map((theme, index) => {
            const id = getThemeRouteId(theme.value);
            return <li key={theme.value}><button type="button" className={id === selectedThemeId ? "selected" : ""} aria-current={id === selectedThemeId ? "true" : undefined} onClick={() => onSelectTheme(id)}>
              <span className="phase2c-theme-rank"><Tag size={17} />{index + 1}</span>
              <span className="phase2c-theme-name"><strong>{label(theme.value)}</strong><progress max="100" value={theme.sharePercent}>{theme.sharePercent}%</progress></span>
              <span>{theme.count}</span><span>{theme.sharePercent}%</span>
            </button></li>;
          })}</ol>
        </section>
        <aside className="phase2c-theme-detail" aria-live="polite">
          {selected ? <ThemeDetail theme={selected} supportingEntries={supportingEntries} trendPoints={trendPoints} onOpenEntry={onOpenEntry} />
            : <div className="phase2c-state">The theme in this URL is not available in the current API response. Select a returned theme.</div>}
        </aside>
      </div>
    </> : null}
  </section>;
}

function ThemeDetail({ theme, supportingEntries, trendPoints, onOpenEntry }: {
  theme: InsightsThemeItem;
  supportingEntries: JournalEntry[];
  trendPoints: { label: string; value: number }[];
  onOpenEntry: (entryId: string) => void;
}) {
  return <>
    <section className="phase2c-theme-profile">
      <header><span className="phase2c-theme-icon"><Tag size={22} /></span><div><h2>{label(theme.value)}</h2><p>{theme.count} {theme.count === 1 ? "entry" : "entries"} · {theme.sharePercent}% of analyzed entries</p></div></header>
      <Trend value={theme.trend} />
      <dl>
        <div><dt>Recent window</dt><dd>{theme.recentCount}</dd></div><div><dt>Previous window</dt><dd>{theme.previousCount}</dd></div>
        {theme.firstSeenAt && <div><dt>First returned</dt><dd>{date(theme.firstSeenAt)}</dd></div>}
        {theme.lastSeenAt && <div><dt>Latest returned</dt><dd>{date(theme.lastSeenAt)}</dd></div>}
      </dl>
    </section>
    <InsightsLineChart title="Theme over time" description="Counts only for months where this theme appears in the API's returned top-theme data." points={trendPoints} />
    <section className="phase2c-supporting" aria-labelledby="supporting-entries-title">
      <header><h2 id="supporting-entries-title">Supporting loaded entries</h2><p>Entries currently loaded in this authenticated archive with this exact analyzed theme.</p></header>
      {supportingEntries.length ? <div>{supportingEntries.slice(0, 4).map((entry) => <EntryCard key={entry.entryId} entry={entry} isSelected={false} onClick={() => onOpenEntry(entry.entryId)} />)}</div>
        : <p className="phase2c-supporting-empty">No matching entry is present in the currently loaded archive page.</p>}
    </section>
  </>;
}
