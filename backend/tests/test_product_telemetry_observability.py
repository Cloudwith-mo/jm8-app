from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
FUNCTION_DIR = BACKEND_ROOT / "function"
sys.path.insert(0, str(BIN_DIR))
sys.path.insert(0, str(FUNCTION_DIR))

from jm8_product_telemetry_dashboard import (  # noqa: E402
    DASHBOARD_VERSION,
    ProductTelemetryDashboardError,
    alarm_arn,
    alarm_name,
    build_product_telemetry_dashboard,
    dashboard_name,
    validate_dashboard,
)
from product_telemetry_contract import ALL_METRICS, metric_namespace  # noqa: E402


ACCOUNT_ID = "114743615542"
REGION = "us-east-1"
APP_NAME = "journalm8"
STAGE = "staging"


def dashboard(stage: str = STAGE) -> dict:
    return build_product_telemetry_dashboard(
        region=REGION,
        account_id=ACCOUNT_ID,
        app_name=APP_NAME,
        stage=stage,
    )


class ProductTelemetryDashboardTests(unittest.TestCase):
    def test_names_are_exact_and_stage_scoped(self):
        self.assertEqual(
            dashboard_name(APP_NAME, STAGE),
            "journalm8-staging-alpha-telemetry",
        )
        self.assertEqual(
            alarm_name(APP_NAME, STAGE),
            "journalm8-staging-product-telemetry-emission-failures",
        )
        self.assertEqual(
            alarm_arn(REGION, ACCOUNT_ID, APP_NAME, STAGE),
            "arn:aws:cloudwatch:us-east-1:114743615542:"
            "alarm:journalm8-staging-product-telemetry-emission-failures",
        )
        self.assertNotEqual(
            metric_namespace("staging"),
            metric_namespace("prod"),
        )

    def test_dashboard_is_deterministic_complete_and_content_free(self):
        document = dashboard()
        self.assertEqual(document, dashboard())
        self.assertEqual(len(document["widgets"]), 8)
        self.assertEqual(document["start"], "-P14D")
        self.assertIn(
            DASHBOARD_VERSION,
            document["widgets"][0]["properties"]["markdown"],
        )
        alarms = [
            value
            for widget in document["widgets"]
            if widget["type"] == "alarm"
            for value in widget["properties"]["alarms"]
        ]
        self.assertEqual(alarms, [
            alarm_arn(REGION, ACCOUNT_ID, APP_NAME, STAGE)
        ])
        metrics = [
            metric
            for widget in document["widgets"]
            if widget["type"] == "metric"
            for metric in widget["properties"]["metrics"]
        ]
        self.assertEqual({metric[1] for metric in metrics}, set(ALL_METRICS))
        self.assertEqual(len(metrics), len(ALL_METRICS))
        self.assertTrue(all(
            metric[0] == "JM8/staging/Product" and len(metric) == 3
            for metric in metrics
        ))
        self.assertTrue(all(
            "dimensions" not in {
                key.casefold() for key in metric[2]
            }
            for metric in metrics
        ))
        serialized = json.dumps(document).casefold()
        for forbidden in (
            "userid", "entryid", "email", "journaltext", "rawtext",
            "cleantext", "@message", "loggroupnames", "source '",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_validator_rejects_wrong_namespace_dimension_log_and_metric_loss(self):
        expected_alarm = alarm_arn(
            REGION, ACCOUNT_ID, APP_NAME, STAGE
        )
        for mutation in ("namespace", "dimension", "log", "missing"):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(dashboard())
                metric_widget = next(
                    item for item in document["widgets"]
                    if item["type"] == "metric"
                )
                if mutation == "namespace":
                    metric_widget["properties"]["metrics"][0][0] = (
                        "JM8/prod/Product"
                    )
                elif mutation == "dimension":
                    metric_widget["properties"]["metrics"][0][2][
                        "Dimensions"
                    ] = [{"Name": "UserId", "Value": "private"}]
                elif mutation == "log":
                    document["widgets"][2] = {
                        "type": "log",
                        "properties": {
                            "query": "SOURCE '/private' | fields @message"
                        },
                    }
                else:
                    metric_widget["properties"]["metrics"].pop()
                with self.assertRaises(ProductTelemetryDashboardError):
                    validate_dashboard(
                        document,
                        expected_namespace=metric_namespace(STAGE),
                        expected_alarm_arn=expected_alarm,
                    )

    def test_context_validation_fails_closed(self):
        for field, value in (
            ("region", "invalid"),
            ("account_id", "123"),
            ("app_name", "Journal M8"),
            ("stage", "production"),
        ):
            arguments = {
                "region": REGION,
                "account_id": ACCOUNT_ID,
                "app_name": APP_NAME,
                "stage": STAGE,
            }
            arguments[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ProductTelemetryDashboardError):
                    build_product_telemetry_dashboard(**arguments)

    def test_cli_outputs_valid_dashboard(self):
        result = subprocess.run(
            [
                sys.executable,
                str(BIN_DIR / "jm8_product_telemetry_dashboard.py"),
                "--region", REGION,
                "--account-id", ACCOUNT_ID,
                "--app-name", APP_NAME,
                "--stage", STAGE,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["widgets"]), 8)


class ProductTelemetryDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.script = (
            BIN_DIR / "deploy-product-telemetry-observability"
        ).read_text(encoding="utf-8")

    def test_deployer_is_guarded_exact_and_dimensionless(self):
        for required in (
            'source "$SCRIPT_DIR/jm8_deployment_guard.sh"',
            'export JM8_OPERATION="deploy-observability"',
            "jm8_validate_contract_or_exit",
            "aws sts get-caller-identity",
            "aws sns get-topic-attributes",
            "aws cloudwatch put-metric-alarm",
            "--metric-name TelemetryEmissionFailed",
            '--namespace "$NAMESPACE"',
            "--treat-missing-data notBreaching",
            '--alarm-actions "$TOPIC_ARN"',
            "aws cloudwatch describe-alarms",
            "aws cloudwatch put-dashboard",
            'NAMESPACE="JM8/${STAGE}/Product"',
            'DASHBOARD_NAME="${APP_NAME}-${STAGE}-alpha-telemetry"',
        ):
            self.assertIn(required, self.script)
        self.assertNotIn("--dimensions", self.script)
        for forbidden in (
            "list-topics", "get-dashboard", "delete-dashboard",
            "delete-alarms", "filter-log-events", "start-query",
            "update-event-source-mapping", "bedrock-runtime", "|| true",
        ):
            self.assertNotIn(forbidden, self.script)

    def test_general_deploy_reconciles_product_observability_last(self):
        deploy = (BIN_DIR / "deploy").read_text(encoding="utf-8")
        semantic = deploy.index("./bin/deploy-semantic-observability")
        product = deploy.index(
            "./bin/deploy-product-telemetry-observability"
        )
        self.assertGreater(product, semantic)

    def test_lambda_package_includes_telemetry_modules(self):
        build = (BIN_DIR / "build").read_text(encoding="utf-8")
        package = (BIN_DIR / "package").read_text(encoding="utf-8")
        deploy = (BIN_DIR / "deploy").read_text(encoding="utf-8")
        self.assertIn("cp function/*.py .build/function/", build)
        self.assertIn(".build/function", package)
        for module in (
            "product_telemetry_contract.py",
            "product_telemetry.py",
            "product_telemetry_store.py",
            "product_telemetry_integration.py",
        ):
            self.assertTrue((FUNCTION_DIR / module).is_file())
        self.assertLess(
            deploy.index("./bin/package"),
            deploy.index("aws lambda update-function-code"),
        )

    def test_runtime_and_production_deployer_iam_cover_exact_operations(self):
        resources = (BIN_DIR / "create-resources").read_text(
            encoding="utf-8"
        )
        self.assertIn('"dynamodb:PutItem"', resources)
        self.assertIn(
            '"arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/${TABLE_NAME}"',
            resources,
        )
        sys.path.insert(0, str(BIN_DIR))
        from jm8_production_deployer_policies import generate_policies

        policies = generate_policies(REGION)
        statements = [
            statement
            for policy in policies.values()
            for statement in policy["Statement"]
        ]
        alarm_statement = next(
            item for item in statements
            if item["Sid"] == "ManageProductionAlarms"
        )
        self.assertIn("cloudwatch:PutMetricAlarm", alarm_statement["Action"])
        self.assertEqual(
            alarm_statement["Resource"],
            "arn:aws:cloudwatch:us-east-1:114743615542:"
            "alarm:journalm8-prod-*",
        )
        dashboard_statement = next(
            item for item in statements
            if item["Sid"] == "ManageProductionDashboards"
        )
        self.assertIn("cloudwatch:PutDashboard", dashboard_statement["Action"])
        self.assertEqual(
            dashboard_statement["Resource"],
            "arn:aws:cloudwatch::114743615542:"
            "dashboard/journalm8-prod-*",
        )


if __name__ == "__main__":
    unittest.main()
