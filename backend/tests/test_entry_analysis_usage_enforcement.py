import io
import json
import os
import unittest
from contextlib import (
    redirect_stdout,
)
from pathlib import Path
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
from entry_analysis_usage import (  # noqa: E402
    EntryAnalysisUsageLimitError,
    EntryAnalysisUsageUnavailableError,
    complete_entry_analysis_usage,
    fail_entry_analysis_usage,
    reserve_entry_analysis_usage,
)
from llm_journal_analyzer import (  # noqa: E402
    AnalyzerInputError,
    AnalyzerInvocationError,
    AnalyzerResponseError,
)
from usage_policy import (  # noqa: E402
    OPERATION_ENTRY_ANALYSIS,
    PLAN_FREE,
)
from usage_store import (  # noqa: E402
    UsageLimitExceededError,
    UsageStoreError,
)
from account_deletion_guard import (  # noqa: E402
    AccountDeletionInProgress,
    DeletionGuardUnavailable,
)


def reservation():
    return {
        "reservationId": (
            "usage_analysisreservation"
        ),
        "period": "2026-07",
        "operation": (
            OPERATION_ENTRY_ANALYSIS
        ),
        "plan": PLAN_FREE,
        "status": "RESERVED",
    }


def limit_decision():
    return {
        "allowed": False,
        "operation": (
            OPERATION_ENTRY_ANALYSIS
        ),
        "operationKey": (
            "entryAnalysis"
        ),
        "plan": PLAN_FREE,
        "period": "2026-07",
        "limit": 10,
        "used": 9,
        "reserved": 1,
        "failed": 3,
        "remaining": 0,
        "resetsAt": (
            "2026-08-01"
            "T00:00:00+00:00"
        ),
    }


def entry():
    return {
        "entryId": "test-entry",
        "cleanText": (
            "A safe synthetic journal "
            "entry used for testing."
        ),
        "status": "READY",
    }


def api_event():
    return {
        "requestContext": {
            "http": {
                "method": "POST",
                "path": (
                    "/entries/test-entry/"
                    "analyze"
                ),
            },
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "test-user",
                    },
                },
            },
        },
        "body": "{}",
    }


class EntryAnalysisUsageHelperTests(
    unittest.TestCase
):
    @patch(
        "entry_analysis_usage."
        "reserve_monthly_usage"
    )
    def test_reservation_defers_plan_to_store(
        self,
        reserve_monthly,
    ):
        reserve_monthly.return_value = (
            reservation()
        )

        result = (
            reserve_entry_analysis_usage(
                "test-user"
            )
        )

        self.assertEqual(
            result["status"],
            "RESERVED",
        )

        reserve_monthly.assert_called_once_with(
            "test-user",
            operation=(
                OPERATION_ENTRY_ANALYSIS
            ),
        )

    @patch(
        "entry_analysis_usage."
        "reserve_monthly_usage"
    )
    def test_limit_becomes_public_error(
        self,
        reserve_monthly,
    ):
        reserve_monthly.side_effect = (
            UsageLimitExceededError(
                limit_decision()
            )
        )

        with self.assertRaises(
            EntryAnalysisUsageLimitError
        ) as raised:
            reserve_entry_analysis_usage(
                "test-user"
            )

        payload = raised.exception.payload

        self.assertEqual(
            payload["error"],
            "UsageLimitExceeded",
        )

        self.assertEqual(
            payload["operation"],
            "entryAnalysis",
        )

        self.assertEqual(
            payload["remaining"],
            0,
        )

    @patch(
        "entry_analysis_usage."
        "reserve_monthly_usage"
    )
    def test_storage_failure_becomes_unavailable(
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
            EntryAnalysisUsageUnavailableError
        ) as raised:
            reserve_entry_analysis_usage(
                "test-user"
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
        "entry_analysis_usage."
        "complete_usage_reservation"
    )
    def test_success_completes_usage(
        self,
        complete_reservation,
    ):
        complete_reservation.return_value = {
            **reservation(),
            "status": "COMPLETED",
        }

        completed = (
            complete_entry_analysis_usage(
                "test-user",
                reservation(),
            )
        )

        self.assertTrue(completed)

        complete_reservation.assert_called_once()

    @patch(
        "entry_analysis_usage."
        "complete_usage_reservation"
    )
    def test_completion_failure_is_visible(
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
            EntryAnalysisUsageUnavailableError
        ):
            complete_entry_analysis_usage(
                "test-user",
                reservation(),
            )

    @patch(
        "entry_analysis_usage."
        "fail_usage_reservation"
    )
    def test_analysis_failure_releases_usage(
        self,
        fail_reservation,
    ):
        fail_reservation.return_value = {
            **reservation(),
            "status": "FAILED",
        }

        released = (
            fail_entry_analysis_usage(
                "test-user",
                reservation(),
            )
        )

        self.assertTrue(released)

        fail_reservation.assert_called_once()

    @patch(
        "entry_analysis_usage."
        "fail_usage_reservation"
    )
    def test_release_failure_preserves_original_path(
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

        released = (
            fail_entry_analysis_usage(
                "test-user",
                reservation(),
            )
        )

        self.assertFalse(released)

    @patch(
        "entry_analysis_usage."
        "reserve_monthly_usage"
    )
    def test_usage_logs_exclude_private_data(
        self,
        reserve_monthly,
    ):
        reserve_monthly.return_value = (
            reservation()
        )

        output = io.StringIO()

        with redirect_stdout(output):
            reserve_entry_analysis_usage(
                "private-user-value"
            )

        logs = output.getvalue()

        for forbidden in (
            "private-user-value",
            "usage_analysisreservation",
            "entryId",
            "rawText",
            "cleanText",
        ):
            self.assertNotIn(
                forbidden,
                logs,
            )


class EntryAnalysisUsageApiTests(
    unittest.TestCase
):
    def setUp(self):
        self.guard = patch("app.ensure_user_mutation_allowed")
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_missing_entry_does_not_reserve(
        self,
    ):
        with (
            patch(
                "app.get_entry_by_id",
                return_value=None,
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage"
            ) as reserve_usage,
            patch(
                "app."
                "analyze_journal_entry_llm"
            ) as analyze,
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        self.assertEqual(
            result["statusCode"],
            404,
        )

        reserve_usage.assert_not_called()
        analyze.assert_not_called()

    def test_entry_without_text_does_not_reserve(
        self,
    ):
        with (
            patch(
                "app.get_entry_by_id",
                return_value={
                    "entryId": "test-entry",
                    "status": "PENDING",
                },
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage"
            ) as reserve_usage,
            patch(
                "app."
                "analyze_journal_entry_llm"
            ) as analyze,
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        self.assertEqual(
            result["statusCode"],
            400,
        )

        reserve_usage.assert_not_called()
        analyze.assert_not_called()

    def test_limit_returns_429_before_bedrock(
        self,
    ):
        payload = {
            "error": (
                "UsageLimitExceeded"
            ),
            "message": (
                "Monthly limit reached."
            ),
            "operation": (
                "entryAnalysis"
            ),
            "plan": PLAN_FREE,
            "period": "2026-07",
            "limit": 10,
            "used": 10,
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
                "app.get_entry_by_id",
                return_value=entry(),
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage",
                side_effect=(
                    EntryAnalysisUsageLimitError(
                        payload
                    )
                ),
            ),
            patch(
                "app."
                "analyze_journal_entry_llm"
            ) as analyze,
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

        analyze.assert_not_called()

    def test_tracking_failure_returns_503_before_bedrock(
        self,
    ):
        unavailable = (
            EntryAnalysisUsageUnavailableError(
                retryable=True
            )
        )

        with (
            patch(
                "app.get_entry_by_id",
                return_value=entry(),
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage",
                side_effect=unavailable,
            ),
            patch(
                "app."
                "analyze_journal_entry_llm"
            ) as analyze,
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

        analyze.assert_not_called()

    def test_success_persists_and_completes_usage(
        self,
    ):
        reserved = reservation()

        with (
            patch(
                "app.get_entry_by_id",
                return_value=entry(),
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage",
                return_value=reserved,
            ),
            patch(
                "app."
                "analyze_journal_entry_llm",
                return_value={
                    "status": "ANALYZED",
                    "usage": {
                        "totalTokens": 10,
                    },
                },
            ),
            patch(
                "app.update_entry_analysis",
                return_value={
                    "entryId": "test-entry",
                    "analysisStatus": (
                        "COMPLETED"
                    ),
                },
            ) as update_analysis,
            patch(
                "app."
                "complete_entry_analysis_usage"
            ) as complete_usage,
            patch(
                "app."
                "fail_entry_analysis_usage"
            ) as fail_usage,
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        self.assertEqual(
            result["statusCode"],
            200,
        )

        update_analysis.assert_called_once()

        complete_usage.assert_called_once_with(
            "test-user",
            reserved,
        )

        fail_usage.assert_not_called()

    def test_final_guard_blocks_analysis_persistence_after_bedrock(self):
        for guard_error, expected_status in (
            (AccountDeletionInProgress(), 409),
            (DeletionGuardUnavailable(), 503),
        ):
            with (
                self.subTest(error=type(guard_error).__name__),
                patch(
                    "app.ensure_user_mutation_allowed",
                    side_effect=[None, guard_error],
                ),
                patch("app.get_entry_by_id", return_value=entry()),
                patch(
                    "app.reserve_entry_analysis_usage",
                    return_value=reservation(),
                ),
                patch(
                    "app.analyze_journal_entry_llm",
                    return_value={"status": "ANALYZED"},
                ) as analyze,
                patch("app.update_entry_analysis") as update_analysis,
                patch("app.complete_entry_analysis_usage") as complete_usage,
                patch("app.fail_entry_analysis_usage") as fail_usage,
                patch("app.mark_entry_analysis_failed") as mark_failed,
            ):
                result = lambda_handler(api_event(), None)

            self.assertEqual(result["statusCode"], expected_status)
            analyze.assert_called_once()
            update_analysis.assert_not_called()
            complete_usage.assert_not_called()
            fail_usage.assert_not_called()
            mark_failed.assert_not_called()

    def test_input_error_releases_usage(
        self,
    ):
        reserved = reservation()

        with (
            patch(
                "app.get_entry_by_id",
                return_value=entry(),
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage",
                return_value=reserved,
            ),
            patch(
                "app."
                "analyze_journal_entry_llm",
                side_effect=(
                    AnalyzerInputError(
                        "Invalid analysis input."
                    )
                ),
            ),
            patch(
                "app."
                "fail_entry_analysis_usage"
            ) as fail_usage,
            patch(
                "app."
                "mark_entry_analysis_failed",
                return_value={
                    "analysisStatus": (
                        "FAILED"
                    ),
                },
            ),
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        self.assertEqual(
            result["statusCode"],
            400,
        )

        fail_usage.assert_called_once_with(
            "test-user",
            reserved,
        )

    def test_invocation_error_releases_usage(
        self,
    ):
        reserved = reservation()

        failure = AnalyzerInvocationError(
            "Private provider detail.",
            error_code=(
                "ThrottlingException"
            ),
            retryable=True,
            retry_attempts=2,
        )

        with (
            patch(
                "app.get_entry_by_id",
                return_value=entry(),
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage",
                return_value=reserved,
            ),
            patch(
                "app."
                "analyze_journal_entry_llm",
                side_effect=failure,
            ),
            patch(
                "app."
                "fail_entry_analysis_usage"
            ) as fail_usage,
            patch(
                "app."
                "mark_entry_analysis_failed",
                return_value={
                    "analysisStatus": (
                        "FAILED"
                    ),
                },
            ),
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
            "test-user",
            reserved,
        )

    def test_response_error_releases_usage(
        self,
    ):
        reserved = reservation()

        with (
            patch(
                "app.get_entry_by_id",
                return_value=entry(),
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage",
                return_value=reserved,
            ),
            patch(
                "app."
                "analyze_journal_entry_llm",
                side_effect=(
                    AnalyzerResponseError(
                        "Invalid provider response."
                    )
                ),
            ),
            patch(
                "app."
                "fail_entry_analysis_usage"
            ) as fail_usage,
            patch(
                "app."
                "mark_entry_analysis_failed",
                return_value={
                    "analysisStatus": (
                        "FAILED"
                    ),
                },
            ),
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        self.assertEqual(
            result["statusCode"],
            502,
        )

        fail_usage.assert_called_once_with(
            "test-user",
            reserved,
        )

    def test_persistence_failure_releases_usage(
        self,
    ):
        reserved = reservation()

        with (
            patch(
                "app.get_entry_by_id",
                return_value=entry(),
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage",
                return_value=reserved,
            ),
            patch(
                "app."
                "analyze_journal_entry_llm",
                return_value={
                    "status": "ANALYZED",
                },
            ),
            patch(
                "app.update_entry_analysis",
                side_effect=RuntimeError(
                    "Synthetic persistence "
                    "failure."
                ),
            ),
            patch(
                "app."
                "fail_entry_analysis_usage"
            ) as fail_usage,
        ):
            result = lambda_handler(
                api_event(),
                None,
            )

        self.assertEqual(
            result["statusCode"],
            500,
        )

        fail_usage.assert_called_once_with(
            "test-user",
            reserved,
        )

    def test_completion_failure_hides_result(
        self,
    ):
        reserved = reservation()

        with (
            patch(
                "app.get_entry_by_id",
                return_value=entry(),
            ),
            patch(
                "app."
                "reserve_entry_analysis_usage",
                return_value=reserved,
            ),
            patch(
                "app."
                "analyze_journal_entry_llm",
                return_value={
                    "status": "ANALYZED",
                },
            ),
            patch(
                "app.update_entry_analysis",
                return_value={
                    "analysisStatus": (
                        "COMPLETED"
                    ),
                },
            ),
            patch(
                "app."
                "complete_entry_analysis_usage",
                side_effect=(
                    EntryAnalysisUsageUnavailableError(
                        retryable=True
                    )
                ),
            ),
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
            "entry",
            body,
        )

    def test_historical_reanalysis_is_not_quotad(
        self,
    ):
        worker = Path(
            "function/"
            "historical_reanalysis_worker.py"
        ).read_text()

        self.assertNotIn(
            "entry_analysis_usage",
            worker,
        )

        self.assertNotIn(
            "reserve_entry_analysis_usage",
            worker,
        )

    def test_deploy_contains_entry_limit(
        self,
    ):
        deploy = Path(
            "bin/deploy"
        ).read_text()

        self.assertIn(
            (
                "FREE_MONTHLY_"
                "ENTRY_ANALYSES"
            ),
            deploy,
        )

        self.assertIn(
            (
                "PRO_MONTHLY_"
                "ENTRY_ANALYSES"
            ),
            deploy,
        )


if __name__ == "__main__":
    unittest.main()
