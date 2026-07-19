import json
from typing import Any

from llm_journal_analyzer import (
    AnalyzerInputError,
    AnalyzerInvocationError,
    AnalyzerResponseError,
    analyze_journal_entry_llm,
)
from storage import (
    classify_historical_analysis_entry,
    get_entry_by_id,
    mark_entry_analysis_failed,
    update_entry_analysis,
)


ANALYSIS_SOURCE = "historical_reanalysis"


def workflow_result(
    *,
    entry_id: str,
    outcome: str,
    job_id: str = "",
    **values: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "entryId": entry_id,
        "outcome": outcome,
    }

    if job_id:
        result["jobId"] = job_id

    result.update(values)

    return result


def record_worker_failure(
    *,
    user_id: str,
    entry_id: str,
    failure_code: str,
    failure_message: str,
) -> None:
    try:
        mark_entry_analysis_failed(
            user_id=user_id,
            entry_id=entry_id,
            failure_code=failure_code,
            failure_message=failure_message,
        )
    except Exception as exc:
        print(json.dumps({
            "event": (
                "historical_reanalysis_"
                "failure_record_failed"
            ),
            "entryId": entry_id,
            "failureCode": failure_code,
            "recordingErrorType": (
                type(exc).__name__
            ),
        }))


def process_historical_reanalysis_entry(
    *,
    user_id: str,
    entry_id: str,
    job_id: str = "",
) -> dict[str, Any]:
    entry = get_entry_by_id(
        user_id=user_id,
        entry_id=entry_id,
    )

    if not entry:
        result = workflow_result(
            entry_id=entry_id,
            job_id=job_id,
            outcome="SKIPPED",
            skipReason="entryNotFound",
        )

        print(json.dumps({
            "event": (
                "historical_reanalysis_skipped"
            ),
            "entryId": entry_id,
            "skipReason": "entryNotFound",
        }))

        return result

    classification = (
        classify_historical_analysis_entry(
            entry
        )
    )

    if not classification["eligible"]:
        skip_reason = (
            classification.get("skipReason")
            or "notEligible"
        )

        result = workflow_result(
            entry_id=entry_id,
            job_id=job_id,
            outcome="SKIPPED",
            skipReason=skip_reason,
        )

        print(json.dumps({
            "event": (
                "historical_reanalysis_skipped"
            ),
            "entryId": entry_id,
            "skipReason": skip_reason,
        }))

        return result

    journal_text = str(
        entry.get("cleanText")
        or entry.get("rawText")
        or ""
    ).strip()

    if not journal_text:
        return workflow_result(
            entry_id=entry_id,
            job_id=job_id,
            outcome="SKIPPED",
            skipReason="noUsableText",
        )

    try:
        analysis = analyze_journal_entry_llm(
            journal_text
        )

    except AnalyzerInputError as exc:
        failure_code = type(exc).__name__

        record_worker_failure(
            user_id=user_id,
            entry_id=entry_id,
            failure_code=failure_code,
            failure_message=(
                "The historical entry could not "
                "be submitted for analysis."
            ),
        )

        print(json.dumps({
            "event": (
                "historical_reanalysis_failed"
            ),
            "entryId": entry_id,
            "failureCode": failure_code,
            "retryable": False,
        }))

        return workflow_result(
            entry_id=entry_id,
            job_id=job_id,
            outcome="FAILED",
            failureCode=failure_code,
            retryable=False,
            sdkRetryAttempts=0,
        )

    except AnalyzerInvocationError as exc:
        failure_code = exc.error_code

        record_worker_failure(
            user_id=user_id,
            entry_id=entry_id,
            failure_code=failure_code,
            failure_message=(
                "JM8 could not complete the "
                "historical analysis."
            ),
        )

        print(json.dumps({
            "event": (
                "historical_reanalysis_failed"
            ),
            "entryId": entry_id,
            "failureCode": failure_code,
            "retryable": exc.retryable,
            "sdkRetryAttempts": (
                exc.retry_attempts
            ),
        }))

        return workflow_result(
            entry_id=entry_id,
            job_id=job_id,
            outcome="FAILED",
            failureCode=failure_code,
            retryable=exc.retryable,
            sdkRetryAttempts=(
                exc.retry_attempts
            ),
        )

    except AnalyzerResponseError as exc:
        failure_code = type(exc).__name__

        record_worker_failure(
            user_id=user_id,
            entry_id=entry_id,
            failure_code=failure_code,
            failure_message=(
                "JM8 received an invalid "
                "historical analysis response."
            ),
        )

        print(json.dumps({
            "event": (
                "historical_reanalysis_failed"
            ),
            "entryId": entry_id,
            "failureCode": failure_code,
            "retryable": False,
        }))

        return workflow_result(
            entry_id=entry_id,
            job_id=job_id,
            outcome="FAILED",
            failureCode=failure_code,
            retryable=False,
            sdkRetryAttempts=0,
        )

    updated_entry = update_entry_analysis(
        user_id=user_id,
        entry_id=entry_id,
        analysis=analysis,
        analysis_source=ANALYSIS_SOURCE,
    )

    if not updated_entry:
        failure_code = (
            "AnalysisUpdateFailed"
        )

        print(json.dumps({
            "event": (
                "historical_reanalysis_failed"
            ),
            "entryId": entry_id,
            "failureCode": failure_code,
            "retryable": True,
        }))

        return workflow_result(
            entry_id=entry_id,
            job_id=job_id,
            outcome="FAILED",
            failureCode=failure_code,
            retryable=True,
            sdkRetryAttempts=0,
        )

    usage = analysis.get("usage") or {}

    result = workflow_result(
        entry_id=entry_id,
        job_id=job_id,
        outcome="COMPLETED",
        analysisSource=ANALYSIS_SOURCE,
        analysisVersionCount=(
            updated_entry.get(
                "analysisVersionCount",
                0,
            )
        ),
        inputTokens=usage.get(
            "inputTokens",
            0,
        ),
        outputTokens=usage.get(
            "outputTokens",
            0,
        ),
        totalTokens=usage.get(
            "totalTokens",
            0,
        ),
    )

    print(json.dumps({
        "event": (
            "historical_reanalysis_completed"
        ),
        "entryId": entry_id,
        "analysisSource": ANALYSIS_SOURCE,
        "totalTokens": result[
            "totalTokens"
        ],
    }))

    return result


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError(
            "Workflow input must be "
            "a JSON object."
        )

    user_id = str(
        event.get("userId")
        or ""
    ).strip()

    entry_id = str(
        event.get("entryId")
        or ""
    ).strip()

    job_id = str(
        event.get("jobId")
        or ""
    ).strip()

    if not user_id:
        raise ValueError(
            "userId is required."
        )

    if not entry_id:
        raise ValueError(
            "entryId is required."
        )

    return (
        process_historical_reanalysis_entry(
            user_id=user_id,
            entry_id=entry_id,
            job_id=job_id,
        )
    )
