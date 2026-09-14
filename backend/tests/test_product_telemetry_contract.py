import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))


from product_telemetry_contract import (  # noqa: E402
    ALL_METRICS,
    COUNT_METRICS,
    DURATION_METRICS,
    ProductTelemetryContractError,
    build_metric_event,
    metric_namespace,
    milestone_sort_key,
    serialize_metric_event,
    utc_week,
    validate_metric_event,
)


NOW = datetime(2026, 9, 14, 1, 2, 3, 456000, tzinfo=timezone.utc)


class ProductTelemetryContractTests(unittest.TestCase):
    def test_exact_alpha_metric_registry(self):
        self.assertEqual(
            ALL_METRICS,
            {
                "ActivatedUser", "WeeklyActiveUser", "FirstEntryCreated",
                "OcrStarted", "OcrCompleted", "OcrFailed",
                "FirstAnalysisCompleted", "FirstGroundedAskCompleted",
                "SecondWeekReturned", "AccountExportRequested",
                "AccountExportCompleted", "AccountExportFailed",
                "AccountDeletionRequested", "AccountDeletionCompleted",
                "AccountDeletionFailed", "CheckoutStarted",
                "ProEntitlementActivated", "CancellationRequested",
                "MeaningfulInsightYes", "MeaningfulInsightNo",
                "TelemetryEmissionFailed", "TimeToFirstMeaningfulInsightMs",
            },
        )
        self.assertEqual(COUNT_METRICS | DURATION_METRICS, ALL_METRICS)

    def test_namespace_is_strictly_stage_isolated(self):
        self.assertEqual(metric_namespace(" PROD "), "JM8/prod/Product")
        self.assertEqual(metric_namespace("staging"), "JM8/staging/Product")
        for invalid in ("", "production", "qa", None, 1):
            with self.assertRaises(ProductTelemetryContractError):
                metric_namespace(invalid)

    def test_count_event_is_dimensionless_and_identifier_free(self):
        event = build_metric_event(
            "prod",
            "FirstEntryCreated",
            occurred_at=NOW,
        )

        directive = event["_aws"]["CloudWatchMetrics"][0]
        self.assertEqual(directive["Namespace"], "JM8/prod/Product")
        self.assertNotIn("Dimensions", directive)
        self.assertEqual(
            directive["Metrics"],
            [{"Name": "FirstEntryCreated", "Unit": "Count"}],
        )
        self.assertEqual(event["FirstEntryCreated"], 1)
        self.assertEqual(event["_aws"]["Timestamp"], 1789347723456)

        serialized = serialize_metric_event(event)
        for forbidden in (
            "userId", "entryId", "rawText", "cleanText", "ocrText",
            "question", "answer", "email", "filename", "exportId",
            "deletionId", "stripeCustomerId", "subscriptionId",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_meaningful_insight_duration_is_bounded(self):
        event = build_metric_event(
            "prod",
            "TimeToFirstMeaningfulInsightMs",
            value=86_400_000,
            occurred_at=NOW,
        )
        metric = event["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]
        self.assertEqual(metric["Unit"], "Milliseconds")
        self.assertEqual(event["TimeToFirstMeaningfulInsightMs"], 86_400_000)

        for invalid in (0, -1, 90 * 24 * 60 * 60 * 1000 + 1, True, "1"):
            with self.assertRaises(ProductTelemetryContractError):
                build_metric_event(
                    "prod",
                    "TimeToFirstMeaningfulInsightMs",
                    value=invalid,
                    occurred_at=NOW,
                )

    def test_count_events_cannot_batch_or_accept_arbitrary_values(self):
        for invalid in (0, 2, -1, True, "1"):
            with self.assertRaises(ProductTelemetryContractError):
                build_metric_event(
                    "prod",
                    "OcrCompleted",
                    value=invalid,
                    occurred_at=NOW,
                )

    def test_unknown_metrics_and_naive_timestamps_fail_closed(self):
        with self.assertRaises(ProductTelemetryContractError):
            build_metric_event("prod", "JournalTextCaptured", occurred_at=NOW)
        with self.assertRaises(ProductTelemetryContractError):
            build_metric_event(
                "prod",
                "ActivatedUser",
                occurred_at=datetime(2026, 9, 14),
            )

    def test_validation_rejects_extra_metadata_and_modified_directives(self):
        event = build_metric_event("prod", "ActivatedUser", occurred_at=NOW)
        validate_metric_event(event)

        with_extra = {**event, "userId": "private-user"}
        with self.assertRaises(ProductTelemetryContractError):
            validate_metric_event(with_extra)

        modified = json.loads(json.dumps(event))
        modified["_aws"]["CloudWatchMetrics"][0]["Dimensions"] = [["userId"]]
        with self.assertRaises(ProductTelemetryContractError):
            validate_metric_event(modified)

    def test_milestone_keys_are_user_partition_local_and_exact(self):
        self.assertEqual(
            milestone_sort_key("FirstEntryCreated"),
            "PRODUCT_MILESTONE#FirstEntryCreated",
        )
        self.assertEqual(
            milestone_sort_key("WeeklyActiveUser", period="2026-W37"),
            "PRODUCT_MILESTONE#WeeklyActiveUser#2026-W37",
        )
        self.assertNotIn("user", milestone_sort_key("FirstEntryCreated").casefold())

        for invalid in ("2026-W00", "2026-W54", "2026-37", None):
            with self.assertRaises(ProductTelemetryContractError):
                milestone_sort_key("WeeklyActiveUser", period=invalid)
        with self.assertRaises(ProductTelemetryContractError):
            milestone_sort_key("OcrCompleted")
        with self.assertRaises(ProductTelemetryContractError):
            milestone_sort_key("ActivatedUser", period="2026-W37")

    def test_utc_week_uses_iso_week_boundaries(self):
        self.assertEqual(
            utc_week(datetime(2027, 1, 1, tzinfo=timezone.utc)),
            "2026-W53",
        )

    def test_module_has_no_vendor_or_model_dependency(self):
        source = (FUNCTION_DIR / "product_telemetry_contract.py").read_text().casefold()
        for forbidden in ("bedrock", "stripe.com", "posthog", "mixpanel", "segment.io"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
