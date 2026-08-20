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
                '"JM8/${STAGE}/HistoricalReanalysis"'
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

    def test_deploy_script_defines_historical_alarms(
        self,
    ):
        script = Path(
            "bin/deploy-observability"
        ).read_text()

        self.assertIn(
            (
                'HISTORICAL_STATE_MACHINE_NAME='
                '"${APP_NAME}-${STAGE}-'
                'historical-reanalysis-workflow"'
            ),
            script,
        )

        self.assertIn(
            "HISTORICAL_STATE_MACHINE_ARN",
            script,
        )

        alarm_variables = [
            "REANALYSIS_START_FAILURES_ALARM",
            "REANALYSIS_ENTRY_FAILURES_ALARM",
            "REANALYSIS_WORKER_ERRORS_ALARM",
            "REANALYSIS_WORKER_THROTTLES_ALARM",
            "REANALYSIS_COORDINATOR_ERRORS_ALARM",
            (
                "REANALYSIS_COORDINATOR_"
                "THROTTLES_ALARM"
            ),
            "REANALYSIS_WORKFLOW_FAILED_ALARM",
            "REANALYSIS_WORKFLOW_TIMEOUT_ALARM",
        ]

        for alarm_variable in alarm_variables:
            self.assertIn(
                alarm_variable,
                script,
            )

        metric_names = [
            "StartFailures",
            "EntriesFailed",
            "Errors",
            "Throttles",
            "ExecutionsFailed",
            "ExecutionsTimedOut",
        ]

        for metric_name in metric_names:
            self.assertIn(
                f'"{metric_name}"',
                script,
            )

        self.assertIn(
            "put_undimensioned_count_alarm",
            script,
        )

        helper = script.split(
            "put_undimensioned_count_alarm() {",
            1,
        )[1].split(
            'echo "Creating CloudWatch alarms..."',
            1,
        )[0]

        self.assertNotIn(
            "--dimensions",
            helper,
        )

        self.assertNotIn(
            "--unit",
            helper,
        )

        self.assertIn(
            "--treat-missing-data",
            helper,
        )

        self.assertIn(
            "--alarm-actions",
            helper,
        )

    def test_deploy_script_defines_historical_dashboard(
        self,
    ):
        script = Path(
            "bin/deploy-observability"
        ).read_text()

        argument_values = [
            (
                '"$HISTORICAL_WORKER_'
                'FUNCTION_NAME"'
            ),
            (
                '"$HISTORICAL_COORDINATOR_'
                'FUNCTION_NAME"'
            ),
            (
                '"$HISTORICAL_STATE_'
                'MACHINE_ARN"'
            ),
            (
                '"$HISTORICAL_WORKER_'
                'LOG_GROUP"'
            ),
            (
                '"$HISTORICAL_COORDINATOR_'
                'LOG_GROUP"'
            ),
            (
                '"$REANALYSIS_METRIC_'
                'NAMESPACE"'
            ),
        ]

        for value in argument_values:
            self.assertIn(
                value,
                script,
            )

        assignments = [
            (
                "historical_worker_function "
                "= sys.argv[13]"
            ),
            (
                "historical_coordinator_function "
                "= sys.argv[14]"
            ),
            (
                "historical_state_machine_arn "
                "= sys.argv[15]"
            ),
            (
                "historical_worker_log_group "
                "= sys.argv[16]"
            ),
            (
                "historical_coordinator_log_group "
                "= sys.argv[17]"
            ),
            (
                "reanalysis_metric_namespace "
                "= sys.argv[18]"
            ),
        ]

        for assignment in assignments:
            self.assertIn(
                assignment,
                script,
            )

        titles = [
            (
                "Historical re-analysis "
                "operations"
            ),
            "Historical Lambda health",
            "Historical Lambda duration",
            (
                "Historical workflow "
                "executions"
            ),
            "Historical job metrics",
            "Historical entry outcomes",
            "Historical alarm status",
            (
                "Recent historical "
                "worker events"
            ),
            (
                "Recent historical "
                "coordinator events"
            ),
        ]

        for title in titles:
            self.assertIn(
                title,
                script,
            )

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

        dashboard_section = script.split(
            "historical_alarm_names = [",
            1,
        )[1].split(
            "print(json.dumps(dashboard))",
            1,
        )[0]

        for metric_name in metric_names:
            self.assertIn(
                metric_name,
                dashboard_section,
            )

        self.assertIn(
            (
                'dashboard["widgets"].'
                "extend(["
            ),
            script,
        )

        self.assertIn(
            "historical_alarm_arns",
            dashboard_section,
        )

        self.assertGreaterEqual(
            dashboard_section.count(
                "| fields @timestamp, event"
            ),
            2,
        )

        private_fields = [
            "@message",
            "userId",
            "jobId",
            "entryId",
            "rawText",
        ]

        for private_field in private_fields:
            self.assertNotIn(
                private_field,
                dashboard_section,
            )


if __name__ == "__main__":
    unittest.main()
