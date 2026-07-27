import json
import os
import unittest


os.environ.setdefault(
    "AWS_ACCESS_KEY_ID",
    "testing",
)
os.environ.setdefault(
    "AWS_SECRET_ACCESS_KEY",
    "testing",
)
os.environ.setdefault(
    "AWS_DEFAULT_REGION",
    "us-east-1",
)
os.environ.setdefault(
    "AWS_EC2_METADATA_DISABLED",
    "true",
)


from billing_policy import (  # noqa: E402
    BILLING_CURRENCY,
    BILLING_INTERVAL,
    BILLING_INTERVAL_COUNT,
    BILLING_OFFER_PRO_MONTHLY,
    BILLING_PROVIDER_STRIPE,
    BILLING_UNIT_AMOUNT,
    STRIPE_PRO_MONTHLY_LOOKUP_KEY,
    BillingPolicyError,
    get_public_billing_offer,
    load_stripe_checkout_config,
    load_stripe_webhook_config,
)
from usage_policy import (  # noqa: E402
    PLAN_PRO,
)


TEST_SECRET_KEY = (
    "sk_test_"
    "billingcontract123456"
)

LIVE_SECRET_KEY = (
    "sk_live_"
    "billingcontract123456"
)

TEST_WEBHOOK_SECRET = (
    "whsec_"
    "billingcontract123456"
)

TEST_PRICE_ID = (
    "price_"
    "billingcontract123456"
)


def checkout_environment():
    return {
        "STRIPE_SECRET_KEY":
            TEST_SECRET_KEY,

        "STRIPE_PRO_MONTHLY_PRICE_ID":
            TEST_PRICE_ID,

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


class BillingPolicyTests(
    unittest.TestCase
):
    def test_public_offer_is_ten_dollars(
        self,
    ):
        offer = get_public_billing_offer()

        self.assertEqual(
            offer["offer"][
                "amount"
            ]["currency"],
            BILLING_CURRENCY,
        )

        self.assertEqual(
            offer["offer"][
                "amount"
            ]["unitAmount"],
            BILLING_UNIT_AMOUNT,
        )

        self.assertEqual(
            offer["offer"][
                "amount"
            ]["display"],
            "$10.00",
        )

        self.assertEqual(
            BILLING_UNIT_AMOUNT,
            1_000,
        )

    def test_public_offer_targets_pro(
        self,
    ):
        offer = get_public_billing_offer()

        self.assertEqual(
            offer["offer"][
                "plan"
            ]["id"],
            PLAN_PRO,
        )

        self.assertEqual(
            offer["offer"]["id"],
            BILLING_OFFER_PRO_MONTHLY,
        )

    def test_stripe_lookup_key_is_stable(
        self,
    ):
        self.assertEqual(
            STRIPE_PRO_MONTHLY_LOOKUP_KEY,
            "jm8_pro_monthly",
        )

    def test_public_offer_is_monthly(
        self,
    ):
        offer = get_public_billing_offer()

        recurring = offer[
            "offer"
        ]["recurring"]

        self.assertEqual(
            recurring["interval"],
            BILLING_INTERVAL,
        )

        self.assertEqual(
            recurring[
                "intervalCount"
            ],
            BILLING_INTERVAL_COUNT,
        )

    def test_public_offer_is_allow_listed(
        self,
    ):
        offer = get_public_billing_offer()

        self.assertEqual(
            set(offer),
            {
                "billingVersion",
                "provider",
                "offer",
            },
        )

        self.assertEqual(
            offer["provider"],
            BILLING_PROVIDER_STRIPE,
        )

    def test_checkout_accepts_test_key(
        self,
    ):
        config = (
            load_stripe_checkout_config(
                checkout_environment()
            )
        )

        self.assertEqual(
            config["secretKey"],
            TEST_SECRET_KEY,
        )

    def test_checkout_accepts_live_key(
        self,
    ):
        environ = checkout_environment()

        environ[
            "STRIPE_SECRET_KEY"
        ] = LIVE_SECRET_KEY

        config = (
            load_stripe_checkout_config(
                environ
            )
        )

        self.assertEqual(
            config["secretKey"],
            LIVE_SECRET_KEY,
        )

    def test_checkout_normalizes_whitespace(
        self,
    ):
        environ = checkout_environment()

        environ[
            "STRIPE_SECRET_KEY"
        ] = (
            f"  {TEST_SECRET_KEY}  "
        )

        environ[
            "STRIPE_PRO_MONTHLY_PRICE_ID"
        ] = (
            f"  {TEST_PRICE_ID}  "
        )

        config = (
            load_stripe_checkout_config(
                environ
            )
        )

        self.assertEqual(
            config["secretKey"],
            TEST_SECRET_KEY,
        )

        self.assertEqual(
            config["priceId"],
            TEST_PRICE_ID,
        )

    def test_missing_secret_is_rejected(
        self,
    ):
        environ = checkout_environment()

        environ.pop(
            "STRIPE_SECRET_KEY"
        )

        with self.assertRaises(
            BillingPolicyError
        ) as raised:
            load_stripe_checkout_config(
                environ
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidStripeSecretKey",
        )

    def test_invalid_secret_is_rejected(
        self,
    ):
        environ = checkout_environment()

        environ[
            "STRIPE_SECRET_KEY"
        ] = "invalid-secret"

        with self.assertRaises(
            BillingPolicyError
        ):
            load_stripe_checkout_config(
                environ
            )

    def test_invalid_price_is_rejected(
        self,
    ):
        environ = checkout_environment()

        environ[
            "STRIPE_PRO_MONTHLY_PRICE_ID"
        ] = "invalid-price"

        with self.assertRaises(
            BillingPolicyError
        ) as raised:
            load_stripe_checkout_config(
                environ
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidStripePriceId",
        )

    def test_https_urls_are_accepted(
        self,
    ):
        config = (
            load_stripe_checkout_config(
                checkout_environment()
            )
        )

        self.assertTrue(
            config["successUrl"]
            .startswith("https://")
        )

        self.assertTrue(
            config["cancelUrl"]
            .startswith("https://")
        )

        self.assertTrue(
            config["portalReturnUrl"]
            .startswith("https://")
        )

    def test_local_http_urls_are_accepted(
        self,
    ):
        environ = checkout_environment()

        environ[
            "STRIPE_CHECKOUT_SUCCESS_URL"
        ] = (
            "http://localhost:5173/"
            "?checkout=success"
        )

        environ[
            "STRIPE_CHECKOUT_CANCEL_URL"
        ] = (
            "http://127.0.0.1:5173/"
            "?checkout=cancelled"
        )

        environ[
            "STRIPE_PORTAL_RETURN_URL"
        ] = (
            "http://localhost:5173/"
        )

        config = (
            load_stripe_checkout_config(
                environ
            )
        )

        self.assertTrue(
            config["successUrl"]
            .startswith("http://")
        )

    def test_nonlocal_http_url_is_rejected(
        self,
    ):
        environ = checkout_environment()

        environ[
            "STRIPE_CHECKOUT_SUCCESS_URL"
        ] = (
            "http://app.example.com/"
        )

        with self.assertRaises(
            BillingPolicyError
        ) as raised:
            load_stripe_checkout_config(
                environ
            )

        self.assertEqual(
            raised.exception.code,
            "InsecureBillingReturnUrl",
        )

    def test_webhook_secret_is_accepted(
        self,
    ):
        config = (
            load_stripe_webhook_config({
                "STRIPE_WEBHOOK_SECRET":
                    TEST_WEBHOOK_SECRET,
            })
        )

        self.assertEqual(
            config["webhookSecret"],
            TEST_WEBHOOK_SECRET,
        )

    def test_invalid_webhook_secret_is_rejected(
        self,
    ):
        with self.assertRaises(
            BillingPolicyError
        ) as raised:
            load_stripe_webhook_config({
                "STRIPE_WEBHOOK_SECRET":
                    "invalid",
            })

        self.assertEqual(
            raised.exception.code,
            (
                "InvalidStripe"
                "WebhookSecret"
            ),
        )

    def test_errors_do_not_expose_secret(
        self,
    ):
        private_value = (
            "private-secret-value"
        )

        environ = checkout_environment()

        environ[
            "STRIPE_SECRET_KEY"
        ] = private_value

        with self.assertRaises(
            BillingPolicyError
        ) as raised:
            load_stripe_checkout_config(
                environ
            )

        serialized = json.dumps({
            "code":
                raised.exception.code,
            "message":
                raised.exception.message,
        })

        self.assertNotIn(
            private_value,
            serialized,
        )

    def test_public_offer_excludes_provider_config(
        self,
    ):
        offer = get_public_billing_offer()

        serialized = json.dumps(
            offer
        )

        for forbidden in (
            TEST_SECRET_KEY,
            TEST_WEBHOOK_SECRET,
            TEST_PRICE_ID,
            "secretKey",
            "webhookSecret",
            "priceId",
            "successUrl",
            "cancelUrl",
            "portalReturnUrl",
        ):
            self.assertNotIn(
                forbidden,
                serialized,
            )


if __name__ == "__main__":
    unittest.main()
