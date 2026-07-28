from __future__ import annotations

import json
import re
from typing import (
    Any,
    Callable,
    Mapping,
)
from urllib.error import (
    HTTPError,
    URLError,
)
from urllib.parse import (
    quote,
    urlencode,
    urlparse,
)
from urllib.request import (
    Request,
    urlopen,
)

from billing_policy import (
    BILLING_OFFER_PRO_MONTHLY,
    BILLING_VERSION,
    BillingPolicyError,
    load_stripe_checkout_config,
)
from stripe_secret_loader import (
    StripeSecretLoadError,
    load_stripe_runtime_environment,
)
from usage_policy import (
    PLAN_PRO,
)


STRIPE_API_BASE = (
    "https://api.stripe.com/v1"
)

STRIPE_GATEWAY_VERSION = "1.0"

STRIPE_CUSTOMER_PATTERN = re.compile(
    r"^cus_[A-Za-z0-9]{6,}$"
)

STRIPE_USER_REFERENCE_PATTERN = (
    re.compile(
        r"^jm8usr_[0-9a-f]{32}$"
    )
)

IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,255}$"
)

SAFE_PROVIDER_CODE_PATTERN = re.compile(
    r"^[A-Za-z0-9._:-]{1,100}$"
)

BLOCKING_SUBSCRIPTION_STATUSES = {
    "incomplete",
    "trialing",
    "active",
    "past_due",
    "unpaid",
    "paused",
}

TERMINAL_SUBSCRIPTION_STATUSES = {
    "incomplete_expired",
    "canceled",
}

RETRYABLE_HTTP_STATUSES = {
    408,
    409,
    425,
    429,
    500,
    502,
    503,
    504,
}


class StripeGatewayError(
    RuntimeError
):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ):
        super().__init__(message)

        self.code = str(
            code
            or "StripeGatewayError"
        )

        self.message = str(
            message
        )

        self.retryable = bool(
            retryable
        )

        self.status_code = (
            int(status_code)
            if status_code is not None
            else None
        )


def _invalid_gateway_input(
    code: str,
    message: str,
) -> StripeGatewayError:
    return StripeGatewayError(
        code,
        message,
        retryable=False,
        status_code=400,
    )


def normalize_stripe_customer_id(
    value: Any,
) -> str:
    normalized = str(
        value or ""
    ).strip()

    if not STRIPE_CUSTOMER_PATTERN.fullmatch(
        normalized
    ):
        raise _invalid_gateway_input(
            "InvalidStripeCustomer",
            (
                "A valid Stripe customer "
                "identifier is required."
            ),
        )

    return normalized


def normalize_billing_user_reference(
    value: Any,
) -> str:
    normalized = str(
        value or ""
    ).strip()

    if not (
        STRIPE_USER_REFERENCE_PATTERN
        .fullmatch(normalized)
    ):
        raise _invalid_gateway_input(
            "InvalidBillingUserReference",
            (
                "A valid private billing "
                "user reference is required."
            ),
        )

    return normalized


def normalize_idempotency_key(
    value: Any,
) -> str:
    normalized = str(
        value or ""
    ).strip()

    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(
        normalized
    ):
        raise _invalid_gateway_input(
            "InvalidIdempotencyKey",
            (
                "A valid Stripe idempotency "
                "key is required."
            ),
        )

    return normalized


def _secret_is_live(
    secret_key: str,
) -> bool:
    return secret_key.startswith(
        "sk_live_"
    )


def _safe_provider_code(
    payload: Any,
) -> str | None:
    if not isinstance(
        payload,
        Mapping,
    ):
        return None

    error = payload.get(
        "error"
    )

    if not isinstance(
        error,
        Mapping,
    ):
        return None

    candidate = str(
        error.get("code")
        or error.get("type")
        or ""
    ).strip()

    if not SAFE_PROVIDER_CODE_PATTERN.fullmatch(
        candidate
    ):
        return None

    return candidate


def _checkout_url(
    value: Any,
) -> str:
    normalized = str(
        value or ""
    ).strip()

    parsed = urlparse(
        normalized
    )

    if (
        parsed.scheme != "https"
        or parsed.hostname
        != "checkout.stripe.com"
    ):
        raise StripeGatewayError(
            "InvalidCheckoutSession",
            (
                "Stripe returned an invalid "
                "Checkout URL."
            ),
            retryable=False,
            status_code=502,
        )

    return normalized


class StripeCheckoutGateway:
    def __init__(
        self,
        *,
        environ: (
            Mapping[str, str] | None
        ) = None,
        opener: Callable[..., Any] = (
            urlopen
        ),
        timeout_seconds: int = 20,
        secret_loader: Callable[
            [Mapping[str, str] | None],
            Mapping[str, str],
        ] = load_stripe_runtime_environment,
    ):
        try:
            runtime_environment = (
                secret_loader(
                    environ
                )
            )

            config = (
                load_stripe_checkout_config(
                    runtime_environment
                )
            )

        except StripeSecretLoadError as error:
            raise StripeGatewayError(
                error.code,
                error.message,
                retryable=error.retryable,
                status_code=(
                    503
                    if error.retryable
                    else 500
                ),
            ) from error

        except BillingPolicyError as error:
            raise StripeGatewayError(
                error.code,
                error.message,
                retryable=False,
                status_code=500,
            ) from error

        self.secret_key = config[
            "secretKey"
        ]

        self.price_id = config[
            "priceId"
        ]

        self.success_url = config[
            "successUrl"
        ]

        self.cancel_url = config[
            "cancelUrl"
        ]

        self.portal_return_url = config[
            "portalReturnUrl"
        ]

        self._opener = opener

        self.timeout_seconds = int(
            timeout_seconds
        )

        if self.timeout_seconds < 1:
            raise StripeGatewayError(
                "InvalidStripeTimeout",
                (
                    "The Stripe timeout "
                    "configuration is invalid."
                ),
                retryable=False,
                status_code=500,
            )

    @property
    def livemode(
        self,
    ) -> bool:
        return _secret_is_live(
            self.secret_key
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        parameters: (
            list[tuple[str, str]]
            | None
        ) = None,
        idempotency_key: (
            str | None
        ) = None,
    ) -> dict[str, Any]:
        normalized_method = str(
            method or ""
        ).strip().upper()

        if normalized_method not in {
            "GET",
            "POST",
        }:
            raise StripeGatewayError(
                "InvalidStripeMethod",
                (
                    "The Stripe request "
                    "method is invalid."
                ),
                retryable=False,
                status_code=500,
            )

        if (
            not isinstance(path, str)
            or not path.startswith("/")
        ):
            raise StripeGatewayError(
                "InvalidStripePath",
                (
                    "The Stripe request "
                    "path is invalid."
                ),
                retryable=False,
                status_code=500,
            )

        parameters = (
            parameters or []
        )

        url = (
            STRIPE_API_BASE
            + path
        )

        request_body = None

        if normalized_method == "GET":
            if parameters:
                url += (
                    "?"
                    + urlencode(
                        parameters
                    )
                )

        else:
            request_body = urlencode(
                parameters
            ).encode("utf-8")

        headers = {
            "Authorization":
                (
                    "Bearer "
                    + self.secret_key
                ),

            "Content-Type":
                (
                    "application/"
                    "x-www-form-urlencoded"
                ),

            "User-Agent":
                (
                    "JM8-Stripe-Gateway/"
                    + STRIPE_GATEWAY_VERSION
                ),
        }

        if idempotency_key is not None:
            headers[
                "Idempotency-Key"
            ] = normalize_idempotency_key(
                idempotency_key
            )

        request = Request(
            url,
            data=request_body,
            method=normalized_method,
            headers=headers,
        )

        try:
            with self._opener(
                request,
                timeout=(
                    self.timeout_seconds
                ),
            ) as response:
                raw_body = (
                    response.read()
                )

        except HTTPError as error:
            provider_payload = None

            try:
                provider_payload = (
                    json.loads(
                        error.read().decode(
                            "utf-8"
                        )
                    )
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                AttributeError,
            ):
                provider_payload = None

            provider_code = (
                _safe_provider_code(
                    provider_payload
                )
            )

            public_code = (
                (
                    "Stripe:"
                    + provider_code
                )
                if provider_code
                else (
                    "StripeHTTP"
                    + str(error.code)
                )
            )

            raise StripeGatewayError(
                public_code,
                (
                    "Stripe could not complete "
                    "the billing request."
                ),
                retryable=(
                    error.code
                    in RETRYABLE_HTTP_STATUSES
                ),
                status_code=error.code,
            ) from error

        except (
            URLError,
            TimeoutError,
        ) as error:
            raise StripeGatewayError(
                "StripeConnectionError",
                (
                    "Stripe could not be "
                    "reached."
                ),
                retryable=True,
                status_code=503,
            ) from error

        try:
            payload = json.loads(
                raw_body.decode(
                    "utf-8"
                )
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise StripeGatewayError(
                "InvalidStripeResponse",
                (
                    "Stripe returned an "
                    "invalid response."
                ),
                retryable=False,
                status_code=502,
            ) from error

        if not isinstance(
            payload,
            dict,
        ):
            raise StripeGatewayError(
                "InvalidStripeResponse",
                (
                    "Stripe returned an "
                    "invalid response."
                ),
                retryable=False,
                status_code=502,
            )

        return payload

    def _verify_livemode(
        self,
        resource: Mapping[str, Any],
    ) -> None:
        resource_livemode = (
            resource.get(
                "livemode"
            )
        )

        if not isinstance(
            resource_livemode,
            bool,
        ):
            raise StripeGatewayError(
                "InvalidStripeResponse",
                (
                    "Stripe returned a "
                    "resource without its "
                    "mode."
                ),
                retryable=False,
                status_code=502,
            )

        if (
            resource_livemode
            != self.livemode
        ):
            raise StripeGatewayError(
                "StripeModeMismatch",
                (
                    "The Stripe resource "
                    "mode did not match the "
                    "configured account mode."
                ),
                retryable=False,
                status_code=502,
            )

    def create_customer(
        self,
        *,
        user_reference: Any,
        idempotency_key: Any,
        email: Any = None,
    ) -> dict[str, Any]:
        normalized_reference = (
            normalize_billing_user_reference(
                user_reference
            )
        )

        parameters = [
            (
                "description",
                "JM8 subscription customer",
            ),
            (
                "metadata[jm8_user_ref]",
                normalized_reference,
            ),
            (
                (
                    "metadata["
                    "jm8_billing_version"
                    "]"
                ),
                BILLING_VERSION,
            ),
        ]

        normalized_email = str(
            email or ""
        ).strip()

        if normalized_email:
            if (
                len(normalized_email) > 512
                or "@" not in normalized_email
            ):
                raise _invalid_gateway_input(
                    "InvalidBillingEmail",
                    (
                        "The authenticated "
                        "billing email is invalid."
                    ),
                )

            parameters.append(
                (
                    "email",
                    normalized_email,
                )
            )

        customer = self._request(
            "POST",
            "/customers",
            parameters=parameters,
            idempotency_key=(
                normalize_idempotency_key(
                    idempotency_key
                )
            ),
        )

        if (
            customer.get("object")
            != "customer"
            or customer.get("deleted")
            is True
        ):
            raise StripeGatewayError(
                "InvalidStripeCustomer",
                (
                    "Stripe returned an "
                    "invalid customer."
                ),
                retryable=False,
                status_code=502,
            )

        normalize_stripe_customer_id(
            customer.get("id")
        )

        self._verify_livemode(
            customer
        )

        return customer

    def retrieve_customer(
        self,
        customer_id: Any,
    ) -> dict[str, Any]:
        normalized_customer_id = (
            normalize_stripe_customer_id(
                customer_id
            )
        )

        customer = self._request(
            "GET",
            (
                "/customers/"
                + quote(
                    normalized_customer_id,
                    safe="",
                )
            ),
        )

        if (
            customer.get("object")
            != "customer"
            or customer.get("deleted")
            is True
        ):
            raise StripeGatewayError(
                "StripeCustomerUnavailable",
                (
                    "The Stripe customer "
                    "is unavailable."
                ),
                retryable=False,
                status_code=409,
            )

        self._verify_livemode(
            customer
        )

        return customer

    def list_price_subscriptions(
        self,
        customer_id: Any,
    ) -> list[dict[str, Any]]:
        normalized_customer_id = (
            normalize_stripe_customer_id(
                customer_id
            )
        )

        result = self._request(
            "GET",
            "/subscriptions",
            parameters=[
                (
                    "customer",
                    normalized_customer_id,
                ),
                (
                    "price",
                    self.price_id,
                ),
                (
                    "status",
                    "all",
                ),
                (
                    "limit",
                    "100",
                ),
            ],
        )

        if (
            result.get("object")
            != "list"
            or not isinstance(
                result.get("data"),
                list,
            )
        ):
            raise StripeGatewayError(
                "InvalidStripeSubscriptionList",
                (
                    "Stripe returned an "
                    "invalid subscription list."
                ),
                retryable=False,
                status_code=502,
            )

        if result.get("has_more") is True:
            raise StripeGatewayError(
                "StripeSubscriptionOverflow",
                (
                    "The Stripe subscription "
                    "history requires manual "
                    "review."
                ),
                retryable=False,
                status_code=409,
            )

        subscriptions = []

        for subscription in result[
            "data"
        ]:
            if not isinstance(
                subscription,
                dict,
            ):
                raise StripeGatewayError(
                    (
                        "InvalidStripe"
                        "SubscriptionList"
                    ),
                    (
                        "Stripe returned an "
                        "invalid subscription."
                    ),
                    retryable=False,
                    status_code=502,
                )

            if (
                subscription.get(
                    "object"
                )
                != "subscription"
            ):
                raise StripeGatewayError(
                    (
                        "InvalidStripe"
                        "SubscriptionList"
                    ),
                    (
                        "Stripe returned an "
                        "invalid subscription."
                    ),
                    retryable=False,
                    status_code=502,
                )

            self._verify_livemode(
                subscription
            )

            subscriptions.append(
                subscription
            )

        return subscriptions

    def find_blocking_subscription(
        self,
        customer_id: Any,
    ) -> dict[str, Any] | None:
        subscriptions = (
            self.list_price_subscriptions(
                customer_id
            )
        )

        for subscription in subscriptions:
            status = str(
                subscription.get(
                    "status"
                )
                or ""
            ).strip().lower()

            if (
                status
                in BLOCKING_SUBSCRIPTION_STATUSES
            ):
                return subscription

        return None

    def create_checkout_session(
        self,
        *,
        customer_id: Any,
        user_reference: Any,
        idempotency_key: Any,
    ) -> dict[str, Any]:
        normalized_customer_id = (
            normalize_stripe_customer_id(
                customer_id
            )
        )

        normalized_reference = (
            normalize_billing_user_reference(
                user_reference
            )
        )

        session = self._request(
            "POST",
            "/checkout/sessions",
            parameters=[
                (
                    "mode",
                    "subscription",
                ),
                (
                    "customer",
                    normalized_customer_id,
                ),
                (
                    "client_reference_id",
                    normalized_reference,
                ),
                (
                    "line_items[0][price]",
                    self.price_id,
                ),
                (
                    "line_items[0][quantity]",
                    "1",
                ),
                (
                    "success_url",
                    self.success_url,
                ),
                (
                    "cancel_url",
                    self.cancel_url,
                ),
                (
                    "metadata[jm8_offer_id]",
                    BILLING_OFFER_PRO_MONTHLY,
                ),
                (
                    "metadata[jm8_plan_id]",
                    PLAN_PRO,
                ),
                (
                    "metadata[jm8_user_ref]",
                    normalized_reference,
                ),
                (
                    (
                        "subscription_data"
                        "[metadata]"
                        "[jm8_offer_id]"
                    ),
                    BILLING_OFFER_PRO_MONTHLY,
                ),
                (
                    (
                        "subscription_data"
                        "[metadata]"
                        "[jm8_plan_id]"
                    ),
                    PLAN_PRO,
                ),
                (
                    (
                        "subscription_data"
                        "[metadata]"
                        "[jm8_user_ref]"
                    ),
                    normalized_reference,
                ),
            ],
            idempotency_key=(
                normalize_idempotency_key(
                    idempotency_key
                )
            ),
        )

        if (
            session.get("object")
            != "checkout.session"
            or session.get("mode")
            != "subscription"
        ):
            raise StripeGatewayError(
                "InvalidCheckoutSession",
                (
                    "Stripe returned an "
                    "invalid Checkout Session."
                ),
                retryable=False,
                status_code=502,
            )

        if (
            session.get("customer")
            != normalized_customer_id
        ):
            raise StripeGatewayError(
                "CheckoutCustomerMismatch",
                (
                    "The Stripe Checkout "
                    "customer did not match."
                ),
                retryable=False,
                status_code=502,
            )

        self._verify_livemode(
            session
        )

        _checkout_url(
            session.get("url")
        )

        return session


def build_public_checkout_result(
    session: Mapping[str, Any],
) -> dict[str, str]:
    if not isinstance(
        session,
        Mapping,
    ):
        raise StripeGatewayError(
            "InvalidCheckoutSession",
            (
                "The Checkout Session "
                "result is invalid."
            ),
            retryable=False,
            status_code=502,
        )

    return {
        "checkoutUrl": _checkout_url(
            session.get("url")
        ),
    }
