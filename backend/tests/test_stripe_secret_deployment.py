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

DEPLOY_SCRIPT = (
    BACKEND_ROOT
    / "bin"
    / "deploy"
)

GITIGNORE = (
    REPOSITORY_ROOT
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
            "backend/.build/",
            self.gitignore,
        )


if __name__ == "__main__":
    unittest.main()
