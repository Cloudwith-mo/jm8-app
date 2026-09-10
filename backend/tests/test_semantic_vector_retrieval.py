from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
import sys
import unittest

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "function"))


from semantic_embedding_contract import (  # noqa: E402
    EMBEDDING_INDEX_NAME,
    embedding_partition,
    validate_embedding_response,
)
from semantic_memory_contract import build_entry_semantic_chunks  # noqa: E402
from semantic_vector_retrieval import (  # noqa: E402
    MAX_BATCH_GET_ATTEMPTS,
    SemanticVectorRetrievalError,
    retrieve_semantic_chunks,
)


SERIALIZER = TypeSerializer()
TABLE = "journalm8-prod-entry-chunks"
USER = "user-private-1"
OTHER_USER = "user-private-2"
VECTOR = [1.0] + [0.0] * 1023


def native_item(item):
    return {name: SERIALIZER.serialize(value) for name, value in item.items()}


def records(user_id=USER, entry_id="entry-1"):
    source = {
        "userId": user_id,
        "entryId": entry_id,
        "sourceType": "typed",
        "status": "REVIEWED",
        "rawText": "I felt calm after a long walk by the water.",
        "createdAt": "2026-09-08T12:00:00Z",
        "updatedAt": "2026-09-08T12:05:00Z",
    }
    chunk = dict(build_entry_semantic_chunks(source)[0])
    token = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()
    digest = chunk["contentDigest"]
    chunk.update({
        "PK": f"USER#{user_id}",
        "SK": (
            f"ENTRY#{token}#GEN#{digest}#CHUNK#"
            f"{chunk['chunkOrdinal']:08d}#{chunk['chunkId']}"
        ),
        "entityType": "SEMANTIC_CHUNK",
        "generationId": digest,
    })
    chunk.update(validate_embedding_response(
        {"embedding": VECTOR, "inputTextTokenCount": 12},
        user_id=user_id,
        content_digest=digest,
    ))
    chunk["embedding"] = [Decimal(str(value)) for value in chunk["embedding"]]
    manifest = {
        "PK": f"USER#{user_id}",
        "SK": f"ENTRY#{token}#MANIFEST",
        "entityType": "SEMANTIC_MEMORY_MANIFEST",
        "userId": user_id,
        "entryId": entry_id,
        "activeContentDigest": digest,
        "chunkCount": 1,
        "chunkingVersion": chunk["chunkingVersion"],
    }
    return chunk, manifest


class FakeClient:
    def __init__(self, *, search_results, items, unprocessed_times=0):
        self.search_results = search_results
        self.items = {
            (item["PK"], item["SK"]): native_item(item)
            for item in items
        }
        self.unprocessed_times = unprocessed_times
        self.search_calls = []
        self.batch_calls = []

    def search_vectors(self, **kwargs):
        self.search_calls.append(kwargs)
        return {"SearchResults": self.search_results}

    def batch_get_item(self, **kwargs):
        self.batch_calls.append(kwargs)
        request = kwargs["RequestItems"][TABLE]
        keys = request["Keys"]
        if self.unprocessed_times:
            self.unprocessed_times -= 1
            return {
                "Responses": {TABLE: []},
                "UnprocessedKeys": {TABLE: {"Keys": keys}},
            }
        found = []
        for key in keys:
            identity = (key["PK"]["S"], key["SK"]["S"])
            if identity in self.items:
                found.append(self.items[identity])
        return {"Responses": {TABLE: found}, "UnprocessedKeys": {}}


def result_for(chunk, score=0.125):
    return {
        "Item": {
            "PK": {"S": chunk["PK"]},
            "SK": {"S": chunk["SK"]},
        },
        "Score": score,
    }


class SemanticVectorRetrievalTests(unittest.TestCase):
    def test_search_is_exactly_tenant_partitioned_and_hydrated_strongly(self):
        chunk, manifest = records()
        client = FakeClient(
            search_results=[result_for(chunk)],
            items=[chunk, manifest],
        )

        matches = retrieve_semantic_chunks(
            client,
            table_name=TABLE,
            user_id=USER,
            query_vector=VECTOR,
            top_k=8,
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["entryId"], "entry-1")
        self.assertEqual(matches[0]["text"], chunk["text"])
        self.assertEqual(matches[0]["score"], 0.125)
        self.assertNotIn("embedding", matches[0])
        self.assertEqual(client.search_calls, [{
            "TableName": TABLE,
            "IndexName": EMBEDDING_INDEX_NAME,
            "SearchVector": [
                {"N": str(component)} for component in VECTOR
            ],
            "TopK": 8,
            "ProjectionExpression": "PK, SK",
            "SearchConditionExpression": "#tenant = :tenant",
            "ExpressionAttributeNames": {"#tenant": "embeddingPartition"},
            "ExpressionAttributeValues": {
                ":tenant": {"S": embedding_partition(USER)},
            },
            "ReturnConsumedCapacity": "NONE",
        }])
        self.assertEqual(len(client.batch_calls), 2)
        for call in client.batch_calls:
            self.assertTrue(call["RequestItems"][TABLE]["ConsistentRead"])

    def test_cross_tenant_search_result_fails_before_hydration(self):
        other_chunk, _ = records(OTHER_USER)
        client = FakeClient(
            search_results=[result_for(other_chunk)],
            items=[],
        )
        with self.assertRaisesRegex(
            SemanticVectorRetrievalError,
            "tenant check failed",
        ):
            retrieve_semantic_chunks(
                client,
                table_name=TABLE,
                user_id=USER,
                query_vector=VECTOR,
            )
        self.assertEqual(client.batch_calls, [])

    def test_stale_or_missing_generation_is_omitted(self):
        chunk, manifest = records()
        manifest["activeContentDigest"] = "f" * 64
        client = FakeClient(
            search_results=[result_for(chunk)],
            items=[chunk, manifest],
        )
        self.assertEqual(
            retrieve_semantic_chunks(
                client,
                table_name=TABLE,
                user_id=USER,
                query_vector=VECTOR,
            ),
            [],
        )

        missing = FakeClient(
            search_results=[result_for(chunk)],
            items=[],
        )
        self.assertEqual(
            retrieve_semantic_chunks(
                missing,
                table_name=TABLE,
                user_id=USER,
                query_vector=VECTOR,
            ),
            [],
        )

    def test_malformed_manifest_fails_closed(self):
        chunk, manifest = records()
        manifest["chunkCount"] = 2
        client = FakeClient(
            search_results=[result_for(chunk)],
            items=[chunk, manifest],
        )
        with self.assertRaisesRegex(
            SemanticVectorRetrievalError,
            "data integrity check failed",
        ):
            retrieve_semantic_chunks(
                client,
                table_name=TABLE,
                user_id=USER,
                query_vector=VECTOR,
            )

    def test_malformed_current_chunk_fails_closed(self):
        chunk, manifest = records()
        chunk["text"] = "tampered"
        client = FakeClient(
            search_results=[result_for(chunk)],
            items=[chunk, manifest],
        )
        with self.assertRaisesRegex(
            SemanticVectorRetrievalError,
            "data integrity check failed",
        ):
            retrieve_semantic_chunks(
                client,
                table_name=TABLE,
                user_id=USER,
                query_vector=VECTOR,
            )

    def test_query_and_result_bounds_fail_before_data_access(self):
        for vector, top_k in (([1.0], 12), (VECTOR, 0), (VECTOR, 25), (VECTOR, True)):
            with self.subTest(top_k=top_k, length=len(vector)):
                client = FakeClient(search_results=[], items=[])
                with self.assertRaises(SemanticVectorRetrievalError):
                    retrieve_semantic_chunks(
                        client,
                        table_name=TABLE,
                        user_id=USER,
                        query_vector=vector,
                        top_k=top_k,
                    )
                self.assertEqual(client.search_calls, [])

    def test_unprocessed_hydration_retries_are_bounded(self):
        chunk, manifest = records()
        client = FakeClient(
            search_results=[result_for(chunk)],
            items=[chunk, manifest],
            unprocessed_times=1,
        )
        matches = retrieve_semantic_chunks(
            client,
            table_name=TABLE,
            user_id=USER,
            query_vector=VECTOR,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(len(client.batch_calls), 3)

        exhausted = FakeClient(
            search_results=[result_for(chunk)],
            items=[chunk, manifest],
            unprocessed_times=MAX_BATCH_GET_ATTEMPTS,
        )
        with self.assertRaisesRegex(
            SemanticVectorRetrievalError,
            "retry failed",
        ):
            retrieve_semantic_chunks(
                exhausted,
                table_name=TABLE,
                user_id=USER,
                query_vector=VECTOR,
            )

    def test_provider_errors_are_sanitized(self):
        class FailingClient:
            def search_vectors(self, **kwargs):
                raise ClientError(
                    {"Error": {"Code": "AccessDenied", "Message": "SECRET"}},
                    "SearchVectors",
                )

        with self.assertRaises(SemanticVectorRetrievalError) as context:
            retrieve_semantic_chunks(
                FailingClient(),
                table_name=TABLE,
                user_id=USER,
                query_vector=VECTOR,
            )
        self.assertNotIn("SECRET", str(context.exception))


if __name__ == "__main__":
    unittest.main()
