"""Authenticated API boundary for Phase 3C3 account deletion coordination."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

import boto3
from botocore.exceptions import ClientError

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
stepfunctions = boto3.client("stepfunctions")


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


def configured_account_deletion_workflow_arn() -> str:
    """Return only the exact stage-scoped account-deletion workflow ARN."""
    workflow_arn = os.environ.get("ACCOUNT_DELETION_WORKFLOW_ARN", "").strip()
    app_name = os.environ.get("APP_NAME", "").strip()
    stage = os.environ.get("STAGE", "").strip()
    region = os.environ.get("AWS_REGION", "").strip()
    account_id = os.environ.get("EXPECTED_AWS_ACCOUNT_ID", "").strip()
    if (
        not app_name
        or not stage
        or not re.fullmatch(r"[a-z]{2}(?:-gov)?-[a-z]+-\d", region)
        or not re.fullmatch(r"[0-9]{12}", account_id)
    ):
        return ""
    expected_name = re.escape(f"{app_name}-{stage}-account-deletion-workflow")
    pattern = (
        rf"arn:aws:states:{re.escape(region)}:{account_id}:stateMachine:"
        + expected_name
    )
    return workflow_arn if re.fullmatch(pattern, workflow_arn) else ""


def start_account_deletion_workflow(
    *,
    request_id: str,
    subject: str,
    client: Any = None,
) -> dict[str, str]:
    workflow_arn = configured_account_deletion_workflow_arn()
    if not workflow_arn:
        raise AccountDeletionApiError(
            503,
            "AccountDeletionUnavailable",
            "Account deletion is temporarily unavailable.",
            True,
        )
    resource = client or stepfunctions
    try:
        response = resource.start_execution(
            stateMachineArn=workflow_arn,
            name=request_id,
            input=json.dumps(
                {"requestId": request_id},
                separators=(",", ":"),
            ),
        )
        return {"executionArn": response["executionArn"]}
    except ClientError as error:
        code = (error.response.get("Error") or {}).get("Code")
        if code != "ExecutionAlreadyExists":
            raise
        token = None
        while True:
            arguments = {"stateMachineArn": workflow_arn}
            if token:
                arguments["nextToken"] = token
            page = resource.list_executions(**arguments)
            for execution in page.get("executions", []):
                if execution.get("name") == request_id:
                    return {"executionArn": execution["executionArn"]}
            token = page.get("nextToken")
            if not token:
                raise


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

    starter = workflow_starter
    if starter is None and configured_account_deletion_workflow_arn():
        starter = start_account_deletion_workflow
    if starter is None:
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
            execution = starter(
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
