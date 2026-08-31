"""Authenticated API boundary for Phase 3C3 account deletion coordination."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from account_deletion_contract import (
    is_valid_deletion_request_id,
    request_token_digest,
    serialize_public_deletion_request,
    validate_confirmation,
)
from account_deletion_store import (
    ActiveDeletionExists,
    DeletionStoreUnavailable,
    create_or_replay_deletion,
    fail_deletion_request,
    get_deletion_request,
    record_workflow_execution,
)


DEFAULT_RECENT_AUTH_SECONDS = 5 * 60
MAX_RECENT_AUTH_SECONDS = 60 * 60
WorkflowStarter = Callable[..., Any]


class AccountDeletionApiError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.payload = {
            "error": code,
            "message": message,
            "retryable": retryable,
        }


def recent_auth_window_seconds() -> int:
    raw_value = os.environ.get("ACCOUNT_DELETION_RECENT_AUTH_SECONDS", "")
    try:
        configured = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_RECENT_AUTH_SECONDS
    if configured <= 0 or configured > MAX_RECENT_AUTH_SECONDS:
        return DEFAULT_RECENT_AUTH_SECONDS
    return configured


def _require_recent_authentication(claims: dict[str, Any]) -> None:
    auth_time = claims.get("auth_time")
    if isinstance(auth_time, bool):
        auth_time = None
    try:
        authenticated_at = int(auth_time)
    except (TypeError, ValueError):
        authenticated_at = 0
    now = int(time.time())
    age = now - authenticated_at
    if authenticated_at <= 0 or age < -60 or age > recent_auth_window_seconds():
        raise AccountDeletionApiError(
            401,
            "RecentAuthenticationRequired",
            "Please sign in again before requesting account deletion.",
        )


def _not_found() -> AccountDeletionApiError:
    return AccountDeletionApiError(
        404,
        "DeletionRequestNotFound",
        "The account deletion request was not found.",
    )


def _execution_arn(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("executionArn")
    if not isinstance(value, str) or not value.startswith("arn:aws:states:"):
        raise ValueError("workflow starter returned an invalid execution ARN")
    return value


def create_account_deletion_request(
    subject: str,
    claims: dict[str, Any],
    body: dict[str, Any],
    *,
    workflow_starter: WorkflowStarter | None = None,
) -> tuple[int, dict[str, Any]]:
    claim_subject = claims.get("sub")
    if not isinstance(claim_subject, str) or claim_subject.strip() != subject:
        raise AccountDeletionApiError(
            401,
            "Unauthorized",
            "Authentication is required.",
        )
    _require_recent_authentication(claims)
    try:
        validate_confirmation(body.get("confirmation"))
    except ValueError:
        raise AccountDeletionApiError(
            400,
            "DeletionConfirmationRequired",
            "Explicit account deletion confirmation is required.",
        ) from None
    try:
        request_token_digest(body.get("requestToken"))
    except ValueError:
        raise AccountDeletionApiError(
            400,
            "InvalidRequestToken",
            "The request token is invalid.",
        ) from None

    if workflow_starter is None:
        raise AccountDeletionApiError(
            503,
            "AccountDeletionUnavailable",
            "Account deletion is temporarily unavailable.",
            True,
        )

    try:
        request, replayed = create_or_replay_deletion(
            subject=subject,
            request_token=body.get("requestToken"),
        )
    except ActiveDeletionExists:
        raise AccountDeletionApiError(
            409,
            "ActiveDeletionExists",
            "An account deletion request is already active.",
        ) from None
    except DeletionStoreUnavailable:
        raise AccountDeletionApiError(
            503,
            "AccountDeletionUnavailable",
            "Account deletion is temporarily unavailable.",
            True,
        ) from None

    if not replayed:
        try:
            execution = workflow_starter(
                request_id=request["requestId"],
                subject=subject,
            )
            request = record_workflow_execution(
                subject=subject,
                request_id=request["requestId"],
                execution_arn=_execution_arn(execution),
            )
        except Exception:
            try:
                fail_deletion_request(
                    subject=subject,
                    request_id=request["requestId"],
                    failure_code="DeletionWorkflowStartFailed",
                    retryable=True,
                )
            except DeletionStoreUnavailable:
                pass
            print(json.dumps({
                "event": "AccountDeletionWorkflowStartFailed",
                "failureCode": "DeletionWorkflowStartFailed",
                "retryable": True,
            }))
            raise AccountDeletionApiError(
                503,
                "AccountDeletionUnavailable",
                "The deletion workflow could not be started.",
                True,
            ) from None

    print(json.dumps({
        "event": "AccountDeletionRequested",
        "status": request["status"],
        "replayed": replayed,
    }))
    status_code = 200 if replayed else 202
    return status_code, {
        "deletionRequest": serialize_public_deletion_request(request),
        "replayed": replayed,
    }


def get_account_deletion_request(
    subject: str,
    request_id: object,
) -> tuple[int, dict[str, Any]]:
    if not is_valid_deletion_request_id(request_id):
        raise _not_found()
    try:
        request = get_deletion_request(subject, str(request_id))
    except DeletionStoreUnavailable:
        raise AccountDeletionApiError(
            503,
            "AccountDeletionUnavailable",
            "The deletion request status is temporarily unavailable.",
            True,
        ) from None
    if request is None:
        raise _not_found()
    return 200, {
        "deletionRequest": serialize_public_deletion_request(request),
    }
