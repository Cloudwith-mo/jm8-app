import json
import os
import re
import unittest
from copy import deepcopy


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
    ASK_HISTORY_ENTITY_TYPE,
    ASK_HISTORY_SK_PREFIX,
    AskHistoryContractError,
    build_ask_history_item,
    build_public_ask_history_detail,
    build_public_ask_history_summary,
    new_ask_history_id,
    normalize_ask_history_answer,
)


HISTORY_ID = (
    "askhist_test1234567890"
)


def sample_answer():
    return {
        "answerVersion": "1.0",
        "generatedAt": (
            "2026-07-25"
            "T18:30:00+00:00"
        ),
        "status": "ANSWERED",
        "question": (
            "Which goal keeps "
            "returning?"
        ),
        "scope": {
            "startDate": (
                "2026-01-01"
            ),
            "endDate": (
                "2026-07-25"
            ),
            "firstEntryAt": (
                "2026-01-03"
            ),
            "latestEntryAt": (
                "2026-07-24"
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
                "goal is building a "
                "consistent daily practice."
            ),
            "explanation": (
                "Several derived signals "
                "connect progress with "
                "repeatable routines."
            ),
        },
        "metrics": {
            "mentionCount": 7,
            "strongestPeriod": (
                "2026-06"
            ),
            "improvementPercent": 25,
            "topTrigger": (
                "Structured mornings"
            ),
        },
        "evidence": [
            {
                "paraphrase": (
                    "A recurring signal "
                    "connected discipline "
                    "with steady progress."
                ),
                "date": "2026-06-12",
                "sourceType": "typed",
                "relevance": "high",
            }
        ],
        "takeaways": [
            "Consistency matters more "
            "than intensity."
        ],
        "relatedThemes": [
            "Discipline",
            "Growth",
        ],
        "growthSignals": [
            "More structured routines"
        ],
        "limitations": [
            "Three entries were not "
            "analyzed."
        ],
        "suggestedFollowUps": [
            "When was this goal "
            "strongest?"
        ],
    }


class AskHistoryContractTests(
    unittest.TestCase
):
    def test_storage_item_is_user_partitioned(
        self,
    ):
        item = build_ask_history_item(
            user_id="test-user",
            answer=sample_answer(),
            history_id=HISTORY_ID,
        )

        self.assertEqual(
            item["PK"],
            "USER#test-user",
        )

        self.assertTrue(
            item["SK"].startswith(
                ASK_HISTORY_SK_PREFIX
            )
        )

        self.assertEqual(
            item["entityType"],
            ASK_HISTORY_ENTITY_TYPE,
        )

        self.assertNotIn(
            "userId",
            item,
        )

    def test_generated_history_id_is_valid(
        self,
    ):
        history_id = (
            new_ask_history_id()
        )

        self.assertRegex(
            history_id,
            re.compile(
                r"^askhist_"
                r"[a-z0-9]{20}$"
            ),
        )

    def test_supported_answer_is_preserved(
        self,
    ):
        normalized = (
            normalize_ask_history_answer(
                sample_answer()
            )
        )

        self.assertEqual(
            normalized["question"],
            sample_answer()["question"],
        )

        self.assertEqual(
            normalized["status"],
            "ANSWERED",
        )

        self.assertEqual(
            normalized["metrics"][
                "mentionCount"
            ],
            7,
        )

        self.assertEqual(
            len(normalized["evidence"]),
            1,
        )

    def test_unknown_private_fields_are_stripped(
        self,
    ):
        answer = sample_answer()

        answer["rawText"] = (
            "synthetic private transcript"
        )

        answer["entryId"] = (
            "private-entry"
        )

        answer["scope"]["userId"] = (
            "private-user"
        )

        answer["evidence"][0][
            "reservationId"
        ] = "private-reservation"

        normalized = (
            normalize_ask_history_answer(
                answer
            )
        )

        serialized = json.dumps(
            normalized
        )

        for forbidden in (
            "rawText",
            "entryId",
            "userId",
            "reservationId",
            "synthetic private transcript",
            "private-entry",
            "private-user",
            "private-reservation",
        ):
            self.assertNotIn(
                forbidden,
                serialized,
            )

    def test_public_detail_excludes_storage_fields(
        self,
    ):
        item = build_ask_history_item(
            user_id="private-user",
            answer=sample_answer(),
            history_id=HISTORY_ID,
        )

        detail = (
            build_public_ask_history_detail(
                item
            )
        )

        self.assertEqual(
            set(detail),
            {
                "historyVersion",
                "historyId",
                "createdAt",
                "answer",
            },
        )

        serialized = json.dumps(
            detail
        )

        for forbidden in (
            '"PK"',
            '"SK"',
            "private-user",
            "entityType",
        ):
            self.assertNotIn(
                forbidden,
                serialized,
            )

    def test_public_summary_is_compact(
        self,
    ):
        item = build_ask_history_item(
            user_id="test-user",
            answer=sample_answer(),
            history_id=HISTORY_ID,
        )

        summary = (
            build_public_ask_history_summary(
                item
            )
        )

        self.assertEqual(
            set(summary),
            {
                "historyVersion",
                "historyId",
                "createdAt",
                "answerVersion",
                "question",
                "status",
                "scope",
                "headline",
                "summary",
                "evidenceCount",
                "takeawayCount",
            },
        )

        self.assertNotIn(
            "evidence",
            summary,
        )

        self.assertNotIn(
            "coverage",
            summary,
        )

        self.assertEqual(
            summary["evidenceCount"],
            1,
        )

    def test_entry_scope_timestamps_are_preserved(
        self,
    ):
        answer = sample_answer()

        answer["scope"][
            "firstEntryAt"
        ] = (
            "2026-01-03"
            "T08:15:00+02:00"
        )

        answer["scope"][
            "latestEntryAt"
        ] = (
            "2026-07-24"
            "T21:30:00Z"
        )

        normalized = (
            normalize_ask_history_answer(
                answer
            )
        )

        self.assertEqual(
            normalized["scope"][
                "firstEntryAt"
            ],
            (
                "2026-01-03"
                "T06:15:00+00:00"
            ),
        )

        self.assertEqual(
            normalized["scope"][
                "latestEntryAt"
            ],
            (
                "2026-07-24"
                "T21:30:00+00:00"
            ),
        )

    def test_naive_entry_timestamp_is_rejected(
        self,
    ):
        answer = sample_answer()

        answer["scope"][
            "firstEntryAt"
        ] = (
            "2026-01-03"
            "T08:15:00"
        )

        with self.assertRaises(
            AskHistoryContractError
        ) as raised:
            normalize_ask_history_answer(
                answer
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidAskHistoryTimestamp",
        )

    def test_invalid_question_is_rejected(
        self,
    ):
        answer = sample_answer()
        answer["question"] = "x"

        with self.assertRaises(
            AskHistoryContractError
        ) as raised:
            normalize_ask_history_answer(
                answer
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidAskHistoryAnswer",
        )

    def test_invalid_status_is_rejected(
        self,
    ):
        answer = sample_answer()
        answer["status"] = "UNKNOWN"

        with self.assertRaises(
            AskHistoryContractError
        ):
            normalize_ask_history_answer(
                answer
            )

    def test_invalid_timestamp_is_rejected(
        self,
    ):
        answer = sample_answer()

        answer["generatedAt"] = (
            "not-a-timestamp"
        )

        with self.assertRaises(
            AskHistoryContractError
        ) as raised:
            normalize_ask_history_answer(
                answer
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidAskHistoryTimestamp",
        )

    def test_invalid_history_id_is_rejected(
        self,
    ):
        with self.assertRaises(
            AskHistoryContractError
        ):
            build_ask_history_item(
                user_id="test-user",
                answer=deepcopy(
                    sample_answer()
                ),
                history_id=(
                    "invalid/history/id"
                ),
            )


if __name__ == "__main__":
    unittest.main()
