import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import boto3
from botocore.exceptions import ClientError


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))


from semantic_memory_contract import (  # noqa: E402
    SemanticMemoryIdentityError,
    build_entry_semantic_chunks,
)
from semantic_memory_store import (  # noqa: E402
    CHUNK_ENTITY_TYPE,
    MANIFEST_ENTITY_TYPE,
    SemanticMemoryConflictError,
    SemanticMemoryIntegrityError,
    SemanticMemoryStoreError,
    delete_entry_memory,
    delete_user_memory,
    get_entry_memory,
    replace_entry_memory,
)


def aws_error(operation: str = "Operation") -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "InternalError",
                "Message": (
                    "secret journal text for user-secret entry-secret "
                    "request-payload-secret"
                ),
            }
        },
        operation,
    )


def conditional_error() -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "conditional AWS message with secret request payload",
            }
        },
        "PutItem",
    )


def reviewed_entry(**changes: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "entryId": "entry-123",
        "userId": "user-456",
        "sourceType": "typed",
        "status": "REVIEWED",
        "rawText": (
            "Alpha thought. Beta thought. Gamma thought. Delta thought. "
            "Epsilon thought. Zeta thought. Eta thought."
        ),
        "createdAt": "2026-09-01T12:00:00Z",
        "updatedAt": "2026-09-02T13:00:00Z",
    }
    entry.update(changes)
    return entry


class FakeBatchClient:
    def __init__(self, table: "FakeTable"):
        self.table = table
        self.calls: list[list[dict[str, object]]] = []
        self.unprocessed_rounds = 0
        self.always_unprocessed = False
        self.fail = False

    def batch_write_item(self, *, RequestItems):
        if self.fail:
            raise aws_error("BatchWriteItem")
        requests = copy.deepcopy(RequestItems[self.table.name])
        self.calls.append(requests)
        self.table.events.append(("batch_delete", copy.deepcopy(requests)))

        native_keys = []
        for request in requests:
            raw_key = request["DeleteRequest"]["Key"]
            if not isinstance(raw_key.get("PK"), str) or not isinstance(
                raw_key.get("SK"), str
            ):
                raise AssertionError(
                    "resource-bound batch client requires native keys"
                )
            native_keys.append((raw_key["PK"], raw_key["SK"]))

        if self.always_unprocessed or self.unprocessed_rounds > 0:
            if self.unprocessed_rounds > 0:
                self.unprocessed_rounds -= 1
            return {"UnprocessedItems": {self.table.name: requests}}

        for key in native_keys:
            self.table.items.pop(key, None)
        return {"UnprocessedItems": {}}


class FakeTable:
    def __init__(self, *, page_size: int = 100):
        self.name = "semantic-memory-test"
        self.items: dict[tuple[str, str], dict[str, object]] = {}
        self.events: list[tuple[str, object]] = []
        self.page_size = page_size
        self.query_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.put_calls: list[dict[str, object]] = []
        self.put_requests: list[dict[str, object]] = []
        self.successful_manifest_puts: list[dict[str, object]] = []
        self.condition_failures = 0
        self.scan_calls = 0
        self.reverse_query_results = False
        self.fail_operation: str | None = None
        self.fail_chunk_ordinal: int | None = None
        self.before_manifest_condition = None
        self.client = FakeBatchClient(self)
        self.meta = SimpleNamespace(client=self.client)

    def put_item(self, *, Item, **kwargs):
        self.put_calls.append(copy.deepcopy(Item))
        self.put_requests.append(
            {"Item": copy.deepcopy(Item), **copy.deepcopy(kwargs)}
        )
        self.events.append(("put", copy.deepcopy(Item)))
        if self.fail_operation == "put":
            raise aws_error("PutItem")
        if (
            Item.get("entityType") == CHUNK_ENTITY_TYPE
            and Item.get("chunkOrdinal") == self.fail_chunk_ordinal
        ):
            raise aws_error("PutItem")

        if Item.get("entityType") == MANIFEST_ENTITY_TYPE and kwargs:
            if self.before_manifest_condition is not None:
                self.before_manifest_condition(self, Item, kwargs)
            current = self.items.get((Item["PK"], Item["SK"]))
            expression = kwargs.get("ConditionExpression")
            if expression == "attribute_not_exists(PK) AND attribute_not_exists(SK)":
                condition_passes = current is None
            else:
                names = kwargs.get("ExpressionAttributeNames", {})
                values = kwargs.get("ExpressionAttributeValues", {})
                condition_passes = (
                    current is not None
                    and names
                    == {
                        "#activeContentDigest": "activeContentDigest",
                        "#chunkingVersion": "chunkingVersion",
                    }
                    and current.get("activeContentDigest")
                    == values.get(":previousContentDigest")
                    and current.get("chunkingVersion")
                    == values.get(":previousChunkingVersion")
                )
            if not condition_passes:
                self.condition_failures += 1
                raise conditional_error()
        self.items[(Item["PK"], Item["SK"])] = copy.deepcopy(Item)
        if Item.get("entityType") == MANIFEST_ENTITY_TYPE:
            self.successful_manifest_puts.append(copy.deepcopy(Item))
        return {}

    def get_item(self, **kwargs):
        self.get_calls.append(copy.deepcopy(kwargs))
        self.events.append(("get", copy.deepcopy(kwargs)))
        if self.fail_operation == "get":
            raise aws_error("GetItem")
        item = self.items.get((kwargs["Key"]["PK"], kwargs["Key"]["SK"]))
        return {"Item": copy.deepcopy(item)} if item is not None else {}

    def query(self, **kwargs):
        self.query_calls.append(copy.deepcopy(kwargs))
        self.events.append(("query", copy.deepcopy(kwargs)))
        if self.fail_operation == "query":
            raise aws_error("Query")

        values = kwargs["ExpressionAttributeValues"]
        pk = values[":pk"]
        prefix = values.get(":sk_prefix")
        matches = [
            copy.deepcopy(item)
            for (item_pk, item_sk), item in sorted(self.items.items())
            if item_pk == pk and (prefix is None or item_sk.startswith(prefix))
        ]
        if self.reverse_query_results:
            matches.reverse()

        start = 0
        exclusive = kwargs.get("ExclusiveStartKey")
        if exclusive:
            exclusive_key = (exclusive["PK"], exclusive["SK"])
            for index, item in enumerate(matches):
                if (item["PK"], item["SK"]) == exclusive_key:
                    start = index + 1
                    break
        page = matches[start : start + self.page_size]
        response: dict[str, object] = {"Items": page}
        if start + self.page_size < len(matches):
            last = page[-1]
            response["LastEvaluatedKey"] = {
                "PK": last["PK"],
                "SK": last["SK"],
            }
        return response

    def scan(self, **kwargs):
        self.scan_calls += 1
        raise AssertionError("Scan must never be called")


def manifest(table: FakeTable, entry_id: str = "entry-123") -> dict[str, object]:
    token = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()
    key = ("USER#user-456", f"ENTRY#{token}#MANIFEST")
    return table.items[key]


def active_chunks(
    table: FakeTable,
    entry_id: str = "entry-123",
) -> list[dict[str, object]]:
    current = manifest(table, entry_id)
    digest = current["activeContentDigest"]
    token = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()
    prefix = f"ENTRY#{token}#GEN#{digest}#CHUNK#"
    return [
        item
        for (pk, sk), item in table.items.items()
        if pk == "USER#user-456" and sk.startswith(prefix)
    ]


class SemanticMemoryStoreTests(unittest.TestCase):
    def test_exact_pk_sk_schema_and_raw_text_absent_from_keys(self):
        table = FakeTable()
        entry = reviewed_entry(rawText="private raw journal words")

        replace_entry_memory(table, entry, max_chunk_size=12, overlap_size=2)

        token = hashlib.sha256(b"entry-123").hexdigest()
        expected_prefix = f"ENTRY#{token}#"
        stored_manifest = manifest(table)
        self.assertEqual(stored_manifest["PK"], "USER#user-456")
        self.assertEqual(stored_manifest["SK"], f"{expected_prefix}MANIFEST")
        for chunk in active_chunks(table):
            ordinal = chunk["chunkOrdinal"]
            expected_sk = (
                f"{expected_prefix}GEN#{chunk['contentDigest']}#CHUNK#"
                f"{ordinal:08d}#{chunk['chunkId']}"
            )
            self.assertEqual(chunk["PK"], "USER#user-456")
            self.assertEqual(chunk["SK"], expected_sk)
        all_keys = " ".join(pk + sk for pk, sk in table.items)
        self.assertNotIn("private", all_keys)
        self.assertNotIn("journal", all_keys)

    def test_manifest_is_published_after_every_chunk(self):
        table = FakeTable()

        replace_entry_memory(
            table,
            reviewed_entry(),
            max_chunk_size=24,
            overlap_size=4,
        )

        put_items = [value for event, value in table.events if event == "put"]
        self.assertGreater(len(put_items), 2)
        self.assertTrue(
            all(item["entityType"] == CHUNK_ENTITY_TYPE for item in put_items[:-1])
        )
        self.assertEqual(put_items[-1]["entityType"], MANIFEST_ENTITY_TYPE)

    def test_manifest_is_captured_consistently_and_published_with_cas(self):
        table = FakeTable()

        first = replace_entry_memory(table, reviewed_entry(rawText="First text."))

        first_manifest_request = next(
            request
            for request in table.put_requests
            if request["Item"]["entityType"] == MANIFEST_ENTITY_TYPE
        )
        self.assertEqual(
            first_manifest_request["ConditionExpression"],
            "attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        self.assertNotIn("ExpressionAttributeNames", first_manifest_request)
        first_get_index = next(
            index for index, event in enumerate(table.events) if event[0] == "get"
        )
        first_chunk_put_index = next(
            index
            for index, event in enumerate(table.events)
            if event[0] == "put" and event[1]["entityType"] == CHUNK_ENTITY_TYPE
        )
        self.assertLess(first_get_index, first_chunk_put_index)
        self.assertTrue(table.get_calls[0]["ConsistentRead"])

        table.put_requests.clear()
        replace_entry_memory(table, reviewed_entry(rawText="Second text."))
        replacement_request = next(
            request
            for request in table.put_requests
            if request["Item"]["entityType"] == MANIFEST_ENTITY_TYPE
        )
        self.assertEqual(
            replacement_request["ConditionExpression"],
            (
                "#activeContentDigest = :previousContentDigest "
                "AND #chunkingVersion = :previousChunkingVersion"
            ),
        )
        self.assertEqual(
            replacement_request["ExpressionAttributeNames"],
            {
                "#activeContentDigest": "activeContentDigest",
                "#chunkingVersion": "chunkingVersion",
            },
        )
        self.assertEqual(
            replacement_request["ExpressionAttributeValues"],
            {
                ":previousContentDigest": first["contentDigest"],
                ":previousChunkingVersion": "jm8-semantic-chunk-v1",
            },
        )

    def test_two_writers_from_same_manifest_only_one_different_content_wins(self):
        table = FakeTable()
        replace_entry_memory(table, reviewed_entry(rawText="Original content."))
        outer_entry = reviewed_entry(rawText="Outer writer content.")
        inner_entry = reviewed_entry(rawText="Inner writer content.")
        outer_digest = build_entry_semantic_chunks(outer_entry)[0]["contentDigest"]
        interleaving: dict[str, object] = {}
        table.events.clear()
        table.client.calls.clear()
        table.successful_manifest_puts.clear()

        def publish_inner_writer(fake, item, kwargs):
            fake.before_manifest_condition = None
            outer_keys_before_inner = {
                key
                for key, value in fake.items.items()
                if value.get("contentDigest") == outer_digest
            }
            self.assertTrue(outer_keys_before_inner)
            interleaving["outerKeys"] = outer_keys_before_inner
            interleaving["winner"] = replace_entry_memory(fake, inner_entry)
            interleaving["batchCallsAfterWinner"] = len(fake.client.calls)

        table.before_manifest_condition = publish_inner_writer

        with self.assertRaises(SemanticMemoryConflictError):
            replace_entry_memory(table, outer_entry)

        winner = interleaving["winner"]
        self.assertEqual(manifest(table)["activeContentDigest"], winner["contentDigest"])
        self.assertNotEqual(winner["contentDigest"], outer_digest)
        self.assertEqual(len(table.successful_manifest_puts), 1)
        self.assertEqual(table.condition_failures, 1)
        self.assertEqual(
            len(table.client.calls),
            interleaving["batchCallsAfterWinner"],
        )
        self.assertTrue(get_entry_memory(table, "user-456", "entry-123"))
        self.assertTrue(
            set(interleaving["outerKeys"]).isdisjoint(table.items),
        )

    def test_conditional_conflict_never_runs_stale_cleanup(self):
        table = FakeTable()
        replace_entry_memory(table, reviewed_entry(rawText="Old content."))
        table.client.calls.clear()

        def publish_different_manifest(fake, item, kwargs):
            fake.before_manifest_condition = None
            concurrent = copy.deepcopy(item)
            concurrent["activeContentDigest"] = "f" * 64
            fake.items[(item["PK"], item["SK"])] = concurrent

        table.before_manifest_condition = publish_different_manifest

        with self.assertRaises(SemanticMemoryConflictError):
            replace_entry_memory(table, reviewed_entry(rawText="Losing content."))

        self.assertEqual(table.client.calls, [])

    def test_same_digest_conflict_returns_active_only_after_integrity_validation(self):
        table = FakeTable()
        entry = reviewed_entry(rawText="Shared desired content.")
        desired = build_entry_semantic_chunks(entry)

        def publish_same_manifest(fake, item, kwargs):
            fake.before_manifest_condition = None
            fake.items[(item["PK"], item["SK"])] = copy.deepcopy(item)

        table.before_manifest_condition = publish_same_manifest

        result = replace_entry_memory(table, entry)

        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["contentDigest"], desired[0]["contentDigest"])
        self.assertEqual(result["chunkCount"], len(desired))
        self.assertEqual(table.condition_failures, 1)
        self.assertEqual(table.client.calls, [])
        self.assertGreaterEqual(len(table.get_calls), 3)
        self.assertTrue(all(call["ConsistentRead"] for call in table.get_calls))
        self.assertEqual(
            len(get_entry_memory(table, "user-456", "entry-123")),
            len(desired),
        )

    def test_same_digest_conflict_with_partial_generation_raises_integrity_error(self):
        table = FakeTable()
        entry = reviewed_entry(rawText="Shared desired content.")

        def publish_partial_same_generation(fake, item, kwargs):
            fake.before_manifest_condition = None
            chunk_key = next(
                key
                for key, value in fake.items.items()
                if value.get("entityType") == CHUNK_ENTITY_TYPE
            )
            fake.items.pop(chunk_key)
            fake.items[(item["PK"], item["SK"])] = copy.deepcopy(item)

        table.before_manifest_condition = publish_partial_same_generation

        with self.assertRaises(SemanticMemoryIntegrityError):
            replace_entry_memory(table, entry)

        self.assertEqual(table.condition_failures, 1)
        self.assertEqual(table.client.calls, [])

    def test_conflict_error_message_is_privacy_safe(self):
        table = FakeTable()
        entry = reviewed_entry(
            entryId="entry-secret",
            userId="user-secret",
            rawText="private journal conflict text",
        )
        desired_digest = build_entry_semantic_chunks(entry)[0]["contentDigest"]

        def publish_different_manifest(fake, item, kwargs):
            fake.before_manifest_condition = None
            concurrent = copy.deepcopy(item)
            concurrent["activeContentDigest"] = "a" * 64
            fake.items[(item["PK"], item["SK"])] = concurrent

        table.before_manifest_condition = publish_different_manifest

        with self.assertRaises(SemanticMemoryConflictError) as raised:
            replace_entry_memory(table, entry)

        message = str(raised.exception)
        for private_value in (
            "user-secret",
            "entry-secret",
            "private journal conflict text",
            desired_digest,
            "conditional AWS message",
            "request payload",
        ):
            self.assertNotIn(private_value, message)

    def test_replacement_is_deterministic_and_idempotent(self):
        table = FakeTable()
        entry = reviewed_entry()

        first = replace_entry_memory(table, entry, max_chunk_size=30, overlap_size=5)
        first_items = copy.deepcopy(table.items)
        second = replace_entry_memory(table, entry, max_chunk_size=30, overlap_size=5)

        self.assertEqual(first, second)
        self.assertEqual(first_items, table.items)
        self.assertEqual(first["status"], "ACTIVE")

    def test_backfill_replay_token_changes_identical_chunk_once(self):
        table = FakeTable()
        entry = reviewed_entry()

        replace_entry_memory(table, entry)
        original = copy.deepcopy(table.items)

        replace_entry_memory(
            table,
            entry,
            replay_token="jm8-semantic-backfill-v1",
        )
        replayed = copy.deepcopy(table.items)

        self.assertNotEqual(original, replayed)
        chunks = [
            item
            for item in replayed.values()
            if item.get("entityType") == CHUNK_ENTITY_TYPE
        ]
        self.assertTrue(chunks)
        self.assertTrue(all(
            item.get("replayToken")
            == "jm8-semantic-backfill-v1"
            for item in chunks
        ))

        replace_entry_memory(
            table,
            entry,
            replay_token="jm8-semantic-backfill-v1",
        )
        self.assertEqual(replayed, table.items)

    def test_same_content_replacement_reuses_generation(self):
        table = FakeTable()
        first_entry = reviewed_entry(updatedAt="old")
        second_entry = reviewed_entry(updatedAt="new")

        first = replace_entry_memory(table, first_entry)
        first_keys = set(table.items)
        second = replace_entry_memory(table, second_entry)

        self.assertEqual(first["contentDigest"], second["contentDigest"])
        self.assertEqual(first_keys, set(table.items))
        self.assertEqual(manifest(table)["entryUpdatedAt"], "new")

    def test_changed_content_replaces_and_cleans_stale_generation(self):
        table = FakeTable()
        old_result = replace_entry_memory(table, reviewed_entry(rawText="Old text."))
        old_chunk_keys = {
            key for key, item in table.items.items() if item["entityType"] == CHUNK_ENTITY_TYPE
        }

        new_result = replace_entry_memory(table, reviewed_entry(rawText="New text."))

        self.assertNotEqual(old_result["contentDigest"], new_result["contentDigest"])
        self.assertTrue(old_chunk_keys.isdisjoint(table.items))
        self.assertEqual(manifest(table)["activeContentDigest"], new_result["contentDigest"])

    def test_retry_after_cleanup_failure_removes_previous_generation(self):
        table = FakeTable()
        replace_entry_memory(table, reviewed_entry(rawText="Old private text."))
        old_chunk_keys = {
            key
            for key, item in table.items.items()
            if item["entityType"] == CHUNK_ENTITY_TYPE
        }
        updated_entry = reviewed_entry(rawText="Updated private text.")
        updated_digest = build_entry_semantic_chunks(updated_entry)[0][
            "contentDigest"
        ]
        table.client.fail = True

        with self.assertRaises(SemanticMemoryStoreError) as raised:
            replace_entry_memory(table, updated_entry)

        self.assertEqual(
            manifest(table)["activeContentDigest"],
            updated_digest,
        )
        self.assertTrue(old_chunk_keys.issubset(table.items))
        message = str(raised.exception)
        for private_value in (
            "Old private text.",
            "Updated private text.",
            "user-456",
            "entry-123",
            updated_digest,
        ):
            self.assertNotIn(private_value, message)

        table.client.fail = False
        result = replace_entry_memory(table, updated_entry)

        self.assertEqual(result["contentDigest"], updated_digest)
        self.assertTrue(old_chunk_keys.isdisjoint(table.items))
        entry_items = [
            item
            for item in table.items.values()
            if item.get("userId") == "user-456"
            and item.get("entryId") == "entry-123"
        ]
        self.assertEqual(len(entry_items), 2)
        self.assertEqual(
            [
                item["contentDigest"]
                for item in entry_items
                if item.get("entityType") == CHUNK_ENTITY_TYPE
            ],
            [updated_digest],
        )
        self.assertEqual(
            manifest(table)["activeContentDigest"],
            updated_digest,
        )

    def test_stale_cleanup_occurs_only_after_manifest_publication(self):
        table = FakeTable()
        replace_entry_memory(table, reviewed_entry(rawText="Old generation."))
        table.events.clear()

        replace_entry_memory(table, reviewed_entry(rawText="New generation."))

        manifest_put = max(
            index
            for index, event in enumerate(table.events)
            if event[0] == "put"
            and event[1]["entityType"] == MANIFEST_ENTITY_TYPE
        )
        deletes = [
            index for index, event in enumerate(table.events) if event[0] == "batch_delete"
        ]
        self.assertTrue(deletes)
        self.assertTrue(all(index > manifest_put for index in deletes))

    def test_chunk_write_failure_preserves_old_readable_generation(self):
        table = FakeTable()
        replace_entry_memory(table, reviewed_entry(rawText="Old readable generation."))
        old_manifest = copy.deepcopy(manifest(table))
        old_read = get_entry_memory(table, "user-456", "entry-123")
        table.fail_chunk_ordinal = 1

        with self.assertRaises(SemanticMemoryStoreError):
            replace_entry_memory(
                table,
                reviewed_entry(rawText="Changed text with enough words for chunks."),
                max_chunk_size=15,
                overlap_size=2,
            )

        table.fail_chunk_ordinal = None
        self.assertEqual(manifest(table), old_manifest)
        self.assertEqual(get_entry_memory(table, "user-456", "entry-123"), old_read)

    def test_get_reads_only_active_generation_and_orders_ordinals(self):
        table = FakeTable(page_size=1)
        replace_entry_memory(
            table,
            reviewed_entry(),
            max_chunk_size=24,
            overlap_size=4,
        )
        table.reverse_query_results = True
        table.query_calls.clear()

        chunks = get_entry_memory(table, "user-456", "entry-123")

        self.assertEqual(
            [chunk["chunkOrdinal"] for chunk in chunks],
            list(range(len(chunks))),
        )
        self.assertTrue(table.get_calls[-1]["ConsistentRead"])
        active_digest = manifest(table)["activeContentDigest"]
        self.assertTrue(
            all(
                call["ExpressionAttributeValues"][":sk_prefix"].endswith(
                    f"GEN#{active_digest}#CHUNK#"
                )
                for call in table.query_calls
            )
        )

    def test_missing_manifest_returns_empty(self):
        table = FakeTable()

        self.assertEqual(get_entry_memory(table, "user-456", "entry-123"), [])
        self.assertEqual(table.query_calls, [])

    def test_partial_generation_raises_integrity_error(self):
        table = FakeTable()
        replace_entry_memory(
            table,
            reviewed_entry(),
            max_chunk_size=24,
            overlap_size=4,
        )
        chunk = active_chunks(table)[0]
        table.items.pop((chunk["PK"], chunk["SK"]))

        with self.assertRaises(SemanticMemoryIntegrityError):
            get_entry_memory(table, "user-456", "entry-123")

    def test_inconsistent_chunk_fields_raise_integrity_error(self):
        mutations = {
            "identity": lambda item: item.__setitem__("entryId", "other-entry"),
            "digest": lambda item: item.__setitem__("chunkDigest", "0" * 64),
            "version": lambda item: item.__setitem__("chunkingVersion", "other"),
            "count": lambda item: item.__setitem__("chunkCount", 999),
        }

        for name, mutate in mutations.items():
            with self.subTest(name=name):
                table = FakeTable()
                replace_entry_memory(table, reviewed_entry())
                mutate(active_chunks(table)[0])
                with self.assertRaises(SemanticMemoryIntegrityError):
                    get_entry_memory(table, "user-456", "entry-123")

    def test_duplicate_ordinal_raises_integrity_error(self):
        table = FakeTable()
        replace_entry_memory(table, reviewed_entry())
        original = active_chunks(table)[0]
        duplicate = copy.deepcopy(original)
        duplicate["chunkId"] = "chunk_" + "f" * 64
        duplicate["SK"] = duplicate["SK"].rsplit("#", 1)[0] + "#" + duplicate["chunkId"]
        table.items[(duplicate["PK"], duplicate["SK"])] = duplicate

        with self.assertRaises(SemanticMemoryIntegrityError):
            get_entry_memory(table, "user-456", "entry-123")

    def test_manifest_inconsistency_raises_integrity_error(self):
        mutations = (
            ("entityType", "WRONG"),
            ("entryId", "wrong-entry"),
            ("activeContentDigest", "not-a-digest"),
            ("chunkingVersion", "wrong-version"),
            ("chunkCount", True),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                table = FakeTable()
                replace_entry_memory(table, reviewed_entry())
                manifest(table)[field] = value
                with self.assertRaises(SemanticMemoryIntegrityError):
                    get_entry_memory(table, "user-456", "entry-123")

    def test_query_pagination_is_followed(self):
        table = FakeTable(page_size=2)
        result = replace_entry_memory(
            table,
            reviewed_entry(),
            max_chunk_size=18,
            overlap_size=3,
        )
        table.query_calls.clear()

        chunks = get_entry_memory(table, "user-456", "entry-123")

        self.assertEqual(len(chunks), result["chunkCount"])
        self.assertGreater(len(table.query_calls), 1)
        self.assertTrue(
            all("ExclusiveStartKey" in call for call in table.query_calls[1:])
        )

    def test_user_deletion_uses_batches_of_at_most_25(self):
        table = FakeTable(page_size=9)
        for index in range(61):
            item = {
                "PK": "USER#user-456",
                "SK": f"ENTRY#token-{index:03d}#MANIFEST",
            }
            table.items[(item["PK"], item["SK"])] = item

        deleted = delete_user_memory(table, "user-456")

        self.assertEqual(deleted, 61)
        self.assertEqual([len(call) for call in table.client.calls], [25, 25, 11])
        self.assertEqual(table.items, {})
        self.assertGreater(len(table.query_calls), 1)

    def test_resource_bound_batch_client_receives_native_keys(self):
        table = FakeTable()
        item = {"PK": "USER#user-456", "SK": "ENTRY#opaque#MANIFEST"}
        table.items[(item["PK"], item["SK"])] = copy.deepcopy(item)

        delete_user_memory(table, "user-456")

        sent_key = table.client.calls[0][0]["DeleteRequest"]["Key"]
        self.assertEqual(sent_key, item)
        self.assertIsInstance(sent_key["PK"], str)
        self.assertIsInstance(sent_key["SK"], str)

    def test_boto3_resource_client_serializes_native_keys_at_wire_boundary(self):
        class RequestCaptured(Exception):
            pass

        resource = boto3.resource(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        table = resource.Table("semantic-memory-test")
        captured: dict[str, object] = {}

        def capture_request(model, params, **kwargs):
            captured.update(params)
            raise RequestCaptured

        table.meta.client.meta.events.register_first(
            "before-call.dynamodb.BatchWriteItem",
            capture_request,
        )

        with self.assertRaises(RequestCaptured):
            table.meta.client.batch_write_item(
                RequestItems={
                    table.name: [{
                        "DeleteRequest": {
                            "Key": {
                                "PK": "USER#user-456",
                                "SK": "ENTRY#opaque#MANIFEST",
                            }
                        }
                    }]
                }
            )

        wire_request = json.loads(captured["body"])
        wire_key = wire_request["RequestItems"][table.name][0][
            "DeleteRequest"
        ]["Key"]
        self.assertEqual(
            wire_key,
            {
                "PK": {"S": "USER#user-456"},
                "SK": {"S": "ENTRY#opaque#MANIFEST"},
            },
        )

    def test_resource_bound_batch_client_rejects_serialized_keys(self):
        table = FakeTable()
        request_items = {
            table.name: [{
                "DeleteRequest": {
                    "Key": {
                        "PK": {"S": "USER#user-456"},
                        "SK": {"S": "ENTRY#opaque#MANIFEST"},
                    }
                }
            }]
        }

        with self.assertRaisesRegex(
            AssertionError,
            "resource-bound batch client requires native keys",
        ):
            table.client.batch_write_item(RequestItems=request_items)

    def test_unprocessed_items_use_bounded_exponential_retries(self):
        table = FakeTable()
        item = {"PK": "USER#user-456", "SK": "ENTRY#opaque#MANIFEST"}
        table.items[(item["PK"], item["SK"])] = item
        table.client.unprocessed_rounds = 2

        with patch("semantic_memory_store.sleep") as sleeper:
            delete_user_memory(table, "user-456")

        self.assertEqual(len(table.client.calls), 3)
        self.assertEqual([call.args[0] for call in sleeper.call_args_list], [0.01, 0.02])
        self.assertEqual(table.client.calls[1:], table.client.calls[:1] * 2)
        for call in table.client.calls:
            key = call[0]["DeleteRequest"]["Key"]
            self.assertEqual(key, item)
            self.assertIsInstance(key["PK"], str)
            self.assertIsInstance(key["SK"], str)

        failing = FakeTable()
        private_item = {
            "PK": "USER#user-secret",
            "SK": "ENTRY#entry-secret#MANIFEST",
        }
        failing.items[(private_item["PK"], private_item["SK"])] = copy.deepcopy(
            private_item
        )
        failing.client.always_unprocessed = True
        with patch("semantic_memory_store.sleep"), self.assertRaises(
            SemanticMemoryStoreError
        ) as raised:
            delete_user_memory(failing, "user-secret")
        self.assertEqual(len(failing.client.calls), 5)
        self.assertEqual(
            str(raised.exception),
            "semantic memory deletion retry failed",
        )
        self.assertNotIn("user-secret", str(raised.exception))
        self.assertNotIn("entry-secret", str(raised.exception))

    def test_entry_deletion_is_isolated(self):
        table = FakeTable()
        replace_entry_memory(table, reviewed_entry(entryId="entry-a"))
        replace_entry_memory(table, reviewed_entry(entryId="entry-b"))
        replace_entry_memory(
            table,
            reviewed_entry(entryId="entry-a", userId="other-user"),
        )

        delete_entry_memory(table, "user-456", "entry-a")

        self.assertEqual(get_entry_memory(table, "user-456", "entry-a"), [])
        self.assertTrue(get_entry_memory(table, "user-456", "entry-b"))
        self.assertTrue(get_entry_memory(table, "other-user", "entry-a"))

    def test_user_deletion_is_isolated(self):
        table = FakeTable()
        replace_entry_memory(table, reviewed_entry(userId="user-a"))
        replace_entry_memory(table, reviewed_entry(userId="user-b"))

        delete_user_memory(table, "user-a")

        self.assertEqual(get_entry_memory(table, "user-a", "entry-123"), [])
        self.assertTrue(get_entry_memory(table, "user-b", "entry-123"))

    def test_ineligible_entry_removes_existing_memory(self):
        table = FakeTable()
        replace_entry_memory(table, reviewed_entry())

        result = replace_entry_memory(
            table,
            reviewed_entry(status="DRAFT", rawText="Unreviewed text."),
        )

        self.assertEqual(result["status"], "DELETED")
        self.assertIsNone(result["contentDigest"])
        self.assertEqual(result["chunkCount"], 0)
        self.assertEqual(get_entry_memory(table, "user-456", "entry-123"), [])

    def test_malformed_identities_and_boolean_configuration_are_rejected(self):
        table = FakeTable()
        invalid_entries = (
            reviewed_entry(entryId=""),
            reviewed_entry(entryId=123),
            reviewed_entry(userId=" \t"),
            reviewed_entry(userId=None),
        )
        for entry in invalid_entries:
            with self.subTest(entry=entry):
                with self.assertRaises(SemanticMemoryIdentityError):
                    replace_entry_memory(table, entry)

        for user_id, entry_id in (("", "entry"), ("user", ""), (None, "entry")):
            with self.subTest(user_id=user_id, entry_id=entry_id):
                with self.assertRaises(SemanticMemoryIdentityError):
                    get_entry_memory(table, user_id, entry_id)

        for max_size, overlap in ((True, 0), (10, False)):
            with self.subTest(max_size=max_size, overlap=overlap):
                with self.assertRaises(SemanticMemoryStoreError):
                    replace_entry_memory(
                        table,
                        reviewed_entry(status="DRAFT"),
                        max_chunk_size=max_size,
                        overlap_size=overlap,
                    )

    def test_aws_failures_are_privacy_safe(self):
        operations = []

        put_table = FakeTable()
        put_table.fail_operation = "put"
        operations.append(lambda: replace_entry_memory(put_table, reviewed_entry()))

        get_table = FakeTable()
        get_table.fail_operation = "get"
        operations.append(lambda: get_entry_memory(get_table, "user-secret", "entry-secret"))

        query_table = FakeTable()
        query_table.fail_operation = "query"
        operations.append(lambda: delete_entry_memory(query_table, "user-secret", "entry-secret"))

        batch_table = FakeTable()
        item = {"PK": "USER#user-secret", "SK": "ENTRY#opaque#MANIFEST"}
        batch_table.items[(item["PK"], item["SK"])] = item
        batch_table.client.fail = True
        operations.append(lambda: delete_user_memory(batch_table, "user-secret"))

        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(SemanticMemoryStoreError) as raised:
                    operation()
                message = str(raised.exception)
                for secret in (
                    "journal",
                    "user-secret",
                    "entry-secret",
                    "request-payload-secret",
                    "InternalError",
                ):
                    self.assertNotIn(secret, message)

    def test_no_scan_is_used_and_entry_is_not_mutated(self):
        table = FakeTable(page_size=1)
        entry = reviewed_entry(nested={"values": [1, 2, 3]})
        original = copy.deepcopy(entry)

        replace_entry_memory(table, entry, max_chunk_size=25, overlap_size=4)
        get_entry_memory(table, "user-456", "entry-123")
        delete_entry_memory(table, "user-456", "entry-123")
        delete_user_memory(table, "user-456")

        self.assertEqual(table.scan_calls, 0)
        self.assertEqual(entry, original)
        source = (FUNCTION_DIR / "semantic_memory_store.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".scan(", source)
        for forbidden in (
            "boto3",
            "os.environ",
            "getenv(",
            "datetime.now",
            "time.time",
            "uuid",
            "random",
            "Bedrock",
            "embedding",
            "vector",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
