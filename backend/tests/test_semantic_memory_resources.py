from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
sys.path.insert(0, str(BIN_DIR))


from jm8_environment_contract import (  # noqa: E402
    EnvironmentContractError,
    validate_entry_chunks_table_description,
    validate_entry_chunks_stream_description,
    validate_entry_chunks_table_pitr,
    validate_entry_chunks_table_tags,
)


AWS_SHIM = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_DDB_STATE"])
log_path = Path(os.environ["FAKE_AWS_LOG"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")

if len(args) < 2 or args[0] != "dynamodb":
    raise SystemExit("unexpected fake AWS service")
operation = args[1]

def option(name):
    return args[args.index(name) + 1]

def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")

def table_document():
    table = {
        "TableName": state["name"],
        "TableArn": state["arn"],
        "TableStatus": state["status"],
        "KeySchema": state["key_schema"],
        "AttributeDefinitions": state["attributes"],
        "BillingModeSummary": {"BillingMode": state["billing_mode"]},
        "DeletionProtectionEnabled": state["deletion_protection"],
    }
    if state.get("global_secondary_indexes"):
        table["GlobalSecondaryIndexes"] = state["global_secondary_indexes"]
    if state.get("stream_enabled"):
        table["StreamSpecification"] = {"StreamEnabled": True}
    return {"Table": table}

if operation == "describe-table":
    if not state.get("exists"):
        print("ResourceNotFoundException", file=sys.stderr)
        raise SystemExit(254)
    print(json.dumps(table_document()))
elif operation == "create-table":
    name = option("--table-name")
    region = option("--region")
    account = os.environ["FAKE_ACCOUNT_ID"]
    state.update({
        "exists": True,
        "name": name,
        "arn": f"arn:aws:dynamodb:{region}:{account}:table/{name}",
        "status": "ACTIVE",
        "key_schema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "attributes": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "billing_mode": option("--billing-mode"),
        "deletion_protection": "--deletion-protection-enabled" in args,
        "pitr": "DISABLED",
        "tags": {},
    })
    tag_start = args.index("--tags") + 1
    for value in args[tag_start:args.index("--profile")]:
        parts = dict(part.split("=", 1) for part in value.split(","))
        state["tags"][parts["Key"]] = parts["Value"]
    save()
    print(json.dumps({"TableDescription": table_document()["Table"]}))
elif operation == "wait":
    pass
elif operation == "list-tags-of-resource":
    print(json.dumps({
        "Tags": [
            {"Key": key, "Value": value}
            for key, value in sorted(state.get("tags", {}).items())
        ]
    }))
elif operation == "tag-resource":
    tag_start = args.index("--tags") + 1
    for value in args[tag_start:args.index("--profile")]:
        parts = dict(part.split("=", 1) for part in value.split(","))
        state.setdefault("tags", {})[parts["Key"]] = parts["Value"]
    save()
elif operation == "update-continuous-backups":
    state["pitr"] = "ENABLED"
    save()
elif operation == "update-table":
    state["deletion_protection"] = True
    state["status"] = "ACTIVE"
    save()
elif operation == "describe-continuous-backups":
    if "--query" in args:
        print(state.get("pitr", "DISABLED"))
    else:
        print(json.dumps({
            "ContinuousBackupsDescription": {
                "PointInTimeRecoveryDescription": {
                    "PointInTimeRecoveryStatus": state.get("pitr", "DISABLED")
                }
            }
        }))
else:
    raise SystemExit(f"unexpected fake AWS operation: {operation}")
'''


class SemanticMemoryResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.create_resources = (BIN_DIR / "create-resources").read_text(
            encoding="utf-8"
        )
        start = cls.create_resources.index("ensure_entry_chunks_table() {")
        end = cls.create_resources.index(
            "\n}\n\nensure_main_table_stream", start
        ) + 3
        cls.function_source = cls.create_resources[start:end]

    @staticmethod
    def _compatible_state(**changes):
        state = {
            "exists": True,
            "name": "journalm8-dev-entry-chunks",
            "arn": (
                "arn:aws:dynamodb:us-east-1:114743615542:"
                "table/journalm8-dev-entry-chunks"
            ),
            "status": "ACTIVE",
            "key_schema": [
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            "attributes": [
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            "billing_mode": "PAY_PER_REQUEST",
            "deletion_protection": True,
            "pitr": "ENABLED",
            "tags": {
                "App": "journalm8",
                "Stage": "dev",
                "ManagedBy": "aws-cli",
            },
        }
        state.update(changes)
        return state

    def _run(self, state):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim_dir = root / "bin"
            shim_dir.mkdir()
            aws_path = shim_dir / "aws"
            aws_path.write_text(AWS_SHIM, encoding="utf-8")
            aws_path.chmod(0o755)
            state_path = root / "state.json"
            log_path = root / "aws.log"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            runner = root / "run.sh"
            runner.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"SCRIPT_DIR={str(BIN_DIR)!r}\n"
                "AWS_PROFILE=jm8-dev\n"
                "AWS_REGION=us-east-1\n"
                "APP_NAME=journalm8\n"
                "STAGE=dev\n"
                "ACCOUNT_ID=114743615542\n"
                "ENTRY_CHUNKS_TABLE_NAME=journalm8-dev-entry-chunks\n"
                "mkdir -p .build\n"
                f"{self.function_source}\n"
                "ensure_entry_chunks_table\n",
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment.update({
                "PATH": f"{shim_dir}:{environment['PATH']}",
                "FAKE_DDB_STATE": str(state_path),
                "FAKE_AWS_LOG": str(log_path),
                "FAKE_ACCOUNT_ID": "114743615542",
            })
            result = subprocess.run(
                ["bash", str(runner)],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            calls = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ] if log_path.exists() else []
            return result, final_state, calls

    def test_creation_uses_exact_schema_billing_and_tags(self):
        result, state, calls = self._run({"exists": False})

        self.assertEqual(result.returncode, 0, result.stderr)
        create = next(call for call in calls if call[1] == "create-table")
        self.assertEqual(
            create[create.index("--table-name") + 1],
            "journalm8-dev-entry-chunks",
        )
        self.assertEqual(
            create[create.index("--billing-mode") + 1],
            "PAY_PER_REQUEST",
        )
        self.assertEqual(
            create[create.index("--attribute-definitions") + 1 : create.index("--key-schema")],
            ["AttributeName=PK,AttributeType=S", "AttributeName=SK,AttributeType=S"],
        )
        self.assertEqual(
            create[create.index("--key-schema") + 1 : create.index("--billing-mode")],
            ["AttributeName=PK,KeyType=HASH", "AttributeName=SK,KeyType=RANGE"],
        )
        self.assertEqual(
            state["tags"],
            {"App": "journalm8", "Stage": "dev", "ManagedBy": "aws-cli"},
        )
        self.assertTrue(state["deletion_protection"])
        self.assertEqual(state["pitr"], "ENABLED")

    def test_compatible_table_is_reused_and_hardening_is_idempotent(self):
        initial = self._compatible_state(
            deletion_protection=False,
            pitr="DISABLED",
            tags={"App": "journalm8", "Stage": "dev"},
        )
        first, state, first_calls = self._run(initial)
        second, second_state, second_calls = self._run(state)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("create-table", {call[1] for call in first_calls})
        self.assertNotIn("create-table", {call[1] for call in second_calls})
        self.assertTrue(second_state["deletion_protection"])
        self.assertEqual(second_state["pitr"], "ENABLED")
        self.assertEqual(second_state["tags"]["ManagedBy"], "aws-cli")
        self.assertIn("update-continuous-backups", {call[1] for call in first_calls})
        self.assertIn("update-table", {call[1] for call in first_calls})

    def test_incompatible_existing_table_fails_before_mutation(self):
        incompatible = (
            self._compatible_state(key_schema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
            ]),
            self._compatible_state(billing_mode="PROVISIONED"),
            self._compatible_state(status="UPDATING"),
            self._compatible_state(tags={"App": "journalm8", "Stage": "prod"}),
            self._compatible_state(arn=(
                "arn:aws:dynamodb:us-east-1:999999999999:"
                "table/journalm8-dev-entry-chunks"
            )),
        )
        mutating = {
            "create-table",
            "tag-resource",
            "update-continuous-backups",
            "update-table",
        }
        for state in incompatible:
            with self.subTest(state=state):
                result, _, calls = self._run(state)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(mutating.isdisjoint(call[1] for call in calls))

    def test_description_tags_and_pitr_validators_fail_closed(self):
        state = self._compatible_state()
        description = {
            "Table": {
                "TableName": state["name"],
                "TableArn": state["arn"],
                "TableStatus": state["status"],
                "KeySchema": state["key_schema"],
                "AttributeDefinitions": state["attributes"],
                "BillingModeSummary": {"BillingMode": state["billing_mode"]},
                "DeletionProtectionEnabled": True,
            }
        }
        validate_entry_chunks_table_description(
            description,
            app_name="journalm8",
            stage="dev",
            account_id="114743615542",
            region="us-east-1",
            table_name="journalm8-dev-entry-chunks",
            require_deletion_protection=True,
        )
        validate_entry_chunks_table_tags(
            {"Tags": [
                {"Key": "App", "Value": "journalm8"},
                {"Key": "Stage", "Value": "dev"},
                {"Key": "ManagedBy", "Value": "aws-cli"},
            ]},
            app_name="journalm8",
            stage="dev",
            before_reconcile=False,
        )
        validate_entry_chunks_table_pitr({
            "ContinuousBackupsDescription": {
                "PointInTimeRecoveryDescription": {
                    "PointInTimeRecoveryStatus": "ENABLED"
                }
            }
        })

        with self.assertRaises(EnvironmentContractError):
            validate_entry_chunks_table_description(
                {**description, "Table": {**description["Table"], "GlobalSecondaryIndexes": [{}]}},
                app_name="journalm8",
                stage="dev",
                account_id="114743615542",
                region="us-east-1",
                table_name="journalm8-dev-entry-chunks",
            )
        with self.assertRaises(EnvironmentContractError):
            validate_entry_chunks_table_pitr({
                "ContinuousBackupsDescription": {
                    "PointInTimeRecoveryDescription": {
                        "PointInTimeRecoveryStatus": "DISABLED"
                    }
                }
            })

    def test_entry_chunks_description_accepts_only_the_embedding_stream(self):
        state = self._compatible_state()
        table = {
            "TableName": state["name"],
            "TableArn": state["arn"],
            "TableStatus": state["status"],
            "KeySchema": state["key_schema"],
            "AttributeDefinitions": state["attributes"],
            "BillingModeSummary": {"BillingMode": state["billing_mode"]},
            "StreamSpecification": {
                "StreamEnabled": True,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            },
            "LatestStreamArn": state["arn"] + "/stream/version",
        }
        document = {"Table": table}
        self.assertEqual(
            validate_entry_chunks_stream_description(
                document,
                app_name="journalm8",
                stage="dev",
                account_id="114743615542",
                region="us-east-1",
                table_name="journalm8-dev-entry-chunks",
                require_stream=True,
            ),
            "REUSE",
        )
        for invalid in ("KEYS_ONLY", "NEW_IMAGE", "OLD_IMAGE"):
            with self.subTest(stream_view_type=invalid):
                broken = {"Table": {
                    **table,
                    "StreamSpecification": {
                        "StreamEnabled": True,
                        "StreamViewType": invalid,
                    },
                }}
                with self.assertRaises(EnvironmentContractError):
                    validate_entry_chunks_stream_description(
                        broken,
                        app_name="journalm8",
                        stage="dev",
                        account_id="114743615542",
                        region="us-east-1",
                        table_name="journalm8-dev-entry-chunks",
                        require_stream=False,
                    )

    def test_entry_chunks_provisioner_has_no_forbidden_features_or_operations(self):
        normalized = self.function_source.lower()
        for forbidden in (
            "global-secondary-indexes",
            "local-secondary-indexes",
            "vector",
            "stream-specification",
            "time-to-live",
            "put-item",
            "batch-write-item",
            "scan",
            "delete-table",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, normalized)


if __name__ == "__main__":
    unittest.main()
