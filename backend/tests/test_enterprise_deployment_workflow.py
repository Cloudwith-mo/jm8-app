import pathlib
import os
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "staged-deployment.yml"
DEPLOY = ROOT / "backend" / "bin" / "deploy"


class EnterpriseDeploymentWorkflowTests(unittest.TestCase):
    def test_verified_account_is_exported_for_later_steps(self):
        for stage in ("dev", "staging", "prod"):
            with self.subTest(stage=stage):
                self.check_account_export(stage, "114743615542", "114743615542", True)

    def test_wrong_or_malformed_account_is_not_exported(self):
        for actual, expected in (("000000000000", "114743615542"), ("None", "None"), ("", "")):
            with self.subTest(actual=actual):
                self.check_account_export("dev", actual, expected, False)

    def check_account_export(self, stage, actual, expected, success):
        step = self.workflow.split("      - name: Create canonical ephemeral AWS profile\n", 1)[1]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
        stub = 'aws() { if [[ "$1" == "sts" ]]; then printf "%s\\n" "$TEST_ACCOUNT"; fi; }\n'
        with tempfile.TemporaryDirectory() as directory:
            env_file = pathlib.Path(directory) / "github-env"
            env = dict(os.environ, STAGE=stage, TEST_ACCOUNT=actual,
                       EXPECTED_AWS_ACCOUNT_ID=expected, GITHUB_ENV=str(env_file),
                       AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test",
                       AWS_SESSION_TOKEN="test", AWS_REGION="us-east-1")
            result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", stub + script],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            lines = env_file.read_text().splitlines()
            account_lines = [line for line in lines if line.startswith("ACCOUNT_ID=")]
            self.assertEqual(account_lines, ["ACCOUNT_ID=" + actual] if success else [])
            if success:
                later_env = dict(env, **dict(line.split("=", 1) for line in lines))
                later = subprocess.run(["bash", "-c", ': "${ACCOUNT_ID:?missing}"; test "$ACCOUNT_ID" = "$TEST_ACCOUNT"'],
                                       env=later_env, capture_output=True, text=True)
                self.assertEqual(later.returncode, 0, later.stderr)

    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.deploy = DEPLOY.read_text(encoding="utf-8")

    def test_workflow_is_manual_and_requires_exact_release_identity(self):
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("release_sha:", self.workflow)
        self.assertIn("^[0-9a-f]{40}$", self.workflow)
        self.assertIn("RELEASE_SHA: ${{ github.sha }}", self.workflow)
        self.assertNotIn("ref: ${{ inputs.release_sha }}", self.workflow)
        self.assertIn("git merge-base --is-ancestor", self.workflow)
        self.assertIn('"deploy-${REQUESTED_STAGE}"', self.workflow)

    def test_workflow_uses_environment_approval_and_serialization(self):
        self.assertIn("name: jm8-${{ inputs.stage }}", self.workflow)
        self.assertIn("group: jm8-deploy-${{ inputs.stage }}", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertIn("PRODUCTION_RELEASE_STATE", self.workflow)

    def test_workflow_uses_short_lived_oidc_credentials(self):
        self.assertIn("id-token: write", self.workflow)
        self.assertIn("aws-actions/configure-aws-credentials@", self.workflow)
        self.assertIn("role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}", self.workflow)
        self.assertNotIn("AWS_ACCESS_KEY_ID: ${{ secrets.", self.workflow)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY: ${{ secrets.", self.workflow)

    def test_release_artifact_is_checksummed_and_retained(self):
        self.assertIn("backend-function.sha256", self.workflow)
        self.assertIn("manifest.json", self.workflow)
        self.assertIn("shasum -a 256 --check", self.workflow)
        self.assertIn("retention-days: 90", self.workflow)

    def test_deploy_uses_the_verified_manifest_checksum(self):
        self.assertIn(
            'awk \'NF {print $1; exit}\' "$GITHUB_WORKSPACE/release/backend-function.sha256"',
            self.workflow,
        )
        self.assertIn("export JM8_EXPECTED_PACKAGE_SHA256", self.workflow)
        self.assertNotIn("hashFiles('release/backend-function.zip')", self.workflow)

    def test_release_artifact_uses_a_visible_upload_directory(self):
        self.assertIn("path: release/", self.workflow)
        self.assertIn("path: release", self.workflow)
        self.assertIn('pathlib.Path("release")', self.workflow)
        self.assertNotIn("path: .release", self.workflow)
        self.assertNotIn('pathlib.Path(".release")', self.workflow)
        self.assertNotIn("/.release/backend-function.zip", self.workflow)

    def test_deploy_accepts_only_a_verified_prebuilt_package(self):
        self.assertIn("JM8_DEPLOY_PACKAGE_PATH", self.deploy)
        self.assertIn("JM8_EXPECTED_PACKAGE_SHA256", self.deploy)
        self.assertIn("ACTUAL_PACKAGE_SHA256", self.deploy)
        self.assertIn("./bin/package", self.deploy)
        self.assertLess(
            self.deploy.index("JM8_EXPECTED_PACKAGE_SHA256"),
            self.deploy.index('echo "Checking AWS identity..."'),
        )

    def test_workflow_runs_existing_fail_closed_contract_and_postconditions(self):
        self.assertIn("jm8_environment_contract.py validate", self.workflow)
        self.assertIn("./bin/deploy", self.workflow)
        self.assertIn("./bin/create-api", self.workflow)
        self.assertIn("./bin/secure-api", self.workflow)
        self.assertIn("./bin/deploy-frontend", self.workflow)
        self.assertIn("Lambda post-deployment state is not healthy", self.workflow)


if __name__ == "__main__":
    unittest.main()
