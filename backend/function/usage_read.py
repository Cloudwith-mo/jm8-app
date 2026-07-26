from __future__ import annotations

import json
from datetime import datetime
from typing import (
    Any,
    Mapping,
)

from botocore.exceptions import (
    ClientError,
)

from entitlement_resolver import (
    EntitlementUnavailableError,
    resolve_user_plan,
)
from usage_policy import (
    UsagePolicyError,
    build_usage_snapshot,
)
from usage_store import (
    RETRYABLE_DYNAMODB_ERRORS,
    UsageStoreError,
    get_monthly_usage_item,
)


class UsageReadUnavailableError(
    RuntimeError
):
    def __init__(
        self,
        *,
        retryable: bool,
    ):
        super().__init__(
            "Monthly usage could not "
            "be retrieved."
        )

        self.retryable = bool(
            retryable
        )

        self.payload = {
            "error": (
                "UsageTrackingUnavailable"
            ),
            "message": (
                "JM8 could not retrieve "
                "your monthly usage "
                "right now."
            ),
            "retryable": self.retryable,
        }

        if self.retryable:
            self.payload[
                "retryAfterSeconds"
            ] = 2


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


def _log_usage_event(
    event_name: str,
    **fields: Any,
) -> None:
    print(json.dumps({
        "event": event_name,
        **fields,
    }))


def _raise_unavailable(
    *,
    failure_code: str,
    retryable: bool,
) -> None:
    _log_usage_event(
        "usage_snapshot_read_failed",
        failureCode=failure_code,
        retryable=retryable,
    )

    raise UsageReadUnavailableError(
        retryable=retryable,
    )


def get_usage_snapshot(
    user_id: Any,
    *,
    now: datetime | None = None,
    environ: (
        Mapping[str, str] | None
    ) = None,
    table_resource=None,
) -> dict[str, Any]:
    try:
        usage_item = (
            get_monthly_usage_item(
                user_id,
                now=now,
                table_resource=(
                    table_resource
                ),
            )
        )

        effective_plan = (
            resolve_user_plan(
                user_id,
                now=now,
                table_resource=(
                    table_resource
                ),
            )
        )

        snapshot = build_usage_snapshot(
            plan=effective_plan,
            usage_item=usage_item,
            now=now,
            environ=environ,
        )

    except EntitlementUnavailableError as error:
        _raise_unavailable(
            failure_code=(
                "EntitlementUnavailable"
            ),
            retryable=error.retryable,
        )

    except UsageStoreError as error:
        _raise_unavailable(
            failure_code=error.code,
            retryable=error.retryable,
        )

    except UsagePolicyError as error:
        _raise_unavailable(
            failure_code=error.code,
            retryable=False,
        )

    except ClientError as error:
        error_code = (
            _client_error_code(error)
        )

        _raise_unavailable(
            failure_code=error_code,
            retryable=(
                error_code
                in RETRYABLE_DYNAMODB_ERRORS
            ),
        )

    operations = snapshot[
        "operations"
    ]

    _log_usage_event(
        "usage_snapshot_read",
        plan=snapshot["plan"]["id"],
        period=(
            snapshot["period"]["key"]
        ),
        askUsed=operations[
            "askJm8"
        ]["used"],
        entryAnalysisUsed=operations[
            "entryAnalysis"
        ]["used"],
    )

    return snapshot
