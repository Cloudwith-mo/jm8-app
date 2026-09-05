import json
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import boto3


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "function"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")

import app  # noqa: E402


REQUEST_ID = "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AWS_GLOBAL_OPTIONS = {"--output", "--profile", "--query", "--region"}
LONG_OPTION_PATTERN = re.compile(r"^--[a-z][a-z0-9-]*$")
AWS_COMMAND_PATTERN = re.compile(
    r"\baws\s+([a-z0-9-]+)\s+([a-z0-9-]+)\b"
)


def all_aws_command_tokens(source: str) -> list[tuple[int, list[str]]]:
    """Tokenize every literal AWS CLI invocation without executing shell code."""
    commands = []
    logical_source = source.replace("\\\n", " ")
    for line_number, line in enumerate(logical_source.splitlines(), start=1):
        match = AWS_COMMAND_PATTERN.search(line)
        if match is None:
            continue
        fragment = line[match.start():].strip()
        if fragment.endswith(')"'):
            fragment = fragment[:-2]
        tokens = shlex.split(fragment)
        command = []
        for token in tokens:
            if (
                token in {"&&", "||", "then", "true"}
                or re.match(r"^(?:[012]?>>?|&>)", token)
            ):
                break
            if token.endswith(";"):
                command.append(token[:-1])
                break
            command.append(token)
        commands.append((line_number, command))
    return commands


def cli_option_for_member(member: str) -> str:
    first_pass = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", member)
    second_pass = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", first_pass)
    return "--" + second_pass.replace("_", "-").lower()


def assert_safe_aws_tokens(test: unittest.TestCase, source: str) -> None:
    commands = all_aws_command_tokens(source)
    test.assertGreater(len(commands), 0)
    for line_number, tokens in commands:
        with test.subTest(line=line_number, command=tokens[:3]):
            test.assertGreaterEqual(len(tokens), 3)
            test.assertEqual(tokens[0], "aws")
            for index, token in enumerate(tokens[3:], start=3):
                if token.startswith("--"):
                    test.assertRegex(token, LONG_OPTION_PATTERN)
                    test.assertLess(index + 1, len(tokens))
                    test.assertFalse(tokens[index + 1].startswith("--"))
                else:
                    test.assertNotIn("--", token)


def shell_command_tokens(source: str, service: str, operation: str) -> list[list[str]]:
    logical_source = source.replace("\\\n", " ")
    prefix = f"aws {service} {operation} "
    commands = []
    for line in logical_source.splitlines():
        if prefix not in line:
            continue
        fragment = line[line.index(prefix):].strip()
        if fragment.endswith(')"'):
            fragment = fragment[:-1]
        tokens = shlex.split(fragment)
        tokens[-1] = tokens[-1].rstrip('")')
        commands.append(tokens)
    return commands


def render_embedded_json(source: str, marker: str, arguments: list[str]) -> dict:
    start_marker = f"<<'{marker}'\n"
    start = source.index(start_marker) + len(start_marker)
    code = source[start:source.index(f"\n{marker}", start)]
    previous_argv = sys.argv
    output = io.StringIO()
    try:
        sys.argv = [marker, *arguments]
        with redirect_stdout(output):
            exec(compile(code, marker, "exec"), {})
    finally:
        sys.argv = previous_argv
    return json.loads(output.getvalue())


def run_mocked_staging_deletion_deploy(
    environment_overrides: dict[str, str] | None = None,
    *,
    stepfunctions_scenario: str = "success",
    state_machine_present: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the actual guarded deploy/package boundary with a local AWS shim."""
    with tempfile.TemporaryDirectory() as directory:
        backend = Path(directory) / "backend"
        bin_dir = backend / "bin"
        function_dir = backend / "function"
        workflow_dir = backend / "workflows"
        mock_bin = Path(directory) / "mock-bin"
        for path in (bin_dir, function_dir, workflow_dir, mock_bin):
            path.mkdir(parents=True, exist_ok=True)

        for name in (
            "build",
            "deploy-account-deletion",
            "jm8_deployment_guard.sh",
            "jm8_environment_contract.py",
            "jm8_resource_tag_contract.py",
            "jm8_resource_tags.sh",
            "package",
        ):
            shutil.copy2(ROOT / "bin" / name, bin_dir / name)
        shutil.copy2(
            ROOT / "workflows" / "account-deletion.asl.json",
            workflow_dir / "account-deletion.asl.json",
        )
        (function_dir / "requirements.txt").write_text("", encoding="utf-8")
        (function_dir / "placeholder.py").write_text(
            "VALUE = 'packaged'\n",
            encoding="utf-8",
        )

        aws_log = Path(directory) / "aws.log"
        aws_shim = mock_bin / "aws"
        aws_shim.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$MOCK_AWS_LOG"

value_after() {
  local option="$1"
  shift
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "$option" ]; then
      printf '%s' "$2"
      return 0
    fi
    shift
  done
  return 1
}

service="$1"
operation="$2"
case "${service}:${operation}" in
  sts:get-caller-identity)
    printf '%s\\n' '114743615542'
    ;;
  cognito-idp:describe-user-pool)
    printf '%s\\n' 'journalm8-staging-users'
    ;;
  iam:get-role)
    role_name="$(value_after --role-name "$@")"
    if [[ " $* " == *" --query Role.Arn "* ]]; then
      printf 'arn:aws:iam::114743615542:role/%s\\n' "$role_name"
    else
      printf '{}\\n'
    fi
    ;;
  lambda:get-function)
    function_name="$(value_after --function-name "$@")"
    function_arn="arn:aws:lambda:us-east-1:114743615542:function:${function_name}"
    if [[ " $* " == *" --query Configuration.FunctionArn "* ]]; then
      printf '%s\\n' "$function_arn"
    elif [[ " $* " == *" --output json "* ]]; then
      printf '{"Configuration":{"FunctionName":"%s","FunctionArn":"%s"}}\\n' \
        "$function_name" "$function_arn"
    else
      printf '{}\\n'
    fi
    ;;
  lambda:list-tags)
    printf '%s\\n' '{"Tags":{"App":"journalm8","Stage":"staging","ManagedBy":"aws-cli"}}'
    ;;
  logs:create-log-group)
    printf '%s\\n' 'ResourceAlreadyExistsException' >&2
    exit 254
    ;;
  stepfunctions:list-state-machines)
    if [ "$MOCK_STATE_MACHINE_PRESENT" = "true" ]; then
      printf '%s\\n' 'arn:aws:states:us-east-1:114743615542:stateMachine:journalm8-staging-account-deletion-workflow'
    else
      printf '%s\\n' 'None'
    fi
    ;;
  stepfunctions:create-state-machine|stepfunctions:update-state-machine)
    attempts="$(($(cat "$MOCK_STEPFUNCTIONS_ATTEMPTS") + 1))"
    printf '%s\\n' "$attempts" > "$MOCK_STEPFUNCTIONS_ATTEMPTS"
    if [ "$operation" = "create-state-machine" ]; then
      operation_name='CreateStateMachine'
    else
      operation_name='UpdateStateMachine'
    fi
    exact_error="An error occurred (AccessDeniedException) when calling the ${operation_name} operation: The state machine IAM Role is not authorized to access the Log Destination."
    case "$MOCK_STEPFUNCTIONS_SCENARIO" in
      exact-then-success)
        if [ "$attempts" -eq 1 ]; then
          printf '%s\\n' "$exact_error" >&2
          exit 42
        fi
        ;;
      exact-persistent)
        printf '%s\\n' "$exact_error" >&2
        exit 42
        ;;
      unrelated-access-denied)
        printf 'An error occurred (AccessDeniedException): %s\\n' "$MOCK_PRIVATE_VALUE" >&2
        exit 43
        ;;
    esac
    if [ "$operation" = "create-state-machine" ]; then
      printf '%s\\n' 'arn:aws:states:us-east-1:114743615542:stateMachine:journalm8-staging-account-deletion-workflow'
    else
      printf '{}\\n'
    fi
    ;;
  *)
    printf '{}\\n'
    ;;
esac
""",
            encoding="utf-8",
        )
        aws_shim.chmod(0o755)
        sleep_shim = mock_bin / "sleep"
        sleep_shim.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf 'sleep %s\\n' "$1" >> "$MOCK_AWS_LOG"
""",
            encoding="utf-8",
        )
        sleep_shim.chmod(0o755)

        attempts_file = Path(directory) / "stepfunctions-attempts"
        attempts_file.write_text("0\n", encoding="utf-8")

        environment = {
            "ACCOUNT_EXPORT_WORKFLOW_ARN": (
                "arn:aws:states:us-east-1:114743615542:stateMachine:"
                "journalm8-staging-account-export-workflow"
            ),
            "API_NAME": "journalm8-staging-api",
            "APP_NAME": "journalm8",
            "AWS_PROFILE": "jm8-dev",
            "AWS_REGION": "us-east-1",
            "COGNITO_USER_POOL_ID": "us-east-1_Staging",
            "COGNITO_USER_POOL_NAME": "journalm8-staging-users",
            "DEPLOY_CONFIRMATION": "staging",
            "EXPECTED_AWS_ACCOUNT_ID": "114743615542",
            "ENTRY_CHUNKS_TABLE_NAME": "journalm8-staging-entry-chunks",
            "EXPORT_BUCKET": "journalm8-staging-exports-114743615542",
            "HISTORICAL_REANALYSIS_WORKFLOW_ARN": (
                "arn:aws:states:us-east-1:114743615542:stateMachine:"
                "journalm8-staging-historical-reanalysis-workflow"
            ),
            "LAMBDA_FUNCTION_NAME": "journalm8-staging-api",
            "MOCK_AWS_LOG": str(aws_log),
            "MOCK_PRIVATE_VALUE": "private-workflow-definition-and-user-data",
            "MOCK_STATE_MACHINE_PRESENT": (
                "true" if state_machine_present else "false"
            ),
            "MOCK_STEPFUNCTIONS_ATTEMPTS": str(attempts_file),
            "MOCK_STEPFUNCTIONS_SCENARIO": stepfunctions_scenario,
            "OCR_WORKFLOW_ARN": (
                "arn:aws:states:us-east-1:114743615542:stateMachine:"
                "journalm8-staging-ocr-workflow"
            ),
            "PATH": f"{mock_bin}:{os.environ['PATH']}",
            "RAW_BUCKET": "journalm8-staging-raw-114743615542",
            "STAGE": "staging",
            "STRIPE_SECRET_ARN": (
                "arn:aws:secretsmanager:us-east-1:114743615542:"
                "secret:journalm8/staging/stripe-ABC123"
            ),
            "TABLE_NAME": "journalm8-staging-main",
        }
        if environment_overrides:
            environment.update(environment_overrides)
        result = subprocess.run(
            [str(bin_dir / "deploy-account-deletion")],
            cwd=backend,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        calls = aws_log.read_text(encoding="utf-8") if aws_log.exists() else ""
        return result, calls


def event(method, path, claims=None, body=None):
    return {
        "requestContext": {
            "http": {"method": method, "path": path},
            "authorizer": {"jwt": {"claims": claims or {}}},
        },
        "headers": {"x-user-id": "dev-fallback-must-not-authorize"},
        "body": json.dumps(body or {}),
    }


class AccountDeletionDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.create_api = (ROOT / "bin" / "create-api").read_text()
        cls.secure_api = (ROOT / "bin" / "secure-api").read_text()
        cls.contract = (
            ROOT.parent / "docs" / "JM8_USER_DATA_CONTRACT.md"
        ).read_text()

    def test_routes_are_declared_once_and_require_jwt_authorization(self):
        for route in (
            "POST /account/deletion-requests",
            "GET /account/deletion-requests/{requestId}",
        ):
            self.assertEqual(self.create_api.count(
                f'create_route_if_missing "{route}"'
            ), 1)
            self.assertEqual(self.secure_api.count(
                f'secure_route "{route}"'
            ), 1)
            self.assertNotIn(f'public_route "{route}"', self.secure_api)

    @patch.object(app, "create_account_deletion_request")
    def test_post_route_requires_verified_claims_and_uses_cognito_subject(self, create):
        unauthorized = app.lambda_handler(event(
            "POST",
            "/account/deletion-requests",
            body={
                "confirmation": "DELETE_MY_ACCOUNT",
                "requestToken": "550e8400-e29b-41d4-a716-446655440000",
            },
        ), None)
        self.assertEqual(unauthorized["statusCode"], 401)
        create.assert_not_called()

        create.return_value = (202, {
            "deletionRequest": {"requestId": REQUEST_ID, "status": "REQUESTED"},
            "replayed": False,
        })
        claims = {
            "sub": "verified-subject",
            "auth_time": str(int(time.time())),
            "email": "private@example.com",
        }
        body = {
            "confirmation": "DELETE_MY_ACCOUNT",
            "requestToken": "550e8400-e29b-41d4-a716-446655440000",
        }
        accepted = app.lambda_handler(event(
            "POST",
            "/account/deletion-requests",
            claims=claims,
            body=body,
        ), None)
        self.assertEqual(accepted["statusCode"], 202)
        create.assert_called_once_with("verified-subject", claims, body)

    @patch.object(app, "get_account_deletion_request")
    def test_get_status_uses_same_verified_subject(self, get_request):
        get_request.return_value = (200, {
            "deletionRequest": {"requestId": REQUEST_ID, "status": "REQUESTED"},
        })
        result = app.lambda_handler(event(
            "GET",
            f"/account/deletion-requests/{REQUEST_ID}",
            claims={"sub": "verified-subject"},
        ), None)
        self.assertEqual(result["statusCode"], 200)
        get_request.assert_called_once_with("verified-subject", REQUEST_ID)

    def test_production_post_is_unavailable_until_workflow_is_configured(self):
        result = app.lambda_handler(event(
            "POST",
            "/account/deletion-requests",
            claims={
                "sub": "verified-subject",
                "auth_time": str(int(time.time())),
            },
            body={
                "confirmation": "DELETE_MY_ACCOUNT",
                "requestToken": "550e8400-e29b-41d4-a716-446655440000",
            },
        ), None)
        self.assertEqual(result["statusCode"], 503)
        payload = json.loads(result["body"])
        self.assertEqual(payload["error"], "AccountDeletionUnavailable")
        self.assertTrue(payload["retryable"])

    def test_phase_3c3c_deployment_source_is_guarded_and_integrated(self):
        deploy = (ROOT / "bin" / "deploy-account-deletion").read_text()
        main_deploy = (ROOT / "bin" / "deploy").read_text()
        for required in (
            "account_deletion_worker.lambda_handler",
            "cognito-idp:AdminDeleteUser",
            "dynamodb:BatchWriteItem",
            "s3:DeleteObjectVersion",
            "states:StopExecution",
            "secretsmanager:GetSecretValue",
            '"includeExecutionData":False',
            '"level":"ERROR"',
            "--reserved-concurrent-executions",
            "put-metric-alarm",
            "put-dashboard",
            "--ephemeral-storage",
            "--architectures arm64",
            "ExecutionsTimedOut",
            "DurablePartialFailures",
            "COGNITO_USER_POOL_NAME",
        ):
            self.assertIn(required, deploy)
        self.assertIn("./bin/deploy-account-deletion", main_deploy)
        self.assertIn("ACCOUNT_DELETION_WORKFLOW_ARN", main_deploy)
        self.assertIn("ACCOUNT_DELETION_RECENT_AUTH_SECONDS", main_deploy)
        self.assertIn("states:StartExecution", main_deploy)

    def test_semantic_deletion_worker_configuration_is_exact(self):
        deploy = (ROOT / "bin" / "deploy-account-deletion").read_text()
        build = (ROOT / "bin" / "build").read_text()
        self.assertIn(': "${ENTRY_CHUNKS_TABLE_NAME:?', deploy)
        self.assertIn('"ENTRY_CHUNKS_TABLE_NAME": entry_chunks_table', deploy)
        self.assertTrue(
            (ROOT / "function" / "semantic_memory_deletion_guard.py").is_file()
        )
        self.assertIn("cp function/*.py", build)
        environment = render_embedded_json(
            deploy,
            "PY_ENV",
            [
                "journalm8-prod-main",
                "journalm8-prod-entry-chunks",
                "raw-bucket",
                "export-bucket",
                "us-east-1_Production",
                "secret-arn",
                "ocr-arn",
                "reanalysis-arn",
                "export-arn",
                "journalm8",
                "prod",
            ],
        )
        self.assertEqual(
            environment["Variables"]["ENTRY_CHUNKS_TABLE_NAME"],
            "journalm8-prod-entry-chunks",
        )
        for forbidden in (
            "dynamodb:Scan",
            "dynamodb:DeleteTable",
            "states:StartExecution",
            "semantic-memory-worker",
        ):
            self.assertNotIn(forbidden, deploy)

    def test_staging_package_contract_keeps_api_and_worker_names_distinct(self):
        result, aws_calls = run_mocked_staging_deletion_deploy()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Package complete: dist/function.zip", result.stdout)
        self.assertIn(
            "Account deletion deployment source reconciled:",
            result.stdout,
        )
        worker_name = "journalm8-staging-account-deletion-worker"
        self.assertIn(
            f"lambda update-function-code --function-name {worker_name}",
            aws_calls,
        )
        self.assertIn(
            "lambda update-function-configuration "
            f"--function-name {worker_name}",
            aws_calls,
        )
        self.assertIn(
            f"lambda put-function-concurrency --function-name {worker_name}",
            aws_calls,
        )
        self.assertIn(
            "stepfunctions create-state-machine --name "
            "journalm8-staging-account-deletion-workflow",
            aws_calls,
        )
        self.assertNotRegex(
            aws_calls,
            r"lambda (?:create-function|update-function-code|"
            r"update-function-configuration) .*journalm8-staging-api",
        )
        self.assertNotIn("start-execution", aws_calls)

    def test_exact_logging_authorization_failure_retries_create_and_update(self):
        for operation, state_machine_present in (
            ("create-state-machine", False),
            ("update-state-machine", True),
        ):
            with self.subTest(operation=operation):
                result, aws_calls = run_mocked_staging_deletion_deploy(
                    stepfunctions_scenario="exact-then-success",
                    state_machine_present=state_machine_present,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    aws_calls.count(f"stepfunctions {operation}"),
                    2,
                )
                self.assertEqual(
                    [line for line in aws_calls.splitlines()
                     if line.startswith("sleep ")],
                    ["sleep 2"],
                )
                self.assertIn(
                    "logging authorization is propagating",
                    result.stderr,
                )
                self.assertNotIn(
                    "not authorized to access the Log Destination",
                    result.stdout + result.stderr,
                )
                self.assertNotIn(
                    "private-workflow-definition-and-user-data",
                    result.stdout + result.stderr,
                )
                self.assertNotIn("start-execution", aws_calls)

    def test_logging_authorization_retry_is_bounded_and_fails_closed(self):
        result, aws_calls = run_mocked_staging_deletion_deploy(
            stepfunctions_scenario="exact-persistent",
        )

        self.assertEqual(result.returncode, 42)
        self.assertEqual(
            aws_calls.count("stepfunctions create-state-machine"),
            5,
        )
        waits = [
            int(line.removeprefix("sleep "))
            for line in aws_calls.splitlines()
            if line.startswith("sleep ")
        ]
        self.assertEqual(waits, [2, 4, 8, 16])
        self.assertEqual(sum(waits), 30)
        self.assertIn(
            "failed after 5 bounded logging propagation attempts",
            result.stderr,
        )
        self.assertNotIn(
            "not authorized to access the Log Destination",
            result.stdout + result.stderr,
        )
        self.assertNotIn("stepfunctions tag-resource", aws_calls)
        self.assertNotIn("start-execution", aws_calls)
        self.assertNotIn("apigateway", aws_calls)
        self.assertNotRegex(
            aws_calls,
            r"lambda (?:create-function|update-function-code|"
            r"update-function-configuration) .*journalm8-staging-api",
        )

    def test_unrelated_access_denied_fails_immediately_without_raw_error(self):
        result, aws_calls = run_mocked_staging_deletion_deploy(
            stepfunctions_scenario="unrelated-access-denied",
        )

        self.assertEqual(result.returncode, 43)
        self.assertEqual(
            aws_calls.count("stepfunctions create-state-machine"),
            1,
        )
        self.assertNotIn("sleep ", aws_calls)
        self.assertIn("without a retryable logging propagation signal", result.stderr)
        for private_value in (
            "AccessDeniedException",
            "private-workflow-definition-and-user-data",
            "account-deletion.asl.json",
            "worker-environment.json",
        ):
            self.assertNotIn(private_value, result.stdout + result.stderr)
        self.assertNotIn("stepfunctions tag-resource", aws_calls)
        self.assertNotIn("start-execution", aws_calls)
        self.assertNotIn("apigateway", aws_calls)

    def test_partial_resources_are_reused_without_execution_or_shared_api_wiring(self):
        result, aws_calls = run_mocked_staging_deletion_deploy(
            stepfunctions_scenario="exact-then-success",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("iam create-role", aws_calls)
        self.assertNotIn("lambda create-function", aws_calls)
        self.assertEqual(aws_calls.count("iam get-role --role-name"), 4)
        self.assertIn(
            "lambda update-function-code --function-name "
            "journalm8-staging-account-deletion-worker",
            aws_calls,
        )
        self.assertIn(
            "logs create-log-group --log-group-name "
            "/aws/vendedlogs/states/"
            "journalm8-staging-account-deletion-workflow",
            aws_calls,
        )
        self.assertEqual(
            aws_calls.count("stepfunctions create-state-machine"),
            2,
        )
        self.assertNotIn("delete-state-machine", aws_calls)
        self.assertNotIn("delete-function", aws_calls)
        self.assertNotIn("delete-role", aws_calls)
        self.assertNotIn("start-execution", aws_calls)
        self.assertNotIn("apigateway", aws_calls)
        self.assertNotRegex(
            aws_calls,
            r"lambda (?:create-function|update-function-code|"
            r"update-function-configuration) .*journalm8-staging-api",
        )

    def test_deletion_worker_tag_contract_remains_exact(self):
        helper = (ROOT / "bin" / "jm8_resource_tags.sh").read_text()
        self.assertIn(
            '"${APP_NAME}-${STAGE}-account-deletion-worker"',
            helper,
        )
        self.assertNotIn(
            '"${APP_NAME}-${STAGE}-account-deletion-*"',
            helper,
        )

    def test_deploy_guard_cannot_be_disabled_for_an_arbitrary_lambda(self):
        result, aws_calls = run_mocked_staging_deletion_deploy({
            "JM8_DISABLE_ENVIRONMENT_VALIDATION": "1",
            "LAMBDA_FUNCTION_NAME": "arbitrary-lambda",
        })

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LAMBDA_FUNCTION_NAME", result.stderr)
        self.assertNotIn("Package complete", result.stdout)
        self.assertNotIn("lambda ", aws_calls)

    def test_deletion_create_and_update_cli_tokens_are_not_concatenated(self):
        deploy = (ROOT / "bin" / "deploy-account-deletion").read_text()
        for required in (
            "aws lambda create-function",
            "aws lambda update-function-configuration",
            "aws stepfunctions create-state-machine",
            "aws stepfunctions update-state-machine",
            "--runtime python3.12",
            "--architectures arm64",
            "--query stateMachineArn",
            '--profile "$AWS_PROFILE" --region "$AWS_REGION"',
        ):
            self.assertIn(required, deploy)
        for forbidden in (
            '--profile"$AWS_PROFILE"',
            '--secret-id"$STRIPE_SECRET_ARN"',
            '"$role_name"--tags',
            '--role-name"$STEP_ROLE_NAME"',
            '--resource-arn"$WORKFLOW_ARN"',
            "DurablePartialFailures--statistic",
            "--evaluation-periods1",
            '--dashboard-name"${APP_NAME}-${STAGE}-account-deletion"',
            '[ "$COGNITO_USER_POOL_NAME" !="${APP_NAME}-${STAGE}-users" ]',
            '"$AWS_PROFILE"--region',
            "--querystateMachineArn",
            "--runtimepython",
            '--region"$AWS_REGION"',
        ):
            self.assertNotIn(forbidden, deploy)

    def test_every_deletion_aws_command_has_safe_token_boundaries(self):
        deploy = (ROOT / "bin" / "deploy-account-deletion").read_text()
        commands = all_aws_command_tokens(deploy)
        self.assertEqual(len(commands), 42)
        self.assertEqual(
            Counter((tokens[1], tokens[2]) for _, tokens in commands),
            Counter({
                ("sts", "get-caller-identity"): 1,
                ("cognito-idp", "describe-user-pool"): 1,
                ("secretsmanager", "describe-secret"): 1,
                ("iam", "get-role"): 3,
                ("iam", "create-role"): 1,
                ("iam", "update-assume-role-policy"): 1,
                ("iam", "tag-role"): 1,
                ("iam", "put-role-policy"): 2,
                ("iam", "attach-role-policy"): 1,
                ("lambda", "get-function"): 2,
                ("lambda", "update-function-code"): 1,
                ("lambda", "wait"): 3,
                ("lambda", "update-function-configuration"): 1,
                ("lambda", "create-function"): 1,
                ("lambda", "put-function-concurrency"): 1,
                ("logs", "create-log-group"): 3,
                ("logs", "put-retention-policy"): 3,
                ("logs", "put-metric-filter"): 5,
                ("stepfunctions", "list-state-machines"): 1,
                ("stepfunctions", "create-state-machine"): 1,
                ("stepfunctions", "update-state-machine"): 1,
                ("stepfunctions", "tag-resource"): 1,
                ("cloudwatch", "put-metric-alarm"): 5,
                ("cloudwatch", "put-dashboard"): 1,
            }),
        )
        assert_safe_aws_tokens(self, deploy)

        for line_number, tokens in commands:
            with self.subTest(line=line_number, command=tokens[:3]):
                self.assertEqual(tokens.count("--profile"), 1)
                profile_index = tokens.index("--profile")
                self.assertEqual(tokens[profile_index + 1], "$AWS_PROFILE")
                if tokens[1] == "iam":
                    self.assertNotIn("--region", tokens)
                else:
                    self.assertEqual(tokens.count("--region"), 1)
                    region_index = tokens.index("--region")
                    self.assertEqual(tokens[region_index + 1], "$AWS_REGION")
                self.assertEqual(
                    "--query" in tokens,
                    "--output" in tokens,
                )
                if "--output" in tokens:
                    output_index = tokens.index("--output")
                    self.assertEqual(tokens[output_index + 1], "text")

    def test_every_deletion_test_expression_has_separate_tokens(self):
        deploy = (ROOT / "bin" / "deploy-account-deletion").read_text()
        expressions = re.findall(r"(?<!\[)\[\s.*?\s\](?!\])", deploy)
        self.assertEqual(len(expressions), 9)
        for expression in expressions:
            with self.subTest(expression=expression):
                tokens = shlex.split(expression)
                self.assertEqual(tokens[0], "[")
                self.assertEqual(tokens[-1], "]")
                if len(tokens) == 4:
                    self.assertIn(tokens[1], {"-n", "-z"})
                else:
                    self.assertEqual(len(tokens), 5)
                    self.assertIn(tokens[2], {"=", "!=", "-eq", "-le"})

    def test_phase_deployment_scripts_have_no_joined_shell_tokens(self):
        joined_source_patterns = (
            re.compile(r"--[a-z][a-z0-9-]*(?=[\"$])"),
            re.compile(r"(?:\"\$[A-Za-z_][A-Za-z0-9_]*\"|\$\{[^}]+\})--[a-z]"),
            re.compile(r"[A-Za-z0-9_}]--[a-z]"),
            re.compile(r"--[a-z][a-z0-9-]*[0-9]+\b"),
        )
        for name in (
            "create-auth",
            "deploy",
            "deploy-account-deletion",
            "deploy-observability",
        ):
            source = (ROOT / "bin" / name).read_text()
            with self.subTest(script=name):
                for pattern in joined_source_patterns:
                    self.assertIsNone(pattern.search(source))

    def test_deletion_aws_options_exist_in_botocore_service_models(self):
        deploy = (ROOT / "bin" / "deploy-account-deletion").read_text()
        clients = {}
        custom_cli_options = {
            ("lambda", "create-function"): {"--zip-file"},
        }
        for line_number, tokens in all_aws_command_tokens(deploy):
            service, operation = tokens[1:3]
            if service not in clients:
                clients[service] = boto3.client(
                    service,
                    region_name="us-east-1",
                    aws_access_key_id="testing",
                    aws_secret_access_key="testing",
                )
            client = clients[service]
            if operation == "wait":
                waiter_name = tokens[3].replace("-", "_")
                with self.subTest(line=line_number, waiter=waiter_name):
                    self.assertIn(waiter_name, client.waiter_names)
                    self.assertEqual(
                        {
                            token for token in tokens[4:]
                            if token.startswith("--")
                        } - AWS_GLOBAL_OPTIONS,
                        {"--function-name"},
                    )
                continue

            operation_name = "".join(
                part.title() for part in operation.split("-")
            )
            model = client.meta.service_model.operation_model(operation_name)
            members = model.input_shape.members if model.input_shape else {}
            modeled_options = {
                cli_option_for_member(member) for member in members
            }
            modeled_options.update(AWS_GLOBAL_OPTIONS)
            modeled_options.update(
                custom_cli_options.get((service, operation), set())
            )
            actual_options = {
                token for token in tokens[3:] if token.startswith("--")
            }
            with self.subTest(
                line=line_number,
                service=service,
                operation=operation,
            ):
                self.assertEqual(actual_options - modeled_options, set())

    def test_sensitive_and_observability_command_values_are_separate(self):
        deploy = (ROOT / "bin" / "deploy-account-deletion").read_text()
        commands = [tokens for _, tokens in all_aws_command_tokens(deploy)]

        def commands_for(service: str, operation: str) -> list[list[str]]:
            return [
                tokens for tokens in commands
                if tokens[1:3] == [service, operation]
            ]

        cognito = commands_for("cognito-idp", "describe-user-pool")[0]
        self.assertEqual(
            cognito[cognito.index("--user-pool-id") + 1],
            "$COGNITO_USER_POOL_ID",
        )
        secret = commands_for("secretsmanager", "describe-secret")[0]
        self.assertEqual(
            secret[secret.index("--secret-id") + 1],
            "$STRIPE_SECRET_ARN",
        )

        step_policy = commands_for("iam", "put-role-policy")[1]
        self.assertEqual(
            step_policy[step_policy.index("--role-name") + 1],
            "$STEP_ROLE_NAME",
        )
        tag_workflow = commands_for("stepfunctions", "tag-resource")[0]
        self.assertEqual(
            tag_workflow[tag_workflow.index("--resource-arn") + 1],
            "$WORKFLOW_ARN",
        )

        metric_filters = commands_for("logs", "put-metric-filter")
        self.assertEqual(len(metric_filters), 5)
        self.assertTrue(all(
            "--metric-transformations" in command
            for command in metric_filters
        ))
        alarms = commands_for("cloudwatch", "put-metric-alarm")
        self.assertEqual(len(alarms), 5)
        self.assertEqual(
            {
                command[command.index("--metric-name") + 1]
                for command in alarms
            },
            {
                "Errors",
                "ExecutionsFailed",
                "ExecutionsTimedOut",
                "DeletionFailures",
                "DurablePartialFailures",
            },
        )
        for command in alarms:
            self.assertEqual(command[command.index("--statistic") + 1], "Sum")
            self.assertEqual(
                command[command.index("--evaluation-periods") + 1], "1"
            )
            self.assertEqual(
                command[command.index("--tags") + 1], "${ALARM_TAGS[@]}"
            )

        dashboard = commands_for("cloudwatch", "put-dashboard")[0]
        self.assertEqual(
            dashboard[dashboard.index("--dashboard-name") + 1],
            "${APP_NAME}-${STAGE}-account-deletion",
        )

    def test_actual_create_and_update_command_tokens_match_aws_operations(self):
        deploy = (ROOT / "bin" / "deploy-account-deletion").read_text()
        create = shell_command_tokens(deploy, "lambda", "create-function")
        update_code = shell_command_tokens(deploy, "lambda", "update-function-code")
        update_config = shell_command_tokens(
            deploy, "lambda", "update-function-configuration"
        )
        self.assertEqual(len(create), 1)
        self.assertEqual(len(update_code), 1)
        self.assertEqual(len(update_config), 1)
        for tokens in (create[0], update_code[0], update_config[0]):
            self.assertEqual(
                tokens[tokens.index("--function-name") + 1],
                "$WORKER_NAME",
            )
            self.assertEqual(tokens[tokens.index("--profile") + 1], "$AWS_PROFILE")
            self.assertEqual(tokens[tokens.index("--region") + 1], "$AWS_REGION")
        for option, expected in (
            ("--runtime", "python3.12"),
            ("--handler", "account_deletion_worker.lambda_handler"),
            ("--timeout", "900"),
            ("--memory-size", "512"),
            ("--environment", "file://${BUILD_DIR}/worker-environment.json"),
        ):
            self.assertEqual(create[0][create[0].index(option) + 1], expected)
            self.assertEqual(
                update_config[0][update_config[0].index(option) + 1], expected
            )
        self.assertEqual(
            create[0][create[0].index("--architectures") + 1], "arm64"
        )
        self.assertEqual(
            update_code[0][update_code[0].index("--architectures") + 1],
            "arm64",
        )
        self.assertNotIn("--architectures", update_config[0])
        self.assertIn("--ephemeral-storage", create[0])
        self.assertIn("--ephemeral-storage", update_config[0])
        self.assertIn("--zip-file", create[0])
        self.assertIn("--zip-file", update_code[0])
        self.assertIn("--tags", create[0])

        create_state = shell_command_tokens(
            deploy, "stepfunctions", "create-state-machine"
        )[0]
        update_state = shell_command_tokens(
            deploy, "stepfunctions", "update-state-machine"
        )[0]
        self.assertEqual(
            create_state[create_state.index("--type") + 1], "STANDARD"
        )
        self.assertEqual(
            create_state[create_state.index("--query") + 1], "stateMachineArn"
        )
        for tokens in (create_state, update_state):
            self.assertIn("--role-arn", tokens)
            self.assertIn("--definition", tokens)
            self.assertIn("--logging-configuration", tokens)
            self.assertEqual(tokens[tokens.index("--profile") + 1], "$AWS_PROFILE")
            self.assertEqual(tokens[tokens.index("--region") + 1], "$AWS_REGION")

        concurrency = shell_command_tokens(
            deploy, "lambda", "put-function-concurrency"
        )[0]
        self.assertEqual(
            concurrency[concurrency.index("--reserved-concurrent-executions") + 1],
            "$RESERVED_CONCURRENCY",
        )

        lambda_model = boto3.client(
            "lambda",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        ).meta.service_model
        update_code_fields = lambda_model.operation_model(
            "UpdateFunctionCode"
        ).input_shape.members
        update_config_fields = lambda_model.operation_model(
            "UpdateFunctionConfiguration"
        ).input_shape.members
        self.assertIn("Architectures", update_code_fields)
        self.assertNotIn("Architectures", update_config_fields)

    def test_generated_runtime_policies_are_structurally_least_privilege(self):
        deletion = (ROOT / "bin" / "deploy-account-deletion").read_text()
        main = (ROOT / "bin" / "deploy").read_text()
        region = "us-east-1"
        account = "114743615542"
        workflows = [
            f"arn:aws:states:{region}:{account}:stateMachine:journalm8-prod-ocr-workflow",
            f"arn:aws:states:{region}:{account}:stateMachine:journalm8-prod-historical-reanalysis-workflow",
            f"arn:aws:states:{region}:{account}:stateMachine:journalm8-prod-account-export-workflow",
        ]
        worker_policy = render_embedded_json(
            deletion,
            "PY_WORKER_POLICY",
            [
                region,
                account,
                "journalm8-prod-main",
                "journalm8-prod-entry-chunks",
                f"journalm8-prod-raw-{account}",
                f"journalm8-prod-exports-{account}",
                "us-east-1_Production",
                (
                    f"arn:aws:secretsmanager:{region}:{account}:"
                    "secret:journalm8/prod/stripe-ABC123"
                ),
                *workflows,
            ],
        )
        statements = {
            statement["Sid"]: statement
            for statement in worker_policy["Statement"]
        }
        self.assertEqual(
            statements["ExactMainTable"]["Resource"],
            f"arn:aws:dynamodb:{region}:{account}:table/journalm8-prod-main",
        )
        self.assertEqual(statements["ExactEntryChunksTable"], {
            "Sid": "ExactEntryChunksTable",
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:Query",
                "dynamodb:BatchWriteItem",
            ],
            "Resource": (
                f"arn:aws:dynamodb:{region}:{account}:"
                "table/journalm8-prod-entry-chunks"
            ),
        })
        self.assertEqual(
            statements["ExactCognitoPool"]["Resource"],
            f"arn:aws:cognito-idp:{region}:{account}:userpool/us-east-1_Production",
        )
        self.assertEqual(statements["InspectKnownWorkflows"]["Resource"], workflows)
        self.assertEqual(
            statements["ReadExistingStripeSecret"]["Resource"],
            f"arn:aws:secretsmanager:{region}:{account}:secret:journalm8/prod/stripe-ABC123",
        )
        self.assertNotIn("*", statements["ExactMainTable"]["Resource"])
        self.assertNotIn("*", statements["ExactEntryChunksTable"]["Resource"])
        for statement in worker_policy["Statement"]:
            self.assertNotEqual(statement["Resource"], "*")
            for action in statement["Action"]:
                self.assertFalse(action.endswith(":*"))

        workflow_arn = (
            f"arn:aws:states:{region}:{account}:"
            "stateMachine:journalm8-prod-account-deletion-workflow"
        )
        api_policy = render_embedded_json(
            main, "PY_ACCOUNT_DELETION", [workflow_arn]
        )
        self.assertEqual(
            api_policy["Statement"],
            [{
                "Effect": "Allow",
                "Action": ["states:StartExecution", "states:ListExecutions"],
                "Resource": workflow_arn,
            }],
        )

        step_policy = render_embedded_json(
            deletion,
            "PY_STEP_POLICY",
            [f"arn:aws:lambda:{region}:{account}:function:journalm8-prod-account-deletion-worker"],
        )
        wildcard = [
            statement for statement in step_policy["Statement"]
            if statement["Resource"] == "*"
        ]
        self.assertEqual(len(wildcard), 1)
        self.assertTrue(all(
            action.startswith("logs:") for action in wildcard[0]["Action"]
        ))

    def test_observability_covers_safe_deletion_failure_modes(self):
        observability = (ROOT / "bin" / "deploy-observability").read_text()
        for required in (
            "AccountDeletionRequested",
            "AccountDeletionWorkflowStartFailed",
            "AccountDeletionActionCompleted",
            "DurablePartialFailures",
            "ExecutionsFailed",
            "ExecutionsTimedOut",
            "account-deletion-worker-errors",
            "account-deletion-durable-partial-failures",
            "--retention-in-days 30",
            "Account deletion safety and completion",
        ):
            self.assertIn(required, observability)
        for forbidden in (
            "requestToken",
            "stripeCustomerId",
            "workflowExecutionArn",
            "userId",
        ):
            self.assertNotIn(forbidden, observability)

    def test_observability_reconcilers_use_identical_filter_names(self):
        deletion = (ROOT / "bin" / "deploy-account-deletion").read_text()
        observability = (ROOT / "bin" / "deploy-observability").read_text()
        for suffix in (
            "account-deletion-requested",
            "account-deletion-workflow-start-failed",
            "account-deletion-completed",
            "account-deletion-failed",
            "account-deletion-durable-partial-failed",
        ):
            expected = f'"${{APP_NAME}}-${{STAGE}}-{suffix}"'
            self.assertIn(expected, deletion)
            self.assertIn(expected, observability)

    def test_only_log_delivery_control_plane_uses_unavoidable_wildcard(self):
        deploy = (ROOT / "bin" / "deploy-account-deletion").read_text()
        self.assertEqual(deploy.count('"Resource":"*"'), 1)
        self.assertIn("logs:CreateLogDelivery", deploy)
        self.assertIn("logs:PutResourcePolicy", deploy)
        self.assertIn(
            'uses `Resource: "*"` only for the CloudWatch Logs delivery',
            self.contract,
        )

    def test_documented_contract_preserves_order_and_retention(self):
        self.assertIn("Phase 3C3 account deletion coordination", self.contract)
        self.assertIn("requires the exact stage-scoped workflow", self.contract)
        self.assertIn("Phase 3C3C", self.contract)
        for required in (
            "ACCOUNT_DELETION#<del_request_id>",
            "ACCOUNT_DELETION_SUBJECT#<subject_digest>",
            "CloudWatch",
            "up to 30 days",
            "DynamoDB PITR",
            "up to 35 days",
            "Stripe",
        ):
            self.assertIn(required, self.contract)
        order = (
            "`START` verifies",
            "`QUIESCE` inspects",
            "`CANCEL_SUBSCRIPTION` stops",
            "`DELETE_RAW_OBJECTS` deletes",
            "`DELETE_EXPORT_OBJECTS` performs",
            "`DELETE_APPLICATION_DATA` repeatedly",
            "`DELETE_COGNITO_IDENTITY` first",
            "`VERIFY` proves",
            "`COMPLETE` records",
        )
        positions = [self.contract.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
