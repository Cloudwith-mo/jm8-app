import io
import json
import os
import unittest
from contextlib import (
    redirect_stdout,
)
from copy import deepcopy
from unittest.mock import patch


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


from app import lambda_handler  # noqa: E402
from ask_history_contract import (  # noqa: E402
    build_ask_history_item,
    build_public_ask_history_detail,
    normalize_ask_history_answer,
)
from ask_history_persistence import (  # noqa: E402
    AskHistoryPersistenceUnavailableError,
    persist_ask_history,
    rollback_persisted_ask_history,
    stable_ask_history_id,
)
from ask_history_store import (  # noqa: E402
    AskHistoryStoreError,
)
from ask_usage import (  # noqa: E402
    AskUsageUnavailableError,
)


def sample_answer():
    return {
        "answerVersion": "1.0",
        "generatedAt": (
            "2026-07-25"
            "T22:00:00+00:00"
        ),
        "status": "ANSWERED",
        "question": (
            "What pattern keeps "
            "returning?"
        ),
        "scope": {
            "startDate": None,
            "endDate": None,
            "firstEntryAt": (
                "2026-01-01"
                "T08:00:00+00:00"
            ),
            "latestEntryAt": (
                "2026-07-24"
                "T20:00:00+00:00"
            ),
        },
        "coverage": {
            "totalEntries": 10,
            "analyzedEntries": 8,
            "unanalyzedEntries": 2,
            (
                "analysisCompletionPercent"
            ): 80,
            (
                "sourceSignalsAvailable"
            ): 12,
            (
                "sourceSignalsIncluded"
            ): 8,
            "contextTruncated": False,
        },
        "answer": {
            "headline": (
                "Consistency keeps "
                "returning"
            ),
            "summary": (
                "Structured routines "
                "appear repeatedly."
            ),
            "explanation": (
                "Derived patterns connect "
                "routine with progress."
            ),
        },
        "metrics": {
            "mentionCount": 4,
            "strongestPeriod": "2026-06",
            "improvementPercent": 20,
            "topTrigger": (
                "Morning structure"
            ),
        },
        "evidence": [],
        "takeaways": [
            "Repeat the routines "
            "that work."
        ],
        "relatedThemes": [
            "Discipline"
        ],
        "growthSignals": [
            "Better consistency"
        ],
        "limitations": [],
        "suggestedFollowUps": [
            "When was progress strongest?"
        ],
    }


def history_detail(
    answer=None,
):
    resolved_answer = (
        answer or sample_answer()
    )

    history_id = (
        stable_ask_history_id(
            resolved_answer
        )
    )

    item = build_ask_history_item(
        user_id="private-user",
        answer=resolved_answer,
        history_id=history_id,
    )

    return (
        build_public_ask_history_detail(
            item
        )
    )


def reservation():
    return {
        "reservationId": (
            "usage_private123456789"
        ),
        "period": "2026-07",
        "operation": "ASK_JM8",
        "plan": "FREE",
        "status": "RESERVED",
    }


def ready_context():
    return {
        "contextStatus": "READY",
        "question": (
            "What pattern keeps "
            "returning?"
        ),
        "scope": {
            "startDate": None,
            "endDate": None,
            "firstEntryAt": None,
            "latestEntryAt": None,
        },
        "coverage": {
            "totalEntries": 1,
            "analyzedEntries": 1,
            "unanalyzedEntries": 0,
            (
                "analysisCompletionPercent"
            ): 100,
            (
                "sourceSignalsAvailable"
            ): 1,
            (
                "sourceSignalsIncluded"
            ): 1,
            "contextTruncated": False,
        },
    }


def api_event():
    return {
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/insights/ask",
            },
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": (
                            "private-user"
                        ),
                    },
                },
            },
        },
        "body": json.dumps({
            "question": (
                "What pattern keeps "
                "returning?"
            ),
        }),
    }


class AskHistoryPersistenceTests(
    unittest.TestCase
):
    def test_stable_id_is_deterministic(
        self,
    ):
        first = stable_ask_history_id(
            sample_answer()
        )

        second = stable_ask_history_id(
            deepcopy(
                sample_answer()
            )
        )

        self.assertEqual(
            first,
            second,
        )

    def test_stable_id_changes_with_answer(
        self,
    ):
        changed = sample_answer()

        changed["answer"][
            "summary"
        ] = "A different result."

        self.assertNotEqual(
            stable_ask_history_id(
                sample_answer()
            ),
            stable_ask_history_id(
                changed
            ),
        )

    @patch(
        "ask_history_persistence."
        "create_ask_history"
    )
    def test_persist_sanitizes_answer(
        self,
        create_history,
    ):
        answer = sample_answer()

        answer["rawText"] = (
            "private transcript"
        )

        answer["reservationId"] = (
            "private reservation"
        )

        create_history.return_value = (
            history_detail(answer)
        )

        result = persist_ask_history(
            "private-user",
            answer,
        )

        saved_answer = (
            create_history
            .call_args.args[1]
        )

        serialized = json.dumps(
            saved_answer
        )

        self.assertNotIn(
            "rawText",
            serialized,
        )

        self.assertNotIn(
            "reservationId",
            serialized,
        )

        self.assertEqual(
            result["historyId"],
            stable_ask_history_id(
                answer
            ),
        )

    @patch(
        "ask_history_persistence."
        "create_ask_history"
    )
    def test_duplicate_is_idempotent(
        self,
        create_history,
    ):
        create_history.side_effect = (
            AskHistoryStoreError(
                "AskHistoryAlreadyExists",
                "private duplicate detail",
                retryable=False,
            )
        )

        result = persist_ask_history(
            "private-user",
            sample_answer(),
        )

        self.assertEqual(
            result["historyId"],
            stable_ask_history_id(
                sample_answer()
            ),
        )

    @patch(
        "ask_history_persistence."
        "create_ask_history"
    )
    def test_retryable_failure_is_safe(
        self,
        create_history,
    ):
        create_history.side_effect = (
            AskHistoryStoreError(
                "ThrottlingException",
                "private provider detail",
                retryable=True,
            )
        )

        with self.assertRaises(
            AskHistoryPersistenceUnavailableError
        ) as raised:
            persist_ask_history(
                "private-user",
                sample_answer(),
            )

        self.assertEqual(
            raised.exception.status_code,
            503,
        )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertNotIn(
            "private provider",
            json.dumps(
                raised.exception.payload
            ),
        )

    @patch(
        "ask_history_persistence."
        "create_ask_history"
    )
    def test_nonretryable_failure_is_safe(
        self,
        create_history,
    ):
        create_history.side_effect = (
            AskHistoryStoreError(
                "ValidationException",
                "private storage detail",
                retryable=False,
            )
        )

        with self.assertRaises(
            AskHistoryPersistenceUnavailableError
        ) as raised:
            persist_ask_history(
                "private-user",
                sample_answer(),
            )

        self.assertEqual(
            raised.exception.status_code,
            500,
        )

        self.assertFalse(
            raised.exception.retryable
        )

    @patch(
        "ask_history_persistence."
        "create_ask_history"
    )
    def test_logs_exclude_private_values(
        self,
        create_history,
    ):
        create_history.return_value = (
            history_detail()
        )

        output = io.StringIO()

        with redirect_stdout(output):
            persist_ask_history(
                "private-user",
                sample_answer(),
            )

        logs = output.getvalue()

        for forbidden in (
            "private-user",
            (
                "What pattern keeps "
                "returning?"
            ),
            "askhist_",
            "rawText",
            "cleanText",
        ):
            self.assertNotIn(
                forbidden,
                logs,
            )

    @patch(
        "ask_history_persistence."
        "delete_ask_history",
        return_value=True,
    )
    def test_rollback_deletes_history(
        self,
        delete_history,
    ):
        history = history_detail()

        deleted = (
            rollback_persisted_ask_history(
                "private-user",
                history,
            )
        )

        self.assertTrue(deleted)

        delete_history.assert_called_once_with(
            "private-user",
            history["historyId"],
        )

    @patch(
        "ask_history_persistence."
        "delete_ask_history"
    )
    def test_rollback_failure_is_suppressed(
        self,
        delete_history,
    ):
        delete_history.side_effect = (
            AskHistoryStoreError(
                "ThrottlingException",
                "private rollback detail",
                retryable=True,
            )
        )

        output = io.StringIO()

        with redirect_stdout(output):
            deleted = (
                rollback_persisted_ask_history(
                    "private-user",
                    history_detail(),
                )
            )

        self.assertFalse(deleted)

        logs = output.getvalue()

        self.assertNotIn(
            "private-user",
            logs,
        )

        self.assertNotIn(
            "private rollback",
            logs,
        )

    def test_api_save_failure_releases_usage(
        self,
    ):
        reserved = reservation()

        unavailable = (
            AskHistoryPersistenceUnavailableError(
                retryable=True
            )
        )

        with (
            patch(
                "app."
                "list_insights_overview_entries",
                return_value=[],
            ),
            patch(
                "app.build_ask_context",
                return_value=ready_context(),
            ),
            patch(
                "app.reserve_ask_usage",
                return_value=reserved,
            ),
            patch(
                "app.answer_journal_history",
                return_value=sample_answer(),
            ),
            patch(
                "app.persist_ask_history",
                side_effect=unavailable,
            ),
            patch(
                "app.fail_ask_usage"
            ) as fail_usage,
            patch(
                "app.complete_ask_usage"
            ) as complete_usage,
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            503,
        )

        self.assertEqual(
            body["error"],
            "AskHistoryUnavailable",
        )

        self.assertNotIn(
            "answer",
            body,
        )

        self.assertNotIn(
            "history",
            body,
        )

        fail_usage.assert_called_once_with(
            "private-user",
            reserved,
        )

        complete_usage.assert_not_called()

    def test_completion_failure_rolls_back_history(
        self,
    ):
        reserved = reservation()
        history = history_detail()

        with (
            patch(
                "app."
                "list_insights_overview_entries",
                return_value=[],
            ),
            patch(
                "app.build_ask_context",
                return_value=ready_context(),
            ),
            patch(
                "app.reserve_ask_usage",
                return_value=reserved,
            ),
            patch(
                "app.answer_journal_history",
                return_value=sample_answer(),
            ),
            patch(
                "app.persist_ask_history",
                return_value=history,
            ),
            patch(
                "app.complete_ask_usage",
                side_effect=(
                    AskUsageUnavailableError(
                        retryable=True
                    )
                ),
            ),
            patch(
                "app."
                "rollback_persisted_ask_history"
            ) as rollback_history,
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            503,
        )

        self.assertEqual(
            body["error"],
            "UsageTrackingUnavailable",
        )

        self.assertNotIn(
            "answer",
            body,
        )

        self.assertNotIn(
            "history",
            body,
        )

        rollback_history.assert_called_once_with(
            "private-user",
            history,
        )


if __name__ == "__main__":
    unittest.main()
