from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

from semantic_backfill import (
    BACKFILL_VERSION,
    SemanticBackfillError,
    collect_backfill_entries,
    execute_semantic_backfill,
    validate_backfill_precondition,
)


def entry(
    *,
    user_id: str = "private-user",
    entry_id: str = "private-entry",
    text: str = "private journal text",
    updated_at: str = "2026-09-12T00:01:00Z",
) -> dict[str, object]:
    return {
        "PK": f"USER#{user_id}",
        "SK": f"ENTRY#2026-09-12T00:00:00Z#{entry_id}",
        "entityType": "ENTRY",
        "userId": user_id,
        "entryId": entry_id,
        "sourceType": "typed",
        "status": "ANALYZED",
        "rawText": text,
        "createdAt": "2026-09-12T00:00:00Z",
        "updatedAt": updated_at,
    }


def safe_report(*, eligible: int = 3, covered: int = 0) -> dict[str, object]:
    return {
        "auditVersion": "jm8-semantic-coverage-audit-v1",
        "status": (
            "BACKFILL_NOT_REQUIRED"
            if eligible == covered
            else "READY_FOR_BACKFILL"
        ),
        "eligibleEntryCount": eligible,
        "coveredEntryCount": covered,
        "duplicateCurrentGenerationCount": 0,
        "invalidEmbeddingCount": 0,
        "orphanedSemanticRecordCount": 0,
        "staleGenerationCount": 0,
        "structuralIntegrityViolationCount": 0,
        "tenantIntegrityViolationCount": 0,
    }


class FakeScanTable:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class SemanticBackfillCollectionTests(unittest.TestCase):
    def test_apply_inventory_reads_only_required_approved_fields(self):
        table = FakeScanTable([
            {
                "Items": [entry()],
                "LastEvaluatedKey": {"private": "pagination-key"},
            },
            {"Items": [entry(user_id="second-user", entry_id="second-entry")]},
        ])

        result = collect_backfill_entries(table)

        self.assertEqual(len(result), 2)
        self.assertEqual(len(table.calls), 2)
        self.assertEqual(
            table.calls[1]["ExclusiveStartKey"],
            {"private": "pagination-key"},
        )
        first = table.calls[0]
        self.assertTrue(first["ConsistentRead"])
        projected = {
            first["ExpressionAttributeNames"][token.strip()]
            for token in first["ProjectionExpression"].split(",")
        }
        self.assertEqual(projected, {
            "PK",
            "SK",
            "entityType",
            "userId",
            "entryId",
            "sourceType",
            "status",
            "reviewStatus",
            "reviewedAt",
            "cleanText",
            "rawText",
            "createdAt",
            "updatedAt",
        })
        combined = (
            first["ProjectionExpression"] + first["FilterExpression"]
        )
        self.assertEqual(
            set(first["ExpressionAttributeNames"]),
            set(re.findall(r"#[A-Za-z0-9]+", combined)),
        )
        self.assertEqual(
            set(first["ExpressionAttributeValues"]),
            set(re.findall(r":[A-Za-z0-9]+", first["FilterExpression"])),
        )

    def test_duplicate_or_malformed_inventory_fails_privately(self):
        private = entry()
        for items in (
            [private, copy.deepcopy(private)],
            [{**private, "PK": "USER#wrong"}],
            [{**private, "status": "DRAFT"}],
        ):
            with self.subTest(items=items):
                with self.assertRaises(SemanticBackfillError) as raised:
                    collect_backfill_entries(FakeScanTable([{"Items": items}]))
                message = str(raised.exception)
                self.assertNotIn("private-user", message)
                self.assertNotIn("private-entry", message)
                self.assertNotIn("private journal text", message)

    def test_scan_failure_does_not_expose_provider_details(self):
        class Broken:
            def scan(self, **kwargs):
                raise RuntimeError("private provider request")

        with self.assertRaises(SemanticBackfillError) as raised:
            collect_backfill_entries(Broken())
        self.assertEqual(
            str(raised.exception),
            "semantic backfill source read failed",
        )


class SemanticBackfillPreconditionTests(unittest.TestCase):
    def test_safe_ready_and_completed_reports_are_accepted(self):
        for covered in (0, 1, 3):
            with self.subTest(covered=covered):
                validate_backfill_precondition(
                    safe_report(covered=covered),
                    expected_entry_count=3,
                    maximum_entry_count=3,
                )

    def test_count_drift_blocked_state_or_violation_fails(self):
        reports = [
            safe_report(eligible=4),
            {**safe_report(), "status": "BLOCKED"},
            {**safe_report(), "tenantIntegrityViolationCount": 1},
        ]
        for report in reports:
            with self.subTest(report=report):
                with self.assertRaises(SemanticBackfillError):
                    validate_backfill_precondition(
                        report,
                        expected_entry_count=3,
                        maximum_entry_count=3,
                    )

    def test_boolean_or_excessive_bounds_fail(self):
        for expected, maximum in ((True, 3), (3, True), (3, 2), (0, 3)):
            with self.subTest(expected=expected, maximum=maximum):
                with self.assertRaises(SemanticBackfillError):
                    validate_backfill_precondition(
                        safe_report(),
                        expected_entry_count=expected,
                        maximum_entry_count=maximum,
                    )


class SemanticBackfillExecutionTests(unittest.TestCase):
    def test_processes_serially_waits_for_embedding_and_reports_counts(self):
        source = entry()
        loader = Mock(return_value=copy.deepcopy(source))
        sleeper = Mock()
        replacement = {
            "status": "ACTIVE",
            "contentDigest": "a" * 64,
            "chunkCount": 1,
        }

        with (
            patch(
                "semantic_backfill.entry_is_fully_covered",
                side_effect=(False, False, True, True),
            ),
            patch(
                "semantic_backfill.replace_entry_memory",
                return_value=replacement,
            ) as replace,
        ):
            result = execute_semantic_backfill(
                [source],
                object(),
                current_entry_loader=loader,
                expected_entry_count=1,
                maximum_entry_count=1,
                poll_attempts=3,
                poll_delay_seconds=0.25,
                sleeper=sleeper,
            )

        self.assertEqual(result, {
            "backfillVersion": BACKFILL_VERSION,
            "eligibleEntryCount": 1,
            "processedEntryCount": 1,
            "alreadyCoveredEntryCount": 0,
            "finalCoveredEntryCount": 1,
        })
        replace.assert_called_once()
        sleeper.assert_called_once_with(0.25)

    def test_covered_entry_is_skipped_for_resumability(self):
        source = entry()
        with (
            patch(
                "semantic_backfill.entry_is_fully_covered",
                side_effect=(True, True),
            ),
            patch("semantic_backfill.replace_entry_memory") as replace,
        ):
            result = execute_semantic_backfill(
                [source],
                object(),
                current_entry_loader=lambda pk, sk: copy.deepcopy(source),
                expected_entry_count=1,
                maximum_entry_count=1,
            )

        replace.assert_not_called()
        self.assertEqual(result["processedEntryCount"], 0)
        self.assertEqual(result["alreadyCoveredEntryCount"], 1)
        self.assertEqual(result["finalCoveredEntryCount"], 1)

    def test_source_drift_stops_before_write(self):
        source = entry()
        changed = entry(updated_at="changed")
        with patch("semantic_backfill.replace_entry_memory") as replace:
            with self.assertRaisesRegex(
                SemanticBackfillError,
                "source changed during execution",
            ):
                execute_semantic_backfill(
                    [source],
                    object(),
                    current_entry_loader=lambda pk, sk: changed,
                    expected_entry_count=1,
                    maximum_entry_count=1,
                )
        replace.assert_not_called()

    def test_embedding_timeout_stops_with_private_error(self):
        source = entry(text="never expose this journal text")
        with (
            patch(
                "semantic_backfill.entry_is_fully_covered",
                return_value=False,
            ),
            patch(
                "semantic_backfill.replace_entry_memory",
                return_value={"status": "ACTIVE"},
            ),
            self.assertRaises(SemanticBackfillError) as raised,
        ):
            execute_semantic_backfill(
                [source],
                object(),
                current_entry_loader=lambda pk, sk: copy.deepcopy(source),
                expected_entry_count=1,
                maximum_entry_count=1,
                poll_attempts=2,
                poll_delay_seconds=0,
            )
        self.assertEqual(
            str(raised.exception),
            "semantic backfill embedding did not become current",
        )
        self.assertNotIn("journal", str(raised.exception))

    def test_persistence_error_does_not_expose_private_values(self):
        source = entry(text="secret journal sentence")
        with (
            patch(
                "semantic_backfill.entry_is_fully_covered",
                return_value=False,
            ),
            patch(
                "semantic_backfill.replace_entry_memory",
                side_effect=RuntimeError("private provider payload"),
            ),
            self.assertRaises(SemanticBackfillError) as raised,
        ):
            execute_semantic_backfill(
                [source],
                object(),
                current_entry_loader=lambda pk, sk: copy.deepcopy(source),
                expected_entry_count=1,
                maximum_entry_count=1,
            )
        self.assertEqual(
            str(raised.exception),
            "semantic backfill persistence failed",
        )
        self.assertNotIn("secret journal sentence", str(raised.exception))
        self.assertNotIn("private provider payload", str(raised.exception))

    def test_exact_inventory_bound_is_enforced_before_writes(self):
        sources = [entry(entry_id="a"), entry(entry_id="b")]
        with patch("semantic_backfill.replace_entry_memory") as replace:
            with self.assertRaisesRegex(
                SemanticBackfillError,
                "bounds are invalid",
            ):
                execute_semantic_backfill(
                    sources,
                    object(),
                    current_entry_loader=lambda pk, sk: sources[0],
                    expected_entry_count=1,
                    maximum_entry_count=1,
                )
        replace.assert_not_called()


class SemanticBackfillCliContractTests(unittest.TestCase):
    def test_cli_has_dry_run_before_text_collection_and_exact_confirmation(self):
        source = (
            FUNCTION_DIR.parent / "bin" / "jm8_semantic_backfill.py"
        ).read_text(encoding="utf-8")
        dry_run = source.index("if not args.apply:")
        text_collection = source.index(
            "entries = collect_backfill_entries(main_table)"
        )
        self.assertLess(dry_run, text_collection)
        self.assertIn("BACKFILL-EXACTLY-", source)
        self.assertIn('"journalTextRead": False', source)
        self.assertIn('"mainTableMutationCount": 0', source)

    def test_cli_never_mutates_main_table_or_logs_entry_values(self):
        source = (
            FUNCTION_DIR.parent / "bin" / "jm8_semantic_backfill.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            ".update_item(",
            ".put_item(",
            ".delete_item(",
            ".batch_writer(",
            "transact_write_items",
            "print(entry",
            "print(entries",
            "print(user_id",
            "print(entry_id",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_public_result_schema_contains_counts_only(self):
        serialized = json.dumps({
            "backfillVersion": BACKFILL_VERSION,
            "eligibleEntryCount": 3,
            "processedEntryCount": 3,
            "alreadyCoveredEntryCount": 0,
            "finalCoveredEntryCount": 3,
        })
        for private in (
            "private-user",
            "private-entry",
            "private journal text",
            "rawText",
            "cleanText",
        ):
            self.assertNotIn(private, serialized)


if __name__ == "__main__":
    unittest.main()
