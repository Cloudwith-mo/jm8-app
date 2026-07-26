import io
import json
import os
import unittest
from contextlib import (
    redirect_stdout,
)
from datetime import (
    datetime,
    timedelta,
    timezone,
)
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


from entitlement_policy import (  # noqa: E402
    SOURCE_DEFAULT,
    SOURCE_STRIPE,
    STATUS_ACTIVE,
    STATUS_CANCELED,
    build_entitlement_record,
)
from entitlement_resolver import (  # noqa: E402
    EntitlementUnavailableError,
    resolve_user_entitlement,
    resolve_user_plan,
)
from entitlement_store import (  # noqa: E402
    EntitlementStoreError,
)
from usage_policy import (  # noqa: E402
    PLAN_FREE,
    PLAN_PRO,
)


FIXED_NOW = datetime(
    2026,
    7,
    26,
    20,
    0,
    tzinfo=timezone.utc,
)

FUTURE = (
    FIXED_NOW
    + timedelta(days=10)
)

PAST = (
    FIXED_NOW
    - timedelta(days=10)
)


def pro_record(
    *,
    status=STATUS_ACTIVE,
    starts_at=None,
    ends_at=None,
    cancel_at_period_end=False,
):
    return build_entitlement_record(
        plan=PLAN_PRO,
        status=status,
        source=SOURCE_STRIPE,
        access_starts_at=starts_at,
        access_ends_at=ends_at,
        cancel_at_period_end=(
            cancel_at_period_end
        ),
        updated_at=FIXED_NOW,
    )


def parsed_logs(
    output: io.StringIO,
):
    return [
        json.loads(line)
        for line
        in output.getvalue().splitlines()
        if line.strip()
    ]


class EntitlementResolverTests(
    unittest.TestCase
):
    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_missing_record_resolves_to_free(
        self,
        read_record,
    ):
        read_record.return_value = None

        entitlement = (
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

        self.assertEqual(
            entitlement[
                "subscription"
            ]["source"],
            SOURCE_DEFAULT,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_active_pro_resolves_to_pro(
        self,
        read_record,
    ):
        read_record.return_value = (
            pro_record()
        )

        entitlement = (
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_PRO,
        )

        self.assertTrue(
            entitlement[
                "access"
            ]["isPro"]
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_future_pro_resolves_to_free(
        self,
        read_record,
    ):
        read_record.return_value = (
            pro_record(
                starts_at=FUTURE,
            )
        )

        entitlement = (
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_ended_pro_resolves_to_free(
        self,
        read_record,
    ):
        read_record.return_value = (
            pro_record(
                starts_at=(
                    PAST
                    - timedelta(days=10)
                ),
                ends_at=PAST,
            )
        )

        entitlement = (
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_canceled_window_resolves_to_pro(
        self,
        read_record,
    ):
        read_record.return_value = (
            pro_record(
                status=STATUS_CANCELED,
                ends_at=FUTURE,
                cancel_at_period_end=True,
            )
        )

        entitlement = (
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_PRO,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_plan_helper_returns_effective_plan(
        self,
        read_record,
    ):
        read_record.return_value = (
            pro_record()
        )

        self.assertEqual(
            resolve_user_plan(
                "test-user",
                now=FIXED_NOW,
            ),
            PLAN_PRO,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_resolver_passes_table_resource(
        self,
        read_record,
    ):
        read_record.return_value = None
        fake_table = object()

        resolve_user_entitlement(
            "test-user",
            now=FIXED_NOW,
            table_resource=fake_table,
        )

        read_record.assert_called_once_with(
            "test-user",
            table_resource=fake_table,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_resolver_passes_fixed_now(
        self,
        read_record,
    ):
        read_record.return_value = None

        entitlement = (
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["generatedAt"],
            FIXED_NOW.isoformat(),
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_invalid_record_fails_safe_to_free(
        self,
        read_record,
    ):
        read_record.side_effect = (
            EntitlementStoreError(
                "InvalidEntitlementRecord",
                "Private malformed value.",
                retryable=False,
            )
        )

        entitlement = (
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

        self.assertEqual(
            entitlement[
                "subscription"
            ]["source"],
            SOURCE_DEFAULT,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_invalid_record_log_is_sanitized(
        self,
        read_record,
    ):
        read_record.side_effect = (
            EntitlementStoreError(
                "InvalidEntitlementRecord",
                (
                    "Private malformed "
                    "provider value."
                ),
                retryable=False,
            )
        )

        output = io.StringIO()

        with redirect_stdout(output):
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )

        serialized = output.getvalue()

        self.assertNotIn(
            "Private malformed",
            serialized,
        )

        self.assertNotIn(
            "test-user",
            serialized,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_retryable_error_raises_unavailable(
        self,
        read_record,
    ):
        read_record.side_effect = (
            EntitlementStoreError(
                "ThrottlingException",
                "Private provider message.",
                retryable=True,
            )
        )

        with self.assertRaises(
            EntitlementUnavailableError
        ) as raised:
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )

        self.assertTrue(
            raised.exception.retryable
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_retryable_payload_has_retry_after(
        self,
        read_record,
    ):
        read_record.side_effect = (
            EntitlementStoreError(
                "ThrottlingException",
                "Private provider message.",
                retryable=True,
            )
        )

        with self.assertRaises(
            EntitlementUnavailableError
        ) as raised:
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.payload[
                "retryAfterSeconds"
            ],
            2,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_nonretryable_error_raises_unavailable(
        self,
        read_record,
    ):
        read_record.side_effect = (
            EntitlementStoreError(
                "ValidationException",
                "Private provider message.",
                retryable=False,
            )
        )

        with self.assertRaises(
            EntitlementUnavailableError
        ) as raised:
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )

        self.assertFalse(
            raised.exception.retryable
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_nonretryable_payload_omits_retry_after(
        self,
        read_record,
    ):
        read_record.side_effect = (
            EntitlementStoreError(
                "ValidationException",
                "Private provider message.",
                retryable=False,
            )
        )

        with self.assertRaises(
            EntitlementUnavailableError
        ) as raised:
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )

        self.assertNotIn(
            "retryAfterSeconds",
            raised.exception.payload,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_provider_message_not_exposed(
        self,
        read_record,
    ):
        read_record.side_effect = (
            EntitlementStoreError(
                "ValidationException",
                (
                    "Synthetic provider "
                    "private detail."
                ),
                retryable=False,
            )
        )

        with self.assertRaises(
            EntitlementUnavailableError
        ) as raised:
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )

        serialized = json.dumps(
            raised.exception.payload
        )

        self.assertNotIn(
            "Synthetic provider",
            serialized,
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_authenticated_user_not_logged(
        self,
        read_record,
    ):
        read_record.return_value = (
            pro_record()
        )

        output = io.StringIO()

        with redirect_stdout(output):
            resolve_user_entitlement(
                "private-user-id",
                now=FIXED_NOW,
            )

        self.assertNotIn(
            "private-user-id",
            output.getvalue(),
        )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_projection_is_allow_listed(
        self,
        read_record,
    ):
        record = pro_record()

        record.update({
            "PK": "private-pk",
            "SK": "private-sk",
            "stripeCustomerId": (
                "private-customer"
            ),
        })

        read_record.return_value = record

        entitlement = (
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            set(entitlement),
            {
                "entitlementVersion",
                "generatedAt",
                "plan",
                "subscription",
                "access",
                "updatedAt",
            },
        )

        serialized = json.dumps(
            entitlement
        )

        for private_value in (
            "private-pk",
            "private-sk",
            "private-customer",
            "stripeCustomerId",
        ):
            self.assertNotIn(
                private_value,
                serialized,
            )

    @patch(
        (
            "entitlement_resolver."
            "get_entitlement_record"
        )
    )
    def test_resolution_log_has_safe_fields(
        self,
        read_record,
    ):
        read_record.return_value = (
            pro_record()
        )

        output = io.StringIO()

        with redirect_stdout(output):
            resolve_user_entitlement(
                "test-user",
                now=FIXED_NOW,
            )

        logs = parsed_logs(
            output
        )

        self.assertEqual(
            len(logs),
            1,
        )

        self.assertEqual(
            set(logs[0]),
            {
                "event",
                "plan",
                "status",
                "source",
                "isPro",
            },
        )


if __name__ == "__main__":
    unittest.main()
