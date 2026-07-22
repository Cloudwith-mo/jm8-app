import {
  useMemo,
  useState,
} from "react";
import {
  AlertCircle,
  ArrowUp,
  BookOpen,
  BrainCircuit,
  CalendarRange,
  CheckCircle2,
  Clock3,
  FileImage,
  FileText,
  History,
  Lightbulb,
  LoaderCircle,
  MessageCircleQuestion,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  X,
} from "lucide-react";
import {
  ApiRequestError,
  askJm8,
} from "../../api/client";
import type {
  AskJm8Answer,
  AskJm8Evidence,
} from "../../types/askJm8";
import type {
  ToastKind,
} from "../ui/ToastStack";
import "./AskJm8Panel.css";


type AskJm8PanelProps = {
  onNotify: (
    kind: ToastKind,
    title: string,
    message?: string
  ) => void;
};


type AskHistoryItem = {
  id: string;
  answer: AskJm8Answer;
};


const questionSuggestions = [
  "What challenge keeps returning?",
  "Which goals do I mention most often?",
  "How has my mindset changed this year?",
  "When was I making the most progress?",
  "What advice would past me give current me?",
];


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


function formatPeriod(
  value: string
) {
  if (!value) {
    return "Not established";
  }

  if (
    /^\d{4}-\d{2}$/.test(value)
  ) {
    const parsed = new Date(
      `${value}-01T00:00:00Z`
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

  return value;
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
    return "Recently generated";
  }

  return parsed.toLocaleString(
    [],
    {
      dateStyle: "medium",
      timeStyle: "short",
    }
  );
}


function formatSourceType(
  value: AskJm8Evidence["sourceType"]
) {
  if (value === "image") {
    return "Scanned journal";
  }

  if (value === "typed") {
    return "Typed reflection";
  }

  return "Journal analysis";
}


function getScopeLabel(
  answer: AskJm8Answer
) {
  const {
    startDate,
    endDate,
    firstEntryAt,
    latestEntryAt,
  } = answer.scope;

  if (startDate || endDate) {
    return (
      `${startDate
        ? formatDate(startDate)
        : "Beginning"
      } – ${
        endDate
          ? formatDate(endDate)
          : "Present"
      }`
    );
  }

  if (
    firstEntryAt
    || latestEntryAt
  ) {
    return (
      `${firstEntryAt
        ? formatDate(firstEntryAt)
        : "Beginning"
      } – ${
        latestEntryAt
          ? formatDate(latestEntryAt)
          : "Present"
      }`
    );
  }

  return "All analyzed history";
}


function getErrorMessage(
  error: unknown
) {
  if (
    error instanceof ApiRequestError
  ) {
    if (
      error.status === 503
    ) {
      return (
        "JM8 is temporarily busy. "
        + "Please try the question again."
      );
    }

    if (
      error.code
      === "InvalidQuestion"
    ) {
      return (
        "Enter a question with at least "
        + "three characters."
      );
    }

    if (
      error.code
      === "FutureDateRange"
    ) {
      return (
        "The selected date range cannot "
        + "extend into the future."
      );
    }

    if (
      error.code
      === "InvalidDateRange"
    ) {
      return (
        "The start date must be before "
        + "the end date."
      );
    }

    return error.message;
  }

  return error instanceof Error
    ? error.message
    : (
        "JM8 could not answer "
        + "this question."
      );
}


function SourceIcon({
  sourceType,
}: {
  sourceType:
    AskJm8Evidence["sourceType"];
}) {
  if (sourceType === "image") {
    return <FileImage size={17} />;
  }

  return <FileText size={17} />;
}


function EmptyListMessage({
  children,
}: {
  children: string;
}) {
  return (
    <p className="ask-jm8-empty-list">
      {children}
    </p>
  );
}


export default function AskJm8Panel({
  onNotify,
}: AskJm8PanelProps) {
  const [
    question,
    setQuestion,
  ] = useState("");

  const [
    startDate,
    setStartDate,
  ] = useState("");

  const [
    endDate,
    setEndDate,
  ] = useState("");

  const [
    answer,
    setAnswer,
  ] = useState<
    AskJm8Answer | null
  >(null);

  const [
    isLoading,
    setIsLoading,
  ] = useState(false);

  const [
    errorMessage,
    setErrorMessage,
  ] = useState("");

  const [
    showHistory,
    setShowHistory,
  ] = useState(false);

  const [
    history,
    setHistory,
  ] = useState<
    AskHistoryItem[]
  >([]);

  const today = useMemo(
    () =>
      new Date()
        .toISOString()
        .slice(0, 10),
    []
  );

  const normalizedQuestion =
    question.trim();

  const canSubmit =
    normalizedQuestion.length >= 3
    && !isLoading;

  async function submitQuestion(
    questionOverride?: string
  ) {
    const nextQuestion = (
      questionOverride
      ?? question
    ).trim();

    if (nextQuestion.length < 3) {
      setErrorMessage(
        "Enter a question with at least three characters."
      );

      return;
    }

    if (
      startDate
      && endDate
      && startDate > endDate
    ) {
      setErrorMessage(
        "The start date must be before the end date."
      );

      return;
    }

    setIsLoading(true);
    setErrorMessage("");
    setQuestion(nextQuestion);
    setShowHistory(false);

    try {
      const result = await askJm8({
        question: nextQuestion,
        ...(startDate
          ? {
              startDate,
            }
          : {}),
        ...(endDate
          ? {
              endDate,
            }
          : {}),
      });

      setAnswer(result.answer);

      setHistory(
        (currentHistory) => [
          {
            id:
              `${result.answer.generatedAt}-${Date.now()}`,
            answer: result.answer,
          },
          ...currentHistory.filter(
            (item) =>
              item.answer.question
              !== result.answer.question
          ),
        ].slice(0, 8)
      );

      onNotify(
        "success",
        "JM8 answered",
        (
          "Your journal history "
          + "was analyzed successfully."
        )
      );
    } catch (error) {
      const message =
        getErrorMessage(error);

      setErrorMessage(message);

      onNotify(
        "error",
        "Answer unavailable",
        message
      );
    } finally {
      setIsLoading(false);
    }
  }

  function askSuggestedQuestion(
    value: string
  ) {
    setQuestion(value);

    void submitQuestion(value);
  }

  function selectHistoryItem(
    item: AskHistoryItem
  ) {
    setAnswer(item.answer);
    setQuestion(
      item.answer.question
    );
    setErrorMessage("");
    setShowHistory(false);
  }

  function startNewQuestion() {
    setQuestion("");
    setAnswer(null);
    setErrorMessage("");
    setShowHistory(false);
  }

  return (
    <section className="ask-jm8-panel">
      <header className="ask-jm8-page-header">
        <div>
          <p className="ask-jm8-kicker">
            <BrainCircuit size={16} />
            Journal intelligence
          </p>

          <h1>Ask JM8</h1>

          <p>
            Ask questions across your
            analyzed journal history and
            receive private, grounded
            reflections.
          </p>
        </div>

        <div className="ask-jm8-header-actions">
          {answer && (
            <button
              type="button"
              className="ask-jm8-new-button"
              onClick={
                startNewQuestion
              }
            >
              <RefreshCw size={17} />
              New question
            </button>
          )}

          <button
            type="button"
            className={
              showHistory
                ? (
                    "ask-jm8-history-button "
                    + "active"
                  )
                : "ask-jm8-history-button"
            }
            onClick={() =>
              setShowHistory(
                (current) => !current
              )
            }
            aria-expanded={
              showHistory
            }
          >
            <History size={18} />
            History
          </button>
        </div>
      </header>

      <form
        className="ask-jm8-composer"
        onSubmit={(event) => {
          event.preventDefault();

          void submitQuestion();
        }}
      >
        <div className="ask-jm8-input-shell">
          <Search
            size={20}
            aria-hidden="true"
          />

          <textarea
            value={question}
            onChange={(event) =>
              setQuestion(
                event.target.value
              )
            }
            placeholder={
              "Ask anything about your "
              + "journal history..."
            }
            maxLength={500}
            rows={2}
            aria-label={
              "Ask a question about "
              + "your journal history"
            }
          />

          <button
            type="submit"
            disabled={!canSubmit}
            aria-label="Ask JM8"
          >
            {isLoading ? (
              <LoaderCircle
                size={20}
                className="ask-jm8-spin"
              />
            ) : (
              <ArrowUp size={20} />
            )}
          </button>
        </div>

        <div className="ask-jm8-composer-footer">
          <div className="ask-jm8-composer-note">
            <ShieldCheck size={15} />

            <span>
              Private analysis using only
              your journal’s derived
              intelligence.
            </span>

            <small>
              {question.length}/500
            </small>
          </div>

          <div className="ask-jm8-date-controls">
            <CalendarRange size={16} />

            <label>
              <span>From</span>

              <input
                type="date"
                value={startDate}
                max={today}
                onChange={(event) =>
                  setStartDate(
                    event.target.value
                  )
                }
              />
            </label>

            <label>
              <span>To</span>

              <input
                type="date"
                value={endDate}
                max={today}
                onChange={(event) =>
                  setEndDate(
                    event.target.value
                  )
                }
              />
            </label>

            {(startDate || endDate) && (
              <button
                type="button"
                className="ask-jm8-clear-dates"
                onClick={() => {
                  setStartDate("");
                  setEndDate("");
                }}
                aria-label={
                  "Clear selected dates"
                }
              >
                <X size={15} />
              </button>
            )}
          </div>
        </div>
      </form>

      {showHistory && (
        <section className="ask-jm8-history-panel">
          <header>
            <div>
              <p>Current session</p>
              <h2>
                Recent questions
              </h2>
            </div>

            <span>
              {history.length} saved
            </span>
          </header>

          {history.length === 0 ? (
            <EmptyListMessage>
              Questions asked during this
              browser session will appear
              here.
            </EmptyListMessage>
          ) : (
            <div className="ask-jm8-history-list">
              {history.map(
                (item) => (
                  <button
                    type="button"
                    key={item.id}
                    onClick={() =>
                      selectHistoryItem(
                        item
                      )
                    }
                  >
                    <MessageCircleQuestion
                      size={18}
                    />

                    <span>
                      <strong>
                        {
                          item.answer
                            .question
                        }
                      </strong>

                      <small>
                        {formatGeneratedAt(
                          item.answer
                            .generatedAt
                        )}
                      </small>
                    </span>
                  </button>
                )
              )}
            </div>
          )}
        </section>
      )}

      {errorMessage && (
        <div
          className="ask-jm8-alert"
          role="alert"
        >
          <AlertCircle size={18} />
          <span>{errorMessage}</span>

          <button
            type="button"
            onClick={() =>
              setErrorMessage("")
            }
            aria-label={
              "Dismiss error"
            }
          >
            <X size={16} />
          </button>
        </div>
      )}

      {isLoading && (
        <section
          className="ask-jm8-loading"
          aria-live="polite"
        >
          <div className="ask-jm8-loading-icon">
            <LoaderCircle
              size={25}
              className="ask-jm8-spin"
            />
          </div>

          <div>
            <h2>
              JM8 is reviewing your
              journal history
            </h2>

            <p>
              Ranking recurring signals,
              comparing periods, and
              preparing a grounded answer.
            </p>
          </div>
        </section>
      )}

      {!isLoading && !answer && (
        <section className="ask-jm8-empty-state">
          <div className="ask-jm8-empty-icon">
            <Sparkles size={31} />
          </div>

          <p className="ask-jm8-empty-kicker">
            Your private intelligence layer
          </p>

          <h2>
            Ask across your entire
            journal history
          </h2>

          <p>
            JM8 looks for recurring themes,
            challenges, goals, mood shifts,
            growth signals, and behavioral
            patterns across time.
          </p>

          <div className="ask-jm8-suggestions">
            {questionSuggestions.map(
              (suggestion) => (
                <button
                  type="button"
                  key={suggestion}
                  onClick={() =>
                    askSuggestedQuestion(
                      suggestion
                    )
                  }
                >
                  <MessageCircleQuestion
                    size={16}
                  />
                  {suggestion}
                </button>
              )
            )}
          </div>
        </section>
      )}

      {!isLoading && answer && (
        <div className="ask-jm8-answer-layout">
          <main className="ask-jm8-answer-column">
            <article
              className={
                answer.status
                === "ANSWERED"
                  ? (
                      "ask-jm8-answer-card "
                      + "answered"
                    )
                  : (
                      "ask-jm8-answer-card "
                      + "insufficient"
                    )
              }
            >
              <header className="ask-jm8-answer-header">
                <div className="ask-jm8-identity">
                  <div>
                    <BrainCircuit
                      size={22}
                    />
                  </div>

                  <span>
                    <strong>
                      JM8 Intelligence
                    </strong>

                    <small>
                      Private journal
                      analysis
                    </small>
                  </span>
                </div>

                <div className="ask-jm8-answer-meta">
                  <span>
                    <CheckCircle2
                      size={15}
                    />

                    {answer.status
                      === "ANSWERED"
                      ? "Grounded answer"
                      : "Limited context"}
                  </span>

                  <small>
                    {getScopeLabel(
                      answer
                    )}
                  </small>
                </div>
              </header>

              <div className="ask-jm8-question-label">
                <MessageCircleQuestion
                  size={15}
                />

                <span>
                  {answer.question}
                </span>
              </div>

              <h2>
                {answer.answer.headline}
              </h2>

              <p className="ask-jm8-answer-summary">
                {answer.answer.summary}
              </p>

              <p className="ask-jm8-answer-explanation">
                {
                  answer.answer
                    .explanation
                }
              </p>

              <div className="ask-jm8-metrics-grid">
                <article>
                  <div className="ask-jm8-metric-icon purple">
                    <Target size={18} />
                  </div>

                  <span>
                    Mentions
                  </span>

                  <strong>
                    {
                      answer.metrics
                        .mentionCount
                    }
                  </strong>

                  <small>
                    Grounded recurring
                    signals
                  </small>
                </article>

                <article>
                  <div className="ask-jm8-metric-icon blue">
                    <Clock3 size={18} />
                  </div>

                  <span>
                    Strongest period
                  </span>

                  <strong className="compact">
                    {formatPeriod(
                      answer.metrics
                        .strongestPeriod
                    )}
                  </strong>

                  <small>
                    Highest concentration
                  </small>
                </article>

                <article>
                  <div className="ask-jm8-metric-icon green">
                    <TrendingUp
                      size={18}
                    />
                  </div>

                  <span>
                    Improvement
                  </span>

                  <strong>
                    {answer.metrics
                      .improvementPercent
                      > 0
                      ? (
                          `${
                            answer.metrics
                              .improvementPercent
                          }%`
                        )
                      : "—"}
                  </strong>

                  <small>
                    Evidence-supported
                    change
                  </small>
                </article>

                <article>
                  <div className="ask-jm8-metric-icon amber">
                    <Lightbulb
                      size={18}
                    />
                  </div>

                  <span>
                    Top trigger
                  </span>

                  <strong className="compact">
                    {
                      answer.metrics
                        .topTrigger
                      || "Not established"
                    }
                  </strong>

                  <small>
                    Strongest associated
                    signal
                  </small>
                </article>
              </div>
            </article>

            <section className="ask-jm8-evidence-section">
              <header>
                <div>
                  <p>
                    Supporting analysis
                  </p>

                  <h2>
                    Key evidence
                  </h2>
                </div>

                <BookOpen size={21} />
              </header>

              {answer.evidence.length
                === 0 ? (
                  <EmptyListMessage>
                    No source-level evidence
                    was strong enough to
                    include.
                  </EmptyListMessage>
                ) : (
                  <div className="ask-jm8-evidence-list">
                    {answer.evidence.map(
                      (
                        evidence,
                        index
                      ) => (
                        <article
                          key={
                            `${
                              evidence.date
                            }-${
                              evidence
                                .sourceType
                            }-${index}`
                          }
                        >
                          <div className="ask-jm8-evidence-number">
                            {index + 1}
                          </div>

                          <div>
                            <p>
                              {
                                evidence
                                  .paraphrase
                              }
                            </p>

                            <span>
                              <SourceIcon
                                sourceType={
                                  evidence
                                    .sourceType
                                }
                              />

                              {formatSourceType(
                                evidence
                                  .sourceType
                              )}

                              <small>
                                {formatDate(
                                  evidence.date
                                )}
                              </small>
                            </span>
                          </div>

                          <em
                            className={
                              evidence
                                .relevance
                            }
                          >
                            {
                              evidence
                                .relevance
                            }
                          </em>
                        </article>
                      )
                    )}
                  </div>
                )}
            </section>

            <section className="ask-jm8-growth-section">
              <header>
                <div>
                  <p>
                    Progress over time
                  </p>

                  <h2>
                    Growth and resolution
                  </h2>
                </div>

                <TrendingUp size={21} />
              </header>

              {answer.growthSignals.length
                === 0 ? (
                  <EmptyListMessage>
                    No grounded growth
                    signals were available
                    for this question.
                  </EmptyListMessage>
                ) : (
                  <ul>
                    {answer.growthSignals.map(
                      (signal) => (
                        <li key={signal}>
                          <CheckCircle2
                            size={17}
                          />
                          <span>
                            {signal}
                          </span>
                        </li>
                      )
                    )}
                  </ul>
                )}
            </section>

            {answer.limitations.length
              > 0 && (
                <section className="ask-jm8-limitations">
                  <header>
                    <AlertCircle
                      size={19}
                    />

                    <h2>
                      What to keep in mind
                    </h2>
                  </header>

                  <ul>
                    {answer.limitations.map(
                      (limitation) => (
                        <li
                          key={limitation}
                        >
                          {limitation}
                        </li>
                      )
                    )}
                  </ul>
                </section>
              )}

            <footer className="ask-jm8-answer-footer">
              <ShieldCheck size={16} />

              <span>
                Generated from structured
                journal-analysis signals.
                Raw journal text is not
                returned by this screen.
              </span>

              <small>
                {formatGeneratedAt(
                  answer.generatedAt
                )}
              </small>
            </footer>
          </main>

          <aside className="ask-jm8-right-rail">
            <section className="ask-jm8-coverage-card">
              <header>
                <div>
                  <p>Answer coverage</p>
                  <h2>
                    Journal scope
                  </h2>
                </div>

                <ShieldCheck size={20} />
              </header>

              <div className="ask-jm8-coverage-stats">
                <div>
                  <span>
                    Total entries
                  </span>

                  <strong>
                    {
                      answer.coverage
                        .totalEntries
                    }
                  </strong>
                </div>

                <div>
                  <span>
                    Analyzed
                  </span>

                  <strong>
                    {
                      answer.coverage
                        .analyzedEntries
                    }
                  </strong>
                </div>

                <div>
                  <span>
                    Completion
                  </span>

                  <strong>
                    {
                      answer.coverage
                        .analysisCompletionPercent
                    }%
                  </strong>
                </div>

                <div>
                  <span>
                    Sources used
                  </span>

                  <strong>
                    {
                      answer.coverage
                        .sourceSignalsIncluded
                    }
                  </strong>
                </div>
              </div>
            </section>

            <section className="ask-jm8-rail-card">
              <header>
                <div>
                  <p>Source matches</p>
                  <h2>
                    Top matching sources
                  </h2>
                </div>

                <BookOpen size={20} />
              </header>

              {answer.evidence.length
                === 0 ? (
                  <EmptyListMessage>
                    No matching sources were
                    returned.
                  </EmptyListMessage>
                ) : (
                  <div className="ask-jm8-source-list">
                    {answer.evidence
                      .slice(0, 4)
                      .map(
                        (
                          evidence,
                          index
                        ) => (
                          <article
                            key={
                              `source-${
                                evidence.date
                              }-${index}`
                            }
                          >
                            <div>
                              <SourceIcon
                                sourceType={
                                  evidence
                                    .sourceType
                                }
                              />
                            </div>

                            <span>
                              <strong>
                                {formatSourceType(
                                  evidence
                                    .sourceType
                                )}
                              </strong>

                              <small>
                                {formatDate(
                                  evidence.date
                                )}
                              </small>
                            </span>

                            <em>
                              {
                                evidence
                                  .relevance
                              }
                            </em>
                          </article>
                        )
                      )}
                  </div>
                )}
            </section>

            <section className="ask-jm8-rail-card">
              <header>
                <div>
                  <p>
                    Core conclusions
                  </p>

                  <h2>
                    Key takeaways
                  </h2>
                </div>

                <Lightbulb size={20} />
              </header>

              {answer.takeaways.length
                === 0 ? (
                  <EmptyListMessage>
                    No additional takeaways
                    were available.
                  </EmptyListMessage>
                ) : (
                  <ol className="ask-jm8-takeaway-list">
                    {answer.takeaways.map(
                      (
                        takeaway,
                        index
                      ) => (
                        <li key={takeaway}>
                          <strong>
                            {index + 1}
                          </strong>

                          <span>
                            {takeaway}
                          </span>
                        </li>
                      )
                    )}
                  </ol>
                )}
            </section>

            <section className="ask-jm8-rail-card">
              <header>
                <div>
                  <p>
                    Connected patterns
                  </p>

                  <h2>
                    Related themes
                  </h2>
                </div>

                <Sparkles size={20} />
              </header>

              {answer.relatedThemes.length
                === 0 ? (
                  <EmptyListMessage>
                    No related themes were
                    grounded strongly enough
                    to include.
                  </EmptyListMessage>
                ) : (
                  <div className="ask-jm8-theme-list">
                    {answer.relatedThemes.map(
                      (theme) => (
                        <span key={theme}>
                          {theme.replaceAll(
                            "_",
                            " "
                          )}
                        </span>
                      )
                    )}
                  </div>
                )}
            </section>

            <section className="ask-jm8-follow-up-card">
              <header>
                <div>
                  <p>Continue exploring</p>
                  <h2>
                    Ask a follow-up
                  </h2>
                </div>

                <MessageCircleQuestion
                  size={20}
                />
              </header>

              {answer
                .suggestedFollowUps
                .length === 0 ? (
                  <EmptyListMessage>
                    Ask another question
                    using the composer above.
                  </EmptyListMessage>
                ) : (
                  <div className="ask-jm8-follow-up-list">
                    {answer
                      .suggestedFollowUps
                      .map(
                        (followUp) => (
                          <button
                            type="button"
                            key={followUp}
                            onClick={() =>
                              askSuggestedQuestion(
                                followUp
                              )
                            }
                          >
                            <span>
                              {followUp}
                            </span>

                            <ArrowUp
                              size={16}
                            />
                          </button>
                        )
                      )}
                  </div>
                )}
            </section>
          </aside>
        </div>
      )}
    </section>
  );
}
