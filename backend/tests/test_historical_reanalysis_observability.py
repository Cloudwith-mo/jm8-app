import io
import json
import os
import unittest
from contextlib import redirect_stdout
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


def captured_events(
    callback,
) -> list[dict]:
    output = io.StringIO()

    with redirect_stdout(output):
        callback()

    events = []

    for line in output.getvalue().splitlines():
        line = line.strip()

        if not line:
            continue

        events.append(
            json.loads(line)
        )

    return events


class HistoricalReanalysisObservabilityTests(
    unittest.TestCase
):
    @patch(
        "historical_reanalysis_coordinator."
        "list_historical_reanalysis_candidates"
    )
    @patch(
        "historical_reanalysis_coordinator."
        "begin_historical_reanalysis_job"
    )
    def test_prepare_event_is_structured_and_safe(
        self,
        begin_job,
        list_candidates,
    ):
        begin_job.return_value = {
            "status": "RUNNING",
            "pageSize": 10,
        }

        list_candidates.return_value = {
            "entries": [
                {
                    "entryId": "entry-test",
                },
            ],
            "count": 1,
            "evaluatedEntries": 2,
            "pageSize": 10,
            "hasMore": False,
            "nextCursor": None,
        }

        events = captured_events(
            lambda: lambda_handler({
                "action": "PREPARE",
                "userId": "private-user-id",
                "jobId": "reanalysis-test",
                "cursor": (
                    "private-cursor-value"
                ),
            }, None)
        )

        self.assertEqual(
            len(events),
            1,
        )

        event = events[0]

        self.assertEqual(
            event["event"],
            (
                "historical_reanalysis_"
                "page_prepared"
            ),
        )

        self.assertEqual(
            event["candidateEntries"],
            1,
        )

        serialized = json.dumps(
            events
        )

        self.assertNotIn(
            "private-user-id",
            serialized,
        )

        self.assertNotIn(
            "private-cursor-value",
            serialized,
        )

        self.assertNotIn(
            "rawText",
            serialized,
        )

    @patch(
        "historical_reanalysis_coordinator."
        "record_historical_reanalysis_page"
    )
    def test_completion_events_include_counts(
        self,
        record_page,
    ):
        record_page.return_value = {
            "status": "COMPLETED",
            "processedEntries": 5,
            "completedEntries": 4,
            "failedEntries": 1,
            "skippedEntries": 0,
            "remainingEntries": 0,
        }

        events = captured_events(
            lambda: lambda_handler({
                "action": "RECORD",
                "userId": "private-user-id",
                "jobId": "reanalysis-test",
                "page": {
                    "pageId": "a" * 32,
                    "hasMore": False,
                    "nextCursor": None,
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
        )

        self.assertEqual(
            [
                event["event"]
                for event in events
            ],
            [
                (
                    "historical_reanalysis_"
                    "page_recorded"
                ),
                (
                    "historical_reanalysis_"
                    "job_completed"
                ),
            ],
        )

        completed = events[1]

        self.assertEqual(
            completed["processedEntries"],
            5,
        )

        self.assertEqual(
            completed["remainingEntries"],
            0,
        )

        self.assertNotIn(
            "private-user-id",
            json.dumps(events),
        )

    @patch(
        "historical_reanalysis_coordinator."
        "fail_historical_reanalysis_job"
    )
    def test_failure_event_excludes_private_cause(
        self,
        fail_job,
    ):
        fail_job.return_value = {
            "status": "FAILED",
            "failureCode": (
                "LambdaFailure"
            ),
        }

        events = captured_events(
            lambda: lambda_handler({
                "action": "FAIL",
                "userId": "private-user-id",
                "jobId": "reanalysis-test",
                "workflowError": {
                    "Error": (
                        "LambdaFailure"
                    ),
                    "Cause": (
                        "private journal text"
                    ),
                },
            }, None)
        )

        self.assertEqual(
            len(events),
            1,
        )

        self.assertEqual(
            events[0]["event"],
            (
                "historical_reanalysis_"
                "workflow_failure_recorded"
            ),
        )

        serialized = json.dumps(
            events
        )

        self.assertNotIn(
            "private journal text",
            serialized,
        )

        self.assertNotIn(
            "private-user-id",
            serialized,
        )

    def test_deploy_script_includes_historical_logs(
        self,
    ):
        script = Path(
            "bin/deploy-observability"
        ).read_text()

        self.assertIn(
            (
                "${APP_NAME}-${STAGE}-"
                "historical-reanalysis-worker"
            ),
            script,
        )

        self.assertIn(
            (
                "${APP_NAME}-${STAGE}-"
                "historical-reanalysis-"
                "coordinator"
            ),
            script,
        )

        self.assertIn(
            (
                '"$HISTORICAL_'
                'WORKER_LOG_GROUP"'
            ),
            script,
        )

        self.assertIn(
            (
                '"$HISTORICAL_'
                'COORDINATOR_LOG_GROUP"'
            ),
            script,
        )

        self.assertIn(
            "retention-in-days 30",
            script,
        )

    def test_deploy_script_defines_historical_metrics(
        self,
    ):
        script = Path(
            "bin/deploy-observability"
        ).read_text()

        self.assertIn(
            (
                'REANALYSIS_METRIC_NAMESPACE='
                '"JM8/HistoricalReanalysis"'
            ),
            script,
        )

        event_names = [
            (
                "historical_reanalysis_"
                "accepted"
            ),
            (
                "historical_reanalysis_"
                "retry_accepted"
            ),
            (
                "historical_reanalysis_"
                "start_failed"
            ),
            (
                "historical_reanalysis_"
                "retry_start_failed"
            ),
            (
                "historical_reanalysis_"
                "job_completed"
            ),
            (
                "historical_reanalysis_"
                "workflow_failure_recorded"
            ),
            (
                "historical_reanalysis_"
                "page_recorded"
            ),
            (
                "historical_reanalysis_"
                "completed"
            ),
            (
                "historical_reanalysis_"
                "failed"
            ),
            (
                "historical_reanalysis_"
                "skipped"
            ),
        ]

        metric_names = [
            "JobsAccepted",
            "RetriesAccepted",
            "StartFailures",
            "JobsCompleted",
            "WorkflowFailures",
            "EntriesProcessed",
            "EntriesCompleted",
            "EntriesFailed",
            "EntriesSkipped",
        ]

        for event_name in event_names:
            self.assertIn(
                event_name,
                script,
            )

        for metric_name in metric_names:
            self.assertIn(
                metric_name,
                script,
            )

        self.assertIn(
            "$.pageProcessedEntries",
            script,
        )

        self.assertIn(
            (
                '"$HISTORICAL_'
                'WORKER_LOG_GROUP"'
            ),
            script,
        )

        self.assertIn(
            (
                '"$HISTORICAL_'
                'COORDINATOR_LOG_GROUP"'
            ),
            script,
        )


if __name__ == "__main__":
    unittest.main()
