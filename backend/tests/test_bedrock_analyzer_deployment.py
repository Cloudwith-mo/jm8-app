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
DEPLOY_SCRIPT = BIN_DIR / "deploy"
CONFIGURE_SCRIPT = BIN_DIR / "configure-bedrock-analyzer"
POLICY_HELPER = BIN_DIR / "jm8_bedrock_analysis_policy.sh"
POLICY_TOOL = BIN_DIR / "jm8_bedrock_analysis_policy.py"
DEFAULT_PROFILE = object()

sys.path.insert(0, str(BIN_DIR))

from jm8_bedrock_analysis_policy import (  # noqa: E402
    build_policy,
    verify_applied_policy,
)


class BedrockAnalyzerDeploymentTests(unittest.TestCase):
    MODEL_ID = "us.anthropic.claude-haiku-test-v1:0"
    ACCOUNT_ID = "111122223333"
    PROFILE_ARN = (
        "arn:aws:bedrock:us-east-1:111122223333:"
        "inference-profile/us.anthropic.claude-haiku-test-v1:0"
    )
    MODEL_ARNS = [
        (
            "arn:aws:bedrock:us-east-1::foundation-model/"
            "anthropic.claude-haiku-test-v1:0"
        ),
        (
            "arn:aws:bedrock:us-east-2::foundation-model/"
            "anthropic.claude-haiku-test-v1:0"
        ),
        (
            "arn:aws:bedrock:us-west-2::foundation-model/"
            "anthropic.claude-haiku-test-v1:0"
        ),
    ]

    @classmethod
    def setUpClass(cls):
        cls.deploy = DEPLOY_SCRIPT.read_text()
        cls.configure = CONFIGURE_SCRIPT.read_text()
        cls.helper = POLICY_HELPER.read_text()
        cls.tool = POLICY_TOOL.read_text()

    def _profile(self):
        return {
            "inferenceProfileId": self.MODEL_ID,
            "inferenceProfileArn": self.PROFILE_ARN,
            "models": [
                {"modelArn": model_arn}
                for model_arn in reversed(self.MODEL_ARNS)
            ],
        }

    def _build_policy(self, profile=DEFAULT_PROFILE, stage="staging"):
        return build_policy(
            self._profile() if profile is DEFAULT_PROFILE else profile,
            self.MODEL_ID,
            "aws",
            "us-east-1",
            self.ACCOUNT_ID,
            "journalm8",
            stage,
        )

    def _fake_aws_script(self):
        return r'''#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >> "$FAKE_AWS_EVENTS"

case "$1:$2" in
  sts:get-caller-identity)
    if [ "$FAKE_AWS_SCENARIO" = "credentials" ]; then
      echo "Unable to locate credentials" >&2
      exit 51
    fi
    if [ "$FAKE_AWS_SCENARIO" = "wrong_account" ]; then
      printf '%s\n' "arn:aws:sts::999900001111:assumed-role/test/session"
      exit 0
    fi
    printf '%s\n' "arn:aws:sts::${EXPECTED_AWS_ACCOUNT_ID}:assumed-role/test/session"
    ;;
  iam:get-role)
    if [ "$FAKE_AWS_SCENARIO" = "missing_role" ]; then
      echo "An error occurred (NoSuchEntity) when calling GetRole" >&2
      exit 52
    fi
    if [ "$FAKE_AWS_SCENARIO" = "wrong_role" ]; then
      printf '%s\n' \
        "arn:aws:iam::${EXPECTED_AWS_ACCOUNT_ID}:role/${APP_NAME}-dev-lambda-basic-role"
      exit 0
    fi
    printf '%s\n' \
      "arn:aws:iam::${EXPECTED_AWS_ACCOUNT_ID}:role/${APP_NAME}-${STAGE}-lambda-basic-role"
    ;;
  bedrock:get-inference-profile)
    case "$FAKE_AWS_SCENARIO" in
      access_denied)
        echo "An error occurred (AccessDeniedException)" >&2
        exit 53
        ;;
      network)
        echo "Could not connect to the endpoint URL" >&2
        exit 54
        ;;
      malformed_profile)
        printf '%s\n' '{}'
        ;;
      *)
        cat "$FAKE_AWS_PROFILE_RESPONSE"
        ;;
    esac
    ;;
  iam:put-role-policy)
    if [ "$FAKE_AWS_SCENARIO" = "put_failure" ]; then
      echo "An error occurred (AccessDeniedException) when calling PutRolePolicy" >&2
      exit 56
    fi
    for argument in "$@"; do
      case "$argument" in
        file://*)
          cp "${argument#file://}" "$FAKE_AWS_POLICY_CAPTURE"
          ;;
      esac
    done
    ;;
  iam:get-role-policy)
    python3 - \
      "$FAKE_AWS_POLICY_CAPTURE" \
      "${APP_NAME}-${STAGE}-lambda-basic-role" \
      "${APP_NAME}-${STAGE}-bedrock-analysis" \
      "$FAKE_AWS_SCENARIO" <<'PY'
import json
import sys
from pathlib import Path

policy = json.loads(Path(sys.argv[1]).read_text())
if sys.argv[4] == "policy_mismatch":
    policy["Statement"][0]["Action"].append("bedrock:ListInferenceProfiles")

print(json.dumps({
    "RoleName": sys.argv[2],
    "PolicyName": sys.argv[3],
    "PolicyDocument": policy,
}))
PY
    ;;
  *)
    echo "Unexpected fake AWS command: $*" >&2
    exit 55
    ;;
esac
'''

    def _run_reconciler(self, scenario, rerun=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_aws = fake_bin / "aws"
            fake_aws.write_text(self._fake_aws_script())
            fake_aws.chmod(0o755)

            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps(self._profile()))
            events_path = root / "events.txt"
            policy_capture = root / "captured-policy.json"
            build_dir = root / "build"

            harness = r'''#!/usr/bin/env bash
set -euo pipefail
source "$1"
jm8_reconcile_bedrock_analysis_policy "$MODEL_ID" "$BUILD_DIR"
if [ "$RERUN" = "yes" ]; then
  jm8_reconcile_bedrock_analysis_policy "$MODEL_ID" "$BUILD_DIR"
fi
'''
            harness_path = root / "harness.sh"
            harness_path.write_text(harness)
            harness_path.chmod(0o755)

            environment = os.environ.copy()
            environment.update({
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "AWS_PROFILE": "jm8-staging",
                "AWS_REGION": "us-east-1",
                "APP_NAME": "journalm8",
                "STAGE": "staging",
                "EXPECTED_AWS_ACCOUNT_ID": self.ACCOUNT_ID,
                "MODEL_ID": self.MODEL_ID,
                "BUILD_DIR": str(build_dir),
                "RERUN": "yes" if rerun else "no",
                "FAKE_AWS_SCENARIO": scenario,
                "FAKE_AWS_EVENTS": str(events_path),
                "FAKE_AWS_PROFILE_RESPONSE": str(profile_path),
                "FAKE_AWS_POLICY_CAPTURE": str(policy_capture),
            })
            result = subprocess.run(
                ["bash", str(harness_path), str(POLICY_HELPER)],
                cwd=BACKEND_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            events = (
                events_path.read_text().splitlines()
                if events_path.exists()
                else []
            )
            captured_policy = (
                json.loads(policy_capture.read_text())
                if policy_capture.exists()
                else None
            )

        return result, events, captured_policy

    def test_deploy_reconciles_and_verifies_before_lambda_write(self):
        reconcile_index = self.deploy.index(
            "jm8_reconcile_bedrock_analysis_policy"
        )
        create_index = self.deploy.index("aws lambda create-function")
        update_index = self.deploy.index("aws lambda update-function-code")
        self.assertLess(reconcile_index, create_index)
        self.assertLess(reconcile_index, update_index)
        self.assertLess(
            self.helper.index("aws iam put-role-policy"),
            self.helper.index("aws iam get-role-policy"),
        )
        self.assertLess(
            self.helper.index("aws iam get-role-policy"),
            self.helper.index("    verify \\"),
        )

    def test_configure_and_deploy_share_one_policy_generator(self):
        source_line = 'source "$SCRIPT_DIR/jm8_bedrock_analysis_policy.sh"'
        self.assertIn(source_line, self.deploy)
        self.assertIn(source_line, self.configure)
        self.assertIn(
            'source "$SCRIPT_DIR/jm8_deployment_guard.sh"',
            self.configure,
        )
        self.assertIn("jm8_validate_contract_or_exit", self.configure)
        self.assertIn(
            "jm8_bedrock_analysis_policy.py",
            self.helper,
        )
        for entry_point in (self.deploy, self.configure):
            self.assertNotIn("ReadJM8AnalysisInferenceProfile", entry_point)
            self.assertNotIn("bedrock:GetInferenceProfile", entry_point)

    def test_policy_and_role_names_are_stage_specific(self):
        for stage in ("dev", "staging", "prod"):
            environment = os.environ.copy()
            environment.update({
                "APP_NAME": "journalm8",
                "STAGE": stage,
            })
            command = (
                'source "$1"; '
                'jm8_bedrock_analysis_role_name; printf "\\n"; '
                'jm8_bedrock_analysis_policy_name'
            )
            result = subprocess.run(
                ["bash", "-c", command, "test", str(POLICY_HELPER)],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    f"journalm8-{stage}-lambda-basic-role",
                    f"journalm8-{stage}-bedrock-analysis",
                ],
            )

    def test_policy_resources_actions_and_condition_are_exact(self):
        policy = self._build_policy()
        statements = {
            statement["Sid"]: statement
            for statement in policy["Statement"]
        }

        self.assertEqual(
            statements["ReadJM8AnalysisInferenceProfile"],
            {
                "Sid": "ReadJM8AnalysisInferenceProfile",
                "Effect": "Allow",
                "Action": ["bedrock:GetInferenceProfile"],
                "Resource": self.PROFILE_ARN,
            },
        )
        self.assertEqual(
            statements["InvokeJM8AnalysisInferenceProfile"]["Action"],
            ["bedrock:InvokeModel"],
        )
        self.assertEqual(
            statements["InvokeJM8AnalysisInferenceProfile"]["Resource"],
            self.PROFILE_ARN,
        )
        destination = statements["InvokeJM8AnalysisDestinationModels"]
        self.assertEqual(destination["Action"], ["bedrock:InvokeModel"])
        self.assertEqual(destination["Resource"], sorted(self.MODEL_ARNS))
        self.assertEqual(
            destination["Condition"],
            {
                "StringEquals": {
                    "bedrock:InferenceProfileArn": self.PROFILE_ARN,
                },
            },
        )

    def test_missing_malformed_or_wrong_scope_profiles_fail_closed(self):
        invalid_profiles = (
            None,
            {},
            {
                "inferenceProfileArn": self.PROFILE_ARN,
                "models": [],
            },
            {
                "inferenceProfileArn": "not-an-arn",
                "models": [{"modelArn": self.MODEL_ARNS[0]}],
            },
            {
                "inferenceProfileArn": self.PROFILE_ARN.replace(
                    self.ACCOUNT_ID,
                    "999900001111",
                ),
                "models": [{"modelArn": self.MODEL_ARNS[0]}],
            },
            {
                "inferenceProfileArn": self.PROFILE_ARN,
                "models": [{"modelArn": "malformed"}],
            },
            {
                "inferenceProfileArn": self.PROFILE_ARN,
                "models": [{
                    "modelArn": (
                        "arn:aws:bedrock:us-east-1:999900001111:"
                        "custom-model/other-stage"
                    ),
                }],
            },
        )
        for profile in invalid_profiles:
            with self.subTest(profile=profile), self.assertRaises(SystemExit):
                self._build_policy(profile=profile)

        with self.assertRaises(SystemExit):
            self._build_policy(stage="staging-other")

    def test_applied_policy_mismatch_role_and_name_fail_closed(self):
        expected = self._build_policy()
        base_response = {
            "RoleName": "journalm8-staging-lambda-basic-role",
            "PolicyName": "journalm8-staging-bedrock-analysis",
            "PolicyDocument": expected,
        }
        verify_applied_policy(
            base_response,
            expected,
            base_response["RoleName"],
            base_response["PolicyName"],
            self.MODEL_ID,
            "aws",
            "us-east-1",
            self.ACCOUNT_ID,
            "journalm8",
            "staging",
        )

        invalid_responses = [None, {}]
        for key, value in (
            ("RoleName", "journalm8-dev-lambda-basic-role"),
            ("PolicyName", "journalm8-dev-bedrock-analysis"),
        ):
            response = json.loads(json.dumps(base_response))
            response[key] = value
            invalid_responses.append(response)
        response = json.loads(json.dumps(base_response))
        response["PolicyDocument"]["Statement"][0]["Action"].append(
            "bedrock:ListInferenceProfiles"
        )
        invalid_responses.append(response)

        for response in invalid_responses:
            with self.subTest(response=response), self.assertRaises(SystemExit):
                verify_applied_policy(
                    response,
                    expected,
                    base_response["RoleName"],
                    base_response["PolicyName"],
                    self.MODEL_ID,
                    "aws",
                    "us-east-1",
                    self.ACCOUNT_ID,
                    "journalm8",
                    "staging",
                )

    def test_reconciliation_is_idempotent_and_verifies_each_rerun(self):
        result, events, captured_policy = self._run_reconciler(
            "success",
            rerun=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            sum(command.startswith("iam put-role-policy") for command in events),
            2,
        )
        self.assertEqual(
            sum(command.startswith("iam get-role-policy") for command in events),
            2,
        )
        self.assertEqual(captured_policy, self._build_policy())

    def test_aws_and_verification_failures_stop_reconciliation(self):
        cases = (
            ("missing_role", "NoSuchEntity"),
            ("wrong_role", "role ARN is missing or incorrect"),
            ("wrong_account", "does not match"),
            ("access_denied", "AccessDeniedException"),
            ("credentials", "Unable to locate credentials"),
            ("network", "Could not connect to the endpoint URL"),
            ("malformed_profile", "inference profile ARN"),
            ("put_failure", "AccessDeniedException"),
            ("policy_mismatch", "does not match"),
        )
        for scenario, expected_error in cases:
            with self.subTest(scenario=scenario):
                result, events, _ = self._run_reconciler(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)
                if scenario not in {"put_failure", "policy_mismatch"}:
                    self.assertFalse(
                        any(
                            command.startswith("iam put-role-policy")
                            for command in events
                        )
                    )
                if scenario == "put_failure":
                    self.assertFalse(
                        any(
                            command.startswith("iam get-role-policy")
                            for command in events
                        )
                    )

    def test_deploy_does_not_write_local_environment(self):
        self.assertNotIn("Path('.env')", self.deploy)
        self.assertNotIn('Path(".env")', self.deploy)
        self.assertNotIn("> .env", self.deploy)
        self.assertNotIn(">> .env", self.deploy)
        self.assertNotIn("  .env \\", self.deploy)
        self.assertIn("  .env \\", self.configure)

    def test_no_wildcards_or_weakened_shell_behavior(self):
        related_sources = "\n".join((
            self.deploy,
            self.configure,
            self.helper,
            self.tool,
        ))
        for forbidden in (
            "|| true",
            "AdministratorAccess",
            '"bedrock:*"',
            '"Resource": "*"',
            "set -x",
        ):
            self.assertNotIn(forbidden, related_sources)
        self.assertNotRegex(related_sources, r"\beval\b")


if __name__ == "__main__":
    unittest.main()
