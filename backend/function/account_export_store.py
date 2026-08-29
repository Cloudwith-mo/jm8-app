"""DynamoDB coordination store for one active account export per user."""

from __future__ import annotations

from typing import Any, Callable

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from account_export_contract import (
    ACTIVE_STATUSES,
    account_export_sk,
    isoformat_utc,
    new_export_id,
    request_token_digest,
    utc_now,
)
from storage import serialize_attribute_map, table, user_pk


ACTIVE_SK = "ACCOUNT_EXPORT_ACTIVE"
IDEMPOTENCY_PREFIX = "ACCOUNT_EXPORT_REQUEST#"
JOB_TTL_SECONDS = 7 * 24 * 60 * 60
LOCK_TTL_SECONDS = 30 * 60
TTL_ATTRIBUTE = "accountExportTtlEpoch"


class ActiveExportExists(RuntimeError):
    pass


class ExportStoreUnavailable(RuntimeError):
    pass


def _idempotency_sk(digest: str) -> str:
    return f"{IDEMPOTENCY_PREFIX}{digest}"


def get_export_job(user_id: str, export_id: str) -> dict[str, Any] | None:
    response = table.get_item(Key={"PK": user_pk(user_id), "SK": account_export_sk(export_id)})
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def _get_coordination(user_id: str, sk: str) -> dict[str, Any] | None:
    response = table.get_item(Key={"PK": user_pk(user_id), "SK": sk}, ConsistentRead=True)
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def create_or_replay_export(
    *, user_id: str, request_token: object, profile: dict[str, Any],
    transact_writer: Callable[..., Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    digest = request_token_digest(request_token)
    idem_sk = _idempotency_sk(digest)
    replay = _get_coordination(user_id, idem_sk)
    if replay and replay.get("exportId"):
        job = get_export_job(user_id, str(replay["exportId"]))
        if job:
            return job, True

    now = utc_now()
    epoch = int(now.timestamp())
    active = _get_coordination(user_id, ACTIVE_SK)
    if active and active.get("status") in ACTIVE_STATUSES and int(active.get(TTL_ATTRIBUTE, 0)) > epoch:
        raise ActiveExportExists()

    export_id = new_export_id(now)
    created_at = isoformat_utc(now)
    job = {
        "PK": user_pk(user_id), "SK": account_export_sk(export_id),
        "entityType": "ACCOUNT_EXPORT", "userId": user_id,
        "exportId": export_id, "status": "QUEUED", "createdAt": created_at,
        TTL_ATTRIBUTE: epoch + JOB_TTL_SECONDS,
        "accountProfile": profile,
    }
    lock = {
        "PK": user_pk(user_id), "SK": ACTIVE_SK,
        "entityType": "ACCOUNT_EXPORT_ACTIVE", "exportId": export_id,
        "status": "QUEUED", "createdAt": created_at,
        TTL_ATTRIBUTE: epoch + LOCK_TTL_SECONDS,
    }
    idempotency = {
        "PK": user_pk(user_id), "SK": idem_sk,
        "entityType": "ACCOUNT_EXPORT_REQUEST", "exportId": export_id,
        TTL_ATTRIBUTE: epoch + JOB_TTL_SECONDS,
    }
    writer = transact_writer or table.meta.client.transact_write_items
    try:
        writer(TransactItems=[
            {"Put": {"TableName": table.name, "Item": serialize_attribute_map(job),
                     "ConditionExpression": "attribute_not_exists(PK)"}},
            {"Put": {"TableName": table.name, "Item": serialize_attribute_map(lock),
                     "ConditionExpression": (
                         "attribute_not_exists(PK) OR #ttl <= :now OR "
                         "(#status <> :queued AND #status <> :running)"
                     ), "ExpressionAttributeNames": {"#ttl": TTL_ATTRIBUTE, "#status": "status"},
                     "ExpressionAttributeValues": serialize_attribute_map({
                         ":now": epoch, ":queued": "QUEUED", ":running": "RUNNING",
                     })}},
            {"Put": {"TableName": table.name, "Item": serialize_attribute_map(idempotency),
                     "ConditionExpression": "attribute_not_exists(PK) OR #ttl <= :now",
                     "ExpressionAttributeNames": {"#ttl": TTL_ATTRIBUTE},
                     "ExpressionAttributeValues": serialize_attribute_map({":now": epoch})}},
        ])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            replay = _get_coordination(user_id, idem_sk)
            if replay and replay.get("exportId"):
                replay_job = get_export_job(user_id, str(replay["exportId"]))
                if replay_job:
                    return replay_job, True
            raise ActiveExportExists() from None
        raise ExportStoreUnavailable() from None
    return job, False


def list_export_jobs(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    response = table.query(
        KeyConditionExpression=Key("PK").eq(user_pk(user_id)) & Key("SK").begins_with("ACCOUNT_EXPORT#exp_"),
        ScanIndexForward=False, Limit=min(max(limit, 1), 10),
    )
    return [item for item in response.get("Items", []) if item.get("entityType") == "ACCOUNT_EXPORT"]


def update_export_status(user_id: str, export_id: str, status: str, **values: Any) -> dict[str, Any]:
    names = {"#status": "status"}
    value_map: dict[str, Any] = {":status": status}
    updates = ["#status = :status"]
    for index, (name, value) in enumerate(values.items()):
        name_key = f"#n{index}"
        value_key = f":v{index}"
        names[name_key] = name
        value_map[value_key] = value
        updates.append(f"{name_key} = {value_key}")
    allowed_previous = {
        "RUNNING": ("QUEUED", "RUNNING"),
        "COMPLETED": ("RUNNING",),
        "FAILED": ("QUEUED", "RUNNING"),
        "EXPIRED": ("COMPLETED",),
    }.get(status)
    condition = "attribute_exists(PK)"
    if allowed_previous:
        previous_values = []
        for index, previous in enumerate(allowed_previous):
            key = f":previous{index}"
            value_map[key] = previous
            previous_values.append(key)
        condition += f" AND #status IN ({', '.join(previous_values)})"
    response = table.update_item(
        Key={"PK": user_pk(user_id), "SK": account_export_sk(export_id)},
        UpdateExpression="SET " + ", ".join(updates),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=value_map,
        ConditionExpression=condition,
        ReturnValues="ALL_NEW",
    )
    return response["Attributes"]


def release_active_lock(user_id: str, export_id: str) -> None:
    try:
        table.delete_item(
            Key={"PK": user_pk(user_id), "SK": ACTIVE_SK},
            ConditionExpression="exportId = :exportId",
            ExpressionAttributeValues={":exportId": export_id},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def fail_export(user_id: str, export_id: str, code: str = "ExportFailed", message: str = "The export could not be completed.", retryable: bool = True) -> None:
    update_export_status(
        user_id, export_id, "FAILED", completedAt=isoformat_utc(utc_now()),
        error={"code": code, "message": message, "retryable": retryable},
    )
    release_active_lock(user_id, export_id)


def mark_export_expired(user_id: str, export_id: str) -> dict[str, Any]:
    return update_export_status(user_id, export_id, "EXPIRED")
