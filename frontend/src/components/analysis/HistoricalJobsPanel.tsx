import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  History,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import {
  ApiRequestError,
  getHistoricalReanalysisInventory,
  listHistoricalReanalysisJobs,
  retryHistoricalReanalysisJob,
  startHistoricalReanalysisJob,
} from "../../api/client";
import type {
  HistoricalReanalysisJob,
  HistoricalReanalysisJobStatus,
  HistoricalReanalysisJobStatusFilter,
} from "../../types/reanalysis";
import type {
  ToastKind,
} from "../ui/ToastStack";

type HistoricalJobsPanelProps = {
  onNotify: (
    kind: ToastKind,
    title: string,
    message?: string
  ) => void;
};

type LoadOptions = {
  silent?: boolean;
};

const STATUS_FILTERS: Array<{
  label: string;
  value: HistoricalReanalysisJobStatusFilter;
}> = [
  {
    label: "All",
    value: "ALL",
  },
  {
    label: "Queued",
    value: "QUEUED",
  },
  {
    label: "Running",
    value: "RUNNING",
  },
  {
    label: "Completed",
    value: "COMPLETED",
  },
  {
    label: "Failed",
    value: "FAILED",
  },
];

const ACTIVE_STATUSES = new Set<
HistoricalReanalysisJobStatus
>([
  "QUEUED",
  "RUNNING",
]);

function numberValue(
  value: number | undefined
) {
  return Math.max(
    Number(value || 0),
    0
  );
}

function formatDate(
  value: string | undefined
) {
  if (!value) return "Not recorded";

  const parsed = new Date(value);

  if (
    Number.isNaN(parsed.getTime())
  ) {
    return "Not recorded";
  }

  return parsed.toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function getProgress(
  job: HistoricalReanalysisJob
) {
  if (
    job.status === "COMPLETED"
  ) {
    return 100;
  }

  const processed = numberValue(
    job.processedEntries
  );

  const total = Math.max(
    numberValue(job.eligibleEntries),
    processed +
      numberValue(
        job.remainingEntries
      )
  );

  if (total < 1) return 0;

  return Math.min(
    Math.round(
      (processed / total) * 100
    ),
    100
  );
}

function getErrorMessage(
  error: unknown,
  fallback: string
) {
  if (
    error instanceof ApiRequestError
  ) {
    if (
      error.code ===
      "NoEligibleEntries"
    ) {
      return (
        "Every eligible journal entry " +
        "has already been analyzed."
      );
    }

    if (
      error.code ===
      "HistoricalReanalysisJobActive"
    ) {
      return (
        "Another historical analysis " +
        "job is already active."
      );
    }

    if (
      error.code ===
      "HistoricalReanalysisJobNotRetryable"
    ) {
      return (
        "Only failed historical jobs " +
        "can be retried."
      );
    }

    return error.message;
  }

  return error instanceof Error
    ? error.message
    : fallback;
}

function StatusIcon({
  status,
}: {
  status: HistoricalReanalysisJobStatus;
}) {
  if (status === "COMPLETED") {
    return (
      <CheckCircle2 size={19} />
    );
  }

  if (status === "FAILED") {
    return (
      <AlertTriangle size={19} />
    );
  }

  if (status === "RUNNING") {
    return (
      <RefreshCw
        className="historical-job-spin"
        size={19}
      />
    );
  }

  return <Clock size={19} />;
}

export default function HistoricalJobsPanel({
  onNotify,
}: HistoricalJobsPanelProps) {
  const notifyRef = useRef(onNotify);
  const controllerRef = useRef<AbortController | null>(null);
  const requestRef = useRef(0);

  const [
    jobs,
    setJobs,
  ] = useState<
  HistoricalReanalysisJob[]
  >([]);

  const [
    statusFilter,
    setStatusFilter,
  ] = useState<
  HistoricalReanalysisJobStatusFilter
  >("ALL");

  const [
    eligibleEntries,
    setEligibleEntries,
  ] = useState<number | null>(null);

  const [
    isLoading,
    setIsLoading,
  ] = useState(true);

  const [
    actionKey,
    setActionKey,
  ] = useState<string | null>(null);

  const [
    errorMessage,
    setErrorMessage,
  ] = useState("");

  const [
    lastUpdatedAt,
    setLastUpdatedAt,
  ] = useState<Date | null>(null);

  const [
    focusedJobId,
    setFocusedJobId,
  ] = useState<string | null>(null);

  useEffect(() => {
    notifyRef.current = onNotify;
  }, [onNotify]);

  const loadJobs = useCallback(
    async (
      options: LoadOptions = {}
    ) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      const request = ++requestRef.current;
      if (!options.silent) {
        setIsLoading(true);
      }

      try {
        const [
          jobsResult,
          inventoryResult,
        ] = await Promise.all([
          listHistoricalReanalysisJobs(
            "ALL",
            50,
            controller.signal,
          ),
          getHistoricalReanalysisInventory(controller.signal),
        ]);

        if (controller.signal.aborted || request !== requestRef.current) return;

        setJobs(jobsResult.jobs);

        setEligibleEntries(
          numberValue(
            inventoryResult.inventory
              .eligibleEntries
          )
        );

        setErrorMessage("");
        setLastUpdatedAt(new Date());
      } catch (error) {
        if (controller.signal.aborted || request !== requestRef.current) return;
        const message = getErrorMessage(
          error,
          "Historical jobs could not be loaded."
        );

        setErrorMessage(message);

        if (!options.silent) {
          notifyRef.current(
            "error",
            "Jobs unavailable",
            message
          );
        }
      } finally {
        if (!controller.signal.aborted && request === requestRef.current) {
          setIsLoading(false);
        }
      }
    },
    []
  );

  useEffect(() => {
    void loadJobs();
    return () => controllerRef.current?.abort();
  }, [loadJobs]);

  const hasActiveJobs = useMemo(
    () =>
      jobs.some((job) =>
        ACTIVE_STATUSES.has(
          job.status
        )
      ),
    [jobs]
  );

  useEffect(() => {
    if (!hasActiveJobs) return;

    const intervalId =
      window.setInterval(() => {
        void loadJobs({
          silent: true,
        });
      }, 3000);

    return () => {
      window.clearInterval(
        intervalId
      );
    };
  }, [
    hasActiveJobs,
    loadJobs,
  ]);

  const visibleJobs = useMemo(
    () =>
      statusFilter === "ALL"
        ? jobs
        : jobs.filter(
            (job) =>
              job.status ===
              statusFilter
          ),
    [
      jobs,
      statusFilter,
    ]
  );

  const summary = useMemo(
    () => ({
      total: jobs.length,
      active: jobs.filter(
        (job) =>
          ACTIVE_STATUSES.has(
            job.status
          )
      ).length,
      completed: jobs.filter(
        (job) =>
          job.status ===
          "COMPLETED"
      ).length,
      failed: jobs.filter(
        (job) =>
          job.status === "FAILED"
      ).length,
    }),
    [jobs]
  );

  async function handleStart() {
    if (
      eligibleEntries !== null &&
      eligibleEntries < 1
    ) {
      notifyRef.current(
        "info",
        "Archive is current",
        "No journal entries currently need historical analysis."
      );

      return;
    }

    setActionKey("start");

    notifyRef.current(
      "loading",
      "Starting historical analysis",
      "JM8 is preparing eligible journal entries."
    );

    try {
      const result =
        await startHistoricalReanalysisJob(
          10
        );

      setStatusFilter("ALL");

      setFocusedJobId(
        result.job.jobId
      );

      await loadJobs();

      notifyRef.current(
        "success",
        "Historical analysis started",
        "JM8 will update this screen automatically."
      );
    } catch (error) {
      const message = getErrorMessage(
        error,
        "Historical analysis could not be started."
      );

      setErrorMessage(message);

      notifyRef.current(
        "error",
        "Start failed",
        message
      );

      await loadJobs({
        silent: true,
      });
    } finally {
      setActionKey(null);
    }
  }

  async function handleRetry(
    job: HistoricalReanalysisJob
  ) {
    setActionKey(job.jobId);

    notifyRef.current(
      "loading",
      "Retrying failed run",
      "JM8 is recalculating the currently eligible entries."
    );

    try {
      const result =
        await retryHistoricalReanalysisJob(
          job.jobId,
          10
        );

      setStatusFilter("ALL");

      setFocusedJobId(
        result.job.jobId
      );

      await loadJobs();

      notifyRef.current(
        "success",
        "Retry started",
        "A new immutable historical analysis run was created."
      );
    } catch (error) {
      const message = getErrorMessage(
        error,
        "The failed job could not be retried."
      );

      setErrorMessage(message);

      notifyRef.current(
        "error",
        "Retry unavailable",
        message
      );

      await loadJobs({
        silent: true,
      });
    } finally {
      setActionKey(null);
    }
  }

  function highlightOriginal(
    sourceJobId: string
  ) {
    setStatusFilter("ALL");
    setFocusedJobId(
      sourceJobId
    );

    notifyRef.current(
      "info",
      "Original run highlighted",
      "The earlier source run is marked below."
    );
  }

  return (
    <section className="historical-jobs-page">
      <header className="historical-jobs-header">
        <div>
          <p className="historical-jobs-kicker">
            <History size={17} />
            JM8 intelligence operations
          </p>

          <h1>
            Historical Analysis Jobs
          </h1>

          <p>
            Review archive-wide analysis
            progress, failures, and retries.
          </p>
        </div>

        <div className="historical-jobs-header-actions">
          <button
            className="historical-jobs-refresh"
            onClick={() =>
              void loadJobs()
            }
            disabled={
              isLoading ||
              actionKey !== null
            }
          >
            <RefreshCw
              className={
                isLoading
                  ? "historical-job-spin"
                  : ""
              }
              size={18}
            />
            Refresh
          </button>

          <button
            className="historical-jobs-start"
            onClick={() =>
              void handleStart()
            }
            disabled={
              actionKey !== null ||
              eligibleEntries === null ||
              eligibleEntries < 1
            }
          >
            <Play size={18} />
            {eligibleEntries === 0
              ? "Archive current"
              : actionKey === "start"
                ? "Starting..."
                : "Analyze history"}
          </button>
        </div>
      </header>

      <div className="historical-jobs-overview">
        <article>
          <span>Total runs</span>
          <strong>
            {summary.total}
          </strong>
          <small>
            Immutable job records
          </small>
        </article>

        <article>
          <span>Active</span>
          <strong>
            {summary.active}
          </strong>
          <small>
            Queued or running
          </small>
        </article>

        <article>
          <span>Completed</span>
          <strong>
            {summary.completed}
          </strong>
          <small>
            Finished workflows
          </small>
        </article>

        <article>
          <span>Failed</span>
          <strong>
            {summary.failed}
          </strong>
          <small>
            Eligible for retry
          </small>
        </article>

        <article className="eligible-summary-card">
          <ShieldCheck size={19} />
          <span>
            Eligible entries
          </span>
          <strong>
            {eligibleEntries ?? "—"}
          </strong>
          <small>
            Need historical analysis
          </small>
        </article>
      </div>

      <div className="historical-jobs-toolbar">
        <div
          className="historical-job-filters"
          role="tablist"
          aria-label="Historical job status filters"
        >
          {STATUS_FILTERS.map(
            (filter) => (
              <button
                key={filter.value}
                className={
                  statusFilter ===
                  filter.value
                    ? "active"
                    : ""
                }
                onClick={() =>
                  setStatusFilter(
                    filter.value
                  )
                }
                aria-pressed={
                  statusFilter ===
                  filter.value
                }
              >
                {filter.label}
              </button>
            )
          )}
        </div>

        <div className="historical-jobs-sync">
          {hasActiveJobs && (
            <span>
              <RefreshCw
                className="historical-job-spin"
                size={14}
              />
              Auto-refreshing
            </span>
          )}

          <small>
            {lastUpdatedAt
              ? `Updated ${lastUpdatedAt.toLocaleTimeString([], {
                  hour: "numeric",
                  minute: "2-digit",
                  second: "2-digit",
                })}`
              : "Waiting for first refresh"}
          </small>
        </div>
      </div>

      {errorMessage && (
        <div
          className="historical-jobs-alert"
          role="alert"
        >
          <AlertTriangle size={18} />
          <span>{errorMessage}</span>
        </div>
      )}

      {isLoading &&
      jobs.length === 0 ? (
        <div className="historical-jobs-empty">
          <RefreshCw
            className="historical-job-spin"
            size={24}
          />
          <h2>Loading job history</h2>
          <p>
            JM8 is retrieving your
            historical analysis records.
          </p>
        </div>
      ) : visibleJobs.length === 0 ? (
        <div className="historical-jobs-empty">
          <History size={26} />
          <h2>
            No matching jobs
          </h2>
          <p>
            Choose another status filter
            or start a historical analysis
            when entries become eligible.
          </p>
        </div>
      ) : (
        <div className="historical-jobs-list">
          {visibleJobs.map(
            (job) => {
              const progress =
                getProgress(job);

              const isRetry =
                Boolean(
                  job.retryOfJobId
                );

              const isFocused =
                focusedJobId ===
                job.jobId;

              const isRetrying =
                actionKey ===
                job.jobId;

              return (
                <article
                  key={job.jobId}
                  className={[
                    "historical-job-card",
                    `status-${job.status.toLowerCase()}`,
                    isFocused
                      ? "highlighted"
                      : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                >
                  <header className="historical-job-card-header">
                    <div className="historical-job-title">
                      <div
                        className={`historical-job-icon ${job.status.toLowerCase()}`}
                      >
                        <StatusIcon
                          status={
                            job.status
                          }
                        />
                      </div>

                      <div>
                        <p>
                          {isRetry
                            ? "Historical retry"
                            : "Historical analysis"}
                        </p>

                        <h2>
                          {job.status ===
                          "RUNNING"
                            ? "Analysis in progress"
                            : job.status ===
                                "QUEUED"
                              ? "Waiting to begin"
                              : job.status ===
                                  "COMPLETED"
                                ? "Analysis completed"
                                : "Analysis failed"}
                        </h2>

                        <small>
                          Created{" "}
                          {formatDate(
                            job.createdAt
                          )}
                        </small>
                      </div>
                    </div>

                    <span
                      className={`historical-job-status ${job.status.toLowerCase()}`}
                    >
                      {job.status}
                    </span>
                  </header>

                  {isRetry && (
                    <div className="historical-job-lineage">
                      <RotateCcw
                        size={16}
                      />

                      <span>
                        Retry of an earlier
                        failed run
                      </span>

                      <button
                        onClick={() =>
                          highlightOriginal(
                            job.retryOfJobId as string
                          )
                        }
                      >
                        Highlight original
                      </button>
                    </div>
                  )}

                  {isFocused && (
                    <div className="historical-job-focus-label">
                      Linked run
                    </div>
                  )}

                  <section className="historical-job-progress">
                    <div>
                      <span>
                        Overall progress
                      </span>

                      <strong>
                        {progress}%
                      </strong>
                    </div>

                    <div
                      className="historical-job-progress-track"
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={
                        progress
                      }
                    >
                      <span
                        style={{
                          width: `${progress}%`,
                        }}
                      />
                    </div>
                  </section>

                  <dl className="historical-job-metrics">
                    <div>
                      <dt>Eligible</dt>
                      <dd>
                        {numberValue(
                          job.eligibleEntries
                        )}
                      </dd>
                    </div>

                    <div>
                      <dt>Processed</dt>
                      <dd>
                        {numberValue(
                          job.processedEntries
                        )}
                      </dd>
                    </div>

                    <div>
                      <dt>Completed</dt>
                      <dd>
                        {numberValue(
                          job.completedEntries
                        )}
                      </dd>
                    </div>

                    <div>
                      <dt>Failed</dt>
                      <dd>
                        {numberValue(
                          job.failedEntries
                        )}
                      </dd>
                    </div>

                    <div>
                      <dt>Skipped</dt>
                      <dd>
                        {numberValue(
                          job.skippedEntries
                        )}
                      </dd>
                    </div>

                    <div>
                      <dt>Remaining</dt>
                      <dd>
                        {numberValue(
                          job.remainingEntries
                        )}
                      </dd>
                    </div>
                  </dl>

                  <dl className="historical-job-timeline">
                    <div>
                      <dt>Started</dt>
                      <dd>
                        {formatDate(
                          job.startedAt
                        )}
                      </dd>
                    </div>

                    <div>
                      <dt>Updated</dt>
                      <dd>
                        {formatDate(
                          job.updatedAt
                        )}
                      </dd>
                    </div>

                    <div>
                      <dt>Finished</dt>
                      <dd>
                        {formatDate(
                          job.completedAt
                        )}
                      </dd>
                    </div>
                  </dl>

                  {job.status ===
                    "FAILED" && (
                    <div className="historical-job-failure">
                      <AlertTriangle
                        size={18}
                      />

                      <div>
                        <strong>
                          {job.failureCode ||
                            "WorkflowFailure"}
                        </strong>

                        <p>
                          {job.failureMessage ||
                            "The historical analysis workflow could not complete."}
                        </p>
                      </div>
                    </div>
                  )}

                  {job.status ===
                    "FAILED" && (
                    <footer className="historical-job-actions">
                      <button
                        onClick={() =>
                          void handleRetry(
                            job
                          )
                        }
                        disabled={
                          actionKey !==
                          null
                        }
                      >
                        <RotateCcw
                          className={
                            isRetrying
                              ? "historical-job-spin"
                              : ""
                          }
                          size={17}
                        />

                        {isRetrying
                          ? "Retrying..."
                          : "Retry failed run"}
                      </button>
                    </footer>
                  )}
                </article>
              );
            }
          )}
        </div>
      )}
    </section>
  );
}
