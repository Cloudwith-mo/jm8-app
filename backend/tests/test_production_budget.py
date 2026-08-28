from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "bin" / "deploy-production-budget"
)
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")


def heredoc_after(marker: str) -> str:
    start = SCRIPT.index(marker) + len(marker)
    end = SCRIPT.index("\nPY\n", start)
    return SCRIPT[start:end]


class ProductionBudgetTests(unittest.TestCase):
    def test_script_is_executable_strict_and_guarded_before_aws(self):
        self.assertTrue(os.access(SCRIPT_PATH, os.X_OK))
        self.assertTrue(SCRIPT_PATH.stat().st_mode & 0o111)
        self.assertIn("set -euo pipefail", SCRIPT)
        for variable in (
            "AWS_PROFILE",
            "AWS_REGION",
            "APP_NAME",
            "STAGE",
            "EXPECTED_AWS_ACCOUNT_ID",
        ):
            self.assertIn(f': "${{{variable}:?', SCRIPT)
        self.assertIn('source "$SCRIPT_DIR/jm8_deployment_guard.sh"', SCRIPT)
        guard = SCRIPT.index("jm8_validate_contract_or_exit")
        self.assertLess(guard, SCRIPT.index("aws sts get-caller-identity"))
        self.assertLess(guard, SCRIPT.index("aws sns create-topic"))

    def test_default_limit_and_stage_scoped_budget_name(self):
        self.assertIn(
            'MONTHLY_LIMIT="${PRODUCTION_MONTHLY_BUDGET_USD:-50}"',
            SCRIPT,
        )
        self.assertIn(
            'BUDGET_NAME="${APP_NAME}-${STAGE}-production-monthly"',
            SCRIPT,
        )
        self.assertIn('TOPIC_NAME="${APP_NAME}-${STAGE}-alerts"', SCRIPT)

    def test_limit_validation_rejects_invalid_and_non_positive_values(self):
        code = heredoc_after('python3 - "$MONTHLY_LIMIT" <<\'PY\'\n')
        for value in ("not-a-number", "0", "-1", "NaN", "Infinity"):
            with self.subTest(value=value):
                result = subprocess.run(
                    [sys.executable, "-", value],
                    input=code,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("PRODUCTION_MONTHLY_BUDGET_USD", result.stderr)

        result = subprocess.run(
            [sys.executable, "-", "50.25"],
            input=code,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_budget_covers_total_account_cost_without_service_filters(self):
        marker = (
            '  "$BUILD_DIR/budget.json" \\\n'
            '  "$BUDGET_NAME" \\\n'
            '  "$MONTHLY_LIMIT" <<\'PY\'\n'
        )
        code = heredoc_after(marker)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "budget.json"
            result = subprocess.run(
                [sys.executable, "-", str(output), "journalm8-prod-production-monthly", "50"],
                input=code,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            budget = json.loads(output.read_text())

        self.assertEqual(budget["BudgetName"], "journalm8-prod-production-monthly")
        self.assertEqual(budget["BudgetLimit"], {"Amount": "50", "Unit": "USD"})
        self.assertEqual(budget["BudgetType"], "COST")
        self.assertEqual(budget["TimeUnit"], "MONTHLY")
        self.assertNotIn("CostFilters", budget)
        self.assertNotIn("Amazon Bedrock", SCRIPT)

    def test_sns_policy_merge_preserves_existing_statements(self):
        marker = '  "$ACCOUNT_ID" <<\'PY\'\n'
        code = heredoc_after(marker)
        original = {
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "KeepExistingControl",
                "Effect": "Allow",
                "Principal": {"Service": "cloudwatch.amazonaws.com"},
                "Action": "SNS:Publish",
                "Resource": "topic-arn",
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "current.json"
            output = Path(directory) / "updated.json"
            source.write_text(json.dumps(original))
            result = subprocess.run(
                [
                    sys.executable,
                    "-",
                    str(source),
                    str(output),
                    "arn:aws:sns:us-east-1:123456789012:journalm8-prod-alerts",
                    "123456789012",
                ],
                input=code,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            policy = json.loads(output.read_text())

        statements = policy["Statement"]
        self.assertEqual(statements[0], original["Statement"][0])
        budget_statements = [
            item for item in statements
            if item.get("Sid") == "AllowJM8BudgetsToPublish"
        ]
        self.assertEqual(len(budget_statements), 1)
        self.assertEqual(
            budget_statements[0]["Principal"],
            {"Service": "budgets.amazonaws.com"},
        )

    def test_budget_and_notifications_are_idempotently_reconciled(self):
        self.assertIn("aws budgets describe-budget", SCRIPT)
        self.assertIn("aws budgets update-budget", SCRIPT)
        self.assertIn("aws budgets create-budget", SCRIPT)
        self.assertIn("aws budgets describe-notifications-for-budget", SCRIPT)
        self.assertEqual(SCRIPT.count("aws budgets create-notification"), 1)
        self.assertIn('if [ "$EXISTS" = "yes" ]; then', SCRIPT)
        self.assertIn("continue", SCRIPT)

        for specification in (
            '"ACTUAL:50"',
            '"ACTUAL:80"',
            '"ACTUAL:100"',
            '"FORECASTED:100"',
        ):
            self.assertIn(specification, SCRIPT)
        self.assertIn("ThresholdType=PERCENTAGE", SCRIPT)

        code = heredoc_after('      "$THRESHOLD" <<\'PY\'\n')
        notifications = {
            "Notifications": [{
                "NotificationType": "ACTUAL",
                "ComparisonOperator": "GREATER_THAN",
                "Threshold": 80,
                "ThresholdType": "PERCENTAGE",
            }]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.json"
            path.write_text(json.dumps(notifications))
            existing = subprocess.run(
                [sys.executable, "-", str(path), "ACTUAL", "80"],
                input=code,
                text=True,
                capture_output=True,
                check=False,
            )
            missing = subprocess.run(
                [sys.executable, "-", str(path), "FORECASTED", "100"],
                input=code,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(existing.stdout.strip(), "yes")
        self.assertEqual(missing.stdout.strip(), "no")

    def test_generated_files_are_isolated_and_no_sensitive_values_are_embedded(self):
        self.assertIn('BUILD_DIR=".build/production-budget"', SCRIPT)
        for generated_name in (
            "current-topic-policy.json",
            "updated-topic-policy.json",
            "budget.json",
            "current-notifications.json",
        ):
            self.assertIn(f'"$BUILD_DIR/{generated_name}"', SCRIPT)

        for forbidden in (
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "SECRET_KEY",
            "STRIPE_SECRET",
            "whsec_",
            "sk_live_",
            "sk_test_",
        ):
            self.assertNotIn(forbidden, SCRIPT)
        self.assertIsNone(
            re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", SCRIPT)
        )
        self.assertNotIn("print(json.dumps(policy", SCRIPT)


if __name__ == "__main__":
    unittest.main()
