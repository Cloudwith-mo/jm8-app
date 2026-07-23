import json
import os
import unittest
from datetime import (
    datetime,
    timezone,
)
from unittest.mock import patch

from boto3.dynamodb.types import (
    TypeDeserializer,
)
from botocore.exceptions import (
    ClientError,
)


os.environ.setdefault(
    "TABLE_NAME",
    "jm8-test",
)

os.environ.setdefault(
    "RAW_BUCKET",
    "jm8-test-raw",
)


from usage_policy import (  # noqa: E402
    OPERATION_ASK_JM8,
    OPERATION_ENTRY_ANALYSIS,
    PLAN_FREE,
)
from usage_store import (  # noqa: E402
    UsageLimitExceededError,
    UsageReservationConflictError,
    UsageReservationStateError,
    UsageStoreError,
    complete_usage_reservation,
    fail_usage_reservation,
    get_monthly_usage_item,
    new_usage_reservation_id,
    reserve_monthly_usage,
    usage_reservation_sk,
    usage_sk,
)


FIXED_NOW = datetime(
    2026,
    7,
    22,
    12,
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


def client_error(
    code: str,
) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": "test",
            }
        },
        "TransactWriteItems",
    )


class FakeClient:
    def __init__(
        self,
        *,
        error: ClientError | None = None,
    ):
        self.error = error
        self.calls = []

    def transact_write_items(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        return {}


class FakeTable:
    def __init__(
        self,
        item=None,
    ):
        self.item = item
        self.calls = []

    def get_item(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.item is None:
            return {}

        return {
            "Item": self.item,
        }


def reservation_fixture(
    *,
    operation=OPERATION_ASK_JM8,
):
    return {
        "reservationId": (
            "usage_testreservation123"
        ),
        "period": "2026-07",
        "operation": operation,
        "plan": PLAN_FREE,
        "status": "RESERVED",
    }


class UsageStoreTests(
    unittest.TestCase
):
    def test_usage_key_contract(
        self,
    ):
        self.assertEqual(
            usage_sk("2026-07"),
            "USAGE#2026-07",
        )

    def test_invalid_usage_period_is_rejected(
        self,
    ):
        with self.assertRaises(
            UsageStoreError
        ) as raised:
            usage_sk("July-2026")

        self.assertEqual(
            raised.exception.code,
            "InvalidUsagePeriod",
        )

    def test_reservation_key_contract(
        self,
    ):
        self.assertEqual(
            usage_reservation_sk(
                "2026-07",
                "usage_test123",
            ),
            (
                "USAGE_RESERVATION#"
                "2026-07#usage_test123"
            ),
        )

    def test_generated_reservation_id_is_valid(
        self,
    ):
        generated = (
            new_usage_reservation_id()
        )

        self.assertTrue(
            generated.startswith(
                "usage_"
            )
        )

        self.assertLessEqual(
            len(generated),
            64,
        )

    def test_missing_usage_item_returns_empty_dict(
        self,
    ):
        fake_table = FakeTable()

        item = get_monthly_usage_item(
            "user-test",
            now=FIXED_NOW,
            table_resource=fake_table,
        )

        self.assertEqual(
            item,
            {},
        )

        self.assertTrue(
            fake_table.calls[0][
                "ConsistentRead"
            ]
        )

    def test_existing_usage_item_is_returned(
        self,
    ):
        expected = {
            "askJm8Completed": 2,
        }

        item = get_monthly_usage_item(
            "user-test",
            now=FIXED_NOW,
            table_resource=(
                FakeTable(expected)
            ),
        )

        self.assertEqual(
            item,
            expected,
        )

    def test_ask_reservation_uses_atomic_transaction(
        self,
    ):
        client = FakeClient()

        result = reserve_monthly_usage(
            "user-test",
            plan=PLAN_FREE,
            operation=(
                OPERATION_ASK_JM8
            ),
            reservation_id=(
                "usage_testreservation123"
            ),
            now=FIXED_NOW,
            environ={},
            client=client,
        )

        self.assertEqual(
            result["status"],
            "RESERVED",
        )

        self.assertEqual(
            len(client.calls),
            1,
        )

        transaction = client.calls[0][
            "TransactItems"
        ]

        self.assertEqual(
            len(transaction),
            2,
        )

        update = transaction[0][
            "Update"
        ]

        put = transaction[1]["Put"]

        self.assertIn(
            "#reserved :one",
            update["UpdateExpression"],
        )

        self.assertIn(
            "#consumed :one",
            update["UpdateExpression"],
        )

        self.assertIn(
            "#consumed < :limit",
            update[
                "ConditionExpression"
            ],
        )

        item = deserialize_map(
            put["Item"]
        )

        self.assertEqual(
            item["operation"],
            OPERATION_ASK_JM8,
        )

        self.assertEqual(
            item["status"],
            "RESERVED",
        )

    def test_ask_reservation_uses_free_limit(
        self,
    ):
        client = FakeClient()

        reserve_monthly_usage(
            "user-test",
            plan=PLAN_FREE,
            operation=(
                OPERATION_ASK_JM8
            ),
            reservation_id=(
                "usage_testreservation123"
            ),
            now=FIXED_NOW,
            environ={},
            client=client,
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
            5,
        )

    def test_entry_analysis_uses_separate_fields(
        self,
    ):
        client = FakeClient()

        reserve_monthly_usage(
            "user-test",
            plan=PLAN_FREE,
            operation=(
                OPERATION_ENTRY_ANALYSIS
            ),
            reservation_id=(
                "usage_testreservation123"
            ),
            now=FIXED_NOW,
            environ={},
            client=client,
        )

        names = client.calls[0][
            "TransactItems"
        ][0]["Update"][
            "ExpressionAttributeNames"
        ]

        self.assertEqual(
            names["#reserved"],
            (
                "entryAnalysisReserved"
            ),
        )

        self.assertEqual(
            names["#consumed"],
            (
                "entryAnalysisConsumed"
            ),
        )

    def test_zero_limit_is_rejected_without_write(
        self,
    ):
        client = FakeClient()

        with self.assertRaises(
            UsageLimitExceededError
        ):
            reserve_monthly_usage(
                "user-test",
                plan=PLAN_FREE,
                operation=(
                    OPERATION_ASK_JM8
                ),
                reservation_id=(
                    "usage_testreservation123"
                ),
                now=FIXED_NOW,
                environ={
                    (
                        "FREE_MONTHLY_"
                        "ASK_QUESTIONS"
                    ): "0",
                },
                client=client,
            )

        self.assertEqual(
            client.calls,
            [],
        )

    def test_cancelled_transaction_at_limit_returns_limit_error(
        self,
    ):
        client = FakeClient(
            error=client_error(
                "TransactionCanceledException"
            )
        )

        with patch(
            (
                "usage_store."
                "get_monthly_usage_item"
            ),
            return_value={
                "askJm8Completed": 5,
                "askJm8Consumed": 5,
            },
        ):
            with self.assertRaises(
                UsageLimitExceededError
            ) as raised:
                reserve_monthly_usage(
                    "user-test",
                    plan=PLAN_FREE,
                    operation=(
                        OPERATION_ASK_JM8
                    ),
                    reservation_id=(
                        "usage_testreservation123"
                    ),
                    now=FIXED_NOW,
                    environ={},
                    client=client,
                )

        self.assertEqual(
            raised.exception.decision[
                "remaining"
            ],
            0,
        )

    def test_cancelled_transaction_with_capacity_returns_conflict(
        self,
    ):
        client = FakeClient(
            error=client_error(
                "TransactionCanceledException"
            )
        )

        with patch(
            (
                "usage_store."
                "get_monthly_usage_item"
            ),
            return_value={
                "askJm8Completed": 1,
                "askJm8Consumed": 1,
            },
        ):
            with self.assertRaises(
                UsageReservationConflictError
            ):
                reserve_monthly_usage(
                    "user-test",
                    plan=PLAN_FREE,
                    operation=(
                        OPERATION_ASK_JM8
                    ),
                    reservation_id=(
                        "usage_testreservation123"
                    ),
                    now=FIXED_NOW,
                    environ={},
                    client=client,
                )

    def test_retryable_storage_error_is_marked_retryable(
        self,
    ):
        client = FakeClient(
            error=client_error(
                "ThrottlingException"
            )
        )

        with self.assertRaises(
            UsageStoreError
        ) as raised:
            reserve_monthly_usage(
                "user-test",
                plan=PLAN_FREE,
                operation=(
                    OPERATION_ASK_JM8
                ),
                reservation_id=(
                    "usage_testreservation123"
                ),
                now=FIXED_NOW,
                environ={},
                client=client,
            )

        self.assertTrue(
            raised.exception.retryable
        )

    def test_success_finalization_is_atomic(
        self,
    ):
        client = FakeClient()

        result = (
            complete_usage_reservation(
                "user-test",
                reservation=(
                    reservation_fixture()
                ),
                now=FIXED_NOW,
                client=client,
            )
        )

        self.assertEqual(
            result["status"],
            "COMPLETED",
        )

        transaction = client.calls[0][
            "TransactItems"
        ]

        self.assertIn(
            "Delete",
            transaction[0],
        )

        update = transaction[1][
            "Update"
        ]

        self.assertIn(
            "#reserved :minusOne",
            update["UpdateExpression"],
        )

        self.assertIn(
            "#completed :one",
            update["UpdateExpression"],
        )

        self.assertNotIn(
            "#consumed :minusOne",
            update["UpdateExpression"],
        )

    def test_failure_releases_consumed_capacity(
        self,
    ):
        client = FakeClient()

        result = fail_usage_reservation(
            "user-test",
            reservation=(
                reservation_fixture()
            ),
            now=FIXED_NOW,
            client=client,
        )

        self.assertEqual(
            result["status"],
            "FAILED",
        )

        update = client.calls[0][
            "TransactItems"
        ][1]["Update"]

        self.assertIn(
            "#reserved :minusOne",
            update["UpdateExpression"],
        )

        self.assertIn(
            "#failed :one",
            update["UpdateExpression"],
        )

        self.assertIn(
            "#consumed :minusOne",
            update["UpdateExpression"],
        )

    def test_entry_analysis_finalization_uses_separate_fields(
        self,
    ):
        client = FakeClient()

        complete_usage_reservation(
            "user-test",
            reservation=(
                reservation_fixture(
                    operation=(
                        OPERATION_ENTRY_ANALYSIS
                    )
                )
            ),
            now=FIXED_NOW,
            client=client,
        )

        names = client.calls[0][
            "TransactItems"
        ][1]["Update"][
            "ExpressionAttributeNames"
        ]

        self.assertEqual(
            names["#completed"],
            (
                "entryAnalysisCompleted"
            ),
        )

        self.assertEqual(
            names["#reserved"],
            (
                "entryAnalysisReserved"
            ),
        )

    def test_duplicate_finalization_is_rejected(
        self,
    ):
        client = FakeClient(
            error=client_error(
                "TransactionCanceledException"
            )
        )

        with self.assertRaises(
            UsageReservationStateError
        ):
            complete_usage_reservation(
                "user-test",
                reservation=(
                    reservation_fixture()
                ),
                now=FIXED_NOW,
                client=client,
            )

    def test_invalid_reservation_is_rejected_before_write(
        self,
    ):
        client = FakeClient()

        with self.assertRaises(
            UsageStoreError
        ):
            complete_usage_reservation(
                "user-test",
                reservation={
                    "reservationId": "bad",
                    "period": "2026-07",
                    "operation": (
                        OPERATION_ASK_JM8
                    ),
                },
                now=FIXED_NOW,
                client=client,
            )

        self.assertEqual(
            client.calls,
            [],
        )

    def test_public_reservation_result_excludes_journal_data(
        self,
    ):
        result = reserve_monthly_usage(
            "user-test",
            plan=PLAN_FREE,
            operation=(
                OPERATION_ASK_JM8
            ),
            reservation_id=(
                "usage_testreservation123"
            ),
            now=FIXED_NOW,
            environ={},
            client=FakeClient(),
        )

        serialized = json.dumps(
            result
        )

        for field in (
            "rawText",
            "cleanText",
            "entryId",
            "userId",
            "question",
            "answer",
        ):
            self.assertNotIn(
                field,
                serialized,
            )

    def test_module_has_no_bedrock_dependency(
        self,
    ):
        from pathlib import Path

        source = Path(
            "function/usage_store.py"
        ).read_text().lower()

        self.assertNotIn(
            "bedrock",
            source,
        )

        self.assertNotIn(
            "converse(",
            source,
        )


if __name__ == "__main__":
    unittest.main()
