from __future__ import annotations

import os
from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
from typing import (
    Any,
    Mapping,
)


USAGE_POLICY_VERSION = "1.0"

PLAN_FREE = "FREE"
PLAN_PRO = "PRO"
DEFAULT_PLAN = PLAN_FREE

SUPPORTED_PLANS = (
    PLAN_FREE,
    PLAN_PRO,
)

PLAN_LABELS = {
    PLAN_FREE: "Free",
    PLAN_PRO: "Pro",
}

OPERATION_ASK_JM8 = "ASK_JM8"
OPERATION_ENTRY_ANALYSIS = (
    "ENTRY_ANALYSIS"
)

SUPPORTED_OPERATIONS = (
    OPERATION_ASK_JM8,
    OPERATION_ENTRY_ANALYSIS,
)

PUBLIC_OPERATION_KEYS = {
    OPERATION_ASK_JM8: "askJm8",
    OPERATION_ENTRY_ANALYSIS: (
        "entryAnalysis"
    ),
}

OPERATION_LABELS = {
    OPERATION_ASK_JM8: "Ask JM8",
    OPERATION_ENTRY_ANALYSIS: (
        "entry analysis"
    ),
}

DEFAULT_LIMITS = {
    PLAN_FREE: {
        OPERATION_ASK_JM8: 5,
        OPERATION_ENTRY_ANALYSIS: 10,
    },
    PLAN_PRO: {
        OPERATION_ASK_JM8: 100,
        OPERATION_ENTRY_ANALYSIS: 250,
    },
}

LIMIT_ENVIRONMENT_VARIABLES = {
    PLAN_FREE: {
        OPERATION_ASK_JM8: (
            "FREE_MONTHLY_ASK_QUESTIONS"
        ),
        OPERATION_ENTRY_ANALYSIS: (
            "FREE_MONTHLY_ENTRY_ANALYSES"
        ),
    },
    PLAN_PRO: {
        OPERATION_ASK_JM8: (
            "PRO_MONTHLY_ASK_QUESTIONS"
        ),
        OPERATION_ENTRY_ANALYSIS: (
            "PRO_MONTHLY_ENTRY_ANALYSES"
        ),
    },
}

COUNTER_FIELDS = {
    OPERATION_ASK_JM8: {
        "completed": (
            "askJm8Completed"
        ),
        "reserved": (
            "askJm8Reserved"
        ),
        "failed": (
            "askJm8Failed"
        ),
    },
    OPERATION_ENTRY_ANALYSIS: {
        "completed": (
            "entryAnalysisCompleted"
        ),
        "reserved": (
            "entryAnalysisReserved"
        ),
        "failed": (
            "entryAnalysisFailed"
        ),
    },
}

MAX_CONFIGURED_LIMIT = 1_000_000


class UsagePolicyError(
    ValueError
):
    def __init__(
        self,
        code: str,
        message: str,
    ):
        super().__init__(message)

        self.code = code
        self.message = message


def normalize_plan(
    plan: Any,
) -> str:
    normalized = str(
        plan or ""
    ).strip().upper()

    if normalized in SUPPORTED_PLANS:
        return normalized

    # Unknown or missing entitlements must
    # fail safely to the Free plan.
    return DEFAULT_PLAN


def normalize_operation(
    operation: Any,
) -> str:
    normalized = str(
        operation or ""
    ).strip().upper()

    if normalized not in (
        SUPPORTED_OPERATIONS
    ):
        raise UsagePolicyError(
            "InvalidUsageOperation",
            (
                "The requested usage "
                "operation is not supported."
            ),
        )

    return normalized


def normalize_now(
    now: datetime | None = None,
) -> datetime:
    current = now or datetime.now(
        timezone.utc
    )

    if current.tzinfo is None:
        current = current.replace(
            tzinfo=timezone.utc
        )

    return current.astimezone(
        timezone.utc
    )


def monthly_usage_period(
    now: datetime | None = None,
) -> dict[str, str]:
    current = normalize_now(now)

    starts_at = datetime(
        current.year,
        current.month,
        1,
        tzinfo=timezone.utc,
    )

    if current.month == 12:
        resets_at = datetime(
            current.year + 1,
            1,
            1,
            tzinfo=timezone.utc,
        )
    else:
        resets_at = datetime(
            current.year,
            current.month + 1,
            1,
            tzinfo=timezone.utc,
        )

    return {
        "key": (
            f"{current.year:04d}-"
            f"{current.month:02d}"
        ),
        "startsAt": (
            starts_at.isoformat()
        ),
        "endsAt": (
            resets_at.isoformat()
        ),
        "resetsAt": (
            resets_at.isoformat()
        ),
    }


def _parse_limit(
    raw_value: Any,
    *,
    environment_name: str,
    fallback: int,
) -> int:
    if raw_value is None:
        return fallback

    cleaned = str(
        raw_value
    ).strip()

    if not cleaned:
        return fallback

    try:
        parsed = int(cleaned)
    except (
        TypeError,
        ValueError,
    ) as error:
        raise UsagePolicyError(
            "InvalidUsageLimit",
            (
                f"{environment_name} must "
                "be a whole number."
            ),
        ) from error

    if (
        parsed < 0
        or parsed
        > MAX_CONFIGURED_LIMIT
    ):
        raise UsagePolicyError(
            "InvalidUsageLimit",
            (
                f"{environment_name} must "
                "be between 0 and "
                f"{MAX_CONFIGURED_LIMIT}."
            ),
        )

    return parsed


def get_plan_limits(
    plan: Any,
    *,
    environ: (
        Mapping[str, str] | None
    ) = None,
) -> dict[str, int]:
    normalized_plan = normalize_plan(
        plan
    )

    source = (
        environ
        if environ is not None
        else os.environ
    )

    defaults = DEFAULT_LIMITS[
        normalized_plan
    ]

    environment_names = (
        LIMIT_ENVIRONMENT_VARIABLES[
            normalized_plan
        ]
    )

    return {
        operation: _parse_limit(
            source.get(
                environment_names[
                    operation
                ]
            ),
            environment_name=(
                environment_names[
                    operation
                ]
            ),
            fallback=defaults[
                operation
            ],
        )
        for operation in (
            SUPPORTED_OPERATIONS
        )
    }


def _non_negative_integer(
    value: Any,
) -> int:
    if value is None:
        return 0

    if isinstance(value, bool):
        return 0

    if isinstance(value, Decimal):
        if (
            value
            != value.to_integral_value()
        ):
            return 0

        parsed = int(value)
    elif isinstance(value, float):
        if not value.is_integer():
            return 0

        parsed = int(value)
    else:
        try:
            parsed = int(value)
        except (
            TypeError,
            ValueError,
        ):
            return 0

    return max(parsed, 0)


def build_operation_usage(
    usage_item: Mapping[str, Any],
    *,
    operation: Any,
    limit: int,
) -> dict[str, Any]:
    normalized_operation = (
        normalize_operation(operation)
    )

    fields = COUNTER_FIELDS[
        normalized_operation
    ]

    completed = (
        _non_negative_integer(
            usage_item.get(
                fields["completed"]
            )
        )
    )

    reserved = (
        _non_negative_integer(
            usage_item.get(
                fields["reserved"]
            )
        )
    )

    failed = (
        _non_negative_integer(
            usage_item.get(
                fields["failed"]
            )
        )
    )

    consumed_capacity = (
        completed + reserved
    )

    remaining = max(
        int(limit)
        - consumed_capacity,
        0,
    )

    return {
        "used": completed,
        "reserved": reserved,
        "failed": failed,
        "limit": int(limit),
        "remaining": remaining,
        "allowed": remaining > 0,
    }


def build_usage_snapshot(
    *,
    plan: Any,
    usage_item: (
        Mapping[str, Any] | None
    ) = None,
    now: datetime | None = None,
    environ: (
        Mapping[str, str] | None
    ) = None,
) -> dict[str, Any]:
    current = normalize_now(now)

    normalized_plan = normalize_plan(
        plan
    )

    period = monthly_usage_period(
        current
    )

    limits = get_plan_limits(
        normalized_plan,
        environ=environ,
    )

    source_item = usage_item or {}

    operations = {
        PUBLIC_OPERATION_KEYS[
            operation
        ]: build_operation_usage(
            source_item,
            operation=operation,
            limit=limits[operation],
        )
        for operation in (
            SUPPORTED_OPERATIONS
        )
    }

    return {
        "usageVersion": (
            USAGE_POLICY_VERSION
        ),
        "generatedAt": (
            current.isoformat()
        ),
        "period": period,
        "plan": {
            "id": normalized_plan,
            "label": PLAN_LABELS[
                normalized_plan
            ],
        },
        "operations": operations,
    }


def evaluate_quota(
    *,
    plan: Any,
    operation: Any,
    usage_item: (
        Mapping[str, Any] | None
    ) = None,
    now: datetime | None = None,
    environ: (
        Mapping[str, str] | None
    ) = None,
) -> dict[str, Any]:
    normalized_operation = (
        normalize_operation(operation)
    )

    snapshot = build_usage_snapshot(
        plan=plan,
        usage_item=usage_item,
        now=now,
        environ=environ,
    )

    public_key = (
        PUBLIC_OPERATION_KEYS[
            normalized_operation
        ]
    )

    operation_usage = (
        snapshot["operations"][
            public_key
        ]
    )

    return {
        "usageVersion": (
            snapshot["usageVersion"]
        ),
        "allowed": (
            operation_usage["allowed"]
        ),
        "operation": (
            normalized_operation
        ),
        "operationKey": public_key,
        "plan": (
            snapshot["plan"]["id"]
        ),
        "period": (
            snapshot["period"]["key"]
        ),
        "limit": (
            operation_usage["limit"]
        ),
        "used": (
            operation_usage["used"]
        ),
        "reserved": (
            operation_usage["reserved"]
        ),
        "failed": (
            operation_usage["failed"]
        ),
        "remaining": (
            operation_usage["remaining"]
        ),
        "resetsAt": (
            snapshot["period"][
                "resetsAt"
            ]
        ),
    }


def build_usage_limit_error(
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    if decision.get("allowed") is True:
        raise UsagePolicyError(
            "UsageLimitNotExceeded",
            (
                "A limit response cannot "
                "be created for an allowed "
                "request."
            ),
        )

    operation = normalize_operation(
        decision.get("operation")
    )

    plan = normalize_plan(
        decision.get("plan")
    )

    return {
        "error": "UsageLimitExceeded",
        "message": (
            f"Your monthly "
            f"{OPERATION_LABELS[operation]} "
            "limit has been reached."
        ),
        "operation": (
            PUBLIC_OPERATION_KEYS[
                operation
            ]
        ),
        "plan": plan,
        "period": str(
            decision.get(
                "period",
                "",
            )
        ),
        "limit": (
            _non_negative_integer(
                decision.get("limit")
            )
        ),
        "used": (
            _non_negative_integer(
                decision.get("used")
            )
        ),
        "reserved": (
            _non_negative_integer(
                decision.get(
                    "reserved"
                )
            )
        ),
        "remaining": 0,
        "resetsAt": str(
            decision.get(
                "resetsAt",
                "",
            )
        ),
        "upgradeRequired": (
            plan == PLAN_FREE
        ),
    }
