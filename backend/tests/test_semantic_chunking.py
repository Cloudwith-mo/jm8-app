import hashlib
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "function"))


from semantic_chunking import (  # noqa: E402
    CHUNKING_VERSION,
    MAX_INPUT_CHARACTERS,
    SemanticChunkingInputError,
    chunk_text,
    normalize_text,
    semantic_chunk_text,
)


ENTRY_ID = "entry-123"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class SemanticChunkingTests(unittest.TestCase):
    def test_output_is_deterministic(self):
        text = "First thought. Second thought.\n\nA different paragraph. " * 8
        first = chunk_text(ENTRY_ID, text, max_chunk_size=90, overlap_size=18)
        second = chunk_text(ENTRY_ID, text, max_chunk_size=90, overlap_size=18)

        self.assertEqual(first, second)
        self.assertEqual(
            semantic_chunk_text(
                ENTRY_ID,
                text,
                max_chunk_size=90,
                overlap_size=18,
            ),
            first,
        )

    def test_unicode_equivalence_has_identical_output(self):
        composed = "I ordered caf\u00e9 after work."
        decomposed = "I ordered cafe\u0301 after work."

        self.assertEqual(normalize_text(composed), normalize_text(decomposed))
        self.assertEqual(
            chunk_text(ENTRY_ID, composed),
            chunk_text(ENTRY_ID, decomposed),
        )

    def test_whitespace_is_normalized_without_losing_paragraphs(self):
        text = "  First\t line\r\ncontinues. \r\n \t\r\n\r\n Second\u00a0paragraph.  "

        self.assertEqual(
            normalize_text(text),
            "First line continues.\n\nSecond paragraph.",
        )

    def test_empty_or_whitespace_only_input_returns_no_chunks(self):
        for value in ("", "  \t\r\n", "\n\n\u00a0"):
            with self.subTest(value=value):
                self.assertEqual(chunk_text(ENTRY_ID, value), [])

    def test_paragraph_then_sentence_then_word_boundaries_are_preferred(self):
        paragraphs = chunk_text(
            ENTRY_ID,
            "Alpha paragraph.\n\nBeta paragraph has more words.",
            max_chunk_size=26,
            overlap_size=0,
        )
        self.assertEqual([item["text"] for item in paragraphs], [
            "Alpha paragraph.",
            "Beta paragraph has more",
            "words.",
        ])

        sentences = chunk_text(
            ENTRY_ID,
            "First complete sentence. Second complete sentence.",
            max_chunk_size=32,
            overlap_size=0,
        )
        self.assertEqual(sentences[0]["text"], "First complete sentence.")

    def test_oversized_paragraph_is_split_at_sentence_and_word_boundaries(self):
        text = (
            "This opening sentence is short. "
            "The next sentence contains several ordinary words for splitting."
        )
        chunks = chunk_text(
            ENTRY_ID,
            text,
            max_chunk_size=38,
            overlap_size=0,
        )

        self.assertEqual(chunks[0]["text"], "This opening sentence is short.")
        self.assertTrue(all(len(item["text"]) <= 38 for item in chunks))
        self.assertTrue(all(not item["text"].startswith(" ") for item in chunks))
        self.assertTrue(all(not item["text"].endswith(" ") for item in chunks))

    def test_long_words_use_a_hard_character_boundary(self):
        text = "x" * 53
        chunks = chunk_text(
            ENTRY_ID,
            text,
            max_chunk_size=20,
            overlap_size=0,
        )

        self.assertEqual([len(item["text"]) for item in chunks], [20, 20, 13])
        self.assertEqual("".join(item["text"] for item in chunks), text)

    def test_no_chunk_exceeds_the_configured_maximum(self):
        text = "One two three four five six seven eight nine ten.\n\n" + "z" * 80
        chunks = chunk_text(
            ENTRY_ID,
            text,
            max_chunk_size=24,
            overlap_size=5,
        )

        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(0 < len(item["text"]) <= 24 for item in chunks))

    def test_overlap_is_present_deterministic_and_bounded(self):
        text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
        chunks = chunk_text(
            ENTRY_ID,
            text,
            max_chunk_size=28,
            overlap_size=10,
        )

        self.assertGreater(len(chunks), 2)
        for previous, current in zip(chunks, chunks[1:]):
            shared = 0
            maximum = min(len(previous["text"]), len(current["text"]), 10)
            for size in range(1, maximum + 1):
                if previous["text"].endswith(current["text"][:size]):
                    shared = size
            self.assertGreater(shared, 0)
            self.assertLessEqual(shared, 10)

    def test_digests_and_version_match_canonical_text(self):
        text = "  A canonical thought.  "
        chunk = chunk_text(ENTRY_ID, text)[0]

        self.assertEqual(chunk["chunkingVersion"], CHUNKING_VERSION)
        self.assertEqual(CHUNKING_VERSION, "jm8-semantic-chunk-v1")
        self.assertEqual(chunk["contentDigest"], digest("A canonical thought."))
        self.assertEqual(chunk["chunkDigest"], digest(chunk["text"]))

    def test_entry_id_changes_identity_but_not_text_digests(self):
        text = "The same journal text belongs to two distinct entries."
        first = chunk_text("entry-a", text)[0]
        second = chunk_text("entry-b", text)[0]

        self.assertEqual(first["contentDigest"], second["contentDigest"])
        self.assertEqual(first["chunkDigest"], second["chunkDigest"])
        self.assertNotEqual(first["chunkId"], second["chunkId"])

    def test_entry_id_is_validated_and_preserved_verbatim(self):
        for invalid in ("", "  \t\n", None, 123):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    SemanticChunkingInputError,
                    "entry_id",
                ):
                    chunk_text(invalid, "text")  # type: ignore[arg-type]

        original_entry_id = "  entry-with-significant-spacing  "
        chunk = chunk_text(original_entry_id, "text")[0]
        stripped_chunk = chunk_text(original_entry_id.strip(), "text")[0]
        self.assertEqual(chunk["entryId"], original_entry_id)
        self.assertNotEqual(chunk["chunkId"], stripped_chunk["chunkId"])

    def test_flat_output_has_exact_persistence_ready_keys(self):
        chunk = chunk_text(ENTRY_ID, "A short journal entry.")[0]

        self.assertEqual(
            set(chunk),
            {
                "entryId",
                "chunkId",
                "chunkOrdinal",
                "text",
                "characterCount",
                "wordCount",
                "contentDigest",
                "chunkDigest",
                "chunkingVersion",
                "chunkCount",
            },
        )
        self.assertNotIn("metadata", chunk)

    def test_character_and_word_counts_match_every_chunk(self):
        chunks = chunk_text(
            ENTRY_ID,
            "Alpha beta gamma. Delta epsilon zeta.\n\nEta theta iota kappa.",
            max_chunk_size=25,
            overlap_size=6,
        )

        for chunk in chunks:
            self.assertEqual(chunk["characterCount"], len(chunk["text"]))
            self.assertEqual(chunk["wordCount"], len(chunk["text"].split()))

    def test_content_changes_change_content_digest_and_chunk_ids(self):
        original = chunk_text(
            ENTRY_ID,
            "Same first sentence. Original ending.",
            max_chunk_size=21,
            overlap_size=0,
        )
        changed = chunk_text(
            ENTRY_ID,
            "Same first sentence. Different ending.",
            max_chunk_size=21,
            overlap_size=0,
        )

        self.assertEqual(original[0]["text"], changed[0]["text"])
        self.assertNotEqual(
            original[0]["contentDigest"],
            changed[0]["contentDigest"],
        )
        self.assertNotEqual(original[0]["chunkId"], changed[0]["chunkId"])

    def test_chunk_order_and_ids_are_unique(self):
        chunks = chunk_text(
            ENTRY_ID,
            "Repeated sentence. " * 15,
            max_chunk_size=45,
            overlap_size=8,
        )

        self.assertEqual(
            [item["chunkOrdinal"] for item in chunks],
            list(range(len(chunks))),
        )
        self.assertTrue(all(item["chunkCount"] == len(chunks) for item in chunks))
        self.assertEqual(len({item["chunkId"] for item in chunks}), len(chunks))
        self.assertTrue(all(len(item["chunkId"]) == 70 for item in chunks))

    def test_input_larger_than_maximum_is_rejected(self):
        with self.assertRaisesRegex(SemanticChunkingInputError, "character limit"):
            chunk_text(ENTRY_ID, "x" * (MAX_INPUT_CHARACTERS + 1))

    def test_meaningful_text_is_preserved(self):
        words = [f"word{index}" for index in range(75)]
        text = "  " + " \t".join(words[:25]) + "\n\n" + "  ".join(words[25:])
        chunks = chunk_text(
            ENTRY_ID,
            text,
            max_chunk_size=64,
            overlap_size=12,
        )
        seen = []
        for item in chunks:
            seen.extend(item["text"].replace("\n\n", " ").split())

        cursor = 0
        for word in seen:
            if cursor < len(words) and word == words[cursor]:
                cursor += 1
        self.assertEqual(cursor, len(words))

    def test_invalid_limits_and_non_string_input_are_rejected(self):
        invalid_calls = (
            lambda: chunk_text(ENTRY_ID, None),  # type: ignore[arg-type]
            lambda: chunk_text(ENTRY_ID, "text", max_chunk_size=0),
            lambda: chunk_text(ENTRY_ID, "text", overlap_size=-1),
            lambda: chunk_text(
                ENTRY_ID,
                "text",
                max_chunk_size=10,
                overlap_size=10,
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(SemanticChunkingInputError):
                    call()


if __name__ == "__main__":
    unittest.main()
