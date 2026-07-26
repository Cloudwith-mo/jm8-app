import {
  CalendarClock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import type {
  AccountEntitlement,
  AccountSubscriptionStatus,
} from "../../types/accountEntitlement";
import "./AccountPlanCard.css";


type AccountPlanCardProps = {
  entitlement: AccountEntitlement | null;
  isLoading: boolean;
  errorMessage: string;
  onRetry: () => void;
};


function formatDate(
  value: string | null
): string | null {
  if (!value) {
    return null;
  }

  const parsed = new Date(value);

  if (
    Number.isNaN(
      parsed.getTime()
    )
  ) {
    return null;
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


function getStatusLabel(
  status: AccountSubscriptionStatus
): string {
  switch (status) {
    case "TRIALING":
      return "Trial active";

    case "ACTIVE":
      return "Active";

    case "PAST_DUE":
      return "Payment attention needed";

    case "CANCELED":
      return "Canceled";

    case "EXPIRED":
      return "Expired";

    default:
      return "Free";
  }
}


function getAccessMessage(
  entitlement: AccountEntitlement
): string {
  const accessEnd = formatDate(
    entitlement.access.endsAt
  );

  if (
    entitlement.subscription
      .cancelAtPeriodEnd
    && accessEnd
  ) {
    return (
      "Pro access remains available "
      + `through ${accessEnd}.`
    );
  }

  if (
    entitlement.subscription.status
      === "PAST_DUE"
    && accessEnd
  ) {
    return (
      "Pro access is temporarily "
      + `available through ${accessEnd}.`
    );
  }

  if (
    entitlement.access.isPro
    && accessEnd
  ) {
    return (
      "Pro access is active through "
      + `${accessEnd}.`
    );
  }

  if (entitlement.access.isPro) {
    return (
      "Your expanded Pro allowances "
      + "are active."
    );
  }

  return (
    "Your private archive includes "
    + "the Free monthly AI allowance."
  );
}


export default function AccountPlanCard({
  entitlement,
  isLoading,
  errorMessage,
  onRetry,
}: AccountPlanCardProps) {
  if (
    isLoading
    && !entitlement
  ) {
    return (
      <section
        className={
          "account-plan-card "
          + "account-plan-loading"
        }
        aria-live="polite"
      >
        <ShieldCheck size={21} />

        <div>
          <strong>
            Checking your plan
          </strong>

          <span>
            Loading account access and
            monthly allowances.
          </span>
        </div>
      </section>
    );
  }

  if (
    errorMessage
    && !entitlement
  ) {
    return (
      <section
        className={
          "account-plan-card "
          + "account-plan-error"
        }
        role="alert"
      >
        <ShieldCheck size={21} />

        <div>
          <strong>
            Plan details unavailable
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

  if (!entitlement) {
    return null;
  }

  const isPro =
    entitlement.access.isPro;

  const statusLabel = getStatusLabel(
    entitlement.subscription.status
  );

  const accessEnd = formatDate(
    entitlement.access.endsAt
  );

  return (
    <section
      className={
        `account-plan-card ${
          isPro
            ? "account-plan-pro"
            : "account-plan-free"
        }`
      }
      aria-label="JM8 plan and access"
    >
      <header className="account-plan-header">
        <div className="account-plan-heading">
          <span className="account-plan-icon">
            <ShieldCheck size={19} />
          </span>

          <div>
            <p>Plan and access</p>

            <h2>
              {entitlement.plan.label}
              {" "}
              plan
            </h2>
          </div>
        </div>

        <div className="account-plan-actions">
          <span
            className={
              `account-plan-badge ${
                isPro ? "pro" : "free"
              }`
            }
          >
            {entitlement.plan.id}
          </span>

          <button
            type="button"
            onClick={onRetry}
            disabled={isLoading}
            aria-label={
              "Refresh account plan"
            }
          >
            <RefreshCw
              size={15}
              className={
                isLoading
                  ? "account-plan-spin"
                  : undefined
              }
            />
          </button>
        </div>
      </header>

      {errorMessage && (
        <p
          className="account-plan-inline-error"
          role="alert"
        >
          {errorMessage}
        </p>
      )}

      <div className="account-plan-summary">
        <div>
          <strong>{statusLabel}</strong>

          <span>
            {getAccessMessage(
              entitlement
            )}
          </span>
        </div>

        {accessEnd && (
          <span className="account-plan-date">
            <CalendarClock size={15} />

            Access date
            {" "}
            {accessEnd}
          </span>
        )}
      </div>

      <div className="account-plan-limits">
        <article>
          <span>Ask JM8</span>

          <strong>
            {
              entitlement.limits
                .askJm8.monthly
            }
          </strong>

          <small>
            questions per month
          </small>
        </article>

        <article>
          <span>Entry analysis</span>

          <strong>
            {
              entitlement.limits
                .entryAnalysis.monthly
            }
          </strong>

          <small>
            analyses per month
          </small>
        </article>
      </div>

      {!isPro && (
        <div className="account-upgrade-panel">
          <span className="account-upgrade-icon">
            <Sparkles size={18} />
          </span>

          <div>
            <strong>
              More space for reflection
            </strong>

            <p>
              Pro expands Ask JM8 and
              entry-analysis allowances.
            </p>
          </div>

          <button
            type="button"
            disabled
            title={
              "Billing will be enabled "
              + "in Upgrade 16D."
            }
          >
            Upgrade coming soon
          </button>
        </div>
      )}

      {isPro && (
        <div className="account-pro-confirmation">
          <Sparkles size={17} />

          <span>
            Pro intelligence allowances
            are enabled for this account.
          </span>
        </div>
      )}
    </section>
  );
}
