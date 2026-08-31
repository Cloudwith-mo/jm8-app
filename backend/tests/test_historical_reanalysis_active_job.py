import json
import os
import unittest
from pathlib import Path
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
    (
        "arn:aws:states:us-east-1:"
        "000000000000:stateMachine:test"
    ),
)

import app  # noqa: E402
import storage  # noqa: E402


def sample_inventory() -> dict:
    return {
        "totalEntries": 4,
        "eligibleEntries": 2,
        "skippedEntries": 2,
        "estimatedBedrockRequests": 2,
    }


def sample_job(
    status: str = "RUNNING",
) -> dict:
    return {
        "jobId": "reanalysis_active",
        "status": status,
        "eligibleEntries": 2,
        "processedEntries": 0,
        "completedEntries": 0,
        "failedEntries": 0,
        "skippedEntries": 0,
        "remainingEntries": 2,
        "pageSize": 1,
    }


def api_event() -> dict:
    return {
        "requestContext": {
            "http": {
                "method": "POST",
                "path": (
                    "/analysis/reanalysis/jobs"
                ),
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
        "body": json.dumps({
            "pageSize": 1,
        }),
    }


class HistoricalReanalysisActiveJobTests(
    unittest.TestCase
):
    def setUp(self):
        self.guard = patch("app.ensure_user_mutation_allowed")
        self.guard.start()
        self.addCleanup(self.guard.stop)

    @patch.object(
        storage,
        "get_historical_reanalysis_job",
    )
    @patch.object(
        storage.table,
        "get_item",
    )
    @patch.object(
        storage.dynamodb_client,
        "transact_write_items",
    )
    def test_concurrent_creation_is_blocked(
        self,
        transact_write_items,
        get_item,
        get_job,
    ):
        transact_write_items.side_effect = (
            ClientError(
                {
                    "Error": {
                        "Code": (
                            "TransactionCanceled"
                            "Exception"
                        ),
                        "Message": "conflict",
                    },
                },
                "TransactWriteItems",
            )
        )

        get_item.return_value = {
            "Item": {
                "PK": storage.user_pk(
                    "user-test"
                ),
                "SK": (
                    storage
                    .historical_reanalysis_active_sk()
                ),
                "jobId": "reanalysis_active",
                "status": "RUNNING",
            },
        }

        get_job.return_value = sample_job()

        with self.assertRaises(
            storage
            .ActiveHistoricalReanalysisJobError
        ) as context:
            storage.create_historical_reanalysis_job(
                "user-test",
                sample_inventory(),
                page_size=1,
            )

        self.assertEqual(
            context.exception.job["status"],
            "RUNNING",
        )

    @patch.object(
        storage,
        "get_historical_reanalysis_job",
    )
    @patch.object(
        storage.dynamodb_client,
        "transact_write_items",
    )
    def test_final_page_releases_lock(
        self,
        transact_write_items,
        get_job,
    ):
        get_job.return_value = sample_job(
            status="COMPLETED"
        )

        storage.record_historical_reanalysis_page(
            "user-test",
            "reanalysis_active",
            page_id="a" * 32,
            results=[
                {
                    "outcome": "COMPLETED",
                },
            ],
            has_more=False,
            next_cursor=None,
        )

        transaction = (
            transact_write_items
            .call_args
            .kwargs["TransactItems"]
        )

        self.assertEqual(
            len(transaction),
            3,
        )

        delete = transaction[2]["Delete"]

        self.assertEqual(
            delete["Key"]["SK"]["S"],
            storage
            .historical_reanalysis_active_sk(),
        )

    @patch.object(
        storage,
        "get_historical_reanalysis_job",
    )
    @patch.object(
        storage.dynamodb_client,
        "transact_write_items",
    )
    def test_nonfinal_page_keeps_lock(
        self,
        transact_write_items,
        get_job,
    ):
        get_job.return_value = sample_job()

        storage.record_historical_reanalysis_page(
            "user-test",
            "reanalysis_active",
            page_id="b" * 32,
            results=[
                {
                    "outcome": "COMPLETED",
                },
            ],
            has_more=True,
            next_cursor="cursor-test",
        )

        transaction = (
            transact_write_items
            .call_args
            .kwargs["TransactItems"]
        )

        self.assertEqual(
            len(transaction),
            2,
        )

    @patch.object(
        storage,
        "get_historical_reanalysis_job",
    )
    @patch.object(
        storage.dynamodb_client,
        "transact_write_items",
    )
    def test_failed_job_releases_lock(
        self,
        transact_write_items,
        get_job,
    ):
        get_job.return_value = sample_job(
            status="FAILED"
        )

        job = (
            storage
            .fail_historical_reanalysis_job(
                "user-test",
                "reanalysis_active",
                failure_code="TestFailure",
            )
        )

        transaction = (
            transact_write_items
            .call_args
            .kwargs["TransactItems"]
        )

        self.assertEqual(
            job["status"],
            "FAILED",
        )
        self.assertEqual(
            len(transaction),
            2,
        )

        delete = transaction[1]["Delete"]

        self.assertEqual(
            delete["Key"]["SK"]["S"],
            storage
            .historical_reanalysis_active_sk(),
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
    def test_api_returns_active_job_conflict(
        self,
        get_inventory,
        create_job,
        start_execution,
    ):
        get_inventory.return_value = (
            sample_inventory()
        )

        create_job.side_effect = (
            app.ActiveHistoricalReanalysisJobError(
                sample_job()
            )
        )

        result = app.lambda_handler(
            api_event(),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            409,
        )
        self.assertEqual(
            body["error"],
            "HistoricalReanalysisJobActive",
        )
        self.assertEqual(
            body["job"]["status"],
            "RUNNING",
        )
        self.assertNotIn(
            "userId",
            json.dumps(body),
        )

        start_execution.assert_not_called()

    def test_coordinator_can_delete_lock(
        self,
    ):
        script = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath(
                "bin/"
                "deploy-historical-"
                "reanalysis-workflow"
            )
            .read_text()
        )

        worker_section = (
            script
            .split(
                "worker_policy = {",
                1,
            )[1]
            .split(
                "coordinator_policy = {",
                1,
            )[0]
        )

        coordinator_section = (
            script
            .split(
                "coordinator_policy = {",
                1,
            )[1]
            .split(
                "worker_policy_path.write_text",
                1,
            )[0]
        )

        self.assertNotIn(
            '"dynamodb:DeleteItem"',
            worker_section,
        )
        self.assertIn(
            '"dynamodb:DeleteItem"',
            coordinator_section,
        )


if __name__ == "__main__":
    unittest.main()
