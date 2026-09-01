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
        self.assertEqual(len(transaction), 4)
        audit = deserialize_item(transaction[0]["Put"]["Item"])
        lock = deserialize_item(transaction[1]["Put"]["Item"])
        idempotency = deserialize_item(transaction[2]["Put"]["Item"])
        subject_recovery = deserialize_item(transaction[3]["Put"]["Item"])
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
        self.assertEqual(subject_recovery["SK"], store.SUBJECT_RECOVERY_SK)
        self.assertEqual(
            subject_recovery["cognitoSubject"], "raw-cognito-subject"
        )
        self.assertIn(store.TTL_ATTRIBUTE, subject_recovery)
        serialized = json.dumps(transaction)
        self.assertNotIn("USER#", serialized)
        self.assertNotIn("secret-request-token-1234", serialized)
        self.assertNotIn("raw-cognito-subject", json.dumps(audit))
        self.assertNotIn("raw-cognito-subject", repr(lock))
        self.assertNotIn("raw-cognito-subject", repr(idempotency))

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

    @patch.object(store, "_get_item")
    def test_subject_recovery_is_internal_validated_and_tamper_evident(self, get_item):
        digest = store.subject_digest("raw-cognito-subject")
        get_item.return_value = {
            "PK": store.audit_pk(REQUEST_ID),
            "SK": store.SUBJECT_RECOVERY_SK,
            "requestId": REQUEST_ID,
            "subjectDigest": digest,
            "cognitoSubject": "raw-cognito-subject",
        }
        self.assertEqual(
            store.get_deletion_subject(REQUEST_ID), "raw-cognito-subject"
        )
        get_item.return_value["subjectDigest"] = store.subject_digest("other")
        self.assertIsNone(store.get_deletion_subject(REQUEST_ID))

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

    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    @patch.object(store, "_get_item")
    def test_durable_partial_deletion_cannot_be_superseded(self, get_item, utc_now):
        get_item.side_effect = [None, {
            "requestId": REQUEST_ID,
            "status": "IN_PROGRESS",
        }]

        with self.assertRaises(store.ActiveDeletionExists):
            store.create_or_replay_deletion(
                subject="subject-a",
                request_token="different-request-token",
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
    @patch.object(store, "_request_by_digest")
    def test_pre_destructive_failure_releases_coordination_and_retains_failed_ttl(
        self, request_by_digest, utc_now,
    ):
        request_by_digest.return_value = {
            "requestId": REQUEST_ID,
            "status": "FAILED",
            "failureCode": "DeletionWorkflowStartFailed",
            "retryable": True,
        }
        resource = SimpleNamespace(name="journalm8-test-main")
        writer = MagicMock()

        result = store.fail_deletion_request(
            subject="subject-a",
            request_id=REQUEST_ID,
            failure_code="DeletionWorkflowStartFailed",
            retryable=True,
            table_resource=resource,
            transact_writer=writer,
        )

        self.assertEqual(result["status"], "FAILED")
        transaction = writer.call_args.kwargs["TransactItems"]
        self.assertEqual(len(transaction), 4)
        audit_update = transaction[0]["Update"]
        self.assertIn("#ttl = :ttl", audit_update["UpdateExpression"])
        audit_values = deserialize_item(audit_update["ExpressionAttributeValues"])
        self.assertEqual(
            audit_values[":ttl"],
            int(FIXED_NOW.timestamp()) + store.FAILED_AUDIT_TTL_SECONDS,
        )
        self.assertTrue(all("Delete" in item for item in transaction[1:]))
        deleted_sort_keys = {
            deserialize_item(item["Delete"]["Key"])["SK"]
            for item in transaction[1:]
        }
        self.assertEqual(
            deleted_sort_keys,
            {
                store.ACTIVE_SK,
                store.BILLING_RECOVERY_SK,
                store.SUBJECT_RECOVERY_SK,
            },
        )

    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    @patch.object(store, "_request_by_digest")
    def test_partial_failure_retains_lock_and_recovery_without_ttl(
        self, request_by_digest, utc_now,
    ):
        request_by_digest.return_value = {
            "requestId": REQUEST_ID,
            "status": "FAILED",
            "failureCode": "S3DeletionUnavailable",
            "retryable": True,
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        resource = SimpleNamespace(name="journalm8-test-main")
        writer = MagicMock()

        store.fail_deletion_request(
            subject="subject-a",
            request_id=REQUEST_ID,
            failure_code="S3DeletionUnavailable",
            retryable=True,
            phase="DELETE_RAW_OBJECTS",
            destructive_started=True,
            table_resource=resource,
            transact_writer=writer,
        )

        transaction = writer.call_args.kwargs["TransactItems"]
        self.assertEqual(len(transaction), 4)
        self.assertTrue(all("Update" in item for item in transaction))
        self.assertTrue(all(
            "REMOVE #ttl" in item["Update"]["UpdateExpression"]
            for item in transaction
        ))
        serialized = json.dumps(transaction)
        self.assertNotIn(f'"{store.TTL_ATTRIBUTE}": {{"N"', serialized)
        lock_values = deserialize_item(
            transaction[1]["Update"]["ExpressionAttributeValues"]
        )
        self.assertEqual(lock_values[":active"], "IN_PROGRESS")

    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    @patch.object(store, "_request_by_digest")
    def test_destructive_boundary_atomically_removes_all_coordination_ttls(
        self, request_by_digest, utc_now,
    ):
        request_by_digest.return_value = {
            "requestId": REQUEST_ID,
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        writer = MagicMock()

        result = store.begin_destructive_deletion(
            subject="subject-a",
            request_id=REQUEST_ID,
            table_resource=SimpleNamespace(name="journalm8-test-main"),
            transact_writer=writer,
        )

        self.assertEqual(result["destructiveStartedAt"], "2026-08-30T12:00:00Z")
        transaction = writer.call_args.kwargs["TransactItems"]
        self.assertEqual(len(transaction), 4)
        self.assertTrue(all("Update" in item for item in transaction))
        self.assertIn(
            "destructiveStartedAt = if_not_exists",
            transaction[0]["Update"]["UpdateExpression"],
        )
        self.assertTrue(all(
            "REMOVE #ttl" in item["Update"]["UpdateExpression"]
            for item in transaction
        ))
        self.assertEqual(
            {
                deserialize_item(item["Update"]["Key"])["SK"]
                for item in transaction[1:]
            },
            {
                store.ACTIVE_SK,
                store.BILLING_RECOVERY_SK,
                store.SUBJECT_RECOVERY_SK,
            },
        )

    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    @patch.object(store, "_request_by_digest")
    def test_raw_stripe_identifier_is_temporary_recovery_data_only(
        self, request_by_digest, utc_now,
    ):
        resource = MagicMock()
        resource.name = "journalm8-test-main"

        store.write_billing_recovery(
            subject="subject-a",
            request_id=REQUEST_ID,
            customer_id="cus_private_recovery",
            livemode=False,
            table_resource=resource,
        )

        recovery = resource.put_item.call_args.kwargs["Item"]
        self.assertEqual(recovery["stripeCustomerId"], "cus_private_recovery")
        self.assertIn(store.TTL_ATTRIBUTE, recovery)
        request_by_digest.return_value = {
            "requestId": REQUEST_ID,
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        writer = MagicMock()

        store.begin_destructive_deletion(
            subject="subject-a",
            request_id=REQUEST_ID,
            table_resource=resource,
            transact_writer=writer,
        )

        boundary = json.dumps(writer.call_args.kwargs["TransactItems"])
        self.assertNotIn("cus_private_recovery", boundary)
        self.assertNotIn("stripeCustomerId", boundary)

    @patch.object(store, "_get_item")
    def test_billing_recovery_remains_discoverable_after_user_partition_is_gone(
        self, get_item,
    ):
        recovery = {
            "PK": store.subject_pk(store.subject_digest("subject-a")),
            "SK": store.BILLING_RECOVERY_SK,
            "requestId": REQUEST_ID,
            "stripeCustomerId": "cus_private_recovery",
            "livemode": False,
        }
        get_item.return_value = recovery

        self.assertEqual(
            store.get_billing_recovery(
                subject="subject-a",
                request_id=REQUEST_ID,
                table_resource=SimpleNamespace(name="journalm8-test-main"),
            ),
            recovery,
        )
        self.assertNotIn(store.TTL_ATTRIBUTE, recovery)

    @patch.object(
        store,
        "utc_now",
        return_value=datetime(2035, 1, 1, tzinfo=timezone.utc),
    )
    @patch.object(store, "_get_item")
    def test_durable_active_lock_survives_far_beyond_former_ttl(
        self, get_item, utc_now,
    ):
        get_item.return_value = {
            "requestId": REQUEST_ID,
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2025-01-01T00:00:00Z",
        }

        self.assertTrue(store.has_active_deletion("subject-a"))

    @patch.object(store, "_request_by_digest")
    def test_same_request_resumes_long_after_partial_failure_without_ttl(
        self, request_by_digest,
    ):
        failed = {
            "requestId": REQUEST_ID,
            "status": "FAILED",
            "destructiveStartedAt": "2025-01-01T00:00:00Z",
            "failureCode": "S3DeletionUnavailable",
        }
        resumed = {
            "requestId": REQUEST_ID,
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2025-01-01T00:00:00Z",
        }
        request_by_digest.side_effect = [failed, resumed]
        writer = MagicMock()

        result = store.start_deletion_request(
            subject="subject-a",
            request_id=REQUEST_ID,
            table_resource=SimpleNamespace(name="journalm8-test-main"),
            transact_writer=writer,
        )

        self.assertEqual(result, resumed)
        transaction = writer.call_args.kwargs["TransactItems"]
        self.assertEqual(len(transaction), 4)
        self.assertTrue(all(
            "REMOVE" in item["Update"]["UpdateExpression"]
            and "#ttl" in item["Update"]["UpdateExpression"]
            for item in transaction
        ))

    @patch.object(store, "utc_now", return_value=FIXED_NOW)
    @patch.object(store, "_request_by_digest")
    def test_completion_requires_verification_then_removes_lock_and_recovery(
        self, request_by_digest, utc_now,
    ):
        in_progress = {
            "requestId": REQUEST_ID,
            "status": "IN_PROGRESS",
            "verifiedAt": "2026-08-30T12:00:00Z",
        }
        completed = {
            "requestId": REQUEST_ID,
            "status": "COMPLETED",
            "completedAt": "2026-08-30T12:00:00Z",
        }
        request_by_digest.side_effect = [in_progress, completed]
        resource = SimpleNamespace(name="journalm8-test-main")
        writer = MagicMock()

        result = store.complete_deletion_request(
            subject="subject-a",
            request_id=REQUEST_ID,
            table_resource=resource,
            transact_writer=writer,
        )

        self.assertEqual(result["status"], "COMPLETED")
        transaction = writer.call_args.kwargs["TransactItems"]
        self.assertEqual(len(transaction), 4)
        self.assertIn(
            "attribute_exists(verifiedAt)",
            transaction[0]["Update"]["ConditionExpression"],
        )
        deleted_sort_keys = {
            deserialize_item(item["Delete"]["Key"])["SK"]
            for item in transaction[1:]
        }
        self.assertEqual(
            deleted_sort_keys,
            {
                store.ACTIVE_SK,
                store.BILLING_RECOVERY_SK,
                store.SUBJECT_RECOVERY_SK,
            },
        )
        serialized = json.dumps(transaction)
        self.assertNotIn("cus_private_recovery", serialized)
        completion_values = deserialize_item(
            transaction[0]["Update"]["ExpressionAttributeValues"]
        )
        self.assertEqual(
            set(completion_values),
            {":completed", ":completed_at", ":subject"},
        )

    @patch("builtins.print")
    @patch.object(store, "_request_by_digest")
    def test_completion_conditional_conflict_is_nonretryable_invariant(
        self, request_by_digest, log,
    ):
        request_by_digest.return_value = {
            "requestId": REQUEST_ID,
            "status": "IN_PROGRESS",
            "verifiedAt": "2026-08-30T12:00:00Z",
        }
        writer = MagicMock(side_effect=ClientError({
            "Error": {"Code": "TransactionCanceledException", "Message": "private"},
            "CancellationReasons": [
                {"Code": "None"},
                {"Code": "ConditionalCheckFailed", "Message": "private"},
                {"Code": "None"},
            ],
        }, "TransactWriteItems"))
        with self.assertRaises(store.DeletionStoreInvariant):
            store.complete_deletion_request(
                subject="subject-a",
                request_id=REQUEST_ID,
                table_resource=SimpleNamespace(name="journalm8-test-main"),
                transact_writer=writer,
            )
        log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
