import { AlertTriangle, RefreshCw, Trash2 } from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ApiRequestError,
  getAccountDeletionRequest,
  requestAccountDeletion,
} from "../../api/client";
import { AUTH_SESSION_EXPIRED_EVENT } from "../../auth/cognito";
import type {
  AccountDeletionRequest,
} from "../../types/accountDeletion";
import {
  hasObservedDestructiveDeletion,
  shouldTreatTrackingAuthLossAsProcessed,
} from "../../types/accountDeletion";
import "./AccountDeletionDangerZone.css";

const CONFIRMATION = "DELETE_MY_ACCOUNT" as const;
const MAX_AUTOMATIC_POLLS = 12;
const MAX_POLL_DELAY_MS = 15_000;

type AccountDeletionDangerZoneProps = {
  isPanelOpen: boolean;
  focusOnOpen: boolean;
  onFocusHandled: () => void;
  onReauthenticate: () => void;
  onDeletionProcessed: () => void;
};

function pollDelay(attempt: number): number {
  return Math.min(2_000 * (2 ** Math.min(attempt, 3)), MAX_POLL_DELAY_MS);
}

function safeError(error: unknown): {
  message: string;
  reauthenticate?: boolean;
  notFound?: boolean;
} {
  if (!(error instanceof ApiRequestError)) {
    return { message: "Account deletion is temporarily unavailable." };
  }
  switch (error.code) {
    case "InvalidConfirmation":
    case "DeletionConfirmationRequired":
      return { message: "Type the confirmation phrase exactly as shown." };
    case "InvalidRequestToken":
      return { message: "The secure request could not be validated. Try again." };
    case "RecentAuthenticationRequired":
      return {
        message: "Sign in again before deleting your account.",
        reauthenticate: true,
      };
    case "ActiveDeletionExists":
      return { message: "An account deletion request is already active." };
    case "DeletionRequestNotFound":
      return {
        message: "That account deletion request is unavailable.",
        notFound: true,
      };
    case "AccountDeletionUnavailable":
      return { message: "Account deletion is temporarily unavailable." };
    case "Unauthorized":
      return { message: "Your secure session is no longer available." };
    default:
      return { message: "The account deletion request could not be processed." };
  }
}

function statusPresentation(request: AccountDeletionRequest | null): {
  label: string;
  message: string;
} {
  if (!request) {
    return {
      label: "NOT REQUESTED",
      message: "No account deletion request has been submitted.",
    };
  }
  switch (request.status) {
    case "REQUESTED":
      return {
        label: "QUEUED",
        message: "Your deletion request is queued.",
      };
    case "IN_PROGRESS":
      return request.destructiveStartedAt
        ? {
            label: "DESTRUCTIVE PROCESSING",
            message: "Deletion is in progress. Account writes are blocked. Support intervention may be needed if completion is delayed.",
          }
        : {
            label: "QUIESCING",
            message: "JM8 is stopping active account work before deletion begins.",
          };
    case "COMPLETED":
      return {
        label: "COMPLETED",
        message: "Your account deletion request was completed.",
      };
    case "FAILED":
      return request.failure?.retryable
        ? {
            label: "RETRYABLE FAILURE",
            message: "Deletion could not finish yet. Refresh the status or contact support.",
          }
        : {
            label: "FAILED",
            message: "Deletion stopped because it could not safely continue. Contact support.",
          };
  }
}

export default function AccountDeletionDangerZone({
  isPanelOpen,
  focusOnOpen,
  onFocusHandled,
  onReauthenticate,
  onDeletionProcessed,
}: AccountDeletionDangerZoneProps) {
  const [isConfirming, setIsConfirming] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [request, setRequest] = useState<AccountDeletionRequest | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [requiresReauthentication, setRequiresReauthentication] = useState(false);
  const [pollingStopped, setPollingStopped] = useState(false);
  const sectionRef = useRef<HTMLElement | null>(null);
  const confirmationRef = useRef<HTMLInputElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const submittingRef = useRef(false);
  const pollInFlightRef = useRef(false);
  const destructiveRef = useRef(false);
  const processedRef = useRef(false);

  const abortTracking = useCallback(() => {
    generationRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    pollInFlightRef.current = false;
  }, []);

  const finishProcessed = useCallback(() => {
    if (processedRef.current) return;
    processedRef.current = true;
    abortTracking();
    onDeletionProcessed();
  }, [abortTracking, onDeletionProcessed]);

  const trackRequest = useCallback((requestId: string, immediate = false) => {
    abortTracking();
    const generation = generationRef.current;
    let attempts = 0;
    setPollingStopped(false);

    const run = async (): Promise<void> => {
      if (generation !== generationRef.current || pollInFlightRef.current) return;
      if (attempts >= MAX_AUTOMATIC_POLLS) {
        setPollingStopped(true);
        return;
      }
      attempts += 1;
      pollInFlightRef.current = true;
      const controller = new AbortController();
      controllerRef.current = controller;
      try {
        const response = await getAccountDeletionRequest(
          requestId,
          controller.signal,
        );
        if (controller.signal.aborted || generation !== generationRef.current) return;
        const next = response.deletionRequest;
        destructiveRef.current = hasObservedDestructiveDeletion(next);
        setRequest(next);
        setErrorMessage("");
        if (next.status === "COMPLETED") {
          finishProcessed();
          return;
        }
        if (next.status === "FAILED") {
          setPollingStopped(true);
          return;
        }
      } catch (error) {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        const safe = safeError(error);
        if (shouldTreatTrackingAuthLossAsProcessed(
          error instanceof ApiRequestError ? error.status : undefined,
          destructiveRef.current,
        )) {
          finishProcessed();
          return;
        }
        setErrorMessage(safe.message);
        if (safe.notFound) {
          setPollingStopped(true);
          return;
        }
      } finally {
        if (generation === generationRef.current) {
          pollInFlightRef.current = false;
        }
      }

      if (generation !== generationRef.current) return;
      if (attempts >= MAX_AUTOMATIC_POLLS) {
        setPollingStopped(true);
        return;
      }
      timerRef.current = window.setTimeout(() => {
        void run();
      }, pollDelay(attempts));
    };

    timerRef.current = window.setTimeout(() => {
      void run();
    }, immediate ? 0 : pollDelay(0));
  }, [abortTracking, finishProcessed]);

  useEffect(() => {
    destructiveRef.current = hasObservedDestructiveDeletion(request);
  }, [request]);

  useEffect(() => {
    if (!isPanelOpen) {
      abortTracking();
      if (request && !["COMPLETED", "FAILED"].includes(request.status)) {
        setPollingStopped(true);
      }
      return;
    }
    if (focusOnOpen) {
      window.requestAnimationFrame(() => {
        sectionRef.current?.focus();
        onFocusHandled();
      });
    }
  }, [abortTracking, focusOnOpen, isPanelOpen, onFocusHandled, request]);

  useEffect(() => abortTracking, [abortTracking]);

  useEffect(() => {
    const handleAuthenticationLoss = () => {
      if (destructiveRef.current) finishProcessed();
    };
    window.addEventListener(
      AUTH_SESSION_EXPIRED_EVENT,
      handleAuthenticationLoss,
    );
    return () => window.removeEventListener(
      AUTH_SESSION_EXPIRED_EVENT,
      handleAuthenticationLoss,
    );
  }, [finishProcessed]);

  useEffect(() => {
    if (!isConfirming) return;
    const frame = window.requestAnimationFrame(() => confirmationRef.current?.focus());
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submittingRef.current) {
        setConfirmation("");
        setIsConfirming(false);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [isConfirming]);

  function cancelConfirmation(): void {
    if (submittingRef.current) return;
    setConfirmation("");
    setErrorMessage("");
    setRequiresReauthentication(false);
    setIsConfirming(false);
  }

  async function submitDeletion(): Promise<void> {
    if (confirmation !== CONFIRMATION || submittingRef.current) return;
    submittingRef.current = true;
    setIsSubmitting(true);
    setErrorMessage("");
    setRequiresReauthentication(false);
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const requestToken = crypto.randomUUID();
      const response = await requestAccountDeletion(
        CONFIRMATION,
        requestToken,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      const accepted = response.deletionRequest;
      setRequest(accepted);
      destructiveRef.current = hasObservedDestructiveDeletion(accepted);
      setConfirmation("");
      setIsConfirming(false);
      if (accepted.status === "COMPLETED") {
        finishProcessed();
      } else if (accepted.status === "FAILED") {
        setPollingStopped(true);
      } else {
        trackRequest(accepted.requestId);
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      const safe = safeError(error);
      setErrorMessage(safe.message);
      setRequiresReauthentication(safe.reauthenticate === true);
    } finally {
      submittingRef.current = false;
      setIsSubmitting(false);
    }
  }

  function reauthenticate(): void {
    abortTracking();
    setConfirmation("");
    setIsConfirming(false);
    setErrorMessage("");
    setRequiresReauthentication(false);
    onReauthenticate();
  }

  const presentation = statusPresentation(request);
  const hasTrackedRequest = request !== null;

  return (
    <section
      className="account-deletion-danger-zone"
      aria-labelledby="account-deletion-heading"
      ref={sectionRef}
      tabIndex={-1}
    >
      <header>
        <span className="account-deletion-icon" aria-hidden="true">
          <AlertTriangle size={18} />
        </span>
        <div>
          <p>Danger zone</p>
          <h2 id="account-deletion-heading">Delete account</h2>
        </div>
      </header>

      <p className="account-deletion-warning">
        Account deletion is permanent and cannot be undone.
      </p>
      <p className="account-deletion-copy">
        JM8 will delete journal entries, uploaded images, OCR and analysis data,
        Ask JM8 history, generated exports, subscription access, and your sign-in identity.
        Minimal non-content audit evidence may remain for restore protection,
        security, and operational obligations.
      </p>

      {hasTrackedRequest ? (
        <div className="account-deletion-status" role="status" aria-live="polite">
          <strong>{presentation.label}</strong>
          <span>{errorMessage || presentation.message}</span>
        </div>
      ) : null}

      {errorMessage && !hasTrackedRequest ? (
        <p className="account-deletion-error" role="alert">{errorMessage}</p>
      ) : null}

      {requiresReauthentication ? (
        <button
          type="button"
          className="account-deletion-secondary"
          onClick={reauthenticate}
        >
          Re-authenticate
        </button>
      ) : null}

      {!isConfirming && !hasTrackedRequest && !requiresReauthentication ? (
        <button
          type="button"
          className="account-deletion-open"
          onClick={() => {
            setErrorMessage("");
            setIsConfirming(true);
          }}
        >
          <Trash2 size={15} /> Delete account
        </button>
      ) : null}

      {isConfirming ? (
        <div
          className="account-deletion-confirmation"
          role="dialog"
          aria-modal="true"
          aria-labelledby="account-deletion-confirm-title"
        >
          <h3 id="account-deletion-confirm-title">Confirm permanent deletion</h3>
          <p>
            This operation cannot be undone. Type <strong>{CONFIRMATION}</strong> exactly.
          </p>
          <label htmlFor="account-deletion-confirmation-input">
            Confirmation phrase
          </label>
          <input
            id="account-deletion-confirmation-input"
            ref={confirmationRef}
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            disabled={isSubmitting}
          />
          <div className="account-deletion-confirm-actions">
            <button
              type="button"
              className="account-deletion-secondary"
              onClick={cancelConfirmation}
              disabled={isSubmitting}
            >
              Cancel
            </button>
            <button
              type="button"
              className="account-deletion-submit"
              onClick={() => void submitDeletion()}
              disabled={confirmation !== CONFIRMATION || isSubmitting}
            >
              {isSubmitting ? <RefreshCw size={15} className="account-deletion-spin" /> : <Trash2 size={15} />}
              {isSubmitting ? "Submitting…" : "Permanently delete account"}
            </button>
          </div>
        </div>
      ) : null}

      {hasTrackedRequest && pollingStopped && request.status !== "COMPLETED" ? (
        <button
          type="button"
          className="account-deletion-secondary"
          onClick={() => trackRequest(request.requestId, true)}
          disabled={pollInFlightRef.current}
        >
          <RefreshCw size={15} /> Refresh status
        </button>
      ) : null}
    </section>
  );
}
