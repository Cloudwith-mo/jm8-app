import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "staged-deployment.yml"
DEPLOY = ROOT / "backend" / "bin" / "deploy"


class EnterpriseDeploymentWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.deploy = DEPLOY.read_text(encoding="utf-8")

    def test_workflow_is_manual_and_requires_exact_release_identity(self):
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("release_sha:", self.workflow)
        self.assertIn("^[0-9a-f]{40}$", self.workflow)
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
