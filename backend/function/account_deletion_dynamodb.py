"""Bounded, paginated DynamoDB deletion for one exact user partition."""

from __future__ import annotations

import time
from typing import Any, Callable

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

from storage import serialize_attribute_map, user_pk


MAX_BATCH_ITEMS = 25
MAX_UNPROCESSED_RETRIES = 5


class DeletionDynamoError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def list_partition_keys(table_resource: Any, subject: str) -> list[dict[str, Any]]:
    keys: list[dict[str, Any]] = []
    arguments: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(user_pk(subject)),
        "ProjectionExpression": "PK, SK",
        "ConsistentRead": True,
    }
    try:
        while True:
            page = table_resource.query(**arguments)
            keys.extend(
                {"PK": item["PK"], "SK": item["SK"]}
                for item in page.get("Items", [])
            )
            last_key = page.get("LastEvaluatedKey")
            if not last_key:
                return keys
            arguments["ExclusiveStartKey"] = last_key
    except (ClientError, BotoCoreError):
        raise DeletionDynamoError(
            "DynamoPartitionQueryUnavailable", retryable=True
        ) from None


def _delete_batch(
    client: Any,
    table_name: str,
    keys: list[dict[str, Any]],
    *,
    sleeper: Callable[[float], None],
) -> None:
    requests = [
        {"DeleteRequest": {"Key": serialize_attribute_map(key)}}
        for key in keys
    ]
    for attempt in range(MAX_UNPROCESSED_RETRIES + 1):
        try:
            response = client.batch_write_item(
                RequestItems={table_name: requests}
            )
        except (ClientError, BotoCoreError):
            raise DeletionDynamoError(
                "DynamoBatchDeleteUnavailable", retryable=True
            ) from None
        requests = response.get("UnprocessedItems", {}).get(table_name, [])
        if not requests:
            return
        if attempt == MAX_UNPROCESSED_RETRIES:
            break
        sleeper(min(0.05 * (2 ** attempt), 1.0))
    raise DeletionDynamoError("DynamoBatchDeleteIncomplete", retryable=True)


def delete_user_partition(
    table_resource: Any,
    client: Any,
    *,
    table_name: str,
    subject: str,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    while True:
        keys = list_partition_keys(table_resource, subject)
        if not keys:
            return
        for offset in range(0, len(keys), MAX_BATCH_ITEMS):
            _delete_batch(
                client,
                table_name,
                keys[offset:offset + MAX_BATCH_ITEMS],
                sleeper=sleeper,
            )


def partition_is_empty(table_resource: Any, subject: str) -> bool:
    try:
        response = table_resource.query(
            KeyConditionExpression=Key("PK").eq(user_pk(subject)),
            ProjectionExpression="PK",
            ConsistentRead=True,
            Limit=1,
        )
        return not bool(response.get("Items"))
    except (ClientError, BotoCoreError):
        raise DeletionDynamoError(
            "DynamoPartitionVerificationUnavailable", retryable=True
        ) from None
