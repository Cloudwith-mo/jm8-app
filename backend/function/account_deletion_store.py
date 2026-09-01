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
BILLING_RECOVERY_SK = "BILLING_RECOVERY"
SUBJECT_RECOVERY_SK = "SUBJECT_RECOVERY"
# The table already uses this attribute as its configured DynamoDB TTL key.
TTL_ATTRIBUTE = "accountExportTtlEpoch"

dynamodb_client = boto3.client("dynamodb")


class ActiveDeletionExists(RuntimeError):
    pass


class DeletionStoreUnavailable(RuntimeError):
    pass


class DeletionStoreInvariant(RuntimeError):
    code = "DeletionCoordinationInvariant"
    retryable = False


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


def get_deletion_audit(
    request_id: str,
    *,
    table_resource: Any = None,
) -> dict[str, Any] | None:
    """Read an audit by opaque ID for the trusted deletion worker only."""
    if not isinstance(request_id, str):
        return None
    return _get_item(
        audit_pk(request_id),
        AUDIT_SK,
        table_resource=table_resource,
    )


def get_deletion_subject(
    request_id: str,
    *,
    table_resource: Any = None,
) -> str | None:
    """Resolve the short-lived internal subject recovery record."""
    item = _get_item(
        audit_pk(request_id),
        SUBJECT_RECOVERY_SK,
        table_resource=table_resource,
    )
    subject = item.get("cognitoSubject") if item else None
    digest = item.get("subjectDigest") if item else None
    if (
        item is None
        or item.get("requestId") != request_id
        or not isinstance(subject, str)
        or not isinstance(digest, str)
        or not hmac.compare_digest(subject_digest(subject), digest)
    ):
        return None
    return subject


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
    if TTL_ATTRIBUTE not in lock:
        return True
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
        and (
            TTL_ATTRIBUTE not in active
            or int(active.get(TTL_ATTRIBUTE, 0)) > epoch
        )
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
    subject_recovery = {
        "PK": audit_pk(request_id),
        "SK": SUBJECT_RECOVERY_SK,
        "requestId": request_id,
        "subjectDigest": digest,
        "cognitoSubject": subject.strip(),
        TTL_ATTRIBUTE: epoch + ACTIVE_LOCK_TTL_SECONDS,
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
            {"Put": {
                "TableName": resource.name,
                "Item": serialize_attribute_map(subject_recovery),
                "ConditionExpression": "attribute_not_exists(PK)",
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
    phase: str = "START",
    destructive_started: bool = False,
    table_resource: Any = None,
    transact_writer: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    resource = table_resource or table
    digest = subject_digest(subject)
    normalized_failure_code = safe_failure_code(failure_code)
    now = utc_now()
    failed_at = isoformat_utc(now)
    ttl = int(now.timestamp()) + FAILED_AUDIT_TTL_SECONDS
    writer = transact_writer or dynamodb_client.transact_write_items
    audit_update = {
        "TableName": resource.name,
        "Key": serialize_attribute_map({
            "PK": audit_pk(request_id), "SK": AUDIT_SK,
        }),
        "ConditionExpression": (
            "subjectDigest = :subject AND "
            + (
                "attribute_exists(destructiveStartedAt)"
                if destructive_started
                else "attribute_not_exists(destructiveStartedAt)"
            )
        ),
        "ExpressionAttributeNames": {
            "#status": "status", "#ttl": TTL_ATTRIBUTE,
        },
    }
    audit_values = {
        ":failed": "FAILED",
        ":failed_at": failed_at,
        ":failure_code": normalized_failure_code,
        ":retryable": retryable,
        ":phase": safe_failure_code(phase),
        ":subject": digest,
    }
    if destructive_started:
        audit_update["UpdateExpression"] = (
            "SET #status = :failed, failedAt = :failed_at, "
            "failureCode = :failure_code, retryable = :retryable, "
            "failurePhase = :phase REMOVE #ttl"
        )
    else:
        audit_update["UpdateExpression"] = (
            "SET #status = :failed, failedAt = :failed_at, "
            "failureCode = :failure_code, retryable = :retryable, "
            "failurePhase = :phase, #ttl = :ttl"
        )
        audit_values[":ttl"] = ttl
    audit_update["ExpressionAttributeValues"] = serialize_attribute_map(
        audit_values
    )

    if destructive_started:
        coordination_changes = [
            {"Update": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": subject_pk(digest), "SK": ACTIVE_SK,
                }),
                "UpdateExpression": "SET #status = :active REMOVE #ttl",
                "ConditionExpression": "requestId = :request_id",
                "ExpressionAttributeNames": {
                    "#status": "status", "#ttl": TTL_ATTRIBUTE,
                },
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":active": "IN_PROGRESS", ":request_id": request_id,
                }),
            }},
            {"Update": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": subject_pk(digest), "SK": BILLING_RECOVERY_SK,
                }),
                "UpdateExpression": "REMOVE #ttl",
                "ConditionExpression": "requestId = :request_id",
                "ExpressionAttributeNames": {"#ttl": TTL_ATTRIBUTE},
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":request_id": request_id,
                }),
            }},
            {"Update": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": audit_pk(request_id), "SK": SUBJECT_RECOVERY_SK,
                }),
                "UpdateExpression": "REMOVE #ttl",
                "ConditionExpression": "subjectDigest = :subject",
                "ExpressionAttributeNames": {"#ttl": TTL_ATTRIBUTE},
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":subject": digest,
                }),
            }},
        ]
    else:
        coordination_changes = [
            {"Delete": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": subject_pk(digest), "SK": ACTIVE_SK,
                }),
                "ConditionExpression": (
                    "attribute_not_exists(PK) OR requestId = :request_id"
                ),
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":request_id": request_id,
                }),
            }},
            {"Delete": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": subject_pk(digest), "SK": BILLING_RECOVERY_SK,
                }),
                "ConditionExpression": (
                    "attribute_not_exists(PK) OR requestId = :request_id"
                ),
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":request_id": request_id,
                }),
            }},
            {"Delete": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": audit_pk(request_id), "SK": SUBJECT_RECOVERY_SK,
                }),
                "ConditionExpression": (
                    "attribute_not_exists(PK) OR subjectDigest = :subject"
                ),
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":subject": digest,
                }),
            }},
        ]
    try:
        writer(TransactItems=[{"Update": audit_update}, *coordination_changes])
    except ClientError as exc:
        _log_store_failure(exc, "TransactWriteItems")
        raise DeletionStoreUnavailable() from None
    failed = _request_by_digest(
        digest, request_id, table_resource=resource,
    )
    if failed is None:
        raise DeletionStoreUnavailable()
    return failed


def start_deletion_request(
    *,
    subject: str,
    request_id: str,
    table_resource: Any = None,
    transact_writer: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    resource = table_resource or table
    digest = subject_digest(subject)
    existing = _request_by_digest(digest, request_id, table_resource=resource)
    if existing is None:
        raise DeletionStoreUnavailable()
    if existing.get("status") in {"IN_PROGRESS", "COMPLETED"}:
        return existing
    if existing.get("destructiveStartedAt"):
        writer = transact_writer or dynamodb_client.transact_write_items
        try:
            writer(TransactItems=[
                {"Update": {
                    "TableName": resource.name,
                    "Key": serialize_attribute_map({
                        "PK": audit_pk(request_id), "SK": AUDIT_SK,
                    }),
                    "UpdateExpression": (
                        "SET #status = :in_progress REMOVE failureCode, "
                        "failurePhase, failedAt, retryable, #ttl"
                    ),
                    "ConditionExpression": (
                        "subjectDigest = :subject AND "
                        "attribute_exists(destructiveStartedAt)"
                    ),
                    "ExpressionAttributeNames": {
                        "#status": "status", "#ttl": TTL_ATTRIBUTE,
                    },
                    "ExpressionAttributeValues": serialize_attribute_map({
                        ":in_progress": "IN_PROGRESS", ":subject": digest,
                    }),
                }},
                {"Update": {
                    "TableName": resource.name,
                    "Key": serialize_attribute_map({
                        "PK": subject_pk(digest), "SK": ACTIVE_SK,
                    }),
                    "UpdateExpression": "SET #status = :active REMOVE #ttl",
                    "ConditionExpression": "requestId = :request_id",
                    "ExpressionAttributeNames": {
                        "#status": "status", "#ttl": TTL_ATTRIBUTE,
                    },
                    "ExpressionAttributeValues": serialize_attribute_map({
                        ":active": "IN_PROGRESS", ":request_id": request_id,
                    }),
                }},
                {"Update": {
                    "TableName": resource.name,
                    "Key": serialize_attribute_map({
                        "PK": subject_pk(digest), "SK": BILLING_RECOVERY_SK,
                    }),
                    "UpdateExpression": "REMOVE #ttl",
                    "ConditionExpression": "requestId = :request_id",
                    "ExpressionAttributeNames": {"#ttl": TTL_ATTRIBUTE},
                    "ExpressionAttributeValues": serialize_attribute_map({
                        ":request_id": request_id,
                    }),
                }},
                {"Update": {
                    "TableName": resource.name,
                    "Key": serialize_attribute_map({
                        "PK": audit_pk(request_id), "SK": SUBJECT_RECOVERY_SK,
                    }),
                    "UpdateExpression": "REMOVE #ttl",
                    "ConditionExpression": "subjectDigest = :subject",
                    "ExpressionAttributeNames": {"#ttl": TTL_ATTRIBUTE},
                    "ExpressionAttributeValues": serialize_attribute_map({
                        ":subject": digest,
                    }),
                }},
            ])
        except ClientError as exc:
            _log_store_failure(exc, "TransactWriteItems")
            raise DeletionStoreUnavailable() from None
        resumed = _request_by_digest(
            digest, request_id, table_resource=resource,
        )
        if resumed is None:
            raise DeletionStoreUnavailable()
        return resumed
    now = utc_now()
    started_at = isoformat_utc(now)
    ttl = int(now.timestamp()) + ACTIVE_LOCK_TTL_SECONDS
    try:
        response = resource.update_item(
            Key={"PK": audit_pk(request_id), "SK": AUDIT_SK},
            UpdateExpression=(
                "SET #status = :in_progress, startedAt = if_not_exists("
                "startedAt, :started_at) REMOVE failureCode, failurePhase, "
                "failedAt, retryable, #ttl"
            ),
            ConditionExpression="subjectDigest = :subject",
            ExpressionAttributeNames={"#status": "status", "#ttl": TTL_ATTRIBUTE},
            ExpressionAttributeValues={
                ":in_progress": "IN_PROGRESS",
                ":started_at": started_at,
                ":subject": digest,
            },
            ReturnValues="ALL_NEW",
        )
        resource.update_item(
            Key={"PK": subject_pk(digest), "SK": ACTIVE_SK},
            UpdateExpression="SET #status = :in_progress, #ttl = :ttl",
            ConditionExpression="requestId = :request_id",
            ExpressionAttributeNames={"#status": "status", "#ttl": TTL_ATTRIBUTE},
            ExpressionAttributeValues={
                ":in_progress": "IN_PROGRESS",
                ":ttl": ttl,
                ":request_id": request_id,
            },
        )
    except ClientError as exc:
        _log_store_failure(exc, "UpdateItem")
        raise DeletionStoreUnavailable() from None
    return response["Attributes"]


def mark_deletion_checkpoint(
    *,
    subject: str,
    request_id: str,
    timestamp_field: str,
    table_resource: Any = None,
) -> dict[str, Any]:
    if timestamp_field not in {"destructiveStartedAt", "verifiedAt"}:
        raise ValueError("invalid deletion checkpoint")
    resource = table_resource or table
    digest = subject_digest(subject)
    try:
        response = resource.update_item(
            Key={"PK": audit_pk(request_id), "SK": AUDIT_SK},
            UpdateExpression="SET #field = if_not_exists(#field, :now)",
            ConditionExpression="subjectDigest = :subject AND #status = :active",
            ExpressionAttributeNames={
                "#field": timestamp_field,
                "#status": "status",
            },
            ExpressionAttributeValues={
                ":now": isoformat_utc(utc_now()),
                ":subject": digest,
                ":active": "IN_PROGRESS",
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        _log_store_failure(exc, "UpdateItem")
        raise DeletionStoreUnavailable() from None
    return response["Attributes"]


def begin_destructive_deletion(
    *,
    subject: str,
    request_id: str,
    table_resource: Any = None,
    transact_writer: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    resource = table_resource or table
    digest = subject_digest(subject)
    writer = transact_writer or dynamodb_client.transact_write_items
    try:
        writer(TransactItems=[
            {"Update": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": audit_pk(request_id), "SK": AUDIT_SK,
                }),
                "UpdateExpression": (
                    "SET destructiveStartedAt = if_not_exists("
                    "destructiveStartedAt, :now) REMOVE #ttl"
                ),
                "ConditionExpression": (
                    "subjectDigest = :subject AND #status = :active"
                ),
                "ExpressionAttributeNames": {
                    "#status": "status", "#ttl": TTL_ATTRIBUTE,
                },
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":now": isoformat_utc(utc_now()),
                    ":subject": digest,
                    ":active": "IN_PROGRESS",
                }),
            }},
            {"Update": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": subject_pk(digest), "SK": ACTIVE_SK,
                }),
                "UpdateExpression": "SET #status = :active REMOVE #ttl",
                "ConditionExpression": "requestId = :request_id",
                "ExpressionAttributeNames": {
                    "#status": "status", "#ttl": TTL_ATTRIBUTE,
                },
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":active": "IN_PROGRESS", ":request_id": request_id,
                }),
            }},
            {"Update": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": subject_pk(digest), "SK": BILLING_RECOVERY_SK,
                }),
                "UpdateExpression": "REMOVE #ttl",
                "ConditionExpression": "requestId = :request_id",
                "ExpressionAttributeNames": {"#ttl": TTL_ATTRIBUTE},
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":request_id": request_id,
                }),
            }},
            {"Update": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": audit_pk(request_id), "SK": SUBJECT_RECOVERY_SK,
                }),
                "UpdateExpression": "REMOVE #ttl",
                "ConditionExpression": "subjectDigest = :subject",
                "ExpressionAttributeNames": {"#ttl": TTL_ATTRIBUTE},
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":subject": digest,
                }),
            }},
        ])
    except ClientError as exc:
        _log_store_failure(exc, "TransactWriteItems")
        raise DeletionStoreUnavailable() from None
    started = _request_by_digest(
        digest, request_id, table_resource=resource,
    )
    if started is None:
        raise DeletionStoreUnavailable()
    return started


def write_billing_recovery(
    *,
    subject: str,
    request_id: str,
    customer_id: str | None = None,
    livemode: bool | None = None,
    table_resource: Any = None,
) -> None:
    resource = table_resource or table
    digest = subject_digest(subject)
    item = {
        "PK": subject_pk(digest),
        "SK": BILLING_RECOVERY_SK,
        "requestId": request_id,
        TTL_ATTRIBUTE: int(utc_now().timestamp()) + ACTIVE_LOCK_TTL_SECONDS,
    }
    if customer_id is not None:
        item["stripeCustomerId"] = customer_id
        item["livemode"] = livemode
    try:
        resource.put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(PK) OR requestId = :request_id"
            ),
            ExpressionAttributeValues={":request_id": request_id},
        )
    except ClientError as exc:
        _log_store_failure(exc, "PutItem")
        raise DeletionStoreUnavailable() from None


def get_billing_recovery(
    *, subject: str, request_id: str, table_resource: Any = None,
) -> dict[str, Any] | None:
    item = _get_item(
        subject_pk(subject_digest(subject)),
        BILLING_RECOVERY_SK,
        table_resource=table_resource,
    )
    return item if item and item.get("requestId") == request_id else None


def complete_deletion_request(
    *,
    subject: str,
    request_id: str,
    table_resource: Any = None,
    transact_writer: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    resource = table_resource or table
    digest = subject_digest(subject)
    existing = _request_by_digest(digest, request_id, table_resource=resource)
    if existing and existing.get("status") == "COMPLETED":
        return existing
    writer = transact_writer or dynamodb_client.transact_write_items
    completed_at = isoformat_utc(utc_now())
    try:
        writer(TransactItems=[
            {"Update": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": audit_pk(request_id), "SK": AUDIT_SK,
                }),
                "UpdateExpression": (
                    "SET #status = :completed, completedAt = :completed_at "
                    "REMOVE workflowExecutionArn, destructiveStartedAt, verifiedAt, "
                    "failureCode, failurePhase, failedAt, retryable, #ttl"
                ),
                "ConditionExpression": (
                    "subjectDigest = :subject AND attribute_exists(verifiedAt)"
                ),
                "ExpressionAttributeNames": {
                    "#status": "status", "#ttl": TTL_ATTRIBUTE,
                },
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":completed": "COMPLETED",
                    ":completed_at": completed_at,
                    ":subject": digest,
                }),
            }},
            {"Delete": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": subject_pk(digest), "SK": ACTIVE_SK,
                }),
                "ConditionExpression": (
                    "attribute_not_exists(PK) OR requestId = :request_id"
                ),
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":request_id": request_id,
                }),
            }},
            {"Delete": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": subject_pk(digest), "SK": BILLING_RECOVERY_SK,
                }),
                "ConditionExpression": (
                    "attribute_not_exists(PK) OR requestId = :request_id"
                ),
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":request_id": request_id,
                }),
            }},
            {"Delete": {
                "TableName": resource.name,
                "Key": serialize_attribute_map({
                    "PK": audit_pk(request_id), "SK": SUBJECT_RECOVERY_SK,
                }),
                "ConditionExpression": (
                    "attribute_not_exists(PK) OR subjectDigest = :subject"
                ),
                "ExpressionAttributeValues": serialize_attribute_map({
                    ":subject": digest,
                }),
            }},
        ])
    except ClientError as exc:
        code = (exc.response.get("Error") or {}).get("Code")
        cancellation_codes = {
            reason.get("Code")
            for reason in exc.response.get("CancellationReasons") or []
            if isinstance(reason, dict)
        }
        if (
            code == "TransactionCanceledException"
            and cancellation_codes
            and cancellation_codes <= {
                None, "None", "ConditionalCheckFailed",
            }
        ):
            raise DeletionStoreInvariant() from None
        if code != "ConditionalCheckFailedException":
            _log_store_failure(exc, "TransactWriteItems")
        raise DeletionStoreUnavailable() from None
    completed = _request_by_digest(
        digest, request_id, table_resource=resource,
    )
    if completed is None:
        raise DeletionStoreUnavailable()
    return completed
