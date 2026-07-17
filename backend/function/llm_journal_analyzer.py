import json
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


DEFAULT_MODEL_ID = (
    "us.anthropic."
    "claude-haiku-4-5-20251001-v1:0"
)

DEFAULT_SCHEMA_VERSION = "2.0"
DEFAULT_PROMPT_VERSION = "journal-analysis-v1"

MAX_JOURNAL_CHARACTERS = 24_000


SENTIMENT_VALUES = [
    "very_negative",
    "negative",
    "mixed_negative",
    "neutral",
    "mixed_positive",
    "positive",
    "very_positive",
]


MOOD_VALUES = [
    "reflective",
    "encouraged",
    "hopeful",
    "grateful",
    "calm",
    "determined",
    "confident",
    "energized",
    "content",
    "heavy",
    "anxious",
    "frustrated",
    "sad",
    "angry",
    "overwhelmed",
    "conflicted",
    "uncertain",
    "numb",
]


THEME_VALUES = [
    "identity",
    "self_image",
    "discipline",
    "career",
    "work",
    "entrepreneurship",
    "finances",
    "faith",
    "relationships",
    "family",
    "health",
    "fitness",
    "learning",
    "creativity",
    "purpose",
    "stress",
    "confidence",
    "grief",
    "gratitude",
    "habits",
    "time_management",
    "leadership",
    "resilience",
    "general_reflection",
]


ANALYSIS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["ANALYZED"],
        },
        "sentiment": {
            "type": "string",
            "enum": SENTIMENT_VALUES,
        },
        "sentimentConfidence": {
            "type": "integer",
        },
        "mood": {
            "type": "string",
            "enum": MOOD_VALUES,
        },
        "secondaryMoods": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": MOOD_VALUES,
            },
        },
        "moodIntensity": {
            "type": "integer",
        },
        "themes": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": THEME_VALUES,
            },
        },
        "emergingTopics": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "summary": {
            "type": "string",
        },
        "keyInsights": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "challenges": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "goals": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "growthSignals": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "behaviorPatterns": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "peopleAndTopics": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "writingStyle": {
            "type": "object",
            "properties": {
                "tone": {
                    "type": "string",
                },
                "cadence": {
                    "type": "string",
                },
                "perspective": {
                    "type": "string",
                    "enum": [
                        "first_person",
                        "second_person",
                        "third_person",
                        "mixed",
                    ],
                },
                "notableDevices": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
                "strengths": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
                "improvements": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
            },
            "required": [
                "tone",
                "cadence",
                "perspective",
                "notableDevices",
                "strengths",
                "improvements",
            ],
            "additionalProperties": False,
        },
        "reflectionPrompt": {
            "type": "string",
        },
        "nextStep": {
            "type": "string",
        },
    },
    "required": [
        "status",
        "sentiment",
        "sentimentConfidence",
        "mood",
        "secondaryMoods",
        "moodIntensity",
        "themes",
        "emergingTopics",
        "summary",
        "keyInsights",
        "challenges",
        "goals",
        "growthSignals",
        "behaviorPatterns",
        "peopleAndTopics",
        "writingStyle",
        "reflectionPrompt",
        "nextStep",
    ],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """
You are JM8, a private journal-analysis engine.

Analyze only the supplied journal entry. Produce a thoughtful,
grounded, non-clinical reflection.

Rules:

1. Do not diagnose the writer or assign mental-health conditions.
2. Do not invent events, relationships, motives, goals, or people.
3. Distinguish the writer's current feelings from emotions describing
   past events or other people.
4. Understand negation and change over time. For example, "I am not
   anxious anymore" is not evidence of current anxiety.
5. Allow mixed sentiment and conflicting emotions.
6. Identify challenges and goals only when supported by the entry.
7. Identify growth signals only when the entry shows progress,
   learning, increased awareness, or behavior change.
8. Behavior patterns must describe patterns visible in the entry,
   not permanent personality judgments.
9. Use concise paraphrases rather than long quotations.
10. Use canonical themes from the supplied schema.
11. Put specific subjects that do not fit a canonical theme into
    emergingTopics.
12. Keep the reflection useful, respectful, and action-oriented.
13. The reflection prompt should invite deeper thinking without
    assuming that your interpretation is certain.
14. The next step must be small, concrete, and realistic.
""".strip()


class AnalyzerError(RuntimeError):
    """Base exception for safe LLM analyzer failures."""


class AnalyzerInputError(AnalyzerError):
    """Raised when journal text cannot be analyzed."""


class AnalyzerInvocationError(AnalyzerError):
    """Raised when Amazon Bedrock cannot complete the request."""


class AnalyzerResponseError(AnalyzerError):
    """Raised when Bedrock returns an unusable response."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_model_id() -> str:
    return (
        os.environ.get("BEDROCK_ANALYSIS_MODEL_ID")
        or DEFAULT_MODEL_ID
    ).strip()


def get_schema_version() -> str:
    return (
        os.environ.get("ANALYSIS_SCHEMA_VERSION")
        or DEFAULT_SCHEMA_VERSION
    ).strip()


def get_prompt_version() -> str:
    return (
        os.environ.get("ANALYSIS_PROMPT_VERSION")
        or DEFAULT_PROMPT_VERSION
    ).strip()


def create_bedrock_client():
    profile_name = (
        os.environ.get("AWS_PROFILE") or ""
    ).strip()

    region_name = (
        os.environ.get("AWS_REGION")
        or "us-east-1"
    ).strip()

    if profile_name:
        session = boto3.Session(
            profile_name=profile_name,
            region_name=region_name,
        )
    else:
        session = boto3.Session(
            region_name=region_name,
        )

    return session.client(
        "bedrock-runtime",
        config=Config(
            connect_timeout=10,
            read_timeout=120,
            retries={
                "max_attempts": 3,
                "mode": "standard",
            },
        ),
    )


def extract_response_text(
    response: dict[str, Any],
) -> str:
    content = (
        response.get("output", {})
        .get("message", {})
        .get("content", [])
    )

    for block in content:
        text = block.get("text")

        if isinstance(text, str) and text.strip():
            return text.strip()

    raise AnalyzerResponseError(
        "Bedrock returned no structured analysis."
    )


def clean_text_value(
    value: Any,
    *,
    max_characters: int,
) -> str:
    cleaned = " ".join(str(value or "").split())

    if len(cleaned) > max_characters:
        cleaned = cleaned[:max_characters].rstrip()

    return cleaned


def clean_string_list(
    value: Any,
    *,
    max_items: int,
    max_characters: int,
) -> list[str]:
    if not isinstance(value, list):
        return []

    cleaned_items: list[str] = []
    seen: set[str] = set()

    for item in value:
        cleaned = clean_text_value(
            item,
            max_characters=max_characters,
        )

        if not cleaned:
            continue

        identity = cleaned.casefold()

        if identity in seen:
            continue

        seen.add(identity)
        cleaned_items.append(cleaned)

        if len(cleaned_items) >= max_items:
            break

    return cleaned_items


def percentage_value(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0

    return max(0, min(parsed, 100))


def normalize_analysis_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    if payload.get("status") != "ANALYZED":
        raise AnalyzerResponseError(
            "Bedrock analysis status was invalid."
        )

    sentiment = str(
        payload.get("sentiment") or ""
    ).strip()

    if sentiment not in SENTIMENT_VALUES:
        raise AnalyzerResponseError(
            "Bedrock returned an unsupported sentiment."
        )

    mood = str(payload.get("mood") or "").strip()

    if mood not in MOOD_VALUES:
        raise AnalyzerResponseError(
            "Bedrock returned an unsupported mood."
        )

    themes = [
        theme
        for theme in clean_string_list(
            payload.get("themes"),
            max_items=6,
            max_characters=60,
        )
        if theme in THEME_VALUES
    ]

    if not themes:
        themes = ["general_reflection"]

    writing_style = payload.get("writingStyle")

    if not isinstance(writing_style, dict):
        raise AnalyzerResponseError(
            "Bedrock returned invalid writing-style data."
        )

    perspective = str(
        writing_style.get("perspective") or ""
    ).strip()

    if perspective not in {
        "first_person",
        "second_person",
        "third_person",
        "mixed",
    }:
        perspective = "first_person"

    return {
        "status": "ANALYZED",
        "sentiment": sentiment,
        "sentimentConfidence": percentage_value(
            payload.get("sentimentConfidence")
        ),
        "mood": mood,
        "secondaryMoods": [
            item
            for item in clean_string_list(
                payload.get("secondaryMoods"),
                max_items=3,
                max_characters=40,
            )
            if item in MOOD_VALUES and item != mood
        ],
        "moodIntensity": percentage_value(
            payload.get("moodIntensity")
        ),
        "themes": themes,
        "emergingTopics": clean_string_list(
            payload.get("emergingTopics"),
            max_items=8,
            max_characters=80,
        ),
        "summary": clean_text_value(
            payload.get("summary"),
            max_characters=1_200,
        ),
        "keyInsights": clean_string_list(
            payload.get("keyInsights"),
            max_items=6,
            max_characters=500,
        ),
        "challenges": clean_string_list(
            payload.get("challenges"),
            max_items=5,
            max_characters=400,
        ),
        "goals": clean_string_list(
            payload.get("goals"),
            max_items=5,
            max_characters=400,
        ),
        "growthSignals": clean_string_list(
            payload.get("growthSignals"),
            max_items=5,
            max_characters=400,
        ),
        "behaviorPatterns": clean_string_list(
            payload.get("behaviorPatterns"),
            max_items=5,
            max_characters=400,
        ),
        "peopleAndTopics": clean_string_list(
            payload.get("peopleAndTopics"),
            max_items=10,
            max_characters=100,
        ),
        "writingStyle": {
            "tone": clean_text_value(
                writing_style.get("tone"),
                max_characters=160,
            ),
            "cadence": clean_text_value(
                writing_style.get("cadence"),
                max_characters=160,
            ),
            "perspective": perspective,
            "notableDevices": clean_string_list(
                writing_style.get("notableDevices"),
                max_items=6,
                max_characters=100,
            ),
            "strengths": clean_string_list(
                writing_style.get("strengths"),
                max_items=4,
                max_characters=300,
            ),
            "improvements": clean_string_list(
                writing_style.get("improvements"),
                max_items=4,
                max_characters=300,
            ),
        },
        "reflectionPrompt": clean_text_value(
            payload.get("reflectionPrompt"),
            max_characters=700,
        ),
        "nextStep": clean_text_value(
            payload.get("nextStep"),
            max_characters=500,
        ),
    }


def analyze_journal_entry_llm(
    text: str,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    journal_text = str(text or "").strip()

    if not journal_text:
        raise AnalyzerInputError(
            "Journal text is required."
        )

    if len(journal_text) > MAX_JOURNAL_CHARACTERS:
        raise AnalyzerInputError(
            "Journal text exceeds the current analysis limit."
        )

    model_id = get_model_id()
    bedrock = client or create_bedrock_client()

    try:
        response = bedrock.converse(
            modelId=model_id,
            system=[
                {
                    "text": SYSTEM_PROMPT,
                },
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "Analyze the following private "
                                "journal entry:\n\n"
                                f"{journal_text}"
                            ),
                        },
                    ],
                },
            ],
            inferenceConfig={
                "maxTokens": 2_200,
                "temperature": 0.2,
            },
            outputConfig={
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "jm8_journal_analysis_v2",
                            "description": (
                                "Structured analysis of one "
                                "Journal M8 entry."
                            ),
                            "schema": json.dumps(
                                ANALYSIS_OUTPUT_SCHEMA,
                                separators=(",", ":"),
                            ),
                        },
                    },
                },
            },
            requestMetadata={
                "application": "journalm8",
                "stage": (
                    os.environ.get("STAGE")
                    or "dev"
                ),
                "purpose": "single-entry-analysis",
                "schema-version": get_schema_version(),
                "prompt-version": get_prompt_version(),
            },
        )

    except ClientError as exc:
        error = exc.response.get("Error") or {}
        error_code = str(
            error.get("Code") or "Unknown"
        )

        raise AnalyzerInvocationError(
            f"Bedrock analysis failed with {error_code}."
        ) from exc

    except BotoCoreError as exc:
        raise AnalyzerInvocationError(
            "Bedrock analysis request could not be completed."
        ) from exc

    try:
        raw_response = extract_response_text(response)
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise AnalyzerResponseError(
            "Bedrock returned invalid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise AnalyzerResponseError(
            "Bedrock analysis was not an object."
        )

    normalized = normalize_analysis_payload(payload)

    usage = response.get("usage") or {}
    metrics = response.get("metrics") or {}

    return {
        **normalized,
        "analyzerType": "LLM",
        "provider": "amazon-bedrock",
        "modelId": model_id,
        "schemaVersion": get_schema_version(),
        "promptVersion": get_prompt_version(),
        "analyzedAt": utc_now(),
        "wordCount": len(journal_text.split()),
        "usage": {
            "inputTokens": int(
                usage.get("inputTokens") or 0
            ),
            "outputTokens": int(
                usage.get("outputTokens") or 0
            ),
            "totalTokens": int(
                usage.get("totalTokens") or 0
            ),
            "latencyMs": int(
                metrics.get("latencyMs") or 0
            ),
            "stopReason": str(
                response.get("stopReason") or ""
            ),
        },
    }
