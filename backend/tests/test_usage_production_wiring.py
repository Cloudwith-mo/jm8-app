import unittest
from pathlib import Path


class UsageProductionWiringTests(
    unittest.TestCase
):
    def test_lambda_role_allows_transactions(
        self,
    ):
        resources = Path(
            "bin/create-resources"
        ).read_text()

        self.assertIn(
            (
                '"dynamodb:'
                'TransactWriteItems"'
            ),
            resources,
        )

    def test_deploy_includes_all_usage_limits(
        self,
    ):
        deploy = Path(
            "bin/deploy"
        ).read_text()

        for variable in (
            "FREE_MONTHLY_ASK_QUESTIONS",
            "FREE_MONTHLY_ENTRY_ANALYSES",
            "PRO_MONTHLY_ASK_QUESTIONS",
            "PRO_MONTHLY_ENTRY_ANALYSES",
        ):
            self.assertIn(
                variable,
                deploy,
            )

    def test_usage_route_is_created_and_secured(
        self,
    ):
        create_api = Path(
            "bin/create-api"
        ).read_text()

        secure_api = Path(
            "bin/secure-api"
        ).read_text()

        self.assertIn(
            (
                'create_route_if_missing '
                '"GET /usage"'
            ),
            create_api,
        )

        self.assertIn(
            (
                'secure_route '
                '"GET /usage"'
            ),
            secure_api,
        )

    def test_proof_checks_concurrency_and_auth(
        self,
    ):
        proof = Path(
            "bin/prove-usage-production"
        ).read_text()

        for requirement in (
            "ThreadPoolExecutor",
            "reserve_monthly_usage",
            "fail_usage_reservation",
            "Unauthenticated usage rejected",
            "AuthorizationType",
            "simulate-principal-policy",
            "Authenticated user isolated",
        ):
            self.assertIn(
                requirement,
                proof,
            )

    def test_proof_does_not_print_credentials(
        self,
    ):
        proof = Path(
            "bin/prove-usage-production"
        ).read_text()

        for forbidden in (
            'echo "$ID_TOKEN"',
            'echo "${ID_TOKEN}"',
            "cat \"$TMP_DIR/authenticated.json\"",
            "set -x",
        ):
            self.assertNotIn(
                forbidden,
                proof,
            )


if __name__ == "__main__":
    unittest.main()
