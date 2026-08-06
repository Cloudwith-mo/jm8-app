import json
import os
import unittest
from unittest.mock import (
    patch,
)


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

os.environ.setdefault(
    "TABLE_NAME",
    "journalm8-test-main",
)

os.environ.setdefault(
    "RAW_BUCKET",
    "journalm8-test-raw",
)


import app  # noqa: E402

from billing_checkout import (  # noqa: E402
    BillingCheckoutError,
)


CHECKOUT_URL = (
    "https://checkout.stripe.com/"
    "c/pay/test-route"
)


def checkout_event(
    *,
    body=None,
    claims=None,
):
    return {
        "requestContext": {
            "http": {
                "method": "POST",
                "path":
                    "/billing/checkout",
            },
            "authorizer": {
                "jwt": {
                    "claims": (
                        claims
                        if claims is not None
                        else {
                            "sub":
                                "jwt-user",

                            "email":
                                (
                                    "trusted"
                                    "@example.com"
                                ),
                        }
                    )
                }
            },
        },
        "body": (
            json.dumps(
                body
                if body is not None
                else {
                    "requestToken":
                        "request-123"
                }
            )
        ),
    }


def webhook_event():
    return {
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/billing/webhook",
            }
        },
        "headers": {"Stripe-Signature": "test-signature"},
        "body": "{}",
    }


def portal_event():
    return {
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/billing/portal",
            },
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "jwt-user",
                    }
                }
            },
        },
        "body": "{}",
    }


class BillingCheckoutRouteTests(
    unittest.TestCase
):
    def test_webhook_route_runs_before_authenticated_user_resolution(self):
        with patch.object(
            app,
            "process_billing_webhook",
            return_value={"received": True},
        ), patch.object(
            app,
            "get_user_id",
            side_effect=AssertionError("webhook must not require a JWT"),
        ):
            result = app.lambda_handler(webhook_event(), None)

        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(json.loads(result["body"]), {"received": True})

    def test_success_uses_jwt_identity_and_email(
        self,
    ):
        captured = {}

        def create_checkout(
            **kwargs,
        ):
            captured.update(
                kwargs
            )

            return {
                "checkoutUrl":
                    CHECKOUT_URL,
            }

        event = checkout_event(
            body={
                "requestToken":
                    "request-123",

                "email":
                    "untrusted@example.com",
            }
        )

        with patch.object(
            app,
            "create_billing_checkout",
            side_effect=create_checkout,
        ):
            result = app.lambda_handler(
                event,
                None,
            )

        self.assertEqual(
            result["statusCode"],
            201,
        )

        self.assertEqual(
            captured["user_id"],
            "jwt-user",
        )

        self.assertEqual(
            captured["email"],
            "trusted@example.com",
        )

        self.assertEqual(
            captured["request_token"],
            "request-123",
        )

        response_body = json.loads(
            result["body"]
        )

        self.assertEqual(
            response_body,
            {
                "checkout": {
                    "checkoutUrl":
                        CHECKOUT_URL,
                }
            },
        )

    def test_portal_route_uses_jwt_identity(self):
        with patch.object(
            app,
            "create_billing_portal",
            return_value={
                "billingPortalUrl": (
                    "https://billing.stripe.com/"
                    "p/session/test-route"
                )
            },
        ) as mocked:
            result = app.lambda_handler(
                portal_event(),
                None,
            )

        self.assertEqual(result["statusCode"], 201)
        mocked.assert_called_once_with(user_id="jwt-user")

        self.assertEqual(
            json.loads(result["body"]),
            {
                "portal": {
                    "billingPortalUrl": (
                        "https://billing.stripe.com/"
                        "p/session/test-route"
                    )
                }
            },
        )

    def test_missing_email_claim_passes_none(
        self,
    ):
        captured = {}

        def create_checkout(
            **kwargs,
        ):
            captured.update(
                kwargs
            )

            return {
                "checkoutUrl":
                    CHECKOUT_URL,
            }

        event = checkout_event(
            claims={
                "sub": "jwt-user",
            }
        )

        with patch.object(
            app,
            "create_billing_checkout",
            side_effect=create_checkout,
        ):
            result = app.lambda_handler(
                event,
                None,
            )

        self.assertEqual(
            result["statusCode"],
            201,
        )

        self.assertIsNone(
            captured["email"]
        )

    def test_invalid_json_returns_400(
        self,
    ):
        event = checkout_event()
        event["body"] = "{invalid"

        with patch.object(
            app,
            "create_billing_checkout",
        ) as mocked:
            result = app.lambda_handler(
                event,
                None,
            )

        self.assertEqual(
            result["statusCode"],
            400,
        )

        mocked.assert_not_called()

    def test_non_object_body_returns_400(
        self,
    ):
        event = checkout_event()
        event["body"] = json.dumps(
            ["request-123"]
        )

        with patch.object(
            app,
            "create_billing_checkout",
        ) as mocked:
            result = app.lambda_handler(
                event,
                None,
            )

        self.assertEqual(
            result["statusCode"],
            400,
        )

        mocked.assert_not_called()

    def test_checkout_error_is_preserved(
        self,
    ):
        error = BillingCheckoutError(
            "ExistingStripeSubscription",
            (
                "A Stripe subscription "
                "already exists."
            ),
            status_code=409,
            retryable=False,
        )

        with patch.object(
            app,
            "create_billing_checkout",
            side_effect=error,
        ):
            result = app.lambda_handler(
                checkout_event(),
                None,
            )

        self.assertEqual(
            result["statusCode"],
            409,
        )

        response_body = json.loads(
            result["body"]
        )

        self.assertEqual(
            response_body["error"],
            (
                "ExistingStripe"
                "Subscription"
            ),
        )

        self.assertFalse(
            response_body["retryable"]
        )


if __name__ == "__main__":
    unittest.main()
