from __future__ import annotations

import json
from typing import Any

from billing_customer_store import (
    BillingCustomerStoreError,
    get_stripe_customer_mapping,
)
from billing_identity import (
    BillingIdentityError,
    build_billing_user_reference,
    normalize_billing_user_id,
)
from stripe_checkout_gateway import (
    StripeCheckoutGateway,
    StripeGatewayError,
    build_public_portal_result,
)


class BillingPortalError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status_code = int(status_code)
        self.retryable = bool(retryable)
        self.payload = {
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }

        if self.retryable:
            self.payload["retryAfterSeconds"] = 2


def _log(event_name: str, **fields: Any) -> None:
    print(json.dumps({"event": event_name, **fields}))


def _gateway_error(error: StripeGatewayError) -> None:
    _log(
        "billing_portal_failed",
        failureCode=error.code,
        failureStage="stripe",
        retryable=error.retryable,
    )

    if error.retryable:
        raise BillingPortalError(
            "BillingProviderUnavailable",
            "JM8 could not reach the billing provider.",
            status_code=503,
            retryable=True,
        ) from error

    status_code = 500 if error.status_code == 500 else 502
    code = (
        "BillingConfigurationUnavailable"
        if status_code == 500
        else "BillingProviderError"
    )

    raise BillingPortalError(
        code,
        "JM8 could not open the Billing Portal.",
        status_code=status_code,
    ) from error


def create_billing_portal(
    *,
    user_id: Any,
    gateway=None,
    mapping_reader=None,
) -> dict[str, str]:
    try:
        normalized_user_id = normalize_billing_user_id(user_id)
    except BillingIdentityError as error:
        raise BillingPortalError(
            error.code,
            error.message,
            status_code=400,
        ) from error

    mapping_reader = mapping_reader or get_stripe_customer_mapping

    try:
        mapping = mapping_reader(normalized_user_id)
    except BillingCustomerStoreError as error:
        _log(
            "billing_portal_failed",
            failureCode=error.code,
            failureStage="customerStore",
            retryable=error.retryable,
        )
        raise BillingPortalError(
            "BillingStoreUnavailable",
            "JM8 could not load your billing account.",
            status_code=503 if error.retryable else 500,
            retryable=error.retryable,
        ) from error

    if mapping is None:
        raise BillingPortalError(
            "BillingCustomerNotFound",
            "This account does not have a billing profile yet.",
            status_code=404,
        )

    if gateway is None:
        try:
            gateway = StripeCheckoutGateway()
        except StripeGatewayError as error:
            _gateway_error(error)

    expected_reference = build_billing_user_reference(
        normalized_user_id
    )

    if mapping.get("livemode") is not gateway.livemode:
        raise BillingPortalError(
            "BillingCustomerModeMismatch",
            "The billing customer mode does not match JM8.",
            status_code=409,
        )

    if mapping.get("userReference") != expected_reference:
        raise BillingPortalError(
            "BillingCustomerOwnershipMismatch",
            "The billing customer mapping could not be verified.",
            status_code=409,
        )

    customer_id = mapping.get("stripeCustomerId")

    try:
        gateway.retrieve_customer(customer_id)
        session = gateway.create_billing_portal_session(
            customer_id=customer_id
        )
        result = build_public_portal_result(session)
    except StripeGatewayError as error:
        _gateway_error(error)

    _log("billing_portal_created")
    return result
