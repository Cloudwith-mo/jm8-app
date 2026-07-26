import json
import os
import unittest
from datetime import (
    datetime,
    timezone,
)
from unittest.mock import (
    patch,
)

from boto3.dynamodb.types import (
    TypeDeserializer,
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


from entitlement_resolver import (  # noqa: E402
    EntitlementUnavailableError,
)
from usage_policy import (  # noqa: E402
    OPERATION_ASK_JM8,
    PLAN_FREE,
    PLAN_PRO,
)
from usage_read import (  # noqa: E402
    UsageReadUnavailableError,
    get_usage_snapshot,
)
from usage_store import (  # noqa: E402
    UsageStoreError,
    reserve_monthly_usage,
)


FIXED_NOW = datetime(
    2026,
    7,
    26,
    21,
    0,
    tzinfo=timezone.utc,
)

DESERIALIZER = TypeDeserializer()


def deserialize_map(
    values,
):
    return {
        key: DESERIALIZER.deserialize(
            value
        )
        for key, value in values.items()
    }


class FakeClient:
    def __init__(self):
        self.calls = []

    def transact_write_items(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        return {}


class FakeTable:
    def __init__(
        self,
        *,
        usage_item=None,
        entitlement_item=None,
    ):
        self.usage_item = usage_item
        self.entitlement_item = (
            entitlement_item
        )

        self.calls = []

    def get_item(
        self,
        **kwargs,
    ):
        self.calls.append(
            kwargs
        )

        key = kwargs.get(
            "Key",
            {},
        )

        sk = key.get(
            "SK"
        )

        if sk == "ENTITLEMENT":
            if (
                self.entitlement_item
                is None
            ):
                return {}

            return {
                "Item": (
                    self.entitlement_item
                )
            }

        if (
            isinstance(sk, str)
            and sk.startswith(
                "USAGE#"
            )
        ):
            if self.usage_item is None:
                return {}

            return {
                "Item": self.usage_item
            }

        return {}


class EntitlementUsageIntegrationTests(
    unittest.TestCase
):
    @patch(
        "usage_store.resolve_user_plan",
        return_value=PLAN_PRO,
    )
    def test_implicit_reservation_uses_pro_plan(
        self,
        resolve_plan,
    ):
        client = FakeClient()

        reservation = (
            reserve_monthly_usage(
                "test-user",
                operation=(
                    OPERATION_ASK_JM8
                ),
                reservation_id=(
                    "usage_entitlementpro123"
                ),
                now=FIXED_NOW,
                environ={},
                client=client,
            )
        )

        self.assertEqual(
            reservation["plan"],
            PLAN_PRO,
        )

        resolve_plan.assert_called_once_with(
            "test-user",
            now=FIXED_NOW,
        )

        update = client.calls[0][
            "TransactItems"
        ][0]["Update"]

        values = deserialize_map(
            update[
                "ExpressionAttributeValues"
            ]
        )

        self.assertEqual(
            values[":limit"],
            100,
        )

        self.assertEqual(
            values[":planSnapshot"],
            PLAN_PRO,
        )

    @patch(
        "usage_store.resolve_user_plan"
    )
    def test_explicit_plan_bypasses_resolver(
        self,
        resolve_plan,
    ):
        client = FakeClient()

        reservation = (
            reserve_monthly_usage(
                "test-user",
                operation=(
                    OPERATION_ASK_JM8
                ),
                plan=PLAN_FREE,
                reservation_id=(
                    "usage_explicitfree123"
                ),
                now=FIXED_NOW,
                environ={},
                client=client,
            )
        )

        self.assertEqual(
            reservation["plan"],
            PLAN_FREE,
        )

        resolve_plan.assert_not_called()

    @patch(
        "usage_store.resolve_user_plan"
    )
    def test_entitlement_failure_blocks_reservation(
        self,
        resolve_plan,
    ):
        resolve_plan.side_effect = (
            EntitlementUnavailableError(
                retryable=True
            )
        )

        client = FakeClient()

        with self.assertRaises(
            UsageStoreError
        ) as raised:
            reserve_monthly_usage(
                "test-user",
                operation=(
                    OPERATION_ASK_JM8
                ),
                reservation_id=(
                    "usage_blockedplan123"
                ),
                now=FIXED_NOW,
                environ={},
                client=client,
            )

        self.assertEqual(
            raised.exception.code,
            "EntitlementUnavailable",
        )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertEqual(
            client.calls,
            [],
        )

    @patch(
        "usage_read.resolve_user_plan",
        return_value=PLAN_PRO,
    )
    def test_usage_snapshot_uses_pro_limits(
        self,
        resolve_plan,
    ):
        table = FakeTable(
            usage_item={
                "askJm8Completed": 3,
                (
                    "entryAnalysisCompleted"
                ): 7,
            }
        )

        snapshot = get_usage_snapshot(
            "test-user",
            now=FIXED_NOW,
            environ={},
            table_resource=table,
        )

        self.assertEqual(
            snapshot["plan"]["id"],
            PLAN_PRO,
        )

        self.assertEqual(
            snapshot["operations"][
                "askJm8"
            ]["limit"],
            100,
        )

        self.assertEqual(
            snapshot["operations"][
                "askJm8"
            ]["remaining"],
            97,
        )

        self.assertEqual(
            snapshot["operations"][
                "entryAnalysis"
            ]["limit"],
            250,
        )

        resolve_plan.assert_called_once_with(
            "test-user",
            now=FIXED_NOW,
            table_resource=table,
        )

    @patch(
        "usage_read.resolve_user_plan",
        return_value=PLAN_FREE,
    )
    def test_usage_snapshot_preserves_free_limits(
        self,
        resolve_plan,
    ):
        snapshot = get_usage_snapshot(
            "test-user",
            now=FIXED_NOW,
            environ={},
            table_resource=FakeTable(),
        )

        self.assertEqual(
            snapshot["plan"]["id"],
            PLAN_FREE,
        )

        self.assertEqual(
            snapshot["operations"][
                "askJm8"
            ]["limit"],
            5,
        )

        self.assertEqual(
            snapshot["operations"][
                "entryAnalysis"
            ]["limit"],
            10,
        )

    @patch(
        "usage_read.resolve_user_plan"
    )
    def test_usage_read_entitlement_failure_is_safe(
        self,
        resolve_plan,
    ):
        resolve_plan.side_effect = (
            EntitlementUnavailableError(
                retryable=True
            )
        )

        with self.assertRaises(
            UsageReadUnavailableError
        ) as raised:
            get_usage_snapshot(
                "test-user",
                now=FIXED_NOW,
                environ={},
                table_resource=(
                    FakeTable()
                ),
            )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertEqual(
            raised.exception.payload[
                "error"
            ],
            "UsageTrackingUnavailable",
        )

        serialized = json.dumps(
            raised.exception.payload
        )

        self.assertNotIn(
            "EntitlementUnavailable",
            serialized,
        )

        self.assertNotIn(
            "test-user",
            serialized,
        )


if __name__ == "__main__":
    unittest.main()
