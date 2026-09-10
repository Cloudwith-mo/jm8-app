"""Privacy-safe quality gates for JM8 semantic retrieval evaluations."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict


SEMANTIC_RETRIEVAL_EVALUATION_VERSION = "jm8-semantic-retrieval-evaluation-v1"
MAX_EVALUATION_CASES = 250
MAX_RESULTS_PER_CASE = 24
MIN_POSITIVE_CASES = 3
MIN_NEGATIVE_CASES = 1
MIN_MACRO_RECALL_AT_K = 0.80
MIN_MACRO_PRECISION_AT_K = 0.50
MIN_MEAN_RECIPROCAL_RANK = 0.75

_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CASE_KEYS = frozenset({
    "caseId",
    "tenantLabel",
    "relevantEntryLabels",
    "results",
})
_RESULT_KEYS = frozenset({"entryLabel", "tenantLabel", "current"})


class SemanticRetrievalEvaluationError(ValueError):
    """Raised when an evaluation input violates the privacy-safe contract."""


class EvaluationResult(TypedDict):
    entryLabel: str
    tenantLabel: str
    current: bool


class EvaluationCase(TypedDict):
    caseId: str
    tenantLabel: str
    relevantEntryLabels: list[str]
    results: list[EvaluationResult]


class EvaluationMetrics(TypedDict):
    macroRecallAtK: float
    macroPrecisionAtK: float
    meanReciprocalRank: float


class EvaluationViolations(TypedDict):
    tenantLeakCount: int
    staleResultCount: int
    duplicateResultCount: int
    negativeFalsePositiveCount: int
    missedPositiveCaseCount: int


class EvaluationThresholds(TypedDict):
    minimumPositiveCases: int
    minimumNegativeCases: int
    minimumMacroRecallAtK: float
    minimumMacroPrecisionAtK: float
    minimumMeanReciprocalRank: float


class SemanticRetrievalEvaluationReport(TypedDict):
    evaluationVersion: str
    status: Literal["PASS", "FAIL"]
    caseCount: int
    positiveCaseCount: int
    negativeCaseCount: int
    metrics: EvaluationMetrics
    violations: EvaluationViolations
    thresholds: EvaluationThresholds
    failureCodes: list[str]
    failingCaseIds: list[str]


def evaluate_semantic_retrieval(
    document: object,
) -> SemanticRetrievalEvaluationReport:
    """Evaluate ranked aliases without accepting or returning journal data."""

    cases = _validated_cases(document)
    positive_cases = [case for case in cases if case["relevantEntryLabels"]]
    negative_cases = [case for case in cases if not case["relevantEntryLabels"]]

    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    tenant_leaks = 0
    stale_results = 0
    duplicate_results = 0
    negative_false_positives = 0
    missed_positive_cases = 0
    failing_case_ids: set[str] = set()

    for case in cases:
        case_id = case["caseId"]
        expected_tenant = case["tenantLabel"]
        relevant = set(case["relevantEntryLabels"])
        results = case["results"]
        seen: set[str] = set()
        relevant_hits: set[str] = set()
        first_relevant_rank: int | None = None

        for rank, result in enumerate(results, start=1):
            entry_label = result["entryLabel"]
            if entry_label in seen:
                duplicate_results += 1
                failing_case_ids.add(case_id)
            seen.add(entry_label)
            if result["tenantLabel"] != expected_tenant:
                tenant_leaks += 1
                failing_case_ids.add(case_id)
            if result["current"] is not True:
                stale_results += 1
                failing_case_ids.add(case_id)
            if entry_label in relevant:
                relevant_hits.add(entry_label)
                if first_relevant_rank is None:
                    first_relevant_rank = rank

        if relevant:
            recall = len(relevant_hits) / len(relevant)
            precision = len(relevant_hits) / len(results) if results else 0.0
            reciprocal_rank = (
                1.0 / first_relevant_rank
                if first_relevant_rank is not None
                else 0.0
            )
            recalls.append(recall)
            precisions.append(precision)
            reciprocal_ranks.append(reciprocal_rank)
            if not relevant_hits:
                missed_positive_cases += 1
                failing_case_ids.add(case_id)
        elif results:
            negative_false_positives += len(results)
            failing_case_ids.add(case_id)

    metrics: EvaluationMetrics = {
        "macroRecallAtK": _mean(recalls),
        "macroPrecisionAtK": _mean(precisions),
        "meanReciprocalRank": _mean(reciprocal_ranks),
    }
    violations: EvaluationViolations = {
        "tenantLeakCount": tenant_leaks,
        "staleResultCount": stale_results,
        "duplicateResultCount": duplicate_results,
        "negativeFalsePositiveCount": negative_false_positives,
        "missedPositiveCaseCount": missed_positive_cases,
    }
    thresholds: EvaluationThresholds = {
        "minimumPositiveCases": MIN_POSITIVE_CASES,
        "minimumNegativeCases": MIN_NEGATIVE_CASES,
        "minimumMacroRecallAtK": MIN_MACRO_RECALL_AT_K,
        "minimumMacroPrecisionAtK": MIN_MACRO_PRECISION_AT_K,
        "minimumMeanReciprocalRank": MIN_MEAN_RECIPROCAL_RANK,
    }

    failure_codes: list[str] = []
    if len(positive_cases) < MIN_POSITIVE_CASES:
        failure_codes.append("INSUFFICIENT_POSITIVE_CASES")
    if len(negative_cases) < MIN_NEGATIVE_CASES:
        failure_codes.append("INSUFFICIENT_NEGATIVE_CASES")
    if metrics["macroRecallAtK"] < MIN_MACRO_RECALL_AT_K:
        failure_codes.append("RECALL_BELOW_THRESHOLD")
    if metrics["macroPrecisionAtK"] < MIN_MACRO_PRECISION_AT_K:
        failure_codes.append("PRECISION_BELOW_THRESHOLD")
    if metrics["meanReciprocalRank"] < MIN_MEAN_RECIPROCAL_RANK:
        failure_codes.append("RANKING_BELOW_THRESHOLD")
    if tenant_leaks:
        failure_codes.append("TENANT_LEAK_DETECTED")
    if stale_results:
        failure_codes.append("STALE_RESULT_DETECTED")
    if duplicate_results:
        failure_codes.append("DUPLICATE_RESULT_DETECTED")
    if negative_false_positives:
        failure_codes.append("NEGATIVE_FALSE_POSITIVE_DETECTED")
    if missed_positive_cases:
        failure_codes.append("POSITIVE_CASE_MISSED")

    return {
        "evaluationVersion": SEMANTIC_RETRIEVAL_EVALUATION_VERSION,
        "status": "FAIL" if failure_codes else "PASS",
        "caseCount": len(cases),
        "positiveCaseCount": len(positive_cases),
        "negativeCaseCount": len(negative_cases),
        "metrics": metrics,
        "violations": violations,
        "thresholds": thresholds,
        "failureCodes": failure_codes,
        "failingCaseIds": sorted(failing_case_ids),
    }


def validate_passing_evaluation_report(report: object) -> None:
    """Require an internally consistent passing report before activation."""

    if not isinstance(report, Mapping):
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation report is malformed"
        )
    required_keys = {
        "evaluationVersion",
        "status",
        "caseCount",
        "positiveCaseCount",
        "negativeCaseCount",
        "metrics",
        "violations",
        "thresholds",
        "failureCodes",
        "failingCaseIds",
    }
    if set(report) != required_keys:
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation report is malformed"
        )
    if report.get("evaluationVersion") != SEMANTIC_RETRIEVAL_EVALUATION_VERSION:
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation version is invalid"
        )
    if report.get("status") != "PASS":
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation did not pass"
        )
    if report.get("failureCodes") != [] or report.get("failingCaseIds") != []:
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation report is inconsistent"
        )
    if report.get("thresholds") != {
        "minimumPositiveCases": MIN_POSITIVE_CASES,
        "minimumNegativeCases": MIN_NEGATIVE_CASES,
        "minimumMacroRecallAtK": MIN_MACRO_RECALL_AT_K,
        "minimumMacroPrecisionAtK": MIN_MACRO_PRECISION_AT_K,
        "minimumMeanReciprocalRank": MIN_MEAN_RECIPROCAL_RANK,
    }:
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation thresholds are invalid"
        )
    positive_count = _nonnegative_integer(
        report.get("positiveCaseCount"), "positive case count"
    )
    negative_count = _nonnegative_integer(
        report.get("negativeCaseCount"), "negative case count"
    )
    case_count = _nonnegative_integer(report.get("caseCount"), "case count")
    if (
        positive_count < MIN_POSITIVE_CASES
        or negative_count < MIN_NEGATIVE_CASES
        or case_count != positive_count + negative_count
        or case_count > MAX_EVALUATION_CASES
    ):
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation case counts are invalid"
        )
    metrics = report.get("metrics")
    if not isinstance(metrics, Mapping) or set(metrics) != {
        "macroRecallAtK",
        "macroPrecisionAtK",
        "meanReciprocalRank",
    }:
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation metrics are malformed"
        )
    metric_thresholds = {
        "macroRecallAtK": MIN_MACRO_RECALL_AT_K,
        "macroPrecisionAtK": MIN_MACRO_PRECISION_AT_K,
        "meanReciprocalRank": MIN_MEAN_RECIPROCAL_RANK,
    }
    for name, threshold in metric_thresholds.items():
        value = _bounded_metric(metrics.get(name), name)
        if value < threshold:
            raise SemanticRetrievalEvaluationError(
                "semantic retrieval evaluation metrics did not pass"
            )
    violations = report.get("violations")
    if not isinstance(violations, Mapping) or set(violations) != {
        "tenantLeakCount",
        "staleResultCount",
        "duplicateResultCount",
        "negativeFalsePositiveCount",
        "missedPositiveCaseCount",
    }:
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation violations are malformed"
        )
    if any(
        _nonnegative_integer(value, name) != 0
        for name, value in violations.items()
    ):
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation contains violations"
        )


def _validated_cases(document: object) -> list[EvaluationCase]:
    if not isinstance(document, Mapping) or set(document) != {
        "evaluationVersion",
        "cases",
    }:
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation input is malformed"
        )
    if document.get("evaluationVersion") != SEMANTIC_RETRIEVAL_EVALUATION_VERSION:
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation version is invalid"
        )
    raw_cases = document.get("cases")
    if (
        not isinstance(raw_cases, Sequence)
        or isinstance(raw_cases, (str, bytes, bytearray))
        or not raw_cases
        or len(raw_cases) > MAX_EVALUATION_CASES
    ):
        raise SemanticRetrievalEvaluationError(
            "semantic retrieval evaluation cases are invalid"
        )
    cases: list[EvaluationCase] = []
    case_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping) or set(raw_case) != _CASE_KEYS:
            raise SemanticRetrievalEvaluationError(
                "semantic retrieval evaluation case is malformed"
            )
        case_id = _alias(raw_case.get("caseId"), "case alias")
        tenant_label = _alias(raw_case.get("tenantLabel"), "tenant alias")
        if case_id in case_ids:
            raise SemanticRetrievalEvaluationError(
                "semantic retrieval evaluation case aliases are duplicated"
            )
        case_ids.add(case_id)
        relevant = _alias_list(
            raw_case.get("relevantEntryLabels"),
            "relevant entry aliases",
        )
        raw_results = raw_case.get("results")
        if (
            not isinstance(raw_results, Sequence)
            or isinstance(raw_results, (str, bytes, bytearray))
            or len(raw_results) > MAX_RESULTS_PER_CASE
        ):
            raise SemanticRetrievalEvaluationError(
                "semantic retrieval evaluation results are invalid"
            )
        results: list[EvaluationResult] = []
        for raw_result in raw_results:
            if not isinstance(raw_result, Mapping) or set(raw_result) != _RESULT_KEYS:
                raise SemanticRetrievalEvaluationError(
                    "semantic retrieval evaluation result is malformed"
                )
            current = raw_result.get("current")
            if not isinstance(current, bool):
                raise SemanticRetrievalEvaluationError(
                    "semantic retrieval evaluation current marker is invalid"
                )
            results.append({
                "entryLabel": _alias(
                    raw_result.get("entryLabel"), "result entry alias"
                ),
                "tenantLabel": _alias(
                    raw_result.get("tenantLabel"), "result tenant alias"
                ),
                "current": current,
            })
        cases.append({
            "caseId": case_id,
            "tenantLabel": tenant_label,
            "relevantEntryLabels": relevant,
            "results": results,
        })
    return cases


def _alias_list(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) > MAX_RESULTS_PER_CASE
    ):
        raise SemanticRetrievalEvaluationError(f"{label} are invalid")
    aliases = [_alias(item, label) for item in value]
    if len(set(aliases)) != len(aliases):
        raise SemanticRetrievalEvaluationError(f"{label} are duplicated")
    return aliases


def _alias(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ALIAS_PATTERN.fullmatch(value):
        raise SemanticRetrievalEvaluationError(f"{label} is invalid")
    return value


def _nonnegative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SemanticRetrievalEvaluationError(f"{label} is invalid")
    return value


def _bounded_metric(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SemanticRetrievalEvaluationError(f"{label} is invalid")
    converted = float(value)
    if not 0.0 <= converted <= 1.0:
        raise SemanticRetrievalEvaluationError(f"{label} is invalid")
    return converted


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


__all__ = [
    "MAX_EVALUATION_CASES",
    "MAX_RESULTS_PER_CASE",
    "MIN_MACRO_PRECISION_AT_K",
    "MIN_MACRO_RECALL_AT_K",
    "MIN_MEAN_RECIPROCAL_RANK",
    "MIN_NEGATIVE_CASES",
    "MIN_POSITIVE_CASES",
    "SEMANTIC_RETRIEVAL_EVALUATION_VERSION",
    "SemanticRetrievalEvaluationError",
    "SemanticRetrievalEvaluationReport",
    "evaluate_semantic_retrieval",
    "validate_passing_evaluation_report",
]
