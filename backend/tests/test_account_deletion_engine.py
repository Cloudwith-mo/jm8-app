import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "function"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
_ORIGINAL_ENTRY_CHUNKS_TABLE_NAME = os.environ.get("ENTRY_CHUNKS_TABLE_NAME")
os.environ.setdefault("ENTRY_CHUNKS_TABLE_NAME", "journalm8-test-entry-chunks")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw")
os.environ.setdefault("EXPORT_BUCKET", "journalm8-test-export")

import account_deletion_cognito as cognito  # noqa: E402
import account_deletion_dynamodb as deletion_dynamodb  # noqa: E402
import account_deletion_quiescence as quiescence  # noqa: E402
import account_deletion_s3 as deletion_s3  # noqa: E402
import account_deletion_stripe as deletion_stripe  # noqa: E402
import account_deletion_worker as worker  # noqa: E402

if _ORIGINAL_ENTRY_CHUNKS_TABLE_NAME is None:
    os.environ.pop("ENTRY_CHUNKS_TABLE_NAME", None)
else:
    os.environ["ENTRY_CHUNKS_TABLE_NAME"] = _ORIGINAL_ENTRY_CHUNKS_TABLE_NAME


REQUEST_ID = "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SUBJECT = "subject-a"


def client_error(code, operation="Operation"):
    return ClientError({"Error": {"Code": code, "Message": "private"}}, operation)


class AccountDeletionAdapterTests(unittest.TestCase):
    def test_s3_paginates_versions_markers_and_limits_delete_batches(self):
        versions = [
            {"Key": f"users/{SUBJECT}/uploads/{index}", "VersionId": f"v{index}"}
            for index in range(1_001)
        ]
        marker = {
            "Key": f"users/{SUBJECT}/uploads/deleted",
            "VersionId": "marker-1",
        }
        client = MagicMock()
        client.list_object_versions.side_effect = [
            {
                "Versions": versions,
                "DeleteMarkers": [marker],
                "IsTruncated": True,
                "NextKeyMarker": "next-key",
                "NextVersionIdMarker": "next-version",
            },
            {"Versions": [], "DeleteMarkers": [], "IsTruncated": False},
        ]
        client.list_objects_v2.return_value = {
            "Contents": [{"Key": f"users/{SUBJECT}/uploads/current"}],
            "IsTruncated": False,
        }
        client.delete_objects.return_value = {}

        deletion_s3.delete_prefix(
            client,
            bucket="raw-bucket",
            prefix=deletion_s3.user_prefix(SUBJECT, export=False),
        )

        self.assertEqual(client.list_object_versions.call_count, 2)
        second_page = client.list_object_versions.call_args_list[1].kwargs
        self.assertEqual(second_page["KeyMarker"], "next-key")
        self.assertEqual(second_page["VersionIdMarker"], "next-version")
        batches = [
            item.kwargs["Delete"]["Objects"]
            for item in client.delete_objects.call_args_list
        ]
        self.assertTrue(all(len(batch) <= 1_000 for batch in batches))
        self.assertIn(marker, [identifier for batch in batches for identifier in batch])

    def test_s3_already_empty_and_strict_prefix_generation(self):
        client = MagicMock()
        client.list_object_versions.return_value = {"IsTruncated": False}
        client.list_objects_v2.return_value = {"IsTruncated": False}
        deletion_s3.delete_prefix(
            client,
            bucket="export-bucket",
            prefix=deletion_s3.user_prefix(SUBJECT, export=True),
        )
        client.delete_objects.assert_not_called()
        with self.assertRaises(deletion_s3.DeletionS3Error):
            deletion_s3.user_prefix("../another-user", export=False)

    def test_dynamodb_query_pagination_batch_limit_and_unprocessed_retry(self):
        table = MagicMock()
        first = [{"PK": f"USER#{SUBJECT}", "SK": f"A#{i}"} for i in range(13)]
        second = [{"PK": f"USER#{SUBJECT}", "SK": f"B#{i}"} for i in range(17)]
        table.query.side_effect = [
            {"Items": first, "LastEvaluatedKey": first[-1]},
            {"Items": second},
            {"Items": []},
        ]
        client = MagicMock()
        unprocessed = [{"DeleteRequest": {"Key": {"PK": {"S": "USER#subject-a"}, "SK": {"S": "A#0"}}}}]
        client.batch_write_item.side_effect = [
            {"UnprocessedItems": {"journalm8-test-main": unprocessed}},
            {"UnprocessedItems": {}},
            {"UnprocessedItems": {}},
        ]
        sleeper = MagicMock()

        deletion_dynamodb.delete_user_partition(
            table,
            client,
            table_name="journalm8-test-main",
            subject=SUBJECT,
            sleeper=sleeper,
        )

        self.assertEqual(table.query.call_count, 3)
        self.assertTrue(all(item.kwargs["ConsistentRead"] for item in table.query.call_args_list))
        sizes = [
            len(item.kwargs["RequestItems"]["journalm8-test-main"])
            for item in client.batch_write_item.call_args_list
        ]
        self.assertEqual(sizes, [25, 1, 5])
        sleeper.assert_called_once()

    def test_quiescence_paginates_and_stops_only_actual_owned_execution_arn(self):
        table = MagicMock()
        table.query.side_effect = [
            {"Items": [{"PK": "USER#subject-a", "SK": "OCR#1"}], "LastEvaluatedKey": {"PK": "USER#subject-a", "SK": "OCR#1"}},
            {"Items": []},
        ]
        states = MagicMock()
        states.list_executions.side_effect = [
            {"executions": [{"executionArn": "arn:actual:owned"}], "nextToken": "next"},
            {"executions": [{"executionArn": "arn:actual:other"}]},
            {"executions": []},
            {"executions": []},
        ]
        states.describe_execution.side_effect = [
            {"input": json.dumps({"userId": SUBJECT})},
            {"input": json.dumps({"userId": "other-subject"})},
        ]

        result = quiescence.quiesce_user_work(
            subject=SUBJECT,
            deletion_started_at="2026-08-30T12:00:00Z",
            table_resource=table,
            stepfunctions_client=states,
            workflow_arns={
                "ocr": "arn:workflow:ocr",
                "reanalysis": "arn:workflow:reanalysis",
                "export": "arn:workflow:export",
            },
            now=1_777_809_600,
        )

        self.assertFalse(result["quiesced"])
        states.stop_execution.assert_called_once()
        self.assertEqual(
            states.stop_execution.call_args.kwargs["executionArn"],
            "arn:actual:owned",
        )
        self.assertNotIn("subject-a", states.stop_execution.call_args.kwargs["cause"])

    def test_cognito_signout_delete_and_absence_verification_are_ordered(self):
        client = MagicMock()
        client.list_users.return_value = {"Users": [{"Username": "opaque-username"}]}
        client.admin_get_user.side_effect = client_error(
            "UserNotFoundException", "AdminGetUser"
        )

        cognito.delete_identity(
            client,
            user_pool_id="us-east-1_exactPool",
            subject=SUBJECT,
        )

        names = [entry[0] for entry in client.method_calls]
        self.assertLess(names.index("admin_user_global_sign_out"), names.index("admin_delete_user"))
        self.assertLess(names.index("admin_delete_user"), names.index("admin_get_user"))
        for method in (
            client.list_users,
            client.admin_user_global_sign_out,
            client.admin_delete_user,
            client.admin_get_user,
        ):
            self.assertEqual(method.call_args.kwargs["UserPoolId"], "us-east-1_exactPool")

    def test_cognito_lookup_paginates_and_rejects_multiple_matches(self):
        client = MagicMock()
        client.list_users.side_effect = [
            {"Users": [{"Username": "first"}], "PaginationToken": "next"},
            {"Users": [{"Username": "second"}]},
        ]
        with self.assertRaises(cognito.DeletionCognitoError) as raised:
            cognito.resolve_username(
                client,
                user_pool_id="us-east-1_exactPool",
                subject=SUBJECT,
            )
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(client.list_users.call_count, 2)

    def test_stripe_recovery_precedes_cancellation_and_missing_is_idempotent(self):
        events = []
        gateway = SimpleNamespace(
            livemode=False,
            find_blocking_subscription=lambda customer: events.append(("find", customer)) or {"id": "sub_abcdef"},
            stop_subscription_renewal=lambda subscription: events.append(("cancel", subscription)),
        )
        result = deletion_stripe.prepare_and_cancel_subscription(
            SUBJECT,
            mapping_reader=lambda _subject: {
                "stripeCustomerId": "cus_abcdef",
                "livemode": False,
            },
            gateway=gateway,
            recovery_writer=lambda **values: events.append(("recover", values)),
            destructive_marker=lambda: events.append(("boundary", None)),
        )
        self.assertEqual(events[0][0], "recover")
        self.assertEqual(events[1][0], "boundary")
        self.assertEqual(events[-1], ("cancel", "sub_abcdef"))
        self.assertEqual(result["SK"], "USER")

        missing_gateway = MagicMock()
        missing_recovery = MagicMock()
        missing_boundary = MagicMock()
        missing = deletion_stripe.prepare_and_cancel_subscription(
            SUBJECT,
            mapping_reader=lambda _subject: None,
            gateway=missing_gateway,
            recovery_writer=missing_recovery,
            destructive_marker=missing_boundary,
        )
        self.assertIsNone(missing)
        missing_recovery.assert_called_once_with(customer_id=None, livemode=None)
        missing_boundary.assert_called_once_with()
        missing_gateway.find_blocking_subscription.assert_not_called()


class AccountDeletionWorkerContractTests(unittest.TestCase):
    @patch("builtins.print")
    @patch.object(worker, "run_action")
    def test_completion_log_excludes_internal_billing_recovery(
        self, run_action, log,
    ):
        run_action.return_value = {
            "action": "COMPLETE",
            "status": "COMPLETED",
            "stripeCustomerId": "cus_private_recovery",
        }

        result = worker.lambda_handler({
            "action": "COMPLETE",
            "requestId": REQUEST_ID,
            "userId": SUBJECT,
        }, None)

        self.assertEqual(result["status"], "COMPLETED")
        record = json.loads(log.call_args.args[0])
        self.assertEqual(record, {
            "event": "AccountDeletionActionCompleted",
            "action": "COMPLETE",
            "status": "COMPLETED",
            "durablePartialFailure": False,
        })
        self.assertNotIn("cus_private_recovery", json.dumps(record))

    @patch("builtins.print")
    @patch.object(worker, "run_action")
    def test_partial_failure_log_is_boolean_only(self, run_action, log):
        run_action.return_value = {
            "action": "FAIL",
            "status": "FAILED",
            "durablePartialFailure": True,
            "stripeCustomerId": "cus_private_recovery",
            "userId": SUBJECT,
        }
        worker.lambda_handler({}, None)
        record = json.loads(log.call_args.args[0])
        self.assertEqual(record, {
            "event": "AccountDeletionActionCompleted",
            "action": "FAIL",
            "status": "FAILED",
            "durablePartialFailure": True,
        })
        self.assertNotIn("cus_private_recovery", json.dumps(record))
        self.assertNotIn(SUBJECT, json.dumps(record))

    def test_actions_and_workflow_order_are_exact(self):
        expected = (
            "START", "QUIESCE", "CANCEL_SUBSCRIPTION", "DELETE_RAW_OBJECTS",
            "DELETE_EXPORT_OBJECTS", "DELETE_APPLICATION_DATA",
            "DELETE_SEMANTIC_MEMORY", "DELETE_COGNITO_IDENTITY", "VERIFY",
            "COMPLETE", "FAIL",
        )
        self.assertEqual(worker.ACTIONS, expected)
        definition = json.loads((ROOT / "workflows" / "account-deletion.asl.json").read_text())
        next_state = definition["StartAt"]
        observed = []
        while next_state != "COMPLETE":
            observed.append(next_state)
            if next_state == "QUIESCE":
                next_state = "CANCEL_SUBSCRIPTION"
            else:
                next_state = definition["States"][next_state]["Next"]
        observed.append("COMPLETE")
        self.assertEqual(observed, list(expected[:-1]))
        self.assertEqual(definition["TimeoutSeconds"], 7200)

    @patch.object(worker, "get_deletion_audit")
    def test_every_action_is_idempotent_after_completion(self, get_audit):
        get_audit.return_value = {
            "requestId": REQUEST_ID,
            "status": "COMPLETED",
        }
        dependencies = worker.Dependencies(
            table_resource=MagicMock(),
            dynamodb=MagicMock(),
            s3_client=MagicMock(),
            stepfunctions_client=MagicMock(),
            cognito_client=MagicMock(),
        )
        for action in worker.ACTIONS:
            with self.subTest(action=action):
                result = worker.run_action({
                    "action": action,
                    "requestId": REQUEST_ID,
                    "userId": SUBJECT,
                }, dependencies)
                self.assertTrue(result["idempotent"])
                self.assertEqual(result["status"], "COMPLETED")

    def test_workflow_has_bounded_retry_and_fail_path_for_every_phase(self):
        definition = json.loads((ROOT / "workflows" / "account-deletion.asl.json").read_text())
        phases = worker.ACTIONS[:-1]
        for phase in phases:
            state = definition["States"][phase]
            retry = state["Retry"][0]
            self.assertLessEqual(retry["MaxAttempts"], 4)
            self.assertGreater(retry["BackoffRate"], 1)
            self.assertNotIn("AccountDeletionInvariantError", retry["ErrorEquals"])
            self.assertEqual(state["Catch"][0]["Next"], f"Failure{phase}")
            failure = definition["States"][f"Failure{phase}"]
            self.assertEqual(failure["Parameters"]["action"], "FAIL")
            self.assertEqual(failure["Parameters"]["failedPhase"], phase)
            self.assertNotIn("userId.$", state.get("Parameters", {}))
            self.assertNotIn("userId.$", failure["Parameters"])

    @patch.object(worker, "mark_deletion_checkpoint")
    @patch.object(worker, "prefix_is_empty", return_value=False)
    @patch.object(worker, "get_billing_recovery", return_value=None)
    @patch.object(worker, "get_deletion_subject", return_value=SUBJECT)
    @patch.object(worker, "get_deletion_audit")
    def test_verification_failure_prevents_completion(
        self, get_audit, get_subject, recovery, prefix_empty, checkpoint,
    ):
        get_audit.return_value = {
            "requestId": REQUEST_ID,
            "subjectDigest": worker.subject_digest(SUBJECT),
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        with patch.object(
            worker, "semantic_deletion_guard_exists", return_value=True
        ), patch.object(worker, "delete_user_memory"), patch.object(
            worker, "_semantic_partition_is_empty", return_value=True
        ), self.assertRaises(worker.AccountDeletionRetryableError):
            worker.run_action(
                {
                    "action": "VERIFY",
                    "requestId": REQUEST_ID,
                    "userId": SUBJECT,
                },
                worker.Dependencies(),
            )
        checkpoint.assert_not_called()

    @patch.object(worker, "delete_user_partition")
    @patch.object(worker, "get_billing_recovery")
    @patch.object(worker, "get_deletion_subject", return_value=SUBJECT)
    @patch.object(worker, "get_deletion_audit")
    def test_reverse_lookup_recovery_survives_partition_delete_failure_window(
        self, get_audit, get_subject, recovery, delete_partition,
    ):
        get_audit.return_value = {
            "requestId": REQUEST_ID,
            "subjectDigest": worker.subject_digest(SUBJECT),
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        recovery.return_value = {
            "requestId": REQUEST_ID,
            "stripeCustomerId": "cus_abcdef",
            "livemode": False,
        }
        table = MagicMock()
        table.delete_item.side_effect = [
            client_error("InternalServerError", "DeleteItem"),
            {},
        ]
        dependencies = worker.Dependencies(
            table_resource=table,
            dynamodb=MagicMock(),
            table_name="journalm8-test-main",
        )
        event = {
            "action": "DELETE_APPLICATION_DATA",
            "requestId": REQUEST_ID,
            "userId": SUBJECT,
        }
        with self.assertRaises(worker.AccountDeletionRetryableError):
            worker.run_action(event, dependencies)
        result = worker.run_action(event, dependencies)
        self.assertEqual(result["status"], "IN_PROGRESS")
        self.assertEqual(delete_partition.call_count, 2)
        self.assertEqual(table.delete_item.call_count, 2)
        deleted_key = table.delete_item.call_args.kwargs["Key"]
        self.assertEqual(deleted_key["SK"], "USER")

    @patch.object(worker, "semantic_deletion_guard_exists", return_value=True)
    @patch.object(worker, "delete_user_memory")
    @patch.object(worker, "put_semantic_deletion_guard")
    @patch.object(worker, "get_deletion_subject", return_value=SUBJECT)
    @patch.object(worker, "get_deletion_audit")
    def test_semantic_deletion_guards_before_exact_partition_delete(
        self, get_audit, get_subject, put_guard, delete_memory, guard_exists,
    ):
        get_audit.return_value = {
            "requestId": REQUEST_ID,
            "subjectDigest": worker.subject_digest(SUBJECT),
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        semantic_table = MagicMock()
        semantic_table.name = "journalm8-test-entry-chunks"
        operations: list[str] = []
        put_guard.side_effect = lambda *_args: operations.append("guard")
        delete_memory.side_effect = lambda *_args: operations.append("delete")
        guard_exists.side_effect = lambda *_args: operations.append("verify") or True
        deps = worker.Dependencies(
            entry_chunks_table=semantic_table,
            entry_chunks_table_name="journalm8-test-entry-chunks",
        )

        result = worker.run_action({
            "action": "DELETE_SEMANTIC_MEMORY",
            "requestId": REQUEST_ID,
        }, deps)

        self.assertEqual(result["status"], "IN_PROGRESS")
        self.assertEqual(operations, ["guard", "delete", "verify"])
        put_guard.assert_called_once_with(semantic_table, SUBJECT)
        delete_memory.assert_called_once_with(semantic_table, SUBJECT)

    @patch.object(worker, "semantic_deletion_guard_exists", return_value=True)
    @patch.object(worker, "delete_user_memory")
    @patch.object(worker, "put_semantic_deletion_guard")
    @patch.object(worker, "get_deletion_subject", return_value=SUBJECT)
    @patch.object(worker, "get_deletion_audit")
    def test_semantic_deletion_retries_safely_after_post_guard_failure(
        self, get_audit, get_subject, put_guard, delete_memory, guard_exists,
    ):
        get_audit.return_value = {
            "requestId": REQUEST_ID,
            "subjectDigest": worker.subject_digest(SUBJECT),
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        delete_memory.side_effect = [
            worker.SemanticMemoryStoreError("privacy-safe failure"),
            0,
        ]
        semantic_table = MagicMock()
        semantic_table.name = "journalm8-test-entry-chunks"
        deps = worker.Dependencies(
            entry_chunks_table=semantic_table,
            entry_chunks_table_name="journalm8-test-entry-chunks",
        )
        event = {"action": "DELETE_SEMANTIC_MEMORY", "requestId": REQUEST_ID}

        with self.assertRaises(worker.AccountDeletionRetryableError):
            worker.run_action(event, deps)
        result = worker.run_action(event, deps)

        self.assertEqual(result["status"], "IN_PROGRESS")
        self.assertEqual(put_guard.call_count, 2)
        self.assertEqual(delete_memory.call_count, 2)
        guard_exists.assert_called_once_with(semantic_table, SUBJECT)

    @patch.object(worker, "mark_deletion_checkpoint")
    @patch.object(worker, "identity_is_absent", return_value=True)
    @patch.object(worker, "_reverse_lookup_absent", return_value=True)
    @patch.object(worker, "partition_is_empty", return_value=True)
    @patch.object(worker, "prefix_is_empty", return_value=True)
    @patch.object(worker, "get_billing_recovery", return_value=None)
    @patch.object(worker, "get_deletion_subject", return_value=SUBJECT)
    @patch.object(worker, "get_deletion_audit")
    def test_verify_requires_empty_semantic_partition_and_valid_guard(
        self, get_audit, get_subject, recovery, prefix_empty, main_empty,
        reverse_absent, identity_absent, checkpoint,
    ):
        get_audit.return_value = {
            "requestId": REQUEST_ID,
            "subjectDigest": worker.subject_digest(SUBJECT),
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        semantic_table = MagicMock()
        semantic_table.name = "journalm8-test-entry-chunks"
        deps = worker.Dependencies(
            entry_chunks_table=semantic_table,
            entry_chunks_table_name="journalm8-test-entry-chunks",
        )
        event = {"action": "VERIFY", "requestId": REQUEST_ID}

        operations: list[str] = []
        residue = [{"PK": f"USER#{SUBJECT}"}]

        def repair(_table: object, user_id: str) -> int:
            operations.append("repair")
            self.assertEqual(user_id, SUBJECT)
            residue.clear()
            return 1

        def query(**_kwargs: object) -> dict[str, object]:
            operations.append("verify-empty")
            return {"Items": list(residue)}

        semantic_table.query.side_effect = query
        with patch.object(
            worker, "semantic_deletion_guard_exists", return_value=True
        ), patch.object(
            worker, "delete_user_memory", side_effect=repair
        ) as delete_memory:
            result = worker.run_action(event, deps)
        self.assertEqual(result["status"], "IN_PROGRESS")
        self.assertEqual(operations, ["repair", "verify-empty"])
        delete_memory.assert_called_once_with(semantic_table, SUBJECT)
        checkpoint.assert_called_once()
        query = semantic_table.query.call_args.kwargs
        self.assertEqual(query["ExpressionAttributeValues"], {":pk": f"USER#{SUBJECT}"})
        self.assertTrue(query["ConsistentRead"])
        self.assertNotIn("Scan", repr(semantic_table.method_calls))

    @patch.object(worker, "get_deletion_subject", return_value=SUBJECT)
    @patch.object(worker, "get_deletion_audit")
    def test_verify_missing_or_invalid_guard_performs_no_repair(
        self, get_audit, get_subject,
    ):
        get_audit.return_value = {
            "requestId": REQUEST_ID,
            "subjectDigest": worker.subject_digest(SUBJECT),
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        semantic_table = MagicMock()
        semantic_table.name = "journalm8-test-entry-chunks"
        deps = worker.Dependencies(
            entry_chunks_table=semantic_table,
            entry_chunks_table_name="journalm8-test-entry-chunks",
        )
        event = {"action": "VERIFY", "requestId": REQUEST_ID}
        failures = (
            False,
            worker.SemanticMemoryDeletionGuardError(
                "semantic memory deletion guard verification failed"
            ),
        )
        for guard_result in failures:
            with self.subTest(guard_result=guard_result), patch.object(
                worker,
                "semantic_deletion_guard_exists",
                return_value=guard_result if guard_result is False else None,
                side_effect=guard_result if isinstance(guard_result, Exception) else None,
            ), patch.object(worker, "delete_user_memory") as repair:
                with self.assertRaises(worker.AccountDeletionRetryableError):
                    worker.run_action(event, deps)
                repair.assert_not_called()
                semantic_table.query.assert_not_called()

    @patch.object(worker, "delete_identity")
    @patch.object(worker, "_semantic_partition_is_empty", return_value=False)
    @patch.object(worker, "_reverse_lookup_absent", return_value=True)
    @patch.object(worker, "partition_is_empty", return_value=True)
    @patch.object(worker, "prefix_is_empty", return_value=True)
    @patch.object(worker, "get_billing_recovery", return_value=None)
    @patch.object(worker, "get_deletion_subject", return_value=SUBJECT)
    @patch.object(worker, "get_deletion_audit")
    def test_cognito_deletion_is_blocked_while_semantic_memory_is_nonempty(
        self, get_audit, get_subject, recovery, prefix_empty, main_empty,
        reverse_absent, semantic_empty, delete_identity,
    ):
        get_audit.return_value = {
            "requestId": REQUEST_ID,
            "subjectDigest": worker.subject_digest(SUBJECT),
            "status": "IN_PROGRESS",
            "destructiveStartedAt": "2026-08-30T12:00:00Z",
        }
        with self.assertRaises(worker.AccountDeletionRetryableError):
            worker.run_action({
                "action": "DELETE_COGNITO_IDENTITY",
                "requestId": REQUEST_ID,
            }, worker.Dependencies())
        delete_identity.assert_not_called()

    def test_deletion_sources_and_workflow_exclude_sensitive_literals(self):
        sources = "\n".join(
            path.read_text()
            for path in sorted((ROOT / "function").glob("account_deletion_*.py"))
        )
        for forbidden_call in ("str(exc)", "repr(exc)"):
            self.assertNotIn(forbidden_call, sources)
        workflow = (ROOT / "workflows" / "account-deletion.asl.json").read_text()
        for sensitive in (
            "email", "journal", "requestToken", "stripeCustomerId",
            "subscriptionId", "claims", "objectKey", "userId",
        ):
            self.assertNotIn(sensitive, workflow)

    def test_every_current_async_user_writer_has_a_late_deletion_guard(self):
        writers = {
            "account_export_worker.py": "update_export_status",
            "ocr_worker.py": "update_entry_ocr_result",
            "ocr_failure_handler.py": "record_ocr_workflow_failure",
            "historical_reanalysis_worker.py": "update_entry_analysis",
            "historical_reanalysis_coordinator.py": "record_historical_reanalysis_page",
        }
        for filename, persistence_call in writers.items():
            with self.subTest(filename=filename):
                source = (ROOT / "function" / filename).read_text()
                self.assertIn("ensure_user_mutation_allowed", source)
                self.assertIn(persistence_call, source)

        app_source = (ROOT / "function" / "app.py").read_text()
        for final_write in (
            "persist_ask_history", "update_entry_analysis",
            "fail_historical_reanalysis_job", "mark_ocr_failed",
        ):
            self.assertIn(final_write, app_source)
        self.assertGreaterEqual(app_source.count("deletion_guard_response(user_id)"), 8)


if __name__ == "__main__":
    unittest.main()
