from llm_journal_analyzer import (
    AnalyzerInputError,
    AnalyzerInvocationError,
    AnalyzerResponseError,
    analyze_journal_entry_llm,
)
from historical_reanalysis_workflow_client import (
    start_historical_reanalysis_execution,
)
from insights_overview import (
    build_insights_overview,
)
from insights_trends import (
    build_mood_insights,
    build_theme_insights,
)
from insights_reports import (
    ReportPeriodError,
    build_monthly_report,
    build_weekly_report,
)
from ocr_workflow_client import start_ocr_execution
import base64
import json
import os
from typing import Any
from storage import (
    ActiveHistoricalReanalysisJobError,
    create_text_entry,
    list_entries,
    list_insights_overview_entries,
    list_ocr_jobs,
    list_entry_analysis_versions,
    list_historical_reanalysis_jobs,
    get_historical_analysis_inventory,
    create_historical_reanalysis_job,
    get_historical_reanalysis_job,
    fail_historical_reanalysis_job,
    historical_reanalysis_job_sk,
    get_entry_by_id,
    update_entry_analysis,
    create_upload_url,
    mark_ocr_failed,
    mark_entry_analysis_failed,
    get_ocr_retry_state,
    queue_ocr_job,
    OcrStateError,
    update_entry_review,
    delete_entry,
)


def lambda_handler(event, context):
    try:
        method, path = get_method_and_path(event)

        if method == "OPTIONS":
            return response(204, {})

        user_id = get_user_id(event)

        if method == "POST" and path == "/entries":
            body = parse_body(event)
            text = body.get("text", "").strip()

            if not text:
                return response(400, {"error": "Text is required."})

            entry = create_text_entry(user_id=user_id, text=text)

            return response(201, {
                "message": "Entry created.",
                "entry": entry
            })

        if method == "GET" and path == "/ocr-jobs":

            query_params = event.get("queryStringParameters") or {}

            status_filter = str(query_params.get("status") or "ALL").upper()


            allowed_statuses = {"ALL", "PENDING", "COMPLETED", "FAILED"}


            if status_filter not in allowed_statuses:

                return response(400, {

                    "error": "Invalid OCR job status.",

                    "allowedStatuses": sorted(allowed_statuses),

                })


            jobs = list_ocr_jobs(

                user_id=get_user_id(event),

                status_filter=status_filter,

            )


            return response(200, {

                "jobs": jobs,

                "count": len(jobs),

                "statusFilter": status_filter,

            })


        if (
            method == "GET"
            and path
            == "/insights/overview"
        ):
            entries = (
                list_insights_overview_entries(
                    user_id=user_id,
                )
            )

            overview = (
                build_insights_overview(
                    entries
                )
            )

            return response(200, {
                "overview": overview,
            })

        if (
            method == "GET"
            and path
            == "/insights/themes"
        ):
            entries = (
                list_insights_overview_entries(
                    user_id=user_id,
                )
            )

            themes = (
                build_theme_insights(
                    entries
                )
            )

            return response(200, {
                "themes": themes,
            })

        if (
            method == "GET"
            and path
            == "/insights/moods"
        ):
            entries = (
                list_insights_overview_entries(
                    user_id=user_id,
                )
            )

            moods = (
                build_mood_insights(
                    entries
                )
            )

            return response(200, {
                "moods": moods,
            })

        if (
            method == "GET"
            and path in {
                "/reports/weekly",
                "/reports/monthly",
            }
        ):
            query = (
                event.get(
                    "queryStringParameters"
                )
                or {}
            )

            period = query.get(
                "period"
            )

            entries = (
                list_insights_overview_entries(
                    user_id=user_id,
                )
            )

            report_type = (
                "WEEKLY"
                if path
                == "/reports/weekly"
                else "MONTHLY"
            )

            try:
                if (
                    report_type
                    == "WEEKLY"
                ):
                    report = (
                        build_weekly_report(
                            entries,
                            period=period,
                        )
                    )
                else:
                    report = (
                        build_monthly_report(
                            entries,
                            period=period,
                        )
                    )
            except ReportPeriodError as exc:
                return response(400, {
                    "error": (
                        "InvalidReportPeriod"
                    ),
                    "message": str(exc),
                    "reportType": (
                        report_type
                    ),
                })

            return response(200, {
                "report": report,
            })

        if method == "GET" and path == "/entries":
            query = event.get("queryStringParameters") or {}
            limit = int(query.get("limit", 25))

            entries = list_entries(user_id=user_id, limit=limit)

            return response(200, {
                "count": len(entries),
                "entries": entries
            })

        if (
            method == "GET"
            and path
            == "/analysis/reanalysis/jobs"
        ):
            query = (
                event.get(
                    "queryStringParameters"
                )
                or {}
            )

            status_filter = str(
                query.get("status")
                or "ALL"
            ).strip().upper()

            allowed_statuses = {
                "ALL",
                "QUEUED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
            }

            if (
                status_filter
                not in allowed_statuses
            ):
                return response(400, {
                    "error": (
                        "InvalidReanalysis"
                        "JobStatus"
                    ),
                    "message": (
                        "status must be one of "
                        "ALL, QUEUED, RUNNING, "
                        "COMPLETED, or FAILED."
                    ),
                    "allowedStatuses": sorted(
                        allowed_statuses
                    ),
                })

            raw_limit = query.get(
                "limit",
                20,
            )

            try:
                limit = int(raw_limit)
            except (
                TypeError,
                ValueError,
            ):
                return response(400, {
                    "error": "InvalidLimit",
                    "message": (
                        "limit must be an "
                        "integer between 1 and 50."
                    ),
                })

            if limit < 1 or limit > 50:
                return response(400, {
                    "error": "InvalidLimit",
                    "message": (
                        "limit must be between "
                        "1 and 50."
                    ),
                })

            jobs = (
                list_historical_reanalysis_jobs(
                    user_id=user_id,
                    status_filter=(
                        status_filter
                    ),
                    limit=limit,
                )
            )

            return response(200, {
                "jobs": jobs,
                "count": len(jobs),
                "statusFilter": (
                    status_filter
                ),
                "limit": limit,
            })

        if (
            method == "POST"
            and path
            == "/analysis/reanalysis/jobs"
        ):
            body = parse_body(event)

            raw_page_size = body.get(
                "pageSize",
                25,
            )

            try:
                page_size = int(
                    raw_page_size
                )
            except (
                TypeError,
                ValueError,
            ):
                return response(400, {
                    "error": "InvalidPageSize",
                    "message": (
                        "pageSize must be an "
                        "integer between 1 and 25."
                    ),
                })

            if (
                page_size < 1
                or page_size > 25
            ):
                return response(400, {
                    "error": "InvalidPageSize",
                    "message": (
                        "pageSize must be between "
                        "1 and 25."
                    ),
                })

            inventory = (
                get_historical_analysis_inventory(
                    user_id=user_id,
                )
            )

            eligible_entries = int(
                inventory.get(
                    "eligibleEntries",
                    0,
                )
                or 0
            )

            if eligible_entries < 1:
                return response(409, {
                    "error": "NoEligibleEntries",
                    "message": (
                        "No journal entries are "
                        "currently eligible for "
                        "historical re-analysis."
                    ),
                    "inventory": inventory,
                })

            try:
                job = (
                    create_historical_reanalysis_job(
                        user_id=user_id,
                        inventory=inventory,
                        page_size=page_size,
                    )
                )

            except (
                ActiveHistoricalReanalysisJobError
            ) as exc:
                conflict = {
                    "error": (
                        "HistoricalReanalysis"
                        "JobActive"
                    ),
                    "message": (
                        "A historical re-analysis "
                        "job is already queued or "
                        "running."
                    ),
                }

                if exc.job:
                    conflict["job"] = exc.job

                return response(
                    409,
                    conflict,
                )

            try:
                execution = (
                    start_historical_reanalysis_execution(
                        user_id=user_id,
                        job_id=job["jobId"],
                    )
                )

            except Exception as exc:
                failure_code = (
                    type(exc).__name__
                )

                try:
                    failed_job = (
                        fail_historical_reanalysis_job(
                            user_id=user_id,
                            job_id=job["jobId"],
                            failure_code=(
                                "WorkflowStartFailed"
                            ),
                        )
                    )
                except Exception:
                    failed_job = job

                print(json.dumps({
                    "event": (
                        "historical_reanalysis_"
                        "start_failed"
                    ),
                    "jobId": job.get(
                        "jobId"
                    ),
                    "failureCode": (
                        failure_code
                    ),
                }))

                return response(502, {
                    "error": (
                        "HistoricalReanalysis"
                        "StartFailed"
                    ),
                    "message": (
                        "JM8 could not start the "
                        "historical re-analysis "
                        "workflow."
                    ),
                    "retryable": True,
                    "job": failed_job,
                })

            print(json.dumps({
                "event": (
                    "historical_reanalysis_"
                    "accepted"
                ),
                "jobId": job.get(
                    "jobId"
                ),
                "eligibleEntries": (
                    eligible_entries
                ),
                "pageSize": page_size,
                "duplicateExecution": (
                    execution.get(
                        "duplicate",
                        False,
                    )
                ),
            }))

            return response(202, {
                "message": (
                    "Historical re-analysis "
                    "job accepted."
                ),
                "job": job,
            })

        if (
            method == "POST"
            and path.startswith(
                "/analysis/reanalysis/jobs/"
            )
            and path.endswith("/retry")
        ):
            retry_prefix = (
                "/analysis/reanalysis/jobs/"
            )

            job_id = (
                path.removeprefix(
                    retry_prefix
                )
                .removesuffix("/retry")
                .strip("/")
            )

            if (
                not job_id
                or "/" in job_id
            ):
                return response(400, {
                    "error": "InvalidJobId",
                    "message": (
                        "A valid historical "
                        "re-analysis job ID "
                        "is required."
                    ),
                })

            try:
                historical_reanalysis_job_sk(
                    job_id
                )
            except ValueError:
                return response(400, {
                    "error": "InvalidJobId",
                    "message": (
                        "The historical "
                        "re-analysis job ID "
                        "is invalid."
                    ),
                })

            source_job = (
                get_historical_reanalysis_job(
                    user_id=user_id,
                    job_id=job_id,
                )
            )

            if not source_job:
                return response(404, {
                    "error": "JobNotFound",
                    "message": (
                        "Historical re-analysis "
                        "job not found."
                    ),
                })

            source_status = str(
                source_job.get("status")
                or ""
            ).strip().upper()

            if source_status != "FAILED":
                return response(409, {
                    "error": (
                        "HistoricalReanalysis"
                        "JobNotRetryable"
                    ),
                    "message": (
                        "Only failed historical "
                        "re-analysis jobs can "
                        "be retried."
                    ),
                    "job": source_job,
                })

            body = parse_body(event)

            default_page_size = (
                source_job.get("pageSize")
                or 25
            )

            raw_page_size = body.get(
                "pageSize",
                default_page_size,
            )

            try:
                page_size = int(
                    raw_page_size
                )
            except (
                TypeError,
                ValueError,
            ):
                return response(400, {
                    "error": "InvalidPageSize",
                    "message": (
                        "pageSize must be an "
                        "integer between 1 and 25."
                    ),
                })

            if (
                page_size < 1
                or page_size > 25
            ):
                return response(400, {
                    "error": "InvalidPageSize",
                    "message": (
                        "pageSize must be between "
                        "1 and 25."
                    ),
                })

            inventory = (
                get_historical_analysis_inventory(
                    user_id=user_id,
                )
            )

            eligible_entries = int(
                inventory.get(
                    "eligibleEntries",
                    0,
                )
                or 0
            )

            if eligible_entries < 1:
                return response(409, {
                    "error": "NoEligibleEntries",
                    "message": (
                        "No journal entries remain "
                        "eligible for historical "
                        "re-analysis."
                    ),
                    "inventory": inventory,
                })

            try:
                retry_job = (
                    create_historical_reanalysis_job(
                        user_id=user_id,
                        inventory=inventory,
                        page_size=page_size,
                        retry_of_job_id=job_id,
                    )
                )

            except (
                ActiveHistoricalReanalysisJobError
            ) as exc:
                conflict = {
                    "error": (
                        "HistoricalReanalysis"
                        "JobActive"
                    ),
                    "message": (
                        "A historical re-analysis "
                        "job is already queued or "
                        "running."
                    ),
                }

                if exc.job:
                    conflict["job"] = exc.job

                return response(
                    409,
                    conflict,
                )

            try:
                execution = (
                    start_historical_reanalysis_execution(
                        user_id=user_id,
                        job_id=retry_job["jobId"],
                    )
                )

            except Exception as exc:
                failure_code = (
                    type(exc).__name__
                )

                try:
                    failed_job = (
                        fail_historical_reanalysis_job(
                            user_id=user_id,
                            job_id=(
                                retry_job["jobId"]
                            ),
                            failure_code=(
                                "WorkflowStartFailed"
                            ),
                        )
                    )
                except Exception:
                    failed_job = retry_job

                print(json.dumps({
                    "event": (
                        "historical_reanalysis_"
                        "retry_start_failed"
                    ),
                    "jobId": retry_job.get(
                        "jobId"
                    ),
                    "retryOfJobId": job_id,
                    "failureCode": (
                        failure_code
                    ),
                }))

                return response(502, {
                    "error": (
                        "HistoricalReanalysis"
                        "RetryStartFailed"
                    ),
                    "message": (
                        "JM8 could not start the "
                        "historical re-analysis "
                        "retry workflow."
                    ),
                    "retryable": True,
                    "job": failed_job,
                })

            print(json.dumps({
                "event": (
                    "historical_reanalysis_"
                    "retry_accepted"
                ),
                "jobId": retry_job.get(
                    "jobId"
                ),
                "retryOfJobId": job_id,
                "eligibleEntries": (
                    eligible_entries
                ),
                "pageSize": page_size,
                "duplicateExecution": (
                    execution.get(
                        "duplicate",
                        False,
                    )
                ),
            }))

            return response(202, {
                "message": (
                    "Historical re-analysis "
                    "retry accepted."
                ),
                "job": retry_job,
            })

        if (
            method == "GET"
            and path.startswith(
                "/analysis/reanalysis/jobs/"
            )
        ):
            job_id = path.removeprefix(
                "/analysis/reanalysis/jobs/"
            ).strip("/")

            if (
                not job_id
                or "/" in job_id
            ):
                return response(400, {
                    "error": "InvalidJobId",
                    "message": (
                        "A valid historical "
                        "re-analysis job ID "
                        "is required."
                    ),
                })

            try:
                historical_reanalysis_job_sk(
                    job_id
                )
            except ValueError:
                return response(400, {
                    "error": "InvalidJobId",
                    "message": (
                        "The historical "
                        "re-analysis job ID "
                        "is invalid."
                    ),
                })

            job = (
                get_historical_reanalysis_job(
                    user_id=user_id,
                    job_id=job_id,
                )
            )

            if not job:
                return response(404, {
                    "error": "JobNotFound",
                    "message": (
                        "Historical re-analysis "
                        "job not found."
                    ),
                })

            return response(200, {
                "job": job,
            })

        if (
            method == "GET"
            and path
            == "/analysis/reanalysis/dry-run"
        ):
            inventory = (
                get_historical_analysis_inventory(
                    user_id=user_id,
                )
            )

            return response(200, {
                "dryRun": True,
                "readOnly": True,
                "mutationsPerformed": 0,
                "bedrockInvocationsPerformed": 0,
                "inventory": inventory,
            })

        if (
            method == "GET"
            and path.startswith("/entries/")
            and path.endswith("/analysis-history")
        ):
            entry_id = (
                path.split("/entries/")[1]
                .split("/")[0]
            )

            entry = get_entry_by_id(
                user_id=user_id,
                entry_id=entry_id,
            )

            if not entry:
                return response(
                    404,
                    {"error": "Entry not found."},
                )

            query = (
                event.get("queryStringParameters")
                or {}
            )

            raw_limit = str(
                query.get("limit")
                or "20"
            )

            try:
                limit = int(raw_limit)
            except ValueError:
                return response(400, {
                    "error": "Invalid history limit.",
                    "message": (
                        "limit must be an integer "
                        "between 1 and 50."
                    ),
                })

            if limit < 1 or limit > 50:
                return response(400, {
                    "error": "Invalid history limit.",
                    "message": (
                        "limit must be between "
                        "1 and 50."
                    ),
                })

            versions = (
                list_entry_analysis_versions(
                    user_id=user_id,
                    entry_id=entry_id,
                    limit=limit,
                )
            )

            return response(200, {
                "entryId": entry_id,
                "count": len(versions),
                "limit": limit,
                "versions": versions,
            })

        if method == "GET" and path.startswith("/entries/"):
            entry_id = path.split("/entries/")[1].split("/")[0]

            entry = get_entry_by_id(user_id=user_id, entry_id=entry_id)

            if not entry:
                return response(404, {"error": "Entry not found."})

            return response(200, {"entry": entry})

        if method == "POST" and path.startswith("/entries/") and path.endswith("/analyze"):
            entry_id = path.split("/entries/")[1].split("/")[0]

            entry = get_entry_by_id(
                user_id=user_id,
                entry_id=entry_id,
            )

            if not entry:
                return response(
                    404,
                    {"error": "Entry not found."},
                )

            journal_text = (
                entry.get("cleanText")
                or entry.get("rawText")
                or ""
            )

            if not journal_text:
                return response(400, {
                    "error": "Entry has no text to analyze yet.",
                    "entryStatus": entry.get("status"),
                })

            try:
                analysis = analyze_journal_entry_llm(
                    journal_text
                )

            except AnalyzerInputError as exc:
                failure_code = type(exc).__name__

                failed_entry = mark_entry_analysis_failed(
                    user_id=user_id,
                    entry_id=entry_id,
                    failure_code=failure_code,
                    failure_message=(
                        "The entry could not be submitted "
                        "for analysis."
                    ),
                )

                print(json.dumps({
                    "event": "journal_analysis_failed",
                    "entryId": entry_id,
                    "failureCode": failure_code,
                }))

                return response(400, {
                    "error": "InvalidAnalysisInput",
                    "message": str(exc),
                    "entry": failed_entry,
                })

            except AnalyzerInvocationError as exc:
                failure_code = exc.error_code

                failed_entry = mark_entry_analysis_failed(
                    user_id=user_id,
                    entry_id=entry_id,
                    failure_code=failure_code,
                    failure_message=(
                        "JM8 could not complete the analysis."
                    ),
                )

                print(json.dumps({
                    "event": "journal_analysis_failed",
                    "entryId": entry_id,
                    "failureCode": (
                        "AnalyzerInvocationError"
                    ),
                    "providerErrorCode": failure_code,
                    "retryable": exc.retryable,
                    "sdkRetryAttempts": (
                        exc.retry_attempts
                    ),
                }))

                status_code = (
                    503
                    if exc.retryable
                    else 502
                )

                response_body = {
                    "error": "JournalAnalysisFailed",
                    "message": (
                        "JM8 could not analyze this entry "
                        "right now."
                    ),
                    "retryable": exc.retryable,
                    "entry": failed_entry,
                }

                if exc.retryable:
                    response_body[
                        "retryAfterSeconds"
                    ] = 2

                return response(
                    status_code,
                    response_body,
                )

            except AnalyzerResponseError as exc:
                failure_code = type(exc).__name__

                failed_entry = mark_entry_analysis_failed(
                    user_id=user_id,
                    entry_id=entry_id,
                    failure_code=failure_code,
                    failure_message=(
                        "JM8 received an invalid "
                        "analysis response."
                    ),
                )

                print(json.dumps({
                    "event": "journal_analysis_failed",
                    "entryId": entry_id,
                    "failureCode": failure_code,
                    "providerErrorCode": None,
                    "retryable": False,
                    "sdkRetryAttempts": 0,
                }))

                return response(502, {
                    "error": "JournalAnalysisFailed",
                    "message": (
                        "JM8 could not validate the "
                        "analysis response."
                    ),
                    "retryable": False,
                    "entry": failed_entry,
                })

            updated_entry = update_entry_analysis(
                user_id=user_id,
                entry_id=entry_id,
                analysis=analysis,
            )

            usage = analysis.get("usage") or {}

            print(json.dumps({
                "event": "journal_analysis_completed",
                "entryId": entry_id,
                "modelId": analysis.get("modelId"),
                "schemaVersion": analysis.get(
                    "schemaVersion"
                ),
                "inputTokens": usage.get(
                    "inputTokens",
                    0,
                ),
                "outputTokens": usage.get(
                    "outputTokens",
                    0,
                ),
                "totalTokens": usage.get(
                    "totalTokens",
                    0,
                ),
                "latencyMs": usage.get(
                    "latencyMs",
                    0,
                ),
                "sdkRetryAttempts": usage.get(
                    "sdkRetryAttempts",
                    0,
                ),
            }))

            return response(200, {
                "message": "Entry analyzed.",
                "entry": updated_entry,
            })

        if method == "POST" and path == "/upload-url":
            body = parse_body(event)

            file_name = body.get("fileName", "journal-upload.jpg")
            content_type = body.get("contentType", "image/jpeg")

            upload = create_upload_url(
                user_id=user_id,
                file_name=file_name,
                content_type=content_type
            )

            return response(201, {
                "message": "Upload URL created.",
                "upload": upload
            })

        if method == "POST" and path.startswith("/entries/") and path.endswith("/ocr/retry"):
            entry_id = path.split("/entries/")[1].split("/")[0]

            entry = get_entry_by_id(
                user_id=user_id,
                entry_id=entry_id,
            )

            if not entry:
                return response(404, {"error": "Entry not found."})

            if entry.get("sourceType") != "image":
                return response(400, {
                    "error": "OCR only works on image entries.",
                    "sourceType": entry.get("sourceType"),
                })

            body = parse_body(event)
            force = body.get("force") is True
            retry_state = get_ocr_retry_state(entry)

            if not retry_state["canRetry"] and not force:
                return response(409, {
                    "error": "OCRRetryNotAllowed",
                    "message": (
                        "Only failed OCR jobs with remaining "
                        "attempts can be retried."
                    ),
                    "retry": retry_state,
                })

            try:
                queued_entry = queue_ocr_job(
                    user_id=user_id,
                    entry_id=entry_id,
                    force=force,
                    is_retry=True,
                )
            except OcrStateError as exc:
                return response(409, {
                    "error": "OCRRetryNotAllowed",
                    "message": str(exc),
                    "retry": get_ocr_retry_state(entry),
                })

            try:
                execution = start_ocr_execution(
                    user_id=user_id,
                    entry_id=entry_id,
                    force=force,
                )
            except Exception as exc:
                failed_entry = mark_ocr_failed(
                    user_id=user_id,
                    entry_id=entry_id,
                    failure_reason=(
                        f"Could not start OCR workflow: {exc}"
                    ),
                )

                return response(502, {
                    "error": "OCRWorkflowStartFailed",
                    "message": str(exc),
                    "entry": failed_entry,
                })

            return response(202, {
                "message": "OCR retry accepted.",
                "entry": queued_entry,
                "job": {
                    "entryId": entry_id,
                    "jobStatus": "PENDING",
                    **execution,
                },
                "retry": get_ocr_retry_state(queued_entry),
            })

        if method == "POST" and path.startswith("/entries/") and path.endswith("/ocr"):
            entry_id = path.split("/entries/")[1].split("/")[0]

            entry = get_entry_by_id(
                user_id=user_id,
                entry_id=entry_id,
            )

            if not entry:
                return response(404, {"error": "Entry not found."})

            if entry.get("sourceType") != "image":
                return response(400, {
                    "error": "OCR only works on image entries.",
                    "sourceType": entry.get("sourceType"),
                })

            try:
                queued_entry = queue_ocr_job(
                    user_id=user_id,
                    entry_id=entry_id,
                )
            except OcrStateError as exc:
                return response(409, {
                    "error": "OCRNotAllowed",
                    "message": str(exc),
                    "retry": get_ocr_retry_state(entry),
                })

            try:
                execution = start_ocr_execution(
                    user_id=user_id,
                    entry_id=entry_id,
                )
            except Exception as exc:
                failed_entry = mark_ocr_failed(
                    user_id=user_id,
                    entry_id=entry_id,
                    failure_reason=(
                        f"Could not start OCR workflow: {exc}"
                    ),
                )

                return response(502, {
                    "error": "OCRWorkflowStartFailed",
                    "message": str(exc),
                    "entry": failed_entry,
                })

            return response(202, {
                "message": "OCR job accepted.",
                "entry": queued_entry,
                "job": {
                    "entryId": entry_id,
                    "jobStatus": "PENDING",
                    **execution,
                },
                "retry": get_ocr_retry_state(queued_entry),
            })

        if method == "PUT" and path.startswith("/entries/") and path.endswith("/review"):
            entry_id = path.split("/entries/")[1].split("/")[0]

            entry = get_entry_by_id(user_id=user_id, entry_id=entry_id)

            if not entry:
                return response(404, {"error": "Entry not found."})

            body = parse_body(event)
            clean_text = (body.get("cleanText") or body.get("text") or "").strip()

            if not clean_text:
                return response(400, {
                    "error": "cleanText is required."
                })

            updated_entry = update_entry_review(
                user_id=user_id,
                entry_id=entry_id,
                clean_text=clean_text
            )

            return response(200, {
                "message": "Entry review saved.",
                "entry": updated_entry
            })

        if method == "DELETE" and path.startswith("/entries/"):
            entry_id = path.split("/entries/")[1].split("/")[0]
            result = delete_entry(user_id, entry_id)

            return response(200, {
                "message": "Entry deleted",
                "result": result,
            })

        return response(404, {
            "error": "Route not found.",
            "method": method,
            "path": path
        })

    except Exception as exc:
        request_id = (
            event.get("requestContext", {})
            .get("requestId")
        )

        error_response = (
            getattr(exc, "response", {})
            or {}
        )

        aws_error = (
            error_response.get("Error")
            or {}
        )

        aws_error_code = str(
            aws_error.get("Code")
            or ""
        ) or None

        cancellation_codes = [
            str(reason.get("Code"))
            for reason in (
                error_response.get(
                    "CancellationReasons"
                )
                or []
            )
            if (
                isinstance(reason, dict)
                and reason.get("Code")
            )
        ]

        print(json.dumps({
            "event": "api_unhandled_error",
            "errorType": type(exc).__name__,
            "awsErrorCode": aws_error_code,
            "awsCancellationCodes": (
                cancellation_codes
            ),
            "requestId": request_id,
        }))

        return response(500, {
            "error": "InternalServerError",
            "message": "An unexpected server error occurred.",
        })


def get_method_and_path(event: dict[str, Any]) -> tuple[str, str]:
    # API Gateway HTTP API v2 event
    if "requestContext" in event and "http" in event["requestContext"]:
        http = event["requestContext"]["http"]
        return http.get("method", "GET"), http.get("path", "/")

    # Direct Lambda invoke fallback from Phase 1
    return "POST", "/entries/analyze-direct"


def get_user_id(event):
    """
    Auth priority:
    1. Cognito JWT claim sub from API Gateway authorizer
    2. x-user-id header for local/demo development
    3. demo-user fallback
    """
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )

    if claims.get("sub"):
        return claims["sub"]

    headers = event.get("headers") or {}
    normalized_headers = {
        str(key).lower(): value for key, value in headers.items()
    }

    return normalized_headers.get("x-user-id", "demo-user")


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")

    if body is None:
        return event

    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    if isinstance(body, str):
        return json.loads(body or "{}")

    if isinstance(body, dict):
        return body

    return {}


def response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "content-type,x-user-id,authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
        },
        "body": json.dumps(body)
    }
