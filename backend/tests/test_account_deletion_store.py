import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "function"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")

import account_deletion_store as store  # noqa: E402


REQUEST_ID = "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FIXED_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def deserialize_item(item):
    deserializer = TypeDeserializer()
    return {
        key: deserializer.deserialize(value)
        for key, value in item.items()
    }


class AccountDeletionStoreTests(unittest.TestCase):
    @patch.object(store, "new_deletion_request_id", return_value=REQUEST_ID)
    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    @patch.object(store, "_get_item", side_effect=[None, None])
    def test_transaction_uses_external_privacy_safe_coordination_partitions(
        self, get_item, utc_now, new_request_id,
    ):
        writer = MagicMock()
        resource = SimpleNamespace(name="journalm8-test-main")

        request, replayed = store.create_or_replay_deletion(
            subject="raw-cognito-subject",
            request_token="secret-request-token-1234",
            transact_writer=writer,
            table_resource=resource,
        )

        self.assertFalse(replayed)
        self.assertEqual(request["requestId"], REQUEST_ID)
        transaction = writer.call_args.kwargs["TransactItems"]
        self.assertEqual(len(transaction), 3)
        audit = deserialize_item(transaction[0]["Put"]["Item"])
        lock = deserialize_item(transaction[1]["Put"]["Item"])
        idempotency = deserialize_item(transaction[2]["Put"]["Item"])
        self.assertEqual(audit["PK"], f"ACCOUNT_DELETION#{REQUEST_ID}")
        self.assertEqual(audit["SK"], "REQUEST")
        self.assertEqual(
            set(audit),
            {"PK", "SK", "requestId", "subjectDigest", "status", "requestedAt"},
        )
        self.assertTrue(lock["PK"].startswith("ACCOUNT_DELETION_SUBJECT#"))
        self.assertEqual(lock["SK"], "ACTIVE")
        self.assertTrue(idempotency["SK"].startswith("REQUEST_TOKEN#"))
        self.assertIn(store.TTL_ATTRIBUTE, idempotency)
        serialized = json.dumps(transaction)
        self.assertNotIn("USER#", serialized)
        self.assertNotIn("raw-cognito-subject", serialized)
        self.assertNotIn("secret-request-token-1234", serialized)

    @patch.object(store, "_get_item")
    def test_request_token_replay_returns_same_audit_without_writing(self, get_item):
        audit = {
            "requestId": REQUEST_ID,
            "subjectDigest": store.subject_digest("subject-a"),
            "status": "REQUESTED",
            "requestedAt": "2026-08-30T12:00:00Z",
        }
        get_item.side_effect = [{"requestId": REQUEST_ID}, audit]
        writer = MagicMock()

        request, replayed = store.create_or_replay_deletion(
            subject="subject-a",
            request_token="request-token-aaaaaaaa",
            transact_writer=writer,
            table_resource=SimpleNamespace(name="journalm8-test-main"),
        )

        self.assertTrue(replayed)
        self.assertEqual(request, audit)
        writer.assert_not_called()

    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    @patch.object(store, "_get_item")
    def test_one_active_deletion_is_enforced(self, get_item, utc_now):
        get_item.side_effect = [None, {
            "requestId": REQUEST_ID,
            "status": "IN_PROGRESS",
            store.TTL_ATTRIBUTE: int(FIXED_NOW.timestamp()) + 60,
        }]
        with self.assertRaises(store.ActiveDeletionExists):
            store.create_or_replay_deletion(
                subject="subject-a",
                request_token="request-token-aaaaaaaa",
                transact_writer=MagicMock(),
                table_resource=SimpleNamespace(name="journalm8-test-main"),
            )

    @patch.object(store, "_get_item")
    def test_request_ownership_isolated_by_subject_digest(self, get_item):
        get_item.return_value = {
            "requestId": REQUEST_ID,
            "subjectDigest": store.subject_digest("subject-a"),
            "status": "REQUESTED",
        }
        self.assertIsNone(store.get_deletion_request("subject-b", REQUEST_ID))
        self.assertIsNotNone(store.get_deletion_request("subject-a", REQUEST_ID))

    @patch("builtins.print")
    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    @patch.object(store, "_get_item", side_effect=[None, None])
    def test_unavailable_transaction_logs_only_safe_aws_fields(
        self, get_item, utc_now, log,
    ):
        writer = MagicMock(side_effect=ClientError({
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "secret aws exception message",
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 503,
                "RequestId": "aws-request-id-123",
            },
        }, "TransactWriteItems"))

        with self.assertRaises(store.DeletionStoreUnavailable):
            store.create_or_replay_deletion(
                subject="secret-subject",
                request_token="secret-request-token-1234",
                transact_writer=writer,
                table_resource=SimpleNamespace(name="journalm8-test-main"),
            )

        record_text = log.call_args.args[0]
        self.assertEqual(json.loads(record_text), {
            "event": "AccountDeletionStoreFailure",
            "operation": "TransactWriteItems",
            "errorCode": "ProvisionedThroughputExceededException",
            "httpStatus": 503,
            "awsRequestId": "aws-request-id-123",
        })
        for secret in (
            "secret aws exception message",
            "secret-subject",
            "secret-request-token-1234",
        ):
            self.assertNotIn(secret, record_text)

    @patch("builtins.print")
    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    @patch.object(store, "_get_item", side_effect=[None, None, None])
    def test_expected_transaction_conflict_maps_without_failure_log(
        self, get_item, utc_now, log,
    ):
        writer = MagicMock(side_effect=ClientError({
            "Error": {
                "Code": "TransactionCanceledException",
                "Message": "conditional conflict",
            },
        }, "TransactWriteItems"))

        with self.assertRaises(store.ActiveDeletionExists):
            store.create_or_replay_deletion(
                subject="subject-a",
                request_token="request-token-aaaaaaaa",
                transact_writer=writer,
                table_resource=SimpleNamespace(name="journalm8-test-main"),
            )

        log.assert_not_called()

    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    def test_failed_coordination_uses_updates_and_failed_ttl(self, utc_now):
        resource = MagicMock()
        resource.update_item.side_effect = [
            {"Attributes": {
                "requestId": REQUEST_ID,
                "status": "FAILED",
                "failureCode": "DeletionWorkflowStartFailed",
                "retryable": True,
            }},
            {},
        ]

        result = store.fail_deletion_request(
            subject="subject-a",
            request_id=REQUEST_ID,
            failure_code="DeletionWorkflowStartFailed",
            retryable=True,
            table_resource=resource,
        )

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(resource.update_item.call_count, 2)
        self.assertIn(
            store.TTL_ATTRIBUTE,
            resource.update_item.call_args_list[0].kwargs[
                "ExpressionAttributeNames"
            ].values(),
        )
        self.assertFalse(hasattr(resource, "delete_item") and resource.delete_item.called)


if __name__ == "__main__":
    unittest.main()
