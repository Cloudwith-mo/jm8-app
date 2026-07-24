import type {
  ReactNode,
} from "react";
import {
  CalendarClock,
  Gauge,
  MessageCircleQuestion,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import type {
  UsageOperation,
  UsageSnapshot,
} from "../../types/usage";
import "./UsageMeter.css";


type UsageMeterProps = {
  usage: UsageSnapshot | null;
  isLoading: boolean;
  errorMessage: string;
  onRetry: () => void;
};


function formatResetDate(
  value: string
) {
  const parsed = new Date(value);

  if (
    Number.isNaN(
      parsed.getTime()
    )
  ) {
    return "next month";
  }

  return parsed.toLocaleDateString(
    [],
    {
      month: "short",
      day: "numeric",
      year: "numeric",
    }
  );
}


function getUsageState(
  operation: UsageOperation
) {
  if (!operation.allowed) {
    return "exhausted";
  }

  const warningThreshold = Math.max(
    1,
    Math.ceil(
      operation.limit * 0.2
    )
  );

  if (
    operation.remaining
    <= warningThreshold
  ) {
    return "warning";
  }

  return "available";
}


function getPercentage(
  operation: UsageOperation
) {
  if (operation.limit <= 0) {
    return 100;
  }

  const consumed =
    operation.used
    + operation.reserved;

  return Math.min(
    100,
    Math.round(
      (
        consumed
        / operation.limit
      ) * 100
    )
  );
}


function UsageOperationCard({
  title,
  description,
  operation,
  icon,
}: {
  title: string;
  description: string;
  operation: UsageOperation;
  icon: ReactNode;
}) {
  const state = getUsageState(
    operation
  );

  const percentage = getPercentage(
    operation
  );

  return (
    <article
      className={
        `usage-operation-card ${state}`
      }
    >
      <header>
        <div className="usage-operation-icon">
          {icon}
        </div>

        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>

        <strong>
          {operation.remaining}
          <span>
            {" "}
            left
          </span>
        </strong>
      </header>

      <div
        className="usage-progress-track"
        aria-label={
          `${title}: ${operation.remaining} `
          + `of ${operation.limit} remaining`
        }
      >
        <span
          style={{
            width: `${percentage}%`,
          }}
        />
      </div>

      <footer>
        <span>
          {operation.used}
          {" "}
          completed
        </span>

        <span>
          {operation.reserved}
          {" "}
          in progress
        </span>

        <span>
          {operation.failed}
          {" "}
          failed
        </span>

        <span>
          Limit
          {" "}
          {operation.limit}
        </span>
      </footer>

      {state === "warning" && (
        <p className="usage-state-message">
          Your monthly allowance is
          running low.
        </p>
      )}

      {state === "exhausted" && (
        <p className="usage-state-message">
          Monthly allowance reached.
          This action is unavailable
          until the reset.
        </p>
      )}
    </article>
  );
}


export default function UsageMeter({
  usage,
  isLoading,
  errorMessage,
  onRetry,
}: UsageMeterProps) {
  if (
    isLoading
    && !usage
  ) {
    return (
      <section
        className="usage-meter loading"
        aria-live="polite"
      >
        <Gauge size={20} />

        <div>
          <strong>
            Loading monthly usage
          </strong>

          <span>
            Checking your current AI
            allowance.
          </span>
        </div>
      </section>
    );
  }

  if (
    errorMessage
    && !usage
  ) {
    return (
      <section
        className="usage-meter error"
        role="alert"
      >
        <Gauge size={20} />

        <div>
          <strong>
            Usage unavailable
          </strong>

          <span>{errorMessage}</span>
        </div>

        <button
          type="button"
          onClick={onRetry}
          disabled={isLoading}
        >
          <RefreshCw size={15} />
          Retry
        </button>
      </section>
    );
  }

  if (!usage) {
    return null;
  }

  return (
    <section
      className="usage-meter"
      aria-label="Monthly AI usage"
    >
      <header className="usage-meter-header">
        <div>
          <p>
            <Gauge size={16} />
            Monthly AI usage
          </p>

          <h2>
            Your JM8 allowance
          </h2>
        </div>

        <div className="usage-meter-meta">
          <span className="usage-plan-badge">
            {usage.plan.label}
          </span>

          <span>
            <CalendarClock size={15} />
            Resets
            {" "}
            {formatResetDate(
              usage.period.resetsAt
            )}
          </span>

          <button
            type="button"
            onClick={onRetry}
            disabled={isLoading}
            aria-label={
              "Refresh monthly usage"
            }
          >
            <RefreshCw
              size={15}
              className={
                isLoading
                  ? "usage-spin"
                  : undefined
              }
            />
          </button>
        </div>
      </header>

      {errorMessage && (
        <p
          className="usage-inline-error"
          role="alert"
        >
          {errorMessage}
        </p>
      )}

      <div className="usage-operation-grid">
        <UsageOperationCard
          title="Ask JM8"
          description={
            "Questions across your "
            + "journal history"
          }
          operation={
            usage.operations.askJm8
          }
          icon={
            <MessageCircleQuestion
              size={19}
            />
          }
        />

        <UsageOperationCard
          title="Entry analysis"
          description={
            "Mood, themes, summary, "
            + "and next steps"
          }
          operation={
            usage.operations
              .entryAnalysis
          }
          icon={
            <Sparkles size={19} />
          }
        />
      </div>
    </section>
  );
}
