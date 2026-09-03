from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import textwrap
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CREATE_API = BACKEND_ROOT / "bin" / "create-api"
ACCOUNT_ID = "114743615542"
REGION = "us-east-1"
STAGING_ORIGIN = "https://d1111111111111.cloudfront.net"
PRODUCTION_ORIGIN = "https://d2222222222222.cloudfront.net"


class StageScopedFrontendOriginBehaviorTests(unittest.TestCase):
    maxDiff = None

    @staticmethod
    def _environment(stage: str, origin: str) -> dict[str, str]:
        production = stage == "prod"
        environment = {
            "APP_NAME": "journalm8",
            "STAGE": stage,
            "AWS_PROFILE": "jm8-prod" if production else "jm8-dev",
            "AWS_REGION": REGION,
            "EXPECTED_AWS_ACCOUNT_ID": ACCOUNT_ID,
            "ACCOUNT_ID": ACCOUNT_ID,
            "TABLE_NAME": f"journalm8-{stage}-main",
            "RAW_BUCKET": f"journalm8-{stage}-raw-{ACCOUNT_ID}",
            "FRONTEND_BUCKET": f"journalm8-{stage}-frontend-{ACCOUNT_ID}",
            "API_NAME": f"journalm8-{stage}-api",
            "FRONTEND_ORIGIN": origin,
            "ALLOWED_ORIGINS": origin,
            "DEPLOY_CONFIRMATION": stage,
        }
        if production:
            environment["PRODUCTION_ISOLATION_MODE"] = (
                "stage-scoped-same-account"
            )
        return environment

    @staticmethod
    def _aws_shim_source() -> str:
        return textwrap.dedent(
            r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
log_path = Path(os.environ["JM8_AWS_CALL_LOG"])
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")

service = args[0] if args else ""
operation = args[1] if len(args) > 1 else ""
stage = os.environ["JM8_TEST_STAGE"]
origin = os.environ["JM8_TEST_STACK_ORIGIN"]
stack_mode = os.environ.get("JM8_TEST_STACK_MODE", "valid")
api_mode = os.environ.get("JM8_TEST_API_MODE", "update")
account = "114743615542"
region = "us-east-1"
app = "journalm8"
stack_name = f"{app}-{stage}-frontend-hosting"
api_name = f"{app}-{stage}-api"
api_id = "api1234567"

def emit(value):
    if isinstance(value, (dict, list)):
        print(json.dumps(value))
    else:
        print(value)

if (service, operation) == ("sts", "get-caller-identity"):
    emit(account)
elif (service, operation) == ("cloudformation", "describe-stacks"):
    stack = {
        "StackName": stack_name,
        "StackId": (
            f"arn:aws:cloudformation:{region}:{account}:"
            f"stack/{stack_name}/stack-id"
        ),
        "StackStatus": "UPDATE_COMPLETE",
        "Outputs": [{"OutputKey": "FrontendOrigin", "OutputValue": origin}],
    }
    if stack_mode == "missing":
        emit({"Stacks": []})
    elif stack_mode == "ambiguous":
        emit({"Stacks": [stack, dict(stack)]})
    elif stack_mode == "missing-output":
        stack["Outputs"] = []
        emit({"Stacks": [stack]})
    elif stack_mode == "unstable":
        stack["StackStatus"] = "UPDATE_IN_PROGRESS"
        emit({"Stacks": [stack]})
    else:
        emit({"Stacks": [stack]})
elif (service, operation) == ("lambda", "get-function"):
    emit(f"arn:aws:lambda:{region}:{account}:function:{api_name}")
elif (service, operation) == ("lambda", "add-permission"):
    pass
elif (service, operation) == ("apigatewayv2", "get-apis"):
    if api_mode == "create":
        emit({"Items": []})
    else:
        emit({"Items": [{"ApiId": api_id, "Name": api_name}]})
elif (service, operation) == ("apigatewayv2", "create-api"):
    emit(api_id)
elif (service, operation) == ("apigatewayv2", "get-api"):
    query = args[args.index("--query") + 1] if "--query" in args else ""
    if query == "ApiEndpoint":
        emit(f"https://{api_id}.execute-api.{region}.amazonaws.com")
    else:
        emit({
            "ApiId": api_id,
            "Name": api_name,
            "ProtocolType": "HTTP",
            "Tags": {"App": app, "Stage": stage, "ManagedBy": "aws-cli"},
        })
elif (service, operation) == ("apigatewayv2", "get-tags"):
    emit({"Tags": {"App": app, "Stage": stage, "ManagedBy": "aws-cli"}})
elif (service, operation) == ("apigatewayv2", "get-integrations"):
    emit("integration123")
elif (service, operation) == ("apigatewayv2", "get-routes"):
    query = args[args.index("--query") + 1]
    if query.startswith("length("):
        emit("1")
    elif query.endswith(".RouteId | [0]"):
        emit("route123")
    elif query.endswith(".Target | [0]"):
        emit("integrations/integration123")
    else:
        raise SystemExit(91)
elif (service, operation) == ("apigatewayv2", "get-stages"):
    emit("$default")
elif service == "apigatewayv2" and operation in {
    "tag-resource", "update-api", "update-integration", "update-route",
    "update-stage",
}:
    pass
elif (service, operation) == ("logs", "describe-log-groups"):
    emit(f"/aws/apigateway/{api_name}")
elif service == "logs" and operation in {
    "put-retention-policy", "put-resource-policy",
}:
    pass
else:
    print(f"unsupported AWS shim call: {service} {operation}", file=sys.stderr)
    raise SystemExit(92)
'''
        )

    def _run_create_api(
        self,
        stage: str,
        supplied_origin: str,
        *,
        allowed_origins: str | None = None,
        stack_origin: str | None = None,
        stack_mode: str = "valid",
        api_mode: str = "update",
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            shim_directory = temporary / "bin"
            shim_directory.mkdir()
            aws_shim = shim_directory / "aws"
            aws_shim.write_text(self._aws_shim_source(), encoding="utf-8")
            aws_shim.chmod(0o755)
            call_log = temporary / "aws-calls.jsonl"

            values = self._environment(stage, supplied_origin)
            values["ALLOWED_ORIGINS"] = (
                supplied_origin if allowed_origins is None else allowed_origins
            )
            values.update({
                "JM8_AWS_CALL_LOG": str(call_log),
                "JM8_TEST_STAGE": stage,
                "JM8_TEST_STACK_ORIGIN": stack_origin or supplied_origin,
                "JM8_TEST_STACK_MODE": stack_mode,
                "JM8_TEST_API_MODE": api_mode,
                "PATH": f"{shim_directory}:{os.environ['PATH']}",
            })

            environment_file = temporary / f"{stage}.env"
            environment_file.write_text(
                "".join(
                    f"export {name}={shlex.quote(value)}\n"
                    for name, value in sorted(values.items())
                ),
                encoding="utf-8",
            )
            process_environment = {
                "HOME": os.environ.get("HOME", str(temporary)),
                "PATH": os.environ["PATH"],
                "TMPDIR": str(temporary),
            }
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    'set -a; source "$1"; exec "$2"',
                    "stage-cors-test",
                    str(environment_file),
                    str(CREATE_API),
                ],
                cwd=BACKEND_ROOT,
                env=process_environment,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            calls = []
            if call_log.exists():
                calls = [
                    json.loads(line)
                    for line in call_log.read_text(encoding="utf-8").splitlines()
                    if line
                ]
            return result, calls

    @staticmethod
    def _mutating_api_calls(calls: list[list[str]]) -> list[list[str]]:
        mutations = {
            "create-api",
            "update-api",
            "tag-resource",
            "create-integration",
            "update-integration",
            "create-route",
            "update-route",
            "create-stage",
            "update-stage",
        }
        return [
            call for call in calls
            if len(call) > 1
            and call[0] == "apigatewayv2"
            and call[1] in mutations
        ]

    @staticmethod
    def _option(call: list[str], name: str) -> str:
        return call[call.index(name) + 1]

    def test_correct_staging_origin_passes_from_export_environment_file(self):
        result, calls = self._run_create_api("staging", STAGING_ORIGIN)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(self._mutating_api_calls(calls))
        stack_calls = [
            call for call in calls
            if call[:2] == ["cloudformation", "describe-stacks"]
        ]
        self.assertEqual(len(stack_calls), 1)
        self.assertEqual(
            self._option(stack_calls[0], "--stack-name"),
            "journalm8-staging-frontend-hosting",
        )

    def test_create_and_update_paths_use_identical_single_origin_cors(self):
        expected_cors = {
            "AllowOrigins": [STAGING_ORIGIN],
            "AllowMethods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "AllowHeaders": ["content-type", "x-user-id", "authorization"],
            "MaxAge": 300,
        }
        for api_mode in ("create", "update"):
            with self.subTest(api_mode=api_mode):
                result, calls = self._run_create_api(
                    "staging", STAGING_ORIGIN, api_mode=api_mode
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                cors_calls = [
                    call for call in calls
                    if call[:2] in (
                        ["apigatewayv2", "create-api"],
                        ["apigatewayv2", "update-api"],
                    )
                ]
                if api_mode == "create":
                    self.assertEqual(len(cors_calls), 2)
                else:
                    self.assertEqual(len(cors_calls), 1)
                    self.assertEqual(cors_calls[0][1], "update-api")
                for call in cors_calls:
                    self.assertEqual(
                        json.loads(self._option(call, "--cors-configuration")),
                        expected_cors,
                    )

    def test_cross_stage_origins_fail_before_api_mutation(self):
        cases = (
            ("staging", PRODUCTION_ORIGIN, STAGING_ORIGIN),
            ("prod", STAGING_ORIGIN, PRODUCTION_ORIGIN),
        )
        for stage, supplied_origin, stack_origin in cases:
            with self.subTest(stage=stage):
                result, calls = self._run_create_api(
                    stage,
                    supplied_origin,
                    stack_origin=stack_origin,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self._mutating_api_calls(calls), [])

    def test_mismatched_multiple_and_wildcard_origins_fail_before_mutation(self):
        cases = (
            "https://different.example.com",
            f"{STAGING_ORIGIN},https://different.example.com",
            "*",
        )
        for allowed_origins in cases:
            with self.subTest(allowed_origins=allowed_origins):
                result, calls = self._run_create_api(
                    "staging",
                    STAGING_ORIGIN,
                    allowed_origins=allowed_origins,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self._mutating_api_calls(calls), [])

    def test_missing_ambiguous_unstable_or_outputless_stack_fails_closed(self):
        for stack_mode in (
            "missing",
            "ambiguous",
            "unstable",
            "missing-output",
        ):
            with self.subTest(stack_mode=stack_mode):
                result, calls = self._run_create_api(
                    "staging",
                    STAGING_ORIGIN,
                    stack_mode=stack_mode,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self._mutating_api_calls(calls), [])

    def test_malformed_stack_origins_fail_before_mutation(self):
        for stack_origin in (
            "http://d1111111111111.cloudfront.net",
            "https://d1111111111111.cloudfront.net/path",
            "https://user@d1111111111111.cloudfront.net",
            "https://d1111111111111.cloudfront.net:444",
        ):
            with self.subTest(stack_origin=stack_origin):
                result, calls = self._run_create_api(
                    "staging",
                    STAGING_ORIGIN,
                    stack_origin=stack_origin,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self._mutating_api_calls(calls), [])


if __name__ == "__main__":
    unittest.main()
