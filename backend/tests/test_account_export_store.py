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

    @patch.object(store, "release_active_lock")
    @patch.object(store, "update_export_status")
    def test_failure_transition_releases_lock(self, update, release):
        store.fail_export("user-a", "exp_20260828T121314Z_aaaaaaaaaaaaaaaa")
        self.assertEqual(update.call_args.args[2], "FAILED")
        release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
