import json
import io
import os
import shlex
import sys
import time
import unittest
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
            '"$AWS_PROFILE"--region',
            "--querystateMachineArn",
            "--runtimepython",
            '--region"$AWS_REGION"',
        ):
            self.assertNotIn(forbidden, deploy)

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
