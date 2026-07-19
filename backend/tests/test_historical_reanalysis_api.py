import json
import os
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError


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
    "HISTORICAL_REANALYSIS_WORKFLOW_ARN",
    "arn:aws:states:us-east-1:"
    "000000000000:stateMachine:test",
)

import app  # noqa: E402
import historical_reanalysis_workflow_client as workflow_client  # noqa: E402


def api_event(
    method: str,
    path: str,
    body: dict | None = None,
) -> dict:
    event = {
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
            },
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "user-test",
                    },
                },
            },
        },
        "headers": {
            "content-type": (
                "application/json"
            ),
        },
    }

    if body is not None:
        event["body"] = json.dumps(
            body
        )

    return event


def response_body(
    result: dict,
) -> dict:
    return json.loads(
        result["body"]
    )


def sample_inventory(
    eligible: int = 2,
) -> dict:
    return {
        "totalEntries": 4,
        "entriesWithUsableText": 3,
        "entriesWithoutUsableText": 1,
        "alreadyVersionedEntries": 1,
        "legacyAnalyzedEntries": 1,
        "neverAnalyzedEntries": 2,
        "failedOrIncompleteEntries": 0,
        "eligibleEntries": eligible,
        "skippedEntries": (
            4 - eligible
        ),
        "skipReasons": {
            "alreadyVersioned": 1,
            "noUsableText": 1,
        },
        "estimatedBedrockRequests": (
            eligible
        ),
    }


def sample_job(
    status: str = "QUEUED",
) -> dict:
    return {
        "jobId": (
            "reanalysis_test123"
        ),
        "status": status,
        "totalEntries": 4,
        "eligibleEntries": 2,
        "processedEntries": 0,
        "completedEntries": 0,
        "failedEntries": 0,
        "skippedEntries": 0,
        "remainingEntries": 2,
        "pageSize": 25,
    }


class HistoricalReanalysisApiTests(
    unittest.TestCase
):
    @patch.object(
        app,
        "start_historical_reanalysis_execution",
    )
    @patch.object(
        app,
        "create_historical_reanalysis_job",
    )
    @patch.object(
        app,
        "get_historical_analysis_inventory",
    )
    def test_start_returns_202(
        self,
        inventory,
        create_job,
        start_execution,
    ):
        inventory.return_value = (
            sample_inventory()
        )
        create_job.return_value = (
            sample_job()
        )
        start_execution.return_value = {
            "executionName": (
                "reanalysis_test123"
            ),
            "duplicate": False,
        }

        result = app.lambda_handler(
            api_event(
                "POST",
                "/analysis/reanalysis/jobs",
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            202,
        )
        self.assertEqual(
            body["job"]["status"],
            "QUEUED",
        )
        self.assertNotIn(
            "executionArn",
            json.dumps(body),
        )
        self.assertNotIn(
            "userId",
            json.dumps(body),
        )

    @patch.object(
        app,
        "create_historical_reanalysis_job",
    )
    @patch.object(
        app,
        "get_historical_analysis_inventory",
    )
    def test_no_eligible_entries_returns_409(
        self,
        inventory,
        create_job,
    ):
        inventory.return_value = (
            sample_inventory(
                eligible=0,
            )
        )

        result = app.lambda_handler(
            api_event(
                "POST",
                "/analysis/reanalysis/jobs",
            ),
            None,
        )

        self.assertEqual(
            result["statusCode"],
            409,
        )
        create_job.assert_not_called()

    def test_invalid_page_size_returns_400(
        self,
    ):
        result = app.lambda_handler(
            api_event(
                "POST",
                "/analysis/reanalysis/jobs",
                {
                    "pageSize": 26,
                },
            ),
            None,
        )

        self.assertEqual(
            result["statusCode"],
            400,
        )

    @patch.object(
        app,
        "fail_historical_reanalysis_job",
    )
    @patch.object(
        app,
        "start_historical_reanalysis_execution",
    )
    @patch.object(
        app,
        "create_historical_reanalysis_job",
    )
    @patch.object(
        app,
        "get_historical_analysis_inventory",
    )
    def test_start_failure_is_sanitized(
        self,
        inventory,
        create_job,
        start_execution,
        fail_job,
    ):
        inventory.return_value = (
            sample_inventory()
        )
        create_job.return_value = (
            sample_job()
        )
        start_execution.side_effect = (
            RuntimeError(
                "private provider detail"
            )
        )
        fail_job.return_value = (
            sample_job(
                status="FAILED",
            )
        )

        result = app.lambda_handler(
            api_event(
                "POST",
                "/analysis/reanalysis/jobs",
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            502,
        )
        self.assertNotIn(
            "private provider detail",
            json.dumps(body),
        )
        self.assertEqual(
            body["job"]["status"],
            "FAILED",
        )

    @patch.object(
        app,
        "get_historical_reanalysis_job",
    )
    def test_status_returns_owned_job(
        self,
        get_job,
    ):
        get_job.return_value = (
            sample_job(
                status="RUNNING",
            )
        )

        result = app.lambda_handler(
            api_event(
                "GET",
                (
                    "/analysis/reanalysis/"
                    "jobs/reanalysis_test123"
                ),
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            200,
        )
        self.assertEqual(
            body["job"]["status"],
            "RUNNING",
        )

        get_job.assert_called_once_with(
            user_id="user-test",
            job_id=(
                "reanalysis_test123"
            ),
        )

    @patch.object(
        app,
        "get_historical_reanalysis_job",
    )
    def test_missing_job_returns_404(
        self,
        get_job,
    ):
        get_job.return_value = None

        result = app.lambda_handler(
            api_event(
                "GET",
                (
                    "/analysis/reanalysis/"
                    "jobs/reanalysis_missing"
                ),
            ),
            None,
        )

        self.assertEqual(
            result["statusCode"],
            404,
        )

    def test_invalid_job_id_returns_400(
        self,
    ):
        result = app.lambda_handler(
            api_event(
                "GET",
                (
                    "/analysis/reanalysis/"
                    "jobs/not%20valid"
                ),
            ),
            None,
        )

        self.assertEqual(
            result["statusCode"],
            400,
        )


class HistoricalWorkflowClientTests(
    unittest.TestCase
):
    @patch.object(
        workflow_client.stepfunctions,
        "start_execution",
    )
    def test_workflow_payload_contains_ids_only(
        self,
        start_execution,
    ):
        start_execution.return_value = {
            "executionArn": "test-arn",
        }

        workflow_client.start_historical_reanalysis_execution(
            user_id="user-test",
            job_id="reanalysis_test123",
        )

        arguments = (
            start_execution.call_args.kwargs
        )

        payload = json.loads(
            arguments["input"]
        )

        self.assertEqual(
            payload,
            {
                "userId": "user-test",
                "jobId": (
                    "reanalysis_test123"
                ),
                "cursor": None,
            },
        )

        self.assertNotIn(
            "text",
            json.dumps(payload).lower(),
        )

    @patch.object(
        workflow_client.stepfunctions,
        "start_execution",
    )
    def test_duplicate_execution_is_accepted(
        self,
        start_execution,
    ):
        start_execution.side_effect = (
            ClientError(
                {
                    "Error": {
                        "Code": (
                            "ExecutionAlreadyExists"
                        ),
                        "Message": "duplicate",
                    },
                },
                "StartExecution",
            )
        )

        result = (
            workflow_client
            .start_historical_reanalysis_execution(
                user_id="user-test",
                job_id=(
                    "reanalysis_test123"
                ),
            )
        )

        self.assertTrue(
            result["duplicate"]
        )


if __name__ == "__main__":
    unittest.main()
