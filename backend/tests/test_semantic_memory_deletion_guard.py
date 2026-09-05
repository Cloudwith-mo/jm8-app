from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

from semantic_memory_deletion_guard import (  # noqa: E402
    GUARD_ENTITY_TYPE,
    GUARD_STATUS,
    GUARD_VERSION,
    SemanticMemoryDeletionGuardError,
    SemanticMemoryDeletionGuardIntegrityError,
    build_semantic_deletion_guard,
    put_semantic_deletion_guard,
    semantic_deletion_guard_exists,
    subject_digest,
)
import semantic_memory_lifecycle as lifecycle  # noqa: E402


SUBJECT = "cognito-subject-private"


class FakeGuardTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}
        self.put_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []

    def put_item(self, **kwargs: object) -> dict[str, object]:
        item = dict(kwargs["Item"])  # type: ignore[arg-type]
        self.put_calls.append(kwargs)
        self.items[(item["PK"], item["SK"])] = item  # type: ignore[index]
        return {}

    def get_item(self, **kwargs: object) -> dict[str, object]:
        self.get_calls.append(kwargs)
        key = kwargs["Key"]  # type: ignore[assignment]
        item = self.items.get((key["PK"], key["SK"]))  # type: ignore[index]
        return {"Item": dict(item)} if item is not None else {}


class SemanticMemoryDeletionGuardTests(unittest.TestCase):
    def test_digest_key_and_record_are_exact_and_privacy_safe(self):
        digest = sha256(SUBJECT.encode("utf-8")).hexdigest()
        self.assertEqual(subject_digest(SUBJECT), digest)
        guard = build_semantic_deletion_guard(SUBJECT)
        self.assertEqual(guard, {
            "PK": f"DELETED_SUBJECT#{digest}",
            "SK": "SEMANTIC_MEMORY_GUARD",
            "entityType": GUARD_ENTITY_TYPE,
            "subjectDigest": digest,
            "status": GUARD_STATUS,
            "guardVersion": GUARD_VERSION,
        })
        self.assertEqual(set(guard), {
            "PK", "SK", "entityType", "subjectDigest", "status", "guardVersion",
        })
        self.assertNotIn(SUBJECT, repr(guard))

    def test_put_and_lookup_are_idempotent_and_consistent(self):
        table = FakeGuardTable()
        first = put_semantic_deletion_guard(table, SUBJECT)
        second = put_semantic_deletion_guard(table, SUBJECT)
        self.assertEqual(first, second)
        self.assertTrue(semantic_deletion_guard_exists(table, SUBJECT))
        self.assertEqual(len(table.items), 1)
        self.assertTrue(all(call["ConsistentRead"] for call in table.get_calls))

    def test_absent_guard_returns_false(self):
        self.assertFalse(semantic_deletion_guard_exists(FakeGuardTable(), SUBJECT))

    def test_malformed_subjects_are_rejected(self):
        for subject in (None, 1, "", "   "):
            with self.subTest(subject=subject), self.assertRaises(ValueError):
                build_semantic_deletion_guard(subject)  # type: ignore[arg-type]

    def test_invalid_persisted_guard_fails_closed(self):
        table = FakeGuardTable()
        guard = build_semantic_deletion_guard(SUBJECT)
        guard["status"] = "ACTIVE"
        table.items[(guard["PK"], guard["SK"])] = guard
        with self.assertRaises(SemanticMemoryDeletionGuardIntegrityError):
            semantic_deletion_guard_exists(table, SUBJECT)

    def test_aws_failures_are_privacy_safe(self):
        private_message = f"aws failure {SUBJECT} private@example.com"

        class FailingTable:
            def put_item(self, **_kwargs: object) -> None:
                raise ClientError(
                    {"Error": {"Code": "InternalServerError", "Message": private_message}},
                    "PutItem",
                )

            def get_item(self, **_kwargs: object) -> None:
                raise ClientError(
                    {"Error": {"Code": "InternalServerError", "Message": private_message}},
                    "GetItem",
                )

        for operation in (
            lambda: put_semantic_deletion_guard(FailingTable(), SUBJECT),
            lambda: semantic_deletion_guard_exists(FailingTable(), SUBJECT),
        ):
            with self.subTest(operation=operation), self.assertRaises(
                SemanticMemoryDeletionGuardError
            ) as raised:
                operation()
            message = str(raised.exception)
            self.assertNotIn(SUBJECT, message)
            self.assertNotIn("private@example.com", message)
            self.assertNotIn("InternalServerError", message)

    def test_deletion_guard_closes_delayed_stream_replacement_race(self):
        table = FakeGuardTable()
        serializer = TypeSerializer()
        entry_id = "entry-race"
        entry = {
            "entityType": "ENTRY",
            "entryId": entry_id,
            "userId": SUBJECT,
            "sourceType": "typed",
            "status": "REVIEWED",
            "rawText": "private delayed journal text",
        }
        record = {
            "eventID": "diagnostic-race",
            "eventName": "INSERT",
            "eventSource": "aws:dynamodb",
            "dynamodb": {
                "SequenceNumber": "000000000001",
                "NewImage": {
                    key: serializer.serialize(value)
                    for key, value in entry.items()
                },
            },
        }

        def purge_user_partition() -> None:
            for key in list(table.items):
                if key[0] == f"USER#{SUBJECT}":
                    del table.items[key]

        def delayed_replace(_table: object, _entry: object) -> dict[str, object]:
            # Deletion wins the guard race, but this already-admitted callback
            # can still leave temporary residue before verification retries.
            put_semantic_deletion_guard(table, SUBJECT)
            purge_user_partition()
            table.items[(f"USER#{SUBJECT}", "ENTRY#opaque#MANIFEST")] = {
                "PK": f"USER#{SUBJECT}",
                "SK": "ENTRY#opaque#MANIFEST",
            }
            return {
                "entryId": entry_id,
                "userId": SUBJECT,
                "contentDigest": "a" * 64,
                "chunkCount": 1,
                "chunkingVersion": "jm8-semantic-chunk-v1",
                "status": "ACTIVE",
            }

        replace = Mock(side_effect=delayed_replace)
        def exact_entry_delete(_table: object, user_id: str, _entry_id: str) -> int:
            self.assertEqual(user_id, SUBJECT)
            purge_user_partition()
            return 1

        with patch.object(lifecycle, "replace_entry_memory", replace), patch.object(
            lifecycle, "delete_entry_memory", side_effect=exact_entry_delete
        ) as delete:
            first = lifecycle.apply_entry_stream_record(table, record)
        self.assertEqual(first["status"], lifecycle.DELETION_GUARDED)
        delete.assert_called_once_with(table, SUBJECT, entry_id)
        self.assertFalse(any(key[0] == f"USER#{SUBJECT}" for key in table.items))

        with patch.object(lifecycle, "replace_entry_memory", replace), patch.object(
            lifecycle, "delete_entry_memory", side_effect=exact_entry_delete
        ):
            replay = lifecycle.apply_entry_stream_record(table, record)

        self.assertEqual(replay["status"], lifecycle.DELETION_GUARDED)
        self.assertEqual(replace.call_count, 1)
        self.assertFalse(any(key[0] == f"USER#{SUBJECT}" for key in table.items))
        self.assertEqual(list(table.items.values()), [
            build_semantic_deletion_guard(SUBJECT)
        ])


if __name__ == "__main__":
    unittest.main()
