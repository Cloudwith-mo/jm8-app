from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
sys.path.insert(0, str(BIN_DIR))


from jm8_production_deployer_policies import (  # noqa: E402
    ACCOUNT_ID,
    compact_json,
    generate_policies,
)


class SemanticEmbeddingDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = BIN_DIR / "deploy-semantic-embedding"
        cls.deploy = cls.path.read_text(encoding="utf-8")

    def test_script_is_executable_and_uses_the_shared_guard(self):
        self.assertTrue(self.path.stat().st_mode & stat.S_IXUSR)
        self.assertIn('source "$SCRIPT_DIR/jm8_deployment_guard.sh"', self.deploy)
        self.assertIn(
            'export JM8_OPERATION="deploy-semantic-embedding"',
            self.deploy,
        )
        self.assertIn("jm8_validate_contract_or_exit", self.deploy)

    def test_names_and_activation_are_independently_stage_scoped(self):
        for assignment in (
            'WORKER_FUNCTION_NAME="${APP_NAME}-${STAGE}-semantic-embedding-worker"',
            'WORKER_ROLE_NAME="${APP_NAME}-${STAGE}-semantic-embedding-worker-role"',
            'DLQ_NAME="${APP_NAME}-${STAGE}-semantic-embedding-dlq"',
            'LOG_GROUP_NAME="/aws/lambda/${WORKER_FUNCTION_NAME}"',
        ):
            self.assertIn(assignment, self.deploy)
        self.assertIn(
            '${SEMANTIC_EMBEDDING_MAPPING_ENABLED:?',
            self.deploy,
        )
        self.assertNotIn("SEMANTIC_MEMORY_ACTIVATION_CONFIRMATION", self.deploy)
        self.assertIn('true) MAPPING_TARGET_STATE="Enabled"', self.deploy)
        self.assertIn('false) MAPPING_TARGET_STATE="Disabled"', self.deploy)

    def test_entry_chunks_stream_is_exact_and_incompatible_streams_fail_closed(self):
        self.assertIn("entry-chunks-stream", self.deploy)
        self.assertIn(
            "StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES",
            self.deploy,
        )
        self.assertNotIn("StreamViewType=KEYS_ONLY", self.deploy)
        self.assertNotIn("StreamViewType=NEW_IMAGE", self.deploy)

    def test_worker_package_contains_exact_runtime_modules(self):
        block = self.deploy.split("REQUIRED_MODULES=(\n", 1)[1].split(
            "\n)", 1
        )[0]
        modules = [line.strip() for line in block.splitlines() if line.strip()]
        self.assertEqual(modules, [
            "semantic_chunking.py",
            "semantic_memory_contract.py",
            "semantic_memory_store.py",
            "semantic_embedding_contract.py",
            "semantic_embedding_provider.py",
            "semantic_embedding_store.py",
            "semantic_embedding_lifecycle.py",
            "semantic_embedding_worker.py",
        ])

    def test_runtime_configuration_is_bounded_and_exact(self):
        self.assertIn("--runtime python3.12", self.deploy)
        self.assertIn("--architectures arm64", self.deploy)
        self.assertIn("--timeout 120", self.deploy)
        self.assertIn("--memory-size 512", self.deploy)
        self.assertIn("prod) RESERVED_CONCURRENCY=1", self.deploy)
        self.assertIn("dev|staging) RESERVED_CONCURRENCY=2", self.deploy)
        self.assertIn("--retention-in-days 30", self.deploy)
        self.assertIn(
            'Variables={ENTRY_CHUNKS_TABLE_NAME=${ENTRY_CHUNKS_TABLE_NAME}}',
            self.deploy,
        )

    def test_worker_policy_is_exact_and_has_no_data_plane_escape_hatches(self):
        body = self.deploy.split(
            '> "$BUILD_DIR/worker-policy.json" <<\'PY\'\n', 1
        )[1].split("\nPY\n", 1)[0]
        output = io.StringIO()
        arguments = [
            "policy",
            "arn:aws:dynamodb:us-east-1:123456789012:table/chunks/stream/version",
            "arn:aws:dynamodb:us-east-1:123456789012:table/chunks",
            "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0",
            "arn:aws:sqs:us-east-1:123456789012:embedding-dlq",
        ]
        with patch.object(sys, "argv", arguments), redirect_stdout(output):
            exec(compile(body, "embedding-worker-policy", "exec"), {})
        policy = json.loads(output.getvalue())
        statements = {item["Sid"]: item for item in policy["Statement"]}
        self.assertEqual(
            set(statements["ReadExactEntryChunksStream"]["Action"]),
            {
                "dynamodb:DescribeStream",
                "dynamodb:GetRecords",
                "dynamodb:GetShardIterator",
            },
        )
        self.assertEqual(
            set(statements["ReadAndPersistExactEntryChunks"]["Action"]),
            {"dynamodb:Query", "dynamodb:TransactWriteItems"},
        )
        self.assertEqual(
            statements["InvokeExactEmbeddingModel"],
            {
                "Sid": "InvokeExactEmbeddingModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": arguments[3],
            },
        )
        self.assertEqual(
            statements["SendExactSemanticEmbeddingDlq"]["Resource"],
            arguments[4],
        )
        serialized = json.dumps(policy)
        for forbidden in (
            "dynamodb:Scan",
            "dynamodb:DeleteTable",
            "dynamodb:DeleteItem",
            "dynamodb:BatchWriteItem",
            "bedrock:InvokeModelWithResponseStream",
            "s3:",
            "cognito-idp:",
            "states:",
        ):
            self.assertNotIn(forbidden, serialized)
        wildcard_statements = [
            item for item in policy["Statement"] if item["Resource"] == "*"
        ]
        self.assertEqual(
            wildcard_statements,
            [{
                "Sid": "ListEntryChunksStreams",
                "Effect": "Allow",
                "Action": ["dynamodb:ListStreams"],
                "Resource": "*",
            }],
        )

    def test_mapping_is_created_disabled_with_partial_batch_retries(self):
        for expected in (
            "--no-enabled",
            "--starting-position LATEST",
            "--batch-size 10",
            "--maximum-batching-window-in-seconds 1",
            "--parallelization-factor 1",
            "--bisect-batch-on-function-error",
            "--maximum-retry-attempts 5",
            "--maximum-record-age-in-seconds 3600",
            "--function-response-types ReportBatchItemFailures",
        ):
            self.assertIn(expected, self.deploy)
        self.assertIn("create-event-source-mapping", self.deploy)
        self.assertIn("update-event-source-mapping", self.deploy)
        self.assertIn("len(mappings) > 1", self.deploy)
        self.assertIn("len(mappings) != 1", self.deploy)

    def test_activation_happens_only_after_mapping_and_alarm_verification(self):
        configured = self.deploy.index(
            'verify_mapping "$MAPPING_STATE" configured'
        )
        alarms = self.deploy.index('> "$BUILD_DIR/alarms.json"', configured)
        activation = self.deploy.index(
            'if [ "$MAPPING_STATE" != "$MAPPING_TARGET_STATE" ]',
            alarms,
        )
        final = self.deploy.index(
            'verify_mapping "$MAPPING_TARGET_STATE" final',
            activation,
        )
        self.assertLess(configured, alarms)
        self.assertLess(alarms, activation)
        self.assertLess(activation, final)

    def test_deployment_does_not_invoke_bedrock_lambda_or_touch_items(self):
        lowered = self.deploy.lower()
        for forbidden in (
            "aws bedrock-runtime invoke-model",
            "aws lambda invoke",
            "aws dynamodb scan",
            "aws dynamodb get-item",
            "aws dynamodb put-item",
            "aws dynamodb update-item",
            "aws dynamodb batch-write-item",
            "process_embedding_stream_event",
            "rawtext",
            "cleantext",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_general_deploy_reconciles_embedding_without_inline_activation(self):
        general = (BIN_DIR / "deploy").read_text(encoding="utf-8")
        self.assertIn("./bin/deploy-semantic-memory", general)
        self.assertIn("./bin/deploy-semantic-embedding", general)
        self.assertLess(
            general.index("./bin/deploy-semantic-memory"),
            general.index("./bin/deploy-semantic-embedding"),
        )
        self.assertNotIn("create-event-source-mapping", general)
        self.assertNotIn("update-event-source-mapping", general)

    def test_production_deployer_can_reconcile_but_cannot_invoke_worker(self):
        serialized = compact_json(generate_policies())
        self.assertIn("journalm8-prod-semantic-embedding-worker", serialized)
        self.assertIn("journalm8-prod-semantic-embedding-worker-role", serialized)
        self.assertIn("journalm8-prod-semantic-embedding-dlq", serialized)
        self.assertNotIn("lambda:InvokeFunction", serialized)
        self.assertNotIn("dynamodb:Scan", serialized)
        self.assertNotIn("dynamodb:DeleteTable", serialized)
        self.assertIn(
            f"arn:aws:sqs:us-east-1:{ACCOUNT_ID}:"
            "journalm8-prod-semantic-embedding-dlq",
            serialized,
        )


if __name__ == "__main__":
    unittest.main()
