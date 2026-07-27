from __future__ import annotations

import os
import re
from typing import (
    Any,
    Mapping,
)
from urllib.parse import (
    urlparse,
)

from usage_policy import (
    PLAN_PRO,
)


BILLING_VERSION = "1.0"

BILLING_PROVIDER_STRIPE = "STRIPE"

BILLING_OFFER_PRO_MONTHLY = (
    "JM8_PRO_MONTHLY"
)

BILLING_PRODUCT_NAME = "JM8 Pro"

STRIPE_PRO_MONTHLY_LOOKUP_KEY = (
    "jm8_pro_monthly"
)

BILLING_CURRENCY = "USD"
BILLING_UNIT_AMOUNT = 1_000

BILLING_INTERVAL = "month"
BILLING_INTERVAL_COUNT = 1

STRIPE_SECRET_KEY_ENV = (
    "STRIPE_SECRET_KEY"
)

STRIPE_WEBHOOK_SECRET_ENV = (
    "STRIPE_WEBHOOK_SECRET"
)

STRIPE_PRO_MONTHLY_PRICE_ID_ENV = (
    "STRIPE_PRO_MONTHLY_PRICE_ID"
)

STRIPE_CHECKOUT_SUCCESS_URL_ENV = (
    "STRIPE_CHECKOUT_SUCCESS_URL"
)

STRIPE_CHECKOUT_CANCEL_URL_ENV = (
    "STRIPE_CHECKOUT_CANCEL_URL"
)

STRIPE_PORTAL_RETURN_URL_ENV = (
    "STRIPE_PORTAL_RETURN_URL"
)


STRIPE_SECRET_KEY_PATTERN = re.compile(
    r"^sk_(test|live)_[A-Za-z0-9_]{8,}$"
)

STRIPE_WEBHOOK_SECRET_PATTERN = re.compile(
    r"^whsec_[A-Za-z0-9_]{8,}$"
)

STRIPE_PRICE_ID_PATTERN = re.compile(
    r"^price_[A-Za-z0-9_]{6,}$"
)


class BillingPolicyError(
    ValueError
):
    def __init__(
        self,
        code: str,
        message: str,
    ):
        super().__init__(message)

        self.code = str(
            code or "BillingPolicyError"
        )

        self.message = str(message)


def _raise_policy_error(
    code: str,
    message: str,
) -> None:
    raise BillingPolicyError(
        code,
        message,
    )


def _environment(
    environ: (
        Mapping[str, str] | None
    ),
) -> Mapping[str, str]:
    return (
        environ
        if environ is not None
        else os.environ
    )


def _require_text(
    value: Any,
    *,
    code: str,
    message: str,
    max_characters: int = 2_048,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        _raise_policy_error(
            code,
            message,
        )

    normalized = value.strip()

    if (
        not normalized
        or len(normalized)
        > max_characters
    ):
        _raise_policy_error(
            code,
            message,
        )

    return normalized


def _normalize_stripe_secret_key(
    value: Any,
) -> str:
    normalized = _require_text(
        value,
        code=(
            "InvalidStripeSecretKey"
        ),
        message=(
            "Stripe API credentials are "
            "not configured correctly."
        ),
        max_characters=300,
    )

    if not (
        STRIPE_SECRET_KEY_PATTERN
        .fullmatch(normalized)
    ):
        _raise_policy_error(
            "InvalidStripeSecretKey",
            (
                "Stripe API credentials are "
                "not configured correctly."
            ),
        )

    return normalized


def _normalize_stripe_webhook_secret(
    value: Any,
) -> str:
    normalized = _require_text(
        value,
        code=(
            "InvalidStripeWebhookSecret"
        ),
        message=(
            "Stripe webhook verification "
            "is not configured correctly."
        ),
        max_characters=300,
    )

    if not (
        STRIPE_WEBHOOK_SECRET_PATTERN
        .fullmatch(normalized)
    ):
        _raise_policy_error(
            "InvalidStripeWebhookSecret",
            (
                "Stripe webhook verification "
                "is not configured correctly."
            ),
        )

    return normalized


def _normalize_stripe_price_id(
    value: Any,
) -> str:
    normalized = _require_text(
        value,
        code="InvalidStripePriceId",
        message=(
            "The Stripe price configuration "
            "is invalid."
        ),
        max_characters=300,
    )

    if not (
        STRIPE_PRICE_ID_PATTERN
        .fullmatch(normalized)
    ):
        _raise_policy_error(
            "InvalidStripePriceId",
            (
                "The Stripe price "
                "configuration is invalid."
            ),
        )

    return normalized


def _normalize_return_url(
    value: Any,
    *,
    field: str,
) -> str:
    normalized = _require_text(
        value,
        code="InvalidBillingReturnUrl",
        message=(
            f"{field} is not configured "
            "correctly."
        ),
    )

    parsed = urlparse(
        normalized
    )

    if (
        parsed.scheme
        not in {
            "http",
            "https",
        }
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        _raise_policy_error(
            "InvalidBillingReturnUrl",
            (
                f"{field} is not configured "
                "correctly."
            ),
        )

    hostname = str(
        parsed.hostname or ""
    ).lower()

    local_hosts = {
        "localhost",
        "127.0.0.1",
        "::1",
    }

    if (
        parsed.scheme == "http"
        and hostname not in local_hosts
    ):
        _raise_policy_error(
            "InsecureBillingReturnUrl",
            (
                f"{field} must use HTTPS "
                "outside local development."
            ),
        )

    return normalized


def get_public_billing_offer(
) -> dict[str, Any]:
    return {
        "billingVersion": (
            BILLING_VERSION
        ),
        "provider": (
            BILLING_PROVIDER_STRIPE
        ),
        "offer": {
            "id": (
                BILLING_OFFER_PRO_MONTHLY
            ),
            "name": (
                BILLING_PRODUCT_NAME
            ),
            "plan": {
                "id": PLAN_PRO,
                "label": "Pro",
            },
            "amount": {
                "currency": (
                    BILLING_CURRENCY
                ),
                "unitAmount": (
                    BILLING_UNIT_AMOUNT
                ),
                "display": "$10.00",
            },
            "recurring": {
                "interval": (
                    BILLING_INTERVAL
                ),
                "intervalCount": (
                    BILLING_INTERVAL_COUNT
                ),
            },
        },
    }


def load_stripe_checkout_config(
    environ: (
        Mapping[str, str] | None
    ) = None,
) -> dict[str, str]:
    source = _environment(
        environ
    )

    return {
        "secretKey": (
            _normalize_stripe_secret_key(
                source.get(
                    STRIPE_SECRET_KEY_ENV
                )
            )
        ),
        "priceId": (
            _normalize_stripe_price_id(
                source.get(
                    STRIPE_PRO_MONTHLY_PRICE_ID_ENV
                )
            )
        ),
        "successUrl": (
            _normalize_return_url(
                source.get(
                    STRIPE_CHECKOUT_SUCCESS_URL_ENV
                ),
                field=(
                    "Stripe Checkout "
                    "success URL"
                ),
            )
        ),
        "cancelUrl": (
            _normalize_return_url(
                source.get(
                    STRIPE_CHECKOUT_CANCEL_URL_ENV
                ),
                field=(
                    "Stripe Checkout "
                    "cancellation URL"
                ),
            )
        ),
        "portalReturnUrl": (
            _normalize_return_url(
                source.get(
                    STRIPE_PORTAL_RETURN_URL_ENV
                ),
                field=(
                    "Stripe customer portal "
                    "return URL"
                ),
            )
        ),
    }


def load_stripe_webhook_config(
    environ: (
        Mapping[str, str] | None
    ) = None,
) -> dict[str, str]:
    source = _environment(
        environ
    )

    return {
        "webhookSecret": (
            _normalize_stripe_webhook_secret(
                source.get(
                    STRIPE_WEBHOOK_SECRET_ENV
                )
            )
        ),
    }
