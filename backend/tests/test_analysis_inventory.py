import os
import unittest

os.environ.setdefault(
    "AWS_ACCESS_KEY_ID",
    "testing",
)
os.environ.setdefault(
    "AWS_SECRET_ACCESS_KEY",
    "testing",
)
os.environ.setdefault(
    "AWS_DEFAULT_REGION",
    "us-east-1",
)
os.environ.setdefault(
    "AWS_EC2_METADATA_DISABLED",
    "true",
)
os.environ.setdefault(
    "TABLE_NAME",
    "journalm8-test-main",
)
os.environ.setdefault(
    "RAW_BUCKET",
    "journalm8-test-raw",
)

from storage import (  # noqa: E402
    build_historical_analysis_inventory,
    classify_historical_analysis_entry,
)


class AnalysisInventoryTests(
    unittest.TestCase
):
    def test_empty_inventory(self):
        inventory = (
            build_historical_analysis_inventory(
                []
            )
        )

        self.assertEqual(
            inventory["totalEntries"],
            0,
        )
        self.assertEqual(
            inventory[
                "estimatedBedrockRequests"
            ],
            0,
        )

    def test_versioned_entry_is_skipped(self):
        entry = {
            "cleanText": "Usable journal text.",
            "analysisVersionId": (
                "analysis_existing"
            ),
            "analysisVersionCount": 2,
        }

        inventory = (
            build_historical_analysis_inventory(
                [entry]
            )
        )

        self.assertEqual(
            inventory[
                "alreadyVersionedEntries"
            ],
            1,
        )
        self.assertEqual(
            inventory["eligibleEntries"],
            0,
        )
        self.assertEqual(
            inventory["skipReasons"][
                "alreadyVersioned"
            ],
            1,
        )

    def test_legacy_analysis_is_eligible(self):
        entry = {
            "rawText": "Legacy journal text.",
            "analysisStatus": "COMPLETED",
            "analysis": {
                "mood": "focused",
            },
        }

        inventory = (
            build_historical_analysis_inventory(
                [entry]
            )
        )

        self.assertEqual(
            inventory[
                "legacyAnalyzedEntries"
            ],
            1,
        )
        self.assertEqual(
            inventory["eligibleEntries"],
            1,
        )
        self.assertEqual(
            inventory[
                "estimatedBedrockRequests"
            ],
            1,
        )

    def test_never_analyzed_entry_is_eligible(self):
        entry = {
            "cleanText": "New journal text.",
            "analysisStatus": "NOT_ANALYZED",
        }

        inventory = (
            build_historical_analysis_inventory(
                [entry]
            )
        )

        self.assertEqual(
            inventory[
                "neverAnalyzedEntries"
            ],
            1,
        )
        self.assertEqual(
            inventory["eligibleEntries"],
            1,
        )

    def test_missing_text_is_skipped(self):
        entry = {
            "cleanText": "   ",
            "rawText": "",
            "analysisStatus": "NOT_ANALYZED",
        }

        inventory = (
            build_historical_analysis_inventory(
                [entry]
            )
        )

        self.assertEqual(
            inventory[
                "entriesWithoutUsableText"
            ],
            1,
        )
        self.assertEqual(
            inventory["eligibleEntries"],
            0,
        )
        self.assertEqual(
            inventory["skipReasons"][
                "noUsableText"
            ],
            1,
        )

    def test_failed_entry_with_text_is_eligible(self):
        entry = {
            "rawText": "Retryable journal text.",
            "analysisStatus": "FAILED",
        }

        classification = (
            classify_historical_analysis_entry(
                entry
            )
        )

        self.assertTrue(
            classification[
                "failedOrIncomplete"
            ]
        )
        self.assertTrue(
            classification["eligible"]
        )

    def test_inventory_counts_are_consistent(self):
        entries = [
            {
                "rawText": "Versioned",
                "analysisVersionCount": 1,
            },
            {
                "rawText": "Legacy",
                "analysis": {
                    "mood": "calm",
                },
            },
            {
                "rawText": "Never analyzed",
                "analysisStatus": (
                    "NOT_ANALYZED"
                ),
            },
            {
                "rawText": "   ",
                "analysisStatus": (
                    "NOT_ANALYZED"
                ),
            },
        ]

        inventory = (
            build_historical_analysis_inventory(
                entries
            )
        )

        self.assertEqual(
            inventory["totalEntries"],
            4,
        )
        self.assertEqual(
            inventory[
                "entriesWithUsableText"
            ],
            3,
        )
        self.assertEqual(
            inventory[
                "entriesWithoutUsableText"
            ],
            1,
        )
        self.assertEqual(
            inventory["eligibleEntries"],
            2,
        )
        self.assertEqual(
            inventory["skippedEntries"],
            2,
        )
        self.assertEqual(
            inventory[
                "estimatedBedrockRequests"
            ],
            2,
        )


if __name__ == "__main__":
    unittest.main()
