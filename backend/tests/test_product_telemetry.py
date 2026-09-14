import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")


from product_telemetry import emit_product_metric  # noqa: E402
from product_telemetry_contract import (  # noqa: E402
    ProductTelemetryContractError,
)
from product_telemetry_store import record_product_milestone  # noqa: E402


NOW = datetime(2026, 9, 14, 1, 2, 3, tzinfo=timezone.utc)


class FakeClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__("private failure details")
        self.response = {
            "Error": {"Code": code, "Message": "private failure details"}
        }


def client_error(code: str) -> FakeClientError:
    return FakeClientError(code)


class FakeTable:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls = []
        self.keys = set()

    def put_item(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        item = kwargs["Item"]
        key = (item["PK"], item["SK"])
        if key in self.keys:
            raise client_error("ConditionalCheckFailedException")
        self.keys.add(key)
        return {}


class ProductTelemetryEmissionTests(unittest.TestCase):
    def test_emitter_writes_one_exact_serialized_event(self):
        output = []
        result = emit_product_metric(
            "prod",
            "OcrCompleted",
            occurred_at=NOW,
            sink=output.append,
        )
        self.assertEqual(result, {"status": "EMITTED", "metricName": "OcrCompleted"})
        self.assertEqual(len(output), 1)
        event = json.loads(output[0])
        self.assertEqual(event["OcrCompleted"], 1)
        self.assertNotIn("Dimensions", event["_aws"]["CloudWatchMetrics"][0])

    def test_sink_failure_is_returned_and_safe_failure_metric_is_attempted(self):
        failures = []

        def unavailable(_event):
            raise RuntimeError("private sink details")

        result = emit_product_metric(
            "prod",
            "OcrFailed",
            occurred_at=NOW,
            sink=unavailable,
            failure_sink=failures.append,
        )

        self.assertEqual(
            result,
            {
                "status": "FAILED",
                "metricName": "OcrFailed",
                "errorCode": "TelemetryEmissionUnavailable",
            },
        )
        self.assertEqual(
            json.loads(failures[0])["metricName"],
            "TelemetryEmissionFailed",
        )
        self.assertNotIn("private sink details", json.dumps(result))

    def test_primary_and_failure_sink_errors_never_escape(self):
        def unavailable(_event):
            raise RuntimeError("unavailable")

        result = emit_product_metric(
            "prod",
            "CheckoutStarted",
            occurred_at=NOW,
            sink=unavailable,
            failure_sink=unavailable,
        )
        self.assertEqual(result["status"], "FAILED")

    def test_contract_errors_still_fail_closed(self):
        with self.assertRaises(ProductTelemetryContractError):
            emit_product_metric("prod", "UnapprovedMetric", sink=lambda _event: None)


class ProductTelemetryMilestoneTests(unittest.TestCase):
    def test_first_milestone_is_stored_and_emitted_once(self):
        table = FakeTable()
        output = []

        first = record_product_milestone(
            "private-user",
            "prod",
            "FirstEntryCreated",
            occurred_at=NOW,
            table_resource=table,
            sink=output.append,
        )
        duplicate = record_product_milestone(
            "private-user",
            "prod",
            "FirstEntryCreated",
            occurred_at=NOW,
            table_resource=table,
            sink=output.append,
        )

        self.assertEqual(first["status"], "RECORDED_AND_EMITTED")
        self.assertEqual(duplicate["status"], "ALREADY_RECORDED")
        self.assertEqual(len(output), 1)
        self.assertEqual(
            table.calls[0]["ConditionExpression"],
            "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )

    def test_marker_contains_no_content_or_secondary_identifier(self):
        table = FakeTable()
        record_product_milestone(
            "private-user",
            "prod",
            "FirstGroundedAskCompleted",
            occurred_at=NOW,
            table_resource=table,
            sink=lambda _event: None,
        )
        item = table.calls[0]["Item"]
        self.assertEqual(item["PK"], "USER#private-user")
        self.assertEqual(
            item["SK"],
            "PRODUCT_MILESTONE#FirstGroundedAskCompleted",
        )
        self.assertEqual(
            set(item),
            {
                "PK", "SK", "entityType", "telemetryVersion",
                "metricName", "metricValue", "recordedAt",
            },
        )
        serialized = json.dumps(item)
        for forbidden in (
            "rawText", "cleanText", "question", "answer", "entryId",
            "email", "filename", "stripeCustomerId", "subscriptionId",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_weekly_activity_derives_and_validates_iso_period(self):
        table = FakeTable()
        result = record_product_milestone(
            "private-user",
            "prod",
            "WeeklyActiveUser",
            occurred_at=NOW,
            table_resource=table,
            sink=lambda _event: None,
        )
        self.assertEqual(result["status"], "RECORDED_AND_EMITTED")
        self.assertEqual(table.calls[0]["Item"]["period"], "2026-W38")
        self.assertEqual(
            table.calls[0]["Item"]["SK"],
            "PRODUCT_MILESTONE#WeeklyActiveUser#2026-W38",
        )
        with self.assertRaises(ProductTelemetryContractError):
            record_product_milestone(
                "private-user",
                "prod",
                "WeeklyActiveUser",
                occurred_at=NOW,
                period="2026-W37",
                table_resource=table,
                sink=lambda _event: None,
            )

    def test_non_milestone_metrics_cannot_create_user_records(self):
        table = FakeTable()
        with self.assertRaises(ProductTelemetryContractError):
            record_product_milestone(
                "private-user",
                "prod",
                "OcrCompleted",
                occurred_at=NOW,
                table_resource=table,
                sink=lambda _event: None,
            )
        self.assertEqual(table.calls, [])

    def test_store_failure_is_safe_and_emits_only_failure_metric(self):
        output = []
        result = record_product_milestone(
            "private-user",
            "prod",
            "ActivatedUser",
            occurred_at=NOW,
            table_resource=FakeTable(error=client_error("ProvisionedThroughputExceededException")),
            sink=output.append,
        )
        self.assertEqual(
            result,
            {
                "status": "STORE_FAILED",
                "metricName": "ActivatedUser",
                "errorCode": "ProductMilestoneStoreUnavailable",
            },
        )
        self.assertEqual(len(output), 1)
        self.assertEqual(
            json.loads(output[0])["metricName"],
            "TelemetryEmissionFailed",
        )
        self.assertNotIn("private-user", json.dumps(result))

    def test_emission_failure_does_not_undo_or_raise_from_milestone(self):
        failure_output = []

        def unavailable(_event):
            raise RuntimeError("private output")

        result = record_product_milestone(
            "private-user",
            "prod",
            "ActivatedUser",
            occurred_at=NOW,
            table_resource=FakeTable(),
            sink=unavailable,
            failure_sink=failure_output.append,
        )
        self.assertEqual(result["status"], "RECORDED_EMISSION_FAILED")
        self.assertEqual(
            json.loads(failure_output[0])["metricName"],
            "TelemetryEmissionFailed",
        )

    def test_invalid_identity_fails_before_storage(self):
        table = FakeTable()
        for invalid in ("", "   ", None, 1):
            with self.assertRaises(ProductTelemetryContractError):
                record_product_milestone(
                    invalid,
                    "prod",
                    "ActivatedUser",
                    occurred_at=NOW,
                    table_resource=table,
                    sink=lambda _event: None,
                )
        self.assertEqual(table.calls, [])

    def test_results_and_emitted_logs_never_expose_user_identity(self):
        table = FakeTable()
        output = []
        result = record_product_milestone(
            "private-user",
            "prod",
            "ActivatedUser",
            occurred_at=NOW,
            table_resource=table,
            sink=output.append,
        )
        self.assertNotIn("private-user", json.dumps(result))
        self.assertNotIn("private-user", "".join(output))


if __name__ == "__main__":
    unittest.main()
