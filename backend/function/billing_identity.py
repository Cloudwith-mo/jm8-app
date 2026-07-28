from __future__ import annotations

import re
from hashlib import (
    sha256,
)
from typing import (
    Any,
)


BILLING_IDENTITY_VERSION = "1.0"

BILLING_USER_REFERENCE_PREFIX = (
    "jm8usr_"
)

CUSTOMER_IDEMPOTENCY_PREFIX = (
    "jm8-customer-v1-"
)

CHECKOUT_IDEMPOTENCY_PREFIX = (
    "jm8-checkout-v1-"
)

REQUEST_TOKEN_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,256}$"
)


class BillingIdentityError(
    ValueError
):
    def __init__(
        self,
        code: str,
        message: str,
    ):
        super().__init__(message)

        self.code = str(
            code
            or "BillingIdentityError"
        )

        self.message = str(
            message
        )


def normalize_billing_user_id(
    user_id: Any,
) -> str:
    normalized = str(
        user_id or ""
    ).strip()

    if (
        not normalized
        or len(normalized) > 256
    ):
        raise BillingIdentityError(
            "InvalidBillingUser",
            (
                "A valid authenticated "
                "billing user is required."
            ),
        )

    return normalized


def normalize_billing_request_token(
    request_token: Any,
) -> str:
    normalized = str(
        request_token or ""
    ).strip()

    if not REQUEST_TOKEN_PATTERN.fullmatch(
        normalized
    ):
        raise BillingIdentityError(
            "InvalidBillingRequestToken",
            (
                "A valid billing request "
                "token is required."
            ),
        )

    return normalized


def _digest_parts(
    *parts: str,
) -> str:
    digest = sha256()

    for part in parts:
        encoded = str(
            part
        ).encode("utf-8")

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

    return digest.hexdigest()


def build_billing_user_reference(
    user_id: Any,
) -> str:
    normalized_user_id = (
        normalize_billing_user_id(
            user_id
        )
    )

    digest = _digest_parts(
        BILLING_IDENTITY_VERSION,
        "user-reference",
        normalized_user_id,
    )

    return (
        BILLING_USER_REFERENCE_PREFIX
        + digest[:32]
    )


def build_customer_idempotency_key(
    user_id: Any,
) -> str:
    normalized_user_id = (
        normalize_billing_user_id(
            user_id
        )
    )

    digest = _digest_parts(
        BILLING_IDENTITY_VERSION,
        "stripe-customer",
        normalized_user_id,
    )

    return (
        CUSTOMER_IDEMPOTENCY_PREFIX
        + digest[:40]
    )


def build_checkout_idempotency_key(
    user_id: Any,
    request_token: Any,
) -> str:
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

    digest = _digest_parts(
        BILLING_IDENTITY_VERSION,
        "stripe-checkout",
        normalized_user_id,
        normalized_request_token,
    )

    return (
        CHECKOUT_IDEMPOTENCY_PREFIX
        + digest[:40]
    )
