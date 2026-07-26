from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from typing import (
    Any,
    Mapping,
)

from botocore.exceptions import (
    ClientError,
)

from entitlement_policy import (
    ENTITLEMENT_ENTITY_TYPE,
    ENTITLEMENT_SK,
    ENTITLEMENT_VERSION,
    EntitlementPolicyError,
    build_entitlement_record,
)
from storage import (
    table,
    user_pk,
)


RETRYABLE_DYNAMODB_ERRORS = {
    "InternalServerError",
    "ProvisionedThroughputExceededException",
    "RequestLimitExceeded",
    "ServiceUnavailable",
    "ThrottlingException",
    "TransactionConflictException",
}

CONDITIONAL_CHECK_FAILED = (
    "ConditionalCheckFailedException"
)

PUBLIC_ENTITLEMENT_RECORD_KEYS = {
    "entityType",
    "entitlementVersion",
    "plan",
    "status",
    "source",
    "accessStartsAt",
    "accessEndsAt",
    "cancelAtPeriodEnd",
    "updatedAt",
}


class EntitlementStoreError(
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

        self.code = str(code)
        self.message = str(message)
        self.retryable = bool(
            retryable
        )


class EntitlementConflictError(
    EntitlementStoreError
):
    def __init__(
        self,
    ):
        super().__init__(
            "EntitlementConflict",
            (
                "The entitlement changed "
                "before this update could "
                "be saved."
            ),
            retryable=False,
        )


def normalize_entitlement_user_id(
    user_id: Any,
) -> str:
    normalized = str(
        user_id or ""
    ).strip()

    if (
        not normalized
        or len(normalized) > 256
    ):
        raise EntitlementStoreError(
            "InvalidEntitlementUser",
            (
                "A valid authenticated "
                "user is required."
            ),
            retryable=False,
        )

    return normalized


def entitlement_key(
    user_id: Any,
) -> dict[str, str]:
    normalized_user_id = (
        normalize_entitlement_user_id(
            user_id
        )
    )

    return {
        "PK": user_pk(
            normalized_user_id
        ),
        "SK": ENTITLEMENT_SK,
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
    operation: str,
) -> None:
    code = _client_error_code(
        error
    )

    if (
        code
        == CONDITIONAL_CHECK_FAILED
    ):
        raise EntitlementConflictError() from error

    raise EntitlementStoreError(
        code,
        (
            "The user entitlement could "
            f"not be {operation}."
        ),
        retryable=(
            code
            in RETRYABLE_DYNAMODB_ERRORS
        ),
    ) from error


def _invalid_record(
    message: str = (
        "The stored entitlement record "
        "is invalid."
    ),
) -> EntitlementStoreError:
    return EntitlementStoreError(
        "InvalidEntitlementRecord",
        message,
        retryable=False,
    )


def _parse_required_timestamp(
    value: Any,
    *,
    field: str,
) -> datetime:
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
            raise _invalid_record(
                (
                    f"The stored {field} "
                    "timestamp is missing."
                )
            )

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
            raise _invalid_record(
                (
                    f"The stored {field} "
                    "timestamp is invalid."
                )
            ) from error
    else:
        raise _invalid_record(
            (
                f"The stored {field} "
                "timestamp is invalid."
            )
        )

    if parsed.tzinfo is None:
        raise _invalid_record(
            (
                f"The stored {field} "
                "timestamp must include "
                "a timezone."
            )
        )

    return parsed.astimezone(
        timezone.utc
    )


def normalize_stored_entitlement(
    item: Mapping[str, Any],
    *,
    expected_key: (
        Mapping[str, str] | None
    ) = None,
) -> dict[str, Any]:
    if not isinstance(
        item,
        Mapping,
    ):
        raise _invalid_record()

    if expected_key is not None:
        if (
            item.get("PK")
            != expected_key.get("PK")
            or item.get("SK")
            != expected_key.get("SK")
        ):
            raise _invalid_record(
                (
                    "The stored entitlement "
                    "ownership key is invalid."
                )
            )

    if (
        item.get("entityType")
        != ENTITLEMENT_ENTITY_TYPE
    ):
        raise _invalid_record(
            (
                "The stored entitlement "
                "entity type is invalid."
            )
        )

    if (
        item.get(
            "entitlementVersion"
        )
        != ENTITLEMENT_VERSION
    ):
        raise _invalid_record(
            (
                "The stored entitlement "
                "version is invalid."
            )
        )

    updated_at = (
        _parse_required_timestamp(
            item.get("updatedAt"),
            field="updatedAt",
        )
    )

    try:
        normalized = (
            build_entitlement_record(
                plan=item.get("plan"),
                status=item.get(
                    "status"
                ),
                source=item.get(
                    "source"
                ),
                access_starts_at=(
                    item.get(
                        "accessStartsAt"
                    )
                ),
                access_ends_at=(
                    item.get(
                        "accessEndsAt"
                    )
                ),
                cancel_at_period_end=(
                    item.get(
                        "cancelAtPeriodEnd",
                        False,
                    )
                ),
                updated_at=updated_at,
            )
        )

    except EntitlementPolicyError as error:
        raise _invalid_record() from error

    return {
        key: normalized[key]
        for key in (
            PUBLIC_ENTITLEMENT_RECORD_KEYS
        )
    }


def get_entitlement_record(
    user_id: Any,
    *,
    table_resource=None,
) -> dict[str, Any] | None:
    key = entitlement_key(
        user_id
    )

    resource = (
        table_resource
        if table_resource is not None
        else table
    )

    try:
        result = resource.get_item(
            Key=key,
            ConsistentRead=True,
        )

    except ClientError as error:
        _raise_client_error(
            error,
            operation="retrieved",
        )

    item = result.get("Item")

    if item is None:
        return None

    if not isinstance(
        item,
        Mapping,
    ):
        raise _invalid_record()

    return normalize_stored_entitlement(
        item,
        expected_key=key,
    )


def create_entitlement_record(
    user_id: Any,
    *,
    plan: Any,
    status: Any,
    source: Any,
    access_starts_at: Any = None,
    access_ends_at: Any = None,
    cancel_at_period_end: bool = False,
    now: datetime | None = None,
    table_resource=None,
) -> dict[str, Any]:
    key = entitlement_key(
        user_id
    )

    try:
        record = build_entitlement_record(
            plan=plan,
            status=status,
            source=source,
            access_starts_at=(
                access_starts_at
            ),
            access_ends_at=(
                access_ends_at
            ),
            cancel_at_period_end=(
                cancel_at_period_end
            ),
            updated_at=now,
        )

    except EntitlementPolicyError as error:
        raise EntitlementStoreError(
            "InvalidEntitlementRecord",
            (
                "The entitlement update "
                "is invalid."
            ),
            retryable=False,
        ) from error

    item = {
        **key,
        **record,
    }

    resource = (
        table_resource
        if table_resource is not None
        else table
    )

    try:
        resource.put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(#sk)"
            ),
            ExpressionAttributeNames={
                "#sk": "SK",
            },
        )

    except ClientError as error:
        _raise_client_error(
            error,
            operation="created",
        )

    return dict(record)


def replace_entitlement_record(
    user_id: Any,
    *,
    expected_updated_at: Any,
    plan: Any,
    status: Any,
    source: Any,
    access_starts_at: Any = None,
    access_ends_at: Any = None,
    cancel_at_period_end: bool = False,
    now: datetime | None = None,
    table_resource=None,
) -> dict[str, Any]:
    key = entitlement_key(
        user_id
    )

    expected_timestamp = (
        _parse_required_timestamp(
            expected_updated_at,
            field="expectedUpdatedAt",
        ).isoformat()
    )

    try:
        record = build_entitlement_record(
            plan=plan,
            status=status,
            source=source,
            access_starts_at=(
                access_starts_at
            ),
            access_ends_at=(
                access_ends_at
            ),
            cancel_at_period_end=(
                cancel_at_period_end
            ),
            updated_at=now,
        )

    except EntitlementPolicyError as error:
        raise EntitlementStoreError(
            "InvalidEntitlementRecord",
            (
                "The entitlement update "
                "is invalid."
            ),
            retryable=False,
        ) from error

    item = {
        **key,
        **record,
    }

    resource = (
        table_resource
        if table_resource is not None
        else table
    )

    try:
        resource.put_item(
            Item=item,
            ConditionExpression=(
                "#updatedAt = "
                ":expectedUpdatedAt"
            ),
            ExpressionAttributeNames={
                "#updatedAt": "updatedAt",
            },
            ExpressionAttributeValues={
                ":expectedUpdatedAt": (
                    expected_timestamp
                ),
            },
        )

    except ClientError as error:
        _raise_client_error(
            error,
            operation="replaced",
        )

    return dict(record)
