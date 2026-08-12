import json
import unittest
from datetime import (
    datetime,
    timedelta,
    timezone,
)

from entitlement_policy import (
    ENTITLEMENT_ENTITY_TYPE,
    ENTITLEMENT_VERSION,
    SOURCE_DEFAULT,
    SOURCE_STRIPE,
    SOURCE_SYSTEM,
    STATUS_ACTIVE,
    STATUS_CANCELED,
    STATUS_EXPIRED,
    STATUS_FREE,
    STATUS_PAST_DUE,
    STATUS_TRIALING,
    EntitlementPolicyError,
    build_entitlement_record,
    resolve_effective_entitlement,
)
from usage_policy import (
    PLAN_FREE,
    PLAN_PRO,
)


FIXED_NOW = datetime(
    2026,
    7,
    25,
    18,
    0,
    tzinfo=timezone.utc,
)

FUTURE = (
    FIXED_NOW
    + timedelta(days=10)
)

PAST = (
    FIXED_NOW
    - timedelta(days=10)
)


def pro_record(
    *,
    status=STATUS_ACTIVE,
    starts_at=None,
    ends_at=None,
    cancel_at_period_end=False,
):
    return build_entitlement_record(
        plan=PLAN_PRO,
        status=status,
        source=SOURCE_STRIPE,
        access_starts_at=starts_at,
        access_ends_at=ends_at,
        cancel_at_period_end=(
            cancel_at_period_end
        ),
        updated_at=FIXED_NOW,
    )


class EntitlementPolicyTests(
    unittest.TestCase
):
    def test_missing_record_defaults_to_free(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                None,
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

        self.assertFalse(
            entitlement[
                "access"
            ]["isPro"]
        )

        self.assertEqual(
            entitlement[
                "subscription"
            ]["source"],
            SOURCE_DEFAULT,
        )

    def test_active_pro_has_pro_access(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                pro_record(),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_PRO,
        )

        self.assertTrue(
            entitlement[
                "access"
            ]["isPro"]
        )

    def test_trialing_pro_has_pro_access(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                pro_record(
                    status=(
                        STATUS_TRIALING
                    ),
                    ends_at=FUTURE,
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_PRO,
        )

    def test_future_access_has_not_started(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                pro_record(
                    starts_at=FUTURE,
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

    def test_ended_access_defaults_to_free(
        self,
    ):
        record = pro_record(
            starts_at=(
                PAST
                - timedelta(days=10)
            ),
            ends_at=PAST,
        )

        entitlement = (
            resolve_effective_entitlement(
                record,
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

    def test_scheduled_cancellation_remains_pro_until_end(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                pro_record(
                    status=(
                        STATUS_ACTIVE
                    ),
                    ends_at=FUTURE,
                    cancel_at_period_end=True,
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_PRO,
        )

        self.assertTrue(
            entitlement[
                "access"
            ]["isPro"]
        )

        self.assertTrue(
            entitlement[
                "subscription"
            ]["cancelAtPeriodEnd"]
        )

    def test_deleted_canceled_status_revokes_pro_immediately(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                pro_record(
                    status=(
                        STATUS_CANCELED
                    ),
                    ends_at=FUTURE,
                    cancel_at_period_end=False,
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

        self.assertFalse(
            entitlement[
                "access"
            ]["isPro"]
        )

    def test_canceled_plan_without_end_is_free(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                pro_record(
                    status=(
                        STATUS_CANCELED
                    ),
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

    def test_past_due_grace_window_is_pro(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                pro_record(
                    status=(
                        STATUS_PAST_DUE
                    ),
                    ends_at=FUTURE,
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_PRO,
        )

    def test_expired_status_is_free(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                pro_record(
                    status=(
                        STATUS_EXPIRED
                    ),
                    ends_at=PAST,
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

    def test_unknown_plan_fails_safe_to_free(
        self,
    ):
        record = pro_record()
        record["plan"] = "ENTERPRISE"

        entitlement = (
            resolve_effective_entitlement(
                record,
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

        self.assertEqual(
            entitlement[
                "subscription"
            ]["source"],
            SOURCE_DEFAULT,
        )

    def test_invalid_timestamp_fails_safe(
        self,
    ):
        record = pro_record()
        record[
            "accessEndsAt"
        ] = "not-a-date"

        entitlement = (
            resolve_effective_entitlement(
                record,
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            entitlement["plan"]["id"],
            PLAN_FREE,
        )

    def test_builds_free_entitlement_record(
        self,
    ):
        record = build_entitlement_record(
            plan=PLAN_FREE,
            status=STATUS_FREE,
            source=SOURCE_SYSTEM,
            updated_at=FIXED_NOW,
        )

        self.assertEqual(
            record["entityType"],
            ENTITLEMENT_ENTITY_TYPE,
        )

        self.assertEqual(
            record[
                "entitlementVersion"
            ],
            ENTITLEMENT_VERSION,
        )

        self.assertEqual(
            record["plan"],
            PLAN_FREE,
        )

    def test_free_plan_rejects_active_status(
        self,
    ):
        with self.assertRaises(
            EntitlementPolicyError
        ) as raised:
            build_entitlement_record(
                plan=PLAN_FREE,
                status=STATUS_ACTIVE,
                source=SOURCE_SYSTEM,
                updated_at=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidEntitlementState",
        )

    def test_pro_plan_rejects_free_status(
        self,
    ):
        with self.assertRaises(
            EntitlementPolicyError
        ) as raised:
            build_entitlement_record(
                plan=PLAN_PRO,
                status=STATUS_FREE,
                source=SOURCE_SYSTEM,
                updated_at=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidEntitlementState",
        )

    def test_reversed_access_window_rejected(
        self,
    ):
        with self.assertRaises(
            EntitlementPolicyError
        ) as raised:
            pro_record(
                starts_at=FUTURE,
                ends_at=PAST,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidEntitlementWindow",
        )

    def test_naive_access_timestamp_rejected(
        self,
    ):
        with self.assertRaises(
            EntitlementPolicyError
        ) as raised:
            pro_record(
                starts_at=datetime(
                    2026,
                    7,
                    25,
                    18,
                    0,
                ),
            )

        self.assertEqual(
            raised.exception.code,
            (
                "InvalidEntitlement"
                "Timestamp"
            ),
        )

    def test_public_projection_is_allow_listed(
        self,
    ):
        entitlement = (
            resolve_effective_entitlement(
                pro_record(
                    ends_at=FUTURE,
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            set(entitlement),
            {
                "entitlementVersion",
                "generatedAt",
                "plan",
                "subscription",
                "access",
                "updatedAt",
            },
        )

        serialized = json.dumps(
            entitlement
        )

        for private_field in (
            "PK",
            "SK",
            "GSI1PK",
            "GSI1SK",
            "userId",
            "customerId",
            "subscriptionId",
            "paymentMethodId",
            "accessToken",
            "idToken",
            "refreshToken",
        ):
            self.assertNotIn(
                private_field,
                serialized,
            )


if __name__ == "__main__":
    unittest.main()
