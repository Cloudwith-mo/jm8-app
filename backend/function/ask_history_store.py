from __future__ import annotations

import base64
import json
from typing import (
    Any,
    Mapping,
)

from boto3.dynamodb.conditions import (
    Key,
)
from botocore.exceptions import (
    ClientError,
)

from ask_history_contract import (
    ASK_HISTORY_ENTITY_TYPE,
    ASK_HISTORY_SK_PREFIX,
    AskHistoryContractError,
    build_ask_history_item,
    build_public_ask_history_detail,
    build_public_ask_history_summary,
    normalize_ask_history_id,
)
from storage import (
    clean_for_dynamodb,
    table,
    user_pk,
)


ASK_HISTORY_INDEX_NAME = "GSI1"

ASK_HISTORY_INDEX_PK_SUFFIX = (
    "#ASK_HISTORY"
)

ASK_HISTORY_INDEX_SK_PREFIX = (
    "HISTORY#"
)

ASK_HISTORY_CURSOR_VERSION = 1

DEFAULT_ASK_HISTORY_LIMIT = 20
MAX_ASK_HISTORY_LIMIT = 50
MAX_CURSOR_CHARACTERS = 2_048

RETRYABLE_DYNAMODB_ERRORS = {
    "InternalServerError",
    "ProvisionedThroughputExceededException",
    "RequestLimitExceeded",
    "ThrottlingException",
    "TransactionInProgressException",
}


class AskHistoryStoreError(
    RuntimeError
):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ):
        super().__init__(message)

        self.code = str(
            code or "AskHistoryStoreError"
        )

        self.message = str(message)
        self.retryable = bool(retryable)


def _raise_store_input_error(
    code: str,
    message: str,
) -> None:
    raise AskHistoryStoreError(
        code,
        message,
        retryable=False,
    )


def _normalize_user_id(
    value: Any,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        _raise_store_input_error(
            "InvalidAskHistoryUser",
            "A valid user is required.",
        )

    normalized = value.strip()

    if (
        not normalized
        or len(normalized) > 180
    ):
        _raise_store_input_error(
            "InvalidAskHistoryUser",
            "A valid user is required.",
        )

    return normalized


def normalize_ask_history_limit(
    value: Any = None,
) -> int:
    if value is None:
        return DEFAULT_ASK_HISTORY_LIMIT

    if isinstance(value, bool):
        _raise_store_input_error(
            "InvalidAskHistoryLimit",
            (
                "History limit must be "
                "an integer."
            ),
        )

    try:
        normalized = int(value)
    except (
        TypeError,
        ValueError,
    ):
        _raise_store_input_error(
            "InvalidAskHistoryLimit",
            (
                "History limit must be "
                "an integer."
            ),
        )

    if (
        normalized < 1
        or normalized
        > MAX_ASK_HISTORY_LIMIT
    ):
        _raise_store_input_error(
            "InvalidAskHistoryLimit",
            (
                "History limit must be "
                "between 1 and "
                f"{MAX_ASK_HISTORY_LIMIT}."
            ),
        )

    return normalized


def ask_history_index_pk(
    user_id: Any,
) -> str:
    normalized_user_id = (
        _normalize_user_id(
            user_id
        )
    )

    return (
        f"{user_pk(normalized_user_id)}"
        f"{ASK_HISTORY_INDEX_PK_SUFFIX}"
    )


def ask_history_index_sk(
    history_id: Any,
) -> str:
    normalized_history_id = (
        normalize_ask_history_id(
            history_id
        )
    )

    return (
        f"{ASK_HISTORY_INDEX_SK_PREFIX}"
        f"{normalized_history_id}"
    )


def _cursor_key_is_valid(
    value: Mapping[str, Any],
    *,
    user_id: str,
) -> bool:
    expected_pk = user_pk(user_id)

    return (
        value.get("PK") == expected_pk
        and isinstance(
            value.get("SK"),
            str,
        )
        and value["SK"].startswith(
            ASK_HISTORY_SK_PREFIX
        )
    )


def encode_ask_history_cursor(
    last_evaluated_key: Any,
    *,
    user_id: Any,
) -> str | None:
    if not last_evaluated_key:
        return None

    normalized_user_id = (
        _normalize_user_id(
            user_id
        )
    )

    if not isinstance(
        last_evaluated_key,
        Mapping,
    ):
        _raise_store_input_error(
            "InvalidAskHistoryCursor",
            (
                "History cursor contains "
                "an invalid key."
            ),
        )

    cursor_key = {
        "PK": last_evaluated_key.get(
            "PK"
        ),
        "SK": last_evaluated_key.get(
            "SK"
        ),
    }

    if not _cursor_key_is_valid(
        cursor_key,
        user_id=normalized_user_id,
    ):
        _raise_store_input_error(
            "InvalidAskHistoryCursor",
            (
                "History cursor does not "
                "belong to this user."
            ),
        )

    payload = json.dumps(
        {
            "version": (
                ASK_HISTORY_CURSOR_VERSION
            ),
            "pk": cursor_key["PK"],
            "sk": cursor_key["SK"],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return (
        base64.urlsafe_b64encode(
            payload
        )
        .decode("ascii")
        .rstrip("=")
    )


def decode_ask_history_cursor(
    cursor: Any,
    *,
    user_id: Any,
) -> dict[str, str] | None:
    if cursor is None or cursor == "":
        return None

    normalized_user_id = (
        _normalize_user_id(
            user_id
        )
    )

    if (
        not isinstance(cursor, str)
        or len(cursor)
        > MAX_CURSOR_CHARACTERS
    ):
        _raise_store_input_error(
            "InvalidAskHistoryCursor",
            "History cursor is invalid.",
        )

    normalized_cursor = cursor.strip()

    if not normalized_cursor:
        return None

    padding = (
        "="
        * (-len(normalized_cursor) % 4)
    )

    try:
        decoded = base64.b64decode(
            normalized_cursor + padding,
            altchars=b"-_",
            validate=True,
        )

        payload = json.loads(
            decoded.decode("utf-8")
        )
    except (
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        _raise_store_input_error(
            "InvalidAskHistoryCursor",
            "History cursor is invalid.",
        )

    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "version",
            "pk",
            "sk",
        }
        or payload.get("version")
        != ASK_HISTORY_CURSOR_VERSION
    ):
        _raise_store_input_error(
            "InvalidAskHistoryCursor",
            "History cursor is invalid.",
        )

    cursor_key = {
        "PK": payload.get("pk"),
        "SK": payload.get("sk"),
    }

    if not _cursor_key_is_valid(
        cursor_key,
        user_id=normalized_user_id,
    ):
        _raise_store_input_error(
            "InvalidAskHistoryCursor",
            (
                "History cursor does not "
                "belong to this user."
            ),
        )

    return {
        "PK": str(cursor_key["PK"]),
        "SK": str(cursor_key["SK"]),
    }


def _client_error_code(
    error: ClientError,
) -> str:
    return str(
        error.response.get(
            "Error",
            {},
        ).get(
            "Code",
            "DynamoDBError",
        )
    )


def _raise_client_error(
    error: ClientError,
    *,
    action: str,
) -> None:
    error_code = _client_error_code(
        error
    )

    raise AskHistoryStoreError(
        error_code,
        (
            "Ask JM8 history could not "
            f"be {action}."
        ),
        retryable=(
            error_code
            in RETRYABLE_DYNAMODB_ERRORS
        ),
    ) from error


def _public_detail(
    item: Any,
) -> dict[str, Any]:
    try:
        return (
            build_public_ask_history_detail(
                item
            )
        )
    except AskHistoryContractError as error:
        raise AskHistoryStoreError(
            "InvalidAskHistoryRecord",
            (
                "A stored Ask JM8 history "
                "record is invalid."
            ),
            retryable=False,
        ) from error


def _public_summary(
    item: Any,
) -> dict[str, Any]:
    try:
        return (
            build_public_ask_history_summary(
                item
            )
        )
    except AskHistoryContractError as error:
        raise AskHistoryStoreError(
            "InvalidAskHistoryRecord",
            (
                "A stored Ask JM8 history "
                "record is invalid."
            ),
            retryable=False,
        ) from error


def create_ask_history(
    user_id: Any,
    answer: Any,
    *,
    history_id: Any | None = None,
    table_resource=None,
) -> dict[str, Any]:
    normalized_user_id = (
        _normalize_user_id(
            user_id
        )
    )

    try:
        item = build_ask_history_item(
            user_id=normalized_user_id,
            answer=answer,
            history_id=history_id,
        )
    except AskHistoryContractError as error:
        raise AskHistoryStoreError(
            error.code,
            error.message,
            retryable=False,
        ) from error

    item["GSI1PK"] = (
        ask_history_index_pk(
            normalized_user_id
        )
    )

    item["GSI1SK"] = (
        ask_history_index_sk(
            item["historyId"]
        )
    )

    resource = (
        table_resource
        if table_resource is not None
        else table
    )

    try:
        resource.put_item(
            Item=clean_for_dynamodb(
                item
            ),
            ConditionExpression=(
                "attribute_not_exists(PK) "
                "AND "
                "attribute_not_exists(SK)"
            ),
        )
    except ClientError as error:
        if (
            _client_error_code(error)
            == "ConditionalCheckFailedException"
        ):
            raise AskHistoryStoreError(
                "AskHistoryAlreadyExists",
                (
                    "This Ask JM8 history "
                    "record already exists."
                ),
                retryable=False,
            ) from error

        _raise_client_error(
            error,
            action="saved",
        )

    return _public_detail(item)


def list_ask_history(
    user_id: Any,
    *,
    limit: Any = None,
    cursor: Any = None,
    table_resource=None,
) -> dict[str, Any]:
    normalized_user_id = (
        _normalize_user_id(
            user_id
        )
    )

    safe_limit = (
        normalize_ask_history_limit(
            limit
        )
    )

    exclusive_start_key = (
        decode_ask_history_cursor(
            cursor,
            user_id=normalized_user_id,
        )
    )

    query_arguments: dict[
        str,
        Any,
    ] = {
        "KeyConditionExpression": (
            Key("PK").eq(
                user_pk(
                    normalized_user_id
                )
            )
            & Key("SK").begins_with(
                ASK_HISTORY_SK_PREFIX
            )
        ),
        "ScanIndexForward": False,
        "ConsistentRead": True,
        "Limit": safe_limit,
    }

    if exclusive_start_key:
        query_arguments[
            "ExclusiveStartKey"
        ] = exclusive_start_key

    resource = (
        table_resource
        if table_resource is not None
        else table
    )

    try:
        result = resource.query(
            **query_arguments
        )
    except ClientError as error:
        _raise_client_error(
            error,
            action="listed",
        )

    summaries = [
        _public_summary(item)
        for item in result.get(
            "Items",
            [],
        )
        if (
            isinstance(item, Mapping)
            and item.get(
                "entityType"
            )
            == ASK_HISTORY_ENTITY_TYPE
        )
    ]

    next_cursor = (
        encode_ask_history_cursor(
            result.get(
                "LastEvaluatedKey"
            ),
            user_id=normalized_user_id,
        )
    )

    return {
        "count": len(summaries),
        "items": summaries,
        "nextCursor": next_cursor,
    }


def _find_ask_history_item(
    user_id: Any,
    history_id: Any,
    *,
    table_resource=None,
) -> dict[str, Any] | None:
    normalized_user_id = (
        _normalize_user_id(
            user_id
        )
    )

    try:
        normalized_history_id = (
            normalize_ask_history_id(
                history_id
            )
        )
    except AskHistoryContractError as error:
        raise AskHistoryStoreError(
            error.code,
            error.message,
            retryable=False,
        ) from error

    resource = (
        table_resource
        if table_resource is not None
        else table
    )

    try:
        result = resource.query(
            IndexName=(
                ASK_HISTORY_INDEX_NAME
            ),
            KeyConditionExpression=(
                Key("GSI1PK").eq(
                    ask_history_index_pk(
                        normalized_user_id
                    )
                )
                & Key("GSI1SK").eq(
                    ask_history_index_sk(
                        normalized_history_id
                    )
                )
            ),
            Limit=1,
        )
    except ClientError as error:
        _raise_client_error(
            error,
            action="retrieved",
        )

    for item in result.get(
        "Items",
        [],
    ):
        if (
            isinstance(item, dict)
            and item.get("PK")
            == user_pk(
                normalized_user_id
            )
            and item.get(
                "historyId"
            )
            == normalized_history_id
            and item.get(
                "entityType"
            )
            == ASK_HISTORY_ENTITY_TYPE
        ):
            return item

    return None


def get_ask_history(
    user_id: Any,
    history_id: Any,
    *,
    table_resource=None,
) -> dict[str, Any] | None:
    item = _find_ask_history_item(
        user_id,
        history_id,
        table_resource=table_resource,
    )

    if item is None:
        return None

    return _public_detail(item)


def delete_ask_history(
    user_id: Any,
    history_id: Any,
    *,
    table_resource=None,
) -> bool:
    item = _find_ask_history_item(
        user_id,
        history_id,
        table_resource=table_resource,
    )

    if item is None:
        return False

    resource = (
        table_resource
        if table_resource is not None
        else table
    )

    try:
        result = resource.delete_item(
            Key={
                "PK": item["PK"],
                "SK": item["SK"],
            },
            ConditionExpression=(
                "attribute_exists(PK) "
                "AND "
                "attribute_exists(SK)"
            ),
            ReturnValues="ALL_OLD",
        )
    except ClientError as error:
        if (
            _client_error_code(error)
            == "ConditionalCheckFailedException"
        ):
            return False

        _raise_client_error(
            error,
            action="deleted",
        )

    return bool(
        result.get("Attributes")
        or item
    )
