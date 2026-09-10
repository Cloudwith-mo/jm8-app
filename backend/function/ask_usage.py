from __future__ import annotations

import json
from typing import (
    Any,
    Mapping,
)

from usage_policy import (
    OPERATION_ASK_JM8,
    build_usage_limit_error,
)
from usage_store import (
    UsageLimitExceededError,
    UsageStoreError,
    complete_usage_reservation,
    fail_usage_reservation,
    reserve_monthly_usage,
)


ASK_USAGE_OPERATION_KEY = "askJm8"


class AskUsageLimitError(
    RuntimeError
):
    def __init__(
        self,
        payload: Mapping[str, Any],
    ):
        super().__init__(
            "The monthly Ask JM8 "
            "limit has been reached."
        )

        self.payload = dict(payload)


class AskUsageUnavailableError(
    RuntimeError
):
    def __init__(
        self,
        *,
        retryable: bool,
    ):
        super().__init__(
            "Ask JM8 usage tracking "
            "is unavailable."
        )

        self.retryable = bool(
            retryable
        )

        self.payload = {
            "error": (
                "UsageTrackingUnavailable"
            ),
            "message": (
                "JM8 could not verify your "
                "monthly usage right now."
            ),
            "retryable": self.retryable,
        }

        if self.retryable:
            self.payload[
                "retryAfterSeconds"
            ] = 2


def ask_context_requires_usage(
    ask_context: Mapping[str, Any],
) -> bool:
    if not isinstance(
        ask_context,
        Mapping,
    ):
        return False

    status = str(
        ask_context.get(
            "contextStatus",
            "",
        )
    ).strip().upper()

    question = ask_context.get(
        "question"
    )

    return (
        status in {
            "EMPTY",
            "PARTIAL",
            "READY",
        }
        and isinstance(question, str)
        and len(question.strip()) >= 3
    )


def _log_usage_event(
    event_name: str,
    **fields: Any,
) -> None:
    print(json.dumps({
        "event": event_name,
        **fields,
    }))


def _raise_unavailable(
    error: UsageStoreError,
    *,
    stage: str,
) -> None:
    _log_usage_event(
        "ask_jm8_usage_store_failed",
        stage=stage,
        failureCode=error.code,
        retryable=error.retryable,
    )

    raise AskUsageUnavailableError(
        retryable=error.retryable,
    ) from error


def reserve_ask_usage(
    user_id: Any,
    ask_context: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not ask_context_requires_usage(
        ask_context
    ):
        return None

    try:
        reservation = (
            reserve_monthly_usage(
                user_id,
                operation=(
                    OPERATION_ASK_JM8
                ),
            )
        )

    except UsageLimitExceededError as error:
        payload = (
            build_usage_limit_error(
                error.decision
            )
        )

        _log_usage_event(
            "ask_jm8_usage_limit_exceeded",
            operation=(
                ASK_USAGE_OPERATION_KEY
            ),
            plan=payload["plan"],
            period=payload["period"],
            limit=payload["limit"],
            used=payload["used"],
            reserved=payload[
                "reserved"
            ],
            remaining=0,
            upgradeRequired=payload[
                "upgradeRequired"
            ],
        )

        raise AskUsageLimitError(
            payload
        ) from error

    except UsageStoreError as error:
        _raise_unavailable(
            error,
            stage="reserve",
        )

    _log_usage_event(
        "ask_jm8_usage_reserved",
        operation=ASK_USAGE_OPERATION_KEY,
        plan=reservation["plan"],
        period=reservation["period"],
    )

    return reservation


def complete_ask_usage(
    user_id: Any,
    reservation: (
        Mapping[str, Any] | None
    ),
) -> bool:
    if reservation is None:
        return False

    try:
        completed = (
            complete_usage_reservation(
                user_id,
                reservation=reservation,
            )
        )

    except UsageStoreError as error:
        _raise_unavailable(
            error,
            stage="complete",
        )

    _log_usage_event(
        "ask_jm8_usage_completed",
        operation=ASK_USAGE_OPERATION_KEY,
        period=completed["period"],
    )

    return True


def fail_ask_usage(
    user_id: Any,
    reservation: (
        Mapping[str, Any] | None
    ),
) -> bool:
    if reservation is None:
        return False

    try:
        released = (
            fail_usage_reservation(
                user_id,
                reservation=reservation,
            )
        )

    except UsageStoreError as error:
        # Preserve the original Ask JM8
        # error response. The reserved slot
        # remains fail-closed for cost safety.
        _log_usage_event(
            "ask_jm8_usage_store_failed",
            stage="release",
            failureCode=error.code,
            retryable=error.retryable,
        )

        return False

    _log_usage_event(
        "ask_jm8_usage_released",
        operation=ASK_USAGE_OPERATION_KEY,
        period=released["period"],
    )

    return True
