import io
import unittest
from contextlib import (
    redirect_stdout,
)

from account_entitlement import (
    AccountEntitlementUnavailableError,
)
from billing_checkout import (
    BillingCheckoutError,
    create_billing_checkout,
)
from billing_customer_store import (
    BillingCustomerConflictError,
    BillingCustomerStoreError,
)
from billing_identity import (
    build_billing_user_reference,
    build_checkout_idempotency_key,
    build_customer_idempotency_key,
)
from stripe_checkout_gateway import (
    StripeGatewayError,
)


TEST_USER = "private-cognito-user"

TEST_CUSTOMER = (
    "cus_checkoutapi123"
)

TEST_REFERENCE = (
    build_billing_user_reference(
        TEST_USER
    )
)

CHECKOUT_URL = (
    "https://checkout.stripe.com/"
    "c/pay/test-checkout"
)


def free_entitlement():
    return {
        "access": {
            "isPro": False,
        }
    }


def pro_entitlement():
    return {
        "access": {
            "isPro": True,
        }
    }


def stored_mapping(
    *,
    customer_id=TEST_CUSTOMER,
    livemode=False,
    user_reference=TEST_REFERENCE,
    created=False,
):
    return {
        "stripeCustomerId":
            customer_id,

        "livemode":
            livemode,

        "userReference":
            user_reference,

        "_createdInRequest":
            created,
    }


class FakeGateway:
    def __init__(
        self,
        *,
        livemode=False,
        blocking_subscription=None,
        retrieve_error=None,
        customer_error=None,
        subscription_error=None,
        checkout_error=None,
    ):
        self.livemode = livemode

        self.blocking_subscription = (
            blocking_subscription
        )

        self.retrieve_error = (
            retrieve_error
        )

        self.customer_error = (
            customer_error
        )

        self.subscription_error = (
            subscription_error
        )

        self.checkout_error = (
            checkout_error
        )

        self.calls = []

    def retrieve_customer(
        self,
        customer_id,
    ):
        self.calls.append({
            "operation":
                "retrieveCustomer",

            "customerId":
                customer_id,
        })

        if self.retrieve_error:
            raise self.retrieve_error

        return {
            "id": customer_id,
            "object": "customer",
            "livemode": self.livemode,
        }

    def create_customer(
        self,
        *,
        user_reference,
        idempotency_key,
        email=None,
    ):
        self.calls.append({
            "operation":
                "createCustomer",

            "userReference":
                user_reference,

            "idempotencyKey":
                idempotency_key,

            "email":
                email,
        })

        if self.customer_error:
            raise self.customer_error

        return {
            "id": TEST_CUSTOMER,
            "object": "customer",
            "livemode": self.livemode,
        }

    def find_blocking_subscription(
        self,
        customer_id,
    ):
        self.calls.append({
            "operation":
                "findSubscription",

            "customerId":
                customer_id,
        })

        if self.subscription_error:
            raise self.subscription_error

        return self.blocking_subscription

    def create_checkout_session(
        self,
        *,
        customer_id,
        user_reference,
        idempotency_key,
    ):
        self.calls.append({
            "operation":
                "createCheckout",

            "customerId":
                customer_id,

            "userReference":
                user_reference,

            "idempotencyKey":
                idempotency_key,
        })

        if self.checkout_error:
            raise self.checkout_error

        return {
            "object":
                "checkout.session",

            "mode":
                "subscription",

            "customer":
                customer_id,

            "livemode":
                self.livemode,

            "url":
                CHECKOUT_URL,
        }


class BillingCheckoutTests(
    unittest.TestCase
):
    def test_invalid_user_rejected(
        self,
    ):
        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=" ",
                request_token="request-1",
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            400,
        )

    def test_invalid_request_token_rejected(
        self,
    ):
        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token=(
                    "invalid token"
                ),
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
            )

        self.assertEqual(
            raised.exception.code,
            (
                "InvalidBilling"
                "RequestToken"
            ),
        )

    def test_existing_pro_blocked_before_gateway(
        self,
    ):
        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                entitlement_reader=(
                    lambda user_id:
                        pro_entitlement()
                ),
            )

        self.assertEqual(
            raised.exception.code,
            "AccountAlreadyPro",
        )

        self.assertEqual(
            raised.exception.status_code,
            409,
        )

    def test_existing_mapping_reuses_customer(
        self,
    ):
        gateway = FakeGateway()

        result = create_billing_checkout(
            user_id=TEST_USER,
            request_token="request-1",
            gateway=gateway,
            entitlement_reader=(
                lambda user_id:
                    free_entitlement()
            ),
            mapping_reader=(
                lambda user_id:
                    stored_mapping()
            ),
        )

        operations = [
            call["operation"]
            for call in gateway.calls
        ]

        self.assertNotIn(
            "createCustomer",
            operations,
        )

        self.assertIn(
            "retrieveCustomer",
            operations,
        )

        checkout_call = next(
            call
            for call in gateway.calls
            if call["operation"]
            == "createCheckout"
        )

        self.assertEqual(
            checkout_call[
                "idempotencyKey"
            ],
            build_checkout_idempotency_key(
                TEST_USER,
                "request-1",
            ),
        )

        self.assertEqual(
            result,
            {
                "checkoutUrl":
                    CHECKOUT_URL,
            },
        )

    def test_missing_mapping_creates_and_stores_customer(
        self,
    ):
        gateway = FakeGateway()
        mapping_calls = []

        def create_mapping(
            **kwargs,
        ):
            mapping_calls.append(
                kwargs
            )

            return stored_mapping(
                created=True
            )

        create_billing_checkout(
            user_id=TEST_USER,
            request_token="request-1",
            email="user@example.com",
            gateway=gateway,
            entitlement_reader=(
                lambda user_id:
                    free_entitlement()
            ),
            mapping_reader=(
                lambda user_id: None
            ),
            mapping_creator=(
                create_mapping
            ),
        )

        customer_call = next(
            call
            for call in gateway.calls
            if call["operation"]
            == "createCustomer"
        )

        self.assertEqual(
            customer_call[
                "idempotencyKey"
            ],
            build_customer_idempotency_key(
                TEST_USER
            ),
        )

        self.assertEqual(
            customer_call["email"],
            "user@example.com",
        )

        self.assertEqual(
            mapping_calls[0][
                "customer_id"
            ],
            TEST_CUSTOMER,
        )

    def test_invalid_authenticated_email_is_omitted(
        self,
    ):
        gateway = FakeGateway()

        create_billing_checkout(
            user_id=TEST_USER,
            request_token="request-1",
            email="not-an-email",
            gateway=gateway,
            entitlement_reader=(
                lambda user_id:
                    free_entitlement()
            ),
            mapping_reader=(
                lambda user_id: None
            ),
            mapping_creator=(
                lambda **kwargs:
                    stored_mapping(
                        created=True
                    )
            ),
        )

        customer_call = next(
            call
            for call in gateway.calls
            if call["operation"]
            == "createCustomer"
        )

        self.assertIsNone(
            customer_call["email"]
        )

    def test_mapping_mode_mismatch_rejected(
        self,
    ):
        gateway = FakeGateway(
            livemode=False
        )

        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=gateway,
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
                mapping_reader=(
                    lambda user_id:
                        stored_mapping(
                            livemode=True
                        )
                ),
            )

        self.assertEqual(
            raised.exception.code,
            (
                "BillingCustomer"
                "ModeMismatch"
            ),
        )

    def test_mapping_reference_mismatch_rejected(
        self,
    ):
        gateway = FakeGateway()

        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=gateway,
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
                mapping_reader=(
                    lambda user_id:
                        stored_mapping(
                            user_reference=(
                                "jm8usr_"
                                + "0" * 32
                            )
                        )
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            409,
        )

    def test_blocking_subscription_rejected(
        self,
    ):
        gateway = FakeGateway(
            blocking_subscription={
                "status": "active",
            }
        )

        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=gateway,
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
                mapping_reader=(
                    lambda user_id:
                        stored_mapping()
                ),
            )

        self.assertEqual(
            raised.exception.code,
            (
                "ExistingStripe"
                "Subscription"
            ),
        )

    def test_customer_store_conflict_returns_409(
        self,
    ):
        gateway = FakeGateway()

        def conflict(
            **kwargs,
        ):
            raise (
                BillingCustomerConflictError()
            )

        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=gateway,
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
                mapping_reader=(
                    lambda user_id: None
                ),
                mapping_creator=conflict,
            )

        self.assertEqual(
            raised.exception.status_code,
            409,
        )

    def test_retryable_store_error_returns_503(
        self,
    ):
        def failed_read(
            user_id,
        ):
            raise BillingCustomerStoreError(
                "ThrottlingException",
                "Private database detail.",
                retryable=True,
            )

        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=FakeGateway(),
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
                mapping_reader=failed_read,
            )

        self.assertEqual(
            raised.exception.status_code,
            503,
        )

        self.assertTrue(
            raised.exception.retryable
        )

    def test_nonretryable_store_error_returns_500(
        self,
    ):
        def failed_read(
            user_id,
        ):
            raise BillingCustomerStoreError(
                "ValidationException",
                "Private database detail.",
                retryable=False,
            )

        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=FakeGateway(),
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
                mapping_reader=failed_read,
            )

        self.assertEqual(
            raised.exception.status_code,
            500,
        )

    def test_retryable_stripe_error_returns_503(
        self,
    ):
        gateway = FakeGateway(
            retrieve_error=(
                StripeGatewayError(
                    "StripeConnectionError",
                    "Private provider detail.",
                    retryable=True,
                    status_code=503,
                )
            )
        )

        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=gateway,
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
                mapping_reader=(
                    lambda user_id:
                        stored_mapping()
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            503,
        )

    def test_nonretryable_stripe_error_returns_502(
        self,
    ):
        gateway = FakeGateway(
            checkout_error=(
                StripeGatewayError(
                    "Stripe:invalid_request",
                    "Private provider detail.",
                    retryable=False,
                    status_code=400,
                )
            )
        )

        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=gateway,
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
                mapping_reader=(
                    lambda user_id:
                        stored_mapping()
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            502,
        )

        self.assertNotIn(
            "Private provider",
            raised.exception.message,
        )

    def test_entitlement_unavailable_returns_503(
        self,
    ):
        def failed_entitlement(
            user_id,
        ):
            raise (
                AccountEntitlementUnavailableError(
                    retryable=True
                )
            )

        with self.assertRaises(
            BillingCheckoutError
        ) as raised:
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=FakeGateway(),
                entitlement_reader=(
                    failed_entitlement
                ),
            )

        self.assertEqual(
            raised.exception.status_code,
            503,
        )

    def test_success_log_excludes_private_identifiers(
        self,
    ):
        output = io.StringIO()

        with redirect_stdout(
            output
        ):
            create_billing_checkout(
                user_id=TEST_USER,
                request_token="request-1",
                gateway=FakeGateway(),
                entitlement_reader=(
                    lambda user_id:
                        free_entitlement()
                ),
                mapping_reader=(
                    lambda user_id:
                        stored_mapping()
                ),
            )

        rendered = output.getvalue()

        self.assertIn(
            "billing_checkout_created",
            rendered,
        )

        self.assertNotIn(
            TEST_USER,
            rendered,
        )

        self.assertNotIn(
            TEST_CUSTOMER,
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
