import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  CircleMinus,
  Flag,
  Lightbulb,
  RefreshCw,
  Sparkles,
  Target,
  TrendingUp,
  Trophy,
} from "lucide-react";
import type {
  LucideIcon,
} from "lucide-react";
import {
  getMonthlyReport,
  getWeeklyReport,
} from "../../api/client";
import type {
  InsightsRankedItem,
} from "../../types/insights";
import type {
  InsightsReport,
  InsightsReportType,
} from "../../types/reports";
import type {
  ToastKind,
} from "../ui/ToastStack";
import "./ReportsPanel.css";


type ReportsPanelProps = {
  onNotify: (
    kind: ToastKind,
    title: string,
    message?: string
  ) => void;
};

type LoadOptions = {
  keepContent?: boolean;
  notify?: boolean;
};

type ReportHighlightKey =
  keyof InsightsReport["highlights"];

type HighlightDefinition = {
  key: ReportHighlightKey;
  label: string;
  description: string;
  icon: LucideIcon;
  variant: string;
};

type RankedListProps = {
  title: string;
  description: string;
  items: InsightsRankedItem[];
  emptyMessage: string;
  icon: LucideIcon;
  variant: string;
};


const highlightDefinitions:
HighlightDefinition[] = [
  {
    key: "dominantMood",
    label: "Dominant mood",
    description:
      "The mood appearing most often.",
    icon: Activity,
    variant: "mood",
  },
  {
    key: "dominantSentiment",
    label: "Dominant sentiment",
    description:
      "The overall emotional direction.",
    icon: TrendingUp,
    variant: "sentiment",
  },
  {
    key: "topRecurringTheme",
    label: "Recurring theme",
    description:
      "The strongest repeated subject.",
    icon: Sparkles,
    variant: "theme",
  },
  {
    key: "biggestChallenge",
    label: "Biggest challenge",
    description:
      "The leading obstacle mentioned.",
    icon: Target,
    variant: "challenge",
  },
  {
    key: "notableProgress",
    label: "Notable progress",
    description:
      "The clearest growth signal.",
    icon: Trophy,
    variant: "progress",
  },
  {
    key: "repeatedConcern",
    label: "Repeated concern",
    description:
      "A challenge appearing repeatedly.",
    icon: AlertCircle,
    variant: "concern",
  },
];


function formatLabel(
  value: string | null
) {
  if (!value) {
    return "No signal yet";
  }

  return value
    .replaceAll("_", " ")
    .split(" ")
    .filter(Boolean)
    .map(
      (word) =>
        word.charAt(0).toUpperCase()
        + word.slice(1)
    )
    .join(" ");
}


function formatDate(
  value: string | null
) {
  if (!value) {
    return "Not available";
  }

  const parsed = new Date(
    `${value.slice(0, 10)}T00:00:00Z`
  );

  if (
    Number.isNaN(
      parsed.getTime()
    )
  ) {
    return "Not available";
  }

  return parsed.toLocaleDateString(
    [],
    {
      dateStyle: "medium",
      timeZone: "UTC",
    }
  );
}


function formatEntryDate(
  value: string | null
) {
  if (!value) {
    return "Not available";
  }

  const parsed = new Date(value);

  if (
    Number.isNaN(
      parsed.getTime()
    )
  ) {
    return "Not available";
  }

  return parsed.toLocaleDateString(
    [],
    {
      dateStyle: "medium",
    }
  );
}


function formatGeneratedAt(
  value: string
) {
  const parsed = new Date(value);

  if (
    Number.isNaN(
      parsed.getTime()
    )
  ) {
    return "Not available";
  }

  return parsed.toLocaleString(
    [],
    {
      dateStyle: "medium",
      timeStyle: "short",
    }
  );
}


function formatPeriodTitle(
  report: InsightsReport
) {
  if (
    report.reportType
    === "MONTHLY"
  ) {
    const parsed = new Date(
      `${report.period.startDate}T00:00:00Z`
    );

    if (
      !Number.isNaN(
        parsed.getTime()
      )
    ) {
      return parsed.toLocaleDateString(
        [],
        {
          month: "long",
          year: "numeric",
          timeZone: "UTC",
        }
      );
    }
  }

  return (
    `${formatDate(
      report.period.startDate
    )} – ${formatDate(
      report.period.endDate
    )}`
  );
}


function clampPercent(
  value: number
) {
  return Math.min(
    Math.max(
      Number(value || 0),
      0
    ),
    100
  );
}


function getErrorMessage(
  error: unknown
) {
  return error instanceof Error
    ? error.message
    : (
        "JM8 could not load "
        + "this report."
      );
}


function HighlightCard({
  definition,
  item,
}: {
  definition: HighlightDefinition;
  item: InsightsRankedItem | null;
}) {
  const Icon = definition.icon;

  return (
    <article
      className={
        "reports-highlight-card "
        + definition.variant
      }
    >
      <div className="reports-highlight-icon">
        <Icon size={20} />
      </div>

      <div>
        <span>
          {definition.label}
        </span>

        <strong>
          {formatLabel(
            item?.value || null
          )}
        </strong>

        <p>
          {item
            ? (
                `${item.count} ${
                  item.count === 1
                    ? "entry"
                    : "entries"
                } · ${
                  clampPercent(
                    item.sharePercent
                  )
                }%`
              )
            : definition.description}
        </p>
      </div>
    </article>
  );
}


function RankedList({
  title,
  description,
  items,
  emptyMessage,
  icon: Icon,
  variant,
}: RankedListProps) {
  return (
    <article
      className={
        "reports-ranked-card "
        + variant
      }
    >
      <header>
        <div className="reports-ranked-icon">
          <Icon size={20} />
        </div>

        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </header>

      {items.length === 0 ? (
        <div className="reports-list-empty">
          {emptyMessage}
        </div>
      ) : (
        <ol className="reports-ranked-list">
          {items.map(
            (
              item,
              index
            ) => {
              const percent =
                clampPercent(
                  item.sharePercent
                );

              return (
                <li key={item.value}>
                  <div className="reports-ranked-copy">
                    <span>
                      <strong>
                        {index + 1}
                      </strong>

                      {formatLabel(
                        item.value
                      )}
                    </span>

                    <small>
                      {item.count}{" "}
                      {item.count === 1
                        ? "entry"
                        : "entries"}
                      {" · "}
                      {percent}%
                    </small>
                  </div>

                  <div
                    className="reports-ranked-track"
                    aria-hidden="true"
                  >
                    <span
                      style={{
                        width: `${percent}%`,
                      }}
                    />
                  </div>
                </li>
              );
            }
          )}
        </ol>
      )}
    </article>
  );
}


export default function ReportsPanel({
  onNotify,
}: ReportsPanelProps) {
  const notifyRef = useRef(
    onNotify
  );

  const [
    reportType,
    setReportType,
  ] = useState<InsightsReportType>(
    "WEEKLY"
  );

  const [
    report,
    setReport,
  ] = useState<
    InsightsReport | null
  >(null);

  const [
    isLoading,
    setIsLoading,
  ] = useState(true);

  const [
    errorMessage,
    setErrorMessage,
  ] = useState("");

  useEffect(() => {
    notifyRef.current =
      onNotify;
  }, [onNotify]);

  const loadReport = useCallback(
    async (
      type: InsightsReportType,
      period?: string,
      options: LoadOptions = {}
    ) => {
      setIsLoading(true);

      if (!options.keepContent) {
        setReport(null);
      }

      try {
        const result =
          type === "WEEKLY"
            ? await getWeeklyReport(
                period
              )
            : await getMonthlyReport(
                period
              );

        setReport(result.report);
        setErrorMessage("");

        if (options.notify) {
          notifyRef.current(
            "success",
            "Report refreshed",
            (
              "Your journal report "
              + "is up to date."
            )
          );
        }
      } catch (error) {
        const message =
          getErrorMessage(error);

        setErrorMessage(
          message
        );

        notifyRef.current(
          "error",
          "Report unavailable",
          message
        );
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    void loadReport(
      reportType
    );
  }, [
    loadReport,
    reportType,
  ]);

  function selectReportType(
    nextType: InsightsReportType
  ) {
    if (
      nextType === reportType
    ) {
      return;
    }

    setErrorMessage("");
    setReportType(nextType);
  }

  function loadPeriod(
    period: string | null
  ) {
    if (!period) {
      return;
    }

    void loadReport(
      reportType,
      period,
      {
        keepContent: true,
      }
    );
  }

  function loadCurrentPeriod() {
    void loadReport(
      reportType,
      undefined,
      {
        keepContent: true,
      }
    );
  }

  function refreshReport() {
    void loadReport(
      reportType,
      report?.period.key,
      {
        keepContent: true,
        notify: true,
      }
    );
  }

  if (
    isLoading
    && !report
  ) {
    return (
      <section
        className={
          "reports-panel "
          + "reports-state"
        }
      >
        <RefreshCw
          className="reports-spin"
          size={36}
        />

        <h1>
          Building your report
        </h1>

        <p>
          JM8 is organizing your
          journal patterns for the
          selected period.
        </p>
      </section>
    );
  }

  if (
    errorMessage
    && !report
  ) {
    return (
      <section
        className={
          "reports-panel "
          + "reports-state "
          + "error"
        }
      >
        <AlertCircle size={40} />

        <h1>
          Report could not be loaded
        </h1>

        <p>{errorMessage}</p>

        <button
          onClick={() => {
            void loadReport(
              reportType,
              undefined,
              {
                notify: true,
              }
            );
          }}
        >
          <RefreshCw size={17} />
          Try again
        </button>
      </section>
    );
  }

  if (!report) {
    return null;
  }

  const isEmpty =
    report.status === "EMPTY";

  const isPartial =
    report.status === "PARTIAL";

  return (
    <section className="reports-panel">
      <header className="reports-header">
        <div>
          <p className="reports-kicker">
            <BarChart3 size={17} />
            Periodic intelligence
          </p>

          <h1>
            Journal Reports
          </h1>

          <p>
            Review the emotional,
            behavioral, and strategic
            patterns that shaped a week
            or month of your journal.
          </p>
        </div>

        <button
          className="reports-refresh"
          disabled={isLoading}
          onClick={refreshReport}
        >
          <RefreshCw
            className={
              isLoading
                ? "reports-spin"
                : undefined
            }
            size={18}
          />

          {isLoading
            ? "Refreshing"
            : "Refresh report"}
        </button>
      </header>

      {errorMessage && (
        <div className="reports-alert">
          <AlertCircle size={18} />
          {errorMessage}
        </div>
      )}

      <nav
        className="reports-type-tabs"
        aria-label="Report type"
      >
        <button
          className={
            reportType === "WEEKLY"
              ? "active"
              : ""
          }
          aria-pressed={
            reportType === "WEEKLY"
          }
          onClick={() => {
            selectReportType(
              "WEEKLY"
            );
          }}
        >
          <CalendarDays size={18} />
          Weekly
        </button>

        <button
          className={
            reportType === "MONTHLY"
              ? "active"
              : ""
          }
          aria-pressed={
            reportType === "MONTHLY"
          }
          onClick={() => {
            selectReportType(
              "MONTHLY"
            );
          }}
        >
          <BarChart3 size={18} />
          Monthly
        </button>
      </nav>

      <section className="reports-period-nav">
        <button
          onClick={() => {
            loadPeriod(
              report.period
                .previousPeriod
            );
          }}
          aria-label="Previous report period"
        >
          <ArrowLeft size={18} />
          Previous
        </button>

        <div>
          <span>
            {report.reportType ===
            "WEEKLY"
              ? "Weekly report"
              : "Monthly report"}
          </span>

          <h2>
            {formatPeriodTitle(
              report
            )}
          </h2>

          <small>
            {report.period.key}
            {report.period
              .isCurrentPeriod
              ? " · Current period"
              : ""}
          </small>
        </div>

        <div className="reports-period-actions">
          {!report.period
            .isCurrentPeriod && (
            <button
              className="current"
              onClick={
                loadCurrentPeriod
              }
            >
              Current
            </button>
          )}

          <button
            disabled={
              !report.period
                .nextPeriod
            }
            onClick={() => {
              loadPeriod(
                report.period
                  .nextPeriod
              );
            }}
            aria-label="Next report period"
          >
            Next
            <ArrowRight size={18} />
          </button>
        </div>
      </section>

      <section
        className={
          "reports-status-banner "
          + report.status
            .toLowerCase()
        }
      >
        <div className="reports-status-icon">
          {isEmpty ? (
            <CircleMinus size={22} />
          ) : isPartial ? (
            <AlertCircle size={22} />
          ) : (
            <CheckCircle2 size={22} />
          )}
        </div>

        <div>
          <strong>
            {isEmpty
              ? "No analyzed entries in this period"
              : isPartial
                ? "This report has partial coverage"
                : "This report is ready"}
          </strong>

          <p>
            {isEmpty
              ? (
                  "Add or analyze journal "
                  + "entries from this period "
                  + "to generate insights."
                )
              : isPartial
                ? (
                    "Some entries are not "
                    + "analyzed yet, so these "
                    + "patterns may change."
                  )
                : (
                    "Every journal entry in "
                    + "this period is represented "
                    + "in the report."
                  )}
          </p>
        </div>
      </section>

      <section
        className="reports-coverage-grid"
        aria-label="Report coverage"
      >
        <article>
          <span>
            <CalendarDays size={18} />
            Entries
          </span>

          <strong>
            {report.coverage
              .totalEntries}
          </strong>

          <small>
            Written in this period
          </small>
        </article>

        <article>
          <span>
            <CheckCircle2 size={18} />
            Analyzed
          </span>

          <strong>
            {report.coverage
              .analyzedEntries}
          </strong>

          <small>
            Included in intelligence
          </small>
        </article>

        <article>
          <span>
            <TrendingUp size={18} />
            Coverage
          </span>

          <strong>
            {report.coverage
              .analysisCompletionPercent}%
          </strong>

          <small>
            Period analysis completed
          </small>
        </article>

        <article>
          <span>
            <Activity size={18} />
            Entry range
          </span>

          <strong className="compact">
            {formatEntryDate(
              report.coverage
                .firstEntryAt
            )}
          </strong>

          <small>
            Latest:{" "}
            {formatEntryDate(
              report.coverage
                .latestEntryAt
            )}
          </small>
        </article>
      </section>

      <section className="reports-highlights-section">
        <header>
          <div>
            <p>
              Executive summary
            </p>

            <h2>
              Period highlights
            </h2>
          </div>

          <Sparkles size={22} />
        </header>

        <div className="reports-highlights-grid">
          {highlightDefinitions.map(
            (definition) => (
              <HighlightCard
                key={definition.key}
                definition={
                  definition
                }
                item={
                  report.highlights[
                    definition.key
                  ]
                }
              />
            )
          )}
        </div>
      </section>

      <section className="reports-reflection-card">
        <div className="reports-reflection-icon">
          <Lightbulb size={23} />
        </div>

        <div>
          <p>
            Suggested reflection
          </p>

          <h2>
            {report.reflectionPrompt}
          </h2>

          <span>
            Use this prompt for your
            next journal entry.
          </span>
        </div>
      </section>

      <section className="reports-details-section">
        <header>
          <div>
            <p>
              Detailed review
            </p>

            <h2>
              What shaped this period
            </h2>
          </div>

          <Activity size={22} />
        </header>

        <div className="reports-details-grid">
          <RankedList
            title="Top themes"
            description={
              "Subjects that appeared "
              + "most often."
            }
            items={
              report.topThemes
            }
            emptyMessage={
              "No themes were identified "
              + "for this period."
            }
            icon={Sparkles}
            variant="themes"
          />

          <RankedList
            title="Top challenges"
            description={
              "Obstacles and concerns "
              + "mentioned in your entries."
            }
            items={
              report.topChallenges
            }
            emptyMessage={
              "No challenges were identified "
              + "for this period."
            }
            icon={Target}
            variant="challenges"
          />

          <RankedList
            title="Progress signals"
            description={
              "Evidence of movement, growth, "
              + "or improved behavior."
            }
            items={
              report.progressSignals
            }
            emptyMessage={
              "No progress signals were "
              + "identified yet."
            }
            icon={Trophy}
            variant="progress"
          />

          <RankedList
            title="Goals mentioned"
            description={
              "Goals and desired outcomes "
              + "recorded in your journal."
            }
            items={
              report.goalsMentioned
            }
            emptyMessage={
              "No goals were identified "
              + "for this period."
            }
            icon={Flag}
            variant="goals"
          />

          <RankedList
            title="Behavior patterns"
            description={
              "Repeated actions, routines, "
              + "and decision patterns."
            }
            items={
              report.behaviorPatterns
            }
            emptyMessage={
              "No behavior patterns were "
              + "identified yet."
            }
            icon={Activity}
            variant="behaviors"
          />
        </div>
      </section>

      <footer className="reports-footer">
        <span>
          Report version{" "}
          {report.reportVersion}
        </span>

        <span>
          Status{" "}
          {formatLabel(
            report.status
          )}
        </span>

        <span>
          Generated{" "}
          {formatGeneratedAt(
            report.generatedAt
          )}
        </span>
      </footer>
    </section>
  );
}
