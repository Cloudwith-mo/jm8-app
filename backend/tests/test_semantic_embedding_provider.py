import json
import math
import sys
import unittest
from pathlib import Path


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

from semantic_embedding_contract import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    SemanticEmbeddingContractError,
    embedding_partition,
)
from semantic_embedding_provider import (
    SemanticEmbeddingProviderError,
    embed_canonical_text,
)


USER_ID = "subject-123"
CONTENT_DIGEST = "a" * 64
CANONICAL_TEXT = "A private journal sentence with café notes."


def normalized_vector():
    return [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


def encoded_response(**updates):
    payload = {
        "embedding": normalized_vector(),
        "inputTextTokenCount": 11,
    }
    payload.update(updates)
    return json.dumps(payload).encode("utf-8")


class FakeBody:
    def __init__(self, value):
        self.value = value
        self.read_count = 0

    def read(self):
        self.read_count += 1
        return self.value


class FailingBody:
    def read(self):
        raise OSError("private provider detail")


class RecordingClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def invoke(client):
    return embed_canonical_text(
        client,
        canonical_text=CANONICAL_TEXT,
        user_id=USER_ID,
        content_digest=CONTENT_DIGEST,
    )


class TestTitanEmbeddingInvocation(unittest.TestCase):
    def test_invocation_uses_exact_titan_v2_protocol(self):
        body = FakeBody(encoded_response())
        client = RecordingClient({"body": body})

        result = invoke(client)

        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["modelId"], EMBEDDING_MODEL_ID)
        self.assertEqual(call["contentType"], "application/json")
        self.assertEqual(call["accept"], "application/json")
        self.assertEqual(
            json.loads(call["body"].decode("utf-8")),
            {
                "inputText": CANONICAL_TEXT,
                "dimensions": 1024,
                "normalize": True,
            },
        )
        self.assertEqual(body.read_count, 1)
        self.assertEqual(result["embeddingModelId"], EMBEDDING_MODEL_ID)
        self.assertEqual(result["embeddingContentDigest"], CONTENT_DIGEST)
        self.assertEqual(result["embeddingPartition"], embedding_partition(USER_ID))

    def test_unicode_request_is_utf8_and_not_ascii_escaped(self):
        client = RecordingClient({"body": FakeBody(encoded_response())})
        invoke(client)
        encoded = client.calls[0]["body"]
        self.assertIn("café".encode("utf-8"), encoded)
        self.assertNotIn(b"\\u00e9", encoded)

    def test_request_contains_no_identity_or_digest(self):
        client = RecordingClient({"body": FakeBody(encoded_response())})
        invoke(client)
        encoded = client.calls[0]["body"].decode("utf-8")
        self.assertNotIn(USER_ID, encoded)
        self.assertNotIn(CONTENT_DIGEST, encoded)

    def test_string_response_body_is_supported(self):
        client = RecordingClient(
            {"body": FakeBody(encoded_response().decode("utf-8"))}
        )
        self.assertEqual(invoke(client)["embeddingInputTokenCount"], 11)

    def test_extra_bedrock_response_fields_are_ignored(self):
        response = encoded_response(providerMetadata={"requestId": "safe"})
        client = RecordingClient({"body": FakeBody(response)})
        self.assertEqual(invoke(client)["embeddingDimensions"], 1024)


class TestPreInvocationValidation(unittest.TestCase):
    def test_invalid_text_never_invokes_bedrock(self):
        client = RecordingClient({"body": FakeBody(encoded_response())})
        with self.assertRaises(SemanticEmbeddingContractError):
            embed_canonical_text(
                client,
                canonical_text="   ",
                user_id=USER_ID,
                content_digest=CONTENT_DIGEST,
            )
        self.assertEqual(client.calls, [])

    def test_invalid_digest_never_invokes_bedrock(self):
        client = RecordingClient({"body": FakeBody(encoded_response())})
        with self.assertRaises(SemanticEmbeddingContractError):
            embed_canonical_text(
                client,
                canonical_text=CANONICAL_TEXT,
                user_id=USER_ID,
                content_digest="invalid",
            )
        self.assertEqual(client.calls, [])

    def test_invalid_identity_never_invokes_bedrock(self):
        client = RecordingClient({"body": FakeBody(encoded_response())})
        with self.assertRaises(SemanticEmbeddingContractError):
            embed_canonical_text(
                client,
                canonical_text=CANONICAL_TEXT,
                user_id="",
                content_digest=CONTENT_DIGEST,
            )
        self.assertEqual(client.calls, [])


class TestProviderFailures(unittest.TestCase):
    def assert_sanitized_failure(self, client, forbidden="private"):
        with self.assertRaises(SemanticEmbeddingProviderError) as raised:
            invoke(client)
        message = str(raised.exception)
        self.assertNotIn(forbidden, message)
        self.assertNotIn(CANONICAL_TEXT, message)
        self.assertIsNone(raised.exception.__cause__)

    def test_client_failure_is_sanitized_and_not_retried(self):
        client = RecordingClient(error=RuntimeError("private provider detail"))
        self.assert_sanitized_failure(client)
        self.assertEqual(len(client.calls), 1)

    def test_non_mapping_response_is_rejected(self):
        self.assert_sanitized_failure(RecordingClient([]))

    def test_missing_and_unreadable_bodies_are_rejected(self):
        for response in ({}, {"body": None}, {"body": object()}):
            with self.subTest(response=response):
                self.assert_sanitized_failure(RecordingClient(response))

    def test_body_read_failure_is_sanitized(self):
        self.assert_sanitized_failure(
            RecordingClient({"body": FailingBody()})
        )

    def test_invalid_body_types_are_rejected(self):
        for value in (None, 3, {}, []):
            with self.subTest(value_type=type(value).__name__):
                self.assert_sanitized_failure(
                    RecordingClient({"body": FakeBody(value)})
                )

    def test_invalid_utf8_is_rejected(self):
        self.assert_sanitized_failure(
            RecordingClient({"body": FakeBody(b"\xff")})
        )

    def test_invalid_json_and_non_object_json_are_rejected(self):
        for value in (b"not-json", b"[]", b"null", b'"text"'):
            with self.subTest(value=value):
                self.assert_sanitized_failure(
                    RecordingClient({"body": FakeBody(value)})
                )

    def test_invalid_provider_vectors_are_rejected_without_values(self):
        invalid_vectors = (
            [1.0],
            [math.nan] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
            [0.5] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
        )
        for vector in invalid_vectors:
            with self.subTest(vector_length=len(vector)):
                client = RecordingClient({
                    "body": FakeBody(encoded_response(embedding=vector))
                })
                self.assert_sanitized_failure(client)

    def test_missing_token_count_is_rejected(self):
        payload = {"embedding": normalized_vector()}
        client = RecordingClient({
            "body": FakeBody(json.dumps(payload).encode("utf-8"))
        })
        self.assert_sanitized_failure(client)


if __name__ == "__main__":
    unittest.main()
