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

def option(name):
    return args[args.index(name) + 1]

if args[:2] == ["lambda", "list-event-source-mappings"]:
    if state.get("list_failure"):
        raise SystemExit(41)
    mappings = [] if state.get("mapping") is None else [{
        "UUID": "mapping-1",
        "State": state["mapping"]["State"],
    }]
    print(json.dumps({"EventSourceMappings": mappings}))
elif args[:2] == ["lambda", "create-event-source-mapping"]:
    if state.get("create_failure"):
        raise SystemExit(42)
    if "--no-enabled" not in args or "--enabled" in args:
        raise SystemExit("mapping creation must begin disabled")
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
elif args[:2] == ["lambda", "update-event-source-mapping"]:
    is_state_update = "--enabled" in args or "--no-enabled" in args
    if is_state_update and state.get("state_update_failure"):
        raise SystemExit(43)
    if not is_state_update and state.get("configuration_update_failure"):
        raise SystemExit(44)
    mapping = state["mapping"]
    if is_state_update:
        mapping["State"] = "Enabled" if "--enabled" in args else "Disabled"
    else:
        destination_path = option("--destination-config").removeprefix("file://")
        mapping.update({
            "FunctionArn": (
                "arn:aws:lambda:us-east-1:114743615542:function:"
                + option("--function-name")
            ),
            "BatchSize": int(option("--batch-size")),
            "MaximumBatchingWindowInSeconds": int(
                option("--maximum-batching-window-in-seconds")
            ),
            "ParallelizationFactor": int(option("--parallelization-factor")),
            "BisectBatchOnFunctionError": "--bisect-batch-on-function-error" in args,
            "MaximumRetryAttempts": int(option("--maximum-retry-attempts")),
            "MaximumRecordAgeInSeconds": int(
                option("--maximum-record-age-in-seconds")
            ),
            "FunctionResponseTypes": [option("--function-response-types")],
            "DestinationConfig": json.loads(
                Path(destination_path).read_text(encoding="utf-8")
            ),
        })
    state["mapping"] = mapping
    state_path.write_text(json.dumps(state), encoding="utf-8")
elif args[:2] == ["lambda", "get-event-source-mapping"]:
    if "--query" in args:
        poll_index = state.get("poll_index", 0)
        if state.get("poll_failure_at") == poll_index + 1:
            raise SystemExit(42)
        poll_states = state.get("poll_states")
        mapping_state = (
            poll_states[min(poll_index, len(poll_states) - 1)]
            if poll_states
            else state["mapping"]["State"]
        )
        if mapping_state in ("Enabled", "Disabled"):
            state["mapping"]["State"] = mapping_state
        state["poll_index"] = poll_index + 1
        state_path.write_text(json.dumps(state), encoding="utf-8")
        print(mapping_state)
    else:
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
        mapping_start = cls.deploy.index('MAPPING_UUID=""')
        mapping_end = cls.deploy.index(
            "\n\nensure_event_source_mapping\n",
            mapping_start,
        )
        cls.mapping_functions = cls.deploy[mapping_start:mapping_end]

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

    @staticmethod
    def _mapping_state(
        mapping_state: str = "Disabled",
        **changes: object,
    ) -> dict[str, object]:
        mapping = {
            "UUID": "mapping-1",
            "EventSourceArn": (
                "arn:aws:dynamodb:us-east-1:114743615542:"
                "table/journalm8-dev-main/stream/version"
            ),
            "FunctionArn": (
                "arn:aws:lambda:us-east-1:114743615542:"
                "function:journalm8-dev-semantic-memory-worker"
            ),
            "State": mapping_state,
            "StartingPosition": "LATEST",
            "BatchSize": 25,
            "MaximumBatchingWindowInSeconds": 1,
            "ParallelizationFactor": 1,
            "BisectBatchOnFunctionError": True,
            "MaximumRetryAttempts": 5,
            "MaximumRecordAgeInSeconds": 3600,
            "FunctionResponseTypes": ["ReportBatchItemFailures"],
            "DestinationConfig": {"OnFailure": {"Destination": (
                "arn:aws:sqs:us-east-1:114743615542:"
                "journalm8-dev-semantic-memory-dlq"
            )}},
        }
        state: dict[str, object] = {"mapping": mapping}
        state.update(changes)
        return state

    def _run_mapping_reconcile(
        self,
        state: dict[str, object],
        *,
        target_enabled: bool = False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim_dir = root / "bin"
            shim_dir.mkdir()
            aws_path = shim_dir / "aws"
            aws_path.write_text(MAPPING_AWS_SHIM, encoding="utf-8")
            aws_path.chmod(0o755)
            state_path = root / "state.json"
            log_path = root / "aws.log"
            state_path.write_text(json.dumps(state), encoding="utf-8")
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
                "#!/usr/bin/env bash\nset -euo pipefail\nsleep() { :; }\n"
                f"BUILD_DIR={str(build_dir)!r}\n"
                "AWS_PROFILE=jm8-dev\nAWS_REGION=us-east-1\n"
                "MAPPING_TARGET_STATE="
                f"{'Enabled' if target_enabled else 'Disabled'}\n"
                "WORKER_FUNCTION_NAME=journalm8-dev-semantic-memory-worker\n"
                "WORKER_ARN=arn:aws:lambda:us-east-1:114743615542:function:journalm8-dev-semantic-memory-worker\n"
                "STREAM_ARN=arn:aws:dynamodb:us-east-1:114743615542:table/journalm8-dev-main/stream/version\n"
                f"DLQ_ARN={dlq_arn!r}\n"
                f"{self.mapping_functions}\n"
                "ensure_event_source_mapping\n"
                "reconcile_event_source_mapping_state\n",
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
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            calls = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            return result, final_state, calls

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

    def test_mapping_configuration_preserves_singleton_and_batch_failures(self):
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
        self.assertIn("mapping.get(key)", self.deploy)
        self.assertIn('"State": expected_state', self.deploy)
        self.assertIn("create-event-source-mapping", self.deploy)
        self.assertIn("update-event-source-mapping", self.deploy)
        self.assertIn("len(mappings) > 1", self.deploy)
        self.assertIn("len(mappings) != 1", self.deploy)
        self.assertNotIn("event-source-mapping-updated", self.deploy)
        self.assertNotIn("event-source-mapping-disabled", self.deploy)

    def test_safe_disabled_target_succeeds_immediately(self):
        result, state, calls = self._run_mapping_reconcile(self._mapping_state())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["mapping"]["State"], "Disabled")
        state_polls = [
            call for call in calls
            if call[:2] == ["lambda", "get-event-source-mapping"]
            and "--query" in call
        ]
        full_gets = [
            call for call in calls
            if call[:2] == ["lambda", "get-event-source-mapping"]
            and "--query" not in call
        ]
        self.assertEqual(len(state_polls), 3)
        self.assertEqual(len(full_gets), 2)

    def test_mapping_poll_accepts_transitional_states_before_stability(self):
        for transitional_state in (
            "Creating",
            "Enabling",
            "Disabling",
            "Updating",
        ):
            with self.subTest(state=transitional_state):
                result, _, calls = self._run_mapping_reconcile(
                    self._mapping_state(
                        poll_states=[
                            transitional_state,
                            "Disabled",
                            "Disabled",
                            "Disabled",
                        ],
                    )
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                state_polls = [
                    call for call in calls
                    if call[:2] == ["lambda", "get-event-source-mapping"]
                    and "--query" in call
                ]
                self.assertEqual(len(state_polls), 4)

    def test_mapping_poll_fails_closed_for_invalid_states(self):
        for invalid_state in (
            "",
            "Failed",
            "Deleting",
            "UnknownState",
        ):
            with self.subTest(state=invalid_state):
                result, _, calls = self._run_mapping_reconcile(
                    self._mapping_state(poll_states=[invalid_state])
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "event-source mapping entered an invalid state",
                    result.stderr,
                )
                state_polls = [
                    call for call in calls
                    if call[:2] == ["lambda", "get-event-source-mapping"]
                    and "--query" in call
                ]
                self.assertEqual(len(state_polls), 1)

    def test_mapping_poll_exhaustion_is_bounded(self):
        result, _, calls = self._run_mapping_reconcile(
            self._mapping_state(poll_states=["Creating"])
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "event-source mapping did not become stable",
            result.stderr,
        )
        state_polls = [
            call for call in calls
            if call[:2] == ["lambda", "get-event-source-mapping"]
            and "--query" in call
        ]
        self.assertEqual(len(state_polls), 20)

    def test_mapping_lookup_aws_failures_propagate(self):
        result, _, calls = self._run_mapping_reconcile(
            self._mapping_state(poll_failure_at=1)
        )

        self.assertEqual(result.returncode, 42)
        state_polls = [
            call for call in calls
            if call[:2] == ["lambda", "get-event-source-mapping"]
            and "--query" in call
        ]
        self.assertEqual(len(state_polls), 1)

    def test_disabled_mapping_transitions_to_enabled(self):
        result, state, calls = self._run_mapping_reconcile(
            self._mapping_state(),
            target_enabled=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["mapping"]["State"], "Enabled")
        state_updates = [
            call for call in calls
            if call[:2] == ["lambda", "update-event-source-mapping"]
            and "--enabled" in call
        ]
        self.assertEqual(len(state_updates), 1)

    def test_enabled_mapping_transitions_to_disabled(self):
        result, state, calls = self._run_mapping_reconcile(
            self._mapping_state("Enabled"),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["mapping"]["State"], "Disabled")
        state_updates = [
            call for call in calls
            if call[:2] == ["lambda", "update-event-source-mapping"]
            and "--no-enabled" in call
        ]
        self.assertEqual(len(state_updates), 1)

    def test_mapping_already_at_target_is_idempotent(self):
        for mapping_state, target_enabled in (
            ("Disabled", False),
            ("Enabled", True),
        ):
            with self.subTest(state=mapping_state):
                result, state, calls = self._run_mapping_reconcile(
                    self._mapping_state(mapping_state),
                    target_enabled=target_enabled,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(state["mapping"]["State"], mapping_state)
                state_updates = [
                    call for call in calls
                    if call[:2] == ["lambda", "update-event-source-mapping"]
                    and ("--enabled" in call or "--no-enabled" in call)
                ]
                self.assertEqual(state_updates, [])

    def test_enable_poll_is_bounded_and_accepts_transitional_states(self):
        result, state, calls = self._run_mapping_reconcile(
            self._mapping_state(poll_states=[
                "Disabled",
                "Updating",
                "Disabled",
                "Disabled",
                "Enabling",
                "Updating",
                "Enabled",
            ]),
            target_enabled=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["mapping"]["State"], "Enabled")
        state_polls = [
            call for call in calls
            if call[:2] == ["lambda", "get-event-source-mapping"]
            and "--query" in call
        ]
        self.assertEqual(len(state_polls), 7)

    def test_disable_poll_accepts_disabling_and_updating_states(self):
        result, state, calls = self._run_mapping_reconcile(
            self._mapping_state("Enabled", poll_states=[
                "Enabled",
                "Enabled",
                "Enabled",
                "Disabling",
                "Updating",
                "Disabled",
            ]),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["mapping"]["State"], "Disabled")
        state_polls = [
            call for call in calls
            if call[:2] == ["lambda", "get-event-source-mapping"]
            and "--query" in call
        ]
        self.assertEqual(len(state_polls), 6)

    def test_activation_poll_exhaustion_is_bounded(self):
        result, _, calls = self._run_mapping_reconcile(
            self._mapping_state(poll_states=(
                ["Disabled", "Disabled", "Disabled"]
                + ["Enabling"] * 20
            )),
            target_enabled=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "event-source mapping did not reach its target state",
            result.stderr,
        )
        state_polls = [
            call for call in calls
            if call[:2] == ["lambda", "get-event-source-mapping"]
            and "--query" in call
        ]
        self.assertEqual(len(state_polls), 23)

    def test_mapping_update_failure_propagates_without_final_verification(self):
        result, _, calls = self._run_mapping_reconcile(
            self._mapping_state(state_update_failure=True),
            target_enabled=True,
        )

        self.assertEqual(result.returncode, 43)
        full_gets = [
            call for call in calls
            if call[:2] == ["lambda", "get-event-source-mapping"]
            and "--query" not in call
        ]
        self.assertEqual(len(full_gets), 1)

    def test_existing_disabled_partial_resources_are_reused(self):
        result, state, calls = self._run_mapping_reconcile(
            self._mapping_state()
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        operations = [call[1] for call in calls]
        self.assertNotIn("create-event-source-mapping", operations)
        self.assertEqual(operations.count("update-event-source-mapping"), 1)
        self.assertEqual(state["mapping"]["State"], "Disabled")

    def test_new_mapping_creation_always_begins_disabled_before_activation(self):
        result, state, calls = self._run_mapping_reconcile(
            {"mapping": None},
            target_enabled=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        creates = [
            call for call in calls
            if call[:2] == ["lambda", "create-event-source-mapping"]
        ]
        self.assertEqual(len(creates), 1)
        self.assertIn("--no-enabled", creates[0])
        self.assertNotIn("--enabled", creates[0])
        self.assertEqual(state["mapping"]["State"], "Enabled")
        self.assertEqual(
            state["mapping"]["FunctionResponseTypes"],
            ["ReportBatchItemFailures"],
        )

    def test_no_activation_occurs_after_an_earlier_deployment_failure(self):
        result, state, calls = self._run_mapping_reconcile(
            self._mapping_state(configuration_update_failure=True),
            target_enabled=True,
        )

        self.assertEqual(result.returncode, 44)
        self.assertEqual(state["mapping"]["State"], "Disabled")
        self.assertFalse(any(
            call[:2] == ["lambda", "update-event-source-mapping"]
            and "--enabled" in call
            for call in calls
        ))

    def test_activation_occurs_only_after_alarm_and_mapping_verification(self):
        configured_verification = self.deploy.index(
            'verify_event_source_mapping "$MAPPING_STATE" "configured"'
        )
        alarm_verification = self.deploy.index(
            '> "$BUILD_DIR/alarms.json"'
        )
        activation = self.deploy.index(
            "\nreconcile_event_source_mapping_state\n",
            alarm_verification,
        )
        self.assertLess(configured_verification, alarm_verification)
        self.assertLess(alarm_verification, activation)

    def test_final_deployed_state_is_fully_verified(self):
        result, state, calls = self._run_mapping_reconcile(
            self._mapping_state(),
            target_enabled=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["mapping"]["State"], "Enabled")
        full_gets = [
            call for call in calls
            if call[:2] == ["lambda", "get-event-source-mapping"]
            and "--query" not in call
        ]
        list_calls = [
            call for call in calls
            if call[:2] == ["lambda", "list-event-source-mappings"]
        ]
        self.assertEqual(len(full_gets), 2)
        self.assertEqual(len(list_calls), 3)

    def test_unsupported_mapping_waiter_is_absent(self):
        self.assertNotIn("event-source-mapping-disabled", self.deploy)
        self.assertNotIn("event-source-mapping-enabled", self.deploy)

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

def test_map_tag_arguments_are_single_shell_arguments():
    from pathlib import Path

    deploy = (
        Path(__file__).resolve().parents[1]
        / "bin"
        / "deploy-semantic-memory"
    ).read_text(encoding="utf-8")

    malformed = (
        '--tags App="$APP_NAME" '
        'Stage="$STAGE" ManagedBy=aws-cli'
    )
    corrected = (
        '--tags "App=${APP_NAME},'
        'Stage=${STAGE},ManagedBy=aws-cli"'
    )

    assert malformed not in deploy
    assert deploy.count(corrected) == 2
