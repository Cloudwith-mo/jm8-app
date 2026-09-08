import copy
import hashlib
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import boto3
from botocore.exceptions import BotoCoreError, ClientError


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

from semantic_chunking import CHUNKING_VERSION, sha256_digest
from semantic_embedding_contract import (
    EMBEDDING_DIMENSIONS,
    SemanticEmbeddingContractError,
    embedding_partition,
    validate_embedding_response,
)
from semantic_embedding_store import (
    SemanticEmbeddingPersistenceError,
    SemanticEmbeddingStaleGenerationError,
    persist_active_embedding,
)
from semantic_memory_store import CHUNK_ENTITY_TYPE, MANIFEST_ENTITY_TYPE


USER_ID = "subject-123"
ENTRY_ID = "entry-456"
CONTENT_DIGEST = "a" * 64
TEXT = "Private active semantic chunk."
CHUNK_DIGEST = sha256_digest(TEXT)


def entry_token(entry_id=ENTRY_ID):
    return hashlib.sha256(entry_id.encode("utf-8")).hexdigest()


def chunk_id(
    entry_id=ENTRY_ID,
    content_digest=CONTENT_DIGEST,
    ordinal=0,
    chunk_digest=CHUNK_DIGEST,
):
    identity = "\0".join((
        CHUNKING_VERSION,
        entry_id,
        content_digest,
        str(ordinal),
        chunk_digest,
    ))
    return f"chunk_{sha256_digest(identity)}"


def semantic_chunk(**changes):
    identifier = chunk_id()
    chunk = {
        "PK": f"USER#{USER_ID}",
        "SK": (
            f"ENTRY#{entry_token()}#GEN#{CONTENT_DIGEST}#CHUNK#"
            f"00000000#{identifier}"
        ),
        "entityType": CHUNK_ENTITY_TYPE,
        "userId": USER_ID,
        "entryId": ENTRY_ID,
        "text": TEXT,
        "contentDigest": CONTENT_DIGEST,
        "chunkDigest": CHUNK_DIGEST,
        "chunkId": identifier,
        "chunkOrdinal": 0,
        "chunkCount": 1,
        "chunkingVersion": CHUNKING_VERSION,
        "generationId": CONTENT_DIGEST,
        "canonicalTextField": "cleanText",
        "sourceType": "typed",
        "characterCount": len(TEXT),
        "wordCount": len(TEXT.split()),
    }
    chunk.update(changes)
    return chunk


def stored_embedding(**changes):
    result = validate_embedding_response(
        {
            "embedding": [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
            "inputTextTokenCount": 7,
        },
        user_id=USER_ID,
        content_digest=CONTENT_DIGEST,
    )
    result.update(changes)
    return result


def transaction_error(code="TransactionCanceledException", reasons=None):
    response = {
        "Error": {
            "Code": code,
            "Message": "private journal subject-123 entry-456 payload",
        }
    }
    if reasons is not None:
        response["CancellationReasons"] = reasons
    return ClientError(response, "TransactWriteItems")


class FakeTransactionClient:
    def __init__(self, table):
        self.table = table
        self.calls = []
        self.error = None
        self.response = {}
        self.before_call = None

    def transact_write_items(self, *, TransactItems):
        request = copy.deepcopy(TransactItems)
        self.calls.append(request)
        if self.before_call is not None:
            self.before_call(self.table)
        if self.error is not None:
            raise self.error

        check = request[0]["ConditionCheck"]
        update = request[1]["Update"]
        for operation in (check, update):
            key = operation["Key"]
            if not isinstance(key.get("PK"), str) or not isinstance(
                key.get("SK"), str
            ):
                raise AssertionError("resource client requires native keys")

        check_key = (check["Key"]["PK"], check["Key"]["SK"])
        manifest = self.table.items.get(check_key)
        check_values = check["ExpressionAttributeValues"]
        manifest_valid = (
            manifest is not None
            and manifest.get("entityType") == check_values[":manifestType"]
            and manifest.get("userId") == check_values[":userId"]
            and manifest.get("entryId") == check_values[":entryId"]
            and manifest.get("activeContentDigest")
            == check_values[":contentDigest"]
            and manifest.get("chunkingVersion")
            == check_values[":chunkingVersion"]
        )

        update_key = (update["Key"]["PK"], update["Key"]["SK"])
        item = self.table.items.get(update_key)
        values = update["ExpressionAttributeValues"]
        chunk_valid = (
            item is not None
            and item.get("entityType") == values[":chunkType"]
            and item.get("userId") == values[":userId"]
            and item.get("entryId") == values[":entryId"]
            and item.get("contentDigest") == values[":contentDigest"]
            and item.get("generationId") == values[":contentDigest"]
            and item.get("chunkId") == values[":chunkId"]
            and item.get("chunkOrdinal") == values[":chunkOrdinal"]
            and item.get("chunkCount") == values[":chunkCount"]
            and item.get("chunkDigest") == values[":chunkDigest"]
            and item.get("characterCount") == values[":characterCount"]
            and item.get("wordCount") == values[":wordCount"]
            and item.get("chunkingVersion") == values[":chunkingVersion"]
        )
        if not manifest_valid or not chunk_valid:
            reasons = [
                {"Code": "None"},
                {"Code": "None"},
            ]
            reasons[0 if not manifest_valid else 1] = {
                "Code": "ConditionalCheckFailed"
            }
            raise transaction_error(reasons=reasons)

        for name, attribute in update["ExpressionAttributeNames"].items():
            if not name.startswith("#embedding"):
                continue
            item[attribute] = copy.deepcopy(values[name.replace("#", ":")])
        return self.response


class FakeTable:
    def __init__(self):
        self.name = "semantic-memory-test"
        self.items = {}
        self.client = FakeTransactionClient(self)
        self.meta = SimpleNamespace(client=self.client)
        chunk = semantic_chunk()
        manifest = {
            "PK": chunk["PK"],
            "SK": f"ENTRY#{entry_token()}#MANIFEST",
            "entityType": MANIFEST_ENTITY_TYPE,
            "userId": USER_ID,
            "entryId": ENTRY_ID,
            "activeContentDigest": CONTENT_DIGEST,
            "chunkingVersion": CHUNKING_VERSION,
        }
        self.items[(manifest["PK"], manifest["SK"])] = manifest
        self.items[(chunk["PK"], chunk["SK"])] = chunk


class TestActiveEmbeddingPersistence(unittest.TestCase):
    def test_active_embedding_is_persisted_atomically(self):
        table = FakeTable()
        chunk = semantic_chunk()
        embedding = stored_embedding()

        result = persist_active_embedding(table, chunk, embedding)

        self.assertEqual(result, embedding)
        self.assertEqual(len(table.client.calls), 1)
        stored = table.items[(chunk["PK"], chunk["SK"])]
        self.assertEqual(stored["embeddingContentDigest"], CONTENT_DIGEST)
        self.assertEqual(
            stored["embeddingPartition"],
            embedding_partition(USER_ID),
        )
        self.assertEqual(len(stored["embedding"]), EMBEDDING_DIMENSIONS)
        self.assertTrue(
            all(isinstance(value, Decimal) for value in stored["embedding"])
        )

    def test_transaction_checks_manifest_before_updating_exact_chunk(self):
        table = FakeTable()
        chunk = semantic_chunk()

        persist_active_embedding(table, chunk, stored_embedding())

        request = table.client.calls[0]
        self.assertEqual(len(request), 2)
        check = request[0]["ConditionCheck"]
        update = request[1]["Update"]
        self.assertEqual(check["TableName"], table.name)
        self.assertEqual(update["TableName"], table.name)
        self.assertEqual(
            check["Key"],
            {
                "PK": f"USER#{USER_ID}",
                "SK": f"ENTRY#{entry_token()}#MANIFEST",
            },
        )
        self.assertEqual(update["Key"], {"PK": chunk["PK"], "SK": chunk["SK"]})
        self.assertIn("#activeContentDigest", check["ConditionExpression"])
        self.assertIn("#generationId", update["ConditionExpression"])

    def test_persistence_is_idempotent(self):
        table = FakeTable()
        chunk = semantic_chunk()
        embedding = stored_embedding()

        first = persist_active_embedding(table, chunk, embedding)
        first_item = copy.deepcopy(table.items[(chunk["PK"], chunk["SK"])])
        second = persist_active_embedding(table, chunk, embedding)

        self.assertEqual(first, second)
        self.assertEqual(
            first_item,
            table.items[(chunk["PK"], chunk["SK"])],
        )
        self.assertEqual(len(table.client.calls), 2)

    def test_inputs_are_not_mutated(self):
        table = FakeTable()
        chunk = semantic_chunk()
        embedding = stored_embedding()
        original_chunk = copy.deepcopy(chunk)
        original_embedding = copy.deepcopy(embedding)

        persist_active_embedding(table, chunk, embedding)

        self.assertEqual(chunk, original_chunk)
        self.assertEqual(embedding, original_embedding)

    def test_transaction_contains_no_canonical_text(self):
        table = FakeTable()
        persist_active_embedding(table, semantic_chunk(), stored_embedding())
        request = repr(table.client.calls[0])
        self.assertNotIn(TEXT, request)

    def test_resource_bound_client_receives_native_document_values(self):
        table = FakeTable()
        persist_active_embedding(table, semantic_chunk(), stored_embedding())
        update = table.client.calls[0][1]["Update"]
        self.assertIsInstance(update["Key"]["PK"], str)
        self.assertIsInstance(update["Key"]["SK"], str)
        vector = update["ExpressionAttributeValues"][":embedding"]
        self.assertTrue(all(isinstance(value, Decimal) for value in vector))

    def test_boto3_resource_client_serializes_transaction_at_wire_boundary(self):
        class RequestCaptured(Exception):
            pass

        resource = boto3.resource(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        table = resource.Table("semantic-memory-test")
        captured = {}

        def capture_request(model, params, **kwargs):
            captured.update(params)
            raise RequestCaptured

        table.meta.client.meta.events.register_first(
            "before-call.dynamodb.TransactWriteItems",
            capture_request,
        )

        with self.assertRaises(RequestCaptured):
            persist_active_embedding(
                table,
                semantic_chunk(),
                stored_embedding(),
            )

        wire = json.loads(captured["body"])
        check = wire["TransactItems"][0]["ConditionCheck"]
        update = wire["TransactItems"][1]["Update"]
        self.assertEqual(check["Key"]["PK"], {"S": f"USER#{USER_ID}"})
        self.assertEqual(update["Key"]["SK"], {"S": semantic_chunk()["SK"]})
        vector = update["ExpressionAttributeValues"][":embedding"]
        self.assertEqual(len(vector["L"]), EMBEDDING_DIMENSIONS)
        self.assertTrue(all("N" in value for value in vector["L"]))


class TestGenerationRaces(unittest.TestCase):
    def assert_stale(self, table, chunk=None):
        with self.assertRaises(SemanticEmbeddingStaleGenerationError) as raised:
            persist_active_embedding(
                table,
                chunk or semantic_chunk(),
                stored_embedding(),
            )
        message = str(raised.exception)
        for private in (USER_ID, ENTRY_ID, TEXT, CONTENT_DIGEST):
            self.assertNotIn(private, message)

    def test_replaced_manifest_rejects_stale_embedding(self):
        table = FakeTable()
        manifest = next(
            item
            for item in table.items.values()
            if item.get("entityType") == MANIFEST_ENTITY_TYPE
        )
        manifest["activeContentDigest"] = "b" * 64

        self.assert_stale(table)
        chunk = semantic_chunk()
        self.assertNotIn(
            "embedding",
            table.items[(chunk["PK"], chunk["SK"])],
        )

    def test_missing_manifest_rejects_embedding(self):
        table = FakeTable()
        manifest_key = next(
            key
            for key, item in table.items.items()
            if item.get("entityType") == MANIFEST_ENTITY_TYPE
        )
        table.items.pop(manifest_key)
        self.assert_stale(table)

    def test_missing_or_changed_chunk_rejects_embedding(self):
        for mutation in ("missing", "changed"):
            with self.subTest(mutation=mutation):
                table = FakeTable()
                chunk = semantic_chunk()
                if mutation == "missing":
                    table.items.pop((chunk["PK"], chunk["SK"]))
                else:
                    table.items[(chunk["PK"], chunk["SK"])][
                        "generationId"
                    ] = "b" * 64
                self.assert_stale(table, chunk)

    def test_deletion_between_validation_and_transaction_fails_closed(self):
        table = FakeTable()

        def delete_records(current):
            current.items.clear()

        table.client.before_call = delete_records
        self.assert_stale(table)
        self.assertEqual(table.items, {})


class TestPersistenceFailures(unittest.TestCase):
    def test_invalid_table_dependency_is_rejected_before_a_call(self):
        for table in (None, object(), SimpleNamespace(name="table")):
            with self.subTest(table_type=type(table).__name__):
                with self.assertRaises(SemanticEmbeddingPersistenceError):
                    persist_active_embedding(
                        table,
                        semantic_chunk(),
                        stored_embedding(),
                    )

    def test_malformed_chunks_are_rejected_before_a_call(self):
        mutations = {
            "entityType": "WRONG",
            "PK": "USER#other",
            "SK": "ENTRY#wrong",
            "generationId": "b" * 64,
            "chunkId": "chunk_wrong",
            "chunkOrdinal": True,
            "chunkCount": 0,
            "characterCount": 999,
            "wordCount": 999,
            "chunkingVersion": "wrong",
            "chunkDigest": "b" * 64,
            "text": "changed text",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                table = FakeTable()
                chunk = semantic_chunk(**{field: value})
                with self.assertRaises(SemanticEmbeddingPersistenceError):
                    persist_active_embedding(
                        table,
                        chunk,
                        stored_embedding(),
                    )
                self.assertEqual(table.client.calls, [])

    def test_invalid_embedding_is_rejected_before_a_call(self):
        changes = {
            "embeddingContentDigest": "b" * 64,
            "embeddingPartition": "b" * 64,
            "embeddingDimensions": 512,
            "embedding": [0.5] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                table = FakeTable()
                with self.assertRaises(SemanticEmbeddingContractError):
                    persist_active_embedding(
                        table,
                        semantic_chunk(),
                        stored_embedding(**{field: value}),
                    )
                self.assertEqual(table.client.calls, [])

    def test_nonconditional_aws_failures_are_retryable_and_sanitized(self):
        errors = (
            transaction_error("InternalError"),
            transaction_error(),
            transaction_error(
                reasons=[{"Code": "TransactionConflict"}, {"Code": "None"}]
            ),
            BotoCoreError(),
        )
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                table = FakeTable()
                table.client.error = error
                with self.assertRaises(SemanticEmbeddingPersistenceError) as raised:
                    persist_active_embedding(
                        table,
                        semantic_chunk(),
                        stored_embedding(),
                    )
                self.assertNotIsInstance(
                    raised.exception,
                    SemanticEmbeddingStaleGenerationError,
                )
                message = str(raised.exception)
                for private in (USER_ID, ENTRY_ID, TEXT, CONTENT_DIGEST):
                    self.assertNotIn(private, message)
                self.assertIsNone(raised.exception.__cause__)

    def test_invalid_response_is_rejected(self):
        table = FakeTable()
        table.client.response = []
        with self.assertRaises(SemanticEmbeddingPersistenceError):
            persist_active_embedding(
                table,
                semantic_chunk(),
                stored_embedding(),
            )


if __name__ == "__main__":
    unittest.main()
