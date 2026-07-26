from __future__ import annotations

import json
from datetime import datetime
from typing import (
    Any,
)

from entitlement_policy import (
    resolve_effective_entitlement,
)
from entitlement_store import (
    EntitlementStoreError,
    get_entitlement_record,
)


INVALID_ENTITLEMENT_RECORD_CODE = (
    "InvalidEntitlementRecord"
)


class EntitlementUnavailableError(
    RuntimeError
):
    def __init__(
        self,
        *,
        retryable: bool,
    ):
        super().__init__(
            "The effective entitlement "
            "could not be resolved."
        )

        self.retryable = bool(
            retryable
        )

        self.payload = {
            "error": (
                "EntitlementUnavailable"
            ),
            "message": (
                "JM8 could not verify your "
                "plan right now."
            ),
            "retryable": self.retryable,
        }

        if self.retryable:
            self.payload[
                "retryAfterSeconds"
            ] = 2


def _log_entitlement_event(
    event_name: str,
    **fields: Any,
) -> None:
    print(json.dumps({
        "event": event_name,
        **fields,
    }))


def resolve_user_entitlement(
    user_id: Any,
    *,
    now: datetime | None = None,
    table_resource=None,
) -> dict[str, Any]:
    try:
        stored_record = (
            get_entitlement_record(
                user_id,
                table_resource=(
                    table_resource
                ),
            )
        )

    except EntitlementStoreError as error:
        if (
            error.code
            == INVALID_ENTITLEMENT_RECORD_CODE
        ):
            entitlement = (
                resolve_effective_entitlement(
                    None,
                    now=now,
                )
            )

            _log_entitlement_event(
                (
                    "entitlement_invalid_"
                    "record_defaulted"
                ),
                failureCode=error.code,
                fallbackPlan=(
                    entitlement[
                        "plan"
                    ]["id"]
                ),
            )

            return entitlement

        _log_entitlement_event(
            "entitlement_resolution_failed",
            failureCode=error.code,
            retryable=error.retryable,
        )

        raise EntitlementUnavailableError(
            retryable=error.retryable,
        ) from error

    entitlement = (
        resolve_effective_entitlement(
            stored_record,
            now=now,
        )
    )

    _log_entitlement_event(
        "entitlement_resolved",
        plan=entitlement[
            "plan"
        ]["id"],
        status=entitlement[
            "subscription"
        ]["status"],
        source=entitlement[
            "subscription"
        ]["source"],
        isPro=entitlement[
            "access"
        ]["isPro"],
    )

    return entitlement


def resolve_user_plan(
    user_id: Any,
    *,
    now: datetime | None = None,
    table_resource=None,
) -> str:
    entitlement = (
        resolve_user_entitlement(
            user_id,
            now=now,
            table_resource=(
                table_resource
            ),
        )
    )

    return str(
        entitlement[
            "plan"
        ]["id"]
    )
