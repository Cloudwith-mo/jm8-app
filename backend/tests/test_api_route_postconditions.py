import unittest
from pathlib import Path


BACKEND_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

CREATE_API = (
    BACKEND_ROOT
    / "bin"
    / "create-api"
)

SECURE_API = (
    BACKEND_ROOT
    / "bin"
    / "secure-api"
)


class ApiRoutePostconditionTests(
    unittest.TestCase
):
    @classmethod
    def setUpClass(
        cls,
    ):
        cls.create_api = (
            CREATE_API.read_text()
        )

        cls.secure_api = (
            SECURE_API.read_text()
        )

    def test_create_rejects_duplicate_routes(
        self,
    ):
        self.assertIn(
            '"$ROUTE_COUNT" -gt 1',
            self.create_api,
        )

    def test_create_updates_route_target(
        self,
    ):
        self.assertIn(
            "update-route",
            self.create_api,
        )

        self.assertIn(
            '--target "$TARGET"',
            self.create_api,
        )

    def test_create_verifies_unique_route(
        self,
    ):
        self.assertIn(
            "VERIFIED_COUNT",
            self.create_api,
        )

        self.assertIn(
            '"$VERIFIED_COUNT" == "1"',
            self.create_api,
        )

    def test_create_verifies_lambda_target(
        self,
    ):
        self.assertIn(
            "VERIFIED_TARGET",
            self.create_api,
        )

        self.assertIn(
            (
                '"$VERIFIED_TARGET" '
                '== "$TARGET"'
            ),
            self.create_api,
        )

    def test_create_retries_postcondition(
        self,
    ):
        self.assertIn(
            "for ATTEMPT in {1..10}",
            self.create_api,
        )

        self.assertIn(
            "sleep 1",
            self.create_api,
        )

    def test_secure_missing_route_fails(
        self,
    ):
        self.assertIn(
            '"$route_count" -ne 1',
            self.secure_api,
        )

        self.assertNotIn(
            (
                "Route not found, "
                "skipping"
            ),
            self.secure_api,
        )

    def test_secure_verifies_authorization_type(
        self,
    ):
        self.assertIn(
            "verified_authorization_type",
            self.secure_api,
        )

        self.assertIn(
            (
                '"$verified_authorization_type" '
                '== "JWT"'
            ),
            self.secure_api,
        )

    def test_secure_verifies_authorizer_identity(
        self,
    ):
        self.assertIn(
            "verified_authorizer_id",
            self.secure_api,
        )

        self.assertIn(
            (
                '"$verified_authorizer_id" '
                '== "$AUTHORIZER_ID"'
            ),
            self.secure_api,
        )

    def test_secure_reads_final_route(
        self,
    ):
        self.assertIn(
            "get-route",
            self.secure_api,
        )

    def test_secure_retries_postcondition(
        self,
    ):
        self.assertIn(
            "for attempt in {1..10}",
            self.secure_api,
        )

        self.assertIn(
            "sleep 1",
            self.secure_api,
        )

    def test_checkout_route_remains_configured(
        self,
    ):
        route = (
            "POST /billing/checkout"
        )

        self.assertIn(
            route,
            self.create_api,
        )

        self.assertIn(
            route,
            self.secure_api,
        )

    def test_authorizer_ids_are_not_printed(
        self,
    ):
        self.assertNotIn(
            'echo "Authorizer ID:',
            self.secure_api,
        )

        self.assertNotIn(
            (
                "Authorizer already exists: "
                "$AUTHORIZER_ID"
            ),
            self.secure_api,
        )


if __name__ == "__main__":
    unittest.main()
