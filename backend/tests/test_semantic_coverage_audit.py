from __future__ import annotations

import copy
import hashlib
import json
import re
import stat
import sys
import unittest
from pathlib import Path


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

from semantic_chunking import CHUNKING_VERSION
from semantic_coverage_audit import (
    AUDIT_VERSION,
    BACKFILL_NOT_REQUIRED,
    BLOCKED,
    READY_FOR_BACKFILL,
    SemanticCoverageAuditError,
    audit_semantic_coverage,
    collect_eligible_entries,
    collect_semantic_records,
)
from semantic_embedding_contract import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_ID,
    EMBEDDING_NORMALIZED,
    EMBEDDING_VERSION,
    embedding_partition,
)


USER = "private-user"
ENTRY = "private-entry"
CONTENT = "a" * 64
CHUNK_DIGEST = "b" * 64


def token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def chunk_id(entry_id: str = ENTRY, content_digest: str = CONTENT) -> str:
    identity = "\0".join((
        CHUNKING_VERSION,
        entry_id,
        content_digest,
        "0",
        CHUNK_DIGEST,
    ))
    return f"chunk_{token(identity)}"


def eligible_entry(user_id: str = USER, entry_id: str = ENTRY) -> dict:
    return {
        "PK": f"USER#{user_id}",
        "SK": f"ENTRY#2026-09-10T00:00:00Z#{entry_id}",
        "entityType": "ENTRY",
        "userId": user_id,
        "entryId": entry_id,
        "sourceType": "typed",
        "status": "REVIEWED",
        "createdAt": "2026-09-10T00:00:00Z",
        "updatedAt": "2026-09-10T00:01:00Z",
        "canonicalTextField": "rawText",
    }


def manifest(user_id: str = USER, entry_id: str = ENTRY) -> dict:
    return {
        "PK": f"USER#{user_id}",
        "SK": f"ENTRY#{token(entry_id)}#MANIFEST",
        "entityType": "SEMANTIC_MEMORY_MANIFEST",
        "userId": user_id,
        "entryId": entry_id,
        "activeContentDigest": CONTENT,
        "chunkCount": 1,
        "chunkingVersion": CHUNKING_VERSION,
        "canonicalTextField": "rawText",
        "sourceType": "typed",
        "entryCreatedAt": "2026-09-10T00:00:00Z",
        "entryUpdatedAt": "2026-09-10T00:01:00Z",
    }


def chunk(user_id: str = USER, entry_id: str = ENTRY) -> dict:
    identity = chunk_id(entry_id)
    return {
        "PK": f"USER#{user_id}",
        "SK": (
            f"ENTRY#{token(entry_id)}#GEN#{CONTENT}#CHUNK#"
            f"00000000#{identity}"
        ),
        "entityType": "SEMANTIC_CHUNK",
        "userId": user_id,
        "entryId": entry_id,
        "contentDigest": CONTENT,
        "generationId": CONTENT,
        "chunkId": identity,
        "chunkOrdinal": 0,
        "chunkCount": 1,
        "chunkDigest": CHUNK_DIGEST,
        "chunkingVersion": CHUNKING_VERSION,
        "embedding": [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
        "embeddingModelId": EMBEDDING_MODEL_ID,
        "embeddingVersion": EMBEDDING_VERSION,
        "embeddingDimensions": EMBEDDING_DIMENSIONS,
        "embeddingNormalized": EMBEDDING_NORMALIZED,
        "embeddingContentDigest": CONTENT,
        "embeddingPartition": embedding_partition(user_id),
        "embeddingInputTokenCount": 12,
    }


class FakeTable:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class SemanticCoverageAuditTests(unittest.TestCase):
    def test_complete_coverage_requires_no_backfill(self):
        report = audit_semantic_coverage(
            [eligible_entry()],
            [manifest(), chunk()],
        )
        self.assertEqual(report, {
            "auditVersion": AUDIT_VERSION,
            "status": BACKFILL_NOT_REQUIRED,
            "eligibleEntryCount": 1,
            "coveredEntryCount": 1,
            "currentManifestCount": 1,
            "currentChunkCount": 1,
            "embeddedCurrentChunkCount": 1,
            "missingManifestCount": 0,
            "missingEmbeddingCount": 0,
            "invalidEmbeddingCount": 0,
            "staleGenerationCount": 0,
            "duplicateCurrentGenerationCount": 0,
            "tenantIntegrityViolationCount": 0,
            "structuralIntegrityViolationCount": 0,
            "orphanedSemanticRecordCount": 0,
        })

    def test_missing_manifest_or_embedding_is_ready_for_backfill(self):
        missing_manifest = audit_semantic_coverage([eligible_entry()], [])
        self.assertEqual(missing_manifest["status"], READY_FOR_BACKFILL)
        self.assertEqual(missing_manifest["missingManifestCount"], 1)

        item = chunk()
        for field in tuple(item):
            if field.startswith("embedding"):
                item.pop(field)
        missing_embedding = audit_semantic_coverage(
            [eligible_entry()],
            [manifest(), item],
        )
        self.assertEqual(missing_embedding["status"], READY_FOR_BACKFILL)
        self.assertEqual(missing_embedding["missingEmbeddingCount"], 1)
        self.assertEqual(missing_embedding["coveredEntryCount"], 0)

    def test_invalid_embedding_blocks_backfill(self):
        item = chunk()
        item["embeddingVersion"] = "old-version"
        report = audit_semantic_coverage(
            [eligible_entry()],
            [manifest(), item],
        )
        self.assertEqual(report["status"], BLOCKED)
        self.assertEqual(report["invalidEmbeddingCount"], 1)

    def test_stale_generation_blocks_backfill(self):
        stale = chunk()
        stale["generationId"] = "c" * 64
        report = audit_semantic_coverage(
            [eligible_entry()],
            [manifest(), chunk(), stale],
        )
        self.assertEqual(report["status"], BLOCKED)
        self.assertEqual(report["staleGenerationCount"], 1)

    def test_cross_tenant_record_blocks_without_exposing_identity(self):
        foreign = chunk()
        foreign["PK"] = "USER#another-user"
        report = audit_semantic_coverage(
            [eligible_entry()],
            [manifest(), foreign],
        )
        serialized = json.dumps(report)
        self.assertEqual(report["status"], BLOCKED)
        self.assertEqual(report["tenantIntegrityViolationCount"], 1)
        self.assertNotIn(USER, serialized)
        self.assertNotIn(ENTRY, serialized)
        self.assertNotIn("another-user", serialized)

    def test_duplicate_current_chunk_blocks(self):
        report = audit_semantic_coverage(
            [eligible_entry()],
            [manifest(), chunk(), copy.deepcopy(chunk())],
        )
        self.assertEqual(report["status"], BLOCKED)
        self.assertEqual(report["duplicateCurrentGenerationCount"], 1)

    def test_orphaned_semantic_records_block(self):
        report = audit_semantic_coverage([], [manifest(), chunk()])
        self.assertEqual(report["status"], BLOCKED)
        self.assertEqual(report["orphanedSemanticRecordCount"], 2)

    def test_manifest_timestamp_or_shape_drift_blocks(self):
        for field, value in (
            ("entryUpdatedAt", "older"),
            ("chunkingVersion", "old"),
            ("chunkCount", 0),
            ("canonicalTextField", "cleanText"),
        ):
            with self.subTest(field=field):
                item = manifest()
                item[field] = value
                report = audit_semantic_coverage(
                    [eligible_entry()],
                    [item, chunk()],
                )
                self.assertEqual(report["status"], BLOCKED)
                self.assertGreater(
                    report["structuralIntegrityViolationCount"],
                    0,
                )

    def test_invalid_top_level_input_fails_safely(self):
        for entries, records in ((None, []), ([], None), ("entries", [])):
            with self.subTest(entries=entries, records=records):
                with self.assertRaises(SemanticCoverageAuditError):
                    audit_semantic_coverage(entries, records)


class SemanticCoverageCollectionTests(unittest.TestCase):
    def test_eligible_scan_filters_on_text_but_never_projects_it(self):
        table = FakeTable([
            {"Items": [eligible_entry()]},
            {"Items": []},
        ])
        result = collect_eligible_entries(table)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["canonicalTextField"], "cleanText")
        self.assertEqual(len(table.calls), 2)
        for call in table.calls:
            projection = call["ProjectionExpression"]
            filter_expression = call["FilterExpression"]
            names = call["ExpressionAttributeNames"]
            values = call["ExpressionAttributeValues"]
            projected_names = {
                names[token.strip()]
                for token in projection.split(",")
            }
            self.assertNotIn("cleanText", projected_names)
            self.assertNotIn("rawText", projected_names)
            self.assertNotIn("text", projected_names)
            self.assertIn("ConsistentRead", call)
            self.assertIn("size(#", filter_expression)
            self.assertEqual(
                set(names),
                set(re.findall(r"#[A-Za-z0-9]+", projection + filter_expression)),
            )
            self.assertEqual(
                set(values),
                set(re.findall(r":[A-Za-z0-9]+", filter_expression)),
            )
            self.assertIn(":analyzed", values)
            self.assertEqual(values[":analyzed"], "ANALYZED")

    def test_semantic_scan_never_projects_chunk_text(self):
        table = FakeTable([{"Items": [manifest(), chunk()]}])
        result = collect_semantic_records(table)
        self.assertEqual(len(result), 2)
        call = table.calls[0]
        projected_names = set(call["ExpressionAttributeNames"].values())
        self.assertNotIn("text", projected_names)
        self.assertIn("embedding", projected_names)
        self.assertTrue(call["ConsistentRead"])

    def test_scan_paginates_without_leaking_failures(self):
        table = FakeTable([
            {"Items": [manifest()], "LastEvaluatedKey": {"private": "key"}},
            {"Items": [chunk()]},
        ])
        self.assertEqual(len(collect_semantic_records(table)), 2)
        self.assertEqual(
            table.calls[1]["ExclusiveStartKey"],
            {"private": "key"},
        )

        class BrokenTable:
            def scan(self, **kwargs):
                raise RuntimeError("private failure")

        with self.assertRaisesRegex(
            SemanticCoverageAuditError,
            "semantic coverage read failed",
        ) as raised:
            collect_semantic_records(BrokenTable())
        self.assertNotIn("private failure", str(raised.exception))


class SemanticCoverageCliContractTests(unittest.TestCase):
    def test_cli_is_executable_and_read_only(self):
        path = FUNCTION_DIR.parent / "bin" / "audit-semantic-coverage"
        self.assertTrue(path.stat().st_mode & stat.S_IXUSR)
        source = path.read_text(encoding="utf-8")
        self.assertIn('session.client("sts").get_caller_identity()', source)
        self.assertIn("collect_eligible_entries(main_table)", source)
        self.assertIn("collect_semantic_records(chunks_table)", source)
        for forbidden in (
            ".put_item(",
            ".update_item(",
            ".delete_item(",
            ".batch_write_item(",
            ".transact_write_items(",
            ".invoke_model(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_cli_never_names_journal_text_fields(self):
        path = FUNCTION_DIR.parent / "bin" / "audit-semantic-coverage"
        source = path.read_text(encoding="utf-8")
        self.assertNotIn("cleanText", source)
        self.assertNotIn("rawText", source)

    def test_cli_reports_named_failure_stages(self):
        path = FUNCTION_DIR.parent / "bin" / "audit-semantic-coverage"
        source = path.read_text(encoding="utf-8")
        for stage in (
            "AWS session setup",
            "account identity check",
            "DynamoDB client setup",
            "eligible-entry inventory",
            "semantic-record inventory",
            "coverage evaluation",
        ):
            with self.subTest(stage=stage):
                self.assertIn(stage, source)


if __name__ == "__main__":
    unittest.main()
