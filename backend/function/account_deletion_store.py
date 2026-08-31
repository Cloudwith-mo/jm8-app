"""Privacy-safe DynamoDB coordination for account deletion requests."""

from __future__ import annotations

import hmac
import json
from typing import Any, Callable

import boto3
from botocore.exceptions import ClientError

from account_deletion_contract import (
    ACTIVE_DELETION_STATUSES,
    isoformat_utc,
    new_deletion_request_id,
    request_token_digest,
    safe_failure_code,
    subject_digest,
    utc_now,
)
from storage import serialize_attribute_map, table


AUDIT_PK_PREFIX = "ACCOUNT_DELETION#"
SUBJECT_PK_PREFIX = "ACCOUNT_DELETION_SUBJECT#"
AUDIT_SK = "REQUEST"
ACTIVE_SK = "ACTIVE"
IDEMPOTENCY_SK_PREFIX = "REQUEST_TOKEN#"
ACTIVE_LOCK_TTL_SECONDS = 7 * 24 * 60 * 60
IDEMPOTENCY_TTL_SECONDS = 7 * 24 * 60 * 60
FAILED_AUDIT_TTL_SECONDS = 30 * 24 * 60 * 60
# The table already uses this attribute as its configured DynamoDB TTL key.
TTL_ATTRIBUTE = "accountExportTtlEpoch"

dynamodb_client = boto3.client("dynamodb")


class ActiveDeletionExists(RuntimeError):
    pass


class DeletionStoreUnavailable(RuntimeError):
    pass


def audit_pk(request_id: str) -> str:
    return f"{AUDIT_PK_PREFIX}{request_id}"


def subject_pk(digest: str) -> str:
    return f"{SUBJECT_PK_PREFIX}{digest}"


def idempotency_sk(token_digest: str) -> str:
    return f"{IDEMPOTENCY_SK_PREFIX}{token_digest}"


def _log_store_failure(exc: ClientError, operation: str) -> None:
    response = exc.response or {}
    metadata = response.get("ResponseMetadata") or {}
    error = response.get("Error") or {}
    print(json.dumps({
        "event": "AccountDeletionStoreFailure",
        "operation": operation,
        "errorCode": error.get("Code"),
        "httpStatus": metadata.get("HTTPStatusCode"),
        "awsRequestId": metadata.get("RequestId"),
    }))


def _get_item(
    pk: str,
    sk: str,
    *,
    table_resource: Any = None,
) -> dict[str, Any] | None:
    resource = table_resource or table
    try:
        response = resource.get_item(
            Key={"PK": pk, "SK": sk},
            ConsistentRead=True,
        )
    except ClientError as exc:
        _log_store_failure(exc, "GetItem")
        raise DeletionStoreUnavailable() from None
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def _request_by_digest(
    digest: str,
    request_id: str,
    *,
    table_resource: Any = None,
) -> dict[str, Any] | None:
    item = _get_item(
        audit_pk(request_id),
        AUDIT_SK,
        table_resource=table_resource,
    )
    stored_digest = item.get("subjectDigest") if item else None
    if not isinstance(stored_digest, str):
        return None
    if not hmac.compare_digest(stored_digest, digest):
        return None
    return item


def get_deletion_request(
    subject: str,
    request_id: str,
    *,
    table_resource: Any = None,
) -> dict[str, Any] | None:
    return _request_by_digest(
        subject_digest(subject),
        request_id,
        table_resource=table_resource,
    )


def has_active_deletion(
    subject: str,
    *,
    table_resource: Any = None,
) -> bool:
    digest = subject_digest(subject)
    lock = _get_item(
        subject_pk(digest),
        ACTIVE_SK,
        table_resource=table_resource,
    )
    if not lock or lock.get("status") not in ACTIVE_DELETION_STATUSES:
        return False
    return int(lock.get(TTL_ATTRIBUTE, 0)) > int(utc_now().timestamp())


def create_or_replay_deletion(
    *,
    subject: str,
    request_token: object,
    transact_writer: Callable[..., Any] | None = None,
    table_resource: Any = None,
) -> tuple[dict[str, Any], bool]:
    resource = table_resource or table
    digest = subject_digest(subject)
    token_digest = request_token_digest(request_token)
    coordination_pk = subject_pk(digest)
    replay_key = idempotency_sk(token_digest)

    replay = _get_item(
        coordination_pk,
        replay_key,
        table_resource=resource,
    )
    if replay and replay.get("requestId"):
        request = _request_by_digest(
            digest,
            str(replay["requestId"]),
            table_resource=resource,
        )
        if request:
            return request, True

    now = utc_now()
    epoch = int(now.timestamp())
    active = _get_item(
        coordination_pk,
        ACTIVE_SK,
        table_resource=resource,
    )
    if (
        active
        and active.get("status") in ACTIVE_DELETION_STATUSES
        and int(active.get(TTL_ATTRIBUTE, 0)) > epoch
    ):
        raise ActiveDeletionExists()

    request_id = new_deletion_request_id()
    requested_at = isoformat_utc(now)
    audit = {
        "PK": audit_pk(request_id),
        "SK": AUDIT_SK,
        "requestId": request_id,
        "subjectDigest": digest,
        "status": "REQUESTED",
        "requestedAt": requested_at,
    }
    lock = {
        "PK": coordination_pk,
        "SK": ACTIVE_SK,
        "requestId": request_id,
        "status": "REQUESTED",
        "requestedAt": requested_at,
        TTL_ATTRIBUTE: epoch + ACTIVE_LOCK_TTL_SECONDS,
    }
    idempotency = {
        "PK": coordination_pk,
        "SK": replay_key,
        "requestId": request_id,
        TTL_ATTRIBUTE: epoch + IDEMPOTENCY_TTL_SECONDS,
    }
    writer = (
        transact_writer
        if transact_writer is not None
        else dynamodb_client.transact_write_items
    )
    try:
        writer(TransactItems=[
            {"Put": {
                "TableName": resource.name,
                "Item": serialize_attribute_map(audit),
                "ConditionExpression": "attribute_not_exists(PK)",
            }},
            {"Put": {
                "TableName": resource.name,
                "Item": serialize_attribute_map(lock),
                "ConditionExpression": (
                    "attribute_not_exists(PK) OR #ttl <= :now OR "
                    "(#status <> :requested AND #status <> :in_progress)"
                ),
                "ExpressionAttributeNames": {
                    "#ttl": TTL_ATTRIBUTE,
                    "#status": "status",
                },
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":now": epoch,
                    ":requested": "REQUESTED",
                    ":in_progress": "IN_PROGRESS",
                }),
            }},
            {"Put": {
                "TableName": resource.name,
                "Item": serialize_attribute_map(idempotency),
                "ConditionExpression": "attribute_not_exists(PK) OR #ttl <= :now",
                "ExpressionAttributeNames": {"#ttl": TTL_ATTRIBUTE},
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":now": epoch,
                }),
            }},
        ])
    except ClientError as exc:
        code = (exc.response.get("Error") or {}).get("Code")
        if code in {
            "TransactionCanceledException",
            "ConditionalCheckFailedException",
        }:
            replay = _get_item(
                coordination_pk,
                replay_key,
                table_resource=resource,
            )
            if replay and replay.get("requestId"):
                request = _request_by_digest(
                    digest,
                    str(replay["requestId"]),
                    table_resource=resource,
                )
                if request:
                    return request, True
            raise ActiveDeletionExists() from None
        _log_store_failure(exc, "TransactWriteItems")
        raise DeletionStoreUnavailable() from None
    return audit, False


def record_workflow_execution(
    *,
    subject: str,
    request_id: str,
    execution_arn: str,
    table_resource: Any = None,
) -> dict[str, Any]:
    resource = table_resource or table
    digest = subject_digest(subject)
    try:
        response = resource.update_item(
            Key={"PK": audit_pk(request_id), "SK": AUDIT_SK},
            UpdateExpression="SET workflowExecutionArn = :arn",
            ConditionExpression=(
                "subjectDigest = :subject AND #status = :requested"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":arn": execution_arn,
                ":subject": digest,
                ":requested": "REQUESTED",
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        _log_store_failure(exc, "UpdateItem")
        raise DeletionStoreUnavailable() from None
    return response["Attributes"]


def fail_deletion_request(
    *,
    subject: str,
    request_id: str,
    failure_code: str,
    retryable: bool,
    table_resource: Any = None,
) -> dict[str, Any]:
    resource = table_resource or table
    digest = subject_digest(subject)
    normalized_failure_code = safe_failure_code(failure_code)
    now = utc_now()
    failed_at = isoformat_utc(now)
    ttl = int(now.timestamp()) + FAILED_AUDIT_TTL_SECONDS
    try:
        response = resource.update_item(
            Key={"PK": audit_pk(request_id), "SK": AUDIT_SK},
            UpdateExpression=(
                "SET #status = :failed, failedAt = :failed_at, "
                "failureCode = :failure_code, retryable = :retryable, "
                "#ttl = :ttl"
            ),
            ConditionExpression="subjectDigest = :subject",
            ExpressionAttributeNames={
                "#status": "status",
                "#ttl": TTL_ATTRIBUTE,
            },
            ExpressionAttributeValues={
                ":failed": "FAILED",
                ":failed_at": failed_at,
                ":failure_code": normalized_failure_code,
                ":retryable": retryable,
                ":ttl": ttl,
                ":subject": digest,
            },
            ReturnValues="ALL_NEW",
        )
        resource.update_item(
            Key={"PK": subject_pk(digest), "SK": ACTIVE_SK},
            UpdateExpression="SET #status = :failed, #ttl = :ttl",
            ConditionExpression="requestId = :request_id",
            ExpressionAttributeNames={
                "#status": "status",
                "#ttl": TTL_ATTRIBUTE,
            },
            ExpressionAttributeValues={
                ":failed": "FAILED",
                ":ttl": ttl,
                ":request_id": request_id,
            },
        )
    except ClientError as exc:
        _log_store_failure(exc, "UpdateItem")
        raise DeletionStoreUnavailable() from None
    return response["Attributes"]
