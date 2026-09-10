from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

from insights_ask_context import normalize_question  # noqa: E402
from semantic_chunking import sha256_digest  # noqa: E402
from semantic_embedding_contract import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    EMBEDDING_NORMALIZED,
    EMBEDDING_VERSION,
    SemanticEmbeddingContractError,
    embedding_partition,
)
from semantic_embedding_provider import (  # noqa: E402
    SemanticEmbeddingProviderError,
)
from semantic_query_retrieval import (  # noqa: E402
    SEMANTIC_QUERY_RETRIEVAL_VERSION,
    SemanticQueryInputError,
    SemanticQueryUnavailableError,
    retrieve_semantic_query_evidence,
)
from semantic_vector_retrieval import (  # noqa: E402
    MAX_TOP_K,
    SemanticVectorRetrievalError,
)


USER_ID = "private-subject-123"
TABLE_NAME = "journalm8-test-entry-chunks"
QUESTION = "What challenge keeps returning?"
VECTOR = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


def stored_embedding(question: str = QUESTION) -> dict[str, object]:
    normalized = normalize_question(question)
    return {
        "embedding": list(VECTOR),
        "embeddingModelId": EMBEDDING_MODEL_ID,
        "embeddingVersion": EMBEDDING_VERSION,
        "embeddingDimensions": EMBEDDING_DIMENSIONS,
        "embeddingNormalized": EMBEDDING_NORMALIZED,
        "embeddingContentDigest": sha256_digest(normalized),
        "embeddingPartition": embedding_partition(USER_ID),
        "embeddingInputTokenCount": 7,
    }


def evidence() -> list[dict[str, object]]:
    return [{
        "score": 0.125,
        "entryId": "entry-1",
        "chunkId": "chunk-1",
        "chunkOrdinal": 0,
        "chunkCount": 1,
        "text": "Private evidence text.",
        "contentDigest": "a" * 64,
        "canonicalTextField": "cleanText",
        "sourceType": "typed",
        "entryCreatedAt": "2026-01-01T00:00:00+00:00",
    }]


class SemanticQueryRetrievalTests(unittest.TestCase):
    @patch("semantic_query_retrieval.retrieve_semantic_chunks")
    @patch("semantic_query_retrieval.embed_canonical_text")
    def test_normalizes_embeds_and_retrieves_for_exact_user(
        self,
        embed_text,
        retrieve_chunks,
    ):
        raw_question = "  What challenge   keeps returning?  "
        normalized = normalize_question(raw_question)
        embed_text.return_value = stored_embedding(normalized)
        retrieve_chunks.return_value = evidence()
        bedrock = MagicMock()
        dynamodb = MagicMock()

        result = retrieve_semantic_query_evidence(
            bedrock,
            dynamodb,
            table_name=TABLE_NAME,
            user_id=USER_ID,
            question=raw_question,
            top_k=8,
        )

        embed_text.assert_called_once_with(
            bedrock,
            canonical_text=normalized,
            user_id=USER_ID,
            content_digest=sha256_digest(normalized),
        )
        retrieve_chunks.assert_called_once_with(
            dynamodb,
            table_name=TABLE_NAME,
            user_id=USER_ID,
            query_vector=VECTOR,
            top_k=8,
        )
        self.assertEqual(result, {
            "semanticRetrievalVersion": SEMANTIC_QUERY_RETRIEVAL_VERSION,
            "semanticRetrievalStatus": "READY",
            "evidenceCount": 1,
            "retrievalLimit": 8,
            "queryEmbedding": {
                "modelId": EMBEDDING_MODEL_ID,
                "version": EMBEDDING_VERSION,
                "dimensions": EMBEDDING_DIMENSIONS,
                "normalized": EMBEDDING_NORMALIZED,
                "inputTokenCount": 7,
            },
            "evidence": evidence(),
        })
        serialized = repr(result)
        self.assertNotIn("embeddingPartition", serialized)
        self.assertNotIn("embeddingContentDigest", serialized)
        self.assertNotIn(str(VECTOR), serialized)

    @patch("semantic_query_retrieval.retrieve_semantic_chunks")
    @patch("semantic_query_retrieval.embed_canonical_text")
    def test_empty_retrieval_has_explicit_status(
        self,
        embed_text,
        retrieve_chunks,
    ):
        embed_text.return_value = stored_embedding()
        retrieve_chunks.return_value = []

        result = retrieve_semantic_query_evidence(
            MagicMock(),
            MagicMock(),
            table_name=TABLE_NAME,
            user_id=USER_ID,
            question=QUESTION,
        )

        self.assertEqual(result["semanticRetrievalStatus"], "EMPTY")
        self.assertEqual(result["evidenceCount"], 0)
        self.assertEqual(result["evidence"], [])

    @patch("semantic_query_retrieval.retrieve_semantic_chunks")
    @patch("semantic_query_retrieval.embed_canonical_text")
    def test_invalid_inputs_fail_before_bedrock(
        self,
        embed_text,
        retrieve_chunks,
    ):
        cases = (
            {"table_name": "", "user_id": USER_ID, "question": QUESTION},
            {"table_name": TABLE_NAME, "user_id": "", "question": QUESTION},
            {"table_name": TABLE_NAME, "user_id": USER_ID, "question": None},
            {"table_name": TABLE_NAME, "user_id": USER_ID, "question": "x"},
            {
                "table_name": TABLE_NAME,
                "user_id": USER_ID,
                "question": "x" * 501,
            },
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(SemanticQueryInputError):
                    retrieve_semantic_query_evidence(
                        MagicMock(),
                        MagicMock(),
                        **values,
                    )

        for top_k in (True, 0, -1, MAX_TOP_K + 1, "1"):
            with self.subTest(top_k=top_k):
                with self.assertRaises(SemanticQueryInputError):
                    retrieve_semantic_query_evidence(
                        MagicMock(),
                        MagicMock(),
                        table_name=TABLE_NAME,
                        user_id=USER_ID,
                        question=QUESTION,
                        top_k=top_k,
                    )

        embed_text.assert_not_called()
        retrieve_chunks.assert_not_called()

    @patch("semantic_query_retrieval.retrieve_semantic_chunks")
    @patch("semantic_query_retrieval.embed_canonical_text")
    def test_provider_failure_is_sanitized_and_stops_retrieval(
        self,
        embed_text,
        retrieve_chunks,
    ):
        embed_text.side_effect = SemanticEmbeddingProviderError(
            "private provider detail"
        )

        with self.assertRaises(SemanticQueryUnavailableError) as raised:
            retrieve_semantic_query_evidence(
                MagicMock(),
                MagicMock(),
                table_name=TABLE_NAME,
                user_id=USER_ID,
                question=QUESTION,
            )

        self.assertEqual(
            str(raised.exception),
            "semantic query embedding is unavailable",
        )
        self.assertIsNone(raised.exception.__cause__)
        retrieve_chunks.assert_not_called()

    @patch("semantic_query_retrieval.retrieve_semantic_chunks")
    @patch("semantic_query_retrieval.embed_canonical_text")
    def test_malformed_embedding_is_rejected_before_search(
        self,
        embed_text,
        retrieve_chunks,
    ):
        malformed = stored_embedding()
        malformed["embeddingPartition"] = "wrong-tenant"
        embed_text.return_value = malformed

        with self.assertRaises(SemanticQueryUnavailableError) as raised:
            retrieve_semantic_query_evidence(
                MagicMock(),
                MagicMock(),
                table_name=TABLE_NAME,
                user_id=USER_ID,
                question=QUESTION,
            )

        self.assertEqual(
            str(raised.exception),
            "semantic query embedding failed validation",
        )
        retrieve_chunks.assert_not_called()

    @patch("semantic_query_retrieval.retrieve_semantic_chunks")
    @patch("semantic_query_retrieval.embed_canonical_text")
    def test_contract_failure_is_sanitized_and_stops_retrieval(
        self,
        embed_text,
        retrieve_chunks,
    ):
        embed_text.side_effect = SemanticEmbeddingContractError(
            "private contract detail"
        )

        with self.assertRaises(SemanticQueryInputError) as raised:
            retrieve_semantic_query_evidence(
                MagicMock(),
                MagicMock(),
                table_name=TABLE_NAME,
                user_id=USER_ID,
                question=QUESTION,
            )

        self.assertEqual(str(raised.exception), "semantic query input is invalid")
        self.assertIsNone(raised.exception.__cause__)
        retrieve_chunks.assert_not_called()

    @patch("semantic_query_retrieval.retrieve_semantic_chunks")
    @patch("semantic_query_retrieval.embed_canonical_text")
    def test_retrieval_failure_is_sanitized(
        self,
        embed_text,
        retrieve_chunks,
    ):
        embed_text.return_value = stored_embedding()
        retrieve_chunks.side_effect = SemanticVectorRetrievalError(
            "private retrieval detail"
        )

        with self.assertRaises(SemanticQueryUnavailableError) as raised:
            retrieve_semantic_query_evidence(
                MagicMock(),
                MagicMock(),
                table_name=TABLE_NAME,
                user_id=USER_ID,
                question=QUESTION,
            )

        self.assertEqual(
            str(raised.exception),
            "semantic query retrieval is unavailable",
        )
        self.assertIsNone(raised.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
