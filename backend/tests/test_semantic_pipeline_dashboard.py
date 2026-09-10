from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
sys.path.insert(0, str(BIN_DIR))

from jm8_semantic_pipeline_dashboard import (  # noqa: E402
    DASHBOARD_VERSION,
    SemanticPipelineDashboardError,
    build_semantic_pipeline_dashboard,
    dashboard_name,
    extract_inventory,
    validate_dashboard,
)


ACCOUNT_ID = "114743615542"
REGION = "us-east-1"
TABLE_NAME = "journalm8-prod-entry-chunks"
TABLE_ARN = f"arn:aws:dynamodb:{REGION}:{ACCOUNT_ID}:table/{TABLE_NAME}"


def table_document() -> dict:
    return {
        "Table": {
            "TableName": TABLE_NAME,
            "TableArn": TABLE_ARN,
            "TableStatus": "ACTIVE",
            "ItemCount": 25,
            "VectorIndexes": [{
                "IndexName": "SemanticEmbeddingIndex",
                "IndexStatus": "ACTIVE",
                "ItemCount": 12,
            }],
        }
    }


def dashboard() -> dict:
    return build_semantic_pipeline_dashboard(
        region=REGION,
        account_id=ACCOUNT_ID,
        app_name="journalm8",
        stage="prod",
        entry_chunks_item_count=25,
        vector_index_item_count=12,
    )


class SemanticPipelineDashboardTests(unittest.TestCase):
    def test_inventory_requires_exact_active_table_and_index(self):
        self.assertEqual(
            extract_inventory(
                table_document(),
                table_name=TABLE_NAME,
                table_arn=TABLE_ARN,
            ),
            (25, 12),
        )

        for mutation in ("table", "arn", "state", "index", "index-state"):
            with self.subTest(mutation=mutation):
                document = table_document()
                table = document["Table"]
                if mutation == "table":
                    table["TableName"] = "journalm8-dev-entry-chunks"
                elif mutation == "arn":
                    table["TableArn"] = TABLE_ARN.replace("prod", "staging")
                elif mutation == "state":
                    table["TableStatus"] = "UPDATING"
                elif mutation == "index":
                    table["VectorIndexes"][0]["IndexName"] = "Other"
                else:
                    table["VectorIndexes"][0]["IndexStatus"] = "CREATING"
                with self.assertRaises(SemanticPipelineDashboardError):
                    extract_inventory(
                        document,
                        table_name=TABLE_NAME,
                        table_arn=TABLE_ARN,
                    )

    def test_dashboard_is_deterministic_and_contains_operational_views(self):
        first = dashboard()
        second = dashboard()
        self.assertEqual(first, second)
        self.assertEqual(first["start"], "-PT24H")
        self.assertEqual(len(first["widgets"]), 12)

        titles = {
            widget["properties"].get("title")
            for widget in first["widgets"]
        }
        for title in (
            "Semantic pipeline safety alarms",
            "Pipeline throughput",
            "Embedding record success rate",
            "Worker processing time",
            "Retry, backlog, and DLQ pressure",
            "Embedding failure boundary",
            "Bedrock request health",
            "Bedrock token volume and latency",
            "Bedrock account estimated charges",
            "EntryChunks DynamoDB activity",
        ):
            self.assertIn(title, titles)

        serialized = json.dumps(first, sort_keys=True)
        for required in (
            DASHBOARD_VERSION,
            "RecordCount",
            "RecordFailures",
            "MemoryReadFailures",
            "ProviderFailures",
            "PersistenceFailures",
            "ContractFailures",
            "UnexpectedFailures",
            "IteratorAge",
            "ApproximateNumberOfMessagesVisible",
            "InputTokenCount",
            "InvocationLatency",
            "EstimatedCharges",
            "ConsumedWriteCapacityUnits",
            "TransactionConflict",
            "ConditionalCheckFailedRequests",
            "WriteThrottleEvents",
        ):
            self.assertIn(required, serialized)

    def test_dashboard_has_nine_exact_alarm_arns_and_no_content_widgets(self):
        document = dashboard()
        alarm_widgets = [
            item for item in document["widgets"]
            if item["type"] == "alarm"
        ]
        self.assertEqual(len(alarm_widgets), 1)
        alarms = alarm_widgets[0]["properties"]["alarms"]
        self.assertEqual(len(alarms), 9)
        self.assertEqual(len(set(alarms)), 9)
        self.assertTrue(all(
            value.startswith(
                "arn:aws:cloudwatch:us-east-1:114743615542:"
                "alarm:journalm8-prod-semantic-"
            )
            for value in alarms
        ))
        self.assertNotIn("log", {item["type"] for item in document["widgets"]})
        serialized = json.dumps(document)
        for forbidden in ("SOURCE '", "@message", "journalText"):
            self.assertNotIn(forbidden, serialized)

    def test_success_rate_is_metric_math_not_a_claimed_inventory_ratio(self):
        document = dashboard()
        success = next(
            item for item in document["widgets"]
            if item["properties"].get("title")
            == "Embedding record success rate"
        )
        expression = success["properties"]["metrics"][0][0]
        self.assertEqual(expression["id"], "success")
        self.assertEqual(
            expression["expression"],
            "100*(records-failures)/records",
        )
        self.assertEqual(
            success["properties"]["yAxis"]["left"],
            {"min": 0, "max": 100},
        )
        header = document["widgets"][0]["properties"]["markdown"]
        self.assertIn("main table → memory worker", header)
        self.assertIn("vector index → Ask JM8", header)
        self.assertIn("approximate", header)
        self.assertIn("not presented as an exact coverage percentage", header)

    def test_bedrock_cost_is_labeled_account_wide_and_delayed(self):
        document = dashboard()
        widget = next(
            item for item in document["widgets"]
            if item["properties"].get("title")
            == "Bedrock account estimated charges"
        )
        metric = widget["properties"]["metrics"][0]
        self.assertEqual(metric[:6], [
            "AWS/Billing",
            "EstimatedCharges",
            "Currency",
            "USD",
            "ServiceName",
            "AmazonBedrock",
        ])
        self.assertIn("Account-wide", metric[-1]["label"])
        self.assertIn("delayed", metric[-1]["label"])

    def test_dashboard_validator_rejects_content_widget_and_missing_metrics(self):
        document = dashboard()
        alarms = set(document["widgets"][1]["properties"]["alarms"])
        document["widgets"][2] = {
            "type": "log",
            "properties": {"query": "SOURCE '/private' | fields @message"},
        }
        with self.assertRaises(SemanticPipelineDashboardError):
            validate_dashboard(document, expected_alarm_arns=alarms)

    def test_names_and_context_fail_closed(self):
        self.assertEqual(
            dashboard_name("journalm8", "prod"),
            "journalm8-prod-semantic-pipeline",
        )
        for field, value in (
            ("region", "invalid"),
            ("account_id", "123"),
            ("app_name", "Journal M8"),
            ("stage", "production"),
        ):
            with self.subTest(field=field):
                arguments = {
                    "region": REGION,
                    "account_id": ACCOUNT_ID,
                    "app_name": "journalm8",
                    "stage": "prod",
                    "entry_chunks_item_count": 1,
                    "vector_index_item_count": 1,
                }
                arguments[field] = value
                with self.assertRaises(SemanticPipelineDashboardError):
                    build_semantic_pipeline_dashboard(**arguments)

    def test_cli_outputs_valid_dashboard_without_private_content(self):
        with tempfile.TemporaryDirectory() as directory:
            table_path = Path(directory) / "table.json"
            table_path.write_text(json.dumps(table_document()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(BIN_DIR / "jm8_semantic_pipeline_dashboard.py"),
                    "--region",
                    REGION,
                    "--account-id",
                    ACCOUNT_ID,
                    "--app-name",
                    "journalm8",
                    "--stage",
                    "prod",
                    "--table-document",
                    str(table_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["widgets"]), 12)


class SemanticPipelineDeploymentTests(unittest.TestCase):
    def test_narrow_deployer_is_guarded_and_only_mutates_one_dashboard(self):
        script = (BIN_DIR / "deploy-semantic-observability").read_text(
            encoding="utf-8"
        )
        for required in (
            'source "$SCRIPT_DIR/jm8_deployment_guard.sh"',
            'export JM8_OPERATION="deploy-observability"',
            "jm8_validate_contract_or_exit",
            "aws sts get-caller-identity",
            "aws dynamodb describe-table",
            "aws cloudwatch put-dashboard",
            'DASHBOARD_NAME="${APP_NAME}-${STAGE}-semantic-pipeline"',
            'messages != []',
        ):
            self.assertIn(required, script)
        for forbidden in (
            "delete-dashboard",
            "delete-alarms",
            "filter-log-events",
            "start-query",
            "update-event-source-mapping",
            "bedrock-runtime",
        ):
            self.assertNotIn(forbidden, script)

    def test_general_deploy_reconciles_semantic_dashboard_last(self):
        deploy = (BIN_DIR / "deploy").read_text(encoding="utf-8")
        embedding = deploy.index("./bin/deploy-semantic-embedding")
        dashboard_call = deploy.index("./bin/deploy-semantic-observability")
        self.assertGreater(dashboard_call, embedding)


if __name__ == "__main__":
    unittest.main()
