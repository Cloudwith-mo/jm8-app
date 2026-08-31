import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "function"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")

import account_deletion_guard as guard  # noqa: E402


class AccountDeletionGuardBoundaryTests(unittest.TestCase):
    def test_active_lookup_is_reusable_and_receives_only_subject(self):
        lookup = MagicMock(return_value=True)
        self.assertTrue(
            guard.deletion_pending("subject-a", active_lookup=lookup)
        )
        lookup.assert_called_once_with("subject-a")

    def test_deletion_status_remains_available(self):
        request_id = "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.assertTrue(guard.route_remains_available_during_deletion(
            "GET",
            f"/account/deletion-requests/{request_id}",
        ))
        self.assertFalse(guard.route_requires_deletion_guard(
            "GET",
            f"/account/deletion-requests/{request_id}",
        ))

    def test_all_required_mutation_classes_are_in_pending_boundary(self):
        routes = {
            "entries": (
                ("POST", "/entries"),
                ("PUT", "/entries/entry-1/review"),
                ("DELETE", "/entries/entry-1"),
            ),
            "uploads": (("POST", "/upload-url"),),
            "ocr": (
                ("POST", "/entries/entry-1/ocr"),
                ("POST", "/entries/entry-1/ocr/retry"),
            ),
            "analysis": (
                ("POST", "/entries/entry-1/analyze"),
                ("POST", "/analysis/reanalysis/jobs"),
                ("POST", "/analysis/reanalysis/jobs/job-1/retry"),
            ),
            "askJm8": (("POST", "/insights/ask"),),
            "billing": (
                ("POST", "/billing/checkout"),
                ("POST", "/billing/portal"),
            ),
            "accountExports": (("POST", "/account/exports"),),
        }
        self.assertEqual(
            set(guard.PENDING_MUTATING_ROUTE_CLASSES),
            set(routes),
        )
        for route_class, class_routes in routes.items():
            with self.subTest(route_class=route_class):
                for method, path in class_routes:
                    self.assertTrue(
                        guard.route_requires_deletion_guard(method, path),
                        f"missing pending deletion guard for {method} {path}",
                    )

    def test_partial_production_enforcement_is_explicitly_disabled_for_3c3a(self):
        app_source = (ROOT / "function" / "app.py").read_text()
        data_contract = (
            ROOT.parent / "docs" / "JM8_USER_DATA_CONTRACT.md"
        ).read_text()
        self.assertFalse(guard.DELETION_GUARD_ENFORCEMENT_ACTIVE)
        self.assertNotIn("route_requires_deletion_guard", app_source)
        self.assertIn("does not globally enforce", data_contract)
        self.assertIn("Phase 3C3B", data_contract)


if __name__ == "__main__":
    unittest.main()
