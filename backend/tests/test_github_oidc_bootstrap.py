from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = BACKEND_ROOT / "bin"
HELPER = BIN_DIR / "jm8_github_oidc_bootstrap.py"
PROVISIONER = BIN_DIR / "provision-github-oidc-deployers"
sys.path.insert(0, str(BIN_DIR))

from jm8_github_oidc_bootstrap import (  # noqa: E402
    ACCOUNT_ID,
    OIDC_AUDIENCE,
    OIDC_HOST,
    REPOSITORY,
    PROVIDER_STACK_NAME,
    STAGES,
    AwsCli,
    OidcBootstrapError,
    execute,
    generate_templates,
    github_subject,
    policy_arn,
    policy_name,
    policy_path,
    role_arn,
    role_name,
    stage_policies,
    trust_policy,
    inspect_bootstrap_state,
    verify_identity,
    write_artifacts,
    validate_stage_policies,
)


class GenerationTests(unittest.TestCase):
    def test_dev_tag_read_grant_is_exact_and_read_only(self):
        statement = next(s for s in stage_policies("dev", "us-east-1")["compute"]["Statement"] if s["Sid"] == "ReadExactDevHttpApiTags")
        self.assertEqual(statement, {
            "Sid": "ReadExactDevHttpApiTags", "Effect": "Allow",
            "Action": ["apigateway:GET"],
            "Resource": "arn:aws:apigateway:us-east-1::/tags/arn:aws:apigateway:us-east-1::/apis/u06tdrfsua",
        })
        self.assertNotIn("*", statement["Resource"])

    def test_tag_read_grant_is_not_copied_to_other_stages_or_regions(self):
        for stage, region in (("staging", "us-east-1"), ("prod", "us-east-1"), ("dev", "us-west-2")):
            with self.subTest(stage=stage, region=region):
                self.assertNotIn("ReadExactDevHttpApiTags", json.dumps(stage_policies(stage, region)))

    def test_secret_resources_and_names_are_exact_for_every_stage(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                statements = stage_policies(stage, "us-east-1")["secrets"]["Statement"]
                expected = f"arn:aws:secretsmanager:us-east-1:{ACCOUNT_ID}:secret:journalm8/{stage}/stripe-*"
                self.assertEqual(len(statements), 2)
                for statement in statements:
                    self.assertEqual(statement["Resource"], expected)
                conditions = statements[1]["Condition"]["StringEquals"]
                self.assertEqual(conditions["secretsmanager:Name"], f"journalm8/{stage}/stripe")
                self.assertEqual(conditions["aws:RequestTag/Stage"], stage)

    def test_rejects_cross_stage_secret_resources_and_conditions(self):
        for stage in STAGES:
            for other in set(STAGES) - {stage}:
                for location in ("resource", "condition"):
                    with self.subTest(stage=stage, other=other, location=location):
                        policies = stage_policies(stage, "us-east-1")
                        statement = policies["secrets"]["Statement"][1]
                        if location == "resource":
                            statement["Resource"] = f"arn:aws:secretsmanager:us-east-1:{ACCOUNT_ID}:secret:journalm8/{other}/stripe-*"
                        else:
                            statement["Condition"]["StringEquals"]["secretsmanager:Name"] = f"journalm8/{other}/stripe"
                        with self.assertRaises(OidcBootstrapError):
                            validate_stage_policies(stage, policies)

    def test_production_policies_unchanged(self):
        from jm8_production_deployer_policies import generate_policies
        source = generate_policies("us-east-1")
        self.assertEqual(stage_policies("prod", "us-east-1"), {
            name.rsplit("-", 1)[-1]: document for name, document in source.items()
        })

    def test_trust_is_exact_per_environment(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                document = trust_policy(stage)
                statement = document["Statement"]
                self.assertEqual(len(statement), 1)
                self.assertEqual(statement[0]["Action"], "sts:AssumeRoleWithWebIdentity")
                self.assertEqual(
                    statement[0]["Principal"]["Federated"],
                    f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/{OIDC_HOST}",
                )
                self.assertEqual(
                    statement[0]["Condition"],
                    {"StringEquals": {
                        f"{OIDC_HOST}:aud": OIDC_AUDIENCE,
                        f"{OIDC_HOST}:sub": (
                            f"repo:{REPOSITORY}:environment:jm8-{stage}"
                        ),
                    }},
                )

    def test_policies_are_stage_scoped_and_do_not_modify_deployer(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                serialized = json.dumps(stage_policies(stage, "us-east-1"))
                self.assertIn(f"journalm8-{stage}", serialized)
                self.assertNotIn(role_arn(stage), serialized)
                for other in set(STAGES) - {stage}:
                    self.assertNotIn(f"journalm8-{other}", serialized)
                self.assertNotIn("AdministratorAccess", serialized)
                self.assertNotIn('"Action": "*"', serialized)

    def test_template_has_one_provider_three_roles_and_eighteen_policies(self):
        templates = generate_templates("us-east-1")
        self.assertEqual(set(templates), {"provider", *STAGES})
        for name, template in templates.items():
            with self.subTest(template=name):
                self.assertLessEqual(
                    len(json.dumps(template, separators=(",", ":"))),
                    51_200,
                )
        provider = templates["provider"]["Resources"]["GitHubOidcProvider"]["Properties"]
        self.assertEqual(provider["ClientIdList"], [OIDC_AUDIENCE])
        self.assertNotIn("ThumbprintList", provider)
        for stage in STAGES:
            resources = templates[stage]["Resources"]
            types = [resource["Type"] for resource in resources.values()]
            self.assertEqual(types.count("AWS::IAM::Role"), 1)
            self.assertEqual(types.count("AWS::IAM::ManagedPolicy"), 6)
            role = resources[f"Role{stage.title()}"]
            self.assertEqual(role["Properties"]["RoleName"], role_name(stage))
            self.assertEqual(len(role["Properties"]["ManagedPolicyArns"]), 6)

    def test_artifacts_are_deterministic(self):
        templates = generate_templates("us-east-1")
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            write_artifacts(templates, Path(first), "us-east-1")
            write_artifacts(templates, Path(second), "us-east-1")
            self.assertEqual(
                {path.name: path.read_bytes() for path in Path(first).iterdir()},
                {path.name: path.read_bytes() for path in Path(second).iterdir()},
            )

    def test_names_and_paths_are_exact(self):
        for stage in STAGES:
            self.assertEqual(role_name(stage), f"journalm8-{stage}-github-deployer")
            self.assertEqual(policy_path(stage), f"/journalm8/{stage}/github-deployer/")
            self.assertEqual(
                policy_arn(stage, "compute"),
                f"arn:aws:iam::{ACCOUNT_ID}:policy/journalm8/{stage}/github-deployer/"
                f"{policy_name(stage, 'compute')}",
            )
            self.assertEqual(
                github_subject(stage),
                f"repo:{REPOSITORY}:environment:jm8-{stage}",
            )


class SafetyTests(unittest.TestCase):
    @staticmethod
    def _missing_stacks_then(*, provider_exists=False, dev_role_exists=False):
        def call(service, operation, *arguments):
            if (service, operation) == ("cloudformation", "describe-stacks"):
                raise OidcBootstrapError(
                    "AWS command failed: cloudformation describe-stacks (ValidationError)."
                )
            if (service, operation) == ("iam", "list-open-id-connect-providers"):
                return {"OpenIDConnectProviderList": ([{
                    "Arn": f"arn:aws:iam::{ACCOUNT_ID}:oidc-provider/{OIDC_HOST}"
                }] if provider_exists else [])}
            if (service, operation) == ("iam", "get-role"):
                requested = arguments[arguments.index("--role-name") + 1]
                if dev_role_exists and requested == role_name("dev"):
                    return {"Role": {"Arn": role_arn("dev")}}
                raise OidcBootstrapError(
                    "AWS command failed: iam get-role (NoSuchEntity)."
                )
            raise AssertionError((service, operation, arguments))
        return call

    def test_generate_never_calls_aws(self):
        aws = MagicMock(spec=AwsCli)
        with tempfile.TemporaryDirectory() as directory:
            execute("generate", aws, Path(directory), "us-east-1")
        aws.run.assert_not_called()
        aws.deploy.assert_not_called()

    def test_wrong_account_fails_identity(self):
        aws = MagicMock(spec=AwsCli)
        aws.run.return_value = {
            "Account": "000000000000",
            "Arn": "arn:aws:iam::000000000000:user/operator",
        }
        with self.assertRaisesRegex(OidcBootstrapError, "approved account"):
            verify_identity(aws)

    def test_first_apply_refuses_existing_provider(self):
        aws = MagicMock(spec=AwsCli)
        aws.run.side_effect = self._missing_stacks_then(provider_exists=True)
        with self.assertRaisesRegex(OidcBootstrapError, "outside the managed stack"):
            inspect_bootstrap_state(aws)
        aws.deploy.assert_not_called()

    def test_first_apply_refuses_existing_role(self):
        aws = MagicMock(spec=AwsCli)
        aws.run.side_effect = self._missing_stacks_then(dev_role_exists=True)
        with self.assertRaisesRegex(OidcBootstrapError, "exists outside"):
            inspect_bootstrap_state(aws)

    @patch("jm8_github_oidc_bootstrap.subprocess.run")
    def test_aws_error_redacts_stderr(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="secret (AccessDenied) secret"
        )
        with self.assertRaisesRegex(OidcBootstrapError, r"\(AccessDenied\)") as raised:
            AwsCli("jm8-dev", "us-east-1").run("iam", "get-role")
        self.assertNotIn("secret", str(raised.exception))

    def test_apply_requires_exact_confirmation(self):
        result = subprocess.run(
            [str(PROVISIONER), "apply"],
            cwd=BACKEND_ROOT,
            env={
                "PATH": "/usr/bin:/bin",
                "AWS_PROFILE": "jm8-dev",
                "AWS_REGION": "us-east-1",
                "EXPECTED_AWS_ACCOUNT_ID": ACCOUNT_ID,
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GITHUB_OIDC_BOOTSTRAP_CONFIRMATION", result.stderr)


if __name__ == "__main__":
    unittest.main()
