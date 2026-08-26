import { useEffect, useRef, useState } from "react";
import {
  AlertCircle, ArrowLeft, ArrowRight, BarChart3, Brain, CalendarDays,
  CheckCircle2, CircleMinus, Flag, Lightbulb, Sparkles, Target,
  TrendingUp, Trophy,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { getMonthlyReport, getWeeklyReport } from "../../api/client";
import type { InsightsRankedItem } from "../../types/insights";
import type { InsightsReport, InsightsReportType } from "../../types/reports";
import type { ToastKind } from "../ui/ToastStack";
import "./ReportsPanel.css";

type ReportRoute = { type: InsightsReportType; window: string | null };

type Props = {
  reportType: InsightsReportType;
  reportWindow: string | null;
  onNavigate: (route: ReportRoute, mode?: "push" | "replace") => void;
  onNotify: (kind: ToastKind, title: string, message?: string) => void;
};

type HighlightDefinition = {
  key: keyof InsightsReport["highlights"];
  label: string;
  icon: LucideIcon;
};

const WEEKLY_HIGHLIGHTS: HighlightDefinition[] = [
  { key: "dominantMood", label: "Dominant mood", icon: Brain },
  { key: "dominantSentiment", label: "Dominant sentiment", icon: TrendingUp },
  { key: "topRecurringTheme", label: "Recurring theme", icon: Sparkles },
  { key: "notableProgress", label: "Notable progress", icon: Trophy },
];

const MONTHLY_HIGHLIGHTS: HighlightDefinition[] = [
  { key: "dominantSentiment", label: "Overall sentiment", icon: TrendingUp },
  { key: "topRecurringTheme", label: "Top recurring theme", icon: Sparkles },
  { key: "biggestChallenge", label: "Biggest challenge", icon: Target },
  { key: "notableProgress", label: "Notable progress", icon: Trophy },
];

function formatLabel(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDay(value: string) {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString([], {
    dateStyle: "medium", timeZone: "UTC",
  });
}

function formatTimestamp(value: string) {
  return new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function periodTitle(report: InsightsReport) {
  return `${formatDay(report.period.startDate)} – ${formatDay(report.period.endDate)}`;
}

function HighlightCard({ definition, item }: { definition: HighlightDefinition; item: InsightsRankedItem }) {
  const Icon = definition.icon;
  return <article className="phase2d-highlight">
    <span className="phase2d-icon"><Icon size={19} /></span>
    <div><small>{definition.label}</small><strong>{formatLabel(item.value)}</strong>
      <p>{item.count} {item.count === 1 ? "entry" : "entries"} · {item.sharePercent}% of analyzed entries</p></div>
  </article>;
}

function HighlightGrid({ report, definitions, title }: {
  report: InsightsReport;
  definitions: HighlightDefinition[];
  title: string;
}) {
  const returned = definitions.flatMap((definition) => {
    const item = report.highlights[definition.key];
    return item ? [{ definition, item }] : [];
  });
  if (!returned.length) return null;
  return <section className="phase2d-module" aria-labelledby="report-highlights-title">
    <header><h2 id="report-highlights-title">{title}</h2><p>Highlights returned by this report endpoint.</p></header>
    <div className="phase2d-highlight-grid">{returned.map(({ definition, item }) =>
      <HighlightCard key={definition.key} definition={definition} item={item} />)}</div>
  </section>;
}

function RankedCard({ title, description, items, icon: Icon }: {
  title: string;
  description: string;
  items: InsightsRankedItem[];
  icon: LucideIcon;
}) {
  if (!items.length) return null;
  return <section className="phase2d-ranked">
    <header><span className="phase2d-icon"><Icon size={18} /></span><div><h2>{title}</h2><p>{description}</p></div></header>
    <ol>{items.map((item, index) => <li key={item.value}>
      <span className="phase2d-rank">{index + 1}</span>
      <span className="phase2d-rank-copy"><strong>{formatLabel(item.value)}</strong>
        <progress max="100" value={item.sharePercent} aria-label={`${formatLabel(item.value)} share ${item.sharePercent}%`}>{item.sharePercent}%</progress></span>
      <small>{item.count} · {item.sharePercent}%</small>
    </li>)}</ol>
  </section>;
}

function StatusSummary({ report }: { report: InsightsReport }) {
  const details = report.status === "EMPTY"
    ? ["No analyzed entries", "Analyze entries from this period to produce report signals."]
    : report.status === "PARTIAL"
      ? ["Partial report coverage", "Some period entries are unanalyzed, so returned patterns may change."]
      : ["Report coverage complete", "All entries returned for this period are represented in the analysis."];
  const Icon = report.status === "EMPTY" ? CircleMinus : report.status === "PARTIAL" ? AlertCircle : CheckCircle2;
  return <section className={`phase2d-status ${report.status.toLowerCase()}`} aria-label="Report status">
    <Icon size={21} /><div><strong>{details[0]}</strong><p>{details[1]}</p></div>
  </section>;
}

function Metrics({ report }: { report: InsightsReport }) {
  return <dl className="phase2d-metrics" aria-label={`${report.reportType.toLowerCase()} report coverage metrics`}>
    <div><CalendarDays size={20} /><dt>Period entries</dt><dd>{report.coverage.totalEntries}</dd><small>Returned for this exact period</small></div>
    <div><BarChart3 size={20} /><dt>Analyzed entries</dt><dd>{report.coverage.analyzedEntries}</dd><small>Included in report signals</small></div>
    <div><CheckCircle2 size={20} /><dt>Analysis coverage</dt><dd>{report.coverage.analysisCompletionPercent}%</dd><small>{report.coverage.unanalyzedEntries} unanalyzed</small></div>
    <div><CalendarDays size={20} /><dt>Entry range</dt><dd className="compact">{report.coverage.firstEntryAt ? formatTimestamp(report.coverage.firstEntryAt) : "No dated entries"}</dd>
      <small>{report.coverage.latestEntryAt ? `Latest ${formatTimestamp(report.coverage.latestEntryAt)}` : "No entry timestamps returned"}</small></div>
  </dl>;
}

function Reflection({ prompt }: { prompt: string }) {
  if (!prompt.trim()) return null;
  return <section className="phase2d-reflection"><Lightbulb size={21} /><div><small>Suggested reflection</small><p>{prompt}</p></div></section>;
}

function CoverageRail({ report }: { report: InsightsReport }) {
  return <section className="phase2d-rail-card">
    <h2>Report summary</h2><p>Coverage from the selected report window.</p>
    <dl><div><dt>Period</dt><dd>{report.period.key}</dd></div><div><dt>Status</dt><dd>{formatLabel(report.status)}</dd></div>
      <div><dt>Entries</dt><dd>{report.coverage.totalEntries}</dd></div><div><dt>Analyzed</dt><dd>{report.coverage.analyzedEntries}</dd></div></dl>
  </section>;
}

function WeeklyDashboard({ report }: { report: InsightsReport }) {
  return <div className="phase2d-dashboard weekly">
    <Metrics report={report} />
    <div className="phase2d-report-layout">
      <div className="phase2d-primary-column">
        <HighlightGrid report={report} definitions={WEEKLY_HIGHLIGHTS} title="This week at a glance" />
        <div className="phase2d-ranked-grid">
          <RankedCard title="Top themes" description="Themes returned most often for this week." items={report.topThemes} icon={Sparkles} />
          <RankedCard title="Progress signals" description="Growth signals returned for this week." items={report.progressSignals} icon={Trophy} />
          <RankedCard title="Goals mentioned" description="Goals returned from analyzed weekly entries." items={report.goalsMentioned} icon={Flag} />
          <RankedCard title="Behavior patterns" description="Behavior patterns returned for this week." items={report.behaviorPatterns} icon={Brain} />
        </div>
      </div>
      <aside className="phase2d-summary-rail">
        <CoverageRail report={report} />
        {report.highlights.biggestChallenge && <section className="phase2d-rail-card accent"><h2>Biggest challenge</h2>
          <HighlightCard definition={{ key: "biggestChallenge", label: "Returned challenge", icon: Target }} item={report.highlights.biggestChallenge} /></section>}
        <RankedCard title="Top challenges" description="Challenges returned for this week." items={report.topChallenges} icon={Target} />
        <Reflection prompt={report.reflectionPrompt} />
      </aside>
    </div>
  </div>;
}

function MonthlyDashboard({ report }: { report: InsightsReport }) {
  return <div className="phase2d-dashboard monthly">
    <Metrics report={report} />
    <div className="phase2d-report-layout">
      <div className="phase2d-primary-column">
        <HighlightGrid report={report} definitions={MONTHLY_HIGHLIGHTS} title="Your month in review" />
        <div className="phase2d-ranked-grid">
          <RankedCard title="Recurring themes" description="Themes returned most often for this month." items={report.topThemes} icon={Sparkles} />
          <RankedCard title="Growth and progress" description="Progress signals returned for this month." items={report.progressSignals} icon={TrendingUp} />
          <RankedCard title="Challenges" description="Challenges returned from analyzed monthly entries." items={report.topChallenges} icon={Target} />
          <RankedCard title="Behavior patterns" description="Behavior patterns returned for this month." items={report.behaviorPatterns} icon={Brain} />
          <RankedCard title="Goals mentioned" description="Goals returned from analyzed monthly entries." items={report.goalsMentioned} icon={Flag} />
        </div>
      </div>
      <aside className="phase2d-summary-rail">
        <CoverageRail report={report} />
        {report.highlights.dominantMood && <section className="phase2d-rail-card accent"><h2>Dominant mood</h2>
          <HighlightCard definition={{ key: "dominantMood", label: "Returned mood", icon: Brain }} item={report.highlights.dominantMood} /></section>}
        {report.highlights.repeatedConcern && <section className="phase2d-rail-card accent"><h2>Repeated concern</h2>
          <HighlightCard definition={{ key: "repeatedConcern", label: "Returned concern", icon: AlertCircle }} item={report.highlights.repeatedConcern} /></section>}
        <Reflection prompt={report.reflectionPrompt} />
      </aside>
    </div>
  </div>;
}

export default function ReportsPanel({ reportType, reportWindow, onNavigate, onNotify }: Props) {
  const notifyRef = useRef(onNotify);
  const requestRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const [report, setReport] = useState<InsightsReport | null>(null);
  const [loadingKey, setLoadingKey] = useState("");
  const [errorState, setErrorState] = useState<{ key: string; message: string } | null>(null);
  const [retry, setRetry] = useState(0);
  const selectionKey = `${reportType}:${reportWindow ?? "current"}`;
  const visibleReport = report && report.reportType === reportType
    && (!reportWindow || report.period.key === reportWindow) ? report : null;

  useEffect(() => { notifyRef.current = onNotify; }, [onNotify]);
  useEffect(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const request = ++requestRef.current;
    setReport(null);
    setErrorState(null);
    setLoadingKey(selectionKey);
    const requestReport = reportType === "WEEKLY"
      ? getWeeklyReport(reportWindow ?? undefined, controller.signal)
      : getMonthlyReport(reportWindow ?? undefined, controller.signal);
    void requestReport.then((result) => {
      if (controller.signal.aborted || request !== requestRef.current) return;
      setReport(result.report);
    }).catch(() => {
      if (controller.signal.aborted || request !== requestRef.current) return;
      const message = "JM8 could not load the selected report. Try again.";
      setErrorState({ key: selectionKey, message });
      notifyRef.current("error", "Report unavailable", message);
    }).finally(() => {
      if (!controller.signal.aborted && request === requestRef.current) setLoadingKey("");
    });
    return () => controller.abort();
  }, [reportType, reportWindow, retry, selectionKey]);

  const isLoading = loadingKey === selectionKey || (!visibleReport && errorState?.key !== selectionKey);
  const errorMessage = errorState?.key === selectionKey ? errorState.message : "";
  const heading = reportType === "WEEKLY" ? "Weekly Report" : "Monthly Report";

  return <section className="phase2d-reports" aria-labelledby="phase2d-report-title">
    <header className="phase2d-header">
      <div><h1 id="phase2d-report-title">{heading}</h1>
        <p>{visibleReport ? periodTitle(visibleReport) : reportType === "WEEKLY" ? "Your selected week in review." : "Your selected month in review."}</p></div>
      <nav aria-label="Report type"><button type="button" className={reportType === "WEEKLY" ? "active" : ""}
        aria-current={reportType === "WEEKLY" ? "page" : undefined} onClick={() => onNavigate({ type: "WEEKLY", window: null })}>Weekly</button>
        <button type="button" className={reportType === "MONTHLY" ? "active" : ""}
          aria-current={reportType === "MONTHLY" ? "page" : undefined} onClick={() => onNavigate({ type: "MONTHLY", window: null })}>Monthly</button></nav>
    </header>

    {visibleReport && <nav className="phase2d-period-nav" aria-label={`${heading} period navigation`}>
      <button type="button" disabled={isLoading} onClick={() => onNavigate({ type: reportType, window: visibleReport.period.previousPeriod })}><ArrowLeft size={17} /> Previous</button>
      <div><strong>{periodTitle(visibleReport)}</strong><small>{visibleReport.period.key}{visibleReport.period.isCurrentPeriod ? " · Current period" : ""}</small></div>
      <span>{!visibleReport.period.isCurrentPeriod && <button type="button" disabled={isLoading} onClick={() => onNavigate({ type: reportType, window: null })}>Current</button>}
        <button type="button" disabled={isLoading || !visibleReport.period.nextPeriod} onClick={() => visibleReport.period.nextPeriod
          && onNavigate({ type: reportType, window: visibleReport.period.nextPeriod })}>Next <ArrowRight size={17} /></button></span>
    </nav>}

    {isLoading && <div className="phase2d-report-state" role="status"><BarChart3 size={28} /><h2>Loading {heading.toLowerCase()}</h2><p>JM8 is retrieving the exact selected report window.</p></div>}
    {!isLoading && errorMessage && <div className="phase2d-report-state error" role="alert"><AlertCircle size={28} /><h2>Report could not be loaded</h2><p>{errorMessage}</p>
      <button type="button" onClick={() => setRetry((value) => value + 1)}>Try again</button></div>}
    {!isLoading && visibleReport && <><StatusSummary report={visibleReport} />
      {visibleReport.reportType === "WEEKLY" ? <WeeklyDashboard report={visibleReport} /> : <MonthlyDashboard report={visibleReport} />}
      <footer className="phase2d-footer"><span>Reports API · version {visibleReport.reportVersion}</span>
        <span>Generated {formatTimestamp(visibleReport.generatedAt)}</span></footer></>}
  </section>;
}
