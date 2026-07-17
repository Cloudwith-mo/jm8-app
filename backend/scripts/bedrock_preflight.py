import json
import os
import sys
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


DEFAULT_MODEL_ID = (
    "us.anthropic."
    "claude-haiku-4-5-20251001-v1:0"
)


PREFLIGHT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["ready"],
        },
        "message": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
        },
    },
    "required": [
        "status",
        "message",
    ],
    "additionalProperties": False,
}


def build_client():
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
            read_timeout=300,
            retries={
                "max_attempts": 3,
                "mode": "standard",
            },
        ),
    )


def extract_text(response: dict[str, Any]) -> str:
    content = (
        response.get("output", {})
        .get("message", {})
        .get("content", [])
    )

    for block in content:
        text = block.get("text")

        if isinstance(text, str) and text.strip():
            return text.strip()

    raise ValueError(
        "Bedrock returned no structured text content."
    )


def main() -> int:
    model_id = (
        os.environ.get("BEDROCK_ANALYSIS_MODEL_ID")
        or DEFAULT_MODEL_ID
    ).strip()

    stage = (
        os.environ.get("STAGE")
        or "dev"
    ).strip()

    client = build_client()

    try:
        response = client.converse(
            modelId=model_id,
            system=[
                {
                    "text": (
                        "You are performing a connection and "
                        "structured-output test. Follow the supplied "
                        "JSON schema exactly. Do not include markdown."
                    ),
                },
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "Return status ready and a brief "
                                "message confirming that structured "
                                "output is working."
                            ),
                        },
                    ],
                },
            ],
            inferenceConfig={
                "maxTokens": 100,
                "temperature": 0,
            },
            outputConfig={
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "jm8_bedrock_preflight",
                            "description": (
                                "Confirms that Journal M8 can invoke "
                                "Bedrock and receive validated JSON."
                            ),
                            "schema": json.dumps(
                                PREFLIGHT_SCHEMA,
                                separators=(",", ":"),
                            ),
                        },
                    },
                },
            },
            requestMetadata={
                "application": "journalm8",
                "stage": stage,
                "purpose": "analyzer-preflight",
            },
        )

        raw_text = extract_text(response)
        payload = json.loads(raw_text)

        if payload.get("status") != "ready":
            raise ValueError(
                "Structured response did not report ready."
            )

        usage = response.get("usage") or {}
        metrics = response.get("metrics") or {}

        print("Model invocation: succeeded")
        print("Structured output: valid")
        print("Status:", payload["status"])
        print(
            "Stop reason:",
            response.get("stopReason"),
        )
        print(
            "Input tokens:",
            usage.get("inputTokens", 0),
        )
        print(
            "Output tokens:",
            usage.get("outputTokens", 0),
        )
        print(
            "Total tokens:",
            usage.get("totalTokens", 0),
        )
        print(
            "Latency ms:",
            metrics.get("latencyMs", 0),
        )

        return 0

    except ClientError as exc:
        error = exc.response.get("Error") or {}

        print("Model invocation: failed")
        print(
            "AWS error code:",
            error.get("Code", "Unknown"),
        )
        print(
            "AWS message:",
            error.get(
                "Message",
                "No error message returned.",
            ),
        )

        return 1

    except (
        BotoCoreError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print("Model invocation: failed")
        print(
            "Local validation error:",
            str(exc),
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
