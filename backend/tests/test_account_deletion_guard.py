import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "function"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")

import account_deletion_guard as guard  # noqa: E402
import app  # noqa: E402


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
            "askHistory": (("DELETE", "/insights/ask/history/history-1"),),
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

    def test_complete_production_enforcement_is_enabled_for_3c3b(self):
        app_source = (ROOT / "function" / "app.py").read_text()
        data_contract = (
            ROOT.parent / "docs" / "JM8_USER_DATA_CONTRACT.md"
        ).read_text()
        self.assertTrue(guard.DELETION_GUARD_ENFORCEMENT_ACTIVE)
        self.assertIn("route_requires_deletion_guard", app_source)
        self.assertIn("fails closed for mutations", data_contract)
        self.assertIn("Phase 3C3B enforces", data_contract)

    def test_guard_blocks_active_deletion_and_fails_closed(self):
        with self.assertRaises(guard.AccountDeletionInProgress):
            guard.ensure_user_mutation_allowed(
                "subject-a", active_lookup=lambda _subject: True,
            )

        def unavailable(_subject):
            raise guard.DeletionStoreUnavailable()

        with self.assertRaises(guard.DeletionGuardUnavailable):
            guard.ensure_user_mutation_allowed(
                "subject-a", active_lookup=unavailable,
            )

    def test_every_guarded_route_returns_fixed_privacy_safe_response(self):
        routes = [
            route
            for route_class in {
                "entries": (("POST", "/entries"), ("PUT", "/entries/e/review"), ("DELETE", "/entries/e")),
                "uploads": (("POST", "/upload-url"),),
                "ocr": (("POST", "/entries/e/ocr"), ("POST", "/entries/e/ocr/retry")),
                "analysis": (("POST", "/entries/e/analyze"), ("POST", "/analysis/reanalysis/jobs"), ("POST", "/analysis/reanalysis/jobs/j/retry")),
                "ask": (("POST", "/insights/ask"),),
                "askHistory": (("DELETE", "/insights/ask/history/h"),),
                "billing": (("POST", "/billing/checkout"), ("POST", "/billing/portal")),
                "exports": (("POST", "/account/exports"),),
            }.values()
            for route in route_class
        ]
        event_base = {
            "requestContext": {
                "authorizer": {"jwt": {"claims": {"sub": "subject-a"}}},
            },
            "body": "{}",
        }
        with patch.object(
            app,
            "ensure_user_mutation_allowed",
            side_effect=guard.AccountDeletionInProgress(),
        ):
            for method, path in routes:
                with self.subTest(method=method, path=path):
                    event = dict(event_base)
                    event["requestContext"] = dict(event_base["requestContext"])
                    event["requestContext"]["http"] = {"method": method, "path": path}
                    result = app.lambda_handler(event, None)
                    self.assertEqual(result["statusCode"], 409)
                    self.assertEqual(json.loads(result["body"]), {
                        "error": "AccountDeletionInProgress",
                        "message": "Account deletion is in progress.",
                        "retryable": False,
                    })

    def test_route_guard_store_failure_returns_fixed_503(self):
        event = {
            "requestContext": {
                "http": {"method": "POST", "path": "/entries"},
                "authorizer": {"jwt": {"claims": {"sub": "subject-a"}}},
            },
            "body": "{}",
        }
        with patch.object(
            app,
            "ensure_user_mutation_allowed",
            side_effect=guard.DeletionGuardUnavailable(),
        ):
            result = app.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 503)
        self.assertEqual(json.loads(result["body"]), {
            "error": "AccountDeletionGuardUnavailable",
            "message": "Account status is temporarily unavailable.",
            "retryable": True,
        })


if __name__ == "__main__":
    unittest.main()
