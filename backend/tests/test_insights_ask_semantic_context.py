from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

from insights_ask_semantic_context import (  # noqa: E402
    AskSemanticContextError,
    MAX_SEMANTIC_EVIDENCE_CHARACTERS,
    MAX_SEMANTIC_EVIDENCE_ITEMS,
    MAX_SEMANTIC_EXCERPT_CHARACTERS,
    compose_ask_context_with_semantic_evidence,
)


def ask_context(
    *,
    start_date: str | None = "2026-01-01",
    end_date: str | None = "2026-01-31",
) -> dict:
    return {
        "contextVersion": "1.0",
        "contextStatus": "READY",
        "question": "What challenge keeps returning?",
        "scope": {
            "startDate": start_date,
            "endDate": end_date,
            "firstEntryAt": "2026-01-01T00:00:00+00:00",
            "latestEntryAt": "2026-01-31T00:00:00+00:00",
        },
        "coverage": {},
        "aggregateSignals": {},
        "timeline": [],
        "sourceSignals": [],
    }


def evidence_item(
    number: int = 1,
    *,
    created_at: object = "2026-01-10T12:00:00+00:00",
    text: object = "I kept returning to the same difficult routine.",
    source_type: object = "typed",
    score: object = 0.125,
) -> dict:
    return {
        "score": score,
        "entryId": f"private-entry-{number}",
        "chunkId": f"private-chunk-{number}",
        "chunkOrdinal": 0,
        "chunkCount": 1,
        "text": text,
        "contentDigest": str(number) * 64,
        "canonicalTextField": "cleanText",
        "sourceType": source_type,
        "entryCreatedAt": created_at,
        "entryUpdatedAt": "2026-01-11T12:00:00+00:00",
    }


def retrieval(items: list[dict]) -> dict:
    return {
        "semanticRetrievalVersion": "1.0",
        "semanticRetrievalStatus": "READY" if items else "EMPTY",
        "evidenceCount": len(items),
        "retrievalLimit": 24,
        "queryEmbedding": {
            "modelId": "amazon.titan-embed-text-v2:0",
            "version": "titan-v2-1024-v1",
            "dimensions": 1024,
            "normalized": True,
            "inputTokenCount": 8,
        },
        "evidence": items,
    }


class AskSemanticContextTests(unittest.TestCase):
    def test_minimizes_private_retrieval_data(self):
        original = ask_context()
        result = compose_ask_context_with_semantic_evidence(
            original,
            retrieval([evidence_item()]),
        )

        semantic = result["semanticEvidence"]
        self.assertEqual(semantic["status"], "READY")
        self.assertEqual(semantic["retrievedEvidence"], 1)
        self.assertEqual(semantic["includedEvidence"], 1)
        self.assertEqual(semantic["items"], [{
            "date": "2026-01-10",
            "sourceType": "typed",
            "distance": 0.125,
            "excerpt": "I kept returning to the same difficult routine.",
        }])
        serialized = json.dumps(result)
        for private_value in (
            "entryId",
            "chunkId",
            "contentDigest",
            "canonicalTextField",
            "entryUpdatedAt",
            "queryEmbedding",
            "amazon.titan-embed-text-v2:0",
            "private-entry-1",
            "private-chunk-1",
        ):
            with self.subTest(private_value=private_value):
                self.assertNotIn(private_value, serialized)
        self.assertNotIn("semanticEvidence", original)

    def test_filters_evidence_outside_requested_date_scope(self):
        result = compose_ask_context_with_semantic_evidence(
            ask_context(),
            retrieval([
                evidence_item(1, created_at="2025-12-31T23:59:59+00:00"),
                evidence_item(2, created_at="2026-01-31T23:59:59-05:00"),
                evidence_item(3, created_at="2026-01-15T12:00:00+00:00"),
            ]),
        )

        semantic = result["semanticEvidence"]
        self.assertEqual(semantic["includedEvidence"], 1)
        self.assertEqual(semantic["scopeExcludedEvidence"], 2)
        self.assertEqual(semantic["items"][0]["date"], "2026-01-15")

    def test_rejects_missing_dates_when_scope_cannot_be_proven(self):
        result = compose_ask_context_with_semantic_evidence(
            ask_context(),
            retrieval([evidence_item(created_at=None)]),
        )

        semantic = result["semanticEvidence"]
        self.assertEqual(semantic["status"], "EMPTY")
        self.assertEqual(semantic["invalidExcludedEvidence"], 1)
        self.assertEqual(semantic["items"], [])

    def test_deduplicates_and_bounds_excerpts_and_total_evidence(self):
        long_text = "x" * (MAX_SEMANTIC_EXCERPT_CHARACTERS + 50)
        items = [
            evidence_item(1, text=long_text),
            evidence_item(2, text=long_text),
        ] + [
            evidence_item(
                number,
                text=f"unique semantic evidence {number} " + ("y" * 1_100),
            )
            for number in range(3, 20)
        ]
        result = compose_ask_context_with_semantic_evidence(
            ask_context(start_date=None, end_date=None),
            retrieval(items),
        )

        semantic = result["semanticEvidence"]
        self.assertLessEqual(len(semantic["items"]), MAX_SEMANTIC_EVIDENCE_ITEMS)
        self.assertLessEqual(
            sum(len(item["excerpt"]) for item in semantic["items"]),
            MAX_SEMANTIC_EVIDENCE_CHARACTERS,
        )
        self.assertEqual(
            len(semantic["items"][0]["excerpt"]),
            MAX_SEMANTIC_EXCERPT_CHARACTERS,
        )
        self.assertEqual(semantic["duplicateExcludedEvidence"], 1)
        self.assertGreater(semantic["limitExcludedEvidence"], 0)
        self.assertTrue(semantic["contextTruncated"])

    def test_invalid_items_are_omitted_without_leaking_values(self):
        invalid = [
            evidence_item(1, text=""),
            evidence_item(2, source_type="audio"),
            evidence_item(3, score=float("nan")),
            evidence_item(4, created_at="not-a-date"),
            {"private": "value"},
        ]
        result = compose_ask_context_with_semantic_evidence(
            ask_context(),
            retrieval(invalid),
        )

        self.assertEqual(result["semanticEvidence"]["status"], "EMPTY")
        self.assertEqual(
            result["semanticEvidence"]["invalidExcludedEvidence"],
            len(invalid),
        )
        self.assertNotIn("private", json.dumps(result))

    def test_malformed_retrieval_and_scope_fail_closed(self):
        malformed = retrieval([evidence_item()])
        malformed["evidenceCount"] = 2
        cases = (
            (None, retrieval([])),
            (ask_context(), None),
            (ask_context(), malformed),
            (
                ask_context(start_date="2026-02-01", end_date="2026-01-01"),
                retrieval([]),
            ),
            (ask_context(start_date="not-a-date"), retrieval([])),
        )

        for context, semantic_retrieval in cases:
            with self.subTest(context=context, retrieval=semantic_retrieval):
                with self.assertRaises(AskSemanticContextError):
                    compose_ask_context_with_semantic_evidence(
                        context,
                        semantic_retrieval,
                    )


if __name__ == "__main__":
    unittest.main()
