import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import type {
  ReactNode,
} from "react";
import {
  AlertCircle,
  BarChart3,
  Brain,
  CalendarDays,
  CheckCircle2,
  Gauge,
  MessageSquareQuote,
  RefreshCw,
  Sparkles,
  Target,
  TrendingUp,
} from "lucide-react";
import {
  getInsightsOverview,
} from "../../api/client";
import type {
  InsightsOverview,
  InsightsRankedItem,
} from "../../types/insights";
import type {
  ToastKind,
} from "../ui/ToastStack";
import "./InsightsOverviewPanel.css";


type InsightsOverviewPanelProps = {
  onNotify: (
    kind: ToastKind,
    title: string,
    message?: string
  ) => void;
};

type LoadOptions = {
  notify?: boolean;
};

type RankedListProps = {
  title: string;
  description: string;
  items: InsightsRankedItem[];
  icon: ReactNode;
  emptyMessage: string;
};


function formatLabel(
  value: string
) {
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


function getErrorMessage(
  error: unknown
) {
  return error instanceof Error
    ? error.message
    : (
        "JM8 could not load your "
        + "insights overview."
      );
}


function RankedList({
  title,
  description,
  items,
  icon,
  emptyMessage,
}: RankedListProps) {
  return (
    <article className="insights-ranked-card">
      <header>
        <span className="insights-card-icon">
          {icon}
        </span>

        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </header>

      {items.length === 0 ? (
        <div className="insights-ranked-empty">
          {emptyMessage}
        </div>
      ) : (
        <ol className="insights-ranked-list">
          {items.map(
            (
              item,
              index
            ) => {
              const share = Math.min(
                Math.max(
                  item.sharePercent,
                  0
                ),
                100
              );

              return (
                <li
                  key={
                    item.value
                  }
                >
                  <div className="insights-ranked-copy">
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
                    className="insights-ranked-track"
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


export default function InsightsOverviewPanel({
  onNotify,
}: InsightsOverviewPanelProps) {
  const notifyRef = useRef(
    onNotify
  );

  const [
    overview,
    setOverview,
  ] = useState<
    InsightsOverview | null
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
    notifyRef.current = onNotify;
  }, [onNotify]);

  const loadOverview = useCallback(
    async (
      options: LoadOptions = {}
    ) => {
      setIsLoading(true);

      try {
        const result =
          await getInsightsOverview();

        setOverview(
          result.overview
        );

        setErrorMessage("");

        if (options.notify) {
          notifyRef.current(
            "success",
            "Insights refreshed",
            (
              "Your journal overview "
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
          "Insights unavailable",
          message
        );
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  if (
    isLoading
    && !overview
  ) {
    return (
      <section
        className={
          "insights-overview-page "
          + "insights-loading-state"
        }
      >
        <RefreshCw
          className="insights-spin"
          size={34}
        />

        <h1>
          Building your overview
        </h1>

        <p>
          JM8 is aggregating your
          existing journal analyses.
        </p>
      </section>
    );
  }

  if (
    errorMessage
    && !overview
  ) {
    return (
      <section
        className={
          "insights-overview-page "
          + "insights-error-state"
        }
      >
        <AlertCircle
          size={38}
        />

        <h1>
          Insights could not be loaded
        </h1>

        <p>
          {errorMessage}
        </p>

        <button
          onClick={() => {
            void loadOverview({
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

  if (!overview) {
    return null;
  }

  const {
    coverage,
  } = overview;

  const hasAnalyzedEntries =
    coverage.analyzedEntries > 0;

  return (
    <section className="insights-overview-page">
      <header className="insights-overview-header">
        <div>
          <p className="insights-overview-kicker">
            <BarChart3 size={17} />
            JM8 Intelligence
          </p>

          <h1>
            Your Insights Overview
          </h1>

          <p>
            A private summary of the
            patterns appearing across
            your analyzed journal
            history.
          </p>
        </div>

        <button
          className="insights-refresh-button"
          disabled={isLoading}
          onClick={() => {
            void loadOverview({
              notify: true,
            });
          }}
        >
          <RefreshCw
            className={
              isLoading
                ? "insights-spin"
                : undefined
            }
            size={18}
          />

          {isLoading
            ? "Refreshing"
            : "Refresh overview"}
        </button>
      </header>

      {errorMessage && (
        <div className="insights-inline-alert">
          <AlertCircle size={18} />
          {errorMessage}
        </div>
      )}

      <section
        className="insights-coverage-grid"
        aria-label="Journal analysis coverage"
      >
        <article>
          <span>
            <BarChart3 size={18} />
            Journal archive
          </span>

          <strong>
            {coverage.totalEntries}
          </strong>

          <small>
            Total journal entries
          </small>
        </article>

        <article>
          <span>
            <CheckCircle2 size={18} />
            Analyzed
          </span>

          <strong>
            {coverage.analyzedEntries}
          </strong>

          <small>
            Entries included
          </small>
        </article>

        <article>
          <span>
            <Gauge size={18} />
            Coverage
          </span>

          <strong>
            {
              coverage
                .analysisCompletionPercent
            }%
          </strong>

          <small>
            Analysis completion
          </small>
        </article>

        <article>
          <span>
            <CalendarDays size={18} />
            Date range
          </span>

          <strong className="insights-date-value">
            {formatDate(
              coverage.firstEntryAt
            )}
          </strong>

          <small>
            Through{" "}
            {formatDate(
              coverage.latestEntryAt
            )}
          </small>
        </article>
      </section>

      {!hasAnalyzedEntries && (
        <section className="insights-empty-analysis">
          <Sparkles size={28} />

          <div>
            <h2>
              Analyze entries to unlock
              your overview
            </h2>

            <p>
              JM8 has not found any
              completed journal analyses
              to aggregate yet.
            </p>
          </div>
        </section>
      )}

      <section className="insights-highlight-grid">
        <article className="insights-highlight-card mood">
          <div className="insights-highlight-icon">
            <Brain size={23} />
          </div>

          <div>
            <span>
              Dominant mood
            </span>

            <strong>
              {overview.dominantMood
                ? formatLabel(
                    overview
                      .dominantMood
                      .value
                  )
                : "Not enough data"}
            </strong>

            <small>
              {overview.dominantMood
                ? (
                    `${overview
                      .dominantMood
                      .sharePercent}% of `
                    + "analyzed entries"
                  )
                : (
                    "Analyze more entries "
                    + "to establish a pattern"
                  )}
            </small>
          </div>
        </article>

        <article className="insights-highlight-card sentiment">
          <div className="insights-highlight-icon">
            <TrendingUp size={23} />
          </div>

          <div>
            <span>
              Dominant sentiment
            </span>

            <strong>
              {overview
                .dominantSentiment
                ? formatLabel(
                    overview
                      .dominantSentiment
                      .value
                  )
                : "Not enough data"}
            </strong>

            <small>
              {overview
                .dominantSentiment
                ? (
                    `${overview
                      .dominantSentiment
                      .sharePercent}% of `
                    + "analyzed entries"
                  )
                : (
                    "No dominant sentiment "
                    + "is available yet"
                  )}
            </small>
          </div>
        </article>
      </section>

      <section className="insights-ranked-grid">
        <RankedList
          title="Top themes"
          description={
            "The subjects that return "
            + "most often."
          }
          items={
            overview.topThemes
          }
          icon={
            <Sparkles size={20} />
          }
          emptyMessage={
            "No recurring themes "
            + "are available yet."
          }
        />

        <RankedList
          title="Recurring challenges"
          description={
            "Challenges appearing across "
            + "multiple reflections."
          }
          items={
            overview.topChallenges
          }
          icon={
            <AlertCircle size={20} />
          }
          emptyMessage={
            "No recurring challenges "
            + "were identified."
          }
        />

        <RankedList
          title="Recurring goals"
          description={
            "The outcomes you continue "
            + "working toward."
          }
          items={
            overview.topGoals
          }
          icon={
            <Target size={20} />
          }
          emptyMessage={
            "No recurring goals "
            + "were identified."
          }
        />

        <RankedList
          title="Notable progress"
          description={
            "Signals of growth, learning, "
            + "and forward movement."
          }
          items={
            overview.notableProgress
          }
          icon={
            <TrendingUp size={20} />
          }
          emptyMessage={
            "No repeated growth signals "
            + "are available yet."
          }
        />

        <RankedList
          title="Behavior patterns"
          description={
            "Repeated actions and habits "
            + "visible in your writing."
          }
          items={
            overview.behaviorPatterns
          }
          icon={
            <Brain size={20} />
          }
          emptyMessage={
            "No repeated behavior patterns "
            + "are available yet."
          }
        />

        <RankedList
          title="Recent mindset signals"
          description={
            `Based on your latest ${
              overview
                .recentAnalyzedEntries
            } analyzed entries.`
          }
          items={
            overview
              .recentMindsetSignals
          }
          icon={
            <Gauge size={20} />
          }
          emptyMessage={
            "No recent mindset signals "
            + "are available yet."
          }
        />
      </section>

      <section className="insights-reflection-card">
        <div className="insights-reflection-icon">
          <MessageSquareQuote
            size={24}
          />
        </div>

        <div>
          <p>
            Suggested reflection
          </p>

          <h2>
            {
              overview
                .reflectionPrompt
            }
          </h2>
        </div>
      </section>

      <footer className="insights-overview-footer">
        <span>
          Overview version{" "}
          {overview.overviewVersion}
        </span>

        <span>
          Generated{" "}
          {new Date(
            overview.generatedAt
          ).toLocaleString([], {
            dateStyle: "medium",
            timeStyle: "short",
          })}
        </span>
      </footer>
    </section>
  );
}
