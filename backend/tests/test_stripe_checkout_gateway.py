import json
import unittest
from urllib.error import (
    HTTPError,
    URLError,
)
from urllib.parse import (
    parse_qs,
    urlparse,
)

from stripe_checkout_gateway import (
    BLOCKING_SUBSCRIPTION_STATUSES,
    StripeCheckoutGateway,
    StripeGatewayError,
    build_public_checkout_result,
    normalize_billing_user_reference,
    normalize_idempotency_key,
    normalize_stripe_customer_id,
)


TEST_SECRET = (
    "sk_test_"
    "checkoutgateway123456"
)

LIVE_SECRET = (
    "sk_live_"
    "checkoutgateway123456"
)

TEST_PRICE = (
    "price_"
    "checkoutgateway123456"
)

TEST_CUSTOMER = (
    "cus_checkoutgateway123"
)

TEST_USER_REFERENCE = (
    "jm8usr_"
    "1234567890abcdef"
    "1234567890abcdef"
)

TEST_IDEMPOTENCY_KEY = (
    "jm8-checkout-v1-"
    "1234567890abcdef"
)


def gateway_environment(
    *,
    secret=TEST_SECRET,
):
    return {
        "STRIPE_SECRET_KEY":
            secret,

        "STRIPE_PRO_MONTHLY_PRICE_ID":
            TEST_PRICE,

        "STRIPE_CHECKOUT_SUCCESS_URL":
            (
                "https://app.example.com/"
                "?checkout=success"
            ),

        "STRIPE_CHECKOUT_CANCEL_URL":
            (
                "https://app.example.com/"
                "?checkout=cancelled"
            ),

        "STRIPE_PORTAL_RETURN_URL":
            "https://app.example.com/",
    }


class FakeResponse:
    def __init__(
        self,
        payload,
    ):
        self.payload = payload

    def __enter__(
        self,
    ):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False

    def read(
        self,
    ):
        return json.dumps(
            self.payload
        ).encode("utf-8")


class InvalidJsonResponse:
    def __enter__(
        self,
    ):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False

    def read(
        self,
    ):
        return b"not-json"


class FakeOpener:
    def __init__(
        self,
        responses=None,
        error=None,
    ):
        self.responses = list(
            responses or []
        )

        self.error = error
        self.calls = []

    def __call__(
        self,
        request,
        *,
        timeout,
    ):
        self.calls.append({
            "request": request,
            "timeout": timeout,
        })

        if self.error:
            raise self.error

        if not self.responses:
            raise AssertionError(
                "No fake Stripe response "
                "was configured."
            )

        response = self.responses.pop(
            0
        )

        if hasattr(
            response,
            "__enter__",
        ):
            return response

        return FakeResponse(
            response
        )


def request_parameters(
    request,
):
    body = request.data or b""

    return parse_qs(
        body.decode("utf-8"),
        keep_blank_values=True,
    )


def customer_payload(
    *,
    livemode=False,
):
    return {
        "id": TEST_CUSTOMER,
        "object": "customer",
        "deleted": False,
        "livemode": livemode,
    }


def session_payload(
    *,
    livemode=False,
):
    return {
        "id": (
            "cs_test_"
            "checkoutgateway123"
        ),
        "object": "checkout.session",
        "mode": "subscription",
        "customer": TEST_CUSTOMER,
        "livemode": livemode,
        "url": (
            "https://checkout.stripe.com/"
            "c/pay/test-session"
        ),
    }


def subscription_payload(
    status,
    *,
    livemode=False,
):
    return {
        "id": (
            "sub_"
            + status.replace(
                "_",
                "",
            )
            + "123456"
        ),
        "object": "subscription",
        "status": status,
        "livemode": livemode,
    }


class StripeCheckoutGatewayTests(
    unittest.TestCase
):
    def test_invalid_customer_id_rejected(
        self,
    ):
        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            normalize_stripe_customer_id(
                "invalid"
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidStripeCustomer",
        )

    def test_invalid_user_reference_rejected(
        self,
    ):
        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            normalize_billing_user_reference(
                "raw-user-id"
            )

        self.assertEqual(
            raised.exception.code,
            (
                "InvalidBilling"
                "UserReference"
            ),
        )

    def test_invalid_idempotency_key_rejected(
        self,
    ):
        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            normalize_idempotency_key(
                "invalid key"
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidIdempotencyKey",
        )

    def test_test_gateway_is_not_livemode(
        self,
    ):
        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=FakeOpener(),
        )

        self.assertFalse(
            gateway.livemode
        )

    def test_live_gateway_is_livemode(
        self,
    ):
        gateway = StripeCheckoutGateway(
            environ=gateway_environment(
                secret=LIVE_SECRET
            ),
            opener=FakeOpener(),
        )

        self.assertTrue(
            gateway.livemode
        )

    def test_customer_creation_uses_idempotency(
        self,
    ):
        opener = FakeOpener(
            responses=[
                customer_payload()
            ]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        customer = gateway.create_customer(
            user_reference=(
                TEST_USER_REFERENCE
            ),
            idempotency_key=(
                TEST_IDEMPOTENCY_KEY
            ),
        )

        self.assertEqual(
            customer["id"],
            TEST_CUSTOMER,
        )

        request = opener.calls[0][
            "request"
        ]

        self.assertEqual(
            request.get_method(),
            "POST",
        )

        self.assertEqual(
            request.headers.get(
                "Idempotency-key"
            ),
            TEST_IDEMPOTENCY_KEY,
        )

        parameters = request_parameters(
            request
        )

        self.assertEqual(
            parameters[
                "metadata[jm8_user_ref]"
            ],
            [TEST_USER_REFERENCE],
        )

    def test_customer_email_is_optional(
        self,
    ):
        opener = FakeOpener(
            responses=[
                customer_payload()
            ]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        gateway.create_customer(
            user_reference=(
                TEST_USER_REFERENCE
            ),
            idempotency_key=(
                TEST_IDEMPOTENCY_KEY
            ),
        )

        parameters = request_parameters(
            opener.calls[0]["request"]
        )

        self.assertNotIn(
            "email",
            parameters,
        )

    def test_invalid_customer_email_rejected(
        self,
    ):
        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=FakeOpener(),
        )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            gateway.create_customer(
                user_reference=(
                    TEST_USER_REFERENCE
                ),
                idempotency_key=(
                    TEST_IDEMPOTENCY_KEY
                ),
                email="not-an-email",
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidBillingEmail",
        )

    def test_mode_mismatch_is_rejected(
        self,
    ):
        opener = FakeOpener(
            responses=[
                customer_payload(
                    livemode=True
                )
            ]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            gateway.create_customer(
                user_reference=(
                    TEST_USER_REFERENCE
                ),
                idempotency_key=(
                    TEST_IDEMPOTENCY_KEY
                ),
            )

        self.assertEqual(
            raised.exception.code,
            "StripeModeMismatch",
        )

    def test_customer_retrieval_is_scoped(
        self,
    ):
        opener = FakeOpener(
            responses=[
                customer_payload()
            ]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        gateway.retrieve_customer(
            TEST_CUSTOMER
        )

        request = opener.calls[0][
            "request"
        ]

        self.assertTrue(
            request.full_url.endswith(
                (
                    "/customers/"
                    + TEST_CUSTOMER
                )
            )
        )

    def test_subscription_list_is_customer_and_price_scoped(
        self,
    ):
        opener = FakeOpener(
            responses=[{
                "object": "list",
                "data": [],
                "has_more": False,
            }]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        result = (
            gateway.list_price_subscriptions(
                TEST_CUSTOMER
            )
        )

        self.assertEqual(
            result,
            [],
        )

        request = opener.calls[0][
            "request"
        ]

        query = parse_qs(
            urlparse(
                request.full_url
            ).query
        )

        self.assertEqual(
            query["customer"],
            [TEST_CUSTOMER],
        )

        self.assertEqual(
            query["price"],
            [TEST_PRICE],
        )

        self.assertEqual(
            query["status"],
            ["all"],
        )

    def test_blocking_statuses_prevent_new_checkout(
        self,
    ):
        for status in sorted(
            BLOCKING_SUBSCRIPTION_STATUSES
        ):
            with self.subTest(
                status=status
            ):
                opener = FakeOpener(
                    responses=[{
                        "object": "list",
                        "data": [
                            subscription_payload(
                                status
                            )
                        ],
                        "has_more": False,
                    }]
                )

                gateway = (
                    StripeCheckoutGateway(
                        environ=(
                            gateway_environment()
                        ),
                        opener=opener,
                    )
                )

                existing = (
                    gateway
                    .find_blocking_subscription(
                        TEST_CUSTOMER
                    )
                )

                self.assertIsNotNone(
                    existing
                )

                self.assertEqual(
                    existing["status"],
                    status,
                )

    def test_terminal_statuses_allow_checkout(
        self,
    ):
        opener = FakeOpener(
            responses=[{
                "object": "list",
                "data": [
                    subscription_payload(
                        "canceled"
                    ),
                    subscription_payload(
                        "incomplete_expired"
                    ),
                ],
                "has_more": False,
            }]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        self.assertIsNone(
            gateway.find_blocking_subscription(
                TEST_CUSTOMER
            )
        )

    def test_subscription_overflow_is_rejected(
        self,
    ):
        opener = FakeOpener(
            responses=[{
                "object": "list",
                "data": [],
                "has_more": True,
            }]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            gateway.list_price_subscriptions(
                TEST_CUSTOMER
            )

        self.assertEqual(
            raised.exception.code,
            (
                "StripeSubscription"
                "Overflow"
            ),
        )

    def test_checkout_uses_subscription_mode(
        self,
    ):
        opener = FakeOpener(
            responses=[
                session_payload()
            ]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        session = (
            gateway.create_checkout_session(
                customer_id=TEST_CUSTOMER,
                user_reference=(
                    TEST_USER_REFERENCE
                ),
                idempotency_key=(
                    TEST_IDEMPOTENCY_KEY
                ),
            )
        )

        self.assertEqual(
            session["mode"],
            "subscription",
        )

        parameters = request_parameters(
            opener.calls[0]["request"]
        )

        self.assertEqual(
            parameters["mode"],
            ["subscription"],
        )

        self.assertEqual(
            parameters[
                "line_items[0][price]"
            ],
            [TEST_PRICE],
        )

        self.assertEqual(
            parameters[
                "line_items[0][quantity]"
            ],
            ["1"],
        )

    def test_checkout_uses_configured_urls(
        self,
    ):
        opener = FakeOpener(
            responses=[
                session_payload()
            ]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        gateway.create_checkout_session(
            customer_id=TEST_CUSTOMER,
            user_reference=(
                TEST_USER_REFERENCE
            ),
            idempotency_key=(
                TEST_IDEMPOTENCY_KEY
            ),
        )

        parameters = request_parameters(
            opener.calls[0]["request"]
        )

        self.assertEqual(
            parameters["success_url"],
            [
                gateway_environment()[
                    (
                        "STRIPE_CHECKOUT"
                        "_SUCCESS_URL"
                    )
                ]
            ],
        )

        self.assertEqual(
            parameters["cancel_url"],
            [
                gateway_environment()[
                    (
                        "STRIPE_CHECKOUT"
                        "_CANCEL_URL"
                    )
                ]
            ],
        )

    def test_checkout_customer_mismatch_rejected(
        self,
    ):
        payload = session_payload()

        payload["customer"] = (
            "cus_othercustomer123"
        )

        opener = FakeOpener(
            responses=[payload]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            gateway.create_checkout_session(
                customer_id=TEST_CUSTOMER,
                user_reference=(
                    TEST_USER_REFERENCE
                ),
                idempotency_key=(
                    TEST_IDEMPOTENCY_KEY
                ),
            )

        self.assertEqual(
            raised.exception.code,
            (
                "CheckoutCustomer"
                "Mismatch"
            ),
        )

    def test_invalid_checkout_host_rejected(
        self,
    ):
        payload = session_payload()

        payload["url"] = (
            "https://example.com/"
            "not-stripe"
        )

        opener = FakeOpener(
            responses=[payload]
        )

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=opener,
        )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            gateway.create_checkout_session(
                customer_id=TEST_CUSTOMER,
                user_reference=(
                    TEST_USER_REFERENCE
                ),
                idempotency_key=(
                    TEST_IDEMPOTENCY_KEY
                ),
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidCheckoutSession",
        )

    def test_public_result_contains_only_url(
        self,
    ):
        result = (
            build_public_checkout_result(
                session_payload()
            )
        )

        self.assertEqual(
            set(result),
            {
                "checkoutUrl",
            },
        )

        serialized = json.dumps(
            result
        )

        self.assertNotIn(
            TEST_CUSTOMER,
            serialized,
        )

        self.assertNotIn(
            "cs_test_",
            serialized,
        )

    def test_http_429_is_retryable_and_sanitized(
        self,
    ):
        private_detail = (
            "private provider detail"
        )

        error_body = json.dumps({
            "error": {
                "code":
                    "rate_limit_error",
                "message":
                    private_detail,
            }
        }).encode("utf-8")

        error = HTTPError(
            (
                "https://api.stripe.com/"
                "v1/customers"
            ),
            429,
            private_detail,
            {},
            None,
        )

        error.read = lambda: error_body

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=FakeOpener(
                error=error
            ),
        )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            gateway.retrieve_customer(
                TEST_CUSTOMER
            )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertEqual(
            raised.exception.status_code,
            429,
        )

        self.assertNotIn(
            private_detail,
            raised.exception.message,
        )

        self.assertNotIn(
            TEST_SECRET,
            raised.exception.message,
        )

    def test_http_400_is_not_retryable(
        self,
    ):
        error = HTTPError(
            (
                "https://api.stripe.com/"
                "v1/customers"
            ),
            400,
            "private",
            {},
            None,
        )

        error.read = lambda: b"{}"

        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=FakeOpener(
                error=error
            ),
        )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            gateway.retrieve_customer(
                TEST_CUSTOMER
            )

        self.assertFalse(
            raised.exception.retryable
        )

        self.assertEqual(
            raised.exception.status_code,
            400,
        )

    def test_connection_error_is_retryable(
        self,
    ):
        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=FakeOpener(
                error=URLError(
                    "private failure"
                )
            ),
        )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            gateway.retrieve_customer(
                TEST_CUSTOMER
            )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertEqual(
            raised.exception.code,
            "StripeConnectionError",
        )

    def test_invalid_json_is_rejected(
        self,
    ):
        gateway = StripeCheckoutGateway(
            environ=gateway_environment(),
            opener=FakeOpener(
                responses=[
                    InvalidJsonResponse()
                ]
            ),
        )

        with self.assertRaises(
            StripeGatewayError
        ) as raised:
            gateway.retrieve_customer(
                TEST_CUSTOMER
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidStripeResponse",
        )


if __name__ == "__main__":
    unittest.main()
