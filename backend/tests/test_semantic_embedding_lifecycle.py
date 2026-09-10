from __future__ import annotations

import copy
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from boto3.dynamodb.types import TypeSerializer


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

import semantic_embedding_lifecycle as lifecycle
from semantic_embedding_contract import (
    EMBEDDING_DIMENSIONS,
    SemanticEmbeddingContractError,
    validate_embedding_response,
)
from semantic_embedding_lifecycle import (
    CURRENT,
    EMBEDDED,
    IGNORED,
    STALE,
    SemanticEmbeddingLifecycleError,
    apply_embedding_stream_record,
    build_embedding_telemetry_document,
    process_embedding_stream_event,
    process_embedding_stream_event_with_telemetry,
)
from semantic_embedding_provider import SemanticEmbeddingProviderError
from semantic_embedding_store import (
    SemanticEmbeddingPersistenceError,
    SemanticEmbeddingStaleGenerationError,
)
from semantic_memory_store import (
    CHUNK_ENTITY_TYPE,
    MANIFEST_ENTITY_TYPE,
    SemanticMemoryIntegrityError,
    SemanticMemoryStoreError,
)


SERIALIZER = TypeSerializer()
USER_ID = "subject-123"
ENTRY_ID = "entry-456"
CHUNK_ID = "chunk_" + "c" * 64
CONTENT_DIGEST = "a" * 64
PK = f"USER#{USER_ID}"
SK = f"ENTRY#opaque#GEN#{CONTENT_DIGEST}#CHUNK#00000000#{CHUNK_ID}"


def stream_image(values):
    return {
        name: SERIALIZER.serialize(value)
        for name, value in values.items()
    }


def semantic_chunk(**changes):
    chunk = {
        "PK": PK,
        "SK": SK,
        "entityType": CHUNK_ENTITY_TYPE,
        "userId": USER_ID,
        "entryId": ENTRY_ID,
        "chunkId": CHUNK_ID,
        "chunkOrdinal": 0,
        "chunkCount": 1,
        "chunkDigest": "b" * 64,
        "contentDigest": CONTENT_DIGEST,
        "generationId": CONTENT_DIGEST,
        "chunkingVersion": "jm8-semantic-chunk-v1",
        "text": "Private active journal chunk.",
        "characterCount": 29,
        "wordCount": 4,
        "sourceType": "typed",
        "canonicalTextField": "cleanText",
    }
    chunk.update(changes)
    return chunk


def stored_embedding():
    embedding = validate_embedding_response(
        {
            "embedding": [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
            "inputTextTokenCount": 8,
        },
        user_id=USER_ID,
        content_digest=CONTENT_DIGEST,
    )
    embedding["embedding"] = [Decimal("1")] + [
        Decimal("0")
    ] * (EMBEDDING_DIMENSIONS - 1)
    return embedding


def stream_record(
    event_id="event-1",
    event_name="INSERT",
    *,
    image=None,
    sequence_number="sequence-1",
    event_source="aws:dynamodb",
):
    dynamodb = {"SequenceNumber": sequence_number}
    if image is not None:
        key = "OldImage" if event_name == "REMOVE" else "NewImage"
        dynamodb[key] = image
    return {
        "eventID": event_id,
        "eventName": event_name,
        "eventSource": event_source,
        "dynamodb": dynamodb,
    }


class TestEmbeddingStreamRecord(unittest.TestCase):
    def test_active_unembedded_chunk_is_embedded_and_persisted(self):
        table = object()
        client = object()
        chunk = semantic_chunk()
        embedding = stored_embedding()
        record = stream_record(image=stream_image(chunk))

        with patch.object(
            lifecycle,
            "get_entry_memory",
            return_value=[chunk],
        ) as read, patch.object(
            lifecycle,
            "embed_canonical_text",
            return_value=embedding,
        ) as embed, patch.object(
            lifecycle,
            "persist_active_embedding",
        ) as persist:
            result = apply_embedding_stream_record(table, client, record)

        read.assert_called_once_with(table, USER_ID, ENTRY_ID)
        embed.assert_called_once_with(
            client,
            canonical_text=chunk["text"],
            user_id=USER_ID,
            content_digest=CONTENT_DIGEST,
        )
        persist.assert_called_once_with(table, chunk, embedding)
        self.assertEqual(result, {
            "eventId": "event-1",
            "eventName": "INSERT",
            "entryId": ENTRY_ID,
            "chunkId": CHUNK_ID,
            "status": EMBEDDED,
        })

    def test_current_embedding_modify_is_terminal_and_prevents_recursion(self):
        table = object()
        chunk = semantic_chunk(**stored_embedding())
        chunk["embeddingInputTokenCount"] = Decimal("8")
        record = stream_record(
            event_name="MODIFY",
            image=stream_image(chunk),
        )

        with patch.object(
            lifecycle,
            "get_entry_memory",
            return_value=[chunk],
        ), patch.object(lifecycle, "embed_canonical_text") as embed, patch.object(
            lifecycle,
            "persist_active_embedding",
        ) as persist:
            result = apply_embedding_stream_record(table, object(), record)

        embed.assert_not_called()
        persist.assert_not_called()
        self.assertEqual(result["status"], CURRENT)

    def test_active_table_state_is_used_instead_of_stale_stream_image(self):
        stream_chunk = semantic_chunk(**stored_embedding())
        active_chunk = semantic_chunk()
        record = stream_record(
            event_name="MODIFY",
            image=stream_image(stream_chunk),
        )

        with patch.object(
            lifecycle,
            "get_entry_memory",
            return_value=[active_chunk],
        ), patch.object(
            lifecycle,
            "embed_canonical_text",
            return_value=stored_embedding(),
        ) as embed, patch.object(lifecycle, "persist_active_embedding"):
            result = apply_embedding_stream_record(object(), object(), record)

        embed.assert_called_once()
        self.assertEqual(result["status"], EMBEDDED)

    def test_noncurrent_embedding_is_regenerated(self):
        chunk = semantic_chunk(**stored_embedding())
        chunk["embeddingVersion"] = "obsolete-version"
        record = stream_record(
            event_name="MODIFY",
            image=stream_image(chunk),
        )

        with patch.object(
            lifecycle,
            "get_entry_memory",
            return_value=[chunk],
        ), patch.object(
            lifecycle,
            "embed_canonical_text",
            return_value=stored_embedding(),
        ) as embed, patch.object(lifecycle, "persist_active_embedding"):
            result = apply_embedding_stream_record(object(), object(), record)

        embed.assert_called_once()
        self.assertEqual(result["status"], EMBEDDED)

    def test_chunk_absent_from_active_generation_is_terminal_stale(self):
        record = stream_record(image=stream_image(semantic_chunk()))
        other = semantic_chunk(PK="USER#other", SK="other")

        with patch.object(
            lifecycle,
            "get_entry_memory",
            return_value=[other],
        ), patch.object(lifecycle, "embed_canonical_text") as embed, patch.object(
            lifecycle,
            "persist_active_embedding",
        ) as persist:
            result = apply_embedding_stream_record(object(), object(), record)

        embed.assert_not_called()
        persist.assert_not_called()
        self.assertEqual(result["status"], STALE)

    def test_persistence_race_is_terminal_stale(self):
        chunk = semantic_chunk()
        record = stream_record(image=stream_image(chunk))

        with patch.object(
            lifecycle,
            "get_entry_memory",
            return_value=[chunk],
        ), patch.object(
            lifecycle,
            "embed_canonical_text",
            return_value=stored_embedding(),
        ), patch.object(
            lifecycle,
            "persist_active_embedding",
            side_effect=SemanticEmbeddingStaleGenerationError("safe"),
        ):
            result = apply_embedding_stream_record(object(), object(), record)

        self.assertEqual(result["status"], STALE)

    def test_remove_is_ignored_without_reading_or_invoking(self):
        record = stream_record(event_name="REMOVE")
        with patch.object(lifecycle, "get_entry_memory") as read, patch.object(
            lifecycle,
            "embed_canonical_text",
        ) as embed, patch.object(
            lifecycle,
            "persist_active_embedding",
        ) as persist:
            result = apply_embedding_stream_record(object(), object(), record)
        read.assert_not_called()
        embed.assert_not_called()
        persist.assert_not_called()
        self.assertEqual(result["status"], IGNORED)

    def test_nonchunk_records_are_ignored(self):
        for entity_type in (MANIFEST_ENTITY_TYPE, "SEMANTIC_MEMORY_DELETION_GUARD"):
            with self.subTest(entity_type=entity_type):
                record = stream_record(
                    image=stream_image({
                        "PK": "opaque",
                        "SK": "opaque",
                        "entityType": entity_type,
                    })
                )
                with patch.object(lifecycle, "get_entry_memory") as read:
                    result = apply_embedding_stream_record(
                        object(),
                        object(),
                        record,
                    )
                read.assert_not_called()
                self.assertEqual(result["status"], IGNORED)

    def test_matching_uses_both_partition_and_sort_keys(self):
        chunk = semantic_chunk()
        same_sort_key = semantic_chunk(PK="USER#another")
        same_partition = semantic_chunk(SK="another")
        record = stream_record(image=stream_image(chunk))

        with patch.object(
            lifecycle,
            "get_entry_memory",
            return_value=[same_sort_key, same_partition, chunk],
        ), patch.object(
            lifecycle,
            "embed_canonical_text",
            return_value=stored_embedding(),
        ), patch.object(lifecycle, "persist_active_embedding") as persist:
            apply_embedding_stream_record(object(), object(), record)

        self.assertIs(persist.call_args.args[1], chunk)


class TestEmbeddingBatchProcessing(unittest.TestCase):
    def test_retryable_failures_return_ordered_unique_checkpoints(self):
        records = [
            stream_record("event-a", sequence_number="sequence-a"),
            stream_record("event-b", sequence_number="sequence-b"),
            stream_record("event-c", sequence_number="sequence-b"),
            stream_record("event-d", sequence_number="sequence-d"),
        ]
        outcomes = [
            SemanticEmbeddingProviderError("safe"),
            None,
            SemanticEmbeddingPersistenceError("safe"),
            None,
        ]

        def apply(*args):
            outcome = outcomes.pop(0)
            if outcome is not None:
                raise outcome
            return {"eventName": "INSERT", "status": EMBEDDED}

        with patch.object(
            lifecycle,
            "apply_embedding_stream_record",
            side_effect=apply,
        ):
            response = process_embedding_stream_event(
                object(),
                object(),
                {"Records": records},
            )

        self.assertEqual(response, {
            "batchItemFailures": [
                {"itemIdentifier": "sequence-a"},
                {"itemIdentifier": "sequence-b"},
            ]
        })

    def test_terminal_stale_result_is_not_a_batch_failure(self):
        chunk = semantic_chunk()
        record = stream_record(image=stream_image(chunk))
        with patch.object(
            lifecycle,
            "get_entry_memory",
            return_value=[],
        ):
            response = process_embedding_stream_event(
                object(),
                object(),
                {"Records": [record]},
            )
        self.assertEqual(response, {"batchItemFailures": []})

    def test_failures_have_privacy_safe_aggregate_categories(self):
        records = [
            stream_record(
                f"event-{index}",
                sequence_number=f"sequence-{index}",
            )
            for index in range(5)
        ]
        errors = [
            SemanticMemoryStoreError("private memory detail"),
            SemanticEmbeddingProviderError("private provider detail"),
            SemanticEmbeddingPersistenceError("private persistence detail"),
            SemanticEmbeddingContractError("private contract detail"),
            RuntimeError("private unexpected detail"),
        ]

        with patch.object(
            lifecycle,
            "apply_embedding_stream_record",
            side_effect=errors,
        ):
            response, telemetry = process_embedding_stream_event_with_telemetry(
                object(),
                object(),
                {"Records": records},
            )

        self.assertEqual(len(response["batchItemFailures"]), 5)
        self.assertEqual(telemetry["recordCount"], 5)
        self.assertEqual(telemetry["failureCount"], 5)
        self.assertEqual(telemetry["failureCounts"], {
            "MemoryReadFailures": 1,
            "ProviderFailures": 1,
            "PersistenceFailures": 1,
            "ContractFailures": 1,
            "UnexpectedFailures": 1,
        })
        self.assertNotIn("private", repr(telemetry))

    def test_failure_telemetry_is_a_bounded_cloudwatch_emf_document(self):
        telemetry = {
            "recordCount": 3,
            "failureCount": 1,
            "failureCounts": {
                "MemoryReadFailures": 1,
                "ProviderFailures": 0,
                "PersistenceFailures": 0,
                "ContractFailures": 0,
                "UnexpectedFailures": 0,
            },
        }

        document = build_embedding_telemetry_document(
            telemetry,
            function_name="journalm8-prod-semantic-embedding-worker",
            timestamp_ms=123456789,
        )

        self.assertEqual(
            document["_aws"]["CloudWatchMetrics"][0]["Namespace"],
            "JournalM8/SemanticEmbedding",
        )
        self.assertEqual(document["RecordCount"], 3)
        self.assertEqual(document["RecordFailures"], 1)
        self.assertEqual(document["MemoryReadFailures"], 1)
        serialized = repr(document)
        for forbidden in ("userId", "entryId", "chunkId", "text", "digest"):
            self.assertNotIn(forbidden, serialized)

    def test_failure_without_sequence_number_fails_closed(self):
        record = stream_record(sequence_number=None)
        with patch.object(
            lifecycle,
            "apply_embedding_stream_record",
            side_effect=RuntimeError("private journal text"),
        ), self.assertRaises(SemanticEmbeddingLifecycleError) as raised:
            process_embedding_stream_event(
                object(),
                object(),
                {"Records": [record]},
            )
        self.assertEqual(
            str(raised.exception),
            "stream batch checkpoint cannot be established safely",
        )
        self.assertNotIn("private", str(raised.exception))

    def test_empty_batch_succeeds(self):
        self.assertEqual(
            process_embedding_stream_event(
                object(),
                object(),
                {"Records": []},
            ),
            {"batchItemFailures": []},
        )


class TestEmbeddingLifecycleValidation(unittest.TestCase):
    def test_invalid_batches_are_rejected(self):
        for event in (None, [], {}, {"Records": None}, {"Records": {}}):
            with self.subTest(event=event):
                with self.assertRaises(SemanticEmbeddingLifecycleError):
                    process_embedding_stream_event(object(), object(), event)

    def test_invalid_record_envelopes_are_rejected(self):
        records = (
            None,
            [],
            {},
            stream_record(event_name="UNKNOWN"),
            stream_record(event_source="aws:sqs"),
            {"eventName": "INSERT", "dynamodb": None},
        )
        for record in records:
            with self.subTest(record=record):
                with self.assertRaises(SemanticEmbeddingLifecycleError):
                    apply_embedding_stream_record(object(), object(), record)

    def test_malformed_images_are_rejected_before_active_read(self):
        invalid_images = (
            None,
            [],
            {"entityType": {"S": 7}},
            {"entityType": {"UNKNOWN": "value"}},
            {"entityType": {"S": CHUNK_ENTITY_TYPE}, "bad": {"S": "x", "N": "1"}},
        )
        for image in invalid_images:
            with self.subTest(image=image), patch.object(
                lifecycle,
                "get_entry_memory",
            ) as read:
                with self.assertRaises(SemanticEmbeddingLifecycleError):
                    apply_embedding_stream_record(
                        object(),
                        object(),
                        stream_record(image=image),
                    )
                read.assert_not_called()

    def test_malformed_chunk_identity_is_rejected_before_active_read(self):
        changes = {
            "userId": "",
            "entryId": None,
            "PK": "USER#another-subject",
            "SK": "",
        }
        for field, value in changes.items():
            with self.subTest(field=field), patch.object(
                lifecycle,
                "get_entry_memory",
            ) as read:
                with self.assertRaises(SemanticEmbeddingLifecycleError):
                    apply_embedding_stream_record(
                        object(),
                        object(),
                        stream_record(
                            image=stream_image(
                                semantic_chunk(**{field: value})
                            )
                        ),
                    )
                read.assert_not_called()

    def test_invalid_active_lookup_results_fail_before_provider_call(self):
        chunk = semantic_chunk()
        record = stream_record(image=stream_image(chunk))
        invalid_results = (None, {}, (chunk,), [chunk, copy.deepcopy(chunk)])
        for active in invalid_results:
            with self.subTest(active_type=type(active).__name__), patch.object(
                lifecycle,
                "get_entry_memory",
                return_value=active,
            ), patch.object(lifecycle, "embed_canonical_text") as embed:
                with self.assertRaises(SemanticEmbeddingLifecycleError):
                    apply_embedding_stream_record(object(), object(), record)
                embed.assert_not_called()

    def test_active_read_failures_are_batch_retryable(self):
        record = stream_record(image=stream_image(semantic_chunk()))
        with patch.object(
            lifecycle,
            "get_entry_memory",
            side_effect=SemanticMemoryIntegrityError("safe"),
        ):
            response = process_embedding_stream_event(
                object(),
                object(),
                {"Records": [record]},
            )
        self.assertEqual(response, {
            "batchItemFailures": [{"itemIdentifier": "sequence-1"}],
        })

    def test_source_has_no_ambient_clients_or_nondeterministic_state(self):
        source = (
            FUNCTION_DIR / "semantic_embedding_lifecycle.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "boto3.client",
            "boto3.resource",
            "os.environ",
            "getenv(",
            "datetime.now",
            "time.time",
            "uuid",
            "random",
            ".scan(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
