#!/usr/bin/env python3
"""Plan or apply a bounded historical JM8 semantic-memory backfill."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import boto3


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "function"))

from semantic_backfill import (  # noqa: E402
    BACKFILL_VERSION,
    SemanticBackfillError,
    collect_backfill_entries,
    execute_semantic_backfill,
    validate_backfill_precondition,
)
from semantic_coverage_audit import (  # noqa: E402
    BACKFILL_NOT_REQUIRED,
    audit_semantic_coverage,
    collect_eligible_entries,
    collect_semantic_records,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument(
        "--stage",
        choices=("dev", "staging", "prod"),
        required=True,
    )
    parser.add_argument("--main-table", required=True)
    parser.add_argument("--entry-chunks-table", required=True)
    parser.add_argument("--expected-eligible-count", type=int, required=True)
    parser.add_argument("--maximum-entry-count", type=int, required=True)
    parser.add_argument("--poll-attempts", type=int, default=20)
    parser.add_argument("--poll-delay-seconds", type=float, default=3.0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation")
    parser.add_argument("--output", type=Path)
    return parser


def _configuration_is_valid(args: argparse.Namespace) -> bool:
    return (
        args.account_id.isdigit()
        and len(args.account_id) == 12
        and args.main_table == f"{args.app_name}-{args.stage}-main"
        and args.entry_chunks_table
        == f"{args.app_name}-{args.stage}-entry-chunks"
        and args.expected_eligible_count >= 1
        and args.maximum_entry_count >= args.expected_eligible_count
        and args.poll_attempts >= 1
        and args.poll_delay_seconds >= 0
        and (
            not args.apply
            or args.stage != "prod"
            or args.confirmation
            == f"BACKFILL-EXACTLY-{args.expected_eligible_count}"
        )
    )


def _load_current_entry(
    table: object,
    pk: str,
    sk: str,
) -> Mapping[str, object] | None:
    response = table.get_item(  # type: ignore[attr-defined]
        Key={"PK": pk, "SK": sk},
        ConsistentRead=True,
    )
    item = response.get("Item")
    return item if isinstance(item, Mapping) else None


def _audit(main_table: object, chunks_table: object) -> dict[str, object]:
    return dict(audit_semantic_coverage(
        collect_eligible_entries(main_table),
        collect_semantic_records(chunks_table),
    ))


def _write_report(path: Path | None, report: Mapping[str, object]) -> None:
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if path is not None:
        path.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


def main() -> int:
    args = _parser().parse_args()
    if not _configuration_is_valid(args):
        print("Semantic backfill configuration is invalid.", file=sys.stderr)
        return 1

    try:
        session = boto3.Session(
            profile_name=args.profile,
            region_name=args.region,
        )
        identity = session.client("sts").get_caller_identity()
        if identity.get("Account") != args.account_id:
            raise SemanticBackfillError(
                "semantic backfill account identity is invalid"
            )
        dynamodb = session.resource("dynamodb")
        main_table = dynamodb.Table(args.main_table)
        chunks_table = dynamodb.Table(args.entry_chunks_table)

        before = _audit(main_table, chunks_table)
        validate_backfill_precondition(
            before,
            expected_entry_count=args.expected_eligible_count,
            maximum_entry_count=args.maximum_entry_count,
        )

        if not args.apply:
            report = {
                "backfillVersion": BACKFILL_VERSION,
                "mode": "DRY_RUN",
                "status": "READY"
                if before.get("status") != BACKFILL_NOT_REQUIRED
                else "NOT_REQUIRED",
                "eligibleEntryCount": before.get("eligibleEntryCount"),
                "coveredEntryCount": before.get("coveredEntryCount"),
                "remainingEntryCount": (
                    int(before["eligibleEntryCount"])
                    - int(before["coveredEntryCount"])
                ),
                "journalTextRead": False,
                "mainTableMutationCount": 0,
                "bedrockInvocationCount": 0,
            }
            _write_report(args.output, report)
            return 0

        entries = collect_backfill_entries(main_table)
        result = execute_semantic_backfill(
            entries,
            chunks_table,
            current_entry_loader=lambda pk, sk: _load_current_entry(
                main_table,
                pk,
                sk,
            ),
            expected_entry_count=args.expected_eligible_count,
            maximum_entry_count=args.maximum_entry_count,
            poll_attempts=args.poll_attempts,
            poll_delay_seconds=args.poll_delay_seconds,
        )

        after = _audit(main_table, chunks_table)
        validate_backfill_precondition(
            after,
            expected_entry_count=args.expected_eligible_count,
            maximum_entry_count=args.maximum_entry_count,
        )
        if (
            after.get("status") != BACKFILL_NOT_REQUIRED
            or after.get("coveredEntryCount")
            != args.expected_eligible_count
            or after.get("missingManifestCount") != 0
            or after.get("missingEmbeddingCount") != 0
        ):
            raise SemanticBackfillError(
                "semantic backfill final audit failed"
            )

        report = {
            **result,
            "mode": "APPLY",
            "status": "COMPLETE",
            "journalTextRead": True,
            "journalTextPrinted": False,
            "identifierPrinted": False,
            "mainTableMutationCount": 0,
            "finalAuditStatus": after.get("status"),
            "missingManifestCount": after.get("missingManifestCount"),
            "missingEmbeddingCount": after.get("missingEmbeddingCount"),
            "integrityViolationCount": sum(
                int(after.get(field, 0))
                for field in (
                    "duplicateCurrentGenerationCount",
                    "invalidEmbeddingCount",
                    "orphanedSemanticRecordCount",
                    "staleGenerationCount",
                    "structuralIntegrityViolationCount",
                    "tenantIntegrityViolationCount",
                )
            ),
        }
        _write_report(args.output, report)
        return 0
    except Exception:
        print("Semantic backfill failed safely.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
