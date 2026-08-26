import {
  useEffect,
  useMemo,
  useRef,
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
  Trash2,
  TrendingUp,
  X,
} from "lucide-react";
import {
  ApiRequestError,
  askJm8,
  deleteAskJm8History,
  getAskJm8History,
  listAskJm8History,
} from "../../api/client";
import type {
  AskJm8Answer,
  AskJm8Evidence,
  AskJm8HistorySummary,
} from "../../types/askJm8";
import type {
  UsageOperation,
} from "../../types/usage";
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
  usage?: UsageOperation;
  onUsageChanged: () => Promise<void>;
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
      error.status === 429
      || error.code
        === "UsageLimitExceeded"
    ) {
      return (
        "Your monthly Ask JM8 "
        + "allowance has been reached."
      );
    }

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


function getHistoryErrorMessage(
  error: unknown
) {
  if (
    error instanceof ApiRequestError
  ) {
    if (error.status === 400) {
      return error.message;
    }

    if (error.status === 404) {
      return (
        "This saved answer is no "
        + "longer available."
      );
    }

    if (error.status === 503) {
      return (
        "Saved Ask JM8 history is "
        + "temporarily unavailable."
      );
    }

    return error.message;
  }

  return error instanceof Error
    ? error.message
    : (
        "Saved Ask JM8 history could "
        + "not be loaded."
      );
}


function mergeHistoryPages(
  currentHistory:
    AskJm8HistorySummary[],
  incomingHistory:
    AskJm8HistorySummary[]
) {
  const merged = [
    ...currentHistory,
  ];

  const seenHistoryIds = new Set(
    currentHistory.map(
      (item) => item.historyId
    )
  );

  for (
    const item
    of incomingHistory
  ) {
    if (
      seenHistoryIds.has(
        item.historyId
      )
    ) {
      continue;
    }

    seenHistoryIds.add(
      item.historyId
    );

    merged.push(item);
  }

  return merged;
}


function isMissingHistoryError(
  error: unknown
) {
  return (
    error instanceof ApiRequestError
    && error.status === 404
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
  usage,
  onUsageChanged,
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
    AskJm8HistorySummary[]
  >([]);

  const [
    isHistoryLoading,
    setIsHistoryLoading,
  ] = useState(false);

  const [
    historyError,
    setHistoryError,
  ] = useState("");

  const [
    activeHistoryId,
    setActiveHistoryId,
  ] = useState<string | null>(
    null
  );

  const [
    loadingHistoryId,
    setLoadingHistoryId,
  ] = useState<string | null>(
    null
  );

  const [
    deletingHistoryId,
    setDeletingHistoryId,
  ] = useState<string | null>(
    null
  );

  const [
    nextHistoryCursor,
    setNextHistoryCursor,
  ] = useState<string | null>(
    null
  );

  const [
    isHistoryPageLoading,
    setIsHistoryPageLoading,
  ] = useState(false);

  const [
    historyPageError,
    setHistoryPageError,
  ] = useState("");

  const historyPageRequestRef =
    useRef<string | null>(null);

  const historySelectionControllerRef =
    useRef<AbortController | null>(null);

  const historySelectionRequestRef =
    useRef(0);

  const loadedHistoryCursorsRef =
    useRef<Set<string>>(
      new Set()
    );

  const today = useMemo(
    () =>
      new Date()
        .toISOString()
        .slice(0, 10),
    []
  );

  const normalizedQuestion =
    question.trim();

  const isUsageExhausted =
    usage?.allowed === false;

  const canSubmit =
    normalizedQuestion.length >= 3
    && !isLoading
    && !isUsageExhausted;

  useEffect(() => {
    const controller = new AbortController();

    async function loadInitialHistory() {
      setIsHistoryLoading(true);
      setHistoryError("");

      try {
        const result =
          await listAskJm8History({
            limit: 20,
            signal: controller.signal,
          });

        if (!controller.signal.aborted) {
          setHistory(result.history);

          setNextHistoryCursor(
            result.nextCursor
          );

          setHistoryPageError("");

          loadedHistoryCursorsRef
            .current
            .clear();
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setHistoryError(
            getHistoryErrorMessage(
              error
            )
          );
        }
      } finally {
        if (!controller.signal.aborted) {
          setIsHistoryLoading(false);
        }
      }
    }

    void loadInitialHistory();

    return () => {
      controller.abort();
      historySelectionControllerRef.current?.abort();
    };
  }, []);


  async function refreshHistory({
    notifyOnError = false,
  }: {
    notifyOnError?: boolean;
  } = {}) {
    setIsHistoryLoading(true);
    setHistoryError("");

    try {
      const result =
        await listAskJm8History({
          limit: 20,
        });

      setHistory(result.history);

      setNextHistoryCursor(
        result.nextCursor
      );

      setHistoryPageError("");

      loadedHistoryCursorsRef
        .current
        .clear();

      return true;
    } catch (error) {
      const message =
        getHistoryErrorMessage(error);

      setHistoryError(message);

      if (notifyOnError) {
        onNotify(
          "error",
          "History unavailable",
          message
        );
      }

      return false;
    } finally {
      setIsHistoryLoading(false);
    }
  }


  async function loadMoreHistory() {
    const cursor =
      nextHistoryCursor;

    if (
      !cursor
      || isHistoryPageLoading
      || historyPageRequestRef
        .current === cursor
    ) {
      return;
    }

    if (
      loadedHistoryCursorsRef
        .current
        .has(cursor)
    ) {
      setNextHistoryCursor(null);

      setHistoryPageError(
        (
          "JM8 stopped loading older "
          + "history because this page "
          + "was already received."
        )
      );

      return;
    }

    historyPageRequestRef.current =
      cursor;

    setIsHistoryPageLoading(true);
    setHistoryPageError("");

    try {
      const result =
        await listAskJm8History({
          limit: 20,
          cursor,
        });

      loadedHistoryCursorsRef
        .current
        .add(cursor);

      setHistory(
        (currentHistory) =>
          mergeHistoryPages(
            currentHistory,
            result.history
          )
      );

      if (
        result.nextCursor
        === cursor
      ) {
        setNextHistoryCursor(null);

        setHistoryPageError(
          (
            "JM8 stopped loading older "
            + "history because pagination "
            + "did not advance."
          )
        );
      } else {
        setNextHistoryCursor(
          result.nextCursor
        );
      }
    } catch (error) {
      setHistoryPageError(
        getHistoryErrorMessage(
          error
        )
      );
    } finally {
      historyPageRequestRef.current =
        null;

      setIsHistoryPageLoading(false);
    }
  }


  function removeHistoryItemLocally(
    historyId: string
  ) {
    setHistory(
      (currentHistory) =>
        currentHistory.filter(
          (item) =>
            item.historyId
            !== historyId
        )
    );

    if (
      activeHistoryId
      !== historyId
    ) {
      return;
    }

    setAnswer(null);
    setQuestion("");
    setStartDate("");
    setEndDate("");
    setActiveHistoryId(null);
  }


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

    if (isUsageExhausted) {
      setErrorMessage(
        (
          "Your monthly Ask JM8 "
          + "allowance has been reached."
        )
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
      setActiveHistoryId(
        result.history.historyId
      );

      await onUsageChanged();
      await refreshHistory();

      onNotify(
        "success",
        "JM8 answered",
        (
          "Your journal history "
          + "was analyzed successfully."
        )
      );
    } catch (error) {
      if (
        error instanceof ApiRequestError
        && error.status === 429
      ) {
        await onUsageChanged();
      }

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

  async function selectHistoryItem(
    item: AskJm8HistorySummary
  ) {
    historySelectionControllerRef.current?.abort();
    const controller = new AbortController();
    historySelectionControllerRef.current = controller;
    const request = ++historySelectionRequestRef.current;
    setLoadingHistoryId(
      item.historyId
    );
    setHistoryError("");

    try {
      const result =
        await getAskJm8History(
          item.historyId,
          controller.signal,
        );

      if (controller.signal.aborted || request !== historySelectionRequestRef.current) return;

      const savedAnswer =
        result.history.answer;

      setAnswer(savedAnswer);
      setQuestion(
        savedAnswer.question
      );
      setStartDate(
        savedAnswer.scope.startDate
        ?? ""
      );
      setEndDate(
        savedAnswer.scope.endDate
        ?? ""
      );
      setActiveHistoryId(
        item.historyId
      );
      setErrorMessage("");
      setShowHistory(false);
    } catch (error) {
      if (controller.signal.aborted || request !== historySelectionRequestRef.current) return;
      if (
        isMissingHistoryError(error)
      ) {
        removeHistoryItemLocally(
          item.historyId
        );

        setHistoryError("");

        onNotify(
          "info",
          "Saved answer removed",
          (
            "This answer was deleted "
            + "from another session or "
            + "device."
          )
        );

        return;
      }

      const message =
        getHistoryErrorMessage(error);

      setHistoryError(message);

      onNotify(
        "error",
        "Saved answer unavailable",
        message
      );
    } finally {
      if (!controller.signal.aborted && request === historySelectionRequestRef.current) {
        setLoadingHistoryId(null);
      }
    }
  }


  async function deleteHistoryItem(
    item: AskJm8HistorySummary
  ) {
    const confirmed = window.confirm(
      (
        "Delete this saved Ask JM8 "
        + "answer? This cannot be undone."
      )
    );

    if (!confirmed) {
      return;
    }

    setDeletingHistoryId(
      item.historyId
    );
    setHistoryError("");

    try {
      await deleteAskJm8History(
        item.historyId
      );

      removeHistoryItemLocally(
        item.historyId
      );

      onNotify(
        "success",
        "Saved answer deleted",
        (
          "The Ask JM8 answer was "
          + "removed from your history."
        )
      );
    } catch (error) {
      if (
        isMissingHistoryError(error)
      ) {
        removeHistoryItemLocally(
          item.historyId
        );

        setHistoryError("");

        onNotify(
          "info",
          "Already removed",
          (
            "This saved answer had "
            + "already been deleted from "
            + "another session or device."
          )
        );

        return;
      }

      const message =
        getHistoryErrorMessage(error);

      setHistoryError(message);

      onNotify(
        "error",
        "Delete failed",
        message
      );
    } finally {
      setDeletingHistoryId(null);
    }
  }


  function startNewQuestion() {
    historySelectionControllerRef.current?.abort();
    historySelectionRequestRef.current += 1;
    setLoadingHistoryId(null);
    setQuestion("");
    setAnswer(null);
    setActiveHistoryId(null);
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

      {usage && (
        <div
          className={
            isUsageExhausted
              ? (
                  "ask-jm8-usage-state "
                  + "exhausted"
                )
              : "ask-jm8-usage-state"
          }
        >
          <span>
            Ask JM8 monthly allowance
          </span>

          <strong>
            {usage.remaining}
            {" "}
            of
            {" "}
            {usage.limit}
            {" "}
            remaining
          </strong>
        </div>
      )}

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
            disabled={
              isUsageExhausted
            }
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
              <p>Private history</p>
              <h2>
                Saved questions
              </h2>
            </div>

            <span>
              {isHistoryLoading
                ? "Loading..."
                : (
                    `${history.length} loaded`
                  )}
            </span>
          </header>

          {historyError && (
            <div
              className="ask-jm8-history-error"
              role="alert"
            >
              <AlertCircle size={17} />

              <span>
                {historyError}
              </span>

              <button
                type="button"
                onClick={() => {
                  void refreshHistory({
                    notifyOnError: true,
                  });
                }}
                disabled={
                  isHistoryLoading
                }
              >
                <RefreshCw
                  size={15}
                  className={
                    isHistoryLoading
                      ? "ask-jm8-spin"
                      : undefined
                  }
                />
                Retry
              </button>
            </div>
          )}

          {isHistoryLoading
            && history.length === 0 ? (
              <div
                className={
                  "ask-jm8-history-loading"
                }
                aria-live="polite"
              >
                <LoaderCircle
                  size={19}
                  className="ask-jm8-spin"
                />

                <span>
                  Loading saved questions…
                </span>
              </div>
            ) : history.length === 0 ? (
              <EmptyListMessage>
                Saved Ask JM8 answers will
                appear here across sessions
                and devices.
              </EmptyListMessage>
            ) : (
              <div className="ask-jm8-history-list">
                {history.map(
                  (item) => {
                    const isOpening =
                      loadingHistoryId
                      === item.historyId;

                    const isDeleting =
                      deletingHistoryId
                      === item.historyId;

                    return (
                      <article
                        className={
                          activeHistoryId
                          === item.historyId
                            ? (
                                "ask-jm8-"
                                + "history-row "
                                + "active"
                              )
                            : (
                                "ask-jm8-"
                                + "history-row"
                              )
                        }
                        key={item.historyId}
                      >
                        <button
                          type="button"
                          className={
                            "ask-jm8-"
                            + "history-open"
                          }
                          onClick={() => {
                            void selectHistoryItem(
                              item
                            );
                          }}
                          disabled={
                            isOpening
                            || isDeleting
                          }
                        >
                          {isOpening ? (
                            <LoaderCircle
                              size={18}
                              className={
                                "ask-jm8-spin"
                              }
                            />
                          ) : (
                            <MessageCircleQuestion
                              size={18}
                            />
                          )}

                          <span>
                            <strong>
                              {item.question}
                            </strong>

                            <small>
                              {formatGeneratedAt(
                                item.createdAt
                              )}
                              {" · "}
                              {item.status
                                === "ANSWERED"
                                  ? (
                                      "Grounded "
                                      + "answer"
                                    )
                                  : (
                                      "Limited "
                                      + "context"
                                    )}
                            </small>
                          </span>
                        </button>

                        <button
                          type="button"
                          className={
                            "ask-jm8-"
                            + "history-delete"
                          }
                          onClick={() => {
                            void deleteHistoryItem(
                              item
                            );
                          }}
                          disabled={
                            isOpening
                            || isDeleting
                          }
                          aria-label={
                            (
                              "Delete saved "
                              + `question: ${
                                item.question
                              }`
                            )
                          }
                        >
                          {isDeleting ? (
                            <LoaderCircle
                              size={16}
                              className={
                                "ask-jm8-spin"
                              }
                            />
                          ) : (
                            <Trash2 size={16} />
                          )}
                        </button>
                      </article>
                    );
                  }
                )}
              </div>
            )}

          {history.length > 0 && (
            <div
              className={
                "ask-jm8-history-"
                + "pagination"
              }
            >
              {historyPageError ? (
                <div
                  className={
                    "ask-jm8-history-"
                    + "page-error"
                  }
                  role="alert"
                >
                  <AlertCircle
                    size={16}
                  />

                  <span>
                    {historyPageError}
                  </span>

                  {nextHistoryCursor && (
                    <button
                      type="button"
                      onClick={() => {
                        void loadMoreHistory();
                      }}
                      disabled={
                        isHistoryPageLoading
                      }
                    >
                      <RefreshCw
                        size={15}
                        className={
                          isHistoryPageLoading
                            ? "ask-jm8-spin"
                            : undefined
                        }
                      />
                      Retry
                    </button>
                  )}
                </div>
              ) : nextHistoryCursor ? (
                <button
                  type="button"
                  className={
                    "ask-jm8-history-"
                    + "load-more"
                  }
                  onClick={() => {
                    void loadMoreHistory();
                  }}
                  disabled={
                    isHistoryPageLoading
                  }
                >
                  {isHistoryPageLoading ? (
                    <LoaderCircle
                      size={17}
                      className={
                        "ask-jm8-spin"
                      }
                    />
                  ) : (
                    <History size={17} />
                  )}

                  {isHistoryPageLoading
                    ? "Loading older questions…"
                    : "Load older questions"}
                </button>
              ) : (
                <span
                  className={
                    "ask-jm8-history-end"
                  }
                >
                  All saved questions loaded
                </span>
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
                  disabled={
                    isUsageExhausted
                  }
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
