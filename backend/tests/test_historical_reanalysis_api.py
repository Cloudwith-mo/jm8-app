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
    query: dict | None = None,
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

    if query is not None:
        event[
            "queryStringParameters"
        ] = query

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
    def setUp(self):
        self.guard = patch("app.ensure_user_mutation_allowed")
        self.guard.start()
        self.addCleanup(self.guard.stop)

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


    @patch.object(
        app,
        "list_historical_reanalysis_jobs",
    )
    def test_job_list_returns_200(
        self,
        list_jobs,
    ):
        list_jobs.return_value = [
            {
                "jobId": "reanalysis_test123",
                "status": "FAILED",
                "remainingEntries": 2,
            },
        ]

        result = app.lambda_handler(
            api_event(
                "GET",
                "/analysis/reanalysis/jobs",
                query={
                    "status": "failed",
                    "limit": "10",
                },
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            200,
        )
        self.assertEqual(
            body["count"],
            1,
        )
        self.assertEqual(
            body["statusFilter"],
            "FAILED",
        )
        self.assertEqual(
            body["limit"],
            10,
        )

        list_jobs.assert_called_once_with(
            user_id="user-test",
            status_filter="FAILED",
            limit=10,
        )

        self.assertNotIn(
            "userId",
            json.dumps(body),
        )
        self.assertNotIn(
            "executionArn",
            json.dumps(body),
        )

    @patch.object(
        app,
        "list_historical_reanalysis_jobs",
    )
    def test_job_list_invalid_status(
        self,
        list_jobs,
    ):
        result = app.lambda_handler(
            api_event(
                "GET",
                "/analysis/reanalysis/jobs",
                query={
                    "status": "BROKEN",
                },
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            400,
        )
        self.assertEqual(
            body["error"],
            "InvalidReanalysisJobStatus",
        )

        list_jobs.assert_not_called()

    @patch.object(
        app,
        "list_historical_reanalysis_jobs",
    )
    def test_job_list_invalid_limit(
        self,
        list_jobs,
    ):
        result = app.lambda_handler(
            api_event(
                "GET",
                "/analysis/reanalysis/jobs",
                query={
                    "limit": "51",
                },
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            400,
        )
        self.assertEqual(
            body["error"],
            "InvalidLimit",
        )

        list_jobs.assert_not_called()


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
    @patch.object(
        app,
        "get_historical_reanalysis_job",
    )
    def test_retry_failed_job_returns_202(
        self,
        get_job,
        inventory,
        create_job,
        start_execution,
    ):
        source_job = sample_job(
            status="FAILED",
        )
        source_job["pageSize"] = 10

        get_job.return_value = source_job
        inventory.return_value = (
            sample_inventory()
        )

        retry_job = {
            **sample_job(),
            "jobId": "reanalysis_retry456",
            "retryOfJobId": (
                "reanalysis_test123"
            ),
            "pageSize": 10,
        }

        create_job.return_value = retry_job

        start_execution.return_value = {
            "executionName": (
                "reanalysis_retry456"
            ),
            "duplicate": False,
        }

        result = app.lambda_handler(
            api_event(
                "POST",
                (
                    "/analysis/reanalysis/jobs/"
                    "reanalysis_test123/retry"
                ),
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            202,
        )
        self.assertEqual(
            body["job"]["retryOfJobId"],
            "reanalysis_test123",
        )

        create_job.assert_called_once_with(
            user_id="user-test",
            inventory=sample_inventory(),
            page_size=10,
            retry_of_job_id=(
                "reanalysis_test123"
            ),
        )

        start_execution.assert_called_once_with(
            user_id="user-test",
            job_id="reanalysis_retry456",
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
        "get_historical_reanalysis_job",
    )
    def test_retry_missing_job_returns_404(
        self,
        get_job,
    ):
        get_job.return_value = None

        result = app.lambda_handler(
            api_event(
                "POST",
                (
                    "/analysis/reanalysis/jobs/"
                    "reanalysis_missing/retry"
                ),
            ),
            None,
        )

        self.assertEqual(
            result["statusCode"],
            404,
        )

    @patch.object(
        app,
        "get_historical_reanalysis_job",
    )
    def test_retry_nonfailed_job_returns_409(
        self,
        get_job,
    ):
        get_job.return_value = (
            sample_job(
                status="COMPLETED",
            )
        )

        result = app.lambda_handler(
            api_event(
                "POST",
                (
                    "/analysis/reanalysis/jobs/"
                    "reanalysis_test123/retry"
                ),
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            409,
        )
        self.assertEqual(
            body["error"],
            (
                "HistoricalReanalysis"
                "JobNotRetryable"
            ),
        )

    @patch.object(
        app,
        "get_historical_analysis_inventory",
    )
    @patch.object(
        app,
        "get_historical_reanalysis_job",
    )
    def test_retry_invalid_page_size(
        self,
        get_job,
        inventory,
    ):
        get_job.return_value = (
            sample_job(
                status="FAILED",
            )
        )

        result = app.lambda_handler(
            api_event(
                "POST",
                (
                    "/analysis/reanalysis/jobs/"
                    "reanalysis_test123/retry"
                ),
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

        inventory.assert_not_called()

    @patch.object(
        app,
        "create_historical_reanalysis_job",
    )
    @patch.object(
        app,
        "get_historical_analysis_inventory",
    )
    @patch.object(
        app,
        "get_historical_reanalysis_job",
    )
    def test_retry_no_eligible_entries(
        self,
        get_job,
        inventory,
        create_job,
    ):
        get_job.return_value = (
            sample_job(
                status="FAILED",
            )
        )

        inventory.return_value = (
            sample_inventory(
                eligible=0,
            )
        )

        result = app.lambda_handler(
            api_event(
                "POST",
                (
                    "/analysis/reanalysis/jobs/"
                    "reanalysis_test123/retry"
                ),
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            409,
        )
        self.assertEqual(
            body["error"],
            "NoEligibleEntries",
        )

        create_job.assert_not_called()

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
    @patch.object(
        app,
        "get_historical_reanalysis_job",
    )
    def test_retry_active_job_is_blocked(
        self,
        get_job,
        inventory,
        create_job,
        start_execution,
    ):
        get_job.return_value = (
            sample_job(
                status="FAILED",
            )
        )

        inventory.return_value = (
            sample_inventory()
        )

        create_job.side_effect = (
            app.ActiveHistoricalReanalysisJobError(
                sample_job(
                    status="RUNNING",
                )
            )
        )

        result = app.lambda_handler(
            api_event(
                "POST",
                (
                    "/analysis/reanalysis/jobs/"
                    "reanalysis_test123/retry"
                ),
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            409,
        )
        self.assertEqual(
            body["error"],
            (
                "HistoricalReanalysis"
                "JobActive"
            ),
        )

        start_execution.assert_not_called()

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
    @patch.object(
        app,
        "get_historical_reanalysis_job",
    )
    def test_retry_start_failure_is_sanitized(
        self,
        get_job,
        inventory,
        create_job,
        start_execution,
        fail_job,
    ):
        get_job.return_value = (
            sample_job(
                status="FAILED",
            )
        )

        inventory.return_value = (
            sample_inventory()
        )

        retry_job = {
            **sample_job(),
            "jobId": "reanalysis_retry456",
            "retryOfJobId": (
                "reanalysis_test123"
            ),
        }

        create_job.return_value = retry_job

        start_execution.side_effect = (
            RuntimeError(
                "private provider detail"
            )
        )

        fail_job.return_value = {
            **retry_job,
            "status": "FAILED",
        }

        result = app.lambda_handler(
            api_event(
                "POST",
                (
                    "/analysis/reanalysis/jobs/"
                    "reanalysis_test123/retry"
                ),
            ),
            None,
        )

        body = response_body(result)

        self.assertEqual(
            result["statusCode"],
            502,
        )
        self.assertEqual(
            body["error"],
            (
                "HistoricalReanalysis"
                "RetryStartFailed"
            ),
        )
        self.assertNotIn(
            "private provider detail",
            json.dumps(body),
        )
        self.assertEqual(
            body["job"]["status"],
            "FAILED",
        )

    def test_retry_invalid_job_id_returns_400(
        self,
    ):
        result = app.lambda_handler(
            api_event(
                "POST",
                (
                    "/analysis/reanalysis/jobs/"
                    "not%20valid/retry"
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
