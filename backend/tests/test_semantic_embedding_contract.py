import copy
import math
import sys
import unittest
from decimal import Decimal
from pathlib import Path


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

from semantic_embedding_contract import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_DISTANCE_FUNCTION,
    EMBEDDING_INDEX_NAME,
    EMBEDDING_MAX_INPUT_CHARACTERS,
    EMBEDDING_MODEL_ID,
    EMBEDDING_NORMALIZED,
    EMBEDDING_PARTITION_ATTRIBUTE,
    EMBEDDING_VECTOR_ATTRIBUTE,
    EMBEDDING_VERSION,
    SemanticEmbeddingContractError,
    build_embedding_request,
    embedding_is_current,
    embedding_partition,
    validate_content_digest,
    validate_embedding_response,
    validate_stored_embedding,
)


USER_ID = "subject-123"
CONTENT_DIGEST = "a" * 64


def normalized_vector():
    return [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


def provider_response():
    return {
        "embedding": normalized_vector(),
        "inputTextTokenCount": 17,
    }


def stored_embedding():
    return validate_embedding_response(
        provider_response(),
        user_id=USER_ID,
        content_digest=CONTENT_DIGEST,
    )


class TestEmbeddingConstants(unittest.TestCase):
    def test_contract_is_pinned_to_titan_v2(self):
        self.assertEqual(
            EMBEDDING_MODEL_ID,
            "amazon.titan-embed-text-v2:0",
        )
        self.assertEqual(EMBEDDING_VERSION, "jm8-titan-text-embedding-v1")
        self.assertEqual(EMBEDDING_DIMENSIONS, 1024)
        self.assertIs(EMBEDDING_NORMALIZED, True)
        self.assertEqual(EMBEDDING_DISTANCE_FUNCTION, "COSINE")

    def test_vector_schema_names_are_versioned_contract_constants(self):
        self.assertEqual(EMBEDDING_VECTOR_ATTRIBUTE, "embedding")
        self.assertEqual(EMBEDDING_PARTITION_ATTRIBUTE, "embeddingPartition")
        self.assertEqual(EMBEDDING_INDEX_NAME, "SemanticEmbeddingIndex")


class TestEmbeddingRequest(unittest.TestCase):
    def test_request_preserves_exact_canonical_text(self):
        text = "  A journal paragraph with intentional spacing.\n"
        self.assertEqual(
            build_embedding_request(text),
            {
                "inputText": text,
                "dimensions": 1024,
                "normalize": True,
            },
        )

    def test_request_accepts_provider_character_limit(self):
        text = "x" * EMBEDDING_MAX_INPUT_CHARACTERS
        self.assertEqual(build_embedding_request(text)["inputText"], text)

    def test_request_rejects_invalid_text(self):
        for value in (None, 7, b"text", "", " \n\t"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SemanticEmbeddingContractError):
                    build_embedding_request(value)

    def test_request_rejects_text_over_provider_limit_without_echoing_it(self):
        text = "private" * 8_000
        with self.assertRaises(SemanticEmbeddingContractError) as raised:
            build_embedding_request(text)
        self.assertNotIn("private", str(raised.exception))


class TestEmbeddingIdentityBinding(unittest.TestCase):
    def test_partition_is_stable_sha256_and_does_not_expose_subject(self):
        first = embedding_partition(USER_ID)
        second = embedding_partition(USER_ID)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotIn(USER_ID, first)

    def test_different_subjects_have_different_partitions(self):
        self.assertNotEqual(
            embedding_partition("subject-one"),
            embedding_partition("subject-two"),
        )

    def test_partition_rejects_invalid_subjects(self):
        for value in (None, 3, "", " \t"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SemanticEmbeddingContractError):
                    embedding_partition(value)

    def test_content_digest_requires_lowercase_sha256(self):
        self.assertEqual(validate_content_digest(CONTENT_DIGEST), CONTENT_DIGEST)
        for value in (
            None,
            "",
            "a" * 63,
            "a" * 65,
            "A" * 64,
            "g" * 64,
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SemanticEmbeddingContractError):
                    validate_content_digest(value)


class TestEmbeddingResponse(unittest.TestCase):
    def test_valid_response_is_bound_to_model_tenant_and_generation(self):
        result = stored_embedding()
        self.assertEqual(result["embedding"], normalized_vector())
        self.assertEqual(result["embeddingModelId"], EMBEDDING_MODEL_ID)
        self.assertEqual(result["embeddingVersion"], EMBEDDING_VERSION)
        self.assertEqual(result["embeddingDimensions"], 1024)
        self.assertIs(result["embeddingNormalized"], True)
        self.assertEqual(result["embeddingContentDigest"], CONTENT_DIGEST)
        self.assertEqual(result["embeddingPartition"], embedding_partition(USER_ID))
        self.assertEqual(result["embeddingInputTokenCount"], 17)

    def test_response_vector_is_defensively_copied(self):
        response = provider_response()
        result = validate_embedding_response(
            response,
            user_id=USER_ID,
            content_digest=CONTENT_DIGEST,
        )
        response["embedding"][0] = 0.0
        self.assertEqual(result["embedding"][0], 1.0)

    def test_stored_dynamodb_decimal_vector_is_accepted(self):
        item = stored_embedding()
        item["embedding"] = [Decimal("1.0")] + [
            Decimal("0.0")
        ] * (EMBEDDING_DIMENSIONS - 1)
        result = validate_stored_embedding(
            item,
            user_id=USER_ID,
            content_digest=CONTENT_DIGEST,
        )
        self.assertEqual(result["embedding"], normalized_vector())

    def test_response_rejects_invalid_vector_shapes(self):
        values = (
            None,
            "not-a-vector",
            [1.0],
            normalized_vector() + [0.0],
        )
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                response = provider_response()
                response["embedding"] = value
                with self.assertRaises(SemanticEmbeddingContractError):
                    validate_embedding_response(
                        response,
                        user_id=USER_ID,
                        content_digest=CONTENT_DIGEST,
                    )

    def test_response_rejects_boolean_and_non_numeric_components(self):
        for value in (True, "0.0", None):
            with self.subTest(value=repr(value)):
                response = provider_response()
                response["embedding"][1] = value
                with self.assertRaises(SemanticEmbeddingContractError):
                    validate_embedding_response(
                        response,
                        user_id=USER_ID,
                        content_digest=CONTENT_DIGEST,
                    )

    def test_response_rejects_non_finite_components(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                response = provider_response()
                response["embedding"][0] = value
                with self.assertRaises(SemanticEmbeddingContractError):
                    validate_embedding_response(
                        response,
                        user_id=USER_ID,
                        content_digest=CONTENT_DIGEST,
                    )

    def test_response_rejects_non_normalized_vector(self):
        response = provider_response()
        response["embedding"] = [0.5] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
        with self.assertRaises(SemanticEmbeddingContractError):
            validate_embedding_response(
                response,
                user_id=USER_ID,
                content_digest=CONTENT_DIGEST,
            )

    def test_response_rejects_invalid_token_counts(self):
        for value in (None, True, 0, -1, 8_193, 1.5, "17"):
            with self.subTest(value=repr(value)):
                response = provider_response()
                response["inputTextTokenCount"] = value
                with self.assertRaises(SemanticEmbeddingContractError):
                    validate_embedding_response(
                        response,
                        user_id=USER_ID,
                        content_digest=CONTENT_DIGEST,
                    )


class TestStoredEmbedding(unittest.TestCase):
    def test_integral_dynamodb_decimal_token_count_is_accepted(self):
        item = stored_embedding()
        item["embeddingInputTokenCount"] = Decimal("17")

        result = validate_stored_embedding(
            item,
            user_id=USER_ID,
            content_digest=CONTENT_DIGEST,
        )

        self.assertEqual(result["embeddingInputTokenCount"], 17)
        self.assertIsInstance(result["embeddingInputTokenCount"], int)

    def test_nonintegral_and_nonfinite_decimal_token_counts_are_rejected(self):
        for value in (Decimal("17.5"), Decimal("NaN")):
            with self.subTest(value=str(value)):
                item = stored_embedding()
                item["embeddingInputTokenCount"] = value
                with self.assertRaises(SemanticEmbeddingContractError):
                    validate_stored_embedding(
                        item,
                        user_id=USER_ID,
                        content_digest=CONTENT_DIGEST,
                    )

    def test_complete_active_embedding_is_current(self):
        item = stored_embedding()
        self.assertTrue(
            embedding_is_current(
                item,
                user_id=USER_ID,
                content_digest=CONTENT_DIGEST,
            )
        )
        self.assertEqual(
            validate_stored_embedding(
                item,
                user_id=USER_ID,
                content_digest=CONTENT_DIGEST,
            ),
            item,
        )

    def test_stale_or_cross_tenant_embedding_is_not_current(self):
        self.assertFalse(
            embedding_is_current(
                stored_embedding(),
                user_id="another-subject",
                content_digest=CONTENT_DIGEST,
            )
        )
        self.assertFalse(
            embedding_is_current(
                stored_embedding(),
                user_id=USER_ID,
                content_digest="b" * 64,
            )
        )

    def test_each_corrupt_metadata_field_is_rejected(self):
        changes = {
            "embeddingModelId": "another-model",
            "embeddingVersion": "another-version",
            "embeddingDimensions": 512,
            "embeddingNormalized": False,
            "embeddingContentDigest": "b" * 64,
            "embeddingPartition": "b" * 64,
            "embeddingInputTokenCount": 0,
            "embedding": [0.5] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                item = copy.deepcopy(stored_embedding())
                item[field] = value
                self.assertFalse(
                    embedding_is_current(
                        item,
                        user_id=USER_ID,
                        content_digest=CONTENT_DIGEST,
                    )
                )

    def test_missing_embedding_fields_are_rejected(self):
        for field in tuple(stored_embedding()):
            with self.subTest(field=field):
                item = stored_embedding()
                item.pop(field)
                self.assertFalse(
                    embedding_is_current(
                        item,
                        user_id=USER_ID,
                        content_digest=CONTENT_DIGEST,
                    )
                )


if __name__ == "__main__":
    unittest.main()
