import hashlib
import hmac
import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw")


from billing_identity import build_billing_user_reference  # noqa: E402
from account_deletion_guard import (  # noqa: E402
    AccountDeletionInProgress,
    DeletionGuardUnavailable,
)
from billing_webhook import (  # noqa: E402
    BillingWebhookError,
    process_billing_webhook,
)
import billing_webhook as webhook  # noqa: E402
from stripe_secret_loader import StripeSecretLoadError  # noqa: E402


SECRET = "whsec_test_webhook_12345678"
USER_ID = "cognito-user-123"
CUSTOMER_ID = "cus_TestCustomer123"
USER_REFERENCE = build_billing_user_reference(USER_ID)


def signed_event(payload, *, timestamp=None, secret=SECRET):
    timestamp = int(time.time()) if timestamp is None else timestamp
    body = json.dumps(payload, separators=(",", ":"))
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/billing/webhook",
            }
        },
        "headers": {"Stripe-Signature": f"t={timestamp},v1={signature}"},
        "body": body,
        "isBase64Encoded": False,
    }


def checkout_payload():
    return {
        "id": "evt_checkout_123",
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {
            "object": {
                "object": "checkout.session",
                "mode": "subscription",
                "payment_status": "paid",
                "customer": CUSTOMER_ID,
                "client_reference_id": USER_REFERENCE,
                "metadata": {
                    "jm8_user_ref": USER_REFERENCE,
                    "jm8_plan_id": "PRO",
                    "jm8_offer_id": "JM8_PRO_MONTHLY",
                },
            }
        },
    }


def customer_reader(customer_id, *, livemode):
    assert customer_id == CUSTOMER_ID
    assert livemode is False
    return {
        "userId": USER_ID,
        "userReference": USER_REFERENCE,
        "stripeCustomerId": CUSTOMER_ID,
        "livemode": False,
    }


class BillingWebhookTests(unittest.TestCase):
    def test_entitlement_write_is_blocked_or_fails_closed_during_deletion(self):
        for error, expected_status, expected_code in (
            (AccountDeletionInProgress(), 409, "AccountDeletionInProgress"),
            (DeletionGuardUnavailable(), 503, "AccountDeletionGuardUnavailable"),
        ):
            reader = MagicMock()
            creator = MagicMock()
            replacer = MagicMock()
            customer_lookup = MagicMock(side_effect=customer_reader)

            def blocked(_user_id, raised=error):
                values.assert_called_once()
                raise raised

            with (
                self.subTest(expected_code=expected_code),
                patch.object(
                    webhook,
                    "_entitlement_values",
                    wraps=webhook._entitlement_values,
                ) as values,
                self.assertRaises(BillingWebhookError) as captured,
            ):
                process_billing_webhook(
                    signed_event(checkout_payload()),
                    secret_loader=lambda _environment: {"STRIPE_WEBHOOK_SECRET": SECRET},
                    customer_reader=customer_lookup,
                    deletion_guard=blocked,
                    entitlement_reader=reader,
                    entitlement_creator=creator,
                    entitlement_replacer=replacer,
                )
            self.assertEqual(captured.exception.status_code, expected_status)
            self.assertEqual(captured.exception.code, expected_code)
            customer_lookup.assert_called_once_with(CUSTOMER_ID, livemode=False)
            reader.assert_not_called()
            creator.assert_not_called()
            replacer.assert_not_called()

    def test_completed_checkout_creates_active_pro_entitlement(self):
        writes = []

        result = process_billing_webhook(
            signed_event(checkout_payload()),
            secret_loader=lambda _environment: {
                "STRIPE_WEBHOOK_SECRET": SECRET,
            },
            customer_reader=customer_reader,
            deletion_guard=lambda _user_id: None,
            entitlement_reader=lambda _user_id: None,
            entitlement_creator=lambda user_id, **values: writes.append((user_id, values)) or values,
            entitlement_replacer=lambda *_args, **_kwargs: self.fail("replace must not run"),
        )

        self.assertTrue(result["updated"])
        self.assertEqual(writes[0][0], USER_ID)
        self.assertEqual(writes[0][1]["plan"], "PRO")
        self.assertEqual(writes[0][1]["status"], "ACTIVE")
        self.assertEqual(writes[0][1]["source"], "STRIPE")

    def test_invalid_signature_is_rejected_before_lookup(self):
        event = signed_event(checkout_payload())
        event["headers"]["Stripe-Signature"] = "t=1,v1=invalid"

        with self.assertRaises(BillingWebhookError) as captured:
            process_billing_webhook(
                event,
                now=1,
                secret_loader=lambda _environment: {
                    "STRIPE_WEBHOOK_SECRET": SECRET,
                },
                customer_reader=lambda *_args, **_kwargs: self.fail("lookup must not run"),
            )

        self.assertEqual(captured.exception.code, "InvalidWebhookSignature")
        self.assertEqual(captured.exception.status_code, 400)

    def test_unhandled_event_is_acknowledged_without_writing(self):
        payload = checkout_payload()
        payload["type"] = "invoice.created"

        result = process_billing_webhook(
            signed_event(payload),
            secret_loader=lambda _environment: {
                "STRIPE_WEBHOOK_SECRET": SECRET,
            },
            customer_reader=lambda *_args, **_kwargs: self.fail("lookup must not run"),
            entitlement_reader=lambda *_args, **_kwargs: self.fail("read must not run"),
        )

        self.assertTrue(result["ignored"])

    def test_subscription_update_replaces_existing_entitlement(self):
        payload = checkout_payload()
        payload["id"] = "evt_subscription_123"
        payload["type"] = "customer.subscription.updated"
        payload["data"]["object"] = {
            "object": "subscription",
            "status": "past_due",
            "customer": CUSTOMER_ID,
            "current_period_start": 1_700_000_000,
            "current_period_end": 1_702_592_000,
            "cancel_at_period_end": True,
            "metadata": {
                "jm8_user_ref": USER_REFERENCE,
                "jm8_plan_id": "PRO",
                "jm8_offer_id": "JM8_PRO_MONTHLY",
            },
        }
        writes = []

        process_billing_webhook(
            signed_event(payload),
            secret_loader=lambda _environment: {
                "STRIPE_WEBHOOK_SECRET": SECRET,
            },
            customer_reader=customer_reader,
            deletion_guard=lambda _user_id: None,
            entitlement_reader=lambda _user_id: {"updatedAt": "2026-01-01T00:00:00+00:00"},
            entitlement_creator=lambda *_args, **_kwargs: self.fail("create must not run"),
            entitlement_replacer=lambda user_id, **values: writes.append((user_id, values)) or values,
        )

        self.assertEqual(writes[0][1]["status"], "PAST_DUE")
        self.assertTrue(writes[0][1]["cancel_at_period_end"])
        self.assertEqual(writes[0][1]["expected_updated_at"], "2026-01-01T00:00:00+00:00")

    def test_subscription_update_treats_cancel_at_as_scheduled_cancellation(self):
        payload = checkout_payload()
        payload["id"] = "evt_subscription_cancel_at_123"
        payload["type"] = "customer.subscription.updated"
        payload["data"]["object"] = {
            "object": "subscription",
            "status": "active",
            "customer": CUSTOMER_ID,
            "current_period_start": 1_700_000_000,
            "current_period_end": 1_702_592_000,
            "cancel_at_period_end": False,
            "cancel_at": 1_702_592_000,
            "metadata": {
                "jm8_user_ref": USER_REFERENCE,
                "jm8_plan_id": "PRO",
                "jm8_offer_id": "JM8_PRO_MONTHLY",
            },
        }
        writes = []

        process_billing_webhook(
            signed_event(payload),
            secret_loader=lambda _environment: {"STRIPE_WEBHOOK_SECRET": SECRET},
            customer_reader=customer_reader,
            deletion_guard=lambda _user_id: None,
            entitlement_reader=lambda _user_id: {"updatedAt": "2026-01-01T00:00:00+00:00"},
            entitlement_creator=lambda *_args, **_kwargs: self.fail("create must not run"),
            entitlement_replacer=lambda user_id, **values: writes.append((user_id, values)) or values,
        )

        self.assertEqual(writes[0][1]["status"], "ACTIVE")
        self.assertTrue(writes[0][1]["cancel_at_period_end"])

    def test_subscription_reactivation_clears_scheduled_cancellation(self):
        payload = checkout_payload()
        payload["id"] = "evt_subscription_reactivated_123"
        payload["type"] = "customer.subscription.updated"
        payload["data"]["object"] = {
            "object": "subscription",
            "status": "active",
            "customer": CUSTOMER_ID,
            "current_period_start": 1_700_000_000,
            "current_period_end": 1_702_592_000,
            "cancel_at_period_end": False,
            "cancel_at": None,
            "metadata": {
                "jm8_user_ref": USER_REFERENCE,
                "jm8_plan_id": "PRO",
                "jm8_offer_id": "JM8_PRO_MONTHLY",
            },
        }
        writes = []

        process_billing_webhook(
            signed_event(payload),
            secret_loader=lambda _environment: {"STRIPE_WEBHOOK_SECRET": SECRET},
            customer_reader=customer_reader,
            deletion_guard=lambda _user_id: None,
            entitlement_reader=lambda _user_id: {"updatedAt": "2026-01-01T00:00:00+00:00"},
            entitlement_creator=lambda *_args, **_kwargs: self.fail("create must not run"),
            entitlement_replacer=lambda user_id, **values: writes.append((user_id, values)) or values,
        )

        self.assertFalse(writes[0][1]["cancel_at_period_end"])

    def test_configuration_failure_logs_safe_fields(self):
        event = signed_event(checkout_payload())
        event["headers"]["Authorization"] = "Bearer secret-jwt-token"
        event["headers"]["Stripe-Signature"] = "t=1700000000,v1=deadbeef"
        event["body"] = json.dumps({
            "customer_email": "alice@example.com",
            "customer": CUSTOMER_ID,
            "subscription_id": "sub_test_123",
            "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.signature",
            "journal": "earnest journal content with sensitive notes",
            "secret": "sk_test_12345",
        }, separators=(",", ":"))

        stdout = io.StringIO()

        with redirect_stdout(stdout), self.assertRaises(BillingWebhookError) as captured:
            process_billing_webhook(
                event,
                secret_loader=lambda _environment: (_ for _ in ()).throw(
                    StripeSecretLoadError(
                        "InvalidStripeSecretArn",
                        "The Stripe secret reference is not configured correctly.",
                        retryable=False,
                    )
                ),
                customer_reader=lambda *_args, **_kwargs: self.fail("lookup must not run"),
            )

        self.assertEqual(captured.exception.code, "InvalidStripeSecretArn")
        self.assertEqual(captured.exception.status_code, 500)
        self.assertFalse(captured.exception.retryable)

        payloads = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
            if line.strip()
        ]
        failure = next(item for item in payloads if item.get("event") == "billing_webhook_failed")

        self.assertEqual(failure["event"], "billing_webhook_failed")
        self.assertEqual(failure["failureCode"], "InvalidStripeSecretArn")
        self.assertFalse(failure["retryable"])
        self.assertEqual(failure["failureStage"], "configuration")

        serialized = json.dumps(payloads)
        for forbidden in (
            "Authorization", "Stripe-Signature", "customer_email",
            "alice@example.com", "customer_id", "customer",
            "subscription_id", "sub_test_123", "secret", "sk_test",
            "whsec", "JWT", "journal", "content", "eyJhbGci",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_missing_webhook_secret_fails_closed(self):
        with self.assertRaises(BillingWebhookError) as captured:
            process_billing_webhook(
                signed_event(checkout_payload()),
                secret_loader=lambda _environment: {
                    "STRIPE_SECRET_KEY": "sk_test_loader12345678",
                },
            )
        self.assertEqual(captured.exception.code, "InvalidStripeWebhookSecret")
        self.assertEqual(captured.exception.status_code, 500)
        self.assertNotIn(SECRET, captured.exception.message)


class ObservabilityDeploymentWiringTests(unittest.TestCase):
    def test_observability_scripts_configure_default_stage_and_safe_api_logging(self):
        create_api = Path("bin/create-api").read_text()
        deploy_observability = Path("bin/deploy-observability").read_text()

        def access_log_format(script_text: str) -> str:
            start = script_text.find('"Format": json.dumps({')
            self.assertNotEqual(start, -1)
            end = script_text.find("}))", start)
            self.assertNotEqual(end, -1)
            return script_text[start:end + 3]

        create_format = access_log_format(create_api)
        deploy_format = access_log_format(deploy_observability)

        self.assertIn("--stage-name '$default'", create_api)
        self.assertIn("--stage-name '$default'", deploy_observability)
        self.assertIn("DetailedMetricsEnabled=true", create_api)
        self.assertIn("DetailedMetricsEnabled=true", deploy_observability)
        self.assertIn("/aws/apigateway/${API_NAME}", create_api)
        self.assertIn("/aws/apigateway/${API_GATEWAY_NAME}", deploy_observability)
        self.assertIn("--retention-in-days 30", create_api)
        self.assertIn("--retention-in-days 30", deploy_observability)
        self.assertIn('"requestId": "$context.requestId"', create_format)
        self.assertIn('"routeKey": "$context.routeKey"', create_format)
        self.assertIn('"httpMethod": "$context.httpMethod"', create_format)
        self.assertIn('"status": "$context.status"', create_format)
        self.assertIn('"responseLatency": "$context.responseLatency"', create_format)
        self.assertIn('"integrationLatency": "$context.integrationLatency"', create_format)
        self.assertIn('"integrationError": "$context.integrationErrorMessage"', create_format)
        self.assertIn('"sourceIp": "$context.identity.sourceIp"', create_format)

        for forbidden in (
            "Authorization",
            "authorization",
            "jwt",
            "request body",
            "response body",
            "Stripe-Signature",
            "journal",
            "journal text",
        ):
            self.assertNotIn(forbidden, create_format)
            self.assertNotIn(forbidden, deploy_format)

        self.assertIn("ANALYZE_ENTRY_LOG_GROUP", deploy_observability)
        self.assertIn("$ANALYZE_ENTRY_LOG_GROUP", deploy_observability)
        self.assertIn("billing_webhook_failed", deploy_observability)
        self.assertIn("$TOPIC_ARN", deploy_observability)
        self.assertIn("billing-webhook-failures", deploy_observability)
        self.assertIn("billing_webhook_failed", deploy_observability)
        self.assertIn('"widgets"', deploy_observability)


if __name__ == "__main__":
    unittest.main()
