import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Activity,
  AlertCircle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Brain,
  CalendarDays,
  CheckCircle2,
  CircleMinus,
  RefreshCw,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import {
  getInsightsMoods,
  getInsightsThemes,
} from "../../api/client";
import type {
  InsightsMoods,
  InsightsRankedItem,
  InsightsThemeItem,
  InsightsThemes,
  InsightsThemeTrend,
} from "../../types/insights";
import type {
  ToastKind,
} from "../ui/ToastStack";
import "./InsightsTrendsPanel.css";


type InsightsTrendsPanelProps = {
  onNotify: (
    kind: ToastKind,
    title: string,
    message?: string
  ) => void;
};

type InsightsTab =
  | "themes"
  | "moods";

type LoadOptions = {
  notify?: boolean;
};

type DistributionListProps = {
  title: string;
  description: string;
  items: InsightsRankedItem[];
  emptyMessage: string;
  variant:
    | "mood"
    | "sentiment";
};


function formatLabel(
  value: string | null
) {
  if (!value) {
    return "Not enough data";
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


function formatMonth(
  period: string
) {
  const parsed = new Date(
    `${period}-01T00:00:00Z`
  );

  if (
    Number.isNaN(
      parsed.getTime()
    )
  ) {
    return period;
  }

  return parsed.toLocaleDateString(
    [],
    {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    }
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
        "JM8 could not load detailed "
        + "themes and moods."
      );
}


function TrendIcon({
  trend,
}: {
  trend: InsightsThemeTrend;
}) {
  if (trend === "RISING") {
    return (
      <ArrowUpRight size={16} />
    );
  }

  if (trend === "COOLING") {
    return (
      <ArrowDownRight size={16} />
    );
  }

  return (
    <CircleMinus size={16} />
  );
}


function ThemeTrendBadge({
  trend,
}: {
  trend: InsightsThemeTrend;
}) {
  return (
    <span
      className={
        "insights-theme-trend "
        + `trend-${trend.toLowerCase()}`
      }
    >
      <TrendIcon trend={trend} />
      {formatLabel(trend)}
    </span>
  );
}


function DistributionList({
  title,
  description,
  items,
  emptyMessage,
  variant,
}: DistributionListProps) {
  return (
    <article className="insights-distribution-card">
      <header>
        <div
          className={
            "insights-distribution-icon "
            + variant
          }
        >
          {variant === "mood" ? (
            <Brain size={21} />
          ) : (
            <TrendingUp size={21} />
          )}
        </div>

        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </header>

      {items.length === 0 ? (
        <div className="insights-trends-empty">
          {emptyMessage}
        </div>
      ) : (
        <ol className="insights-distribution-list">
          {items.map(
            (
              item,
              index
            ) => {
              const share =
                clampPercent(
                  item.sharePercent
                );

              return (
                <li key={item.value}>
                  <div className="insights-distribution-copy">
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
                      {share}%
                    </small>
                  </div>

                  <div
                    className={
                      "insights-distribution-track "
                      + variant
                    }
                    aria-hidden="true"
                  >
                    <span
                      style={{
                        width: `${share}%`,
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


function ThemeRanking({
  theme,
  rank,
}: {
  theme: InsightsThemeItem;
  rank: number;
}) {
  const share = clampPercent(
    theme.sharePercent
  );

  return (
    <article className="insights-theme-row">
      <div className="insights-theme-rank">
        {rank}
      </div>

      <div className="insights-theme-main">
        <div className="insights-theme-heading">
          <div>
            <h3>
              {formatLabel(
                theme.value
              )}
            </h3>

            <p>
              {theme.count}{" "}
              {theme.count === 1
                ? "analyzed entry"
                : "analyzed entries"}
              {" · "}
              {share}% of coverage
            </p>
          </div>

          <ThemeTrendBadge
            trend={theme.trend}
          />
        </div>

        <div
          className="insights-theme-share-track"
          aria-hidden="true"
        >
          <span
            style={{
              width: `${share}%`,
            }}
          />
        </div>

        <dl className="insights-theme-metadata">
          <div>
            <dt>Recent window</dt>
            <dd>
              {theme.recentCount}
            </dd>
          </div>

          <div>
            <dt>Previous window</dt>
            <dd>
              {theme.previousCount}
            </dd>
          </div>

          <div>
            <dt>First seen</dt>
            <dd>
              {formatDate(
                theme.firstSeenAt
              )}
            </dd>
          </div>

          <div>
            <dt>Latest seen</dt>
            <dd>
              {formatDate(
                theme.lastSeenAt
              )}
            </dd>
          </div>
        </dl>
      </div>
    </article>
  );
}


export default function InsightsTrendsPanel({
  onNotify,
}: InsightsTrendsPanelProps) {
  const notifyRef = useRef(
    onNotify
  );

  const [
    activeTab,
    setActiveTab,
  ] = useState<InsightsTab>(
    "themes"
  );

  const [
    themes,
    setThemes,
  ] = useState<
    InsightsThemes | null
  >(null);

  const [
    moods,
    setMoods,
  ] = useState<
    InsightsMoods | null
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

  const loadTrends = useCallback(
    async (
      options: LoadOptions = {}
    ) => {
      setIsLoading(true);

      try {
        const [
          themesResult,
          moodsResult,
        ] = await Promise.all([
          getInsightsThemes(),
          getInsightsMoods(),
        ]);

        setThemes(
          themesResult.themes
        );

        setMoods(
          moodsResult.moods
        );

        setErrorMessage("");

        if (options.notify) {
          notifyRef.current(
            "success",
            "Intelligence refreshed",
            (
              "Your themes and moods "
              + "are up to date."
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
          "Intelligence unavailable",
          message
        );
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    void loadTrends();
  }, [loadTrends]);

  if (
    isLoading
    && !themes
    && !moods
  ) {
    return (
      <section
        className={
          "insights-trends-page "
          + "insights-trends-state"
        }
      >
        <RefreshCw
          className="insights-trends-spin"
          size={36}
        />

        <h1>
          Reading your patterns
        </h1>

        <p>
          JM8 is organizing your
          themes, moods, sentiments,
          and changes over time.
        </p>
      </section>
    );
  }

  if (
    errorMessage
    && !themes
    && !moods
  ) {
    return (
      <section
        className={
          "insights-trends-page "
          + "insights-trends-state "
          + "error"
        }
      >
        <AlertCircle size={40} />

        <h1>
          Detailed insights could not
          be loaded
        </h1>

        <p>
          {errorMessage}
        </p>

        <button
          onClick={() => {
            void loadTrends({
              notify: true,
            });
          }}
        >
          <RefreshCw size={17} />
          Try again
        </button>
      </section>
    );
  }

  if (
    !themes
    || !moods
  ) {
    return null;
  }

  const coverage =
    themes.coverage;

  const previousMoodValue =
    moods.previousDominantMood
      ?.value
    || null;

  const recentMoodValue =
    moods.recentDominantMood
      ?.value
    || null;

  let moodShiftText =
    "Not enough data";

  let moodShiftState =
    "new";

  let moodShiftHeading =
    "More journal history is needed";

  if (
    recentMoodValue
    && !previousMoodValue
  ) {
    moodShiftText =
      `New signal: ${formatLabel(
        recentMoodValue
      )}`;

    moodShiftHeading =
      "A recent mood signal is emerging";
  } else if (
    recentMoodValue
    && previousMoodValue
    && moods.moodShift.changed
  ) {
    moodShiftText =
      `${formatLabel(
        previousMoodValue
      )} → ${formatLabel(
        recentMoodValue
      )}`;

    moodShiftState =
      "changed";

    moodShiftHeading =
      "Your recent dominant mood has changed";
  } else if (
    recentMoodValue
    && previousMoodValue
  ) {
    moodShiftText =
      `Stable: ${formatLabel(
        recentMoodValue
      )}`;

    moodShiftState =
      "steady";

    moodShiftHeading =
      "Your dominant mood is holding steady";
  }

  return (
    <section className="insights-trends-page">
      <header className="insights-trends-header">
        <div>
          <p className="insights-trends-kicker">
            <Activity size={17} />
            Cross-entry intelligence
          </p>

          <h1>
            Themes &amp; Moods
          </h1>

          <p>
            See what keeps returning,
            what is gaining momentum,
            and how your emotional
            patterns are changing.
          </p>
        </div>

        <button
          className="insights-trends-refresh"
          disabled={isLoading}
          onClick={() => {
            void loadTrends({
              notify: true,
            });
          }}
        >
          <RefreshCw
            className={
              isLoading
                ? "insights-trends-spin"
                : undefined
            }
            size={18}
          />

          {isLoading
            ? "Refreshing"
            : "Refresh intelligence"}
        </button>
      </header>

      {errorMessage && (
        <div className="insights-trends-alert">
          <AlertCircle size={18} />
          {errorMessage}
        </div>
      )}

      <nav
        className="insights-trends-tabs"
        aria-label="Detailed insights"
      >
        <button
          className={
            activeTab === "themes"
              ? "active"
              : ""
          }
          aria-pressed={
            activeTab === "themes"
          }
          onClick={() => {
            setActiveTab(
              "themes"
            );
          }}
        >
          <Sparkles size={18} />
          Themes
        </button>

        <button
          className={
            activeTab === "moods"
              ? "active"
              : ""
          }
          aria-pressed={
            activeTab === "moods"
          }
          onClick={() => {
            setActiveTab(
              "moods"
            );
          }}
        >
          <Brain size={18} />
          Moods
        </button>
      </nav>

      <section
        className="insights-trends-coverage"
        aria-label="Detailed insights coverage"
      >
        <article>
          <span>
            <CheckCircle2 size={18} />
            Analyzed
          </span>

          <strong>
            {coverage.analyzedEntries}
          </strong>

          <small>
            Entries represented
          </small>
        </article>

        <article>
          <span>
            <TrendingUp size={18} />
            Coverage
          </span>

          <strong>
            {
              coverage
                .analysisCompletionPercent
            }%
          </strong>

          <small>
            Archive analyzed
          </small>
        </article>

        <article>
          <span>
            <CalendarDays size={18} />
            First analysis
          </span>

          <strong className="compact">
            {formatDate(
              coverage.firstEntryAt
            )}
          </strong>

          <small>
            Beginning of coverage
          </small>
        </article>

        <article>
          <span>
            <CalendarDays size={18} />
            Latest analysis
          </span>

          <strong className="compact">
            {formatDate(
              coverage.latestEntryAt
            )}
          </strong>

          <small>
            End of coverage
          </small>
        </article>
      </section>

      {activeTab === "themes" && (
        <div className="insights-themes-view">
          <section className="insights-trends-summary-grid">
            <article>
              <span>
                Ranked themes
              </span>

              <strong>
                {themes.themes.length}
              </strong>

              <small>
                Distinct patterns found
              </small>
            </article>

            <article>
              <span>
                Emerging
              </span>

              <strong>
                {
                  themes
                    .emergingThemes
                    .length
                }
              </strong>

              <small>
                Themes gaining momentum
              </small>
            </article>

            <article>
              <span>
                Trend window
              </span>

              <strong>
                {themes.windowSize}
              </strong>

              <small>
                Recent entries compared
              </small>
            </article>

            <article>
              <span>
                Months covered
              </span>

              <strong>
                {
                  themes
                    .monthlyBreakdown
                    .length
                }
              </strong>

              <small>
                Monthly groups available
              </small>
            </article>
          </section>

          <section className="insights-emerging-section">
            <header>
              <div>
                <p>
                  Momentum
                </p>

                <h2>
                  Emerging themes
                </h2>
              </div>

              <ArrowUpRight size={22} />
            </header>

            {themes.emergingThemes.length
              === 0 ? (
                <div className="insights-trends-empty">
                  No themes are currently
                  rising above their
                  previous window.
                </div>
              ) : (
                <div className="insights-emerging-grid">
                  {themes.emergingThemes.map(
                    (theme) => (
                      <article
                        key={theme.value}
                      >
                        <Sparkles
                          size={19}
                        />

                        <h3>
                          {formatLabel(
                            theme.value
                          )}
                        </h3>

                        <p>
                          {
                            theme.recentCount
                          }{" "}
                          recent versus{" "}
                          {
                            theme
                              .previousCount
                          }{" "}
                          previously
                        </p>

                        <ThemeTrendBadge
                          trend={
                            theme.trend
                          }
                        />
                      </article>
                    )
                  )}
                </div>
              )}
          </section>

          <section className="insights-theme-ranking-card">
            <header>
              <div>
                <p>
                  Full ranking
                </p>

                <h2>
                  Theme intelligence
                </h2>
              </div>

              <span>
                {themes.themes.length}{" "}
                themes
              </span>
            </header>

            {themes.themes.length === 0 ? (
              <div className="insights-trends-empty">
                Analyze more journal
                entries to build a theme
                ranking.
              </div>
            ) : (
              <div className="insights-theme-ranking-list">
                {themes.themes.map(
                  (
                    theme,
                    index
                  ) => (
                    <ThemeRanking
                      key={theme.value}
                      theme={theme}
                      rank={index + 1}
                    />
                  )
                )}
              </div>
            )}
          </section>

          <section className="insights-monthly-section">
            <header>
              <div>
                <p>
                  Timeline
                </p>

                <h2>
                  Themes by month
                </h2>
              </div>

              <CalendarDays
                size={22}
              />
            </header>

            {themes.monthlyBreakdown.length
              === 0 ? (
                <div className="insights-trends-empty">
                  Monthly theme history
                  is not available yet.
                </div>
              ) : (
                <div className="insights-theme-month-list">
                  {themes.monthlyBreakdown.map(
                    (month) => (
                      <article
                        key={
                          month.period
                        }
                      >
                        <div>
                          <h3>
                            {formatMonth(
                              month.period
                            )}
                          </h3>

                          <p>
                            {
                              month
                                .analyzedEntries
                            }{" "}
                            analyzed{" "}
                            {
                              month
                                .analyzedEntries
                                === 1
                                ? "entry"
                                : "entries"
                            }
                          </p>
                        </div>

                        <div className="insights-month-chips">
                          {month.topThemes.map(
                            (theme) => (
                              <span
                                key={
                                  theme.value
                                }
                              >
                                {formatLabel(
                                  theme.value
                                )}
                                <small>
                                  {
                                    theme
                                      .sharePercent
                                  }%
                                </small>
                              </span>
                            )
                          )}
                        </div>
                      </article>
                    )
                  )}
                </div>
              )}
          </section>
        </div>
      )}

      {activeTab === "moods" && (
        <div className="insights-moods-view">
          <section className="insights-mood-highlight-grid">
            <article className="mood">
              <span>
                Dominant mood
              </span>

              <strong>
                {formatLabel(
                  moods.dominantMood
                    ?.value
                    || null
                )}
              </strong>

              <small>
                {moods.dominantMood
                  ? (
                      `${moods
                        .dominantMood
                        .sharePercent}% of `
                      + "analyzed entries"
                    )
                  : (
                      "No dominant mood "
                      + "available"
                    )}
              </small>
            </article>

            <article className="sentiment">
              <span>
                Dominant sentiment
              </span>

              <strong>
                {formatLabel(
                  moods
                    .dominantSentiment
                    ?.value
                    || null
                )}
              </strong>

              <small>
                {moods.dominantSentiment
                  ? (
                      `${moods
                        .dominantSentiment
                        .sharePercent}% of `
                      + "analyzed entries"
                    )
                  : (
                      "No dominant sentiment "
                      + "available"
                    )}
              </small>
            </article>

            <article className="shift">
              <span>
                Mood shift
              </span>

              <strong>
                {moodShiftText}
              </strong>

              <small>
                Latest {moods.windowSize}
                {" "}entries compared with
                the previous window
              </small>
            </article>
          </section>

          <section
            className={
              "insights-mood-shift-card "
              + moodShiftState
            }
          >
            <div className="insights-mood-shift-icon">
              {moodShiftState
                === "changed" ? (
                <ArrowRight size={24} />
              ) : moodShiftState
                === "steady" ? (
                  <CircleMinus size={24} />
                ) : (
                  <Sparkles size={24} />
                )}
            </div>

            <div>
              <p>
                Recent emotional direction
              </p>

              <h2>
                {moodShiftHeading}
              </h2>

              <span>
                Previous:{" "}
                <strong>
                  {formatLabel(
                    previousMoodValue
                  )}
                </strong>
                {" · "}
                Recent:{" "}
                <strong>
                  {formatLabel(
                    recentMoodValue
                  )}
                </strong>
              </span>
            </div>
          </section>

          <section className="insights-distribution-grid">
            <DistributionList
              title="Mood distribution"
              description={
                "How often each mood "
                + "appears across your "
                + "analyzed archive."
              }
              items={moods.moods}
              emptyMessage={
                "No mood distribution "
                + "is available yet."
              }
              variant="mood"
            />

            <DistributionList
              title="Sentiment distribution"
              description={
                "The overall emotional "
                + "direction of your "
                + "journal entries."
              }
              items={
                moods.sentiments
              }
              emptyMessage={
                "No sentiment distribution "
                + "is available yet."
              }
              variant="sentiment"
            />
          </section>

          <section className="insights-monthly-section mood-history">
            <header>
              <div>
                <p>
                  Emotional timeline
                </p>

                <h2>
                  Moods by month
                </h2>
              </div>

              <Activity size={22} />
            </header>

            {moods.monthlyBreakdown.length
              === 0 ? (
                <div className="insights-trends-empty">
                  Monthly mood history
                  is not available yet.
                </div>
              ) : (
                <div className="insights-mood-month-grid">
                  {moods.monthlyBreakdown.map(
                    (month) => (
                      <article
                        key={
                          month.period
                        }
                      >
                        <header>
                          <div>
                            <h3>
                              {formatMonth(
                                month.period
                              )}
                            </h3>

                            <p>
                              {
                                month
                                  .analyzedEntries
                              }{" "}
                              analyzed{" "}
                              {
                                month
                                  .analyzedEntries
                                  === 1
                                  ? "entry"
                                  : "entries"
                              }
                            </p>
                          </div>

                          <CalendarDays
                            size={19}
                          />
                        </header>

                        <dl>
                          <div>
                            <dt>
                              Dominant mood
                            </dt>

                            <dd>
                              {formatLabel(
                                month
                                  .dominantMood
                                  ?.value
                                  || null
                              )}
                            </dd>
                          </div>

                          <div>
                            <dt>
                              Sentiment
                            </dt>

                            <dd>
                              {formatLabel(
                                month
                                  .dominantSentiment
                                  ?.value
                                  || null
                              )}
                            </dd>
                          </div>
                        </dl>

                        <div className="insights-month-chips">
                          {month.moods.map(
                            (mood) => (
                              <span
                                key={
                                  mood.value
                                }
                              >
                                {formatLabel(
                                  mood.value
                                )}

                                <small>
                                  {
                                    mood
                                      .sharePercent
                                  }%
                                </small>
                              </span>
                            )
                          )}
                        </div>
                      </article>
                    )
                  )}
                </div>
              )}
          </section>
        </div>
      )}

      <footer className="insights-trends-footer">
        <span>
          Themes version{" "}
          {themes.themesVersion}
        </span>

        <span>
          Moods version{" "}
          {moods.moodsVersion}
        </span>

        <span>
          Generated{" "}
          {new Date(
            themes.generatedAt
          ).toLocaleString([], {
            dateStyle: "medium",
            timeStyle: "short",
          })}
        </span>
      </footer>
    </section>
  );
}
