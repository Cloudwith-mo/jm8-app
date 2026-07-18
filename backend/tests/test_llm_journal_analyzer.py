import json
import unittest

from botocore.exceptions import ClientError

from llm_journal_analyzer import (
    AnalyzerInputError,
    AnalyzerInvocationError,
    AnalyzerResponseError,
    analyze_journal_entry_llm,
)


VALID_PAYLOAD = {
    "status": "ANALYZED",
    "sentiment": "mixed_positive",
    "sentimentConfidence": 92,
    "mood": "determined",
    "secondaryMoods": [
        "hopeful",
        "uncertain",
    ],
    "moodIntensity": 78,
    "themes": [
        "career",
        "discipline",
        "growth",
        "career",
    ],
    "emergingTopics": [
        "prototype launch",
    ],
    "summary": (
        "The writer feels more confident while still "
        "recognizing uncertainty about the next step."
    ),
    "keyInsights": [
        "Progress has reduced earlier anxiety.",
        "Consistency is becoming a source of confidence.",
    ],
    "challenges": [
        "Balancing ambition with adequate rest.",
    ],
    "goals": [
        "Complete a working prototype.",
    ],
    "growthSignals": [
        "The writer describes being less anxious than before.",
    ],
    "behaviorPatterns": [
        "Daily action appears to strengthen confidence.",
    ],
    "peopleAndTopics": [
        "side project",
    ],
    "writingStyle": {
        "tone": "Reflective and forward-looking",
        "cadence": "Measured with decisive closing statements",
        "perspective": "first_person",
        "notableDevices": [
            "contrast",
        ],
        "strengths": [
            "Clear connection between action and emotion",
        ],
        "improvements": [
            "Add more specific evidence of progress",
        ],
    },
    "reflectionPrompt": (
        "What evidence best shows that your confidence "
        "is becoming more stable?"
    ),
    "nextStep": (
        "Define one prototype task that can be finished "
        "in a single focused session."
    ),
}


class FakeBedrockClient:
    def __init__(self, payload=None):
        self.payload = payload or VALID_PAYLOAD
        self.last_request = None

    def converse(self, **kwargs):
        self.last_request = kwargs

        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "text": json.dumps(
                                self.payload
                            ),
                        },
                    ],
                },
            },
            "usage": {
                "inputTokens": 250,
                "outputTokens": 400,
                "totalTokens": 650,
            },
            "metrics": {
                "latencyMs": 1_500,
            },
            "stopReason": "end_turn",
            "ResponseMetadata": {
                "RetryAttempts": 1,
            },
        }


class ErrorBedrockClient:
    def __init__(
        self,
        error_code: str,
        retry_attempts: int,
    ):
        self.error_code = error_code
        self.retry_attempts = retry_attempts

    def converse(self, **kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": self.error_code,
                    "Message": "Synthetic AWS error.",
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
    def converse(self, **kwargs):
        return {
            "output": {
                "message": {
                    "content": [],
                },
            },
        }


class LlmJournalAnalyzerTests(unittest.TestCase):
    def test_structured_analysis_is_normalized(self):
        client = FakeBedrockClient()

        result = analyze_journal_entry_llm(
            "I feel more confident after working daily.",
            client=client,
        )

        self.assertEqual(
            result["analyzerType"],
            "LLM",
        )
        self.assertEqual(
            result["provider"],
            "amazon-bedrock",
        )
        self.assertEqual(
            result["schemaVersion"],
            "2.0",
        )
        self.assertEqual(
            result["sentiment"],
            "mixed_positive",
        )
        self.assertEqual(
            result["mood"],
            "determined",
        )
        self.assertEqual(
            result["usage"]["totalTokens"],
            650,
        )
        self.assertEqual(
            result["usage"]["sdkRetryAttempts"],
            1,
        )

    def test_duplicate_themes_are_removed(self):
        client = FakeBedrockClient()

        result = analyze_journal_entry_llm(
            "Synthetic entry.",
            client=client,
        )

        self.assertEqual(
            result["themes"],
            [
                "career",
                "discipline",
            ],
        )

    def test_confidence_values_are_bounded(self):
        payload = {
            **VALID_PAYLOAD,
            "sentimentConfidence": 140,
            "moodIntensity": -20,
        }

        result = analyze_journal_entry_llm(
            "Synthetic entry.",
            client=FakeBedrockClient(payload),
        )

        self.assertEqual(
            result["sentimentConfidence"],
            100,
        )
        self.assertEqual(
            result["moodIntensity"],
            0,
        )

    def test_throttling_error_is_retryable(self):
        with self.assertRaises(
            AnalyzerInvocationError
        ) as context:
            analyze_journal_entry_llm(
                "Synthetic entry.",
                client=ErrorBedrockClient(
                    "ThrottlingException",
                    1,
                ),
            )

        error = context.exception

        self.assertEqual(
            error.error_code,
            "ThrottlingException",
        )
        self.assertTrue(error.retryable)
        self.assertEqual(
            error.retry_attempts,
            1,
        )

    def test_validation_error_is_not_retryable(self):
        with self.assertRaises(
            AnalyzerInvocationError
        ) as context:
            analyze_journal_entry_llm(
                "Synthetic entry.",
                client=ErrorBedrockClient(
                    "ValidationException",
                    0,
                ),
            )

        error = context.exception

        self.assertEqual(
            error.error_code,
            "ValidationException",
        )
        self.assertFalse(error.retryable)
        self.assertEqual(
            error.retry_attempts,
            0,
        )

    def test_blank_text_is_rejected(self):
        with self.assertRaises(AnalyzerInputError):
            analyze_journal_entry_llm(
                "   ",
                client=FakeBedrockClient(),
            )

    def test_missing_model_content_is_rejected(self):
        with self.assertRaises(AnalyzerResponseError):
            analyze_journal_entry_llm(
                "Synthetic entry.",
                client=MissingContentClient(),
            )


if __name__ == "__main__":
    unittest.main()
