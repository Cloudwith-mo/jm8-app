from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    Mapping,
)

from usage_policy import (
    PLAN_FREE,
    PLAN_LABELS,
    PLAN_PRO,
)


ENTITLEMENT_VERSION = "1.0"

ENTITLEMENT_ENTITY_TYPE = (
    "USER_ENTITLEMENT"
)

ENTITLEMENT_SK = "ENTITLEMENT"

STATUS_FREE = "FREE"
STATUS_TRIALING = "TRIALING"
STATUS_ACTIVE = "ACTIVE"
STATUS_PAST_DUE = "PAST_DUE"
STATUS_CANCELED = "CANCELED"
STATUS_EXPIRED = "EXPIRED"

SUPPORTED_ENTITLEMENT_STATUSES = (
    STATUS_FREE,
    STATUS_TRIALING,
    STATUS_ACTIVE,
    STATUS_PAST_DUE,
    STATUS_CANCELED,
    STATUS_EXPIRED,
)

SOURCE_DEFAULT = "DEFAULT"
SOURCE_SYSTEM = "SYSTEM"
SOURCE_STRIPE = "STRIPE"

SUPPORTED_ENTITLEMENT_SOURCES = (
    SOURCE_DEFAULT,
    SOURCE_SYSTEM,
    SOURCE_STRIPE,
)

OPEN_ENDED_PRO_STATUSES = {
    STATUS_TRIALING,
    STATUS_ACTIVE,
}

WINDOWED_PRO_STATUSES = {
    STATUS_PAST_DUE,
}


class EntitlementPolicyError(
    ValueError
):
    def __init__(
        self,
        code: str,
        message: str,
    ):
        super().__init__(message)

        self.code = str(code)
        self.message = str(message)


def _raise_policy_error(
    code: str,
    message: str,
) -> None:
    raise EntitlementPolicyError(
        code,
        message,
    )


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


def _normalize_plan_strict(
    value: Any,
) -> str:
    normalized = str(
        value or ""
    ).strip().upper()

    if normalized not in {
        PLAN_FREE,
        PLAN_PRO,
    }:
        _raise_policy_error(
            "InvalidEntitlementPlan",
            (
                "The entitlement plan "
                "is not supported."
            ),
        )

    return normalized


def _normalize_status_strict(
    value: Any,
) -> str:
    normalized = str(
        value or ""
    ).strip().upper()

    if normalized not in (
        SUPPORTED_ENTITLEMENT_STATUSES
    ):
        _raise_policy_error(
            "InvalidEntitlementStatus",
            (
                "The entitlement status "
                "is not supported."
            ),
        )

    return normalized


def _normalize_source_strict(
    value: Any,
) -> str:
    normalized = str(
        value or ""
    ).strip().upper()

    if normalized not in (
        SUPPORTED_ENTITLEMENT_SOURCES
    ):
        _raise_policy_error(
            "InvalidEntitlementSource",
            (
                "The entitlement source "
                "is not supported."
            ),
        )

    return normalized


def _parse_access_timestamp(
    value: Any,
    *,
    field: str,
) -> datetime | None:
    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):
        parsed = value
    elif isinstance(
        value,
        str,
    ):
        cleaned = value.strip()

        if not cleaned:
            return None

        if cleaned.endswith("Z"):
            cleaned = (
                cleaned[:-1]
                + "+00:00"
            )

        try:
            parsed = datetime.fromisoformat(
                cleaned
            )
        except ValueError as error:
            raise EntitlementPolicyError(
                "InvalidEntitlementTimestamp",
                (
                    f"{field} must be a "
                    "valid ISO-8601 timestamp."
                ),
            ) from error
    else:
        _raise_policy_error(
            "InvalidEntitlementTimestamp",
            (
                f"{field} must be a "
                "valid ISO-8601 timestamp."
            ),
        )

    if parsed.tzinfo is None:
        _raise_policy_error(
            "InvalidEntitlementTimestamp",
            (
                f"{field} must include "
                "a timezone."
            ),
        )

    return parsed.astimezone(
        timezone.utc
    )


def _timestamp_or_none(
    value: datetime | None,
) -> str | None:
    if value is None:
        return None

    return value.isoformat()


def _validate_plan_status(
    *,
    plan: str,
    status: str,
) -> None:
    if (
        plan == PLAN_FREE
        and status != STATUS_FREE
    ):
        _raise_policy_error(
            "InvalidEntitlementState",
            (
                "The Free plan must use "
                "the FREE status."
            ),
        )

    if (
        plan == PLAN_PRO
        and status == STATUS_FREE
    ):
        _raise_policy_error(
            "InvalidEntitlementState",
            (
                "The Pro plan cannot use "
                "the FREE status."
            ),
        )


def build_entitlement_record(
    *,
    plan: Any,
    status: Any,
    source: Any,
    access_starts_at: Any = None,
    access_ends_at: Any = None,
    cancel_at_period_end: bool = False,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    normalized_plan = (
        _normalize_plan_strict(
            plan
        )
    )

    normalized_status = (
        _normalize_status_strict(
            status
        )
    )

    normalized_source = (
        _normalize_source_strict(
            source
        )
    )

    _validate_plan_status(
        plan=normalized_plan,
        status=normalized_status,
    )

    if not isinstance(
        cancel_at_period_end,
        bool,
    ):
        _raise_policy_error(
            "InvalidEntitlementCancellation",
            (
                "cancelAtPeriodEnd must "
                "be true or false."
            ),
        )

    starts_at = (
        _parse_access_timestamp(
            access_starts_at,
            field="accessStartsAt",
        )
    )

    ends_at = (
        _parse_access_timestamp(
            access_ends_at,
            field="accessEndsAt",
        )
    )

    if (
        starts_at is not None
        and ends_at is not None
        and ends_at <= starts_at
    ):
        _raise_policy_error(
            "InvalidEntitlementWindow",
            (
                "accessEndsAt must occur "
                "after accessStartsAt."
            ),
        )

    if normalized_plan == PLAN_FREE:
        if (
            starts_at is not None
            or ends_at is not None
            or cancel_at_period_end
        ):
            _raise_policy_error(
                "InvalidEntitlementState",
                (
                    "The Free plan cannot "
                    "contain a Pro access "
                    "window."
                ),
            )

    current = normalize_now(
        updated_at
    )

    return {
        "entityType": (
            ENTITLEMENT_ENTITY_TYPE
        ),
        "entitlementVersion": (
            ENTITLEMENT_VERSION
        ),
        "plan": normalized_plan,
        "status": normalized_status,
        "source": normalized_source,
        "accessStartsAt": (
            _timestamp_or_none(
                starts_at
            )
        ),
        "accessEndsAt": (
            _timestamp_or_none(
                ends_at
            )
        ),
        "cancelAtPeriodEnd": (
            cancel_at_period_end
        ),
        "updatedAt": (
            current.isoformat()
        ),
    }


def _default_entitlement(
    current: datetime,
) -> dict[str, Any]:
    return {
        "entitlementVersion": (
            ENTITLEMENT_VERSION
        ),
        "generatedAt": (
            current.isoformat()
        ),
        "plan": {
            "id": PLAN_FREE,
            "label": (
                PLAN_LABELS[
                    PLAN_FREE
                ]
            ),
        },
        "subscription": {
            "configuredPlan": (
                PLAN_FREE
            ),
            "status": STATUS_FREE,
            "source": SOURCE_DEFAULT,
            "cancelAtPeriodEnd": False,
        },
        "access": {
            "isPro": False,
            "startsAt": None,
            "endsAt": None,
        },
        "updatedAt": None,
    }


def resolve_effective_entitlement(
    entitlement_record: (
        Mapping[str, Any] | None
    ),
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = normalize_now(now)

    if not isinstance(
        entitlement_record,
        Mapping,
    ):
        return _default_entitlement(
            current
        )

    try:
        configured_plan = (
            _normalize_plan_strict(
                entitlement_record.get(
                    "plan"
                )
            )
        )

        status = (
            _normalize_status_strict(
                entitlement_record.get(
                    "status"
                )
            )
        )

        source = (
            _normalize_source_strict(
                entitlement_record.get(
                    "source"
                )
            )
        )

        _validate_plan_status(
            plan=configured_plan,
            status=status,
        )

        starts_at = (
            _parse_access_timestamp(
                entitlement_record.get(
                    "accessStartsAt"
                ),
                field="accessStartsAt",
            )
        )

        ends_at = (
            _parse_access_timestamp(
                entitlement_record.get(
                    "accessEndsAt"
                ),
                field="accessEndsAt",
            )
        )

        updated_at = (
            _parse_access_timestamp(
                entitlement_record.get(
                    "updatedAt"
                ),
                field="updatedAt",
            )
        )

        cancel_at_period_end = (
            entitlement_record.get(
                "cancelAtPeriodEnd",
                False,
            )
        )

        if not isinstance(
            cancel_at_period_end,
            bool,
        ):
            _raise_policy_error(
                (
                    "InvalidEntitlement"
                    "Cancellation"
                ),
                (
                    "cancelAtPeriodEnd must "
                    "be true or false."
                ),
            )

        if (
            starts_at is not None
            and ends_at is not None
            and ends_at <= starts_at
        ):
            _raise_policy_error(
                "InvalidEntitlementWindow",
                (
                    "The entitlement access "
                    "window is invalid."
                ),
            )

    except EntitlementPolicyError:
        return _default_entitlement(
            current
        )

    started = (
        starts_at is None
        or starts_at <= current
    )

    not_ended = (
        ends_at is None
        or current < ends_at
    )

    pro_access = False

    if (
        configured_plan == PLAN_PRO
        and started
        and not_ended
    ):
        if status in (
            OPEN_ENDED_PRO_STATUSES
        ):
            pro_access = True
        elif (
            status
            in WINDOWED_PRO_STATUSES
            and ends_at is not None
        ):
            pro_access = True

    effective_plan = (
        PLAN_PRO
        if pro_access
        else PLAN_FREE
    )

    return {
        "entitlementVersion": (
            ENTITLEMENT_VERSION
        ),
        "generatedAt": (
            current.isoformat()
        ),
        "plan": {
            "id": effective_plan,
            "label": (
                PLAN_LABELS[
                    effective_plan
                ]
            ),
        },
        "subscription": {
            "configuredPlan": (
                configured_plan
            ),
            "status": status,
            "source": source,
            "cancelAtPeriodEnd": (
                cancel_at_period_end
            ),
        },
        "access": {
            "isPro": pro_access,
            "startsAt": (
                _timestamp_or_none(
                    starts_at
                )
            ),
            "endsAt": (
                _timestamp_or_none(
                    ends_at
                )
            ),
        },
        "updatedAt": (
            _timestamp_or_none(
                updated_at
            )
        ),
    }
