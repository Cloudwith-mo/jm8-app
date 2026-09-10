import json
import unittest
from datetime import (
    datetime,
    timezone,
)

from botocore.exceptions import (
    ClientError,
)

from insights_ask_answer import (
    AskAnswerInputError,
    AskAnswerInvocationError,
    AskAnswerResponseError,
    answer_journal_history,
)
from insights_ask_context import (
    build_ask_context,
)


FIXED_NOW = datetime(
    2026,
    7,
    22,
    12,
    0,
    tzinfo=timezone.utc,
)


VALID_PAYLOAD = {
    "status": "ANSWERED",
    "headline": (
        "Consistency was the "
        "strongest recurring challenge."
    ),
    "summary": (
        "The analyzed history repeatedly "
        "connected discipline with "
        "follow-through."
    ),
    "explanation": (
        "The pattern appeared across "
        "multiple periods and became less "
        "dominant as routines improved."
    ),
    "primarySignal": {
        "category": "challenges",
        "value": (
            "Maintaining consistency"
        ),
    },
    "strongestPeriod": "2026-02",
    "improvementPercent": 64,
    "topTrigger": (
        "Losing structure during "
        "busy periods"
    ),
    "evidence": [
        {
            "paraphrase": (
                "One analyzed entry linked "
                "execution problems to an "
                "unstable routine."
            ),
            "date": "2026-01-10",
            "sourceType": "typed",
            "relevance": "high",
        },
        {
            "paraphrase": (
                "A later scanned entry "
                "described stronger "
                "follow-through after "
                "protecting a routine."
            ),
            "date": "2026-02-10",
            "sourceType": "image",
            "relevance": "high",
        },
    ],
    "takeaways": [
        (
            "Consistency improved when "
            "daily structure was protected."
        ),
        (
            "Reviewing progress supported "
            "better follow-through."
        ),
    ],
    "relatedThemes": [
        "discipline",
        "health",
    ],
    "growthSignals": [
        "Returned to the plan",
        "Followed the routine",
    ],
    "limitations": [],
    "suggestedFollowUps": [
        (
            "Which goals keep "
            "returning?"
        ),
        (
            "How has my mindset "
            "changed?"
        ),
    ],
}


def analyzed_entry(
    *,
    created_at: str,
    source_type: str,
    growth_signal: str,
    behavior_pattern: str,
) -> dict:
    return {
        "createdAt": created_at,
        "sourceType": source_type,
        "analysisStatus": (
            "COMPLETED"
        ),
        "analysis": {
            "status": "ANALYZED",
            "mood": "determined",
            "sentiment": (
                "mixed_positive"
            ),
            "secondaryMoods": [
                "reflective",
            ],
            "themes": [
                "discipline",
                "health",
            ],
            "emergingTopics": [],
            "keyInsights": [
                (
                    "Structure supports "
                    "execution"
                ),
            ],
            "challenges": [
                (
                    "Maintaining "
                    "consistency"
                ),
            ],
            "goals": [
                (
                    "Build a reliable "
                    "routine"
                ),
            ],
            "growthSignals": [
                growth_signal,
            ],
            "behaviorPatterns": [
                behavior_pattern,
            ],
            "peopleAndTopics": [],
            "nextStep": (
                "Protect one focused "
                "work period"
            ),
        },
    }


def build_context() -> dict:
    return build_ask_context(
        [
            analyzed_entry(
                created_at=(
                    "2026-01-10T10:00:00"
                    "+00:00"
                ),
                source_type="typed",
                growth_signal=(
                    "Returned to the plan"
                ),
                behavior_pattern=(
                    "Plans before acting"
                ),
            ),
            analyzed_entry(
                created_at=(
                    "2026-02-10T10:00:00"
                    "+00:00"
                ),
                source_type="image",
                growth_signal=(
                    "Followed the routine"
                ),
                behavior_pattern=(
                    "Reviews progress"
                ),
            ),
        ],
        question=(
            "What challenge keeps "
            "returning?"
        ),
        start_date="2026-01-01",
        end_date="2026-02-28",
        now=FIXED_NOW,
    )


def add_semantic_evidence(
    context: dict,
    *,
    excerpt: str = "I kept returning to the same difficult routine.",
    evidence_date: str = "2026-01-10",
) -> dict:
    return {
        **context,
        "semanticEvidence": {
            "semanticContextVersion": "1.0",
            "retrievalVersion": "1.0",
            "status": "READY",
            "retrievedEvidence": 1,
            "includedEvidence": 1,
            "scopeExcludedEvidence": 0,
            "invalidExcludedEvidence": 0,
            "duplicateExcludedEvidence": 0,
            "limitExcludedEvidence": 0,
            "contextTruncated": False,
            "items": [{
                "date": evidence_date,
                "sourceType": "typed",
                "distance": 0.125,
                "excerpt": excerpt,
            }],
        },
    }


class FakeBedrockClient:
    def __init__(
        self,
        payload=None,
    ):
        self.payload = (
            payload
            if payload is not None
            else VALID_PAYLOAD
        )

        self.last_request = None

    def converse(
        self,
        **kwargs,
    ):
        self.last_request = kwargs

        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": (
                                json.dumps(
                                    self.payload
                                )
                            ),
                        },
                    ],
                },
            },
            "usage": {
                "inputTokens": 900,
                "outputTokens": 600,
                "totalTokens": 1_500,
            },
            "metrics": {
                "latencyMs": 1_200,
            },
            "stopReason": "end_turn",
            "ResponseMetadata": {
                "RetryAttempts": 0,
            },
        }


class NoCallClient:
    def converse(
        self,
        **kwargs,
    ):
        raise AssertionError(
            (
                "Bedrock should not "
                "have been called."
            )
        )


class ErrorBedrockClient:
    def __init__(
        self,
        error_code: str,
        retry_attempts: int,
    ):
        self.error_code = error_code

        self.retry_attempts = (
            retry_attempts
        )

    def converse(
        self,
        **kwargs,
    ):
        raise ClientError(
            {
                "Error": {
                    "Code": (
                        self.error_code
                    ),
                    "Message": (
                        "Synthetic private "
                        "provider details."
                    ),
                },
                "ResponseMetadata": {
                    "RetryAttempts": (
                        self.retry_attempts
                    ),
                },
            },
            "Converse",
        )


class MissingContentClient:
    def converse(
        self,
        **kwargs,
    ):
        return {
            "output": {
                "message": {
                    "content": [],
                },
            },
        }


class InvalidJsonClient:
    def converse(
        self,
        **kwargs,
    ):
        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": (
                                "{not-json"
                            ),
                        },
                    ],
                },
            },
        }


class AskAnswerTests(
    unittest.TestCase
):
    def test_empty_context_skips_bedrock(
        self,
    ):
        context = build_ask_context(
            [],
            question=(
                "What challenge keeps "
                "returning?"
            ),
            now=FIXED_NOW,
        )

        result = (
            answer_journal_history(
                context,
                client=NoCallClient(),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            result["status"],
            "INSUFFICIENT_CONTEXT",
        )

        self.assertEqual(
            result["metrics"][
                "mentionCount"
            ],
            0,
        )

        self.assertEqual(
            result["evidence"],
            [],
        )

    def test_valid_answer_is_normalized(
        self,
    ):
        result = (
            answer_journal_history(
                build_context(),
                client=(
                    FakeBedrockClient()
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            result["status"],
            "ANSWERED",
        )

        self.assertEqual(
            result["answerVersion"],
            "1.0",
        )

        self.assertEqual(
            result["metrics"][
                "mentionCount"
            ],
            2,
        )

        self.assertEqual(
            result["metrics"][
                "strongestPeriod"
            ],
            "2026-02",
        )

        self.assertEqual(
            len(result["evidence"]),
            2,
        )

        self.assertEqual(
            result["relatedThemes"],
            [
                "discipline",
                "health",
            ],
        )

    def test_semantic_evidence_can_answer_without_structured_analysis(self):
        context = build_ask_context(
            [],
            question="What challenge keeps returning?",
            now=FIXED_NOW,
        )
        context = add_semantic_evidence(context)
        payload = {
            **VALID_PAYLOAD,
            "evidence": [{
                "paraphrase": "A journal passage described returning to a difficult routine.",
                "date": "2026-01-10",
                "sourceType": "typed",
                "relevance": "high",
            }],
        }
        client = FakeBedrockClient(payload)

        result = answer_journal_history(
            context,
            client=client,
            now=FIXED_NOW,
        )

        self.assertIsNotNone(client.last_request)
        self.assertEqual(result["status"], "ANSWERED")
        self.assertEqual(len(result["evidence"]), 1)

    def test_semantic_excerpt_is_bounded_to_model_context(self):
        private_excerpt = "A private but relevant journal passage."
        client = FakeBedrockClient()

        answer_journal_history(
            add_semantic_evidence(
                build_context(),
                excerpt=private_excerpt,
            ),
            client=client,
            now=FIXED_NOW,
        )

        user_text = client.last_request["messages"][0]["content"][0]["text"]
        self.assertIn(private_excerpt, user_text)
        self.assertIn('"semanticEvidence"', user_text)
        self.assertNotIn("entryId", user_text)
        self.assertNotIn("queryEmbedding", user_text)

    def test_request_uses_structured_schema(
        self,
    ):
        client = FakeBedrockClient()

        answer_journal_history(
            build_context(),
            client=client,
            now=FIXED_NOW,
        )

        request = (
            client.last_request
        )

        self.assertEqual(
            request[
                "requestMetadata"
            ]["purpose"],
            "ask-jm8-history",
        )

        schema = request[
            "outputConfig"
        ]["textFormat"][
            "structure"
        ]["jsonSchema"]

        self.assertEqual(
            schema["name"],
            "jm8_history_answer_v2",
        )

        user_text = request[
            "messages"
        ][0]["content"][0]["text"]

        for private_field in [
            "rawText",
            "cleanText",
            "entryId",
            "userId",
            "s3RawKey",
        ]:
            with self.subTest(
                private_field=(
                    private_field
                )
            ):
                self.assertNotIn(
                    private_field,
                    user_text,
                )

    def test_duplicate_lists_are_removed(
        self,
    ):
        payload = {
            **VALID_PAYLOAD,
            "takeaways": [
                "Protect the routine",
                "protect the routine",
                "Protect the routine",
            ],
            "suggestedFollowUps": [
                "What changed?",
                "what changed?",
            ],
            "relatedThemes": [
                "discipline",
                "Discipline",
            ],
        }

        result = (
            answer_journal_history(
                build_context(),
                client=(
                    FakeBedrockClient(
                        payload
                    )
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            result["takeaways"],
            [
                "Protect the routine",
            ],
        )

        self.assertEqual(
            result[
                "suggestedFollowUps"
            ],
            [
                "What changed?",
            ],
        )

        self.assertEqual(
            result["relatedThemes"],
            [
                "discipline",
            ],
        )

    def test_percentages_are_bounded(
        self,
    ):
        payload = {
            **VALID_PAYLOAD,
            "improvementPercent": 160,
        }

        result = (
            answer_journal_history(
                build_context(),
                client=(
                    FakeBedrockClient(
                        payload
                    )
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            result["metrics"][
                "improvementPercent"
            ],
            100,
        )

    def test_invalid_status_is_rejected(
        self,
    ):
        payload = {
            **VALID_PAYLOAD,
            "status": "CERTAIN",
        }

        with self.assertRaises(
            AskAnswerResponseError
        ):
            answer_journal_history(
                build_context(),
                client=(
                    FakeBedrockClient(
                        payload
                    )
                ),
                now=FIXED_NOW,
            )

    def test_missing_content_is_rejected(
        self,
    ):
        with self.assertRaises(
            AskAnswerResponseError
        ):
            answer_journal_history(
                build_context(),
                client=(
                    MissingContentClient()
                ),
                now=FIXED_NOW,
            )

    def test_invalid_json_is_rejected(
        self,
    ):
        with self.assertRaises(
            AskAnswerResponseError
        ):
            answer_journal_history(
                build_context(),
                client=(
                    InvalidJsonClient()
                ),
                now=FIXED_NOW,
            )

    def test_retryable_error_is_classified(
        self,
    ):
        with self.assertRaises(
            AskAnswerInvocationError
        ) as raised:
            answer_journal_history(
                build_context(),
                client=(
                    ErrorBedrockClient(
                        "ThrottlingException",
                        1,
                    )
                ),
                now=FIXED_NOW,
            )

        error = raised.exception

        self.assertEqual(
            error.error_code,
            "ThrottlingException",
        )

        self.assertTrue(
            error.retryable
        )

        self.assertEqual(
            error.retry_attempts,
            1,
        )

        self.assertNotIn(
            "Synthetic private",
            str(error),
        )

    def test_nonretryable_error_is_classified(
        self,
    ):
        with self.assertRaises(
            AskAnswerInvocationError
        ) as raised:
            answer_journal_history(
                build_context(),
                client=(
                    ErrorBedrockClient(
                        "ValidationException",
                        0,
                    )
                ),
                now=FIXED_NOW,
            )

        error = raised.exception

        self.assertEqual(
            error.error_code,
            "ValidationException",
        )

        self.assertFalse(
            error.retryable
        )

    def test_evidence_must_match_source_context(
        self,
    ):
        payload = {
            **VALID_PAYLOAD,
            "evidence": [
                {
                    "paraphrase": (
                        "A grounded item."
                    ),
                    "date": "2026-01-10",
                    "sourceType": "typed",
                    "relevance": "high",
                },
                {
                    "paraphrase": (
                        "An invented source."
                    ),
                    "date": "2024-01-01",
                    "sourceType": "typed",
                    "relevance": "high",
                },
            ],
        }

        result = (
            answer_journal_history(
                build_context(),
                client=(
                    FakeBedrockClient(
                        payload
                    )
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            len(result["evidence"]),
            1,
        )

        self.assertEqual(
            result["evidence"][0][
                "date"
            ],
            "2026-01-10",
        )

    def test_invented_related_theme_is_removed(
        self,
    ):
        payload = {
            **VALID_PAYLOAD,
            "relatedThemes": [
                "discipline",
                "invented_theme",
            ],
        }

        result = (
            answer_journal_history(
                build_context(),
                client=(
                    FakeBedrockClient(
                        payload
                    )
                ),
                now=FIXED_NOW,
            )
        )

        self.assertEqual(
            result["relatedThemes"],
            [
                "discipline",
            ],
        )

    def test_private_context_keys_are_rejected(
        self,
    ):
        context = build_context()

        context["rawText"] = (
            "private transcript"
        )

        with self.assertRaises(
            AskAnswerInputError
        ) as raised:
            answer_journal_history(
                context,
                client=NoCallClient(),
                now=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.code,
            "UnsafeContext",
        )

    def test_missing_question_is_rejected(
        self,
    ):
        context = build_context()

        context.pop("question")

        with self.assertRaises(
            AskAnswerInputError
        ) as raised:
            answer_journal_history(
                context,
                client=NoCallClient(),
                now=FIXED_NOW,
            )

        self.assertEqual(
            raised.exception.code,
            "InvalidContext",
        )

    def test_partial_and_truncated_limitations_are_added(
        self,
    ):
        context = build_context()

        context[
            "contextStatus"
        ] = "PARTIAL"

        context["coverage"][
            "contextTruncated"
        ] = True

        result = (
            answer_journal_history(
                context,
                client=(
                    FakeBedrockClient()
                ),
                now=FIXED_NOW,
            )
        )

        serialized = " ".join(
            result["limitations"]
        ).casefold()

        self.assertIn(
            "have not been analyzed",
            serialized,
        )

        self.assertIn(
            "highest-relevance",
            serialized,
        )

    def test_public_output_excludes_provider_metadata(
        self,
    ):
        result = (
            answer_journal_history(
                build_context(),
                client=(
                    FakeBedrockClient()
                ),
                now=FIXED_NOW,
            )
        )

        serialized = json.dumps(
            result
        )

        forbidden = [
            "modelId",
            "provider",
            "amazon-bedrock",
            "usage",
            "inputTokens",
            "outputTokens",
            "totalTokens",
            "rawText",
            "cleanText",
            "entryId",
            "userId",
            "s3RawKey",
            "executionArn",
        ]

        for value in forbidden:
            with self.subTest(
                value=value
            ):
                self.assertNotIn(
                    value,
                    serialized,
                )


if __name__ == "__main__":
    unittest.main()
