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
    analysis_history_prefix,
    build_analysis_history_items,
    build_legacy_analysis_history_item,
)


ANALYSIS_V2 = {
    "status": "ANALYZED",
    "analyzerType": "LLM",
    "provider": "amazon-bedrock",
    "modelId": "test-model",
    "schemaVersion": "2.0",
    "promptVersion": "journal-analysis-v1",
    "analyzedAt": "2026-07-18T10:00:00+00:00",
    "sentiment": "mixed_positive",
    "mood": "determined",
}


def base_entry() -> dict:
    return {
        "PK": "USER#test-user",
        "SK": (
            "ENTRY#2026-07-18T09:00:00+00:00"
            "#entry_test"
        ),
        "entryId": "entry_test",
        "userId": "test-user",
        "createdAt": "2026-07-18T09:00:00+00:00",
        "updatedAt": "2026-07-18T09:00:00+00:00",
    }


class AnalysisVersioningTests(unittest.TestCase):
    def test_new_analysis_creates_one_version(self):
        items = build_analysis_history_items(
            entry=base_entry(),
            user_id="test-user",
            entry_id="entry_test",
            analysis=ANALYSIS_V2,
            analysis_source="interactive",
            captured_at="2026-07-18T10:00:01+00:00",
            new_version_id="analysis_new",
            legacy_version_id="legacy_unused",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["analysisVersionId"],
            "analysis_new",
        )
        self.assertEqual(
            items[0]["analysisSource"],
            "interactive",
        )

    def test_unversioned_analysis_is_snapshotted(self):
        entry = base_entry()
        entry["analysisStatus"] = "COMPLETED"
        entry["analysis"] = {
            "status": "ANALYZED",
            "sentiment": "positive",
            "analyzedAt": (
                "2026-07-17T10:00:00+00:00"
            ),
        }

        items = build_analysis_history_items(
            entry=entry,
            user_id="test-user",
            entry_id="entry_test",
            analysis=ANALYSIS_V2,
            analysis_source="historical_reanalysis",
            captured_at="2026-07-18T10:00:01+00:00",
            new_version_id="analysis_new",
            legacy_version_id="legacy_old",
        )

        self.assertEqual(len(items), 2)
        self.assertEqual(
            items[0]["analysisSource"],
            "legacy_snapshot",
        )
        self.assertEqual(
            items[1]["analysisSource"],
            "historical_reanalysis",
        )

    def test_versioned_analysis_is_not_duplicated(self):
        entry = base_entry()
        entry["analysis"] = ANALYSIS_V2
        entry[
            "analysisVersionId"
        ] = "analysis_existing"

        items = build_analysis_history_items(
            entry=entry,
            user_id="test-user",
            entry_id="entry_test",
            analysis=ANALYSIS_V2,
            analysis_source="interactive",
            captured_at="2026-07-18T10:00:01+00:00",
            new_version_id="analysis_new",
            legacy_version_id="legacy_unused",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0]["analysisVersionId"],
            "analysis_new",
        )

    def test_legacy_builder_skips_empty_analysis(self):
        result = (
            build_legacy_analysis_history_item(
                entry=base_entry(),
                user_id="test-user",
                entry_id="entry_test",
                captured_at=(
                    "2026-07-18T10:00:01+00:00"
                ),
                version_id="legacy_unused",
            )
        )

        self.assertIsNone(result)

    def test_history_prefix_is_entry_scoped(self):
        self.assertEqual(
            analysis_history_prefix(
                "entry_test"
            ),
            "ANALYSIS#entry_test#",
        )


if __name__ == "__main__":
    unittest.main()
