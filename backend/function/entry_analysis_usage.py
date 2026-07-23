from __future__ import annotations

import json
from typing import (
    Any,
    Mapping,
)

from usage_policy import (
    OPERATION_ENTRY_ANALYSIS,
    PLAN_FREE,
    build_usage_limit_error,
)
from usage_store import (
    UsageLimitExceededError,
    UsageStoreError,
    complete_usage_reservation,
    fail_usage_reservation,
    reserve_monthly_usage,
)


ENTRY_ANALYSIS_OPERATION_KEY = (
    "entryAnalysis"
)


class EntryAnalysisUsageLimitError(
    RuntimeError
):
    def __init__(
        self,
        payload: Mapping[str, Any],
    ):
        super().__init__(
            "The monthly entry-analysis "
            "limit has been reached."
        )

        self.payload = dict(payload)


class EntryAnalysisUsageUnavailableError(
    RuntimeError
):
    def __init__(
        self,
        *,
        retryable: bool,
    ):
        super().__init__(
            "Entry-analysis usage tracking "
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
                "monthly analysis usage "
                "right now."
            ),
            "retryable": self.retryable,
        }

        if self.retryable:
            self.payload[
                "retryAfterSeconds"
            ] = 2


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
        (
            "entry_analysis_usage_"
            "store_failed"
        ),
        stage=stage,
        failureCode=error.code,
        retryable=error.retryable,
    )

    raise (
        EntryAnalysisUsageUnavailableError(
            retryable=error.retryable,
        )
    ) from error


def reserve_entry_analysis_usage(
    user_id: Any,
) -> dict[str, Any]:
    try:
        reservation = (
            reserve_monthly_usage(
                user_id,
                plan=PLAN_FREE,
                operation=(
                    OPERATION_ENTRY_ANALYSIS
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
            (
                "entry_analysis_usage_"
                "limit_exceeded"
            ),
            operation=(
                ENTRY_ANALYSIS_OPERATION_KEY
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

        raise (
            EntryAnalysisUsageLimitError(
                payload
            )
        ) from error

    except UsageStoreError as error:
        _raise_unavailable(
            error,
            stage="reserve",
        )

    _log_usage_event(
        (
            "entry_analysis_usage_"
            "reserved"
        ),
        operation=(
            ENTRY_ANALYSIS_OPERATION_KEY
        ),
        plan=reservation["plan"],
        period=reservation["period"],
    )

    return reservation


def complete_entry_analysis_usage(
    user_id: Any,
    reservation: Mapping[str, Any],
) -> bool:
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
        (
            "entry_analysis_usage_"
            "completed"
        ),
        operation=(
            ENTRY_ANALYSIS_OPERATION_KEY
        ),
        period=completed["period"],
    )

    return True


def fail_entry_analysis_usage(
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
        # Preserve the original analysis
        # failure response. Capacity remains
        # fail-closed for cost safety.
        _log_usage_event(
            (
                "entry_analysis_usage_"
                "store_failed"
            ),
            stage="release",
            failureCode=error.code,
            retryable=error.retryable,
        )

        return False

    _log_usage_event(
        (
            "entry_analysis_usage_"
            "released"
        ),
        operation=(
            ENTRY_ANALYSIS_OPERATION_KEY
        ),
        period=released["period"],
    )

    return True
