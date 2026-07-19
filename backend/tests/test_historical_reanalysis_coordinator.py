import json
import os
import unittest
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

from historical_reanalysis_coordinator import (  # noqa: E402
    lambda_handler,
)
from storage import (  # noqa: E402
    historical_reanalysis_page_id,
    summarize_historical_reanalysis_results,
)


class HistoricalReanalysisCoordinatorTests(
    unittest.TestCase
):
    def test_invalid_action_is_rejected(
        self,
    ):
        with self.assertRaises(ValueError):
            lambda_handler(
                {"action": "UNKNOWN"},
                None,
            )

    @patch(
        "historical_reanalysis_coordinator."
        "list_historical_reanalysis_candidates"
    )
    @patch(
        "historical_reanalysis_coordinator."
        "begin_historical_reanalysis_job"
    )
    def test_prepare_page(
        self,
        begin_job,
        list_candidates,
    ):
        begin_job.return_value = {
            "jobId": "reanalysis-test",
            "status": "RUNNING",
            "pageSize": 20,
        }

        list_candidates.return_value = {
            "entries": [
                {"entryId": "entry-one"},
            ],
            "count": 1,
            "evaluatedEntries": 2,
            "pageSize": 20,
            "hasMore": True,
            "nextCursor": "cursor-next",
        }

        result = lambda_handler({
            "action": "PREPARE",
            "userId": "user-test",
            "jobId": "reanalysis-test",
            "cursor": None,
        }, None)

        self.assertEqual(
            result["count"],
            1,
        )
        self.assertTrue(
            result["hasMore"]
        )
        self.assertEqual(
            result["entries"],
            [{"entryId": "entry-one"}],
        )
        self.assertEqual(
            len(result["pageId"]),
            32,
        )
        self.assertNotIn(
            "rawText",
            str(result),
        )

    @patch(
        "historical_reanalysis_coordinator."
        "record_historical_reanalysis_page"
    )
    def test_record_page(
        self,
        record_page,
    ):
        record_page.return_value = {
            "jobId": "reanalysis-test",
            "status": "RUNNING",
            "processedEntries": 2,
            "completedEntries": 1,
            "failedEntries": 1,
            "skippedEntries": 0,
            "remainingEntries": 3,
        }

        result = lambda_handler({
            "action": "RECORD",
            "userId": "user-test",
            "jobId": "reanalysis-test",
            "page": {
                "pageId": "a" * 32,
                "hasMore": True,
                "nextCursor": "cursor-next",
            },
            "results": [
                {
                    "outcome": "COMPLETED",
                },
                {
                    "outcome": "FAILED",
                },
            ],
        }, None)

        self.assertEqual(
            result["status"],
            "RUNNING",
        )
        self.assertEqual(
            result["processedEntries"],
            2,
        )
        self.assertTrue(
            result["hasMore"]
        )

    @patch(
        "historical_reanalysis_coordinator."
        "fail_historical_reanalysis_job"
    )
    def test_failure_cause_is_not_stored(
        self,
        fail_job,
    ):
        fail_job.return_value = {
            "jobId": "reanalysis-test",
            "status": "FAILED",
            "failureCode": "LambdaFailure",
        }

        result = lambda_handler({
            "action": "FAIL",
            "userId": "user-test",
            "jobId": "reanalysis-test",
            "workflowError": {
                "Error": "LambdaFailure",
                "Cause": (
                    "private journal content"
                ),
            },
        }, None)

        call = fail_job.call_args.kwargs

        self.assertEqual(
            call["failure_code"],
            "LambdaFailure",
        )
        self.assertNotIn(
            "private journal content",
            str(call),
        )
        self.assertEqual(
            result["status"],
            "FAILED",
        )

    def test_result_summary(self):
        summary = (
            summarize_historical_reanalysis_results([
                {"outcome": "COMPLETED"},
                {"outcome": "FAILED"},
                {"outcome": "SKIPPED"},
                {"outcome": "UNKNOWN"},
            ])
        )

        self.assertEqual(
            summary["processedEntries"],
            4,
        )
        self.assertEqual(
            summary["completedEntries"],
            1,
        )
        self.assertEqual(
            summary["failedEntries"],
            2,
        )
        self.assertEqual(
            summary["skippedEntries"],
            1,
        )

    def test_page_id_is_deterministic(self):
        first = (
            historical_reanalysis_page_id(
                "reanalysis-test",
                "cursor-test",
            )
        )

        second = (
            historical_reanalysis_page_id(
                "reanalysis-test",
                "cursor-test",
            )
        )

        different = (
            historical_reanalysis_page_id(
                "reanalysis-test",
                "different-cursor",
            )
        )

        self.assertEqual(
            first,
            second,
        )
        self.assertNotEqual(
            first,
            different,
        )

    def test_state_machine_is_controlled(self):
        definition = json.loads(
            Path(
                "infra/"
                "historical-reanalysis-"
                "workflow.asl.json"
            ).read_text()
        )

        process_page = definition[
            "States"
        ]["ProcessPage"]

        self.assertEqual(
            process_page[
                "MaxConcurrency"
            ],
            2,
        )

        self.assertEqual(
            process_page["Type"],
            "Map",
        )

        self.assertIn(
            "RecordWorkflowFailure",
            definition["States"],
        )

        serialized = json.dumps(
            definition
        )

        self.assertIn(
            "__REANALYSIS_WORKER_ARN__",
            serialized,
        )
        self.assertIn(
            "__REANALYSIS_COORDINATOR_ARN__",
            serialized,
        )


if __name__ == "__main__":
    unittest.main()
