from __future__ import annotations

import copy
from decimal import Decimal
import inspect
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from boto3.dynamodb.types import TypeSerializer


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))


import semantic_memory_lifecycle as lifecycle  # noqa: E402
from semantic_memory_lifecycle import (  # noqa: E402
    ACTIVE,
    DELETED,
    IGNORED,
    SemanticMemoryLifecycleError,
    apply_entry_stream_record,
    process_entry_stream_event,
)
from semantic_memory_store import (  # noqa: E402
    SemanticMemoryConflictError,
    SemanticMemoryIntegrityError,
    SemanticMemoryStoreError,
)


SERIALIZER = TypeSerializer()


def stream_image(values: dict[str, object]) -> dict[str, dict[str, object]]:
    return {key: SERIALIZER.serialize(value) for key, value in values.items()}


def typed_entry(**changes: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "entityType": "ENTRY",
        "entryId": "entry-123",
        "userId": "user-456",
        "sourceType": "typed",
        "status": "REVIEWED",
        "rawText": "A private journal passage.",
    }
    entry.update(changes)
    return entry


def stream_record(
    event_id: object,
    event_name: str,
    *,
    new_image: object | None = None,
    old_image: object | None = None,
    event_source: object = "aws:dynamodb",
    sequence_number: object | None = "automatic",
) -> dict[str, object]:
    dynamodb: dict[str, object] = {}
    if sequence_number == "automatic":
        sequence_number = f"sequence-{event_id}"
    if sequence_number is not None:
        dynamodb["SequenceNumber"] = sequence_number
    if new_image is not None:
        dynamodb["NewImage"] = new_image
    if old_image is not None:
        dynamodb["OldImage"] = old_image
    return {
        "eventID": event_id,
        "eventName": event_name,
        "eventSource": event_source,
        "dynamodb": dynamodb,
    }


def replacement_result(
    entry_id: str = "entry-123",
    *,
    status: str = ACTIVE,
) -> dict[str, object]:
    return {
        "entryId": entry_id,
        "userId": "user-456",
        "contentDigest": "a" * 64 if status == ACTIVE else None,
        "chunkCount": 1 if status == ACTIVE else 0,
        "chunkingVersion": "jm8-semantic-chunk-v1",
        "status": status,
    }


class SemanticMemoryLifecycleTests(unittest.TestCase):
    def test_typed_entry_insert_calls_replacement(self):
        table = object()
        entry = typed_entry()
        record = stream_record(
            "event-insert",
            "INSERT",
            new_image=stream_image(entry),
        )

        with patch.object(
            lifecycle,
            "replace_entry_memory",
            return_value=replacement_result(),
        ) as replace:
            result = apply_entry_stream_record(table, record)

        replace.assert_called_once_with(table, entry)
        self.assertEqual(result, {
            "eventId": "event-insert",
            "eventName": "INSERT",
            "entryId": "entry-123",
            "status": ACTIVE,
        })

    def test_reviewed_ocr_modify_preserves_clean_text_for_store_eligibility(self):
        table = object()
        entry = typed_entry(
            sourceType="upload",
            status="OCR_COMPLETE",
            reviewStatus="COMPLETED",
            cleanText="User-approved corrected text.",
            rawText="Unreviewed OCR output.",
        )
        record = stream_record(
            "event-ocr",
            "MODIFY",
            new_image=stream_image(entry),
        )

        with patch.object(
            lifecycle,
            "replace_entry_memory",
            return_value=replacement_result(),
        ) as replace:
            result = apply_entry_stream_record(table, record)

        submitted_entry = replace.call_args.args[1]
        self.assertEqual(submitted_entry, entry)
        self.assertEqual(
            submitted_entry["cleanText"],
            "User-approved corrected text.",
        )
        self.assertEqual(result["status"], ACTIVE)

    def test_unreviewed_ocr_is_delegated_and_store_deletion_is_reported(self):
        table = object()
        entry = typed_entry(
            sourceType="upload",
            status="OCR_COMPLETE",
            reviewStatus="PENDING",
            cleanText="Not approved yet.",
        )
        record = stream_record(
            "event-ineligible",
            "MODIFY",
            new_image=stream_image(entry),
        )

        with patch.object(
            lifecycle,
            "replace_entry_memory",
            return_value=replacement_result(status=DELETED),
        ) as replace, patch.object(
            lifecycle,
            "delete_entry_memory",
        ) as delete:
            result = apply_entry_stream_record(table, record)

        replace.assert_called_once_with(table, entry)
        delete.assert_not_called()
        self.assertEqual(result["status"], DELETED)

    def test_entry_remove_calls_exact_entry_deletion(self):
        table = object()
        entry = typed_entry()
        record = stream_record(
            "event-remove",
            "REMOVE",
            old_image=stream_image(entry),
        )

        with patch.object(lifecycle, "delete_entry_memory") as delete:
            result = apply_entry_stream_record(table, record)

        delete.assert_called_once_with(table, "user-456", "entry-123")
        self.assertEqual(result, {
            "eventId": "event-remove",
            "eventName": "REMOVE",
            "entryId": "entry-123",
            "status": DELETED,
        })

    def test_insert_and_modify_use_new_image_and_never_old_image(self):
        table = object()
        for event_name in ("INSERT", "MODIFY"):
            expected = typed_entry(entryId=f"new-{event_name.lower()}")
            record = stream_record(
                f"event-{event_name.lower()}",
                event_name,
                new_image=stream_image(expected),
                old_image={"entityType": {"invalid": "private old image"}},
            )
            with self.subTest(event_name=event_name), patch.object(
                lifecycle,
                "replace_entry_memory",
                return_value=replacement_result(expected["entryId"]),
            ) as replace:
                result = apply_entry_stream_record(table, record)
                replace.assert_called_once_with(table, expected)
                self.assertEqual(result["entryId"], expected["entryId"])

    def test_remove_uses_old_image_and_never_new_image(self):
        table = object()
        expected = typed_entry(entryId="old-entry")
        record = stream_record(
            "event-old",
            "REMOVE",
            old_image=stream_image(expected),
            new_image={"entityType": {"invalid": "private new image"}},
        )

        with patch.object(lifecycle, "delete_entry_memory") as delete:
            result = apply_entry_stream_record(table, record)

        delete.assert_called_once_with(table, "user-456", "old-entry")
        self.assertEqual(result["entryId"], "old-entry")

    def test_non_entry_records_are_ignored_after_deserialization(self):
        table = object()
        for event_name, entity_type in (
            ("INSERT", "USAGE"),
            ("MODIFY", "ANALYSIS_HISTORY"),
            ("REMOVE", "DELETION_AUDIT"),
        ):
            image_name = "old_image" if event_name == "REMOVE" else "new_image"
            record = stream_record(
                f"event-{entity_type}",
                event_name,
                **{image_name: stream_image({
                    "entityType": entity_type,
                    "details": {"count": Decimal("3"), "flags": [True, None]},
                })},
            )
            with self.subTest(entity_type=entity_type), patch.object(
                lifecycle,
                "replace_entry_memory",
            ) as replace, patch.object(
                lifecycle,
                "delete_entry_memory",
            ) as delete:
                result = apply_entry_stream_record(table, record)
                self.assertEqual(result["status"], IGNORED)
                self.assertNotIn("entryId", result)
                replace.assert_not_called()
                delete.assert_not_called()

    def test_records_are_processed_in_supplied_order(self):
        order: list[str] = []
        records = [
            stream_record(
                "first",
                "INSERT",
                new_image=stream_image(typed_entry(entryId="entry-first")),
            ),
            stream_record(
                "second",
                "MODIFY",
                new_image=stream_image(typed_entry(entryId="entry-second")),
            ),
            stream_record(
                "third",
                "REMOVE",
                old_image=stream_image(typed_entry(entryId="entry-third")),
            ),
        ]

        def replace(_table: object, entry: dict[str, object]):
            order.append(entry["entryId"])
            return replacement_result(entry["entryId"])

        def delete(_table: object, _user_id: str, entry_id: str):
            order.append(entry_id)
            return 1

        with patch.object(lifecycle, "replace_entry_memory", side_effect=replace), patch.object(
            lifecycle,
            "delete_entry_memory",
            side_effect=delete,
        ):
            response = process_entry_stream_event(object(), {"Records": records})

        self.assertEqual(order, ["entry-first", "entry-second", "entry-third"])
        self.assertEqual(response, {"batchItemFailures": []})

    def test_duplicate_delivery_repeats_the_idempotent_store_operation_safely(self):
        table = object()
        record = stream_record(
            "duplicate-event",
            "INSERT",
            new_image=stream_image(typed_entry()),
        )
        with patch.object(
            lifecycle,
            "replace_entry_memory",
            return_value=replacement_result(),
        ) as replace:
            response = process_entry_stream_event(
                table,
                {"Records": [copy.deepcopy(record), copy.deepcopy(record)]},
            )

        self.assertEqual(replace.call_count, 2)
        self.assertEqual(response, {"batchItemFailures": []})

    def test_one_failure_does_not_block_later_records(self):
        records = [
            stream_record(
                "diagnostic-failed-event",
                "INSERT",
                new_image=stream_image(typed_entry(entryId="failed-entry")),
                sequence_number="00000000000000000041",
            ),
            stream_record(
                "diagnostic-successful-event",
                "MODIFY",
                new_image=stream_image(typed_entry(entryId="successful-entry")),
                sequence_number="00000000000000000042",
            ),
        ]
        replacement = Mock(side_effect=[
            SemanticMemoryStoreError("privacy-safe store failure"),
            replacement_result("successful-entry"),
            replacement_result("successful-entry"),
        ])
        with patch.object(lifecycle, "replace_entry_memory", replacement):
            response = process_entry_stream_event(object(), {"Records": records})
            replay_response = process_entry_stream_event(
                object(),
                {"Records": [copy.deepcopy(records[1])]},
            )

        self.assertEqual(replacement.call_count, 3)
        self.assertEqual(response, {
            "batchItemFailures": [
                {"itemIdentifier": "00000000000000000041"}
            ]
        })
        self.assertEqual(replay_response, {"batchItemFailures": []})
        self.assertNotIn("diagnostic-failed-event", repr(response))

    def test_ordinary_record_failure_does_not_block_later_records(self):
        records = [
            stream_record(
                "input-failure",
                "INSERT",
                new_image=stream_image(typed_entry(entryId="oversized-entry")),
            ),
            stream_record(
                "later-success",
                "INSERT",
                new_image=stream_image(typed_entry(entryId="later-entry")),
            ),
        ]
        replacement = Mock(side_effect=[
            ValueError("private input detail"),
            replacement_result("later-entry"),
        ])
        with patch.object(lifecycle, "replace_entry_memory", replacement):
            response = process_entry_stream_event(object(), {"Records": records})

        self.assertEqual(replacement.call_count, 2)
        self.assertEqual(response, {
            "batchItemFailures": [{"itemIdentifier": "sequence-input-failure"}]
        })

    def test_conflicts_and_integrity_errors_are_partial_batch_failures(self):
        error_types = (
            SemanticMemoryConflictError,
            SemanticMemoryIntegrityError,
        )
        for index, error_type in enumerate(error_types):
            event_id = f"retry-{index}"
            record = stream_record(
                event_id,
                "MODIFY",
                new_image=stream_image(typed_entry()),
            )
            with self.subTest(error_type=error_type.__name__), patch.object(
                lifecycle,
                "replace_entry_memory",
                side_effect=error_type("privacy-safe store failure"),
            ):
                response = process_entry_stream_event(
                    object(),
                    {"Records": [record]},
                )
                self.assertEqual(response, {
                    "batchItemFailures": [
                        {"itemIdentifier": f"sequence-{event_id}"}
                    ]
                })

    def test_malformed_entry_identities_fail_closed(self):
        malformed = (
            typed_entry(entryId=""),
            typed_entry(entryId="   "),
            typed_entry(entryId=123),
            typed_entry(userId=""),
            typed_entry(userId=None),
        )
        for index, entry in enumerate(malformed):
            event_id = f"identity-{index}"
            record = stream_record(
                event_id,
                "INSERT",
                new_image=stream_image(entry),
            )
            with self.subTest(entry=entry), patch.object(
                lifecycle,
                "replace_entry_memory",
            ) as replace:
                response = process_entry_stream_event(
                    object(),
                    {"Records": [record]},
                )
                self.assertEqual(response, {
                    "batchItemFailures": [
                        {"itemIdentifier": f"sequence-{event_id}"}
                    ]
                })
                replace.assert_not_called()

    def test_missing_selected_images_are_failures(self):
        for event_name in ("INSERT", "MODIFY", "REMOVE"):
            event_id = f"missing-{event_name.lower()}"
            record = stream_record(event_id, event_name)
            with self.subTest(event_name=event_name):
                response = process_entry_stream_event(
                    object(),
                    {"Records": [record]},
                )
                self.assertEqual(response, {
                    "batchItemFailures": [
                        {"itemIdentifier": f"sequence-{event_id}"}
                    ]
                })

    def test_malformed_unrelated_image_is_not_silently_ignored(self):
        image = {
            "entityType": {"S": "EXPORT"},
            "untrusted": {"S": "private text", "N": "4"},
        }
        record = stream_record("malformed-unrelated", "INSERT", new_image=image)

        response = process_entry_stream_event(object(), {"Records": [record]})

        self.assertEqual(response, {
            "batchItemFailures": [
                {"itemIdentifier": "sequence-malformed-unrelated"}
            ]
        })

    def test_invalid_batch_shapes_sources_and_event_names_fail_safely(self):
        for event in (None, [], {}, {"Records": None}, {"Records": {}}):
            with self.subTest(event=event), self.assertRaises(
                SemanticMemoryLifecycleError
            ):
                process_entry_stream_event(object(), event)  # type: ignore[arg-type]

        records = (
            stream_record(
                "bad-source",
                "INSERT",
                new_image=stream_image(typed_entry()),
                event_source="aws:s3",
            ),
            stream_record(
                "bad-name",
                "UPSERT",
                new_image=stream_image(typed_entry()),
            ),
        )
        response = process_entry_stream_event(object(), {"Records": list(records)})
        self.assertEqual(response, {
            "batchItemFailures": [
                {"itemIdentifier": "sequence-bad-source"},
                {"itemIdentifier": "sequence-bad-name"},
            ]
        })

    def test_failed_record_without_valid_sequence_number_cannot_checkpoint(self):
        invalid_sequence_numbers = (None, "", "   ", 12)
        for sequence_number in invalid_sequence_numbers:
            record = stream_record(
                "diagnostic-event-id",
                "INSERT",
                new_image=stream_image(typed_entry()),
                sequence_number=sequence_number,
            )
            with self.subTest(sequence_number=sequence_number), patch.object(
                lifecycle,
                "replace_entry_memory",
                side_effect=SemanticMemoryStoreError("private store failure"),
            ), self.assertRaises(SemanticMemoryLifecycleError) as raised:
                process_entry_stream_event(object(), {"Records": [record]})
            self.assertEqual(
                str(raised.exception),
                "stream batch checkpoint cannot be established safely",
            )

    def test_successful_and_ignored_records_do_not_require_sequence_number(self):
        successful = stream_record(
            None,
            "INSERT",
            new_image=stream_image(typed_entry()),
            sequence_number=None,
        )
        ignored = stream_record(
            None,
            "MODIFY",
            new_image=stream_image({"entityType": "LOCK"}),
            sequence_number=None,
        )
        with patch.object(
            lifecycle,
            "replace_entry_memory",
            return_value=replacement_result(),
        ):
            successful_result = apply_entry_stream_record(object(), successful)
            ignored_result = apply_entry_stream_record(object(), ignored)
            response = process_entry_stream_event(
                object(),
                {"Records": [successful, ignored]},
            )

        self.assertEqual(successful_result, {
            "eventName": "INSERT",
            "entryId": "entry-123",
            "status": ACTIVE,
        })
        self.assertEqual(ignored_result, {
            "eventName": "MODIFY",
            "status": IGNORED,
        })
        self.assertEqual(response, {"batchItemFailures": []})

    def test_multiple_failures_use_unique_sequence_numbers_in_record_order(self):
        records = [
            stream_record(
                "event-id-first",
                "INSERT",
                new_image=stream_image(typed_entry(entryId="entry-first")),
                sequence_number="0000000000000100",
            ),
            stream_record(
                "event-id-duplicate",
                "MODIFY",
                new_image=stream_image(typed_entry(entryId="entry-second")),
                sequence_number="0000000000000100",
            ),
            stream_record(
                "event-id-last",
                "REMOVE",
                old_image=stream_image(typed_entry(entryId="entry-third")),
                sequence_number="0000000000000125",
            ),
        ]
        with patch.object(
            lifecycle,
            "replace_entry_memory",
            side_effect=SemanticMemoryStoreError("store failure"),
        ), patch.object(
            lifecycle,
            "delete_entry_memory",
            side_effect=SemanticMemoryStoreError("store failure"),
        ):
            response = process_entry_stream_event(object(), {"Records": records})

        self.assertEqual(response, {
            "batchItemFailures": [
                {"itemIdentifier": "0000000000000100"},
                {"itemIdentifier": "0000000000000125"},
            ]
        })
        self.assertNotIn("event-id-first", repr(response))
        self.assertNotIn("event-id-duplicate", repr(response))
        self.assertNotIn("event-id-last", repr(response))

    def test_event_and_images_are_not_mutated_and_nested_values_deserialize(self):
        table = object()
        entry = typed_entry(
            score=Decimal("4.25"),
            context={
                "numbers": [Decimal("1"), Decimal("2.50")],
                "flags": {"reviewed": True, "removed": None},
            },
        )
        event = {"Records": [stream_record(
            "nested-event",
            "INSERT",
            new_image=stream_image(entry),
        )]}
        original = copy.deepcopy(event)

        with patch.object(
            lifecycle,
            "replace_entry_memory",
            return_value=replacement_result(),
        ) as replace:
            response = process_entry_stream_event(table, event)

        submitted = replace.call_args.args[1]
        self.assertEqual(submitted["score"], Decimal("4.25"))
        self.assertEqual(
            submitted["context"],
            {
                "numbers": [Decimal("1"), Decimal("2.50")],
                "flags": {"reviewed": True, "removed": None},
            },
        )
        self.assertEqual(event, original)
        self.assertEqual(response, {"batchItemFailures": []})

    def test_privacy_safe_errors_do_not_contain_record_or_store_secrets(self):
        secrets = (
            "user-secret",
            "entry-secret",
            "private journal text",
            "digest-secret",
            "raw AWS message",
            "event-secret",
        )
        entry = typed_entry(
            userId=secrets[0],
            entryId=secrets[1],
            rawText=secrets[2],
            contentDigest=secrets[3],
        )
        record = stream_record(
            secrets[5],
            "INSERT",
            new_image=stream_image(entry),
            sequence_number=None,
        )
        with patch.object(
            lifecycle,
            "replace_entry_memory",
            side_effect=SemanticMemoryStoreError(secrets[4]),
        ), self.assertRaises(SemanticMemoryLifecycleError) as raised:
            process_entry_stream_event(object(), {"Records": [record]})

        message = str(raised.exception)
        for secret in secrets:
            self.assertNotIn(secret, message)

    def test_base_exceptions_are_not_swallowed(self):
        record = stream_record(
            "base-exception-event",
            "INSERT",
            new_image=stream_image(typed_entry()),
        )
        for exception in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(exception=type(exception).__name__), patch.object(
                lifecycle,
                "replace_entry_memory",
                side_effect=exception,
            ), self.assertRaises(type(exception)):
                process_entry_stream_event(object(), {"Records": [record]})

    def test_module_has_no_ambient_or_prohibited_behavior(self):
        source = inspect.getsource(lifecycle)
        lowered = source.lower()
        self.assertIn(
            'FunctionResponseTypes=["ReportBatchItemFailures"]',
            source,
        )
        for prohibited in (
            "boto3.resource",
            "boto3.client",
            "os.environ",
            "os.getenv",
            ".scan(",
            "embedding",
            "uuid",
            "random",
            "logging",
            "print(",
        ):
            with self.subTest(prohibited=prohibited):
                self.assertNotIn(prohibited, lowered)


if __name__ == "__main__":
    unittest.main()
