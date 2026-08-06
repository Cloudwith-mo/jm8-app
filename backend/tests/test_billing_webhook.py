import hashlib
import hmac
import json
import os
import time
import unittest


os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw")


from billing_identity import build_billing_user_reference  # noqa: E402
from billing_webhook import (  # noqa: E402
    BillingWebhookError,
    process_billing_webhook,
)


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
    def test_completed_checkout_creates_active_pro_entitlement(self):
        writes = []

        result = process_billing_webhook(
            signed_event(checkout_payload()),
            secret_loader=lambda _environment: {
                "STRIPE_WEBHOOK_SECRET": SECRET,
            },
            customer_reader=customer_reader,
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
            entitlement_reader=lambda _user_id: {"updatedAt": "2026-01-01T00:00:00+00:00"},
            entitlement_creator=lambda *_args, **_kwargs: self.fail("create must not run"),
            entitlement_replacer=lambda user_id, **values: writes.append((user_id, values)) or values,
        )

        self.assertFalse(writes[0][1]["cancel_at_period_end"])


if __name__ == "__main__":
    unittest.main()
