import json
import os
import unittest
from copy import deepcopy

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


from ask_history_contract import (  # noqa: E402
    build_ask_history_item,
)
from ask_history_store import (  # noqa: E402
    ASK_HISTORY_INDEX_NAME,
    AskHistoryStoreError,
    create_ask_history,
    decode_ask_history_cursor,
    delete_ask_history,
    delete_ask_history_by_key,
    encode_ask_history_cursor,
    get_ask_history,
    list_ask_history,
)


HISTORY_ID = (
    "askhist_store123456789"
)

SECOND_HISTORY_ID = (
    "askhist_store987654321"
)


def sample_answer(
    *,
    generated_at=(
        "2026-07-25"
        "T18:30:00+00:00"
    ),
):
    return {
        "answerVersion": "1.0",
        "generatedAt": generated_at,
        "status": "ANSWERED",
        "question": (
            "Which goal keeps "
            "returning?"
        ),
        "scope": {
            "startDate": "2026-01-01",
            "endDate": "2026-07-25",
            "firstEntryAt": (
                "2026-01-03"
                "T08:00:00+00:00"
            ),
            "latestEntryAt": (
                "2026-07-24"
                "T20:00:00+00:00"
            ),
        },
        "coverage": {
            "totalEntries": 18,
            "analyzedEntries": 15,
            "unanalyzedEntries": 3,
            (
                "analysisCompletionPercent"
            ): 83,
            (
                "sourceSignalsAvailable"
            ): 24,
            (
                "sourceSignalsIncluded"
            ): 10,
            "contextTruncated": False,
        },
        "answer": {
            "headline": (
                "Consistency remains "
                "the recurring goal"
            ),
            "summary": (
                "The strongest recurring "
                "goal is consistency."
            ),
            "explanation": (
                "Derived patterns connect "
                "repeatable routines with "
                "progress."
            ),
        },
        "metrics": {
            "mentionCount": 7,
            "strongestPeriod": "2026-06",
            "improvementPercent": 25,
            "topTrigger": (
                "Structured mornings"
            ),
        },
        "evidence": [
            {
                "paraphrase": (
                    "Discipline repeatedly "
                    "appeared with progress."
                ),
                "date": "2026-06-12",
                "sourceType": "typed",
                "relevance": "high",
            }
        ],
        "takeaways": [
            "Consistency matters."
        ],
        "relatedThemes": [
            "Discipline"
        ],
        "growthSignals": [
            "More structured routines"
        ],
        "limitations": [
            "Three entries were not "
            "analyzed."
        ],
        "suggestedFollowUps": [
            "When was progress strongest?"
        ],
    }


def stored_item(
    user_id="test-user",
    history_id=HISTORY_ID,
    generated_at=(
        "2026-07-25"
        "T18:30:00+00:00"
    ),
):
    item = build_ask_history_item(
        user_id=user_id,
        answer=sample_answer(
            generated_at=generated_at
        ),
        history_id=history_id,
    )

    item["GSI1PK"] = (
        f"USER#{user_id}#ASK_HISTORY"
    )

    item["GSI1SK"] = (
        f"HISTORY#{history_id}"
    )

    return item


def client_error(
    code: str,
    *,
    operation: str,
) -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": code,
                "Message": (
                    "Synthetic private "
                    "provider detail."
                ),
            }
        },
        operation,
    )


class FakeTable:
    def __init__(
        self,
        *,
        query_results=None,
        put_error=None,
        query_error=None,
        delete_error=None,
        delete_result=None,
    ):
        self.query_results = list(
            query_results or []
        )

        self.put_error = put_error
        self.query_error = query_error
        self.delete_error = delete_error

        self.delete_result = (
            delete_result
            if delete_result is not None
            else {
                "Attributes": {
                    "deleted": True,
                }
            }
        )

        self.put_calls = []
        self.query_calls = []
        self.delete_calls = []

    def put_item(
        self,
        **kwargs,
    ):
        self.put_calls.append(kwargs)

        if self.put_error:
            raise self.put_error

        return {}

    def query(
        self,
        **kwargs,
    ):
        self.query_calls.append(kwargs)

        if self.query_error:
            raise self.query_error

        if self.query_results:
            return self.query_results.pop(0)

        return {
            "Items": [],
        }

    def delete_item(
        self,
        **kwargs,
    ):
        self.delete_calls.append(kwargs)

        if self.delete_error:
            raise self.delete_error

        return self.delete_result


class AskHistoryStoreTests(
    unittest.TestCase
):
    def test_create_writes_user_partitioned_record(
        self,
    ):
        table = FakeTable()

        detail = create_ask_history(
            "test-user",
            sample_answer(),
            history_id=HISTORY_ID,
            table_resource=table,
        )

        stored = table.put_calls[
            0
        ]["Item"]

        self.assertEqual(
            stored["PK"],
            "USER#test-user",
        )

        self.assertTrue(
            stored["SK"].startswith(
                "ASK_HISTORY#"
            )
        )

        self.assertEqual(
            detail["historyId"],
            HISTORY_ID,
        )

        self.assertNotIn(
            "PK",
            detail,
        )

    def test_create_adds_user_scoped_lookup_index(
        self,
    ):
        table = FakeTable()

        create_ask_history(
            "test-user",
            sample_answer(),
            history_id=HISTORY_ID,
            table_resource=table,
        )

        stored = table.put_calls[
            0
        ]["Item"]

        self.assertEqual(
            stored["GSI1PK"],
            (
                "USER#test-user"
                "#ASK_HISTORY"
            ),
        )

        self.assertEqual(
            stored["GSI1SK"],
            (
                "HISTORY#"
                + HISTORY_ID
            ),
        )

    def test_create_conflict_is_safe(
        self,
    ):
        table = FakeTable(
            put_error=client_error(
                (
                    "ConditionalCheck"
                    "FailedException"
                ),
                operation="PutItem",
            )
        )

        with self.assertRaises(
            AskHistoryStoreError
        ) as raised:
            create_ask_history(
                "test-user",
                sample_answer(),
                history_id=HISTORY_ID,
                table_resource=table,
            )

        self.assertEqual(
            raised.exception.code,
            "AskHistoryAlreadyExists",
        )

        self.assertNotIn(
            "Synthetic private",
            str(raised.exception),
        )

    def test_list_is_newest_first_and_consistent(
        self,
    ):
        table = FakeTable(
            query_results=[
                {
                    "Items": [],
                }
            ]
        )

        result = list_ask_history(
            "test-user",
            limit=12,
            table_resource=table,
        )

        call = table.query_calls[0]

        self.assertFalse(
            call["ScanIndexForward"]
        )

        self.assertTrue(
            call["ConsistentRead"]
        )

        self.assertEqual(
            call["Limit"],
            12,
        )

        self.assertEqual(
            result["count"],
            0,
        )

    def test_list_returns_summaries_and_cursor(
        self,
    ):
        first = stored_item()

        second = stored_item(
            history_id=SECOND_HISTORY_ID,
            generated_at=(
                "2026-07-24"
                "T18:30:00+00:00"
            ),
        )

        table = FakeTable(
            query_results=[
                {
                    "Items": [
                        first,
                        second,
                    ],
                    "LastEvaluatedKey": {
                        "PK": (
                            "USER#test-user"
                        ),
                        "SK": second["SK"],
                    },
                }
            ]
        )

        result = list_ask_history(
            "test-user",
            limit=2,
            table_resource=table,
        )

        self.assertEqual(
            result["count"],
            2,
        )

        self.assertIsNotNone(
            result["nextCursor"]
        )

        self.assertNotIn(
            "evidence",
            result["items"][0],
        )

        serialized = json.dumps(
            result
        )

        self.assertNotIn(
            '"PK"',
            serialized,
        )

        self.assertNotIn(
            '"SK"',
            serialized,
        )

    def test_cursor_round_trip(
        self,
    ):
        key = {
            "PK": "USER#test-user",
            "SK": (
                "ASK_HISTORY#"
                "2026-07-25"
                "T18:30:00+00:00#"
                + HISTORY_ID
            ),
        }

        cursor = (
            encode_ask_history_cursor(
                key,
                user_id="test-user",
            )
        )

        decoded = (
            decode_ask_history_cursor(
                cursor,
                user_id="test-user",
            )
        )

        self.assertEqual(
            decoded,
            key,
        )

        self.assertNotIn(
            "USER#test-user",
            cursor,
        )

    def test_cross_user_cursor_is_rejected(
        self,
    ):
        cursor = (
            encode_ask_history_cursor(
                {
                    "PK": "USER#first-user",
                    "SK": (
                        "ASK_HISTORY#"
                        "2026-07-25"
                        "T18:30:00+00:00#"
                        + HISTORY_ID
                    ),
                },
                user_id="first-user",
            )
        )

        with self.assertRaises(
            AskHistoryStoreError
        ) as raised:
            decode_ask_history_cursor(
                cursor,
                user_id="second-user",
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidAskHistoryCursor",
        )

    def test_invalid_cursor_is_rejected(
        self,
    ):
        with self.assertRaises(
            AskHistoryStoreError
        ):
            decode_ask_history_cursor(
                "not-a-valid-cursor",
                user_id="test-user",
            )

    def test_list_applies_decoded_cursor(
        self,
    ):
        key = {
            "PK": "USER#test-user",
            "SK": (
                "ASK_HISTORY#"
                "2026-07-25"
                "T18:30:00+00:00#"
                + HISTORY_ID
            ),
        }

        cursor = (
            encode_ask_history_cursor(
                key,
                user_id="test-user",
            )
        )

        table = FakeTable(
            query_results=[
                {
                    "Items": [],
                }
            ]
        )

        list_ask_history(
            "test-user",
            cursor=cursor,
            table_resource=table,
        )

        self.assertEqual(
            table.query_calls[0][
                "ExclusiveStartKey"
            ],
            key,
        )

    def test_get_is_user_scoped_and_returns_detail(
        self,
    ):
        item = stored_item()

        table = FakeTable(
            query_results=[
                {
                    "Items": [
                        item
                    ],
                }
            ]
        )

        detail = get_ask_history(
            "test-user",
            HISTORY_ID,
            table_resource=table,
        )

        call = table.query_calls[0]

        self.assertEqual(
            call["IndexName"],
            ASK_HISTORY_INDEX_NAME,
        )

        self.assertEqual(
            call["Limit"],
            1,
        )

        self.assertEqual(
            detail["historyId"],
            HISTORY_ID,
        )

        self.assertNotIn(
            "GSI1PK",
            detail,
        )

    def test_get_missing_returns_none(
        self,
    ):
        table = FakeTable(
            query_results=[
                {
                    "Items": [],
                }
            ]
        )

        result = get_ask_history(
            "test-user",
            HISTORY_ID,
            table_resource=table,
        )

        self.assertIsNone(result)

    def test_delete_removes_base_record(
        self,
    ):
        item = stored_item()

        table = FakeTable(
            query_results=[
                {
                    "Items": [
                        item
                    ],
                }
            ]
        )

        deleted = delete_ask_history(
            "test-user",
            HISTORY_ID,
            table_resource=table,
        )

        self.assertTrue(deleted)

        self.assertEqual(
            table.delete_calls[0]["Key"],
            {
                "PK": item["PK"],
                "SK": item["SK"],
            },
        )

    def test_direct_delete_uses_base_key_without_query(
        self,
    ):
        item = stored_item()

        table = FakeTable(
            delete_result={
                "Attributes": item,
            }
        )

        deleted = (
            delete_ask_history_by_key(
                "test-user",
                item["createdAt"],
                HISTORY_ID,
                table_resource=table,
            )
        )

        self.assertTrue(deleted)

        self.assertEqual(
            table.query_calls,
            [],
        )

        self.assertEqual(
            len(table.delete_calls),
            1,
        )

        call = table.delete_calls[0]

        self.assertEqual(
            call["Key"],
            {
                "PK": item["PK"],
                "SK": item["SK"],
            },
        )

        self.assertEqual(
            call[
                "ExpressionAttributeValues"
            ][":historyId"],
            HISTORY_ID,
        )

        self.assertEqual(
            call[
                "ExpressionAttributeValues"
            ][":entityType"],
            "ASK_HISTORY",
        )

    def test_delete_missing_returns_false(
        self,
    ):
        table = FakeTable(
            query_results=[
                {
                    "Items": [],
                }
            ]
        )

        deleted = delete_ask_history(
            "test-user",
            HISTORY_ID,
            table_resource=table,
        )

        self.assertFalse(deleted)

        self.assertEqual(
            table.delete_calls,
            [],
        )

    def test_retryable_error_is_sanitized(
        self,
    ):
        table = FakeTable(
            query_error=client_error(
                "ThrottlingException",
                operation="Query",
            )
        )

        with self.assertRaises(
            AskHistoryStoreError
        ) as raised:
            list_ask_history(
                "test-user",
                table_resource=table,
            )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertEqual(
            raised.exception.code,
            "ThrottlingException",
        )

        self.assertNotIn(
            "Synthetic private",
            str(raised.exception),
        )


if __name__ == "__main__":
    unittest.main()
