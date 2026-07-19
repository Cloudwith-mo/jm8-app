from typing import Any

from storage import (
    begin_historical_reanalysis_job,
    fail_historical_reanalysis_job,
    historical_reanalysis_page_id,
    list_historical_reanalysis_candidates,
    record_historical_reanalysis_page,
)


def required_value(
    event: dict[str, Any],
    field: str,
) -> str:
    value = str(
        event.get(field)
        or ""
    ).strip()

    if not value:
        raise ValueError(
            f"{field} is required."
        )

    return value


def prepare_page(
    event: dict[str, Any],
) -> dict[str, Any]:
    user_id = required_value(
        event,
        "userId",
    )

    job_id = required_value(
        event,
        "jobId",
    )

    cursor_value = event.get("cursor")

    cursor = (
        str(cursor_value).strip()
        if cursor_value
        else None
    )

    job = begin_historical_reanalysis_job(
        user_id,
        job_id,
    )

    page_size = int(
        job.get("pageSize")
        or 25
    )

    page = (
        list_historical_reanalysis_candidates(
            user_id,
            limit=page_size,
            cursor=cursor,
        )
    )

    return {
        "jobId": job_id,
        "pageId": (
            historical_reanalysis_page_id(
                job_id,
                cursor,
            )
        ),
        "entries": page["entries"],
        "count": page["count"],
        "evaluatedEntries": page[
            "evaluatedEntries"
        ],
        "pageSize": page["pageSize"],
        "hasMore": page["hasMore"],
        "nextCursor": page["nextCursor"],
    }


def record_page(
    event: dict[str, Any],
) -> dict[str, Any]:
    user_id = required_value(
        event,
        "userId",
    )

    job_id = required_value(
        event,
        "jobId",
    )

    page = event.get("page")
    results = event.get("results")

    if not isinstance(page, dict):
        raise ValueError(
            "page is required."
        )

    if not isinstance(results, list):
        raise ValueError(
            "results must be a list."
        )

    page_id = required_value(
        page,
        "pageId",
    )

    has_more = (
        page.get("hasMore") is True
    )

    next_cursor = page.get(
        "nextCursor"
    )

    job = record_historical_reanalysis_page(
        user_id,
        job_id,
        page_id=page_id,
        results=results,
        has_more=has_more,
        next_cursor=(
            str(next_cursor)
            if next_cursor
            else None
        ),
    )

    return {
        "jobId": job_id,
        "status": job.get("status"),
        "hasMore": has_more,
        "nextCursor": (
            str(next_cursor)
            if next_cursor
            else None
        ),
        "processedEntries": job.get(
            "processedEntries",
            0,
        ),
        "completedEntries": job.get(
            "completedEntries",
            0,
        ),
        "failedEntries": job.get(
            "failedEntries",
            0,
        ),
        "skippedEntries": job.get(
            "skippedEntries",
            0,
        ),
        "remainingEntries": job.get(
            "remainingEntries",
            0,
        ),
    }


def record_workflow_failure(
    event: dict[str, Any],
) -> dict[str, Any]:
    user_id = required_value(
        event,
        "userId",
    )

    job_id = required_value(
        event,
        "jobId",
    )

    workflow_error = event.get(
        "workflowError"
    )

    if not isinstance(
        workflow_error,
        dict,
    ):
        workflow_error = {}

    failure_code = str(
        workflow_error.get("Error")
        or "WorkflowFailure"
    ).strip()

    job = fail_historical_reanalysis_job(
        user_id,
        job_id,
        failure_code=failure_code,
    )

    return {
        "jobId": job_id,
        "status": job.get(
            "status",
            "FAILED",
        ),
        "failureCode": job.get(
            "failureCode",
            "WorkflowFailure",
        ),
    }


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError(
            "Workflow input must be "
            "a JSON object."
        )

    action = str(
        event.get("action")
        or ""
    ).strip().upper()

    if action == "PREPARE":
        return prepare_page(event)

    if action == "RECORD":
        return record_page(event)

    if action == "FAIL":
        return record_workflow_failure(
            event
        )

    raise ValueError(
        "Unsupported historical "
        "re-analysis action."
    )
