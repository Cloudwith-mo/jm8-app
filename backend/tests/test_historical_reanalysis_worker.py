import os
import unittest
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
os.environ.setdefault(
    "BEDROCK_ANALYSIS_MODEL_ID",
    "test-model",
)

from historical_reanalysis_worker import (  # noqa: E402
    ANALYSIS_SOURCE,
    lambda_handler,
    record_worker_failure,
)
from llm_journal_analyzer import (  # noqa: E402
    AnalyzerInvocationError,
    AnalyzerResponseError,
)
from account_deletion_guard import (  # noqa: E402
    AccountDeletionInProgress,
    DeletionGuardUnavailable,
)


class HistoricalReanalysisWorkerTests(
    unittest.TestCase
):
    def setUp(self):
        self.guard = patch(
            "historical_reanalysis_worker.ensure_user_mutation_allowed"
        )
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_non_object_input_is_rejected(
        self,
    ):
        with self.assertRaises(ValueError):
            lambda_handler([], None)

    def test_missing_identifiers_are_rejected(
        self,
    ):
        with self.assertRaises(ValueError):
            lambda_handler({}, None)

        with self.assertRaises(ValueError):
            lambda_handler(
                {"userId": "user-test"},
                None,
            )

    @patch(
        "historical_reanalysis_worker."
        "get_entry_by_id"
    )
    def test_missing_entry_is_skipped(
        self,
        get_entry,
    ):
        get_entry.return_value = None

        result = lambda_handler({
            "userId": "user-test",
            "entryId": "entry-test",
            "jobId": "job-test",
        }, None)

        self.assertEqual(
            result["outcome"],
            "SKIPPED",
        )
        self.assertEqual(
            result["skipReason"],
            "entryNotFound",
        )

    @patch(
        "historical_reanalysis_worker."
        "analyze_journal_entry_llm"
    )
    @patch(
        "historical_reanalysis_worker."
        "get_entry_by_id"
    )
    def test_versioned_entry_is_skipped(
        self,
        get_entry,
        analyze,
    ):
        get_entry.return_value = {
            "rawText": "Existing text",
            "analysisVersionId": (
                "analysis-existing"
            ),
            "analysisVersionCount": 1,
        }

        result = lambda_handler({
            "userId": "user-test",
            "entryId": "entry-test",
        }, None)

        self.assertEqual(
            result["outcome"],
            "SKIPPED",
        )
        self.assertEqual(
            result["skipReason"],
            "alreadyVersioned",
        )

        analyze.assert_not_called()

    @patch(
        "historical_reanalysis_worker."
        "analyze_journal_entry_llm"
    )
    @patch(
        "historical_reanalysis_worker."
        "get_entry_by_id"
    )
    def test_entry_without_text_is_skipped(
        self,
        get_entry,
        analyze,
    ):
        get_entry.return_value = {
            "rawText": " ",
            "cleanText": "",
            "analysisStatus": (
                "NOT_ANALYZED"
            ),
        }

        result = lambda_handler({
            "userId": "user-test",
            "entryId": "entry-test",
        }, None)

        self.assertEqual(
            result["outcome"],
            "SKIPPED",
        )
        self.assertEqual(
            result["skipReason"],
            "noUsableText",
        )

        analyze.assert_not_called()

    @patch(
        "historical_reanalysis_worker."
        "update_entry_analysis"
    )
    @patch(
        "historical_reanalysis_worker."
        "analyze_journal_entry_llm"
    )
    @patch(
        "historical_reanalysis_worker."
        "get_entry_by_id"
    )
    def test_eligible_entry_is_analyzed(
        self,
        get_entry,
        analyze,
        update_analysis,
    ):
        get_entry.return_value = {
            "cleanText": (
                "A historical journal entry."
            ),
            "analysisStatus": (
                "NOT_ANALYZED"
            ),
        }

        analyze.return_value = {
            "status": "ANALYZED",
            "modelId": "test-model",
            "schemaVersion": "2.0",
            "usage": {
                "inputTokens": 20,
                "outputTokens": 10,
                "totalTokens": 30,
            },
        }

        update_analysis.return_value = {
            "analysisVersionCount": 1,
        }

        result = lambda_handler({
            "userId": "user-test",
            "entryId": "entry-test",
            "jobId": "job-test",
        }, None)

        self.assertEqual(
            result["outcome"],
            "COMPLETED",
        )
        self.assertEqual(
            result["analysisSource"],
            ANALYSIS_SOURCE,
        )
        self.assertEqual(
            result["totalTokens"],
            30,
        )

        update_analysis.assert_called_once()

        call = (
            update_analysis.call_args.kwargs
        )

        self.assertEqual(
            call["analysis_source"],
            "historical_reanalysis",
        )

        self.assertNotIn(
            "analysis",
            result,
        )
        self.assertNotIn(
            "rawText",
            result,
        )
        self.assertNotIn(
            "cleanText",
            result,
        )

    def test_final_guard_blocks_analysis_and_failure_writes_after_bedrock(self):
        for guard_error in (
            AccountDeletionInProgress(),
            DeletionGuardUnavailable(),
        ):
            events = []

            def analyze(_journal_text):
                events.append("analyze")
                return {"status": "ANALYZED", "usage": {"totalTokens": 1}}

            def reject(_user_id):
                self.assertEqual(events, ["analyze"])
                raise guard_error

            with (
                self.subTest(error=type(guard_error).__name__),
                patch(
                    "historical_reanalysis_worker.get_entry_by_id",
                    return_value={
                        "cleanText": "Eligible historical text.",
                        "analysisStatus": "NOT_ANALYZED",
                    },
                ),
                patch(
                    "historical_reanalysis_worker.analyze_journal_entry_llm",
                    side_effect=analyze,
                ) as analyze_mock,
                patch(
                    "historical_reanalysis_worker.ensure_user_mutation_allowed",
                    side_effect=reject,
                ),
                patch(
                    "historical_reanalysis_worker.update_entry_analysis",
                ) as update_analysis,
                patch(
                    "historical_reanalysis_worker.record_worker_failure",
                ) as record_failure,
                self.assertRaises(type(guard_error)),
            ):
                lambda_handler({
                    "userId": "user-test",
                    "entryId": "entry-test",
                    "jobId": "job-test",
                }, None)

            analyze_mock.assert_called_once()
            update_analysis.assert_not_called()
            record_failure.assert_not_called()

    def test_failure_recording_guard_blocks_fallback_user_write(self):
        for guard_error in (
            AccountDeletionInProgress(),
            DeletionGuardUnavailable(),
        ):
            with (
                self.subTest(error=type(guard_error).__name__),
                patch(
                    "historical_reanalysis_worker.ensure_user_mutation_allowed",
                    side_effect=guard_error,
                ),
                patch(
                    "historical_reanalysis_worker.mark_entry_analysis_failed",
                ) as mark_failed,
            ):
                record_worker_failure(
                    user_id="user-test",
                    entry_id="entry-test",
                    failure_code="SyntheticFailure",
                    failure_message="Safe failure.",
                )
            mark_failed.assert_not_called()

    @patch(
        "historical_reanalysis_worker."
        "mark_entry_analysis_failed"
    )
    @patch(
        "historical_reanalysis_worker."
        "analyze_journal_entry_llm"
    )
    @patch(
        "historical_reanalysis_worker."
        "get_entry_by_id"
    )
    def test_retryable_provider_failure_isolated(
        self,
        get_entry,
        analyze,
        mark_failed,
    ):
        get_entry.return_value = {
            "rawText": (
                "Retryable historical entry."
            ),
            "analysisStatus": (
                "NOT_ANALYZED"
            ),
        }

        analyze.side_effect = (
            AnalyzerInvocationError(
                "Provider unavailable.",
                error_code=(
                    "ThrottlingException"
                ),
                retryable=True,
                retry_attempts=2,
            )
        )

        result = lambda_handler({
            "userId": "user-test",
            "entryId": "entry-test",
        }, None)

        self.assertEqual(
            result["outcome"],
            "FAILED",
        )
        self.assertTrue(
            result["retryable"]
        )
        self.assertEqual(
            result["sdkRetryAttempts"],
            2,
        )

        mark_failed.assert_called_once()

    @patch(
        "historical_reanalysis_worker."
        "mark_entry_analysis_failed"
    )
    @patch(
        "historical_reanalysis_worker."
        "analyze_journal_entry_llm"
    )
    @patch(
        "historical_reanalysis_worker."
        "get_entry_by_id"
    )
    def test_invalid_provider_response_isolated(
        self,
        get_entry,
        analyze,
        mark_failed,
    ):
        get_entry.return_value = {
            "rawText": (
                "Historical journal entry."
            ),
            "analysisStatus": (
                "NOT_ANALYZED"
            ),
        }

        analyze.side_effect = (
            AnalyzerResponseError(
                "Invalid provider response."
            )
        )

        result = lambda_handler({
            "userId": "user-test",
            "entryId": "entry-test",
        }, None)

        self.assertEqual(
            result["outcome"],
            "FAILED",
        )
        self.assertFalse(
            result["retryable"]
        )

        mark_failed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
