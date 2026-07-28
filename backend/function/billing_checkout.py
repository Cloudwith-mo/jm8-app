from __future__ import annotations

import json
from typing import Any

from account_entitlement import (
    AccountEntitlementUnavailableError,
    get_account_entitlement,
)
from billing_customer_store import (
    BillingCustomerConflictError,
    BillingCustomerStoreError,
    create_stripe_customer_mapping,
    get_stripe_customer_mapping,
)
from billing_identity import (
    BillingIdentityError,
    build_billing_user_reference,
    build_checkout_idempotency_key,
    build_customer_idempotency_key,
    normalize_billing_request_token,
    normalize_billing_user_id,
)
from stripe_checkout_gateway import (
    StripeCheckoutGateway,
    StripeGatewayError,
    build_public_checkout_result,
)


BILLING_CHECKOUT_VERSION = "1.0"


class BillingCheckoutError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
    ):
        super().__init__(message)

        self.code = str(
            code or "BillingCheckoutError"
        )

        self.message = str(message)
        self.status_code = int(status_code)
        self.retryable = bool(retryable)

        self.payload = {
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }

        if self.retryable:
            self.payload[
                "retryAfterSeconds"
            ] = 2


def _log(
    event_name: str,
    **fields: Any,
) -> None:
    print(json.dumps({
        "event": event_name,
        **fields,
    }))


def _raise_checkout_error(
    code: str,
    message: str,
    *,
    status_code: int,
    retryable: bool = False,
) -> None:
    raise BillingCheckoutError(
        code,
        message,
        status_code=status_code,
        retryable=retryable,
    )


def _raise_store_error(
    error: BillingCustomerStoreError,
) -> None:
    _log(
        "billing_checkout_failed",
        failureCode=error.code,
        failureStage="customerStore",
        retryable=error.retryable,
    )

    _raise_checkout_error(
        "BillingStoreUnavailable",
        (
            "JM8 could not prepare your "
            "billing account."
        ),
        status_code=(
            503 if error.retryable else 500
        ),
        retryable=error.retryable,
    )


def _raise_gateway_error(
    error: StripeGatewayError,
) -> None:
    _log(
        "billing_checkout_failed",
        failureCode=error.code,
        failureStage="stripe",
        retryable=error.retryable,
    )

    if error.retryable:
        _raise_checkout_error(
            "BillingProviderUnavailable",
            (
                "JM8 could not reach the "
                "billing provider."
            ),
            status_code=503,
            retryable=True,
        )

    if error.status_code == 409:
        _raise_checkout_error(
            "BillingProviderConflict",
            (
                "The billing account requires "
                "review before Checkout can "
                "continue."
            ),
            status_code=409,
            retryable=False,
        )

    if error.status_code == 500:
        _raise_checkout_error(
            "BillingConfigurationUnavailable",
            (
                "JM8 billing is not configured "
                "correctly."
            ),
            status_code=500,
            retryable=False,
        )

    _raise_checkout_error(
        "BillingProviderError",
        (
            "JM8 could not create the "
            "Checkout Session."
        ),
        status_code=502,
        retryable=False,
    )


def _normalize_authenticated_email(
    email: Any,
) -> str | None:
    normalized = str(
        email or ""
    ).strip()

    if not normalized:
        return None

    if (
        len(normalized) > 512
        or "@" not in normalized
    ):
        return None

    return normalized


def _entitlement_is_pro(
    entitlement: Any,
) -> bool:
    if not isinstance(
        entitlement,
        dict,
    ):
        _raise_checkout_error(
            "BillingEntitlementUnavailable",
            (
                "JM8 could not verify your "
                "current plan."
            ),
            status_code=500,
            retryable=False,
        )

    access = entitlement.get(
        "access"
    )

    if not isinstance(
        access,
        dict,
    ):
        _raise_checkout_error(
            "BillingEntitlementUnavailable",
            (
                "JM8 could not verify your "
                "current plan."
            ),
            status_code=500,
            retryable=False,
        )

    return access.get(
        "isPro"
    ) is True


def create_billing_checkout(
    *,
    user_id: Any,
    request_token: Any,
    email: Any = None,
    gateway=None,
    entitlement_reader=None,
    mapping_reader=None,
    mapping_creator=None,
) -> dict[str, str]:
    try:
        normalized_user_id = (
            normalize_billing_user_id(
                user_id
            )
        )

        normalized_request_token = (
            normalize_billing_request_token(
                request_token
            )
        )

    except BillingIdentityError as error:
        raise BillingCheckoutError(
            error.code,
            error.message,
            status_code=400,
            retryable=False,
        ) from error

    entitlement_reader = (
        entitlement_reader
        or get_account_entitlement
    )

    mapping_reader = (
        mapping_reader
        or get_stripe_customer_mapping
    )

    mapping_creator = (
        mapping_creator
        or create_stripe_customer_mapping
    )

    try:
        entitlement = entitlement_reader(
            normalized_user_id
        )

    except (
        AccountEntitlementUnavailableError
    ) as error:
        _log(
            "billing_checkout_failed",
            failureCode=(
                "AccountEntitlementUnavailable"
            ),
            failureStage="entitlement",
            retryable=error.retryable,
        )

        _raise_checkout_error(
            "BillingEntitlementUnavailable",
            (
                "JM8 could not verify your "
                "current plan."
            ),
            status_code=503,
            retryable=error.retryable,
        )

    if _entitlement_is_pro(
        entitlement
    ):
        _log(
            "billing_checkout_blocked",
            reason="accountAlreadyPro",
        )

        _raise_checkout_error(
            "AccountAlreadyPro",
            (
                "This account already has "
                "JM8 Pro access."
            ),
            status_code=409,
            retryable=False,
        )

    if gateway is None:
        try:
            gateway = (
                StripeCheckoutGateway()
            )

        except StripeGatewayError as error:
            _raise_gateway_error(
                error
            )

    user_reference = (
        build_billing_user_reference(
            normalized_user_id
        )
    )

    try:
        mapping = mapping_reader(
            normalized_user_id
        )

    except BillingCustomerStoreError as error:
        _raise_store_error(
            error
        )

    mapping_created = False
    customer_provisioned = False

    if mapping is not None:
        if (
            mapping.get("livemode")
            is not gateway.livemode
        ):
            _raise_checkout_error(
                "BillingCustomerModeMismatch",
                (
                    "The billing customer mode "
                    "does not match the current "
                    "Stripe configuration."
                ),
                status_code=409,
                retryable=False,
            )

        if (
            mapping.get("userReference")
            != user_reference
        ):
            _raise_checkout_error(
                "BillingCustomerOwnershipMismatch",
                (
                    "The billing customer "
                    "mapping could not be "
                    "verified."
                ),
                status_code=409,
                retryable=False,
            )

        customer_id = mapping.get(
            "stripeCustomerId"
        )

        try:
            gateway.retrieve_customer(
                customer_id
            )

        except StripeGatewayError as error:
            _raise_gateway_error(
                error
            )

    else:
        try:
            customer = gateway.create_customer(
                user_reference=(
                    user_reference
                ),
                idempotency_key=(
                    build_customer_idempotency_key(
                        normalized_user_id
                    )
                ),
                email=(
                    _normalize_authenticated_email(
                        email
                    )
                ),
            )

        except StripeGatewayError as error:
            _raise_gateway_error(
                error
            )

        customer_id = customer.get(
            "id"
        )

        customer_provisioned = True

        try:
            mapping = mapping_creator(
                user_id=(
                    normalized_user_id
                ),
                customer_id=customer_id,
                livemode=gateway.livemode,
                user_reference=(
                    user_reference
                ),
            )

        except BillingCustomerConflictError as error:
            _log(
                "billing_checkout_failed",
                failureCode=error.code,
                failureStage=(
                    "customerMapping"
                ),
                retryable=False,
            )

            _raise_checkout_error(
                "BillingCustomerConflict",
                (
                    "A different billing "
                    "customer is already linked "
                    "to this account."
                ),
                status_code=409,
                retryable=False,
            )

        except BillingCustomerStoreError as error:
            _raise_store_error(
                error
            )

        if (
            mapping.get(
                "stripeCustomerId"
            )
            != customer_id
            or mapping.get(
                "livemode"
            )
            is not gateway.livemode
            or mapping.get(
                "userReference"
            )
            != user_reference
        ):
            _raise_checkout_error(
                "BillingCustomerConflict",
                (
                    "The billing customer "
                    "mapping could not be "
                    "verified."
                ),
                status_code=409,
                retryable=False,
            )

        mapping_created = bool(
            mapping.get(
                "_createdInRequest"
            )
        )

    try:
        blocking_subscription = (
            gateway.find_blocking_subscription(
                customer_id
            )
        )

    except StripeGatewayError as error:
        _raise_gateway_error(
            error
        )

    if blocking_subscription is not None:
        _log(
            "billing_checkout_blocked",
            reason=(
                "existingStripeSubscription"
            ),
        )

        _raise_checkout_error(
            "ExistingStripeSubscription",
            (
                "A Stripe subscription already "
                "exists for this account."
            ),
            status_code=409,
            retryable=False,
        )

    try:
        session = (
            gateway.create_checkout_session(
                customer_id=customer_id,
                user_reference=(
                    user_reference
                ),
                idempotency_key=(
                    build_checkout_idempotency_key(
                        normalized_user_id,
                        normalized_request_token,
                    )
                ),
            )
        )

        public_result = (
            build_public_checkout_result(
                session
            )
        )

    except StripeGatewayError as error:
        _raise_gateway_error(
            error
        )

    _log(
        "billing_checkout_created",
        billingCheckoutVersion=(
            BILLING_CHECKOUT_VERSION
        ),
        mappingCreated=(
            mapping_created
        ),
        customerProvisioned=(
            customer_provisioned
        ),
        livemode=gateway.livemode,
    )

    return public_result
