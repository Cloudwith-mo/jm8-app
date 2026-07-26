import io
import json
import os
import unittest
from contextlib import (
    redirect_stdout,
)
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
from ask_usage import (  # noqa: E402
    AskUsageLimitError,
    AskUsageUnavailableError,
    ask_context_requires_usage,
    complete_ask_usage,
    fail_ask_usage,
    reserve_ask_usage,
)
from insights_ask_answer import (  # noqa: E402
    AskAnswerInvocationError,
)
from usage_policy import (  # noqa: E402
    OPERATION_ASK_JM8,
    PLAN_FREE,
)
from usage_store import (  # noqa: E402
    UsageLimitExceededError,
    UsageStoreError,
)


def ready_context():
    return {
        "contextStatus": "READY",
        "question": (
            "What patterns keep returning?"
        ),
        "coverage": {
            "analyzedEntries": 3,
        },
    }


def partial_context():
    return {
        "contextStatus": "PARTIAL",
        "coverage": {
            "analyzedEntries": 1,
        },
    }


def empty_context():
    return {
        "contextStatus": "EMPTY",
        "coverage": {
            "analyzedEntries": 0,
        },
    }


def reservation():
    return {
        "reservationId": (
            "usage_testreservation123"
        ),
        "period": "2026-07",
        "operation": OPERATION_ASK_JM8,
        "plan": PLAN_FREE,
        "status": "RESERVED",
    }


def limit_decision():
    return {
        "allowed": False,
        "operation": OPERATION_ASK_JM8,
        "operationKey": "askJm8",
        "plan": PLAN_FREE,
        "period": "2026-07",
        "limit": 5,
        "used": 4,
        "reserved": 1,
        "failed": 2,
        "remaining": 0,
        "resetsAt": (
            "2026-08-01"
            "T00:00:00+00:00"
        ),
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
                "What patterns keep "
                "returning?"
            ),
        }),
    }


class AskUsageHelperTests(
    unittest.TestCase
):
    def test_empty_context_does_not_require_usage(
        self,
    ):
        self.assertFalse(
            ask_context_requires_usage(
                empty_context()
            )
        )

    def test_ready_context_requires_usage(
        self,
    ):
        self.assertTrue(
            ask_context_requires_usage(
                ready_context()
            )
        )

    def test_partial_context_requires_usage(
        self,
    ):
        self.assertTrue(
            ask_context_requires_usage(
                partial_context()
            )
        )

    @patch(
        "ask_usage.reserve_monthly_usage"
    )
    def test_reservation_uses_free_ask_plan(
        self,
        reserve_monthly,
    ):
        reserve_monthly.return_value = (
            reservation()
        )

        result = reserve_ask_usage(
            "private-user",
            ready_context(),
        )

        self.assertEqual(
            result["status"],
            "RESERVED",
        )

        reserve_monthly.assert_called_once_with(
            "private-user",
            plan=PLAN_FREE,
            operation=(
                OPERATION_ASK_JM8
            ),
        )

    @patch(
        "ask_usage.reserve_monthly_usage"
    )
    def test_limit_is_converted_to_public_error(
        self,
        reserve_monthly,
    ):
        reserve_monthly.side_effect = (
            UsageLimitExceededError(
                limit_decision()
            )
        )

        with self.assertRaises(
            AskUsageLimitError
        ) as raised:
            reserve_ask_usage(
                "private-user",
                ready_context(),
            )

        self.assertEqual(
            raised.exception.payload[
                "error"
            ],
            "UsageLimitExceeded",
        )

        self.assertEqual(
            raised.exception.payload[
                "remaining"
            ],
            0,
        )

    @patch(
        "ask_usage.reserve_monthly_usage"
    )
    def test_storage_failure_is_converted_to_unavailable(
        self,
        reserve_monthly,
    ):
        reserve_monthly.side_effect = (
            UsageStoreError(
                "ThrottlingException",
                "private storage detail",
                retryable=True,
            )
        )

        with self.assertRaises(
            AskUsageUnavailableError
        ) as raised:
            reserve_ask_usage(
                "private-user",
                ready_context(),
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

    @patch(
        "ask_usage."
        "complete_usage_reservation"
    )
    def test_success_completes_reservation(
        self,
        complete_reservation,
    ):
        complete_reservation.return_value = {
            **reservation(),
            "status": "COMPLETED",
        }

        completed = complete_ask_usage(
            "private-user",
            reservation(),
        )

        self.assertTrue(completed)

        complete_reservation.assert_called_once()

    @patch(
        "ask_usage."
        "complete_usage_reservation"
    )
    def test_completion_failure_is_not_hidden(
        self,
        complete_reservation,
    ):
        complete_reservation.side_effect = (
            UsageStoreError(
                "ThrottlingException",
                "private",
                retryable=True,
            )
        )

        with self.assertRaises(
            AskUsageUnavailableError
        ):
            complete_ask_usage(
                "private-user",
                reservation(),
            )

    @patch(
        "ask_usage.fail_usage_reservation"
    )
    def test_answer_failure_releases_reservation(
        self,
        fail_reservation,
    ):
        fail_reservation.return_value = {
            **reservation(),
            "status": "FAILED",
        }

        released = fail_ask_usage(
            "private-user",
            reservation(),
        )

        self.assertTrue(released)

        fail_reservation.assert_called_once()

    @patch(
        "ask_usage.fail_usage_reservation"
    )
    def test_release_failure_preserves_original_error_path(
        self,
        fail_reservation,
    ):
        fail_reservation.side_effect = (
            UsageStoreError(
                "InternalServerError",
                "private",
                retryable=True,
            )
        )

        released = fail_ask_usage(
            "private-user",
            reservation(),
        )

        self.assertFalse(released)

    @patch(
        "ask_usage.reserve_monthly_usage"
    )
    def test_usage_logs_exclude_private_inputs(
        self,
        reserve_monthly,
    ):
        reserve_monthly.return_value = (
            reservation()
        )

        output = io.StringIO()

        with redirect_stdout(output):
            reserve_ask_usage(
                "private-user",
                {
                    **ready_context(),
                    "question": (
                        "private question text"
                    ),
                },
            )

        logs = output.getvalue()

        for forbidden in (
            "private-user",
            "private question text",
            "usage_testreservation123",
            "rawText",
            "cleanText",
        ):
            self.assertNotIn(
                forbidden,
                logs,
            )


class AskUsageApiTests(
    unittest.TestCase
):
    def test_quota_limit_returns_429_before_answer(
        self,
    ):
        payload = {
            "error": (
                "UsageLimitExceeded"
            ),
            "message": (
                "Monthly limit reached."
            ),
            "operation": "askJm8",
            "plan": PLAN_FREE,
            "period": "2026-07",
            "limit": 5,
            "used": 5,
            "reserved": 0,
            "remaining": 0,
            "resetsAt": (
                "2026-08-01"
                "T00:00:00+00:00"
            ),
            "upgradeRequired": True,
        }

        with (
            patch(
                "app."
                "list_insights_overview_entries",
                return_value=[],
            ),
            patch(
                "app.build_ask_context",
                return_value=(
                    ready_context()
                ),
            ),
            patch(
                "app.reserve_ask_usage",
                side_effect=(
                    AskUsageLimitError(
                        payload
                    )
                ),
            ),
            patch(
                "app.answer_journal_history"
            ) as answer_history,
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
            429,
        )

        self.assertEqual(
            body["error"],
            "UsageLimitExceeded",
        )

        answer_history.assert_not_called()

    def test_tracking_failure_returns_503_before_answer(
        self,
    ):
        unavailable = (
            AskUsageUnavailableError(
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
                return_value=(
                    ready_context()
                ),
            ),
            patch(
                "app.reserve_ask_usage",
                side_effect=unavailable,
            ),
            patch(
                "app.answer_journal_history"
            ) as answer_history,
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

        answer_history.assert_not_called()

    def test_success_completes_reserved_usage(
        self,
    ):
        reserved = reservation()

        with (
            patch(
                "app."
                "list_insights_overview_entries",
                return_value=[],
            ),
            patch(
                "app.build_ask_context",
                return_value=(
                    ready_context()
                ),
            ),
            patch(
                "app.reserve_ask_usage",
                return_value=reserved,
            ),
            patch(
                "app.answer_journal_history",
                return_value={
                    "status": "ANSWERED",
                },
            ),
            patch(
                "app.persist_ask_history",
                return_value={
                    "historyVersion": "1.0",
                    "historyId": (
                        "askhist_usage123456789"
                    ),
                    "createdAt": (
                        "2026-07-25"
                        "T22:00:00+00:00"
                    ),
                },
            ) as persist_history,
            patch(
                "app.complete_ask_usage"
            ) as complete_usage,
            patch(
                "app.fail_ask_usage"
            ) as fail_usage,
            patch(
                "app."
                "rollback_persisted_ask_history"
            ) as rollback_history,
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        self.assertEqual(
            result["statusCode"],
            200,
        )

        persist_history.assert_called_once()

        complete_usage.assert_called_once_with(
            "private-user",
            reserved,
        )

        fail_usage.assert_not_called()
        rollback_history.assert_not_called()

    def test_answer_failure_releases_reserved_usage(
        self,
    ):
        reserved = reservation()

        failure = AskAnswerInvocationError(
            "private provider detail",
            error_code=(
                "ThrottlingException"
            ),
            retryable=True,
            retry_attempts=2,
        )

        with (
            patch(
                "app."
                "list_insights_overview_entries",
                return_value=[],
            ),
            patch(
                "app.build_ask_context",
                return_value=(
                    ready_context()
                ),
            ),
            patch(
                "app.reserve_ask_usage",
                return_value=reserved,
            ),
            patch(
                "app.answer_journal_history",
                side_effect=failure,
            ),
            patch(
                "app.complete_ask_usage"
            ) as complete_usage,
            patch(
                "app.fail_ask_usage"
            ) as fail_usage,
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        self.assertEqual(
            result["statusCode"],
            503,
        )

        fail_usage.assert_called_once_with(
            "private-user",
            reserved,
        )

        complete_usage.assert_not_called()

    def test_completion_failure_hides_answer(
        self,
    ):
        reserved = reservation()

        with (
            patch(
                "app."
                "list_insights_overview_entries",
                return_value=[],
            ),
            patch(
                "app.build_ask_context",
                return_value=(
                    ready_context()
                ),
            ),
            patch(
                "app.reserve_ask_usage",
                return_value=reserved,
            ),
            patch(
                "app.answer_journal_history",
                return_value={
                    "status": "ANSWERED",
                },
            ),
            patch(
                "app.persist_ask_history",
                return_value={
                    "historyVersion": "1.0",
                    "historyId": (
                        "askhist_usagefailure123"
                    ),
                    "createdAt": (
                        "2026-07-25"
                        "T22:00:00+00:00"
                    ),
                },
            ) as persist_history,
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

        persist_history.assert_called_once()

        rollback_history.assert_called_once()

    def test_deploy_includes_usage_limits(
        self,
    ):
        from pathlib import Path

        deploy = Path(
            "bin/deploy"
        ).read_text()

        for variable in (
            "FREE_MONTHLY_ASK_QUESTIONS",
            "FREE_MONTHLY_ENTRY_ANALYSES",
            "PRO_MONTHLY_ASK_QUESTIONS",
            "PRO_MONTHLY_ENTRY_ANALYSES",
        ):
            self.assertIn(
                variable,
                deploy,
            )


if __name__ == "__main__":
    unittest.main()
