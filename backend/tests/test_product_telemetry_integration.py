import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

from product_telemetry_integration import (  # noqa: E402
    is_grounded_answer,
    record_authenticated_activity,
    record_product_outcome,
)


class ProductTelemetryIntegrationTests(unittest.TestCase):
    def test_authenticated_activity_records_first_and_weekly_milestones(self):
        calls = []
        guards = []

        def recorder(user_id, stage, metric_name):
            calls.append((user_id, stage, metric_name))
            return {"status": "RECORDED_AND_EMITTED", "metricName": metric_name}

        result = record_authenticated_activity(
            "private-user",
            stage="prod",
            mutation_guard=guards.append,
            recorder=recorder,
        )

        self.assertEqual(guards, ["private-user"])
        self.assertEqual(
            calls,
            [
                ("private-user", "prod", "ActivatedUser"),
                ("private-user", "prod", "WeeklyActiveUser"),
            ],
        )
        self.assertEqual(result["status"], "RECORDED")

    def test_missing_stage_skips_storage(self):
        calls = []
        with patch.dict(os.environ, {}, clear=True):
            result = record_authenticated_activity(
                "private-user",
                recorder=lambda *args: calls.append(args),
            )
        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["reason"], "StageUnavailable")
        self.assertEqual(calls, [])

    def test_guard_failure_skips_activity_without_raising_or_writing(self):
        calls = []

        def unavailable(_user_id):
            raise RuntimeError("private guard details")

        result = record_authenticated_activity(
            "private-user",
            stage="prod",
            mutation_guard=unavailable,
            recorder=lambda *args: calls.append(args),
        )

        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(calls, [])
        self.assertNotIn("private-user", str(result))
        self.assertNotIn("private guard details", str(result))

    def test_success_outcomes_use_the_closed_milestone_set(self):
        calls = []

        def recorder(user_id, stage, metric_name):
            calls.append((user_id, stage, metric_name))
            return {"status": "RECORDED_AND_EMITTED", "metricName": metric_name}

        for milestone in (
            "FirstEntryCreated",
            "FirstAnalysisCompleted",
            "FirstGroundedAskCompleted",
        ):
            result = record_product_outcome(
                "private-user",
                milestone,
                stage="prod",
                recorder=recorder,
            )
            self.assertEqual(result["status"], "RECORDED")

        skipped = record_product_outcome(
            "private-user",
            "OcrCompleted",
            stage="prod",
            recorder=recorder,
        )
        self.assertEqual(skipped["status"], "SKIPPED")
        self.assertEqual(
            [call[2] for call in calls],
            [
                "FirstEntryCreated",
                "FirstAnalysisCompleted",
                "FirstGroundedAskCompleted",
            ],
        )

    def test_recorder_failure_is_isolated_from_product_action(self):
        def unavailable(*_args):
            raise RuntimeError("private telemetry failure")

        result = record_product_outcome(
            "private-user",
            "FirstEntryCreated",
            stage="prod",
            recorder=unavailable,
        )

        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(
            result["metricStatuses"][0]["errorCode"],
            "TelemetryIntegrationUnavailable",
        )
        self.assertNotIn("private-user", str(result))
        self.assertNotIn("private telemetry failure", str(result))

    def test_store_or_emission_degradation_is_reported_as_partial(self):
        for status in ("STORE_FAILED", "RECORDED_EMISSION_FAILED"):
            with self.subTest(status=status):
                result = record_product_outcome(
                    "private-user",
                    "FirstAnalysisCompleted",
                    stage="prod",
                    recorder=lambda *_args, status=status: {
                        "status": status,
                        "metricName": "FirstAnalysisCompleted",
                    },
                )
                self.assertEqual(result["status"], "PARTIAL")

    def test_grounded_answer_requires_answered_status_and_evidence(self):
        self.assertTrue(is_grounded_answer({
            "status": "ANSWERED",
            "evidence": [{"date": "2026-09-14"}],
        }))
        self.assertFalse(is_grounded_answer({
            "status": "ANSWERED",
            "evidence": [],
        }))
        self.assertFalse(is_grounded_answer({
            "status": "INSUFFICIENT_CONTEXT",
            "evidence": [{"date": "2026-09-14"}],
        }))
        self.assertFalse(is_grounded_answer(None))


class ProductTelemetryAppWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[1]
            / "function"
            / "app.py"
        ).read_text(encoding="utf-8")

    def test_activity_is_recorded_after_authentication(self):
        authenticated = self.source.index("user_id = get_user_id(event)")
        activity = self.source.index("record_authenticated_activity(")
        first_route = self.source.index(
            'if method == "POST" and path == "/entries"'
        )
        self.assertLess(authenticated, activity)
        self.assertLess(activity, first_route)

    def test_entry_milestone_follows_persisted_entry(self):
        created = self.source.index("entry = create_text_entry(")
        milestone = self.source.index('"FirstEntryCreated"')
        response = self.source.index('"message": "Entry created."')
        self.assertLess(created, milestone)
        self.assertLess(milestone, response)

    def test_analysis_milestone_follows_usage_completion(self):
        route = self.source.index(
            'path.endswith("/analyze")'
        )
        completed = self.source.index(
            "complete_entry_analysis_usage(",
            route,
        )
        milestone = self.source.index(
            '"FirstAnalysisCompleted"',
            route,
        )
        self.assertLess(completed, milestone)

    def test_grounded_ask_milestone_is_evidence_gated(self):
        route = self.source.index('path\n            == "/insights/ask"')
        completed = self.source.index("complete_ask_usage(", route)
        grounded = self.source.index("is_grounded_answer(answer)", route)
        milestone = self.source.index(
            '"FirstGroundedAskCompleted"',
            route,
        )
        self.assertLess(completed, grounded)
        self.assertLess(grounded, milestone)


if __name__ == "__main__":
    unittest.main()
