"""Stripe renewal cancellation and retry-safe reverse-lookup recovery."""

from __future__ import annotations

from typing import Any

from billing_customer_store import (
    BillingCustomerStoreError,
    get_stripe_customer_mapping,
    stripe_customer_lookup_key,
)
from stripe_checkout_gateway import StripeCheckoutGateway, StripeGatewayError


class DeletionStripeError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def prepare_and_cancel_subscription(
    subject: str,
    *,
    mapping_reader=get_stripe_customer_mapping,
    gateway: Any = None,
    recovery_writer: Any,
    destructive_marker: Any,
) -> dict[str, Any] | None:
    try:
        mapping = mapping_reader(subject)
    except BillingCustomerStoreError as error:
        raise DeletionStripeError(
            "BillingMappingUnavailable", retryable=error.retryable
        ) from None
    if mapping is None:
        recovery_writer(customer_id=None, livemode=None)
        destructive_marker()
        return None
    customer_id = mapping["stripeCustomerId"]
    livemode = mapping["livemode"]
    recovery_writer(customer_id=customer_id, livemode=livemode)
    destructive_marker()
    provider = gateway or StripeCheckoutGateway()
    if provider.livemode is not livemode:
        raise DeletionStripeError("StripeModeMismatch", retryable=False)
    try:
        subscription = provider.find_blocking_subscription(customer_id)
        if subscription is None:
            return stripe_customer_lookup_key(customer_id, livemode=livemode)
        provider.stop_subscription_renewal(subscription.get("id"))
        return stripe_customer_lookup_key(customer_id, livemode=livemode)
    except StripeGatewayError as error:
        if error.status_code == 404:
            return stripe_customer_lookup_key(customer_id, livemode=livemode)
        raise DeletionStripeError(
            "StripeRenewalCancellationFailed", retryable=error.retryable
        ) from None
