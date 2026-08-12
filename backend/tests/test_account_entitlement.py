import io
import json
import os
import unittest
from contextlib import (
    redirect_stdout,
)
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path
from unittest.mock import (
    patch,
)


os.environ.setdefault(
    "AWS_ACCESS_KEY_ID",
    "testing",
)
os.environ.setdefault(
    "AWS_SECRET_ACCESS_KEY",
    "testing",
)
os.environ.setdefault(
    "AWS_DEFAULT_REGION",
    "us-east-1",
)
os.environ.setdefault(
    "AWS_EC2_METADATA_DISABLED",
    "true",
)
os.environ.setdefault(
    "TABLE_NAME",
    "journalm8-test-main",
)
os.environ.setdefault(
    "RAW_BUCKET",
    "journalm8-test-raw",
)


from account_entitlement import (  # noqa: E402
    ACCOUNT_ENTITLEMENT_VERSION,
    AccountEntitlementUnavailableError,
    get_account_entitlement,
)
from app import lambda_handler  # noqa: E402
from entitlement_resolver import (  # noqa: E402
    EntitlementUnavailableError,
)
from usage_policy import (  # noqa: E402
    PLAN_FREE,
    PLAN_PRO,
    UsagePolicyError,
)


FIXED_NOW = datetime(
    2026,
    7,
    26,
    22,
    0,
    tzinfo=timezone.utc,
)


def entitlement_fixture(
    *,
    plan=PLAN_FREE,
):
    is_pro = plan == PLAN_PRO

    return {
        "entitlementVersion": "1.0",
        "generatedAt": (
            FIXED_NOW.isoformat()
        ),
        "plan": {
            "id": plan,
            "label": (
                "Pro"
                if is_pro
                else "Free"
            ),
        },
        "subscription": {
            "configuredPlan": plan,
            "status": (
                "ACTIVE"
                if is_pro
                else "FREE"
            ),
            "source": (
                "STRIPE"
                if is_pro
                else "DEFAULT"
            ),
            "cancelAtPeriodEnd": False,
        },
        "access": {
            "isPro": is_pro,
            "startsAt": None,
            "endsAt": None,
        },
        "updatedAt": (
            FIXED_NOW.isoformat()
            if is_pro
            else None
        ),
    }


def account_projection(
    *,
    plan=PLAN_FREE,
):
    is_pro = plan == PLAN_PRO

    return {
        "accountEntitlementVersion": (
            ACCOUNT_ENTITLEMENT_VERSION
        ),
        "generatedAt": (
            FIXED_NOW.isoformat()
        ),
        "plan": {
            "id": plan,
            "label": (
                "Pro"
                if is_pro
                else "Free"
            ),
        },
        "subscription": {
            "configuredPlan": plan,
            "status": (
                "ACTIVE"
                if is_pro
                else "FREE"
            ),
            "source": (
                "STRIPE"
                if is_pro
                else "DEFAULT"
            ),
            "cancelAtPeriodEnd": False,
        },
        "access": {
            "isPro": is_pro,
            "startsAt": None,
            "endsAt": None,
        },
        "limits": {
            "askJm8": {
                "monthly": (
                    100
                    if is_pro
                    else 5
                ),
            },
            "entryAnalysis": {
                "monthly": (
                    250
                    if is_pro
                    else 10
                ),
            },
        },
        "updatedAt": (
            FIXED_NOW.isoformat()
            if is_pro
            else None
        ),
    }


def api_event():
    return {
        "requestContext": {
            "http": {
                "method": "GET",
                "path": (
                    "/account/entitlement"
                ),
            },
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": (
                            "private-user"
                        ),
                    },
                },
            },
        },
    }


def parse_logs(
    output: io.StringIO,
):
    return [
        json.loads(line)
        for line
        in output.getvalue().splitlines()
        if line.strip()
    ]


class AccountEntitlementServiceTests(
    unittest.TestCase
):
    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_free_projection_has_free_limits(
        self,
        resolve_entitlement,
    ):
        resolve_entitlement.return_value = (
            entitlement_fixture()
        )

        result = get_account_entitlement(
            "test-user",
            now=FIXED_NOW,
            environ={},
        )

        self.assertEqual(
            result["plan"]["id"],
            PLAN_FREE,
        )

        self.assertEqual(
            result["limits"][
                "askJm8"
            ]["monthly"],
            5,
        )

        self.assertEqual(
            result["limits"][
                "entryAnalysis"
            ]["monthly"],
            10,
        )

    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_pro_projection_has_pro_limits(
        self,
        resolve_entitlement,
    ):
        resolve_entitlement.return_value = (
            entitlement_fixture(
                plan=PLAN_PRO
            )
        )

        result = get_account_entitlement(
            "test-user",
            now=FIXED_NOW,
            environ={},
        )

        self.assertEqual(
            result["plan"]["id"],
            PLAN_PRO,
        )

        self.assertEqual(
            result["limits"][
                "askJm8"
            ]["monthly"],
            100,
        )

        self.assertEqual(
            result["limits"][
                "entryAnalysis"
            ]["monthly"],
            250,
        )

    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_deleted_subscription_projection_restores_free_limits(
        self,
        resolve_entitlement,
    ):
        resolve_entitlement.return_value = {
            "entitlementVersion": "1.0",
            "generatedAt": (
                FIXED_NOW.isoformat()
            ),
            "plan": {
                "id": PLAN_FREE,
                "label": "Free",
            },
            "subscription": {
                "configuredPlan": PLAN_PRO,
                "status": "CANCELED",
                "source": "STRIPE",
                "cancelAtPeriodEnd": False,
            },
            "access": {
                "isPro": False,
                "startsAt": (
                    "2026-07-01T00:00:00+00:00"
                ),
                "endsAt": (
                    "2026-08-31T00:00:00+00:00"
                ),
            },
            "updatedAt": (
                FIXED_NOW.isoformat()
            ),
        }

        result = get_account_entitlement(
            "test-user",
            now=FIXED_NOW,
            environ={},
        )

        self.assertFalse(
            result["access"]["isPro"]
        )

        self.assertEqual(
            result["plan"]["id"],
            PLAN_FREE,
        )

        self.assertEqual(
            result["subscription"]["status"],
            "CANCELED",
        )

        self.assertEqual(
            result["limits"][
                "askJm8"
            ]["monthly"],
            5,
        )

        self.assertEqual(
            result["limits"][
                "entryAnalysis"
            ]["monthly"],
            10,
        )

    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_resolver_receives_user_context(
        self,
        resolve_entitlement,
    ):
        resolve_entitlement.return_value = (
            entitlement_fixture()
        )

        fake_table = object()

        get_account_entitlement(
            "scoped-user",
            now=FIXED_NOW,
            environ={},
            table_resource=fake_table,
        )

        resolve_entitlement.assert_called_once_with(
            "scoped-user",
            now=FIXED_NOW,
            table_resource=fake_table,
        )

    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_environment_overrides_are_applied(
        self,
        resolve_entitlement,
    ):
        resolve_entitlement.return_value = (
            entitlement_fixture()
        )

        result = get_account_entitlement(
            "test-user",
            now=FIXED_NOW,
            environ={
                (
                    "FREE_MONTHLY_"
                    "ASK_QUESTIONS"
                ): "8",
                (
                    "FREE_MONTHLY_"
                    "ENTRY_ANALYSES"
                ): "16",
            },
        )

        self.assertEqual(
            result["limits"][
                "askJm8"
            ]["monthly"],
            8,
        )

        self.assertEqual(
            result["limits"][
                "entryAnalysis"
            ]["monthly"],
            16,
        )

    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_retryable_resolver_failure_is_safe(
        self,
        resolve_entitlement,
    ):
        resolve_entitlement.side_effect = (
            EntitlementUnavailableError(
                retryable=True
            )
        )

        with self.assertRaises(
            AccountEntitlementUnavailableError
        ) as raised:
            get_account_entitlement(
                "test-user",
                now=FIXED_NOW,
                environ={},
            )

        self.assertTrue(
            raised.exception.retryable
        )

        self.assertEqual(
            raised.exception.payload[
                "retryAfterSeconds"
            ],
            2,
        )

    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_nonretryable_resolver_failure_is_safe(
        self,
        resolve_entitlement,
    ):
        resolve_entitlement.side_effect = (
            EntitlementUnavailableError(
                retryable=False
            )
        )

        with self.assertRaises(
            AccountEntitlementUnavailableError
        ) as raised:
            get_account_entitlement(
                "test-user",
                now=FIXED_NOW,
                environ={},
            )

        self.assertFalse(
            raised.exception.retryable
        )

        self.assertNotIn(
            "retryAfterSeconds",
            raised.exception.payload,
        )

    @patch(
        (
            "account_entitlement."
            "get_plan_limits"
        )
    )
    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_policy_failure_is_safe(
        self,
        resolve_entitlement,
        get_limits,
    ):
        resolve_entitlement.return_value = (
            entitlement_fixture()
        )

        get_limits.side_effect = (
            UsagePolicyError(
                "InvalidUsageLimit",
                (
                    "Private configuration "
                    "detail."
                ),
            )
        )

        with self.assertRaises(
            AccountEntitlementUnavailableError
        ) as raised:
            get_account_entitlement(
                "test-user",
                now=FIXED_NOW,
                environ={},
            )

        self.assertFalse(
            raised.exception.retryable
        )

    @patch(
        (
            "account_entitlement."
            "get_plan_limits"
        )
    )
    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_private_failure_message_not_exposed(
        self,
        resolve_entitlement,
        get_limits,
    ):
        resolve_entitlement.return_value = (
            entitlement_fixture()
        )

        get_limits.side_effect = (
            UsagePolicyError(
                "InvalidUsageLimit",
                (
                    "Private configuration "
                    "detail."
                ),
            )
        )

        with self.assertRaises(
            AccountEntitlementUnavailableError
        ) as raised:
            get_account_entitlement(
                "test-user",
                now=FIXED_NOW,
                environ={},
            )

        serialized = json.dumps(
            raised.exception.payload
        )

        self.assertNotIn(
            "Private configuration",
            serialized,
        )

        self.assertNotIn(
            "InvalidUsageLimit",
            serialized,
        )

    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_projection_is_allow_listed(
        self,
        resolve_entitlement,
    ):
        entitlement = entitlement_fixture(
            plan=PLAN_PRO
        )

        entitlement.update({
            "PK": "private-pk",
            "SK": "private-sk",
            "userId": "private-user",
            "stripeCustomerId": (
                "private-customer"
            ),
            "stripeSubscriptionId": (
                "private-subscription"
            ),
            "paymentMethodId": (
                "private-payment"
            ),
        })

        resolve_entitlement.return_value = (
            entitlement
        )

        result = get_account_entitlement(
            "test-user",
            now=FIXED_NOW,
            environ={},
        )

        self.assertEqual(
            set(result),
            {
                "accountEntitlementVersion",
                "generatedAt",
                "plan",
                "subscription",
                "access",
                "limits",
                "updatedAt",
            },
        )

        serialized = json.dumps(
            result
        )

        for private_value in (
            "private-pk",
            "private-sk",
            "private-user",
            "private-customer",
            "private-subscription",
            "private-payment",
            "stripeCustomerId",
            "stripeSubscriptionId",
            "paymentMethodId",
        ):
            self.assertNotIn(
                private_value,
                serialized,
            )

    @patch(
        (
            "account_entitlement."
            "resolve_user_entitlement"
        )
    )
    def test_success_log_has_safe_fields(
        self,
        resolve_entitlement,
    ):
        resolve_entitlement.return_value = (
            entitlement_fixture(
                plan=PLAN_PRO
            )
        )

        output = io.StringIO()

        with redirect_stdout(output):
            get_account_entitlement(
                "private-user",
                now=FIXED_NOW,
                environ={},
            )

        logs = parse_logs(
            output
        )

        self.assertEqual(
            len(logs),
            1,
        )

        self.assertEqual(
            set(logs[0]),
            {
                "event",
                "plan",
                "status",
                "isPro",
                "askMonthlyLimit",
                (
                    "entryAnalysis"
                    "MonthlyLimit"
                ),
            },
        )

        self.assertNotIn(
            "private-user",
            output.getvalue(),
        )


class AccountEntitlementApiTests(
    unittest.TestCase
):
    @patch(
        "app.get_account_entitlement"
    )
    def test_api_returns_authenticated_entitlement(
        self,
        get_entitlement,
    ):
        expected = account_projection()

        get_entitlement.return_value = (
            expected
        )

        result = lambda_handler(
            api_event(),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            200,
        )

        self.assertEqual(
            body["entitlement"],
            expected,
        )

    @patch(
        "app.get_account_entitlement"
    )
    def test_api_passes_authenticated_user(
        self,
        get_entitlement,
    ):
        get_entitlement.return_value = (
            account_projection()
        )

        lambda_handler(
            api_event(),
            None,
        )

        get_entitlement.assert_called_once_with(
            "private-user"
        )

    @patch(
        "app.get_account_entitlement"
    )
    def test_api_failure_returns_503(
        self,
        get_entitlement,
    ):
        get_entitlement.side_effect = (
            AccountEntitlementUnavailableError(
                retryable=True
            )
        )

        result = lambda_handler(
            api_event(),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            503,
        )

        self.assertEqual(
            body["error"],
            (
                "AccountEntitlementUnavailable"
            ),
        )

        self.assertTrue(
            body["retryable"]
        )


class AccountEntitlementRouteTests(
    unittest.TestCase
):
    def test_create_api_contains_account_route(
        self,
    ):
        script = Path(
            "bin/create-api"
        ).read_text()

        self.assertIn(
            (
                'create_route_if_missing '
                '"GET /account/entitlement"'
            ),
            script,
        )

    def test_secure_api_contains_account_route(
        self,
    ):
        script = Path(
            "bin/secure-api"
        ).read_text()

        self.assertIn(
            (
                'secure_route '
                '"GET /account/entitlement"'
            ),
            script,
        )


if __name__ == "__main__":
    unittest.main()
