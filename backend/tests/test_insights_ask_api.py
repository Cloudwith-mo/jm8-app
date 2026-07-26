import json
import os
import unittest
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
from insights_ask_answer import (  # noqa: E402
    AskAnswerInputError,
    AskAnswerInvocationError,
    AskAnswerResponseError,
)


def api_event(
    body,
    *,
    encode_json: bool = True,
) -> dict:
    return {
        "requestContext": {
            "http": {
                "method": "POST",
                "path": (
                    "/insights/ask"
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
        "body": (
            json.dumps(body)
            if encode_json
            else body
        ),
    }


def analyzed_entry() -> dict:
    return {
        "entryId": "private-entry",
        "userId": "private-user",
        "rawText": (
            "private raw transcript"
        ),
        "cleanText": (
            "private clean transcript"
        ),
        "s3RawKey": "private-key",
        "createdAt": (
            "2026-01-10T10:00:00"
            "+00:00"
        ),
        "sourceType": "typed",
        "analysisStatus": (
            "COMPLETED"
        ),
        "analysis": {
            "status": "ANALYZED",
            "mood": "determined",
            "sentiment": (
                "mixed_positive"
            ),
            "themes": [
                "discipline",
            ],
            "challenges": [
                (
                    "Maintaining "
                    "consistency"
                ),
            ],
            "goals": [
                "Build a routine",
            ],
            "growthSignals": [
                "Returned to the plan",
            ],
            "behaviorPatterns": [
                "Plans before acting",
            ],
        },
    }


class AskJm8ApiTests(
    unittest.TestCase
):
    @patch(
        "app.complete_ask_usage"
    )
    @patch(
        "app.persist_ask_history"
    )
    @patch(
        "app.reserve_ask_usage"
    )
    @patch(
        "app.answer_journal_history"
    )
    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_api_is_user_scoped_and_private(
        self,
        list_entries,
        answer_history,
        reserve_usage,
        persist_history,
        complete_usage,
    ):
        list_entries.return_value = [
            analyzed_entry()
        ]

        answer_history.return_value = {
            "status": "ANSWERED",
        }

        persist_history.return_value = {
            "historyVersion": "1.0",
            "historyId": (
                "askhist_api123456789012"
            ),
            "createdAt": (
                "2026-07-25"
                "T22:00:00+00:00"
            ),
        }

        response = lambda_handler(
            api_event({
                "question": (
                    "What challenge keeps "
                    "returning?"
                ),
                "startDate": (
                    "2026-01-01"
                ),
                "endDate": (
                    "2026-01-31"
                ),
            }),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )

        list_entries.assert_called_once_with(
            user_id="private-user",
        )

        context = (
            answer_history
            .call_args.args[0]
        )

        self.assertEqual(
            context["question"],
            (
                "What challenge keeps "
                "returning?"
            ),
        )

        self.assertEqual(
            context["scope"][
                "startDate"
            ],
            "2026-01-01",
        )

        self.assertEqual(
            context["scope"][
                "endDate"
            ],
            "2026-01-31",
        )

        context_json = json.dumps(
            context
        )

        for value in [
            "entryId",
            "userId",
            "rawText",
            "cleanText",
            "s3RawKey",
            "private-entry",
            "private-user",
            "private raw transcript",
            "private clean transcript",
            "private-key",
        ]:
            with self.subTest(
                value=value
            ):
                self.assertNotIn(
                    value,
                    context_json,
                )

        self.assertEqual(
            body["answer"][
                "status"
            ],
            "ANSWERED",
        )

        reserve_usage.assert_called_once()

        persist_history.assert_called_once_with(
            "private-user",
            answer_history.return_value,
        )

        complete_usage.assert_called_once()

        self.assertEqual(
            body["history"]["historyId"],
            "askhist_api123456789012",
        )

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_invalid_json_returns_400(
        self,
        list_entries,
    ):
        response = lambda_handler(
            api_event(
                "{not-json",
                encode_json=False,
            ),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            400,
        )

        self.assertEqual(
            body["error"],
            "InvalidRequestBody",
        )

        list_entries.assert_not_called()

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_nonobject_body_returns_400(
        self,
        list_entries,
    ):
        response = lambda_handler(
            api_event([]),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            400,
        )

        self.assertEqual(
            body["error"],
            "InvalidRequestBody",
        )

        list_entries.assert_not_called()

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_invalid_question_returns_400(
        self,
        list_entries,
    ):
        list_entries.return_value = []

        response = lambda_handler(
            api_event({
                "question": "?",
            }),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            400,
        )

        self.assertEqual(
            body["error"],
            "InvalidQuestion",
        )

    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_future_date_returns_400(
        self,
        list_entries,
    ):
        list_entries.return_value = []

        response = lambda_handler(
            api_event({
                "question": (
                    "What changed?"
                ),
                "endDate": (
                    "2999-01-01"
                ),
            }),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            400,
        )

        self.assertEqual(
            body["error"],
            "FutureDateRange",
        )

    @patch(
        "app.answer_journal_history"
    )
    @patch(
        "app.build_ask_context"
    )
    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_answer_input_error_returns_400(
        self,
        list_entries,
        build_context,
        answer_history,
    ):
        list_entries.return_value = []

        build_context.return_value = {
            "question": (
                "What changed?"
            ),
        }

        answer_history.side_effect = (
            AskAnswerInputError(
                "InvalidContext",
                (
                    "The supplied context "
                    "was invalid."
                ),
            )
        )

        response = lambda_handler(
            api_event({
                "question": (
                    "What changed?"
                ),
            }),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            400,
        )

        self.assertEqual(
            body["error"],
            "InvalidContext",
        )

    @patch(
        "app.answer_journal_history"
    )
    @patch(
        "app.build_ask_context"
    )
    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_retryable_failure_returns_503(
        self,
        list_entries,
        build_context,
        answer_history,
    ):
        list_entries.return_value = []

        build_context.return_value = {
            "question": (
                "What changed?"
            ),
        }

        answer_history.side_effect = (
            AskAnswerInvocationError(
                (
                    "Private provider "
                    "failure detail."
                ),
                error_code=(
                    "ThrottlingException"
                ),
                retryable=True,
                retry_attempts=2,
            )
        )

        response = lambda_handler(
            api_event({
                "question": (
                    "What changed?"
                ),
            }),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            503,
        )

        self.assertEqual(
            body["error"],
            "AskJM8Unavailable",
        )

        self.assertTrue(
            body["retryable"]
        )

        self.assertEqual(
            body["retryAfterSeconds"],
            2,
        )

        serialized = response["body"]

        self.assertNotIn(
            "ThrottlingException",
            serialized,
        )

        self.assertNotIn(
            "Private provider",
            serialized,
        )

    @patch(
        "app.answer_journal_history"
    )
    @patch(
        "app.build_ask_context"
    )
    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_nonretryable_failure_returns_502(
        self,
        list_entries,
        build_context,
        answer_history,
    ):
        list_entries.return_value = []

        build_context.return_value = {
            "question": (
                "What changed?"
            ),
        }

        answer_history.side_effect = (
            AskAnswerInvocationError(
                "Private provider detail.",
                error_code=(
                    "ValidationException"
                ),
                retryable=False,
                retry_attempts=0,
            )
        )

        response = lambda_handler(
            api_event({
                "question": (
                    "What changed?"
                ),
            }),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            502,
        )

        self.assertFalse(
            body["retryable"]
        )

        self.assertNotIn(
            "retryAfterSeconds",
            body,
        )

        self.assertNotIn(
            "ValidationException",
            response["body"],
        )

    @patch(
        "app.answer_journal_history"
    )
    @patch(
        "app.build_ask_context"
    )
    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_invalid_model_response_returns_502(
        self,
        list_entries,
        build_context,
        answer_history,
    ):
        list_entries.return_value = []

        build_context.return_value = {
            "question": (
                "What changed?"
            ),
        }

        answer_history.side_effect = (
            AskAnswerResponseError(
                (
                    "Private invalid model "
                    "response detail."
                )
            )
        )

        response = lambda_handler(
            api_event({
                "question": (
                    "What changed?"
                ),
            }),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            502,
        )

        self.assertEqual(
            body["error"],
            "AskJM8InvalidResponse",
        )

        self.assertFalse(
            body["retryable"]
        )

        self.assertNotIn(
            "Private invalid",
            response["body"],
        )

    @patch(
        "app.persist_ask_history"
    )
    @patch(
        "insights_ask_answer."
        "create_bedrock_client"
    )
    @patch(
        "app."
        "list_insights_overview_entries"
    )
    def test_empty_history_skips_bedrock(
        self,
        list_entries,
        create_client,
        persist_history,
    ):
        list_entries.return_value = []

        persist_history.return_value = {
            "historyVersion": "1.0",
            "historyId": (
                "askhist_empty1234567890"
            ),
            "createdAt": (
                "2026-07-25"
                "T22:00:00+00:00"
            ),
        }

        response = lambda_handler(
            api_event({
                "question": (
                    "What challenge keeps "
                    "returning?"
                ),
            }),
            None,
        )

        body = json.loads(
            response["body"]
        )

        self.assertEqual(
            response["statusCode"],
            200,
        )

        self.assertEqual(
            body["answer"]["status"],
            "INSUFFICIENT_CONTEXT",
        )

        create_client.assert_not_called()

    def test_deployment_scripts_include_secured_route(
        self,
    ):
        create_api = Path(
            "bin/create-api"
        ).read_text()

        secure_api = Path(
            "bin/secure-api"
        ).read_text()

        route = (
            '"POST /insights/ask"'
        )

        self.assertIn(
            route,
            create_api,
        )

        self.assertIn(
            route,
            secure_api,
        )


if __name__ == "__main__":
    unittest.main()
