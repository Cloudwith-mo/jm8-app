import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
spec = importlib.util.spec_from_file_location("dev_semantic_bootstrap", BIN / "bootstrap_dev_semantic_tables.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BootstrapTests(unittest.TestCase):
    def test_chunk_schema_and_ownership_reject_mismatch(self):
        with self.assertRaises(b.contract.EnvironmentContractError):
            b.contract.validate_entry_chunks_table_description(
                {"Table": {"TableName": "journalm8-prod-entry-chunks"}},
                **b.COMMON, table_name=b.CHUNKS)
        with self.assertRaises(b.contract.EnvironmentContractError):
            b.contract.validate_entry_chunks_table_tags(
                {"Tags": [{"Key": "Stage", "Value": "prod"}]},
                app_name="journalm8", stage="dev", before_reconcile=True)
        with self.assertRaises(b.contract.EnvironmentContractError):
            b.contract.validate_entry_chunks_table_tags(
                {"Tags": []}, app_name="journalm8", stage="dev", before_reconcile=False)

    def test_existing_ready_tables_not_recreated(self):
        with patch.object(b, "inspect", return_value=("REUSE", {"Table": {"DeletionProtectionEnabled": True}})), \
             patch.object(b, "pitr", return_value={"ContinuousBackupsDescription": {"ContinuousBackupsStatus": "ENABLED", "PointInTimeRecoveryDescription": {"PointInTimeRecoveryStatus": "ENABLED"}}}), \
             patch.object(b, "aws") as aws:
            b.run("apply", b.CONFIRMATION)
            self.assertEqual(aws.call_count, 1)
            self.assertEqual(aws.call_args.args[:2], ("dynamodb", "update-continuous-backups"))

    def test_confirmation_before_any_aws_call(self):
        with patch.object(b, "aws") as aws:
            with self.assertRaises(b.Stop):
                b.run("apply", "dev")
            aws.assert_not_called()

    def test_wrong_account_stops(self):
        with patch.object(b, "aws", return_value={"Account": "000000000000"}) as aws:
            with self.assertRaises(b.Stop):
                b.inspect()
            self.assertEqual(aws.call_count, 1)

    def test_active_or_transitioning_consumer_stops(self):
        for state in ("Enabled", "Enabling", "Disabling", "Updating"):
            with self.subTest(state=state), patch.object(b, "aws", return_value={
                "EventSourceMappings": [{"EventSourceArn": b.arn(b.MAIN) + "/stream/test", "State": state}]
            }):
                with self.assertRaises(b.Stop):
                    b.consumers_disabled()

    def test_disabled_or_absent_consumers_allowed(self):
        for mappings in ([], [{"EventSourceArn": b.arn(b.CHUNKS) + "/stream/test", "State": "Disabled"}]):
            with patch.object(b, "aws", return_value={"EventSourceMappings": mappings}):
                b.consumers_disabled()

    def test_malformed_mapping_response_stops(self):
        with patch.object(b, "aws", return_value={}):
            with self.assertRaises(b.Stop):
                b.consumers_disabled()

    def test_check_and_verify_never_write(self):
        for mode in ("check", "verify"):
            with patch.object(b, "inspect", return_value=("REUSE", {"Table": {}})), patch.object(b, "aws") as aws:
                b.run(mode)
                aws.assert_not_called()

    def test_preflight_failure_prevents_writes(self):
        with patch.object(b, "inspect", side_effect=b.Stop("schema mismatch")), patch.object(b, "aws") as aws:
            with self.assertRaises(b.Stop):
                b.run("apply", b.CONFIRMATION)
            aws.assert_not_called()

    def test_apply_only_expected_dynamodb_writes(self):
        with patch.object(b, "inspect", side_effect=[("ENABLE", None), ("REUSE", {})]), \
             patch.object(b, "wait_ready"), patch.object(b, "consumers_disabled"), \
             patch.object(b, "description", return_value={}), \
             patch.object(b.contract, "validate_main_table_stream_description", return_value="ENABLE"), \
             patch.object(b, "pitr", return_value={"ContinuousBackupsDescription": {"ContinuousBackupsStatus": "ENABLED", "PointInTimeRecoveryDescription": {"PointInTimeRecoveryStatus": "ENABLED"}}}), \
             patch.object(b, "aws") as aws:
            b.run("apply", b.CONFIRMATION)
            calls = [call.args for call in aws.call_args_list]
            self.assertEqual([call[:2] for call in calls], [
                ("dynamodb", "create-table"), ("dynamodb", "update-continuous-backups"), ("dynamodb", "update-table")])
            self.assertEqual(calls[0][3], b.CHUNKS)
            self.assertIn("--deletion-protection-enabled", calls[0])
            self.assertIn("PAY_PER_REQUEST", calls[0])
            self.assertEqual(calls[2][3], b.MAIN)
            self.assertIn("StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES", calls[2])

    def test_cli_denial_not_treated_as_absence(self):
        result = type("Result", (), {"returncode": 254, "stderr": "An error occurred (AccessDeniedException) private-detail", "stdout": ""})()
        with patch.object(b.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(b.Stop, "AccessDeniedException") as error:
                b.description(b.CHUNKS, missing_ok=True)
            self.assertNotIn("private-detail", str(error.exception))

    def test_missing_table_handled_only_when_allowed(self):
        result = type("Result", (), {"returncode": 254, "stderr": "An error occurred (ResourceNotFoundException)", "stdout": ""})()
        with patch.object(b.subprocess, "run", return_value=result):
            self.assertIsNone(b.description(b.CHUNKS, missing_ok=True))
            with self.assertRaises(b.Stop):
                b.description(b.MAIN)

    def test_fixed_profile_region_and_no_inherited_keys(self):
        result = type("Result", (), {"returncode": 0, "stdout": "{}"})()
        with patch.dict(b.os.environ, {"AWS_ACCESS_KEY_ID": "not-used"}), patch.object(b.subprocess, "run", return_value=result) as run:
            b.aws("sts", "get-caller-identity")
            self.assertNotIn("AWS_ACCESS_KEY_ID", run.call_args.kwargs["env"])
            self.assertIn("jm8-dev", run.call_args.args[0])
            self.assertIn("us-east-1", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()


class ReadinessTests(unittest.TestCase):
    def test_delayed_backup_readiness(self):
        with patch.object(b, "pitr", side_effect=[
            {"ContinuousBackupsDescription": {"ContinuousBackupsStatus": "DISABLED"}},
            {"ContinuousBackupsDescription": {"ContinuousBackupsStatus": "ENABLED"}},
        ]), patch.object(b.time, "sleep") as sleep:
            b.wait_backups_ready()
            sleep.assert_called_once_with(2)

    def test_readiness_timeout_is_bounded(self):
        with patch.object(b, "pitr", return_value={"ContinuousBackupsDescription": {"ContinuousBackupsStatus": "DISABLED"}}) as read, patch.object(b.time, "sleep"), patch.object(b, "aws") as aws:
            with self.assertRaises(b.Stop):
                b.wait_backups_ready()
            self.assertEqual(read.call_count, 60)
            aws.assert_not_called()

    def test_denial_is_not_retried(self):
        with patch.object(b, "pitr", side_effect=b.Stop("AccessDeniedException")) as read:
            with self.assertRaises(b.Stop):
                b.wait_backups_ready()
            read.assert_called_once()
