from __future__ import annotations

import json
from datetime import datetime
from typing import (
    Any,
    Mapping,
)

from entitlement_resolver import (
    EntitlementUnavailableError,
    resolve_user_entitlement,
)
from usage_policy import (
    OPERATION_ASK_JM8,
    OPERATION_ENTRY_ANALYSIS,
    UsagePolicyError,
    get_plan_limits,
)


ACCOUNT_ENTITLEMENT_VERSION = "1.0"


class AccountEntitlementUnavailableError(
    RuntimeError
):
    def __init__(
        self,
        *,
        retryable: bool,
    ):
        super().__init__(
            "The account entitlement "
            "could not be retrieved."
        )

        self.retryable = bool(
            retryable
        )

        self.payload = {
            "error": (
                "AccountEntitlementUnavailable"
            ),
            "message": (
                "JM8 could not retrieve your "
                "plan details right now."
            ),
            "retryable": self.retryable,
        }

        if self.retryable:
            self.payload[
                "retryAfterSeconds"
            ] = 2


def _log_account_entitlement_event(
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
    _log_account_entitlement_event(
        (
            "account_entitlement_"
            "read_failed"
        ),
        failureCode=failure_code,
        retryable=retryable,
    )

    raise (
        AccountEntitlementUnavailableError(
            retryable=retryable,
        )
    )


def get_account_entitlement(
    user_id: Any,
    *,
    now: datetime | None = None,
    environ: (
        Mapping[str, str] | None
    ) = None,
    table_resource=None,
) -> dict[str, Any]:
    try:
        entitlement = (
            resolve_user_entitlement(
                user_id,
                now=now,
                table_resource=(
                    table_resource
                ),
            )
        )

        effective_plan = (
            entitlement[
                "plan"
            ]["id"]
        )

        limits = get_plan_limits(
            effective_plan,
            environ=environ,
        )

    except EntitlementUnavailableError as error:
        _raise_unavailable(
            failure_code=(
                "EntitlementUnavailable"
            ),
            retryable=error.retryable,
        )

    except UsagePolicyError as error:
        _raise_unavailable(
            failure_code=error.code,
            retryable=False,
        )

    projection = {
        "accountEntitlementVersion": (
            ACCOUNT_ENTITLEMENT_VERSION
        ),
        "generatedAt": entitlement[
            "generatedAt"
        ],
        "plan": dict(
            entitlement["plan"]
        ),
        "subscription": dict(
            entitlement["subscription"]
        ),
        "access": dict(
            entitlement["access"]
        ),
        "limits": {
            "askJm8": {
                "monthly": limits[
                    OPERATION_ASK_JM8
                ],
            },
            "entryAnalysis": {
                "monthly": limits[
                    OPERATION_ENTRY_ANALYSIS
                ],
            },
        },
        "updatedAt": entitlement[
            "updatedAt"
        ],
    }

    _log_account_entitlement_event(
        "account_entitlement_read",
        plan=projection[
            "plan"
        ]["id"],
        status=projection[
            "subscription"
        ]["status"],
        isPro=projection[
            "access"
        ]["isPro"],
        askMonthlyLimit=projection[
            "limits"
        ]["askJm8"]["monthly"],
        entryAnalysisMonthlyLimit=(
            projection[
                "limits"
            ]["entryAnalysis"]["monthly"]
        ),
    )

    return projection
