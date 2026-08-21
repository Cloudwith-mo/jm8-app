import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
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

    def _retry_function(self):
        marker = (
            "run_stepfunctions_write_with_iam_propagation_retry() {"
        )
        start = self.script.index(marker)
        end = self.script.index(
            "\n}\n\nSTATE_ROLE_ARN",
            start,
        ) + 2
        return self.script[start:end]

    def _run_retry_scenario(self, scenario):
        harness = "\n\n".join((
            "#!/usr/bin/env bash\nset -u",
            self._retry_function(),
            r'''
scenario="$1"
events_file="$2"
attempt_file="$3"
error_file="$4"

sleep() {
  printf 'sleep:%s\n' "$1" >> "$events_file"
}

fake_stepfunctions_write() {
  local requested_scenario="$1"
  local current_attempt=0

  if [ -f "$attempt_file" ]; then
    IFS= read -r current_attempt < "$attempt_file"
  fi

  current_attempt=$((current_attempt + 1))
  printf '%s\n' "$current_attempt" > "$attempt_file"
  printf 'attempt:%s\n' "$current_attempt" >> "$events_file"

  case "$requested_scenario" in
    propagation_then_success)
      if [ "$current_attempt" -eq 1 ]; then
        printf '%s\n' \
          'An error occurred (AccessDeniedException): The state machine IAM Role is not authorized to access the Log Destination' \
          >&2
        return 42
      fi
      printf '%s\n' 'arn:aws:states:us-east-1:111122223333:stateMachine:test'
      return 0
      ;;
    propagation_exhausted)
      printf '%s\n' \
        'An error occurred (AccessDeniedException): The state machine IAM Role is not authorized to access the Log Destination' \
        >&2
      return 42
      ;;
    unrelated_access_denied)
      printf '%s\n' \
        'An error occurred (AccessDeniedException): not authorized to call UpdateStateMachine' \
        >&2
      return 43
      ;;
    validation_error)
      printf '%s\n' \
        'An error occurred (ValidationException): Invalid State Machine Definition' \
        >&2
      return 44
      ;;
    credential_error)
      printf '%s\n' 'Unable to locate credentials' >&2
      return 45
      ;;
    network_error)
      printf '%s\n' 'Could not connect to the endpoint URL' >&2
      return 47
      ;;
    *)
      printf '%s\n' 'Unexpected test scenario' >&2
      return 46
      ;;
  esac
}

set +e
run_stepfunctions_write_with_iam_propagation_retry \
  "test-write" \
  "$error_file" \
  fake_stepfunctions_write \
  "$scenario"
status=$?
set -e

printf 'status:%s\n' "$status" >> "$events_file"
exit "$status"
'''.strip(),
        ))

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            harness_path = directory_path / "retry-harness.sh"
            events_path = directory_path / "events.txt"
            attempt_path = directory_path / "attempt.txt"
            error_path = directory_path / "aws-error.txt"
            harness_path.write_text(harness)

            result = subprocess.run(
                [
                    "bash",
                    str(harness_path),
                    scenario,
                    str(events_path),
                    str(attempt_path),
                    str(error_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            events = events_path.read_text().splitlines()

        return result, events

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

    def test_exact_propagation_error_retries_then_reaches_verification(self):
        result, events = self._run_retry_scenario(
            "propagation_then_success"
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            events,
            ["attempt:1", "sleep:5", "attempt:2", "status:0"],
        )
        self.assertIn("attempt 1/5", result.stderr)
        self.assertIn("waiting 5 seconds", result.stderr)
        self.assertIn("succeeded on attempt 2/5", result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "arn:aws:states:us-east-1:111122223333:stateMachine:test",
        )

        update_retry = self.script.index(
            '"update-state-machine" \\\n'
            '    "$STATE_MACHINE_WRITE_ERROR_FILE"'
        )
        final_verification = self.script.index(
            "aws stepfunctions describe-state-machine"
        )
        self.assertLess(update_retry, final_verification)

    def test_unrelated_failures_are_not_retried(self):
        cases = (
            (
                "unrelated_access_denied",
                43,
                "not authorized to call UpdateStateMachine",
            ),
            (
                "validation_error",
                44,
                "ValidationException",
            ),
            (
                "credential_error",
                45,
                "Unable to locate credentials",
            ),
            (
                "network_error",
                47,
                "Could not connect to the endpoint URL",
            ),
        )

        for scenario, status, expected_error in cases:
            with self.subTest(scenario=scenario):
                result, events = self._run_retry_scenario(scenario)
                self.assertEqual(result.returncode, status)
                self.assertEqual(events, ["attempt:1", f"status:{status}"])
                self.assertNotIn("sleep:", "\n".join(events))
                self.assertIn("non-retryable AWS error", result.stderr)
                self.assertIn(expected_error, result.stderr)

    def test_propagation_retries_are_bounded_and_exhaustion_fails(self):
        result, events = self._run_retry_scenario(
            "propagation_exhausted"
        )

        self.assertEqual(result.returncode, 42)
        self.assertEqual(
            [event for event in events if event.startswith("attempt:")],
            [
                "attempt:1",
                "attempt:2",
                "attempt:3",
                "attempt:4",
                "attempt:5",
            ],
        )
        self.assertEqual(
            [event for event in events if event.startswith("sleep:")],
            ["sleep:5", "sleep:10", "sleep:20", "sleep:30"],
        )
        self.assertEqual(sum((5, 10, 20, 30)), 65)
        self.assertEqual(events[-1], "status:42")
        self.assertIn("exhausted 5 IAM propagation attempts", result.stderr)
        self.assertIn("AccessDeniedException", result.stderr)
        self.assertIn(
            (
                "state machine IAM Role is not authorized to access "
                "the Log Destination"
            ),
            result.stderr,
        )

    def test_create_and_update_both_use_the_retry_helper(self):
        for operation in (
            "create-state-machine",
            "update-state-machine",
        ):
            command_index = self.script.index(
                f"aws stepfunctions {operation}"
            )
            prefix = self.script[
                max(0, command_index - 220):command_index
            ]
            with self.subTest(operation=operation):
                self.assertIn(
                    "run_stepfunctions_write_with_iam_propagation_retry",
                    prefix,
                )
                self.assertIn(f'"{operation}"', prefix)

    def test_retry_helper_does_not_log_sensitive_inputs_or_weaken_shell(self):
        retry_function = self._retry_function()
        self.assertIn("AccessDeniedException", retry_function)
        self.assertIn(
            (
                "state machine IAM Role is not authorized to access "
                "the Log Destination"
            ),
            retry_function,
        )
        for sensitive_name in (
            "AWS_PROFILE",
            "STATE_POLICY",
            "RESOLVED_DEFINITION",
            "CALLER_ARN",
            "STATE_LOG_GROUP_DESTINATION_ARN",
        ):
            self.assertNotIn(sensitive_name, retry_function)
        self.assertNotIn('echo "$@"', retry_function)
        self.assertNotIn("|| true", self.script)
        self.assertNotRegex(self.script, r"\beval\b")
        self.assertNotIn("set -x", self.script)

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
