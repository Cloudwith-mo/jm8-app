"""Stop known workflows and wait out already-running Lambda invocations."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Mapping

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

from storage import user_pk


MAX_IN_FLIGHT_SECONDS = 15 * 60 + 30


class DeletionQuiescenceError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _running_executions(client: Any, workflow_arn: str) -> list[dict[str, Any]]:
    executions: list[dict[str, Any]] = []
    token = None
    while True:
        arguments = {
            "stateMachineArn": workflow_arn,
            "statusFilter": "RUNNING",
        }
        if token:
            arguments["nextToken"] = token
        page = client.list_executions(**arguments)
        executions.extend(page.get("executions", []))
        token = page.get("nextToken")
        if not token:
            return executions


def _belongs_to_subject(client: Any, execution_arn: str, subject: str) -> bool:
    execution = client.describe_execution(executionArn=execution_arn)
    try:
        payload = json.loads(execution.get("input") or "{}")
    except json.JSONDecodeError:
        raise DeletionQuiescenceError(
            "WorkflowInputInvariant", retryable=False
        ) from None
    return (
        execution.get("status") in {None, "RUNNING"}
        and isinstance(payload, dict)
        and payload.get("userId") == subject
    )


def quiesce_user_work(
    *,
    subject: str,
    deletion_started_at: str,
    table_resource: Any,
    stepfunctions_client: Any,
    workflow_arns: Mapping[str, str],
    now: float | None = None,
) -> dict[str, Any]:
    required_workflows = {"ocr", "reanalysis", "export"}
    if (
        not required_workflows.issubset(workflow_arns)
        or any(not workflow_arns[name] for name in required_workflows)
    ):
        raise DeletionQuiescenceError(
            "QuiescenceConfigurationInvalid", retryable=False
        )
    try:
        query_arguments: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(user_pk(subject)),
            "ProjectionExpression": (
                "PK, SK, #status, entityType, workflowExecutionArn, executionArn"
            ),
            "ExpressionAttributeNames": {"#status": "status"},
            "ConsistentRead": True,
        }
        stored_execution_arns: set[str] = set()
        while True:
            page = table_resource.query(**query_arguments)
            for item in page.get("Items", []):
                for field in ("workflowExecutionArn", "executionArn"):
                    value = item.get(field)
                    if isinstance(value, str) and value.startswith("arn:aws:states:"):
                        stored_execution_arns.add(value)
            last_key = page.get("LastEvaluatedKey")
            if not last_key:
                break
            query_arguments["ExclusiveStartKey"] = last_key
        stopped = 0
        inspected: set[str] = set()
        for execution_arn in stored_execution_arns:
            inspected.add(execution_arn)
            if _belongs_to_subject(
                stepfunctions_client, execution_arn, subject
            ):
                stepfunctions_client.stop_execution(
                    executionArn=execution_arn,
                    error="AccountDeletionInProgress",
                    cause="User mutations are quiescing for account deletion.",
                )
                stopped += 1
        for workflow_arn in workflow_arns.values():
            for execution in _running_executions(
                stepfunctions_client, workflow_arn
            ):
                execution_arn = execution.get("executionArn")
                if not isinstance(execution_arn, str):
                    raise DeletionQuiescenceError(
                        "WorkflowExecutionInvariant", retryable=False
                    )
                if execution_arn in inspected:
                    continue
                inspected.add(execution_arn)
                if _belongs_to_subject(
                    stepfunctions_client, execution_arn, subject
                ):
                    stepfunctions_client.stop_execution(
                        executionArn=execution_arn,
                        error="AccountDeletionInProgress",
                        cause="User mutations are quiescing for account deletion.",
                    )
                    stopped += 1
    except DeletionQuiescenceError:
        raise
    except (ClientError, BotoCoreError):
        raise DeletionQuiescenceError(
            "QuiescenceInspectionUnavailable", retryable=True
        ) from None

    try:
        started = datetime.fromisoformat(
            deletion_started_at.replace("Z", "+00:00")
        ).timestamp()
    except (AttributeError, ValueError):
        raise DeletionQuiescenceError(
            "DeletionTimestampInvariant", retryable=False
        ) from None
    current = time.time() if now is None else now
    remaining = max(0, MAX_IN_FLIGHT_SECONDS - int(current - started))
    return {
        "quiesced": stopped == 0 and remaining == 0,
        "waitSeconds": min(max(remaining, 5), 60) if remaining else 5,
    }
