import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest


BACKEND_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

HISTORICAL_DEPLOY_SCRIPT = (
    BACKEND_ROOT
    / "bin"
    / "deploy-historical-reanalysis-workflow"
)


def read_backend_file(
    relative_path: str,
) -> str:
    return (
        BACKEND_ROOT
        .joinpath(relative_path)
        .read_text()
    )


class HistoricalReanalysisDeploymentTests(
    unittest.TestCase
):
    def test_api_deploy_resolves_workflow(
        self,
    ):
        script = read_backend_file(
            "bin/deploy"
        )

        self.assertIn(
            (
                "HISTORICAL_REANALYSIS_"
                "WORKFLOW_NAME"
            ),
            script,
        )
        self.assertIn(
            (
                "HISTORICAL_REANALYSIS_"
                "WORKFLOW_ARN"
            ),
            script,
        )
        self.assertIn(
            (
                "start-historical-"
                "reanalysis-workflow"
            ),
            script,
        )
        self.assertIn(
            "states:StartExecution",
            script,
        )

    def test_api_environment_has_workflow_arn(
        self,
    ):
        script = read_backend_file(
            "bin/deploy"
        )

        self.assertIn(
            (
                "export "
                "HISTORICAL_REANALYSIS_"
                "WORKFLOW_ARN"
            ),
            script,
        )

        environment_block = (
            script
            .split(
                "environment_keys = [",
                1,
            )[1]
            .split(
                "]\n\nvariables",
                1,
            )[0]
        )

        self.assertIn(
            (
                '"HISTORICAL_REANALYSIS_'
                'WORKFLOW_ARN"'
            ),
            environment_block,
        )

        self.assertEqual(
            script.count(
                (
                    "--environment "
                    "file://.build/"
                    "api-environment.json"
                )
            ),
            2,
        )


    LOG_GROUP_NAME = (
        "/aws/vendedlogs/states/"
        "journalm8-staging-"
        "historical-reanalysis-workflow"
    )
    LOG_GROUP_BASE_ARN = (
        "arn:aws:logs:us-east-1:111122223333:"
        "log-group:"
        + LOG_GROUP_NAME
    )
    STATE_ROLE_ARN = (
        "arn:aws:iam::111122223333:role/"
        "journalm8-staging-historical-reanalysis-step-role"
    )

    @classmethod
    def setUpClass(cls):
        cls.script = HISTORICAL_DEPLOY_SCRIPT.read_text()

    def _heredoc(self, tag):
        match = re.search(
            rf"<<'{re.escape(tag)}'\n(?P<body>.*?)\n{re.escape(tag)}",
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        return match.group("body")

    def _run_log_group_parser(self, fields):
        body = self._heredoc("PY_LOG_GROUP")
        original_argv = sys.argv
        output = io.StringIO()
        sys.argv = [
            "deploy-historical-reanalysis-workflow:PY_LOG_GROUP",
            json.dumps(fields),
            self.LOG_GROUP_NAME,
            "aws",
            "us-east-1",
            "111122223333",
        ]
        try:
            with contextlib.redirect_stdout(output):
                exec(compile(body, "PY_LOG_GROUP", "exec"), {})
        finally:
            sys.argv = original_argv
        return output.getvalue().strip()

    def _run_log_group_lookup_parser(self, response):
        body = self._heredoc("PY_LOG_GROUP_LOOKUP")
        original_argv = sys.argv
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            response_path = Path(directory) / "response.json"
            response_path.write_text(json.dumps(response))
            sys.argv = [
                "deploy-historical-reanalysis-workflow:PY_LOG_GROUP_LOOKUP",
                str(response_path),
                self.LOG_GROUP_NAME,
            ]
            try:
                with contextlib.redirect_stdout(output):
                    exec(
                        compile(body, "PY_LOG_GROUP_LOOKUP", "exec"),
                        {},
                    )
            finally:
                sys.argv = original_argv
        return output.getvalue().strip()

    def _state_policy(self):
        match = re.search(
            (
                r'"\$STATE_POLICY_FILE" <<\'PY\'\n'
                r"(?P<body>.*?)\nPY"
            ),
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        original_argv = sys.argv
        worker_arn = (
            "arn:aws:lambda:us-east-1:111122223333:function:"
            "journalm8-staging-historical-reanalysis-worker"
        )
        coordinator_arn = (
            "arn:aws:lambda:us-east-1:111122223333:function:"
            "journalm8-staging-historical-reanalysis-coordinator"
        )
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            sys.argv = [
                "deploy-historical-reanalysis-workflow:state-policy",
                worker_arn,
                coordinator_arn,
                str(policy_path),
            ]
            try:
                exec(compile(match.group("body"), "state-policy", "exec"), {})
            finally:
                sys.argv = original_argv
            policy = json.loads(policy_path.read_text())
        return policy, worker_arn, coordinator_arn

    def _run_state_machine_verifier(self, description):
        body = self._heredoc("PY_VERIFY")
        destination_arn = self.LOG_GROUP_BASE_ARN + ":*"
        original_argv = sys.argv
        sys.argv = [
            "deploy-historical-reanalysis-workflow:PY_VERIFY",
            json.dumps(description),
            self.STATE_ROLE_ARN,
            destination_arn,
        ]
        try:
            exec(compile(body, "PY_VERIFY", "exec"), {})
        finally:
            sys.argv = original_argv

    def _valid_state_machine_description(self):
        return {
            "status": "ACTIVE",
            "roleArn": self.STATE_ROLE_ARN,
            "loggingConfiguration": {
                "level": "ERROR",
                "includeExecutionData": False,
                "destinations": [
                    {
                        "cloudWatchLogsLogGroup": {
                            "logGroupArn": self.LOG_GROUP_BASE_ARN + ":*",
                        },
                    },
                ],
            },
            "tracingConfiguration": {
                "enabled": True,
            },
        }

    def test_stage_specific_log_group_and_retention(self):
        self.assertIn(
            (
                'STATE_LOG_GROUP="/aws/vendedlogs/states/'
                '${STATE_MACHINE_NAME}"'
            ),
            self.script,
        )
        self.assertIn(
            '"${APP_NAME}-${STAGE}-historical-reanalysis-workflow"',
            self.script,
        )
        self.assertIn("aws logs put-retention-policy", self.script)
        self.assertIn("--retention-in-days 30", self.script)

    def test_log_group_lookup_fails_closed(self):
        self.assertEqual(
            self._run_log_group_lookup_parser({"logGroups": []}),
            "ABSENT",
        )
        self.assertEqual(
            self._run_log_group_lookup_parser({
                "logGroups": [
                    {"logGroupName": self.LOG_GROUP_NAME},
                ],
            }),
            "PRESENT",
        )
        for malformed in ({}, [], {"logGroups": [None]}):
            with self.subTest(response=malformed), self.assertRaises(SystemExit):
                self._run_log_group_lookup_parser(malformed)
        self.assertIn("LOG_GROUP_LOOKUP_STATUS", self.script)
        self.assertIn(
            "CloudWatch log group lookup failed; refusing to create it.",
            self.script,
        )
        self.assertNotIn("|| true", self.script)

    def test_create_and_update_enable_secure_logging_and_tracing(self):
        create_block = self.script.split(
            "aws stepfunctions create-state-machine",
            1,
        )[1].split("else", 1)[0]
        update_block = self.script.split(
            "aws stepfunctions update-state-machine",
            1,
        )[1].split("\nfi", 1)[0]
        for operation, block in (
            ("create", create_block),
            ("update", update_block),
        ):
            with self.subTest(operation=operation):
                self.assertIn("--logging-configuration", block)
                self.assertIn(
                    "level=ERROR,includeExecutionData=false",
                    block,
                )
                self.assertIn(
                    "logGroupArn=${STATE_LOG_GROUP_DESTINATION_ARN}",
                    block,
                )
                self.assertIn("--tracing-configuration enabled=true", block)
        self.assertEqual(self.script.count("--logging-configuration"), 2)
        self.assertEqual(
            self.script.count("--tracing-configuration enabled=true"),
            2,
        )
        self.assertNotIn("includeExecutionData=true", self.script)

    def test_state_role_policy_is_least_privilege(self):
        policy, worker_arn, coordinator_arn = self._state_policy()
        statements = {
            statement["Sid"]: statement
            for statement in policy["Statement"]
        }
        invoke = statements["InvokeReanalysisFunctions"]
        self.assertEqual(invoke["Action"], ["lambda:InvokeFunction"])
        self.assertEqual(invoke["Resource"], [worker_arn, coordinator_arn])

        self.assertEqual(
            set(statements["StepFunctionsLogDelivery"]["Action"]),
            {
                "logs:CreateLogDelivery",
                "logs:GetLogDelivery",
                "logs:UpdateLogDelivery",
                "logs:DeleteLogDelivery",
                "logs:ListLogDeliveries",
                "logs:PutResourcePolicy",
                "logs:DescribeResourcePolicies",
                "logs:DescribeLogGroups",
            },
        )
        self.assertEqual(
            set(statements["StepFunctionsTracing"]["Action"]),
            {
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
                "xray:GetSamplingRules",
                "xray:GetSamplingTargets",
            },
        )
        wildcard_sids = {
            statement["Sid"]
            for statement in policy["Statement"]
            if statement["Resource"] == "*"
        }
        self.assertEqual(
            wildcard_sids,
            {"StepFunctionsLogDelivery", "StepFunctionsTracing"},
        )
        for broad_action in (
            '"logs:*"',
            '"xray:*"',
            '"lambda:*"',
            '"states:*"',
            "AdministratorAccess",
        ):
            self.assertNotIn(broad_action, self.script)

    def test_applied_policy_is_verified_before_state_machine_changes(self):
        get_policy_index = self.script.index("aws iam get-role-policy")
        policy_verify_index = self.script.index("PY_POLICY_VERIFY")
        create_index = self.script.index("aws stepfunctions create-state-machine")
        update_index = self.script.index("aws stepfunctions update-state-machine")
        self.assertLess(get_policy_index, policy_verify_index)
        self.assertLess(policy_verify_index, create_index)
        self.assertLess(policy_verify_index, update_index)
        self.assertIn("applied_policy != expected_policy", self.script)

    def test_log_group_parser_prefers_log_group_arn(self):
        self.assertEqual(
            self._run_log_group_parser({
                "logGroupName": self.LOG_GROUP_NAME,
                "logGroupArn": self.LOG_GROUP_BASE_ARN,
                "arn": self.LOG_GROUP_BASE_ARN + ":unrelated",
            }),
            self.LOG_GROUP_BASE_ARN,
        )

    def test_log_group_parser_falls_back_and_removes_one_suffix(self):
        actual = self._run_log_group_parser({
            "logGroupName": self.LOG_GROUP_NAME,
            "arn": self.LOG_GROUP_BASE_ARN + ":*",
        })
        self.assertEqual(actual, self.LOG_GROUP_BASE_ARN)
        destination = actual + ":*"
        self.assertTrue(destination.endswith(":*"))
        self.assertEqual(destination.count(":*"), 1)
        self.assertIn(
            'STATE_LOG_GROUP_DESTINATION_ARN="${STATE_LOG_GROUP_BASE_ARN}:*"',
            self.script,
        )

    def test_log_group_parser_rejects_noncanonical_or_wrong_scope_arns(self):
        invalid_arns = (
            self.LOG_GROUP_BASE_ARN + ":*:*",
            self.LOG_GROUP_BASE_ARN.replace("arn:aws:", "arn:aws-cn:"),
            self.LOG_GROUP_BASE_ARN.replace(":logs:", ":states:"),
            self.LOG_GROUP_BASE_ARN.replace(":us-east-1:", ":us-west-2:"),
            self.LOG_GROUP_BASE_ARN.replace("111122223333", "999900001111"),
            self.LOG_GROUP_BASE_ARN.replace(self.LOG_GROUP_NAME, "/other"),
        )
        for arn in invalid_arns:
            with self.subTest(arn=arn), self.assertRaises(SystemExit):
                self._run_log_group_parser({
                    "logGroupName": self.LOG_GROUP_NAME,
                    "arn": arn,
                })

    def test_final_state_machine_postconditions_fail_closed(self):
        valid = self._valid_state_machine_description()
        self._run_state_machine_verifier(valid)

        invalid_descriptions = []
        mutations = (
            ("status", "CREATING"),
            ("roleArn", "arn:aws:iam::111122223333:role/other"),
        )
        for key, value in mutations:
            description = json.loads(json.dumps(valid))
            description[key] = value
            invalid_descriptions.append(description)

        for key, value in (
            ("level", "ALL"),
            ("includeExecutionData", True),
            ("destinations", []),
        ):
            description = json.loads(json.dumps(valid))
            description["loggingConfiguration"][key] = value
            invalid_descriptions.append(description)

        description = json.loads(json.dumps(valid))
        description["loggingConfiguration"]["destinations"][0][
            "cloudWatchLogsLogGroup"
        ]["logGroupArn"] = self.LOG_GROUP_BASE_ARN + ":*:*"
        invalid_descriptions.append(description)

        description = json.loads(json.dumps(valid))
        description["loggingConfiguration"]["destinations"].append(
            description["loggingConfiguration"]["destinations"][0]
        )
        invalid_descriptions.append(description)

        description = json.loads(json.dumps(valid))
        description["tracingConfiguration"]["enabled"] = False
        invalid_descriptions.append(description)

        for description in invalid_descriptions:
            with self.subTest(description=description), self.assertRaises(SystemExit):
                self._run_state_machine_verifier(description)

        self.assertIn("aws stepfunctions describe-state-machine", self.script)

    def test_every_python_heredoc_compiles_at_column_zero(self):
        blocks = re.findall(
            r"<<'(?P<tag>PY[0-9A-Z_]*)'\n(?P<body>.*?)\n(?P=tag)",
            self.script,
            flags=re.DOTALL,
        )
        self.assertGreaterEqual(len(blocks), 7)
        for tag, body in blocks:
            with self.subTest(heredoc=tag):
                compile(
                    body,
                    f"deploy-historical-reanalysis-workflow:{tag}",
                    "exec",
                )
                first_line = next(
                    line
                    for line in body.splitlines()
                    if line.strip()
                )
                self.assertFalse(first_line[0].isspace())

    def test_create_api_has_job_routes(
        self,
    ):
        script = read_backend_file(
            "bin/create-api"
        )

        self.assertIn(
            (
                'create_route_if_missing '
                '"POST /analysis/'
                'reanalysis/jobs"'
            ),
            script,
        )
        self.assertIn(
            (
                'create_route_if_missing '
                '"GET /analysis/'
                'reanalysis/jobs"'
            ),
            script,
        )
        self.assertIn(
            (
                'create_route_if_missing '
                '"GET /analysis/'
                'reanalysis/jobs/{jobId}"'
            ),
            script,
        )
        self.assertIn(
            (
                'create_route_if_missing '
                '"POST /analysis/'
                'reanalysis/jobs/'
                '{jobId}/retry"'
            ),
            script,
        )
        self.assertIn(
            '"authorization"',
            script,
        )
        self.assertIn(
            "update-api",
            script,
        )

    def test_secure_api_has_job_routes(
        self,
    ):
        script = read_backend_file(
            "bin/secure-api"
        )

        self.assertIn(
            (
                'secure_route "POST '
                '/analysis/reanalysis/jobs"'
            ),
            script,
        )
        self.assertIn(
            (
                'secure_route "GET '
                '/analysis/reanalysis/jobs"'
            ),
            script,
        )
        self.assertIn(
            (
                'secure_route "GET '
                '/analysis/reanalysis/'
                'jobs/{jobId}"'
            ),
            script,
        )
        self.assertIn(
            (
                'secure_route "POST '
                '/analysis/reanalysis/'
                'jobs/{jobId}/retry"'
            ),
            script,
        )

    def test_transaction_roles_can_put_items(
        self,
    ):
        script = read_backend_file(
            "bin/"
            "deploy-historical-reanalysis-workflow"
        )

        self.assertEqual(
            script.count(
                '"dynamodb:PutItem"'
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
