from __future__ import annotations

import re
from datetime import (
    datetime,
    timezone,
)
from hashlib import (
    sha256,
)
from typing import (
    Any,
    Mapping,
)

from botocore.exceptions import (
    ClientError,
)

from billing_identity import (
    BillingIdentityError,
    normalize_billing_user_id,
)
from storage import (
    TABLE_NAME,
    dynamodb_client,
    serialize_attribute_map,
    table,
    user_pk,
)


BILLING_CUSTOMER_MAPPING_VERSION = (
    "1.0"
)

BILLING_CUSTOMER_ENTITY_TYPE = (
    "STRIPE_CUSTOMER_MAPPING"
)

BILLING_CUSTOMER_LOOKUP_ENTITY_TYPE = (
    "STRIPE_CUSTOMER_LOOKUP"
)

BILLING_CUSTOMER_PROVIDER = "STRIPE"

BILLING_CUSTOMER_SK = (
    "BILLING#STRIPE#CUSTOMER"
)

BILLING_CUSTOMER_LOOKUP_SK = "USER"

STRIPE_CUSTOMER_PATTERN = re.compile(
    r"^cus_[A-Za-z0-9]{6,}$"
)

BILLING_USER_REFERENCE_PATTERN = (
    re.compile(
        r"^jm8usr_[0-9a-f]{32}$"
    )
)

RETRYABLE_DYNAMODB_ERRORS = {
    "InternalServerError",
    "ProvisionedThroughputExceededException",
    "RequestLimitExceeded",
    "ServiceUnavailable",
    "ThrottlingException",
    "TransactionConflictException",
}

RETRYABLE_CANCELLATION_CODES = {
    "ProvisionedThroughputExceeded",
    "ThrottlingError",
    "TransactionConflict",
}

CONDITIONAL_CHECK_FAILED = (
    "ConditionalCheckFailedException"
)

TRANSACTION_CANCELED = (
    "TransactionCanceledException"
)


class BillingCustomerStoreError(
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

        self.code = str(
            code
            or "BillingCustomerStoreError"
        )

        self.message = str(
            message
        )

        self.retryable = bool(
            retryable
        )


class BillingCustomerConflictError(
    BillingCustomerStoreError
):
    def __init__(
        self,
    ):
        super().__init__(
            "BillingCustomerConflict",
            (
                "The billing customer mapping "
                "conflicts with an existing "
                "record."
            ),
            retryable=False,
        )


def normalize_customer_store_user_id(
    user_id: Any,
) -> str:
    try:
        return normalize_billing_user_id(
            user_id
        )

    except BillingIdentityError as error:
        raise BillingCustomerStoreError(
            "InvalidBillingCustomerUser",
            (
                "A valid authenticated billing "
                "user is required."
            ),
            retryable=False,
        ) from error


def normalize_stored_customer_id(
    customer_id: Any,
) -> str:
    normalized = str(
        customer_id or ""
    ).strip()

    if not STRIPE_CUSTOMER_PATTERN.fullmatch(
        normalized
    ):
        raise BillingCustomerStoreError(
            "InvalidStripeCustomer",
            (
                "A valid Stripe customer "
                "identifier is required."
            ),
            retryable=False,
        )

    return normalized


def normalize_stored_user_reference(
    user_reference: Any,
) -> str:
    normalized = str(
        user_reference or ""
    ).strip()

    if not (
        BILLING_USER_REFERENCE_PATTERN
        .fullmatch(normalized)
    ):
        raise BillingCustomerStoreError(
            "InvalidBillingUserReference",
            (
                "A valid private billing user "
                "reference is required."
            ),
            retryable=False,
        )

    return normalized


def normalize_livemode(
    livemode: Any,
) -> bool:
    if not isinstance(
        livemode,
        bool,
    ):
        raise BillingCustomerStoreError(
            "InvalidStripeMode",
            (
                "The Stripe account mode "
                "is invalid."
            ),
            retryable=False,
        )

    return livemode


def stripe_mode_name(
    livemode: Any,
) -> str:
    return (
        "LIVE"
        if normalize_livemode(
            livemode
        )
        else "TEST"
    )


def billing_customer_key(
    user_id: Any,
) -> dict[str, str]:
    normalized_user_id = (
        normalize_customer_store_user_id(
            user_id
        )
    )

    return {
        "PK": user_pk(
            normalized_user_id
        ),
        "SK": BILLING_CUSTOMER_SK,
    }


def stripe_customer_lookup_key(
    customer_id: Any,
    *,
    livemode: Any,
) -> dict[str, str]:
    normalized_customer_id = (
        normalize_stored_customer_id(
            customer_id
        )
    )

    mode_name = stripe_mode_name(
        livemode
    )

    return {
        "PK": (
            "STRIPE_CUSTOMER#"
            + mode_name
            + "#"
            + normalized_customer_id
        ),
        "SK":
            BILLING_CUSTOMER_LOOKUP_SK,
    }


def _normalize_timestamp(
    value: Any,
    *,
    field: str,
) -> str:
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
            raise BillingCustomerStoreError(
                "InvalidBillingCustomerRecord",
                (
                    f"The stored {field} "
                    "timestamp is invalid."
                ),
                retryable=False,
            ) from error

    else:
        raise BillingCustomerStoreError(
            "InvalidBillingCustomerRecord",
            (
                f"The stored {field} "
                "timestamp is invalid."
            ),
            retryable=False,
        )

    if parsed.tzinfo is None:
        raise BillingCustomerStoreError(
            "InvalidBillingCustomerRecord",
            (
                f"The stored {field} "
                "timestamp must include "
                "a timezone."
            ),
            retryable=False,
        )

    return parsed.astimezone(
        timezone.utc
    ).isoformat()


def _created_timestamp(
    now: datetime | None,
) -> str:
    moment = (
        now
        if now is not None
        else datetime.now(
            timezone.utc
        )
    )

    return _normalize_timestamp(
        moment,
        field="createdAt",
    )


def build_customer_mapping_items(
    *,
    user_id: Any,
    customer_id: Any,
    livemode: Any,
    user_reference: Any,
    now: datetime | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    normalized_user_id = (
        normalize_customer_store_user_id(
            user_id
        )
    )

    normalized_customer_id = (
        normalize_stored_customer_id(
            customer_id
        )
    )

    normalized_livemode = (
        normalize_livemode(
            livemode
        )
    )

    normalized_reference = (
        normalize_stored_user_reference(
            user_reference
        )
    )

    timestamp = _created_timestamp(
        now
    )

    customer_key = (
        billing_customer_key(
            normalized_user_id
        )
    )

    lookup_key = (
        stripe_customer_lookup_key(
            normalized_customer_id,
            livemode=(
                normalized_livemode
            ),
        )
    )

    mapping_item = {
        **customer_key,

        "entityType":
            BILLING_CUSTOMER_ENTITY_TYPE,

        "mappingVersion":
            BILLING_CUSTOMER_MAPPING_VERSION,

        "provider":
            BILLING_CUSTOMER_PROVIDER,

        "stripeCustomerId":
            normalized_customer_id,

        "livemode":
            normalized_livemode,

        "userReference":
            normalized_reference,

        "createdAt":
            timestamp,

        "updatedAt":
            timestamp,
    }

    lookup_item = {
        **lookup_key,

        "entityType":
            (
                BILLING_CUSTOMER_LOOKUP_ENTITY_TYPE
            ),

        "mappingVersion":
            BILLING_CUSTOMER_MAPPING_VERSION,

        "provider":
            BILLING_CUSTOMER_PROVIDER,

        "userId":
            normalized_user_id,

        "stripeCustomerId":
            normalized_customer_id,

        "livemode":
            normalized_livemode,

        "userReference":
            normalized_reference,

        "customerMappingPK":
            customer_key["PK"],

        "customerMappingSK":
            customer_key["SK"],

        "createdAt":
            timestamp,

        "updatedAt":
            timestamp,
    }

    return (
        mapping_item,
        lookup_item,
    )


def _invalid_record(
    message: str = (
        "The stored billing customer "
        "record is invalid."
    ),
) -> BillingCustomerStoreError:
    return BillingCustomerStoreError(
        "InvalidBillingCustomerRecord",
        message,
        retryable=False,
    )


def normalize_customer_mapping(
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
                    "The billing customer "
                    "mapping owner is invalid."
                )
            )

    if (
        item.get("entityType")
        != BILLING_CUSTOMER_ENTITY_TYPE
        or item.get("mappingVersion")
        != BILLING_CUSTOMER_MAPPING_VERSION
        or item.get("provider")
        != BILLING_CUSTOMER_PROVIDER
    ):
        raise _invalid_record()

    customer_id = (
        normalize_stored_customer_id(
            item.get(
                "stripeCustomerId"
            )
        )
    )

    livemode = normalize_livemode(
        item.get("livemode")
    )

    user_reference = (
        normalize_stored_user_reference(
            item.get(
                "userReference"
            )
        )
    )

    created_at = _normalize_timestamp(
        item.get("createdAt"),
        field="createdAt",
    )

    updated_at = _normalize_timestamp(
        item.get("updatedAt"),
        field="updatedAt",
    )

    return {
        "entityType":
            BILLING_CUSTOMER_ENTITY_TYPE,

        "mappingVersion":
            BILLING_CUSTOMER_MAPPING_VERSION,

        "provider":
            BILLING_CUSTOMER_PROVIDER,

        "stripeCustomerId":
            customer_id,

        "livemode":
            livemode,

        "userReference":
            user_reference,

        "createdAt":
            created_at,

        "updatedAt":
            updated_at,
    }


def normalize_customer_lookup(
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
                    "The Stripe customer "
                    "lookup key is invalid."
                )
            )

    if (
        item.get("entityType")
        != (
            BILLING_CUSTOMER_LOOKUP_ENTITY_TYPE
        )
        or item.get("mappingVersion")
        != BILLING_CUSTOMER_MAPPING_VERSION
        or item.get("provider")
        != BILLING_CUSTOMER_PROVIDER
    ):
        raise _invalid_record()

    user_id = (
        normalize_customer_store_user_id(
            item.get("userId")
        )
    )

    customer_id = (
        normalize_stored_customer_id(
            item.get(
                "stripeCustomerId"
            )
        )
    )

    livemode = normalize_livemode(
        item.get("livemode")
    )

    user_reference = (
        normalize_stored_user_reference(
            item.get(
                "userReference"
            )
        )
    )

    expected_mapping_key = (
        billing_customer_key(
            user_id
        )
    )

    if (
        item.get("customerMappingPK")
        != expected_mapping_key["PK"]
        or item.get(
            "customerMappingSK"
        )
        != expected_mapping_key["SK"]
    ):
        raise _invalid_record(
            (
                "The Stripe customer lookup "
                "mapping reference is invalid."
            )
        )

    created_at = _normalize_timestamp(
        item.get("createdAt"),
        field="createdAt",
    )

    updated_at = _normalize_timestamp(
        item.get("updatedAt"),
        field="updatedAt",
    )

    return {
        "entityType":
            (
                BILLING_CUSTOMER_LOOKUP_ENTITY_TYPE
            ),

        "mappingVersion":
            BILLING_CUSTOMER_MAPPING_VERSION,

        "provider":
            BILLING_CUSTOMER_PROVIDER,

        "userId":
            user_id,

        "stripeCustomerId":
            customer_id,

        "livemode":
            livemode,

        "userReference":
            user_reference,

        "createdAt":
            created_at,

        "updatedAt":
            updated_at,
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


def _cancellation_codes(
    error: ClientError,
) -> set[str]:
    reasons = error.response.get(
        "CancellationReasons"
    ) or []

    return {
        str(
            reason.get("Code")
            or ""
        )
        for reason in reasons
        if isinstance(
            reason,
            Mapping,
        )
        and reason.get("Code")
    }


def _is_condition_conflict(
    error: ClientError,
) -> bool:
    code = _client_error_code(
        error
    )

    if code == CONDITIONAL_CHECK_FAILED:
        return True

    if code != TRANSACTION_CANCELED:
        return False

    return (
        "ConditionalCheckFailed"
        in _cancellation_codes(
            error
        )
    )


def _client_error_is_retryable(
    error: ClientError,
) -> bool:
    code = _client_error_code(
        error
    )

    if code in RETRYABLE_DYNAMODB_ERRORS:
        return True

    if code != TRANSACTION_CANCELED:
        return False

    return bool(
        _cancellation_codes(
            error
        )
        & RETRYABLE_CANCELLATION_CODES
    )


def _raise_store_client_error(
    error: ClientError,
    *,
    operation: str,
) -> None:
    code = _client_error_code(
        error
    )

    raise BillingCustomerStoreError(
        code,
        (
            "The billing customer mapping "
            f"could not be {operation}."
        ),
        retryable=(
            _client_error_is_retryable(
                error
            )
        ),
    ) from error


def get_stripe_customer_mapping(
    user_id: Any,
    *,
    table_resource=None,
) -> dict[str, Any] | None:
    key = billing_customer_key(
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
        _raise_store_client_error(
            error,
            operation="retrieved",
        )

    item = result.get("Item")

    if item is None:
        return None

    return normalize_customer_mapping(
        item,
        expected_key=key,
    )


def get_billing_user_for_customer(
    customer_id: Any,
    *,
    livemode: Any,
    table_resource=None,
) -> dict[str, Any] | None:
    key = stripe_customer_lookup_key(
        customer_id,
        livemode=livemode,
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
        _raise_store_client_error(
            error,
            operation="retrieved",
        )

    item = result.get("Item")

    if item is None:
        return None

    return normalize_customer_lookup(
        item,
        expected_key=key,
    )


def build_customer_mapping_transaction_token(
    *,
    user_id: Any,
    customer_id: Any,
    livemode: Any,
    user_reference: Any,
) -> str:
    normalized_values = (
        normalize_customer_store_user_id(
            user_id
        ),
        normalize_stored_customer_id(
            customer_id
        ),
        stripe_mode_name(
            livemode
        ),
        normalize_stored_user_reference(
            user_reference
        ),
    )

    digest = sha256()

    for value in normalized_values:
        encoded = value.encode(
            "utf-8"
        )

        digest.update(
            len(encoded).to_bytes(
                4,
                byteorder="big",
                signed=False,
            )
        )

        digest.update(
            encoded
        )

    return (
        "jm8-billing-"
        + digest.hexdigest()[:24]
    )


def _mapping_matches(
    mapping: Mapping[str, Any],
    lookup: Mapping[str, Any],
    *,
    user_id: str,
    customer_id: str,
    livemode: bool,
    user_reference: str,
) -> bool:
    return (
        mapping.get(
            "stripeCustomerId"
        )
        == customer_id
        and mapping.get(
            "livemode"
        )
        is livemode
        and mapping.get(
            "userReference"
        )
        == user_reference
        and lookup.get(
            "userId"
        )
        == user_id
        and lookup.get(
            "stripeCustomerId"
        )
        == customer_id
        and lookup.get(
            "livemode"
        )
        is livemode
        and lookup.get(
            "userReference"
        )
        == user_reference
    )


def create_stripe_customer_mapping(
    *,
    user_id: Any,
    customer_id: Any,
    livemode: Any,
    user_reference: Any,
    now: datetime | None = None,
    table_resource=None,
    client_resource=None,
) -> dict[str, Any]:
    normalized_user_id = (
        normalize_customer_store_user_id(
            user_id
        )
    )

    normalized_customer_id = (
        normalize_stored_customer_id(
            customer_id
        )
    )

    normalized_livemode = (
        normalize_livemode(
            livemode
        )
    )

    normalized_reference = (
        normalize_stored_user_reference(
            user_reference
        )
    )

    mapping_item, lookup_item = (
        build_customer_mapping_items(
            user_id=normalized_user_id,
            customer_id=(
                normalized_customer_id
            ),
            livemode=(
                normalized_livemode
            ),
            user_reference=(
                normalized_reference
            ),
            now=now,
        )
    )

    resource = (
        client_resource
        if client_resource is not None
        else dynamodb_client
    )

    condition = (
        "attribute_not_exists(#pk) "
        "AND attribute_not_exists(#sk)"
    )

    names = {
        "#pk": "PK",
        "#sk": "SK",
    }

    try:
        resource.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName":
                            TABLE_NAME,

                        "Item":
                            serialize_attribute_map(
                                mapping_item
                            ),

                        "ConditionExpression":
                            condition,

                        "ExpressionAttributeNames":
                            names,
                    }
                },
                {
                    "Put": {
                        "TableName":
                            TABLE_NAME,

                        "Item":
                            serialize_attribute_map(
                                lookup_item
                            ),

                        "ConditionExpression":
                            condition,

                        "ExpressionAttributeNames":
                            names,
                    }
                },
            ],
            ClientRequestToken=(
                build_customer_mapping_transaction_token(
                    user_id=(
                        normalized_user_id
                    ),
                    customer_id=(
                        normalized_customer_id
                    ),
                    livemode=(
                        normalized_livemode
                    ),
                    user_reference=(
                        normalized_reference
                    ),
                )
            ),
        )

    except ClientError as error:
        if not _is_condition_conflict(
            error
        ):
            _raise_store_client_error(
                error,
                operation="created",
            )

        existing_mapping = (
            get_stripe_customer_mapping(
                normalized_user_id,
                table_resource=(
                    table_resource
                ),
            )
        )

        existing_lookup = (
            get_billing_user_for_customer(
                normalized_customer_id,
                livemode=(
                    normalized_livemode
                ),
                table_resource=(
                    table_resource
                ),
            )
        )

        if (
            existing_mapping is None
            or existing_lookup is None
            or not _mapping_matches(
                existing_mapping,
                existing_lookup,
                user_id=(
                    normalized_user_id
                ),
                customer_id=(
                    normalized_customer_id
                ),
                livemode=(
                    normalized_livemode
                ),
                user_reference=(
                    normalized_reference
                ),
            )
        ):
            raise (
                BillingCustomerConflictError()
            ) from error

        reused = dict(
            existing_mapping
        )

        reused[
            "_createdInRequest"
        ] = False

        return reused

    created = normalize_customer_mapping(
        mapping_item,
        expected_key=(
            billing_customer_key(
                normalized_user_id
            )
        ),
    )

    created[
        "_createdInRequest"
    ] = True

    return created
