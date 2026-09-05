from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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


MAIN_STREAM_AWS_SHIM = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_MAIN_TABLE_STATE"])
log_path = Path(os.environ["FAKE_MAIN_TABLE_LOG"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")

if args[:2] == ["dynamodb", "describe-table"]:
    table = {
        "TableName": state["name"],
        "TableArn": state["arn"],
        "TableStatus": state["status"],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [{
            "IndexName": "GSI1",
            "KeySchema": [
                {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }],
        "BillingModeSummary": {"BillingMode": "PAY_PER_REQUEST"},
    }
    if state.get("stream_enabled"):
        table["StreamSpecification"] = {
            "StreamEnabled": True,
            "StreamViewType": state["stream_view_type"],
        }
        table["LatestStreamArn"] = state["arn"] + "/stream/2026-09-05T00:00:00.000"
    print(json.dumps({"Table": table}))
elif args[:2] == ["dynamodb", "update-table"]:
    state["stream_enabled"] = True
    state["stream_view_type"] = "NEW_AND_OLD_IMAGES"
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif args[:2] == ["dynamodb", "wait"]:
    pass
else:
    raise SystemExit("unexpected fake AWS operation")
'''


MAPPING_AWS_SHIM = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_MAPPING_STATE"])
log_path = Path(os.environ["FAKE_MAPPING_LOG"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")

if args[:2] == ["lambda", "list-event-source-mappings"]:
    mappings = [] if state.get("mapping") is None else [{"UUID": "mapping-1"}]
    print(json.dumps({"EventSourceMappings": mappings}))
elif args[:2] in (["lambda", "create-event-source-mapping"], ["lambda", "update-event-source-mapping"]):
    def option(name):
        return args[args.index(name) + 1]
    if "--no-enabled" not in args:
        raise SystemExit("mapping must remain disabled")
    previous = state.get("mapping") or {}
    destination_path = option("--destination-config").removeprefix("file://")
    destination = json.loads(Path(destination_path).read_text(encoding="utf-8"))
    function_name = option("--function-name")
    mapping = {
        "UUID": "mapping-1",
        "EventSourceArn": previous.get("EventSourceArn") or option("--event-source-arn"),
        "FunctionArn": f"arn:aws:lambda:us-east-1:114743615542:function:{function_name}",
        "State": "Disabled",
        "StartingPosition": previous.get("StartingPosition") or option("--starting-position"),
        "BatchSize": int(option("--batch-size")),
        "MaximumBatchingWindowInSeconds": int(option("--maximum-batching-window-in-seconds")),
        "ParallelizationFactor": int(option("--parallelization-factor")),
        "BisectBatchOnFunctionError": "--bisect-batch-on-function-error" in args,
        "MaximumRetryAttempts": int(option("--maximum-retry-attempts")),
        "MaximumRecordAgeInSeconds": int(option("--maximum-record-age-in-seconds")),
        "FunctionResponseTypes": [option("--function-response-types")],
        "DestinationConfig": destination,
    }
    state["mapping"] = mapping
    state_path.write_text(json.dumps(state), encoding="utf-8")
    if "--query" in args:
        print("mapping-1")
elif args[:2] == ["lambda", "wait"]:
    pass
elif args[:2] == ["lambda", "get-event-source-mapping"]:
    print(json.dumps(state["mapping"]))
else:
    raise SystemExit("unexpected fake Lambda operation")
'''


class SemanticMemoryDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deploy = (BIN_DIR / "deploy-semantic-memory").read_text(
            encoding="utf-8"
        )
        cls.create_resources = (BIN_DIR / "create-resources").read_text(
            encoding="utf-8"
        )
        start = cls.create_resources.index("ensure_main_table_stream() {")
        end = cls.create_resources.index("\n}\n\necho", start) + 3
        cls.stream_function = cls.create_resources[start:end]
        mapping_start = cls.deploy.index("ensure_disabled_event_source_mapping() {")
        mapping_end = cls.deploy.index(
            "\n}\n\nensure_disabled_event_source_mapping", mapping_start
        ) + 3
        cls.mapping_function = cls.deploy[mapping_start:mapping_end]

    @staticmethod
    def _main_state(**changes: object) -> dict[str, object]:
        state: dict[str, object] = {
            "name": "journalm8-dev-main",
            "arn": (
                "arn:aws:dynamodb:us-east-1:114743615542:"
                "table/journalm8-dev-main"
            ),
            "status": "ACTIVE",
            "stream_enabled": False,
            "stream_view_type": None,
        }
        state.update(changes)
        return state

    def _run_stream_reconcile(self, state: dict[str, object]):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim_dir = root / "bin"
            shim_dir.mkdir()
            aws_path = shim_dir / "aws"
            aws_path.write_text(MAIN_STREAM_AWS_SHIM, encoding="utf-8")
            aws_path.chmod(0o755)
            state_path = root / "state.json"
            log_path = root / "aws.log"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            runner = root / "run.sh"
            runner.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"SCRIPT_DIR={str(BIN_DIR)!r}\n"
                "AWS_PROFILE=jm8-dev\nAWS_REGION=us-east-1\n"
                "APP_NAME=journalm8\nSTAGE=dev\nACCOUNT_ID=114743615542\n"
                "TABLE_NAME=journalm8-dev-main\nmkdir -p .build\n"
                f"{self.stream_function}\nensure_main_table_stream\n",
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment.update({
                "PATH": f"{shim_dir}:{environment['PATH']}",
                "FAKE_MAIN_TABLE_STATE": str(state_path),
                "FAKE_MAIN_TABLE_LOG": str(log_path),
            })
            result = subprocess.run(
                ["bash", str(runner)],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
            )
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            calls = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            return result, final_state, calls

    def test_main_stream_is_enabled_exactly_and_then_reused(self):
        first, state, first_calls = self._run_stream_reconcile(self._main_state())
        second, second_state, second_calls = self._run_stream_reconcile(state)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(second_state["stream_enabled"])
        self.assertEqual(second_state["stream_view_type"], "NEW_AND_OLD_IMAGES")
        first_updates = [call for call in first_calls if call[:2] == ["dynamodb", "update-table"]]
        second_updates = [call for call in second_calls if call[:2] == ["dynamodb", "update-table"]]
        self.assertEqual(len(first_updates), 1)
        self.assertIn("StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES", first_updates[0])
        self.assertEqual(second_updates, [])

    def test_incompatible_existing_stream_fails_before_mutation(self):
        result, _, calls = self._run_stream_reconcile(self._main_state(
            stream_enabled=True,
            stream_view_type="KEYS_ONLY",
        ))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[:2] == ["dynamodb", "update-table"] for call in calls))

    def test_worker_package_contains_exact_required_modules(self):
        block = self.deploy.split("REQUIRED_MODULES=(\n", 1)[1].split("\n)", 1)[0]
        modules = [line.strip() for line in block.splitlines() if line.strip()]
        self.assertEqual(modules, [
            "semantic_chunking.py",
            "semantic_memory_contract.py",
            "semantic_memory_deletion_guard.py",
            "semantic_memory_store.py",
            "semantic_memory_lifecycle.py",
            "semantic_memory_worker.py",
        ])
        self.assertNotIn("./bin/package", self.deploy)

    def test_stage_scoped_names_runtime_and_concurrency_are_exact(self):
        for assignment in (
            'WORKER_FUNCTION_NAME="${APP_NAME}-${STAGE}-semantic-memory-worker"',
            'WORKER_ROLE_NAME="${APP_NAME}-${STAGE}-semantic-memory-worker-role"',
            'DLQ_NAME="${APP_NAME}-${STAGE}-semantic-memory-dlq"',
            'LOG_GROUP_NAME="/aws/lambda/${WORKER_FUNCTION_NAME}"',
        ):
            self.assertIn(assignment, self.deploy)
        self.assertIn("--runtime python3.12", self.deploy)
        self.assertIn("prod) RESERVED_CONCURRENCY=1", self.deploy)
        self.assertIn("dev|staging) RESERVED_CONCURRENCY=2", self.deploy)
        self.assertIn("--retention-in-days 30", self.deploy)
        self.assertIn(
            'Variables={ENTRY_CHUNKS_TABLE_NAME=${ENTRY_CHUNKS_TABLE_NAME}}',
            self.deploy,
        )

    def test_worker_role_policy_is_exact_and_has_no_forbidden_access(self):
        body = self.deploy.split(
            '> "$BUILD_DIR/worker-policy.json" <<\'PY\'\n', 1
        )[1].split("\nPY\n", 1)[0]
        output = io.StringIO()
        arguments = [
            "policy",
            "arn:aws:dynamodb:us-east-1:123456789012:table/journalm8-dev-main/stream/version",
            "arn:aws:dynamodb:us-east-1:123456789012:table/journalm8-dev-entry-chunks",
            "arn:aws:sqs:us-east-1:123456789012:journalm8-dev-semantic-memory-dlq",
        ]
        with patch.object(sys, "argv", arguments), redirect_stdout(output):
            exec(compile(body, "worker-policy", "exec"), {})
        policy = json.loads(output.getvalue())
        statements = {item["Sid"]: item for item in policy["Statement"]}
        self.assertEqual(set(statements["ReadExactMainTableStream"]["Action"]), {
            "dynamodb:DescribeStream",
            "dynamodb:GetRecords",
            "dynamodb:GetShardIterator",
        })
        self.assertEqual(statements["ListMainTableStreams"], {
            "Sid": "ListMainTableStreams",
            "Effect": "Allow",
            "Action": ["dynamodb:ListStreams"],
            "Resource": "*",
        })
        self.assertEqual(set(statements["WriteExactEntryChunksTable"]["Action"]), {
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:Query",
            "dynamodb:BatchWriteItem",
        })
        self.assertEqual(
            statements["SendExactSemanticMemoryDlq"]["Action"],
            ["sqs:SendMessage"],
        )
        serialized = json.dumps(policy)
        for forbidden in (
            "dynamodb:Scan",
            "dynamodb:DeleteTable",
            "dynamodb:UpdateItem",
            "dynamodb:DeleteItem",
            "bedrock:",
            "s3:",
            "cognito-idp:",
            "states:",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_dlq_hardening_destination_and_alarms_are_configured(self):
        self.assertIn("KmsMasterKeyId=alias/aws/sqs", self.deploy)
        self.assertIn("DLQ_RETENTION_SECONDS=1209600", self.deploy)
        self.assertIn('--tags App="$APP_NAME",Stage="$STAGE",ManagedBy=aws-cli', self.deploy)
        self.assertIn('{"OnFailure": {"Destination": sys.argv[1]}}', self.deploy)
        for metric in (
            "--metric-name Errors",
            "--metric-name Throttles",
            "--metric-name IteratorAge",
            "--metric-name ApproximateNumberOfMessagesVisible",
        ):
            self.assertIn(metric, self.deploy)

    def test_mapping_is_created_and_reconciled_disabled_with_exact_settings(self):
        for expected in (
            "--no-enabled",
            "--starting-position LATEST",
            "--batch-size 25",
            "--maximum-batching-window-in-seconds 1",
            "--parallelization-factor 1",
            "--bisect-batch-on-function-error",
            "--maximum-retry-attempts 5",
            "--maximum-record-age-in-seconds 3600",
            "--function-response-types ReportBatchItemFailures",
        ):
            self.assertIn(expected, self.deploy)
        self.assertNotIn("--enabled", self.deploy.replace("--no-enabled", ""))
        self.assertIn("mapping.get(key)", self.deploy)
        self.assertIn('"State": "Disabled"', self.deploy)
        self.assertIn("create-event-source-mapping", self.deploy)
        self.assertIn("update-event-source-mapping", self.deploy)
        self.assertIn("len(mappings) > 1", self.deploy)
        self.assertIn("len(mappings) != 1", self.deploy)

    def test_rerun_reuses_mapping_and_still_forces_it_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim_dir = root / "bin"
            shim_dir.mkdir()
            aws_path = shim_dir / "aws"
            aws_path.write_text(MAPPING_AWS_SHIM, encoding="utf-8")
            aws_path.chmod(0o755)
            state_path = root / "state.json"
            log_path = root / "aws.log"
            state_path.write_text(json.dumps({"mapping": None}), encoding="utf-8")
            build_dir = root / "build"
            build_dir.mkdir()
            dlq_arn = (
                "arn:aws:sqs:us-east-1:114743615542:"
                "journalm8-dev-semantic-memory-dlq"
            )
            (build_dir / "destination-config.json").write_text(json.dumps({
                "OnFailure": {"Destination": dlq_arn}
            }), encoding="utf-8")
            runner = root / "run.sh"
            runner.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\n"
                f"BUILD_DIR={str(build_dir)!r}\n"
                "AWS_PROFILE=jm8-dev\nAWS_REGION=us-east-1\n"
                "WORKER_FUNCTION_NAME=journalm8-dev-semantic-memory-worker\n"
                "WORKER_ARN=arn:aws:lambda:us-east-1:114743615542:function:journalm8-dev-semantic-memory-worker\n"
                "STREAM_ARN=arn:aws:dynamodb:us-east-1:114743615542:table/journalm8-dev-main/stream/version\n"
                f"DLQ_ARN={dlq_arn!r}\n"
                f"{self.mapping_function}\n"
                "ensure_disabled_event_source_mapping\n"
                "ensure_disabled_event_source_mapping\n",
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment.update({
                "PATH": f"{shim_dir}:{environment['PATH']}",
                "FAKE_MAPPING_STATE": str(state_path),
                "FAKE_MAPPING_LOG": str(log_path),
            })
            result = subprocess.run(
                ["bash", str(runner)],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            calls = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(result.returncode, 0, result.stderr)
        operations = [call[1] for call in calls]
        self.assertEqual(operations.count("create-event-source-mapping"), 1)
        self.assertEqual(operations.count("update-event-source-mapping"), 1)
        self.assertEqual(state["mapping"]["State"], "Disabled")
        self.assertEqual(
            state["mapping"]["FunctionResponseTypes"],
            ["ReportBatchItemFailures"],
        )

    def test_deployment_never_invokes_or_processes_data(self):
        lowered = self.deploy.lower()
        for forbidden in (
            "aws lambda invoke",
            "aws dynamodb scan",
            "aws dynamodb get-item",
            "aws dynamodb put-item",
            "aws dynamodb update-item",
            "aws dynamodb batch-write-item",
            "process_entry_stream_event",
            "rawtext",
            "cleantext",
        ):
            self.assertNotIn(forbidden, lowered)
        chunks_function = self.create_resources.split(
            "ensure_entry_chunks_table() {", 1
        )[1].split("\n}\n\nensure_main_table_stream", 1)[0]
        self.assertNotIn("stream-specification", chunks_function.lower())

    def test_general_deploy_only_reconciles_the_disabled_mapping(self):
        general = (BIN_DIR / "deploy").read_text(encoding="utf-8")
        self.assertIn("./bin/deploy-semantic-memory", general)
        self.assertNotIn("create-event-source-mapping", general)
        self.assertNotIn("update-event-source-mapping", general)
        self.assertNotIn("--enabled", general)

    def test_production_deployer_is_exact_and_cannot_invoke_worker(self):
        policies = generate_policies()
        serialized = compact_json(policies)
        self.assertIn("journalm8-prod-semantic-memory-worker", serialized)
        self.assertIn("journalm8-prod-semantic-memory-worker-role", serialized)
        self.assertIn("journalm8-prod-semantic-memory-dlq", serialized)
        self.assertNotIn("lambda:InvokeFunction", serialized)
        self.assertNotIn("dynamodb:Scan", serialized)
        self.assertNotIn("dynamodb:DeleteTable", serialized)
        self.assertIn(
            f"arn:aws:sqs:us-east-1:{ACCOUNT_ID}:journalm8-prod-semantic-memory-dlq",
            serialized,
        )


if __name__ == "__main__":
    unittest.main()
