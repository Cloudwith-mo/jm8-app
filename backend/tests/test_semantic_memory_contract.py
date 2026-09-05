import ast
import copy
import sys
import unittest
from pathlib import Path


FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))


from semantic_chunking import CHUNKING_VERSION, chunk_text  # noqa: E402
from semantic_memory_contract import (  # noqa: E402
    SemanticMemoryIdentityError,
    build_entry_semantic_chunks,
    canonical_entry_text,
)


def base_entry(**changes: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "entryId": "entry-123",
        "userId": "user-456",
        "sourceType": "typed",
        "status": "REVIEWED",
        "rawText": "A typed journal entry.",
        "createdAt": "2026-09-01T12:00:00Z",
        "updatedAt": "2026-09-02T13:30:00Z",
    }
    entry.update(changes)
    return entry


class SemanticMemoryContractTests(unittest.TestCase):
    def test_typed_reviewed_entry_selects_raw_text(self):
        selected = canonical_entry_text(base_entry())

        self.assertEqual(
            selected,
            {
                "entryId": "entry-123",
                "userId": "user-456",
                "text": "A typed journal entry.",
                "textField": "rawText",
                "sourceType": "typed",
            },
        )

    def test_reviewed_ocr_entry_selects_clean_text(self):
        selected = canonical_entry_text(
            base_entry(
                sourceType="image",
                status="OCR_COMPLETE",
                reviewStatus="COMPLETED",
                rawText="Machine transcript.",
                cleanText="User-approved transcript.",
            )
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected["text"], "User-approved transcript.")
        self.assertEqual(selected["textField"], "cleanText")

    def test_reviewed_clean_text_precedes_typed_raw_text(self):
        selected = canonical_entry_text(
            base_entry(
                sourceType=" TyPeD ",
                rawText="Original typed entry.",
                cleanText="Corrected typed entry.",
                reviewedAt="2026-09-03T14:00:00Z",
            )
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected["text"], "Corrected typed entry.")
        self.assertEqual(selected["textField"], "cleanText")
        self.assertEqual(selected["sourceType"], "typed")

    def test_unreviewed_ocr_raw_text_is_ineligible(self):
        entry = base_entry(
            sourceType="image",
            status="OCR_COMPLETE",
            rawText="Machine transcript only.",
        )

        self.assertIsNone(canonical_entry_text(entry))
        self.assertEqual(build_entry_semantic_chunks(entry), [])

    def test_unreviewed_ocr_clean_text_is_ineligible(self):
        entry = base_entry(
            sourceType="upload",
            status="OCR_COMPLETE",
            reviewStatus="PENDING",
            rawText="Raw OCR.",
            cleanText="Generated OCR clean text.",
            reviewedAt="  ",
        )

        self.assertIsNone(canonical_entry_text(entry))

    def test_empty_canonical_text_is_ineligible(self):
        for entry in (
            base_entry(rawText=" \t\n"),
            base_entry(
                sourceType="image",
                rawText="ignored raw OCR",
                cleanText="  \n ",
            ),
        ):
            with self.subTest(entry=entry):
                self.assertIsNone(canonical_entry_text(entry))

    def test_malformed_or_missing_identities_are_rejected(self):
        invalid_entries = (
            {"userId": "user-456"},
            {"entryId": "", "userId": "user-456"},
            {"entryId": " \t", "userId": "user-456"},
            {"entryId": 123, "userId": "user-456"},
            {"entryId": "entry-123"},
            {"entryId": "entry-123", "userId": ""},
            {"entryId": "entry-123", "userId": object()},
        )

        for entry in invalid_entries:
            with self.subTest(entry=entry):
                with self.assertRaises(SemanticMemoryIdentityError):
                    canonical_entry_text(entry)
                with self.assertRaises(SemanticMemoryIdentityError):
                    build_entry_semantic_chunks(entry)

    def test_input_dictionary_is_not_mutated(self):
        entry = base_entry(
            cleanText="  Corrected\tjournal text.  ",
            reviewStatus="COMPLETED",
            nested={"unchanged": [1, 2, 3]},
        )
        original = copy.deepcopy(entry)

        canonical_entry_text(entry)
        build_entry_semantic_chunks(
            entry,
            max_chunk_size=20,
            overlap_size=4,
        )

        self.assertEqual(entry, original)

    def test_chunk_output_is_deterministic(self):
        entry = base_entry(rawText="Sentence one. Sentence two. " * 10)

        first = build_entry_semantic_chunks(
            entry,
            max_chunk_size=45,
            overlap_size=8,
        )
        second = build_entry_semantic_chunks(
            entry,
            max_chunk_size=45,
            overlap_size=8,
        )

        self.assertEqual(first, second)

    def test_entry_user_and_source_metadata_are_propagated(self):
        chunks = build_entry_semantic_chunks(base_entry())

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["entryId"], "entry-123")
        self.assertEqual(chunks[0]["userId"], "user-456")
        self.assertEqual(chunks[0]["sourceType"], "typed")
        self.assertEqual(chunks[0]["canonicalTextField"], "rawText")
        self.assertEqual(chunks[0]["entryCreatedAt"], "2026-09-01T12:00:00Z")
        self.assertEqual(chunks[0]["entryUpdatedAt"], "2026-09-02T13:30:00Z")

    def test_missing_timestamps_are_not_defaulted(self):
        entry = base_entry()
        entry.pop("createdAt")
        entry.pop("updatedAt")

        chunk = build_entry_semantic_chunks(entry)[0]

        self.assertNotIn("entryCreatedAt", chunk)
        self.assertNotIn("entryUpdatedAt", chunk)

    def test_semantic_chunk_fields_are_unchanged_from_direct_chunking(self):
        entry = base_entry(
            cleanText="  Alpha\tthought. Beta thought. Gamma thought.  ",
            reviewStatus="COMPLETED",
        )
        direct = chunk_text(
            "entry-123",
            entry["cleanText"],
            max_chunk_size=25,
            overlap_size=5,
        )
        built = build_entry_semantic_chunks(
            entry,
            max_chunk_size=25,
            overlap_size=5,
        )

        self.assertEqual(len(built), len(direct))
        for direct_chunk, built_chunk in zip(direct, built):
            for key, value in direct_chunk.items():
                self.assertEqual(built_chunk[key], value)
            self.assertEqual(built_chunk["canonicalTextField"], "cleanText")
            self.assertEqual(built_chunk["chunkingVersion"], CHUNKING_VERSION)
            self.assertEqual(
                built_chunk["chunkCount"],
                len(built),
            )
        self.assertEqual(
            [chunk["chunkOrdinal"] for chunk in built],
            list(range(len(built))),
        )

    def test_different_entry_ids_produce_different_chunk_ids(self):
        first = build_entry_semantic_chunks(base_entry(entryId="entry-a"))
        second = build_entry_semantic_chunks(base_entry(entryId="entry-b"))

        self.assertEqual(first[0]["text"], second[0]["text"])
        self.assertEqual(first[0]["contentDigest"], second[0]["contentDigest"])
        self.assertEqual(first[0]["chunkDigest"], second[0]["chunkDigest"])
        self.assertNotEqual(first[0]["chunkId"], second[0]["chunkId"])

    def test_contract_has_no_runtime_or_aws_dependencies(self):
        source_path = FUNCTION_DIR / "semantic_memory_contract.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )

        self.assertEqual(
            imported_roots,
            {"__future__", "collections", "typing", "semantic_chunking"},
        )
        for forbidden in (
            "boto3",
            "botocore",
            "os.environ",
            "getenv(",
            "datetime.now",
            "time.time",
            "random",
            "uuid",
            "secrets",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
