import os
import re
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

from storage import (  # noqa: E402
    create_historical_reanalysis_job,
    decode_historical_reanalysis_cursor,
    encode_historical_reanalysis_cursor,
    get_historical_reanalysis_job,
    historical_reanalysis_active_sk,
    historical_reanalysis_job_sk,
    list_historical_reanalysis_candidates,
    list_historical_reanalysis_jobs,
    public_historical_reanalysis_job,
    record_historical_reanalysis_page,
    user_pk,
)


class HistoricalReanalysisJobTests(
    unittest.TestCase
):
    def test_job_key_validation(self):
        self.assertEqual(
            historical_reanalysis_job_sk(
                "reanalysis_test"
            ),
            (
                "REANALYSIS_JOB#"
                "reanalysis_test"
            ),
        )

        with self.assertRaises(ValueError):
            historical_reanalysis_job_sk(
                "../invalid"
            )

    def test_cursor_round_trip(self):
        key = {
            "PK": user_pk("user-test"),
            "SK": (
                "ENTRY#2026-01-01#entry-test"
            ),
        }

        cursor = (
            encode_historical_reanalysis_cursor(
                key
            )
        )

        self.assertNotIn(
            "USER#",
            cursor,
        )

        decoded = (
            decode_historical_reanalysis_cursor(
                cursor,
                user_id="user-test",
            )
        )

        self.assertEqual(
            decoded,
            key,
        )

    def test_cross_user_cursor_is_rejected(
        self,
    ):
        cursor = (
            encode_historical_reanalysis_cursor({
                "PK": user_pk("user-one"),
                "SK": (
                    "ENTRY#2026-01-01#entry-test"
                ),
            })
        )

        with self.assertRaises(ValueError):
            decode_historical_reanalysis_cursor(
                cursor,
                user_id="user-two",
            )

    @patch("storage.table.query")
    def test_candidate_page_is_restricted(
        self,
        query,
    ):
        query.return_value = {
            "Items": [
                {
                    "entryId": "entry-eligible",
                    "rawText": "Usable text",
                    "analysisStatus": (
                        "NOT_ANALYZED"
                    ),
                },
                {
                    "entryId": "entry-versioned",
                    "cleanText": "Existing text",
                    "analysisVersionCount": 1,
                },
                {
                    "entryId": "entry-empty",
                    "rawText": " ",
                    "analysisStatus": (
                        "NOT_ANALYZED"
                    ),
                },
            ],
            "LastEvaluatedKey": {
                "PK": user_pk("user-test"),
                "SK": (
                    "ENTRY#2026-01-03#entry-empty"
                ),
            },
        }

        result = (
            list_historical_reanalysis_candidates(
                "user-test",
                limit=3,
            )
        )

        self.assertEqual(
            result["entries"],
            [{
                "entryId": "entry-eligible",
            }],
        )
        self.assertEqual(
            result["count"],
            1,
        )
        self.assertEqual(
            result["evaluatedEntries"],
            3,
        )
        self.assertTrue(
            result["hasMore"]
        )
        self.assertIsNotNone(
            result["nextCursor"]
        )

        self.assertNotIn(
            "rawText",
            str(result),
        )
        self.assertNotIn(
            "cleanText",
            str(result),
        )
        self.assertNotIn(
            "PK",
            str(result),
        )
        self.assertNotIn(
            "user-test",
            str(result),
        )

        arguments = query.call_args.kwargs

        self.assertTrue(
            arguments["ScanIndexForward"]
        )
        self.assertTrue(
            arguments["ConsistentRead"]
        )
        self.assertEqual(
            arguments["Limit"],
            3,
        )

    @patch("storage.table.query")
    def test_candidate_cursor_is_applied(
        self,
        query,
    ):
        start_key = {
            "PK": user_pk("user-test"),
            "SK": (
                "ENTRY#2026-01-01#entry-one"
            ),
        }

        cursor = (
            encode_historical_reanalysis_cursor(
                start_key
            )
        )

        query.return_value = {
            "Items": [],
        }

        list_historical_reanalysis_candidates(
            "user-test",
            limit=25,
            cursor=cursor,
        )

        arguments = query.call_args.kwargs

        self.assertEqual(
            arguments[
                "ExclusiveStartKey"
            ],
            start_key,
        )

    def test_public_job_hides_internal_fields(
        self,
    ):
        public_job = (
            public_historical_reanalysis_job({
                "PK": "USER#private",
                "SK": (
                    "REANALYSIS_JOB#private"
                ),
                "userId": "private-user",
                "executionArn": (
                    "private-execution-arn"
                ),
                "jobId": "reanalysis-test",
                "status": "QUEUED",
                "eligibleEntries": 5,
                "processedEntries": 0,
            })
        )

        self.assertEqual(
            public_job["jobId"],
            "reanalysis-test",
        )
        self.assertNotIn(
            "PK",
            public_job,
        )
        self.assertNotIn(
            "SK",
            public_job,
        )
        self.assertNotIn(
            "userId",
            public_job,
        )
        self.assertNotIn(
            "executionArn",
            public_job,
        )

    @patch(
        "storage.dynamodb_client."
        "transact_write_items"
    )
    @patch(
        "storage."
        "new_historical_reanalysis_job_id"
    )
    @patch("storage.utc_now")
    def test_job_creation(
        self,
        now,
        new_job_id,
        transact_write_items,
    ):
        now.return_value = (
            "2026-07-19T00:00:00+00:00"
        )

        new_job_id.return_value = (
            "reanalysis-test"
        )

        inventory = {
            "totalEntries": 9,
            "eligibleEntries": 6,
            "skippedEntries": 3,
            "estimatedBedrockRequests": 6,
        }

        job = (
            create_historical_reanalysis_job(
                "user-test",
                inventory,
                page_size=20,
            )
        )

        self.assertEqual(
            job["status"],
            "QUEUED",
        )
        self.assertEqual(
            job["eligibleEntries"],
            6,
        )
        self.assertEqual(
            job["remainingEntries"],
            6,
        )
        self.assertEqual(
            job["pageSize"],
            20,
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

        job_item = (
            transaction[0]["Put"]["Item"]
        )

        lock_item = (
            transaction[1]["Put"]["Item"]
        )

        self.assertEqual(
            job_item["PK"]["S"],
            user_pk("user-test"),
        )
        self.assertEqual(
            job_item["status"]["S"],
            "QUEUED",
        )
        self.assertEqual(
            job_item[
                "processedEntries"
            ]["N"],
            "0",
        )

        self.assertEqual(
            lock_item["PK"]["S"],
            user_pk("user-test"),
        )
        self.assertEqual(
            lock_item["SK"]["S"],
            historical_reanalysis_active_sk(),
        )
        self.assertEqual(
            lock_item["jobId"]["S"],
            "reanalysis-test",
        )

    @patch(
        "storage.dynamodb_client."
        "transact_write_items"
    )
    def test_empty_job_is_rejected(
        self,
        transact_write_items,
    ):
        with self.assertRaises(ValueError):
            create_historical_reanalysis_job(
                "user-test",
                {
                    "totalEntries": 4,
                    "eligibleEntries": 0,
                },
            )

        transact_write_items.assert_not_called()

    @patch("storage.table.get_item")
    def test_job_lookup_is_user_scoped(
        self,
        get_item,
    ):
        get_item.return_value = {
            "Item": {
                "PK": user_pk("user-test"),
                "SK": (
                    "REANALYSIS_JOB#"
                    "reanalysis-test"
                ),
                "userId": "user-test",
                "jobId": "reanalysis-test",
                "status": "RUNNING",
                "processedEntries": 2,
            },
        }

        job = get_historical_reanalysis_job(
            "user-test",
            "reanalysis-test",
        )

        self.assertEqual(
            job["status"],
            "RUNNING",
        )

        key = (
            get_item.call_args.kwargs["Key"]
        )

        self.assertEqual(
            key["PK"],
            user_pk("user-test"),
        )

    @patch("storage.table.query")
    def test_job_list_is_sorted_and_sanitized(
        self,
        query,
    ):
        query.return_value = {
            "Items": [
                {
                    "PK": "USER#private",
                    "SK": "REANALYSIS_JOB#older",
                    "userId": "private-user",
                    "executionArn": "private-arn",
                    "inventory": {
                        "private": True,
                    },
                    "jobId": "reanalysis_older",
                    "status": "FAILED",
                    "eligibleEntries": 4,
                    "remainingEntries": 2,
                    "createdAt": (
                        "2026-07-18T00:00:00+00:00"
                    ),
                },
                {
                    "PK": "USER#private",
                    "SK": "REANALYSIS_JOB#newer",
                    "userId": "private-user",
                    "executionArn": "private-arn",
                    "jobId": "reanalysis_newer",
                    "status": "COMPLETED",
                    "eligibleEntries": 2,
                    "remainingEntries": 0,
                    "createdAt": (
                        "2026-07-20T00:00:00+00:00"
                    ),
                },
            ],
        }

        jobs = list_historical_reanalysis_jobs(
            "user-test",
            limit=20,
        )

        self.assertEqual(
            [
                job["jobId"]
                for job in jobs
            ],
            [
                "reanalysis_newer",
                "reanalysis_older",
            ],
        )

        serialized = str(jobs)

        self.assertNotIn(
            "USER#",
            serialized,
        )
        self.assertNotIn(
            "private-user",
            serialized,
        )
        self.assertNotIn(
            "private-arn",
            serialized,
        )
        self.assertNotIn(
            "inventory",
            serialized,
        )
        self.assertNotIn(
            "'PK'",
            serialized,
        )
        self.assertNotIn(
            "'SK'",
            serialized,
        )

        arguments = query.call_args.kwargs

        self.assertTrue(
            arguments["ConsistentRead"]
        )

        condition = (
            arguments[
                "KeyConditionExpression"
            ].get_expression()
        )

        self.assertEqual(
            condition["operator"],
            "AND",
        )

        (
            partition_condition,
            sort_condition,
        ) = condition["values"]

        partition_expression = (
            partition_condition.get_expression()
        )

        sort_expression = (
            sort_condition.get_expression()
        )

        self.assertEqual(
            partition_expression["operator"],
            "=",
        )
        self.assertEqual(
            partition_expression[
                "values"
            ][0].name,
            "PK",
        )
        self.assertEqual(
            partition_expression[
                "values"
            ][1],
            user_pk("user-test"),
        )

        self.assertEqual(
            sort_expression["operator"],
            "begins_with",
        )
        self.assertEqual(
            sort_expression[
                "values"
            ][0].name,
            "SK",
        )
        self.assertEqual(
            sort_expression[
                "values"
            ][1],
            "REANALYSIS_JOB#",
        )

    @patch("storage.table.query")
    def test_job_list_filters_status(
        self,
        query,
    ):
        query.return_value = {
            "Items": [
                {
                    "jobId": "reanalysis_failed",
                    "status": "FAILED",
                    "createdAt": (
                        "2026-07-20T00:00:00+00:00"
                    ),
                },
                {
                    "jobId": "reanalysis_completed",
                    "status": "COMPLETED",
                    "createdAt": (
                        "2026-07-19T00:00:00+00:00"
                    ),
                },
            ],
        }

        jobs = list_historical_reanalysis_jobs(
            "user-test",
            status_filter="FAILED",
            limit=20,
        )

        self.assertEqual(
            len(jobs),
            1,
        )
        self.assertEqual(
            jobs[0]["status"],
            "FAILED",
        )

    def test_job_list_validation(
        self,
    ):
        with self.assertRaises(
            ValueError
        ):
            list_historical_reanalysis_jobs(
                "user-test",
                status_filter="INVALID",
            )

        with self.assertRaises(
            ValueError
        ):
            list_historical_reanalysis_jobs(
                "user-test",
                limit=0,
            )

        with self.assertRaises(
            ValueError
        ):
            list_historical_reanalysis_jobs(
                "user-test",
                limit=51,
            )

    def assert_page_transaction_uses_all_values(
        self,
        transact_write_items,
    ):
        transaction = (
            transact_write_items
            .call_args
            .kwargs["TransactItems"]
        )

        update = transaction[1]["Update"]

        expression = " ".join([
            update["UpdateExpression"],
            update["ConditionExpression"],
        ])

        used_values = set(
            re.findall(
                r":[A-Za-z][A-Za-z0-9]*",
                expression,
            )
        )

        provided_values = set(
            update[
                "ExpressionAttributeValues"
            ].keys()
        )

        self.assertEqual(
            provided_values,
            used_values,
        )

    @patch(
        "storage."
        "get_historical_reanalysis_job"
    )
    @patch(
        "storage.dynamodb_client."
        "transact_write_items"
    )
    def test_nonfinal_page_uses_all_values(
        self,
        transact_write_items,
        get_job,
    ):
        get_job.return_value = {
            "jobId": "reanalysis-test",
            "status": "RUNNING",
        }

        record_historical_reanalysis_page(
            "user-test",
            "reanalysis-test",
            page_id="a" * 32,
            results=[
                {
                    "outcome": "COMPLETED",
                },
            ],
            has_more=True,
            next_cursor="cursor-test",
        )

        self.assert_page_transaction_uses_all_values(
            transact_write_items
        )

        update = (
            transact_write_items
            .call_args
            .kwargs["TransactItems"][1]["Update"]
        )

        values = update[
            "ExpressionAttributeValues"
        ]

        self.assertIn(
            ":negativeProcessed",
            values,
        )
        self.assertNotIn(
            ":completed",
            values,
        )
        self.assertNotIn(
            ":zero",
            values,
        )

    @patch(
        "storage."
        "get_historical_reanalysis_job"
    )
    @patch(
        "storage.dynamodb_client."
        "transact_write_items"
    )
    def test_final_page_uses_all_values(
        self,
        transact_write_items,
        get_job,
    ):
        get_job.return_value = {
            "jobId": "reanalysis-test",
            "status": "COMPLETED",
        }

        record_historical_reanalysis_page(
            "user-test",
            "reanalysis-test",
            page_id="b" * 32,
            results=[
                {
                    "outcome": "COMPLETED",
                },
            ],
            has_more=False,
            next_cursor=None,
        )

        self.assert_page_transaction_uses_all_values(
            transact_write_items
        )

        update = (
            transact_write_items
            .call_args
            .kwargs["TransactItems"][1]["Update"]
        )

        values = update[
            "ExpressionAttributeValues"
        ]

        self.assertIn(
            ":completed",
            values,
        )
        self.assertIn(
            ":zero",
            values,
        )
        self.assertNotIn(
            ":negativeProcessed",
            values,
        )


if __name__ == "__main__":
    unittest.main()
