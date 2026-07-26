import io
import json
import os
import unittest
from contextlib import (
    redirect_stdout,
)
from pathlib import Path
from unittest.mock import patch


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


from app import lambda_handler  # noqa: E402
from ask_history_api import (  # noqa: E402
    AskHistoryApiError,
    get_ask_history_for_api,
    list_ask_history_for_api,
)
from ask_history_store import (  # noqa: E402
    AskHistoryStoreError,
)


HISTORY_ID = (
    "askhist_api123456789012"
)


def api_event(
    method: str,
    path: str,
    *,
    query=None,
    user_id="first-user",
):
    return {
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
            },
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": user_id,
                    },
                },
            },
        },
        "queryStringParameters": query,
    }


def history_summary():
    return {
        "historyVersion": "1.0",
        "historyId": HISTORY_ID,
        "createdAt": (
            "2026-07-25"
            "T22:00:00+00:00"
        ),
        "answerVersion": "1.0",
        "question": (
            "What pattern keeps "
            "returning?"
        ),
        "status": "ANSWERED",
        "scope": {
            "startDate": None,
            "endDate": None,
            "firstEntryAt": None,
            "latestEntryAt": None,
        },
        "headline": (
            "Consistency keeps returning"
        ),
        "summary": (
            "Structured routines "
            "appear repeatedly."
        ),
        "evidenceCount": 1,
        "takeawayCount": 1,
    }


def history_detail():
    return {
        "historyVersion": "1.0",
        "historyId": HISTORY_ID,
        "createdAt": (
            "2026-07-25"
            "T22:00:00+00:00"
        ),
        "answer": {
            "answerVersion": "1.0",
            "status": "ANSWERED",
            "question": (
                "What pattern keeps "
                "returning?"
            ),
        },
    }


class AskHistoryApiServiceTests(
    unittest.TestCase
):
    @patch(
        "ask_history_api.list_ask_history"
    )
    def test_input_error_maps_to_400(
        self,
        list_history,
    ):
        list_history.side_effect = (
            AskHistoryStoreError(
                "InvalidAskHistoryLimit",
                "private input detail",
                retryable=False,
            )
        )

        with self.assertRaises(
            AskHistoryApiError
        ) as raised:
            list_ask_history_for_api(
                "private-user",
                limit="invalid",
            )

        self.assertEqual(
            raised.exception.status_code,
            400,
        )

        self.assertEqual(
            raised.exception.payload[
                "error"
            ],
            "InvalidAskHistoryLimit",
        )

        self.assertNotIn(
            "private input",
            json.dumps(
                raised.exception.payload
            ),
        )

    @patch(
        "ask_history_api.list_ask_history"
    )
    def test_retryable_store_error_maps_to_503(
        self,
        list_history,
    ):
        list_history.side_effect = (
            AskHistoryStoreError(
                "ThrottlingException",
                "private provider detail",
                retryable=True,
            )
        )

        with self.assertRaises(
            AskHistoryApiError
        ) as raised:
            list_ask_history_for_api(
                "private-user"
            )

        self.assertEqual(
            raised.exception.status_code,
            503,
        )

        self.assertTrue(
            raised.exception.payload[
                "retryable"
            ]
        )

        self.assertEqual(
            raised.exception.payload[
                "retryAfterSeconds"
            ],
            2,
        )

    @patch(
        "ask_history_api.list_ask_history"
    )
    def test_nonretryable_store_error_is_safe(
        self,
        list_history,
    ):
        list_history.side_effect = (
            AskHistoryStoreError(
                "InvalidAskHistoryRecord",
                "private record detail",
                retryable=False,
            )
        )

        with self.assertRaises(
            AskHistoryApiError
        ) as raised:
            list_ask_history_for_api(
                "private-user"
            )

        self.assertEqual(
            raised.exception.status_code,
            503,
        )

        self.assertFalse(
            raised.exception.payload[
                "retryable"
            ]
        )

        self.assertNotIn(
            "retryAfterSeconds",
            raised.exception.payload,
        )

    @patch(
        "ask_history_api.get_ask_history"
    )
    def test_failure_logs_exclude_private_values(
        self,
        get_history,
    ):
        get_history.side_effect = (
            AskHistoryStoreError(
                "ThrottlingException",
                "private provider detail",
                retryable=True,
            )
        )

        output = io.StringIO()

        with redirect_stdout(output):
            with self.assertRaises(
                AskHistoryApiError
            ):
                get_ask_history_for_api(
                    "private-user",
                    HISTORY_ID,
                )

        logs = output.getvalue()

        for forbidden in (
            "private-user",
            HISTORY_ID,
            "private provider detail",
            "rawText",
            "cleanText",
        ):
            self.assertNotIn(
                forbidden,
                logs,
            )


class AskHistoryApiRouteTests(
    unittest.TestCase
):
    @patch(
        "app.list_ask_history_for_api"
    )
    def test_list_is_user_scoped_and_paginated(
        self,
        list_history,
    ):
        list_history.return_value = {
            "count": 1,
            "items": [
                history_summary()
            ],
            "nextCursor": (
                "opaque-next-cursor"
            ),
        }

        result = lambda_handler(
            api_event(
                "GET",
                "/insights/ask/history",
                query={
                    "limit": "10",
                    "cursor": (
                        "opaque-current-cursor"
                    ),
                },
            ),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            200,
        )

        list_history.assert_called_once_with(
            "first-user",
            limit="10",
            cursor=(
                "opaque-current-cursor"
            ),
        )

        self.assertEqual(
            body["count"],
            1,
        )

        self.assertEqual(
            body["history"][0][
                "historyId"
            ],
            HISTORY_ID,
        )

        self.assertEqual(
            body["nextCursor"],
            "opaque-next-cursor",
        )

    @patch(
        "app.list_ask_history_for_api"
    )
    def test_invalid_list_input_returns_400(
        self,
        list_history,
    ):
        list_history.side_effect = (
            AskHistoryApiError(
                400,
                {
                    "error": (
                        "InvalidAskHistoryCursor"
                    ),
                    "message": (
                        "The history cursor "
                        "is invalid."
                    ),
                    "retryable": False,
                },
            )
        )

        result = lambda_handler(
            api_event(
                "GET",
                "/insights/ask/history",
                query={
                    "cursor": "invalid",
                },
            ),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            400,
        )

        self.assertEqual(
            body["error"],
            "InvalidAskHistoryCursor",
        )

    @patch(
        "app.get_ask_history_for_api"
    )
    def test_detail_is_user_scoped(
        self,
        get_history,
    ):
        get_history.return_value = (
            history_detail()
        )

        result = lambda_handler(
            api_event(
                "GET",
                (
                    "/insights/ask/history/"
                    + HISTORY_ID
                ),
                user_id="second-user",
            ),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            200,
        )

        get_history.assert_called_once_with(
            "second-user",
            HISTORY_ID,
        )

        self.assertEqual(
            body["history"][
                "historyId"
            ],
            HISTORY_ID,
        )

    @patch(
        "app.get_ask_history_for_api",
        return_value=None,
    )
    def test_missing_detail_returns_404(
        self,
        get_history,
    ):
        result = lambda_handler(
            api_event(
                "GET",
                (
                    "/insights/ask/history/"
                    + HISTORY_ID
                ),
            ),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            404,
        )

        self.assertEqual(
            body["error"],
            "AskHistoryNotFound",
        )

    @patch(
        "app.get_ask_history_for_api"
    )
    def test_malformed_history_path_returns_400(
        self,
        get_history,
    ):
        result = lambda_handler(
            api_event(
                "GET",
                (
                    "/insights/ask/history/"
                    "invalid/nested"
                ),
            ),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            400,
        )

        self.assertEqual(
            body["error"],
            "InvalidAskHistoryId",
        )

        get_history.assert_not_called()

    @patch(
        "app.delete_ask_history_for_api",
        return_value=True,
    )
    def test_delete_is_user_scoped(
        self,
        delete_history,
    ):
        result = lambda_handler(
            api_event(
                "DELETE",
                (
                    "/insights/ask/history/"
                    + HISTORY_ID
                ),
            ),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            200,
        )

        self.assertTrue(
            body["deleted"]
        )

        delete_history.assert_called_once_with(
            "first-user",
            HISTORY_ID,
        )

    @patch(
        "app.delete_ask_history_for_api",
        return_value=False,
    )
    def test_missing_delete_returns_404(
        self,
        delete_history,
    ):
        result = lambda_handler(
            api_event(
                "DELETE",
                (
                    "/insights/ask/history/"
                    + HISTORY_ID
                ),
            ),
            None,
        )

        body = json.loads(
            result["body"]
        )

        self.assertEqual(
            result["statusCode"],
            404,
        )

        self.assertEqual(
            body["error"],
            "AskHistoryNotFound",
        )

    @patch(
        "app.delete_ask_history_for_api"
    )
    def test_delete_failure_returns_safe_503(
        self,
        delete_history,
    ):
        delete_history.side_effect = (
            AskHistoryApiError(
                503,
                {
                    "error": (
                        "AskHistoryUnavailable"
                    ),
                    "message": (
                        "JM8 could not access "
                        "your private Ask history "
                        "right now."
                    ),
                    "retryable": True,
                    "retryAfterSeconds": 2,
                },
            )
        )

        result = lambda_handler(
            api_event(
                "DELETE",
                (
                    "/insights/ask/history/"
                    + HISTORY_ID
                ),
            ),
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
            "AskHistoryUnavailable",
        )

        self.assertNotIn(
            "history",
            body,
        )

    @patch(
        "app.get_ask_history_for_api"
    )
    def test_detail_response_excludes_storage_keys(
        self,
        get_history,
    ):
        get_history.return_value = (
            history_detail()
        )

        result = lambda_handler(
            api_event(
                "GET",
                (
                    "/insights/ask/history/"
                    + HISTORY_ID
                ),
            ),
            None,
        )

        serialized = result["body"]

        for forbidden in (
            '"PK"',
            '"SK"',
            "GSI1PK",
            "GSI1SK",
            "userId",
            "rawText",
            "cleanText",
        ):
            self.assertNotIn(
                forbidden,
                serialized,
            )

    def test_deployment_scripts_include_secured_routes(
        self,
    ):
        create_api = Path(
            "bin/create-api"
        ).read_text()

        secure_api = Path(
            "bin/secure-api"
        ).read_text()

        routes = (
            (
                "GET "
                "/insights/ask/history"
            ),
            (
                "GET "
                "/insights/ask/history/"
                "{historyId}"
            ),
            (
                "DELETE "
                "/insights/ask/history/"
                "{historyId}"
            ),
        )

        for route in routes:
            with self.subTest(
                route=route
            ):
                self.assertIn(
                    (
                        "create_route_if_missing "
                        f'"{route}"'
                    ),
                    create_api,
                )

                self.assertIn(
                    (
                        "secure_route "
                        f'"{route}"'
                    ),
                    secure_api,
                )


if __name__ == "__main__":
    unittest.main()
