import io
import json
import os
import unittest
from contextlib import (
    redirect_stdout,
)
from datetime import (
    datetime,
    timezone,
)

from botocore.exceptions import (
    ClientError,
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


from usage_read import (  # noqa: E402
    UsageReadUnavailableError,
    get_usage_snapshot,
)


FIXED_NOW = datetime(
    2026,
    7,
    22,
    12,
    0,
    tzinfo=timezone.utc,
)


class FakeTable:
    def __init__(
        self,
        *,
        item=None,
        error=None,
    ):
        self.item = item
        self.error = error
        self.calls = []

    def get_item(
        self,
        **kwargs,
    ):
        self.calls.append(kwargs)

        if self.error:
            raise self.error

        if self.item is None:
            return {}

        return {
            "Item": self.item,
        }


def client_error(
    code: str,
) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": (
                    "Private storage detail."
                ),
            }
        },
        "GetItem",
    )


class UsageReadTests(
    unittest.TestCase
):
    def test_empty_usage_returns_free_allowances(
        self,
    ):
        snapshot = get_usage_snapshot(
            "test-user",
            now=FIXED_NOW,
            environ={},
            table_resource=FakeTable(),
        )

        self.assertEqual(
            snapshot["plan"],
            {
                "id": "FREE",
                "label": "Free",
            },
        )

        operations = snapshot[
            "operations"
        ]

        self.assertEqual(
            operations[
                "askJm8"
            ]["limit"],
            5,
        )

        self.assertEqual(
            operations[
                "askJm8"
            ]["remaining"],
            5,
        )

        self.assertEqual(
            operations[
                "entryAnalysis"
            ]["limit"],
            10,
        )

        self.assertEqual(
            operations[
                "entryAnalysis"
            ]["remaining"],
            10,
        )

    def test_existing_counters_are_returned(
        self,
    ):
        snapshot = get_usage_snapshot(
            "test-user",
            now=FIXED_NOW,
            environ={},
            table_resource=FakeTable(
                item={
                    "askJm8Completed": 2,
                    "askJm8Reserved": 1,
                    "askJm8Failed": 3,
                    (
                        "entryAnalysisCompleted"
                    ): 4,
                    (
                        "entryAnalysisReserved"
                    ): 2,
                    (
                        "entryAnalysisFailed"
                    ): 5,
                }
            ),
        )

        ask = snapshot[
            "operations"
        ]["askJm8"]

        analysis = snapshot[
            "operations"
        ]["entryAnalysis"]

        self.assertEqual(
            ask["used"],
            2,
        )

        self.assertEqual(
            ask["reserved"],
            1,
        )

        self.assertEqual(
            ask["failed"],
            3,
        )

        self.assertEqual(
            ask["remaining"],
            2,
        )

        self.assertEqual(
            analysis["used"],
            4,
        )

        self.assertEqual(
            analysis["reserved"],
            2,
        )

        self.assertEqual(
            analysis["remaining"],
            4,
        )

    def test_read_is_consistent_and_user_scoped(
        self,
    ):
        table = FakeTable()

        get_usage_snapshot(
            "scoped-user",
            now=FIXED_NOW,
            environ={},
            table_resource=table,
        )

        call = table.calls[0]

        self.assertTrue(
            call["ConsistentRead"]
        )

        self.assertEqual(
            call["Key"]["PK"],
            "USER#scoped-user",
        )

        self.assertEqual(
            call["Key"]["SK"],
            "USAGE#2026-07",
        )

    def test_private_storage_fields_are_excluded(
        self,
    ):
        snapshot = get_usage_snapshot(
            "private-user",
            now=FIXED_NOW,
            environ={},
            table_resource=FakeTable(
                item={
                    "PK": (
                        "USER#private-user"
                    ),
                    "SK": "USAGE#2026-07",
                    "userId": "private-user",
                    "reservationId": (
                        "private-reservation"
                    ),
                    "askJm8Consumed": 3,
                    (
                        "entryAnalysisConsumed"
                    ): 4,
                    "rawText": (
                        "private journal text"
                    ),
                    "cleanText": (
                        "private clean text"
                    ),
                    "askJm8Completed": 2,
                }
            ),
        )

        serialized = json.dumps(
            snapshot
        )

        for forbidden in (
            '"PK"',
            '"SK"',
            "userId",
            "reservationId",
            "askJm8Consumed",
            "entryAnalysisConsumed",
            "rawText",
            "cleanText",
            "private-user",
            "private journal text",
            "private clean text",
        ):
            self.assertNotIn(
                forbidden,
                serialized,
            )

    def test_retryable_storage_error_is_safe(
        self,
    ):
        with self.assertRaises(
            UsageReadUnavailableError
        ) as raised:
            get_usage_snapshot(
                "test-user",
                now=FIXED_NOW,
                environ={},
                table_resource=FakeTable(
                    error=client_error(
                        "ThrottlingException"
                    )
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

        self.assertEqual(
            raised.exception.payload[
                "retryAfterSeconds"
            ],
            2,
        )

        serialized = json.dumps(
            raised.exception.payload
        )

        self.assertNotIn(
            "ThrottlingException",
            serialized,
        )

        self.assertNotIn(
            "Private storage detail",
            serialized,
        )

    def test_invalid_limit_configuration_is_safe(
        self,
    ):
        with self.assertRaises(
            UsageReadUnavailableError
        ) as raised:
            get_usage_snapshot(
                "test-user",
                now=FIXED_NOW,
                environ={
                    (
                        "FREE_MONTHLY_"
                        "ASK_QUESTIONS"
                    ): "invalid",
                },
                table_resource=FakeTable(),
            )

        self.assertFalse(
            raised.exception.retryable
        )

        self.assertNotIn(
            "retryAfterSeconds",
            raised.exception.payload,
        )

    def test_logs_exclude_user_and_storage_keys(
        self,
    ):
        output = io.StringIO()

        with redirect_stdout(output):
            get_usage_snapshot(
                "private-user-value",
                now=FIXED_NOW,
                environ={},
                table_resource=FakeTable(
                    item={
                        "askJm8Completed": 2,
                    }
                ),
            )

        logs = output.getvalue()

        for forbidden in (
            "private-user-value",
            '"PK"',
            '"SK"',
            "reservationId",
            "rawText",
            "cleanText",
        ):
            self.assertNotIn(
                forbidden,
                logs,
            )


if __name__ == "__main__":
    unittest.main()
