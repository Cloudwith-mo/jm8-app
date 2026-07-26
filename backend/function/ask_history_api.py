from __future__ import annotations

import json
from typing import (
    Any,
)

from ask_history_store import (
    AskHistoryStoreError,
    delete_ask_history,
    get_ask_history,
    list_ask_history,
)


ASK_HISTORY_INPUT_MESSAGES = {
    "InvalidAskHistoryLimit": (
        "limit must be an integer "
        "between 1 and 50."
    ),
    "InvalidAskHistoryCursor": (
        "The history cursor is invalid."
    ),
    "InvalidAskHistoryId": (
        "The Ask JM8 history ID "
        "is invalid."
    ),
    "InvalidAskHistoryUser": (
        "A valid authenticated user "
        "is required."
    ),
}


class AskHistoryApiError(
    RuntimeError
):
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any],
    ):
        super().__init__(
            str(
                payload.get(
                    "message"
                )
                or "Ask JM8 history failed."
            )
        )

        self.status_code = int(
            status_code
        )

        self.payload = dict(
            payload
        )


def _log_api_failure(
    *,
    operation: str,
    error: AskHistoryStoreError,
) -> None:
    print(json.dumps({
        "event": (
            "ask_jm8_history_api_failed"
        ),
        "operation": operation,
        "failureCode": error.code,
        "retryable": error.retryable,
    }))


def _raise_public_error(
    error: AskHistoryStoreError,
    *,
    operation: str,
) -> None:
    _log_api_failure(
        operation=operation,
        error=error,
    )

    input_message = (
        ASK_HISTORY_INPUT_MESSAGES.get(
            error.code
        )
    )

    if input_message:
        raise AskHistoryApiError(
            400,
            {
                "error": error.code,
                "message": input_message,
                "retryable": False,
            },
        ) from error

    payload = {
        "error": (
            "AskHistoryUnavailable"
        ),
        "message": (
            "JM8 could not access your "
            "private Ask history right now."
        ),
        "retryable": error.retryable,
    }

    if error.retryable:
        payload[
            "retryAfterSeconds"
        ] = 2

    raise AskHistoryApiError(
        503,
        payload,
    ) from error


def list_ask_history_for_api(
    user_id: Any,
    *,
    limit: Any = None,
    cursor: Any = None,
) -> dict[str, Any]:
    try:
        return list_ask_history(
            user_id,
            limit=limit,
            cursor=cursor,
        )

    except AskHistoryStoreError as error:
        _raise_public_error(
            error,
            operation="list",
        )


def get_ask_history_for_api(
    user_id: Any,
    history_id: Any,
) -> dict[str, Any] | None:
    try:
        return get_ask_history(
            user_id,
            history_id,
        )

    except AskHistoryStoreError as error:
        _raise_public_error(
            error,
            operation="get",
        )


def delete_ask_history_for_api(
    user_id: Any,
    history_id: Any,
) -> bool:
    try:
        return delete_ask_history(
            user_id,
            history_id,
        )

    except AskHistoryStoreError as error:
        _raise_public_error(
            error,
            operation="delete",
        )
