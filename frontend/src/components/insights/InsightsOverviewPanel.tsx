import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, BarChart3, BookOpen, Brain, CalendarDays, Sparkles, Target, TrendingUp } from "lucide-react";
import { getInsightsMoods, getInsightsOverview } from "../../api/client";
import type { InsightsMoods, InsightsOverview, InsightsRankedItem } from "../../types/insights";
import type { ToastKind } from "../ui/ToastStack";
import InsightsLineChart from "./InsightsLineChart";

type Props = { onNotify: (kind: ToastKind, title: string, message?: string) => void };

function label(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function date(value: string | null) {
  return value ? new Date(value).toLocaleDateString([], { dateStyle: "medium" }) : "Unavailable";
}

function RankedList({ title, subtitle, items }: { title: string; subtitle: string; items: InsightsRankedItem[] }) {
  if (!items.length) return null;
  return <section className="phase2c-ranking-card">
    <header><h2>{title}</h2><p>{subtitle}</p></header>
    <ol>{items.map((item, index) => <li key={item.value}>
      <span className="phase2c-rank">{index + 1}</span>
      <span className="phase2c-ranking-name"><strong>{label(item.value)}</strong><progress max="100" value={item.sharePercent}>{item.sharePercent}%</progress></span>
      <small>{item.count} {item.count === 1 ? "entry" : "entries"} · {item.sharePercent}%</small>
    </li>)}</ol>
  </section>;
}

export default function InsightsOverviewPanel({ onNotify }: Props) {
  const notifyRef = useRef(onNotify);
  const controllerRef = useRef<AbortController | null>(null);
  const requestRef = useRef(0);
  const [overview, setOverview] = useState<InsightsOverview | null>(null);
  const [moods, setMoods] = useState<InsightsMoods | null>(null);
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
    const results = await Promise.allSettled([
      getInsightsOverview(controller.signal),
      getInsightsMoods(controller.signal),
    ]);
    if (controller.signal.aborted || request !== requestRef.current) return;
    let failed = 0;
    if (results[0].status === "fulfilled") setOverview(results[0].value.overview); else { setOverview(null); failed += 1; }
    if (results[1].status === "fulfilled") setMoods(results[1].value.moods); else { setMoods(null); failed += 1; }
    if (failed) {
      const message = failed === 2 ? "JM8 could not load your insights. Try again." : "Some insight data is temporarily unavailable. Available API results are shown.";
      setErrorMessage(message);
      notifyRef.current("error", failed === 2 ? "Insights unavailable" : "Insights partially loaded", message);
    }
    setIsLoading(false);
  }, []);

  useEffect(() => { void load(); return () => controllerRef.current?.abort(); }, [load]);

  const coverage = overview?.coverage ?? moods?.coverage;
  const dominantMood = overview?.dominantMood ?? moods?.dominantMood;
  const dominantSentiment = overview?.dominantSentiment ?? moods?.dominantSentiment;
  const moodSource = overview?.dominantMood ? "overview" : "moods";
  const sentimentSource = overview?.dominantSentiment ? "overview" : "moods";
  const coverageSource = overview ? "overview" : "moods";
  const chartPoints = moods?.monthlyBreakdown.map((month) => ({ label: month.period, value: month.analyzedEntries })) ?? [];
  const hasData = Boolean(overview || moods);

  return <section className="phase2c-view phase2c-insights" aria-labelledby="phase2c-insights-title">
    <header className="phase2c-page-header">
      <div><h1 id="phase2c-insights-title">Insights</h1><p>Patterns and signals returned from your analyzed journal history.</p></div>
      {(overview || moods) && <div className="phase2c-provenance">
        {overview && <small>Overview API · {date(overview.generatedAt)}</small>}
        {moods && <small>Moods API · {date(moods.generatedAt)}</small>}
      </div>}
    </header>
    {errorMessage && <div className="phase2c-alert" role="alert"><AlertCircle size={18} /><span>{errorMessage}</span><button type="button" onClick={() => void load()}>Retry</button></div>}
    {isLoading && !hasData && <div className="phase2c-state" role="status">Loading your private insights…</div>}
    {!isLoading && !hasData && !errorMessage && <div className="phase2c-state">No insight data was returned.</div>}

    {coverage && <>
      <dl className="phase2c-metrics" aria-label={`Insights coverage from the ${coverageSource} API`}>
        <div><BookOpen size={21} /><dt>Total entries</dt><dd>{coverage.totalEntries}</dd><small>In this API aggregate</small></div>
        <div><BarChart3 size={21} /><dt>Analyzed entries</dt><dd>{coverage.analyzedEntries}</dd><small>{coverage.analysisCompletionPercent}% coverage</small></div>
        <div><Sparkles size={21} /><dt>Unanalyzed entries</dt><dd>{coverage.unanalyzedEntries}</dd><small>Not included in analysis</small></div>
        <div><CalendarDays size={21} /><dt>Date range</dt><dd>{coverage.firstEntryAt ? date(coverage.firstEntryAt) : "Unavailable"}</dd><small>{coverage.latestEntryAt ? `through ${date(coverage.latestEntryAt)}` : "No dated entries"}</small></div>
      </dl>
      {(dominantMood || dominantSentiment) && <section className="phase2c-signal-grid" aria-label="Dominant journal signals">
        {dominantMood && <article className="phase2c-signal mood"><Brain size={23} /><span><small>Dominant mood</small><strong>{label(dominantMood.value)}</strong><em>{dominantMood.sharePercent}% of analyzed entries · {moodSource} API</em></span></article>}
        {dominantSentiment && <article className="phase2c-signal sentiment"><TrendingUp size={23} /><span><small>Overall sentiment</small><strong>{label(dominantSentiment.value)}</strong><em>{dominantSentiment.sharePercent}% of analyzed entries · {sentimentSource} API</em></span></article>}
      </section>}
    </>}

    {(overview || moods) && <div className={`phase2c-insights-columns${overview && moods ? "" : " single"}`}>
      {overview && <div className="phase2c-ranking-grid">
        <RankedList title="Top themes" subtitle="Most frequent themes returned by the overview API." items={overview.topThemes} />
        <RankedList title="Recurring challenges" subtitle="Challenges identified in analyzed entries." items={overview.topChallenges} />
        <RankedList title="Goals" subtitle="Goals identified in analyzed entries." items={overview.topGoals} />
        <RankedList title="Notable progress" subtitle="Progress signals identified in analyzed entries." items={overview.notableProgress} />
      </div>}
      {moods && <aside className="phase2c-insights-aside">
        <InsightsLineChart title="Analyzed entries by month" description="Ordered monthly counts returned by the moods endpoint." points={chartPoints} />
        {moods?.moods.length ? <RankedList title="Mood distribution" subtitle="Across analyzed entries in this API snapshot." items={moods.moods} /> : null}
        {moods?.sentiments.length ? <RankedList title="Sentiment distribution" subtitle="Across analyzed entries in this API snapshot." items={moods.sentiments} /> : null}
      </aside>}
    </div>}
    {overview?.reflectionPrompt.trim() && <section className="phase2c-reflection"><Target size={22} /><div><small>Suggested reflection</small><p>{overview.reflectionPrompt}</p></div></section>}
  </section>;
}
