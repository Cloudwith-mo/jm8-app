from __future__ import annotations

import re
import uuid
from datetime import (
    datetime,
    timedelta,
)
from typing import (
    Any,
    Mapping,
)

from botocore.exceptions import (
    ClientError,
)

from storage import (
    TABLE_NAME,
    dynamodb_client,
    serialize_attribute_map,
    table,
    user_pk,
)
from usage_policy import (
    COUNTER_FIELDS,
    OPERATION_ASK_JM8,
    OPERATION_ENTRY_ANALYSIS,
    USAGE_POLICY_VERSION,
    evaluate_quota,
    get_plan_limits,
    monthly_usage_period,
    normalize_now,
    normalize_operation,
    normalize_plan,
)


USAGE_ITEM_TYPE = "MONTHLY_USAGE"
USAGE_RESERVATION_ITEM_TYPE = (
    "USAGE_RESERVATION"
)

RESERVATION_STATUS_RESERVED = (
    "RESERVED"
)

RESERVATION_LEASE_SECONDS = 15 * 60

RESERVATION_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]{8,64}$"
)

PERIOD_PATTERN = re.compile(
    r"^\d{4}-\d{2}$"
)

CONSUMED_COUNTER_FIELDS = {
    OPERATION_ASK_JM8: (
        "askJm8Consumed"
    ),
    OPERATION_ENTRY_ANALYSIS: (
        "entryAnalysisConsumed"
    ),
}

RETRYABLE_DYNAMODB_ERRORS = {
    "InternalServerError",
    "ProvisionedThroughputExceededException",
    "RequestLimitExceeded",
    "ServiceUnavailable",
    "ThrottlingException",
    "TransactionConflictException",
}


class UsageStoreError(
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

        self.code = code
        self.message = message
        self.retryable = retryable


class UsageLimitExceededError(
    UsageStoreError
):
    def __init__(
        self,
        decision: Mapping[str, Any],
    ):
        super().__init__(
            "UsageLimitExceeded",
            (
                "The monthly usage limit "
                "has been reached."
            ),
            retryable=False,
        )

        self.decision = dict(decision)


class UsageReservationConflictError(
    UsageStoreError
):
    def __init__(
        self,
    ):
        super().__init__(
            "UsageReservationConflict",
            (
                "The usage reservation "
                "already exists or could "
                "not be created."
            ),
            retryable=False,
        )


class UsageReservationStateError(
    UsageStoreError
):
    def __init__(
        self,
    ):
        super().__init__(
            "InvalidUsageReservationState",
            (
                "The usage reservation "
                "does not exist or has "
                "already been finalized."
            ),
            retryable=False,
        )


def usage_sk(
    period: str,
) -> str:
    normalized = str(
        period or ""
    ).strip()

    if not PERIOD_PATTERN.fullmatch(
        normalized
    ):
        raise UsageStoreError(
            "InvalidUsagePeriod",
            (
                "The usage period must "
                "use YYYY-MM format."
            ),
        )

    return f"USAGE#{normalized}"


def usage_reservation_sk(
    period: str,
    reservation_id: str,
) -> str:
    normalized_id = (
        normalize_reservation_id(
            reservation_id
        )
    )

    return (
        "USAGE_RESERVATION#"
        f"{str(period).strip()}#"
        f"{normalized_id}"
    )


def new_usage_reservation_id() -> str:
    return (
        f"usage_{uuid.uuid4().hex[:24]}"
    )


def normalize_reservation_id(
    reservation_id: Any,
) -> str:
    normalized = str(
        reservation_id or ""
    ).strip()

    if not RESERVATION_ID_PATTERN.fullmatch(
        normalized
    ):
        raise UsageStoreError(
            "InvalidUsageReservationId",
            (
                "The usage reservation ID "
                "is invalid."
            ),
        )

    return normalized


def normalize_user_id(
    user_id: Any,
) -> str:
    normalized = str(
        user_id or ""
    ).strip()

    if (
        not normalized
        or len(normalized) > 256
    ):
        raise UsageStoreError(
            "InvalidUsageUser",
            (
                "A valid authenticated "
                "user is required."
            ),
        )

    return normalized


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


def _transaction_token(
    action: str,
    reservation_id: str,
) -> str:
    return (
        f"{action}-{reservation_id}"
    )[:36]


def get_monthly_usage_item(
    user_id: Any,
    *,
    now: datetime | None = None,
    table_resource=None,
) -> dict[str, Any]:
    normalized_user_id = (
        normalize_user_id(user_id)
    )

    period = monthly_usage_period(
        now
    )["key"]

    resource = (
        table_resource
        if table_resource is not None
        else table
    )

    result = resource.get_item(
        Key={
            "PK": user_pk(
                normalized_user_id
            ),
            "SK": usage_sk(period),
        },
        ConsistentRead=True,
    )

    item = result.get("Item")

    if not isinstance(item, dict):
        return {}

    return item


def reserve_monthly_usage(
    user_id: Any,
    *,
    plan: Any,
    operation: Any,
    reservation_id: str | None = None,
    now: datetime | None = None,
    environ: (
        Mapping[str, str] | None
    ) = None,
    client=None,
) -> dict[str, Any]:
    normalized_user_id = (
        normalize_user_id(user_id)
    )

    normalized_plan = normalize_plan(
        plan
    )

    normalized_operation = (
        normalize_operation(operation)
    )

    current = normalize_now(now)

    period = monthly_usage_period(
        current
    )

    limits = get_plan_limits(
        normalized_plan,
        environ=environ,
    )

    limit = limits[
        normalized_operation
    ]

    normalized_reservation_id = (
        normalize_reservation_id(
            reservation_id
            or new_usage_reservation_id()
        )
    )

    if limit <= 0:
        decision = evaluate_quota(
            plan=normalized_plan,
            operation=(
                normalized_operation
            ),
            usage_item={},
            now=current,
            environ=environ,
        )

        raise UsageLimitExceededError(
            decision
        )

    counters = COUNTER_FIELDS[
        normalized_operation
    ]

    consumed_field = (
        CONSUMED_COUNTER_FIELDS[
            normalized_operation
        ]
    )

    now_value = current.isoformat()

    expires_at = int(
        (
            current
            + timedelta(
                seconds=(
                    RESERVATION_LEASE_SECONDS
                )
            )
        ).timestamp()
    )

    usage_key = {
        "PK": user_pk(
            normalized_user_id
        ),
        "SK": usage_sk(
            period["key"]
        ),
    }

    reservation_item = {
        "PK": user_pk(
            normalized_user_id
        ),
        "SK": usage_reservation_sk(
            period["key"],
            normalized_reservation_id,
        ),
        "entityType": (
            USAGE_RESERVATION_ITEM_TYPE
        ),
        "reservationId": (
            normalized_reservation_id
        ),
        "period": period["key"],
        "operation": (
            normalized_operation
        ),
        "planSnapshot": (
            normalized_plan
        ),
        "status": (
            RESERVATION_STATUS_RESERVED
        ),
        "createdAt": now_value,
        "expiresAt": expires_at,
    }

    expression_names = {
        "#entityType": "entityType",
        "#usageVersion": (
            "usageVersion"
        ),
        "#period": "period",
        "#periodStartsAt": (
            "periodStartsAt"
        ),
        "#resetsAt": "resetsAt",
        "#planSnapshot": (
            "planSnapshot"
        ),
        "#createdAt": "createdAt",
        "#updatedAt": "updatedAt",
        "#reserved": (
            counters["reserved"]
        ),
        "#consumed": consumed_field,
    }

    expression_values = (
        serialize_attribute_map(
            {
                ":usageType": (
                    USAGE_ITEM_TYPE
                ),
                ":usageVersion": (
                    USAGE_POLICY_VERSION
                ),
                ":period": period["key"],
                ":periodStartsAt": (
                    period["startsAt"]
                ),
                ":resetsAt": (
                    period["resetsAt"]
                ),
                ":planSnapshot": (
                    normalized_plan
                ),
                ":now": now_value,
                ":one": 1,
                ":zero": 0,
                ":limit": limit,
            }
        )
    )

    transaction = [
        {
            "Update": {
                "TableName": TABLE_NAME,
                "Key": (
                    serialize_attribute_map(
                        usage_key
                    )
                ),
                "UpdateExpression": (
                    "SET "
                    "#entityType = "
                    "if_not_exists("
                    "#entityType, :usageType"
                    "), "
                    "#usageVersion = "
                    ":usageVersion, "
                    "#period = :period, "
                    "#periodStartsAt = "
                    ":periodStartsAt, "
                    "#resetsAt = :resetsAt, "
                    "#planSnapshot = "
                    ":planSnapshot, "
                    "#createdAt = "
                    "if_not_exists("
                    "#createdAt, :now"
                    "), "
                    "#updatedAt = :now "
                    "ADD "
                    "#reserved :one, "
                    "#consumed :one"
                ),
                "ConditionExpression": (
                    ":limit > :zero AND "
                    "("
                    "attribute_not_exists("
                    "#consumed"
                    ") OR "
                    "#consumed < :limit"
                    ")"
                ),
                "ExpressionAttributeNames": (
                    expression_names
                ),
                "ExpressionAttributeValues": (
                    expression_values
                ),
            },
        },
        {
            "Put": {
                "TableName": TABLE_NAME,
                "Item": (
                    serialize_attribute_map(
                        reservation_item
                    )
                ),
                "ConditionExpression": (
                    "attribute_not_exists(PK) "
                    "AND "
                    "attribute_not_exists(SK)"
                ),
            },
        },
    ]

    dynamodb = (
        client
        if client is not None
        else dynamodb_client
    )

    try:
        dynamodb.transact_write_items(
            TransactItems=transaction,
            ClientRequestToken=(
                _transaction_token(
                    "reserve",
                    normalized_reservation_id,
                )
            ),
        )
    except ClientError as error:
        error_code = _client_error_code(
            error
        )

        if (
            error_code
            == "TransactionCanceledException"
        ):
            usage_item = (
                get_monthly_usage_item(
                    normalized_user_id,
                    now=current,
                )
            )

            decision = evaluate_quota(
                plan=normalized_plan,
                operation=(
                    normalized_operation
                ),
                usage_item=usage_item,
                now=current,
                environ=environ,
            )

            if not decision["allowed"]:
                raise (
                    UsageLimitExceededError(
                        decision
                    )
                ) from error

            raise (
                UsageReservationConflictError()
            ) from error

        raise UsageStoreError(
            error_code,
            (
                "The usage reservation "
                "could not be created."
            ),
            retryable=(
                error_code
                in RETRYABLE_DYNAMODB_ERRORS
            ),
        ) from error

    return {
        "reservationId": (
            normalized_reservation_id
        ),
        "period": period["key"],
        "operation": (
            normalized_operation
        ),
        "plan": normalized_plan,
        "status": (
            RESERVATION_STATUS_RESERVED
        ),
        "createdAt": now_value,
        "expiresAt": expires_at,
    }


def _reservation_context(
    reservation: Mapping[str, Any],
) -> dict[str, str]:
    if not isinstance(
        reservation,
        Mapping,
    ):
        raise UsageStoreError(
            "InvalidUsageReservation",
            (
                "A valid usage reservation "
                "is required."
            ),
        )

    reservation_id = (
        normalize_reservation_id(
            reservation.get(
                "reservationId"
            )
        )
    )

    period = str(
        reservation.get(
            "period",
            "",
        )
    ).strip()

    # Validate through the aggregate key
    # builder before using the period.
    usage_sk(period)

    operation = normalize_operation(
        reservation.get("operation")
    )

    return {
        "reservationId": reservation_id,
        "period": period,
        "operation": operation,
    }


def _finalize_usage_reservation(
    user_id: Any,
    *,
    reservation: Mapping[str, Any],
    outcome: str,
    now: datetime | None = None,
    client=None,
) -> dict[str, Any]:
    normalized_user_id = (
        normalize_user_id(user_id)
    )

    context = _reservation_context(
        reservation
    )

    current = normalize_now(now)

    counters = COUNTER_FIELDS[
        context["operation"]
    ]

    consumed_field = (
        CONSUMED_COUNTER_FIELDS[
            context["operation"]
        ]
    )

    if outcome == "COMPLETED":
        update_expression = (
            "SET #updatedAt = :now "
            "ADD "
            "#reserved :minusOne, "
            "#completed :one"
        )

        expression_names = {
            "#updatedAt": "updatedAt",
            "#reserved": (
                counters["reserved"]
            ),
            "#completed": (
                counters["completed"]
            ),
            "#consumed": (
                consumed_field
            ),
        }
    elif outcome == "FAILED":
        update_expression = (
            "SET #updatedAt = :now "
            "ADD "
            "#reserved :minusOne, "
            "#failed :one, "
            "#consumed :minusOne"
        )

        expression_names = {
            "#updatedAt": "updatedAt",
            "#reserved": (
                counters["reserved"]
            ),
            "#failed": (
                counters["failed"]
            ),
            "#consumed": (
                consumed_field
            ),
        }
    else:
        raise UsageStoreError(
            "InvalidUsageOutcome",
            (
                "The usage reservation "
                "outcome is invalid."
            ),
        )

    usage_values = (
        serialize_attribute_map(
            {
                ":now": (
                    current.isoformat()
                ),
                ":one": 1,
                ":minusOne": -1,
            }
        )
    )

    reservation_values = (
        serialize_attribute_map(
            {
                ":operation": (
                    context["operation"]
                ),
                ":reservedStatus": (
                    RESERVATION_STATUS_RESERVED
                ),
            }
        )
    )

    transaction = [
        {
            "Delete": {
                "TableName": TABLE_NAME,
                "Key": (
                    serialize_attribute_map(
                        {
                            "PK": user_pk(
                                normalized_user_id
                            ),
                            "SK": (
                                usage_reservation_sk(
                                    context[
                                        "period"
                                    ],
                                    context[
                                        "reservationId"
                                    ],
                                )
                            ),
                        }
                    )
                ),
                "ConditionExpression": (
                    "attribute_exists(PK) "
                    "AND "
                    "attribute_exists(SK) "
                    "AND "
                    "#operation = :operation "
                    "AND "
                    "#status = "
                    ":reservedStatus"
                ),
                "ExpressionAttributeNames": {
                    "#operation": (
                        "operation"
                    ),
                    "#status": "status",
                },
                "ExpressionAttributeValues": (
                    reservation_values
                ),
            },
        },
        {
            "Update": {
                "TableName": TABLE_NAME,
                "Key": (
                    serialize_attribute_map(
                        {
                            "PK": user_pk(
                                normalized_user_id
                            ),
                            "SK": usage_sk(
                                context["period"]
                            ),
                        }
                    )
                ),
                "UpdateExpression": (
                    update_expression
                ),
                "ConditionExpression": (
                    "attribute_exists(PK) "
                    "AND "
                    "attribute_exists(SK) "
                    "AND "
                    "#reserved >= :one "
                    "AND "
                    "#consumed >= :one"
                ),
                "ExpressionAttributeNames": (
                    expression_names
                ),
                "ExpressionAttributeValues": (
                    usage_values
                ),
            },
        },
    ]

    dynamodb = (
        client
        if client is not None
        else dynamodb_client
    )

    try:
        dynamodb.transact_write_items(
            TransactItems=transaction,
            ClientRequestToken=(
                _transaction_token(
                    outcome.lower(),
                    context[
                        "reservationId"
                    ],
                )
            ),
        )
    except ClientError as error:
        error_code = _client_error_code(
            error
        )

        if (
            error_code
            == "TransactionCanceledException"
        ):
            raise (
                UsageReservationStateError()
            ) from error

        raise UsageStoreError(
            error_code,
            (
                "The usage reservation "
                "could not be finalized."
            ),
            retryable=(
                error_code
                in RETRYABLE_DYNAMODB_ERRORS
            ),
        ) from error

    return {
        "reservationId": (
            context["reservationId"]
        ),
        "period": context["period"],
        "operation": (
            context["operation"]
        ),
        "status": outcome,
        "finalizedAt": (
            current.isoformat()
        ),
    }


def complete_usage_reservation(
    user_id: Any,
    *,
    reservation: Mapping[str, Any],
    now: datetime | None = None,
    client=None,
) -> dict[str, Any]:
    return _finalize_usage_reservation(
        user_id,
        reservation=reservation,
        outcome="COMPLETED",
        now=now,
        client=client,
    )


def fail_usage_reservation(
    user_id: Any,
    *,
    reservation: Mapping[str, Any],
    now: datetime | None = None,
    client=None,
) -> dict[str, Any]:
    return _finalize_usage_reservation(
        user_id,
        reservation=reservation,
        outcome="FAILED",
        now=now,
        client=client,
    )
