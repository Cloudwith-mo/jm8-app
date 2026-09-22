import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import jm8_api_semantic_policy as policy
import jm8_environment_contract as contract

CONFIG = dict(app_name="journalm8", stage="dev", aws_region="us-east-1",
              aws_profile="jm8-dev", account_id="114743615542",
              entry_chunks_table_name="journalm8-dev-entry-chunks")


class SemanticPolicyTests(unittest.TestCase):
    def document(self, **overrides):
        config = dict(CONFIG, **overrides)
        return policy.policy_document(
            config["app_name"], config["stage"], config["aws_region"],
            config["account_id"], config["entry_chunks_table_name"])

    def test_stage_boundaries_and_no_writes(self):
        for stage in ("dev", "staging", "prod"):
            doc = self.document(stage=stage,
                                entry_chunks_table_name=f"journalm8-{stage}-entry-chunks")
            table = f"arn:aws:dynamodb:us-east-1:114743615542:table/journalm8-{stage}-entry-chunks"
            grants = {(a, s["Resource"]) for s in doc["Statement"] for a in s["Action"]}
            self.assertEqual(grants, {
                ("dynamodb:SearchVectors", table + "/index/SemanticEmbeddingIndex"),
                ("dynamodb:BatchGetItem", table)})
            for other in {"dev", "staging", "prod"} - {stage}:
                self.assertNotIn(f"journalm8-{other}-", json.dumps(doc))
            self.assertNotIn("*", json.dumps(doc))
            self.assertTrue(all(s["Effect"] == "Allow" for s in doc["Statement"]))

    def test_rejects_cross_stage_and_malformed_identifiers(self):
        for override in (
            dict(entry_chunks_table_name="journalm8-prod-entry-chunks"),
            dict(entry_chunks_table_name="journalm8-dev-entry-chunks*"),
            dict(stage="unknown"), dict(app_name="other"),
            dict(account_id="000000000000"), dict(aws_region="*"),
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.document(**override)

    def call(self, config, service, operation, *args):
        self.calls.append((service, operation, args))
        role = "journalm8-dev-lambda-basic-role"
        if operation == "get-role":
            return {"Role": {"Arn": self.role}}
        if operation == "put-role-policy":
            self.written = json.loads(args[args.index("--policy-document") + 1])
            return {}
        if operation == "get-role-policy":
            return {"RoleName": role, "PolicyName": "journalm8-dev-semantic-query-retrieval",
                    "PolicyDocument": self.readback}
        raise AssertionError(operation)

    def setUp(self):
        self.calls = []
        self.role = "arn:aws:iam::114743615542:role/journalm8-dev-lambda-basic-role"
        self.readback = self.document()

    def test_check_is_read_only(self):
        result = policy.reconcile(CONFIG, "check", self.call)
        self.assertEqual(result["policy"], self.document())
        self.assertEqual([c[1] for c in self.calls], ["get-role"])

    def test_apply_writes_exact_policy_and_verifies(self):
        self.assertTrue(policy.reconcile(CONFIG, "apply", self.call)["verified"])
        self.assertEqual(self.written, self.document())
        self.assertEqual([c[1] for c in self.calls],
                         ["get-role", "put-role-policy", "get-role-policy"])

    def test_verify_does_not_mutate(self):
        policy.reconcile(CONFIG, "verify", self.call)
        self.assertEqual([c[1] for c in self.calls], ["get-role", "get-role-policy"])

    def test_wrong_role_blocks_write(self):
        self.role = self.role.replace("-dev-", "-prod-")
        with self.assertRaisesRegex(ValueError, "ARN mismatch"):
            policy.reconcile(CONFIG, "apply", self.call)
        self.assertEqual([c[1] for c in self.calls], ["get-role"])

    def test_readback_drift_fails(self):
        self.readback = {"Version": "2012-10-17", "Statement": []}
        with self.assertRaisesRegex(ValueError, "does not match"):
            policy.reconcile(CONFIG, "verify", self.call)

    def test_aws_error_is_not_swallowed_or_dumped(self):
        with patch.object(policy.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "An error occurred (AccessDenied) sensitive-value"
            with self.assertRaisesRegex(RuntimeError, r"failed \(AccessDenied\)$"):
                policy.aws(CONFIG, "iam", "put-role-policy")

    def test_wrong_account_stops_before_reconciliation(self):
        env = dict(APP_NAME="journalm8", STAGE="dev", AWS_REGION="us-east-1",
                   AWS_PROFILE="jm8-dev", EXPECTED_AWS_ACCOUNT_ID="114743615542",
                   TABLE_NAME="journalm8-dev-main",
                   ENTRY_CHUNKS_TABLE_NAME="journalm8-dev-entry-chunks",
                   RAW_BUCKET="journalm8-dev-raw-114743615542")
        with patch.dict(os.environ, env, clear=True), \
             patch.object(sys, "argv", ["helper", "apply"]), \
             patch.object(contract, "get_actual_aws_account_id", return_value="000000000000"), \
             patch.object(policy, "reconcile") as reconcile:
            with self.assertRaises(contract.EnvironmentContractError):
                policy.main()
            reconcile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
