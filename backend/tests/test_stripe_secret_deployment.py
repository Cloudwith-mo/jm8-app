import unittest
from pathlib import Path


BACKEND_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

REPOSITORY_ROOT = (
    BACKEND_ROOT.parent
)

PROVISION_SCRIPT = (
    BACKEND_ROOT
    / "bin"
    / "provision-stripe-secret"
)

SETUP_SCRIPT = (
    BACKEND_ROOT
    / "bin"
    / "setup-stripe-catalog"
)

DEPLOY_SCRIPT = (
    BACKEND_ROOT
    / "bin"
    / "deploy"
)

GITIGNORE = (
    BACKEND_ROOT
    / ".gitignore"
)


class StripeSecretDeploymentTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(
        cls,
    ):
        cls.provision = (
            PROVISION_SCRIPT
            .read_text()
        )

        cls.setup = SETUP_SCRIPT.read_text()

        cls.deploy = (
            DEPLOY_SCRIPT
            .read_text()
        )

        cls.gitignore = (
            GITIGNORE
            .read_text()
        )

    def test_provision_requires_core_environment(
        self,
    ):
        for name in (
            "AWS_PROFILE",
            "AWS_REGION",
            "APP_NAME",
            "STAGE",
            "STRIPE_SECRET_KEY",
        ):
            self.assertIn(
                name,
                self.provision,
            )

    def test_provision_rejects_live_key(
        self,
    ):
        self.assertIn(
            '"dev": "sk_test_"',
            self.provision,
        )

        self.assertIn(
            '"staging": "sk_test_"',
            self.provision,
        )

        self.assertIn(
            '"prod": "sk_live_"',
            self.provision,
        )

        self.assertIn(
            'export JM8_OPERATION="provision-stripe-secret"',
            self.provision,
        )

    def test_catalog_uses_stage_specific_non_secret_output(self):
        self.assertIn('f"{stage}.stripe.env"', self.setup)
        self.assertIn('Path(os.environ["SCRIPT_DIR"])', self.setup)
        self.assertIn("mkdir(parents=True, exist_ok=True)", self.setup)
        self.assertIn("NamedTemporaryFile", self.setup)
        self.assertIn("temporary_path.replace(output_path)", self.setup)
        self.assertIn("temporary_path.chmod(0o600)", self.setup)
        self.assertIn('"STRIPE_SECRET_KEY": secret_key', self.setup)
        self.assertIn('load_stripe_checkout_config({', self.setup)
        self.assertNotIn('path = Path(".env")', self.setup)
        self.assertNotIn('"STRIPE_WEBHOOK_SECRET":', self.setup.split("def update_environment", 1)[1])

    def test_catalog_validates_before_atomic_publication_and_preserves_arn(self):
        validation_index = self.setup.index('load_stripe_checkout_config({')
        temp_index = self.setup.index('NamedTemporaryFile', validation_index)
        replace_index = self.setup.index('temporary_path.replace(output_path)', temp_index)
        self.assertLess(validation_index, temp_index)
        self.assertLess(temp_index, replace_index)
        self.assertIn('managed_keys = set(updates)', self.setup)
        self.assertIn('rendered_lines.append(line)', self.setup)
        self.assertIn('if output_path.exists()', self.setup)

    def test_catalog_requires_caller_urls_and_no_localhost_fallback(self):
        for name in (
            "STRIPE_CHECKOUT_SUCCESS_URL",
            "STRIPE_CHECKOUT_CANCEL_URL",
            "STRIPE_PORTAL_RETURN_URL",
        ):
            self.assertIn(f': "${{{name}:?{name} is required.', self.setup)
        self.assertNotIn("http://localhost:5173/", self.setup)
        self.assertIn("validate_non_dev_urls", self.setup)

    def test_provision_can_create_secret(
        self,
    ):
        self.assertIn(
            "create-secret",
            self.provision,
        )

    def test_provision_can_update_secret(
        self,
    ):
        self.assertIn(
            "put-secret-value",
            self.provision,
        )

    def test_secret_payload_uses_named_json_field(
        self,
    ):
        self.assertIn(
            '"STRIPE_SECRET_KEY":',
            self.provision,
        )

        self.assertIn(
            "json.dumps",
            self.provision,
        )

    def test_secret_value_is_not_printed(
        self,
    ):
        self.assertNotIn(
            'echo "$STRIPE_SECRET_KEY"',
            self.provision,
        )

        self.assertNotIn(
            'printf "$STRIPE_SECRET_KEY"',
            self.provision,
        )

    def test_local_environment_is_updated(
        self,
    ):
        self.assertIn(
            "STRIPE_SECRET_ARN",
            self.provision,
        )

        self.assertIn(
            "os.replace",
            self.provision,
        )
        self.assertIn("infra/environments/generated/${STAGE}.stripe.env", self.provision)
        self.assertNotIn('Path(\n    ".env"', self.provision)

    def test_secret_update_preserves_existing_webhook(self):
        self.assertIn("get-secret-value", self.provision)
        self.assertIn('existing.get("STRIPE_WEBHOOK_SECRET")', self.provision)
        self.assertIn('payload["STRIPE_WEBHOOK_SECRET"] = existing_webhook', self.provision)

    def test_production_requires_complete_payload_but_bootstrap_does_not(self):
        self.assertIn('os.environ.get("STAGE") == "prod"', self.provision)
        self.assertIn("Production requires STRIPE_WEBHOOK_SECRET.", self.provision)
        self.assertIn('STRIPE_WEBHOOK_SECRET="${STRIPE_WEBHOOK_SECRET:-}"', self.provision)

    def test_temporary_secret_file_is_cleaned(
        self,
    ):
        self.assertIn(
            "mktemp -d",
            self.provision,
        )

        self.assertIn(
            "trap cleanup EXIT",
            self.provision,
        )

    def test_deploy_requires_secret_arn(
        self,
    ):
        self.assertIn(
            (
                "STRIPE_SECRET_ARN "
                "is required"
            ),
            self.deploy,
        )

    def test_deploy_requires_checkout_configuration(
        self,
    ):
        for name in (
            "STRIPE_PRO_MONTHLY_PRICE_ID",
            "STRIPE_CHECKOUT_SUCCESS_URL",
            "STRIPE_CHECKOUT_CANCEL_URL",
            "STRIPE_PORTAL_RETURN_URL",
        ):
            self.assertIn(
                name,
                self.deploy,
            )

    def test_deploy_verifies_secret_exists(
        self,
    ):
        self.assertIn(
            "describe-secret",
            self.deploy,
        )

    def test_lambda_policy_uses_get_secret_value(
        self,
    ):
        self.assertIn(
            (
                "secretsmanager:"
                "GetSecretValue"
            ),
            self.deploy,
        )

    def test_lambda_policy_uses_specific_resource(
        self,
    ):
        self.assertIn(
            '"Resource": secret_arn',
            self.deploy,
        )

    def test_create_function_uses_environment_file(
        self,
    ):
        self.assertGreaterEqual(
            self.deploy.count(
                (
                    "--environment "
                    "file://.build/"
                    "api-environment.json"
                )
            ),
            2,
        )

    def test_update_function_uses_environment_file(
        self,
    ):
        self.assertNotIn(
            '--environment "Variables={',
            self.deploy,
        )

    def test_plaintext_secret_excluded_from_deploy(
        self,
    ):
        environment_block = (
            self.deploy
            .split(
                "environment_keys = [",
                1,
            )[1]
            .split(
                "]\n\nvariables",
                1,
            )[0]
        )

        self.assertNotIn(
            '"STRIPE_SECRET_KEY"',
            environment_block,
        )

        self.assertNotIn(
            "export STRIPE_SECRET_KEY",
            self.deploy,
        )

        self.assertNotIn(
            "${STRIPE_SECRET_KEY}",
            self.deploy,
        )
        self.assertNotIn("export STRIPE_WEBHOOK_SECRET", self.deploy)
        self.assertNotIn('"STRIPE_WEBHOOK_SECRET",', environment_block)

    def test_deploy_allows_staging_bootstrap_and_checks_prod_secret(self):
        self.assertIn("get-secret-value", self.deploy)
        self.assertIn('stage == "prod"', self.deploy)
        self.assertIn("Production Stripe webhook secret is missing or invalid.", self.deploy)

    def test_stripe_generated_config_is_ignored_and_non_secret(self):
        self.assertIn("infra/environments/generated/*.stripe.env", self.gitignore)
        self.assertIn("STRIPE_ENV_FILE", self.provision)
        self.assertIn("STRIPE_ENV_FILE", self.provision)

    def test_billing_values_enter_managed_environment(
        self,
    ):
        for name in (
            "STRIPE_SECRET_ARN",
            "STRIPE_PRO_MONTHLY_PRICE_ID",
            "STRIPE_CHECKOUT_SUCCESS_URL",
            "STRIPE_CHECKOUT_CANCEL_URL",
            "STRIPE_PORTAL_RETURN_URL",
        ):
            self.assertIn(
                f'"{name}"',
                self.deploy,
            )

    def test_build_artifacts_are_gitignored(
        self,
    ):
        self.assertIn(
            ".build/",
            self.gitignore,
        )


if __name__ == "__main__":
    unittest.main()
