import json
import unittest
from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal

from usage_policy import (
    DEFAULT_LIMITS,
    OPERATION_ASK_JM8,
    OPERATION_ENTRY_ANALYSIS,
    PLAN_FREE,
    PLAN_PRO,
    UsagePolicyError,
    build_usage_limit_error,
    build_usage_snapshot,
    evaluate_quota,
    get_plan_limits,
    monthly_usage_period,
    normalize_operation,
    normalize_plan,
)


FIXED_NOW = datetime(
    2026,
    7,
    22,
    12,
    30,
    tzinfo=timezone.utc,
)


class UsagePolicyTests(
    unittest.TestCase
):
    def test_unknown_plan_fails_safe_to_free(
        self,
    ):
        self.assertEqual(
            normalize_plan("enterprise"),
            PLAN_FREE,
        )

    def test_pro_plan_is_normalized(
        self,
    ):
        self.assertEqual(
            normalize_plan(" pro "),
            PLAN_PRO,
        )

    def test_supported_operation_is_normalized(
        self,
    ):
        self.assertEqual(
            normalize_operation(
                " ask_jm8 "
            ),
            OPERATION_ASK_JM8,
        )

    def test_unknown_operation_is_rejected(
        self,
    ):
        with self.assertRaises(
            UsagePolicyError
        ) as raised:
            normalize_operation(
                "OCR"
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidUsageOperation",
        )

    def test_monthly_period_uses_utc_calendar_month(
        self,
    ):
        self.assertEqual(
            monthly_usage_period(
                FIXED_NOW
            ),
            {
                "key": "2026-07",
                "startsAt": (
                    "2026-07-01"
                    "T00:00:00+00:00"
                ),
                "endsAt": (
                    "2026-08-01"
                    "T00:00:00+00:00"
                ),
                "resetsAt": (
                    "2026-08-01"
                    "T00:00:00+00:00"
                ),
            },
        )

    def test_december_period_rolls_into_next_year(
        self,
    ):
        period = monthly_usage_period(
            datetime(
                2026,
                12,
                31,
                23,
                59,
                tzinfo=timezone.utc,
            )
        )

        self.assertEqual(
            period["key"],
            "2026-12",
        )

        self.assertEqual(
            period["resetsAt"],
            (
                "2027-01-01"
                "T00:00:00+00:00"
            ),
        )

    def test_naive_datetime_is_treated_as_utc(
        self,
    ):
        period = monthly_usage_period(
            datetime(
                2026,
                2,
                15,
                10,
                0,
            )
        )

        self.assertEqual(
            period["key"],
            "2026-02",
        )

    def test_free_defaults_are_returned(
        self,
    ):
        self.assertEqual(
            get_plan_limits(
                PLAN_FREE,
                environ={},
            ),
            DEFAULT_LIMITS[
                PLAN_FREE
            ],
        )

    def test_pro_defaults_are_returned(
        self,
    ):
        self.assertEqual(
            get_plan_limits(
                PLAN_PRO,
                environ={},
            ),
            DEFAULT_LIMITS[
                PLAN_PRO
            ],
        )

    def test_environment_overrides_limits(
        self,
    ):
        limits = get_plan_limits(
            PLAN_FREE,
            environ={
                (
                    "FREE_MONTHLY_"
                    "ASK_QUESTIONS"
                ): "12",
                (
                    "FREE_MONTHLY_"
                    "ENTRY_ANALYSES"
                ): "34",
            },
        )

        self.assertEqual(
            limits[
                OPERATION_ASK_JM8
            ],
            12,
        )

        self.assertEqual(
            limits[
                OPERATION_ENTRY_ANALYSIS
            ],
            34,
        )

    def test_negative_limit_is_rejected(
        self,
    ):
        with self.assertRaises(
            UsagePolicyError
        ) as raised:
            get_plan_limits(
                PLAN_FREE,
                environ={
                    (
                        "FREE_MONTHLY_"
                        "ASK_QUESTIONS"
                    ): "-1",
                },
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidUsageLimit",
        )

    def test_non_numeric_limit_is_rejected(
        self,
    ):
        with self.assertRaises(
            UsagePolicyError
        ) as raised:
            get_plan_limits(
                PLAN_FREE,
                environ={
                    (
                        "FREE_MONTHLY_"
                        "ASK_QUESTIONS"
                    ): "many",
                },
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidUsageLimit",
        )

    def test_empty_usage_has_full_allowance(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_FREE,
            usage_item={},
            now=FIXED_NOW,
            environ={},
        )

        ask = snapshot[
            "operations"
        ]["askJm8"]

        self.assertEqual(
            ask["used"],
            0,
        )

        self.assertEqual(
            ask["reserved"],
            0,
        )

        self.assertEqual(
            ask["failed"],
            0,
        )

        self.assertEqual(
            ask["remaining"],
            5,
        )

        self.assertTrue(
            ask["allowed"]
        )

    def test_completed_operations_reduce_remaining(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_FREE,
            usage_item={
                "askJm8Completed": 2,
            },
            now=FIXED_NOW,
            environ={},
        )

        ask = snapshot[
            "operations"
        ]["askJm8"]

        self.assertEqual(
            ask["used"],
            2,
        )

        self.assertEqual(
            ask["remaining"],
            3,
        )

    def test_reserved_operations_reduce_remaining(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_FREE,
            usage_item={
                "askJm8Completed": 2,
                "askJm8Reserved": 1,
            },
            now=FIXED_NOW,
            environ={},
        )

        ask = snapshot[
            "operations"
        ]["askJm8"]

        self.assertEqual(
            ask["remaining"],
            2,
        )

    def test_failed_operations_do_not_consume_quota(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_FREE,
            usage_item={
                "askJm8Failed": 500,
            },
            now=FIXED_NOW,
            environ={},
        )

        ask = snapshot[
            "operations"
        ]["askJm8"]

        self.assertEqual(
            ask["failed"],
            500,
        )

        self.assertEqual(
            ask["remaining"],
            5,
        )

        self.assertTrue(
            ask["allowed"]
        )

    def test_request_is_blocked_at_limit(
        self,
    ):
        decision = evaluate_quota(
            plan=PLAN_FREE,
            operation=(
                OPERATION_ASK_JM8
            ),
            usage_item={
                "askJm8Completed": 4,
                "askJm8Reserved": 1,
            },
            now=FIXED_NOW,
            environ={},
        )

        self.assertFalse(
            decision["allowed"]
        )

        self.assertEqual(
            decision["remaining"],
            0,
        )

    def test_remaining_is_clamped_to_zero(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_FREE,
            usage_item={
                "askJm8Completed": 900,
            },
            now=FIXED_NOW,
            environ={},
        )

        self.assertEqual(
            snapshot[
                "operations"
            ]["askJm8"]["remaining"],
            0,
        )

    def test_entry_analysis_has_separate_counter(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_FREE,
            usage_item={
                (
                    "entryAnalysisCompleted"
                ): 7,
                "askJm8Completed": 1,
            },
            now=FIXED_NOW,
            environ={},
        )

        operations = snapshot[
            "operations"
        ]

        self.assertEqual(
            operations[
                "entryAnalysis"
            ]["remaining"],
            3,
        )

        self.assertEqual(
            operations[
                "askJm8"
            ]["remaining"],
            4,
        )

    def test_decimal_counters_are_supported(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_FREE,
            usage_item={
                "askJm8Completed": (
                    Decimal("3")
                ),
                "askJm8Reserved": (
                    Decimal("1")
                ),
            },
            now=FIXED_NOW,
            environ={},
        )

        ask = snapshot[
            "operations"
        ]["askJm8"]

        self.assertEqual(
            ask["used"],
            3,
        )

        self.assertEqual(
            ask["remaining"],
            1,
        )

    def test_invalid_counter_values_fail_safe_to_zero(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_FREE,
            usage_item={
                "askJm8Completed": -4,
                "askJm8Reserved": True,
                "askJm8Failed": "invalid",
            },
            now=FIXED_NOW,
            environ={},
        )

        ask = snapshot[
            "operations"
        ]["askJm8"]

        self.assertEqual(
            ask["used"],
            0,
        )

        self.assertEqual(
            ask["reserved"],
            0,
        )

        self.assertEqual(
            ask["failed"],
            0,
        )

    def test_snapshot_has_public_contract(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_PRO,
            usage_item={},
            now=FIXED_NOW,
            environ={},
        )

        self.assertEqual(
            set(snapshot),
            {
                "usageVersion",
                "generatedAt",
                "period",
                "plan",
                "operations",
            },
        )

        self.assertEqual(
            snapshot["plan"],
            {
                "id": "PRO",
                "label": "Pro",
            },
        )

    def test_snapshot_excludes_private_fields(
        self,
    ):
        snapshot = build_usage_snapshot(
            plan=PLAN_FREE,
            usage_item={
                "PK": "private",
                "SK": "private",
                "userId": "private",
                "entryId": "private",
            },
            now=FIXED_NOW,
            environ={},
        )

        serialized = json.dumps(
            snapshot
        )

        for forbidden in (
            "PK",
            "SK",
            "userId",
            "entryId",
        ):
            self.assertNotIn(
                forbidden,
                serialized,
            )

    def test_allowed_quota_decision_contract(
        self,
    ):
        decision = evaluate_quota(
            plan=PLAN_PRO,
            operation=(
                OPERATION_ENTRY_ANALYSIS
            ),
            usage_item={
                (
                    "entryAnalysisCompleted"
                ): 10,
            },
            now=FIXED_NOW,
            environ={},
        )

        self.assertTrue(
            decision["allowed"]
        )

        self.assertEqual(
            decision["operationKey"],
            "entryAnalysis",
        )

        self.assertEqual(
            decision["remaining"],
            240,
        )

    def test_free_limit_error_requires_upgrade(
        self,
    ):
        decision = evaluate_quota(
            plan=PLAN_FREE,
            operation=(
                OPERATION_ASK_JM8
            ),
            usage_item={
                "askJm8Completed": 5,
            },
            now=FIXED_NOW,
            environ={},
        )

        payload = (
            build_usage_limit_error(
                decision
            )
        )

        self.assertEqual(
            payload["error"],
            "UsageLimitExceeded",
        )

        self.assertEqual(
            payload["operation"],
            "askJm8",
        )

        self.assertTrue(
            payload[
                "upgradeRequired"
            ]
        )

    def test_pro_limit_error_does_not_require_upgrade(
        self,
    ):
        decision = evaluate_quota(
            plan=PLAN_PRO,
            operation=(
                OPERATION_ASK_JM8
            ),
            usage_item={
                "askJm8Completed": 100,
            },
            now=FIXED_NOW,
            environ={},
        )

        payload = (
            build_usage_limit_error(
                decision
            )
        )

        self.assertFalse(
            payload[
                "upgradeRequired"
            ]
        )

    def test_limit_error_rejects_allowed_decision(
        self,
    ):
        decision = evaluate_quota(
            plan=PLAN_FREE,
            operation=(
                OPERATION_ASK_JM8
            ),
            usage_item={},
            now=FIXED_NOW,
            environ={},
        )

        with self.assertRaises(
            UsagePolicyError
        ) as raised:
            build_usage_limit_error(
                decision
            )

        self.assertEqual(
            raised.exception.code,
            "UsageLimitNotExceeded",
        )


if __name__ == "__main__":
    unittest.main()
