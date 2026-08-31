import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "function"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("TABLE_NAME", "journalm8-test-main")
os.environ.setdefault("RAW_BUCKET", "journalm8-test-raw-000000000000")

import account_deletion_api as api  # noqa: E402


REQUEST_ID = "del_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NOW = 1_777_800_000
VALID_BODY = {
    "confirmation": "DELETE_MY_ACCOUNT",
    "requestToken": "550e8400-e29b-41d4-a716-446655440000",
}


def claims(subject="subject-a", auth_time=NOW):
    return {
        "sub": subject,
        "auth_time": str(auth_time),
        "email": "private@example.com",
        "stripeCustomerId": "cus_secret",
    }


def request(status="REQUESTED"):
    return {
        "PK": f"ACCOUNT_DELETION#{REQUEST_ID}",
        "SK": "REQUEST",
        "requestId": REQUEST_ID,
        "subjectDigest": "secret-subject-digest",
        "status": status,
        "requestedAt": "2026-08-30T12:00:00Z",
        "workflowExecutionArn": "arn:aws:states:us-east-1:000:execution:secret",
    }


class AccountDeletionApiTests(unittest.TestCase):
    @patch.object(api.time, "time", return_value=NOW)
    def test_recent_authentication_rejects_missing_stale_and_future_claims(self, now):
        starter = MagicMock()
        for auth_time in (None, NOW - 301, NOW + 61, True, "invalid"):
            with self.subTest(auth_time=auth_time):
                with self.assertRaises(api.AccountDeletionApiError) as raised:
                    api.create_account_deletion_request(
                        "subject-a",
                        claims(auth_time=auth_time),
                        VALID_BODY,
                        workflow_starter=starter,
                    )
                self.assertEqual(raised.exception.status_code, 401)
                self.assertEqual(
                    raised.exception.payload["error"],
                    "RecentAuthenticationRequired",
                )
        starter.assert_not_called()

    @patch.object(api.time, "time", return_value=NOW)
    def test_recent_auth_window_is_configurable_with_secure_fallback(self, now):
        with patch.dict(
            os.environ,
            {"ACCOUNT_DELETION_RECENT_AUTH_SECONDS": "600"},
        ):
            self.assertEqual(api.recent_auth_window_seconds(), 600)
            api._require_recent_authentication(claims(auth_time=NOW - 599))
        for invalid in ("0", "7200", "not-a-number"):
            with self.subTest(invalid=invalid), patch.dict(
                os.environ,
                {"ACCOUNT_DELETION_RECENT_AUTH_SECONDS": invalid},
            ):
                self.assertEqual(
                    api.recent_auth_window_seconds(),
                    api.DEFAULT_RECENT_AUTH_SECONDS,
                )

    @patch.object(api.time, "time", return_value=NOW)
    def test_explicit_confirmation_and_request_token_are_required(self, now):
        starter = MagicMock()
        for body, expected in (
            ({"requestToken": VALID_BODY["requestToken"]}, "DeletionConfirmationRequired"),
            ({"confirmation": "yes", "requestToken": VALID_BODY["requestToken"]}, "DeletionConfirmationRequired"),
            ({"confirmation": "DELETE_MY_ACCOUNT"}, "InvalidRequestToken"),
            ({"confirmation": "DELETE_MY_ACCOUNT", "requestToken": "short"}, "InvalidRequestToken"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(api.AccountDeletionApiError) as raised:
                    api.create_account_deletion_request(
                        "subject-a",
                        claims(),
                        body,
                        workflow_starter=starter,
                    )
                self.assertEqual(raised.exception.status_code, 400)
                self.assertEqual(raised.exception.payload["error"], expected)
        starter.assert_not_called()

    @patch.object(api.time, "time", return_value=NOW)
    def test_token_validation_exception_cannot_reach_any_public_or_log_output(
        self, now,
    ):
        sensitive_values = (
            "sensitive-token-value",
            "user@example.com",
            "cus_secret",
            "raw-cognito-subject",
        )
        validation_error = ValueError(" ".join(sensitive_values))
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch.object(api, "request_token_digest", side_effect=validation_error),
            patch("builtins.print") as structured_logs,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            self.assertRaises(api.AccountDeletionApiError) as raised,
        ):
            api.create_account_deletion_request(
                "subject-a",
                claims(),
                VALID_BODY,
                workflow_starter=MagicMock(),
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.payload, {
            "error": "InvalidRequestToken",
            "message": "The request token is invalid.",
            "retryable": False,
        })
        response_text = json.dumps(raised.exception.payload)
        log_text = " ".join(
            " ".join(map(str, call.args))
            for call in structured_logs.call_args_list
        )
        serialized_request = json.dumps(
            api.serialize_public_deletion_request(request())
        )
        for sensitive in sensitive_values:
            self.assertNotIn(sensitive, response_text)
            self.assertNotIn(sensitive, stdout.getvalue())
            self.assertNotIn(sensitive, stderr.getvalue())
            self.assertNotIn(sensitive, log_text)
            self.assertNotIn(sensitive, serialized_request)
        structured_logs.assert_not_called()

    def test_production_deletion_modules_never_stringify_exceptions(self):
        function_root = Path(__file__).resolve().parents[1] / "function"
        modules = sorted(function_root.glob("account_deletion_*.py"))
        self.assertTrue(modules)
        for module in modules:
            with self.subTest(module=module.name):
                source = module.read_text()
                self.assertNotIn("str(exc)", source)
                self.assertNotIn("repr(exc)", source)

    @patch.object(api, "create_or_replay_deletion")
    @patch.object(api.time, "time", return_value=NOW)
    def test_production_path_is_temporarily_unavailable_without_workflow(
        self, now, create,
    ):
        with self.assertRaises(api.AccountDeletionApiError) as raised:
            api.create_account_deletion_request(
                "subject-a",
                claims(),
                VALID_BODY,
            )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertTrue(raised.exception.payload["retryable"])
        create.assert_not_called()

    @patch("builtins.print")
    @patch.object(api, "record_workflow_execution")
    @patch.object(api, "create_or_replay_deletion")
    @patch.object(api.time, "time", return_value=NOW)
    def test_new_request_returns_202_with_safe_json_response(
        self, now, create, record_execution, log,
    ):
        create.return_value = (request(), False)
        record_execution.return_value = request()
        starter = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:000:execution:test",
        })

        status, payload = api.create_account_deletion_request(
            "subject-a",
            claims(),
            VALID_BODY,
            workflow_starter=starter,
        )

        self.assertEqual(status, 202)
        self.assertFalse(payload["replayed"])
        self.assertEqual(payload["deletionRequest"]["requestId"], REQUEST_ID)
        self.assertIn("residualRetention", payload["deletionRequest"])
        starter.assert_called_once_with(request_id=REQUEST_ID, subject="subject-a")
        serialized = json.dumps(payload)
        for secret in (
            "subject-a",
            "private@example.com",
            "cus_secret",
            VALID_BODY["requestToken"],
            "secret-subject-digest",
            "execution:secret",
        ):
            self.assertNotIn(secret, serialized)
        log_text = " ".join(call.args[0] for call in log.call_args_list)
        self.assertNotIn("subject-a", log_text)
        self.assertNotIn("private@example.com", log_text)
        self.assertNotIn(VALID_BODY["requestToken"], log_text)

    @patch("builtins.print")
    @patch.object(api, "record_workflow_execution")
    @patch.object(api, "create_or_replay_deletion")
    @patch.object(api.time, "time", return_value=NOW)
    def test_idempotent_replay_returns_200_without_restarting_workflow(
        self, now, create, record_execution, log,
    ):
        create.return_value = (request(), True)
        starter = MagicMock()

        status, payload = api.create_account_deletion_request(
            "subject-a",
            claims(),
            VALID_BODY,
            workflow_starter=starter,
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["replayed"])
        starter.assert_not_called()
        record_execution.assert_not_called()

    @patch.object(api, "create_or_replay_deletion")
    @patch.object(api.time, "time", return_value=NOW)
    def test_conflict_and_store_unavailability_are_explicitly_mapped(
        self, now, create,
    ):
        for exception, status, code in (
            (api.ActiveDeletionExists(), 409, "ActiveDeletionExists"),
            (
                api.DeletionStoreUnavailable(),
                503,
                "AccountDeletionUnavailable",
            ),
        ):
            with self.subTest(code=code):
                create.side_effect = exception
                with self.assertRaises(api.AccountDeletionApiError) as raised:
                    api.create_account_deletion_request(
                        "subject-a",
                        claims(),
                        VALID_BODY,
                        workflow_starter=MagicMock(),
                    )
                self.assertEqual(raised.exception.status_code, status)
                self.assertEqual(raised.exception.payload["error"], code)

    @patch("builtins.print")
    @patch.object(api, "fail_deletion_request")
    @patch.object(api, "create_or_replay_deletion", return_value=(request(), False))
    @patch.object(api.time, "time", return_value=NOW)
    def test_workflow_failure_does_not_expose_exception_or_private_values(
        self, now, create, fail_request, log,
    ):
        starter = MagicMock(
            side_effect=RuntimeError("secret AWS message private@example.com")
        )

        with self.assertRaises(api.AccountDeletionApiError) as raised:
            api.create_account_deletion_request(
                "subject-a",
                claims(),
                VALID_BODY,
                workflow_starter=starter,
            )

        self.assertEqual(raised.exception.status_code, 503)
        response_text = json.dumps(raised.exception.payload)
        log_text = " ".join(call.args[0] for call in log.call_args_list)
        for secret in (
            "secret AWS message",
            "private@example.com",
            "subject-a",
            VALID_BODY["requestToken"],
        ):
            self.assertNotIn(secret, response_text)
            self.assertNotIn(secret, log_text)
        fail_request.assert_called_once_with(
            subject="subject-a",
            request_id=REQUEST_ID,
            failure_code="DeletionWorkflowStartFailed",
            retryable=True,
        )

    @patch.object(api, "get_deletion_request")
    def test_detail_response_is_owned_filtered_and_store_errors_are_safe(self, get):
        get.return_value = request()
        status, payload = api.get_account_deletion_request("subject-a", REQUEST_ID)
        self.assertEqual(status, 200)
        self.assertNotIn("subjectDigest", payload["deletionRequest"])
        self.assertNotIn("workflowExecutionArn", payload["deletionRequest"])
        json.dumps(payload)

        get.return_value = None
        with self.assertRaises(api.AccountDeletionApiError) as missing:
            api.get_account_deletion_request("subject-b", REQUEST_ID)
        self.assertEqual(missing.exception.status_code, 404)

        get.side_effect = api.DeletionStoreUnavailable("secret AWS message")
        with self.assertRaises(api.AccountDeletionApiError) as unavailable:
            api.get_account_deletion_request("subject-a", REQUEST_ID)
        self.assertEqual(unavailable.exception.status_code, 503)
        self.assertNotIn("secret AWS message", json.dumps(unavailable.exception.payload))


if __name__ == "__main__":
    unittest.main()
