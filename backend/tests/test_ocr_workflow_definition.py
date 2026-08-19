import json
import contextlib
import io
import re
import sys
import unittest
from pathlib import Path


DEPLOY_SCRIPT = Path(__file__).parents[1] / "bin" / "deploy-ocr-workflow"


class OcrWorkflowDefinitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        definition_path = (
            Path(__file__).parents[1] / "infra" / "ocr-workflow.asl.json"
        )
        cls.script = DEPLOY_SCRIPT.read_text()
        cls.definition = json.loads(definition_path.read_text())

    def test_retryable_worker_errors_use_bounded_backoff(self):
        retries = self.definition["States"]["RunOcrWorker"]["Retry"]
        retry = next(
            item
            for item in retries
            if "OcrRetryableError" in item["ErrorEquals"]
        )

        self.assertEqual(retry["MaxAttempts"], 3)
        self.assertEqual(retry["BackoffRate"], 2)
        self.assertEqual(retry["JitterStrategy"], "FULL")

    def test_all_terminal_failures_are_recorded(self):
        worker = self.definition["States"]["RunOcrWorker"]
        self.assertEqual(worker["Catch"][0]["ErrorEquals"], ["States.ALL"])
        self.assertEqual(worker["Catch"][0]["Next"], "RecordWorkflowFailure")

    def test_step_functions_log_delivery_permissions_are_explicit(self):
        for action in (
            "logs:CreateLogDelivery",
            "logs:GetLogDelivery",
            "logs:UpdateLogDelivery",
            "logs:DeleteLogDelivery",
            "logs:ListLogDeliveries",
            "logs:PutResourcePolicy",
            "logs:DescribeResourcePolicies",
            "logs:DescribeLogGroups",
        ):
            self.assertIn(action, self.script)
        self.assertIn('"Resource": "*"', self.script)
        self.assertIn("do not support resource-level", self.script)
        self.assertNotIn('"logs:*"', self.script)
        self.assertNotIn("AdministratorAccess", self.script)

    def test_log_group_arn_is_canonical_for_step_functions(self):
        self.assertIn("logGroupArn:logGroupArn", self.script)
        self.assertIn('fields.get("logGroupArn") or fields.get("arn")', self.script)
        self.assertIn('raw_arn[:-2] if raw_arn.endswith(":*") else raw_arn', self.script)
        self.assertIn('match.group("name") != expected_name', self.script)
        self.assertIn('match.group("region") != expected_region', self.script)
        self.assertIn('match.group("account") != expected_account', self.script)
        self.assertIn('STATE_LOG_GROUP_DESTINATION_ARN="${STATE_LOG_GROUP_BASE_ARN}:*"', self.script)
        self.assertIn("logGroupArn=${STATE_LOG_GROUP_DESTINATION_ARN}", self.script)
        self.assertNotIn("STATE_LOG_GROUP_DESTINATION_ARN=\"${STATE_LOG_GROUP_DESTINATION_ARN}:*\"", self.script)

    def test_execution_data_logging_is_disabled(self):
        self.assertIn("level=ERROR,includeExecutionData=false", self.script)
        self.assertNotIn("includeExecutionData=true", self.script)
        self.assertIn('logging.get("level") != "ERROR"', self.script)
        self.assertIn('logging.get("includeExecutionData") is not False', self.script)
        self.assertIn('actual != sys.argv[3]', self.script)
        self.assertIn('"$STATE_MACHINE_DESCRIPTION" "$STATE_ROLE_ARN" "$STATE_LOG_GROUP_DESTINATION_ARN"', self.script)

    def test_sensitive_workflow_data_is_not_configured_for_logging(self):
        self.assertNotIn("includeExecutionData=true", self.script)
        self.assertNotIn("States.Input", self.script)
        self.assertNotIn("States.Output", self.script)

    def test_reconciles_policy_before_state_machine(self):
        policy_index = self.script.index("aws iam put-role-policy")
        create_index = self.script.index("aws stepfunctions create-state-machine")
        self.assertLess(policy_index, create_index)
        self.assertIn("aws iam get-role-policy", self.script)
        self.assertIn("describe-state-machine", self.script)

    def test_lambda_permissions_remain_stage_scoped(self):
        self.assertIn('"lambda:InvokeFunction"', self.script)
        self.assertIn("worker_arn", self.script)
        self.assertIn("failure_handler_arn", self.script)
        self.assertNotIn('"lambda:*"', self.script)

    def test_lookup_failures_are_not_treated_as_missing(self):
        self.assertIn("LOG_GROUP_LOOKUP_STATUS", self.script)
        self.assertIn("CloudWatch log group lookup failed", self.script)
        self.assertIn("STATE_ROLE_LOOKUP_STATUS", self.script)
        self.assertNotIn("|| true", self.script)

    def test_every_python_heredoc_compiles_at_column_zero(self):
        blocks = re.findall(
            r"<<'(?P<tag>PY[0-9A-Z_]*)'\n(?P<body>.*?)\n(?P=tag)",
            self.script,
            flags=re.DOTALL,
        )
        self.assertEqual(
            [tag for tag, _ in blocks],
            ["PY_LOG_GROUP", "PY", "PY", "PY", "PY_VERIFY"],
        )
        for tag, body in blocks:
            with self.subTest(heredoc=tag):
                compile(body, f"deploy-ocr-workflow:{tag}", "exec")
                first_line = next(line for line in body.splitlines() if line.strip())
                self.assertFalse(first_line[0].isspace())

    def _run_log_group_parser(self, fields):
        match = re.search(
            r"<<'PY_LOG_GROUP'\n(?P<body>.*?)\nPY_LOG_GROUP",
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group("body")
        original_argv = sys.argv
        output = io.StringIO()
        sys.argv = [
            "deploy-ocr-workflow:PY_LOG_GROUP",
            json.dumps(fields),
            "/aws/vendedlogs/states/journalm8-staging-ocr-workflow",
            "us-east-1",
            "111122223333",
        ]
        try:
            with contextlib.redirect_stdout(output):
                exec(compile(body, "PY_LOG_GROUP", "exec"), {})
        finally:
            sys.argv = original_argv
        return output.getvalue().strip()

    def test_log_group_parser_prefers_log_group_arn(self):
        expected = "arn:aws:logs:us-east-1:111122223333:log-group:/aws/vendedlogs/states/journalm8-staging-ocr-workflow"
        actual = self._run_log_group_parser({
            "logGroupName": "/aws/vendedlogs/states/journalm8-staging-ocr-workflow",
            "logGroupArn": expected,
            "arn": expected + ":unrelated",
        })
        self.assertEqual(actual, expected)
        self.assertFalse(actual.endswith(":*"))

    def test_log_group_parser_canonicalizes_legacy_arn_suffix_only(self):
        expected = "arn:aws:logs:us-east-1:111122223333:log-group:/aws/vendedlogs/states/journalm8-staging-ocr-workflow"
        self.assertEqual(
            self._run_log_group_parser({
                "logGroupName": "/aws/vendedlogs/states/journalm8-staging-ocr-workflow",
                "arn": expected + ":*",
            }),
            expected,
        )

    def test_step_functions_destination_reconstructs_exactly_one_suffix(self):
        base = "arn:aws:logs:us-east-1:111122223333:log-group:/aws/vendedlogs/states/journalm8-staging-ocr-workflow"
        destination = base + ":*"
        self.assertTrue(destination.endswith(":*"))
        self.assertFalse(destination.endswith(":*:*") )
        self.assertEqual(destination.count(":*"), 1)
        self.assertIn('STATE_LOG_GROUP_DESTINATION_ARN="${STATE_LOG_GROUP_BASE_ARN}:*"', self.script)

    def test_log_group_parser_rejects_missing_group_and_malformed_arn(self):
        cases = (
            {"logGroupName": "/other", "arn": "arn:aws:logs:us-east-1:111122223333:log-group:/other"},
            {"logGroupName": "/aws/vendedlogs/states/journalm8-staging-ocr-workflow", "arn": "not-an-arn"},
        )
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(SystemExit):
                self._run_log_group_parser(fields)


if __name__ == "__main__":
    unittest.main()
