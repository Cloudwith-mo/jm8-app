import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "function"))


os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")

import account_export_store as store  # noqa: E402


class AccountExportStoreTests(unittest.TestCase):
    @patch.object(store, "get_export_job")
    @patch.object(store, "_get_coordination")
    def test_idempotency_replay_returns_same_job(self, coordination, get_job):
        coordination.return_value = {"exportId": "exp_20260828T121314Z_aaaaaaaaaaaaaaaa"}
        get_job.return_value = {"exportId": coordination.return_value["exportId"], "status": "QUEUED"}
        job, replayed = store.create_or_replay_export(
            user_id="user-a", request_token="550e8400-e29b-41d4-a716-446655440000",
            profile={}, transact_writer=MagicMock(),
        )
        self.assertTrue(replayed)
        self.assertEqual(job["exportId"], coordination.return_value["exportId"])

    @patch.object(store, "_get_coordination")
    def test_live_active_lock_is_enforced(self, coordination):
        coordination.side_effect = [None, {
            "status": "RUNNING", store.TTL_ATTRIBUTE: 9_999_999_999,
        }]
        with self.assertRaises(store.ActiveExportExists):
            store.create_or_replay_export(
                user_id="user-a", request_token="550e8400-e29b-41d4-a716-446655440000",
                profile={}, transact_writer=MagicMock(),
            )

    @patch.object(store, "_get_coordination")
    def test_expired_lock_is_recovered_transactionally(self, coordination):
        coordination.side_effect = [None, {
            "status": "RUNNING", store.TTL_ATTRIBUTE: 1,
        }]
        writer = MagicMock()
        job, replayed = store.create_or_replay_export(
            user_id="user-a", request_token="550e8400-e29b-41d4-a716-446655440000",
            profile={}, transact_writer=writer,
        )
        self.assertFalse(replayed)
        self.assertEqual(job["status"], "QUEUED")
        writer.assert_called_once()
        transaction = writer.call_args.kwargs["TransactItems"]
        self.assertEqual(len(transaction), 3)
        self.assertIn("#ttl <= :now", transaction[1]["Put"]["ConditionExpression"])

    @patch("builtins.print")
    @patch.object(store, "new_export_id", return_value="exp_secret-export-id")
    @patch.object(store, "_get_coordination", side_effect=[None, None])
    def test_unavailable_store_error_logs_only_safe_fields(
        self, coordination, new_export_id, log,
    ):
        error = ClientError({
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "secret-aws-error-message",
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 503,
                "RequestId": "aws-request-id-123",
            },
            "TransactionData": "secret-transaction-data",
        }, "TransactWriteItems")
        writer = MagicMock(side_effect=error)

        with self.assertRaises(store.ExportStoreUnavailable):
            store.create_or_replay_export(
                user_id="secret-user-id",
                request_token="secret-request-token",
                profile={"private": "secret-profile"},
                transact_writer=writer,
            )

        log.assert_called_once()
        record_text = log.call_args.args[0]
        record = json.loads(record_text)
        self.assertEqual(record, {
            "event": "AccountExportStoreFailure",
            "operation": "TransactWriteItems",
            "errorCode": "ProvisionedThroughputExceededException",
            "httpStatus": 503,
            "awsRequestId": "aws-request-id-123",
        })
        for secret in (
            "secret-aws-error-message",
            "secret-user-id",
            "secret-request-token",
            "secret-export-id",
            "secret-profile",
            "secret-transaction-data",
        ):
            self.assertNotIn(secret, record_text)

    @patch("builtins.print")
    @patch.object(store, "_get_coordination", side_effect=[None, None, None])
    def test_transaction_conflict_remains_active_export_and_is_not_logged(
        self, coordination, log,
    ):
        writer = MagicMock(side_effect=ClientError({
            "Error": {
                "Code": "TransactionCanceledException",
                "Message": "secret-conflict-message",
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 400,
                "RequestId": "conflict-request-id",
            },
        }, "TransactWriteItems"))

        with self.assertRaises(store.ActiveExportExists):
            store.create_or_replay_export(
                user_id="user-a",
                request_token="550e8400-e29b-41d4-a716-446655440000",
                profile={},
                transact_writer=writer,
            )

        log.assert_not_called()

    @patch("builtins.print")
    @patch.object(store, "get_export_job")
    @patch.object(store, "_get_coordination")
    def test_transaction_conflict_replay_is_preserved(
        self, coordination, get_job, log,
    ):
        replayed_job = {
            "exportId": "exp_20260828T121314Z_aaaaaaaaaaaaaaaa",
            "status": "QUEUED",
        }
        coordination.side_effect = [None, None, {"exportId": replayed_job["exportId"]}]
        get_job.return_value = replayed_job
        writer = MagicMock(side_effect=ClientError({
            "Error": {"Code": "ConditionalCheckFailedException", "Message": "conflict"},
        }, "TransactWriteItems"))

        job, replayed = store.create_or_replay_export(
            user_id="user-a",
            request_token="550e8400-e29b-41d4-a716-446655440000",
            profile={},
            transact_writer=writer,
        )

        self.assertEqual(job, replayed_job)
        self.assertTrue(replayed)
        log.assert_not_called()

    @patch.object(store, "release_active_lock")
    @patch.object(store, "update_export_status")
    def test_failure_transition_releases_lock(self, update, release):
        store.fail_export("user-a", "exp_20260828T121314Z_aaaaaaaaaaaaaaaa")
        self.assertEqual(update.call_args.args[2], "FAILED")
        release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
