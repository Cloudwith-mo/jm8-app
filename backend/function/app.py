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
from insights_ask_context import (
    AskContextInputError,
    build_ask_context,
)
from insights_ask_answer import (
    AskAnswerInputError,
    AskAnswerInvocationError,
    AskAnswerResponseError,
    answer_journal_history,
)
from insights_ask_semantic_context import (
    AskSemanticContextError,
    compose_ask_context_with_semantic_evidence,
)
from semantic_query_retrieval import (
    SemanticQueryInputError,
    SemanticQueryUnavailableError,
    retrieve_semantic_query_evidence,
)
from ask_usage import (
    AskUsageLimitError,
    AskUsageUnavailableError,
    complete_ask_usage,
    fail_ask_usage,
    reserve_ask_usage,
)
from ask_history_persistence import (
    AskHistoryPersistenceUnavailableError,
    persist_ask_history,
    rollback_persisted_ask_history,
)
from ask_history_api import (
    AskHistoryApiError,
    delete_ask_history_for_api,
    get_ask_history_for_api,
    list_ask_history_for_api,
)
from entry_analysis_usage import (
    EntryAnalysisUsageLimitError,
    EntryAnalysisUsageUnavailableError,
    complete_entry_analysis_usage,
    fail_entry_analysis_usage,
    reserve_entry_analysis_usage,
)
from usage_read import (
    UsageReadUnavailableError,
    get_usage_snapshot,
)
from account_entitlement import (
    AccountEntitlementUnavailableError,
    get_account_entitlement,
)
from billing_checkout import (
    BillingCheckoutError,
    create_billing_checkout,
)
from billing_portal import (
    BillingPortalError,
    create_billing_portal,
)
from billing_webhook import (
    BillingWebhookError,
    process_billing_webhook,
)
from account_export_api import (
    AccountExportApiError,
    create_account_export,
    get_account_export,
    list_account_exports,
)
from account_deletion_api import (
    AccountDeletionApiError,
    create_account_deletion_request,
    get_account_deletion_request,
)
from account_deletion_guard import (
    AccountDeletionInProgress,
    DeletionGuardUnavailable,
    ensure_user_mutation_allowed,
    route_requires_deletion_guard,
)
from product_telemetry_integration import (
    is_grounded_answer,
    record_authenticated_activity,
    record_product_outcome,
)
from ocr_workflow_client import start_ocr_execution
import base64
import boto3
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


def build_semantic_ask_context(
    user_id: str,
    ask_context: dict[str, Any],
) -> dict[str, Any]:
    semantic_retrieval = (
        retrieve_semantic_query_evidence(
            boto3.client("bedrock-runtime"),
            boto3.client("dynamodb"),
            table_name=os.environ[
                "ENTRY_CHUNKS_TABLE_NAME"
            ],
            user_id=user_id,
            question=ask_context.get(
                "question"
            ),
            top_k=24,
        )
    )
    return (
        compose_ask_context_with_semantic_evidence(
            ask_context,
            semantic_retrieval,
        )
    )


def deletion_guard_response(user_id):
    try:
        ensure_user_mutation_allowed(user_id)
    except AccountDeletionInProgress:
        return response(409, {
            "error": "AccountDeletionInProgress",
            "message": "Account deletion is in progress.",
            "retryable": False,
        })
    except DeletionGuardUnavailable:
        return response(503, {
            "error": "AccountDeletionGuardUnavailable",
            "message": "Account status is temporarily unavailable.",
            "retryable": True,
        })
    return None


def lambda_handler(event, context):
    try:
        method, path = get_method_and_path(event)

        if method == "OPTIONS":
            return response(204, {})

        if method == "POST" and path == "/billing/webhook":
            try:
                result = process_billing_webhook(event)
            except BillingWebhookError as exc:
                return response(exc.status_code, exc.payload)
            return response(200, result)

        if (
            path == "/account/deletion-requests"
            or path.startswith("/account/deletion-requests/")
        ):
            claims = get_verified_cognito_claims(event)
            if claims is None:
                return response(401, {
                    "error": "Unauthorized",
                    "message": "Authentication is required.",
                })
            deletion_subject = str(claims["sub"])
            try:
                if method == "POST" and path == "/account/deletion-requests":
                    try:
                        deletion_body = parse_body(event)
                    except (
                        json.JSONDecodeError,
                        UnicodeDecodeError,
                        ValueError,
                    ):
                        return response(400, {
                            "error": "InvalidRequest",
                            "message": "The request body must be valid JSON.",
                            "retryable": False,
                        })
                    if not isinstance(deletion_body, dict):
                        return response(400, {
                            "error": "InvalidRequest",
                            "message": "The request body must be a JSON object.",
                            "retryable": False,
                        })
                    status_code, payload = create_account_deletion_request(
                        deletion_subject,
                        claims,
                        deletion_body,
                    )
                    return response(status_code, payload)
                if method == "GET" and path.startswith(
                    "/account/deletion-requests/"
                ):
                    request_id = path[len("/account/deletion-requests/"):]
                    status_code, payload = get_account_deletion_request(
                        deletion_subject,
                        request_id,
                    )
                    return response(status_code, payload)
            except AccountDeletionApiError as exc:
                return response(exc.status_code, exc.payload)

        if path == "/account/exports" or path.startswith("/account/exports/"):
            claims = get_verified_cognito_claims(event)
            if claims is None:
                return response(401, {
                    "error": "Unauthorized",
                    "message": "Authentication is required.",
                })
            export_user_id = str(claims["sub"])
            try:
                if method == "POST" and path == "/account/exports":
                    guard_response = deletion_guard_response(export_user_id)
                    if guard_response is not None:
                        return guard_response
                    try:
                        export_body = parse_body(event)
                    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                        return response(400, {
                            "error": "InvalidRequest",
                            "message": "The request body must be valid JSON.",
                            "retryable": False,
                        })
                    if not isinstance(export_body, dict):
                        return response(400, {
                            "error": "InvalidRequest",
                            "message": "The request body must be a JSON object.",
                            "retryable": False,
                        })
                    status_code, payload = create_account_export(
                        export_user_id,
                        claims,
                        export_body,
                    )
                    return response(status_code, payload)
                if method == "GET" and path == "/account/exports":
                    status_code, payload = list_account_exports(export_user_id)
                    return response(status_code, payload)
                if method == "GET" and path.startswith("/account/exports/"):
                    export_id = path[len("/account/exports/"):]
                    status_code, payload = get_account_export(export_user_id, export_id)
                    return response(status_code, payload)
            except AccountExportApiError as exc:
                return response(exc.status_code, exc.payload)

        user_id = get_user_id(event)
        if user_id is None:
            return response(401, {
                "error": "Unauthorized",
                "message": "Authentication is required.",
            })

        requires_deletion_guard = route_requires_deletion_guard(method, path)
        if requires_deletion_guard:
            guard_response = deletion_guard_response(user_id)
            if guard_response is not None:
                return guard_response

        record_authenticated_activity(
            user_id,
            mutation_guard=(
                None
                if requires_deletion_guard
                else ensure_user_mutation_allowed
            ),
        )

        if method == "POST" and path == "/entries":
            body = parse_body(event)
            text = body.get("text", "").strip()

            if not text:
                return response(400, {"error": "Text is required."})

            entry = create_text_entry(user_id=user_id, text=text)
            record_product_outcome(
                user_id,
                "FirstEntryCreated",
            )

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

            try:
                page = list_ocr_jobs(
                    user_id=user_id,
                    status_filter=status_filter,
                    limit=query_params.get("limit", 25),
                    cursor=query_params.get("cursor"),
                )
            except ValueError:
                return response(400, {
                    "error": "InvalidOCRJobsCursor",
                    "message": (
                        "The OCR jobs cursor is invalid for this request."
                    ),
                    "retryable": False,
                })

            return response(200, {
                "jobs": page["items"],
                "count": page["count"],
                "statusFilter": status_filter,
                "limit": page["limit"],
                "nextCursor": page["nextCursor"],
            })

        if (
            method == "GET"
            and path == "/usage"
        ):
            try:
                usage_snapshot = (
                    get_usage_snapshot(
                        user_id
                    )
                )

            except UsageReadUnavailableError as exc:
                return response(
                    503,
                    exc.payload,
                )

            return response(200, {
                "usage": usage_snapshot,
            })

        if (
            method == "GET"
            and path
            == "/account/entitlement"
        ):
            try:
                account_entitlement = (
                    get_account_entitlement(
                        user_id
                    )
                )

            except (
                AccountEntitlementUnavailableError
            ) as exc:
                return response(
                    503,
                    exc.payload,
                )

            return response(200, {
                "entitlement": (
                    account_entitlement
                ),
            })

        if (
            method == "POST"
            and path
            == "/billing/checkout"
        ):
            try:
                body = parse_body(
                    event
                )

            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
            ):
                return response(400, {
                    "error":
                        "InvalidRequestBody",

                    "message":
                        (
                            "Request body must "
                            "contain valid JSON."
                        ),
                })

            if not isinstance(
                body,
                dict,
            ):
                return response(400, {
                    "error":
                        "InvalidRequestBody",

                    "message":
                        (
                            "Request body must "
                            "be a JSON object."
                        ),
                })

            try:
                checkout = (
                    create_billing_checkout(
                        user_id=user_id,
                        request_token=(
                            body.get(
                                "requestToken"
                            )
                        ),
                        email=(
                            get_user_email(
                                event
                            )
                        ),
                    )
                )

            except BillingCheckoutError as exc:
                return response(
                    exc.status_code,
                    exc.payload,
                )

            return response(201, {
                "checkout": checkout,
            })

        if (
            method == "POST"
            and path == "/billing/portal"
        ):
            try:
                portal = create_billing_portal(
                    user_id=user_id,
                )
            except BillingPortalError as exc:
                return response(
                    exc.status_code,
                    exc.payload,
                )

            return response(201, {
                "portal": portal,
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
            method == "POST"
            and path
            == "/insights/ask"
        ):
            try:
                body = parse_body(
                    event
                )
            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
            ):
                return response(400, {
                    "error": (
                        "InvalidRequestBody"
                    ),
                    "message": (
                        "Request body must "
                        "contain valid JSON."
                    ),
                })

            if not isinstance(
                body,
                dict,
            ):
                return response(400, {
                    "error": (
                        "InvalidRequestBody"
                    ),
                    "message": (
                        "Request body must "
                        "be a JSON object."
                    ),
                })

            entries = (
                list_insights_overview_entries(
                    user_id=user_id,
                )
            )

            try:
                ask_context = (
                    build_ask_context(
                        entries,
                        question=body.get(
                            "question"
                        ),
                        start_date=body.get(
                            "startDate"
                        ),
                        end_date=body.get(
                            "endDate"
                        ),
                    )
                )

            except AskContextInputError as exc:
                return response(400, {
                    "error": exc.code,
                    "message": exc.message,
                })

            try:
                usage_reservation = (
                    reserve_ask_usage(
                        user_id,
                        ask_context,
                    )
                )

            except AskUsageLimitError as exc:
                return response(
                    429,
                    exc.payload,
                )

            except AskUsageUnavailableError as exc:
                return response(
                    503,
                    exc.payload,
                )

            try:
                ask_context = (
                    build_semantic_ask_context(
                        user_id,
                        ask_context,
                    )
                )

            except SemanticQueryUnavailableError:
                fail_ask_usage(
                    user_id,
                    usage_reservation,
                )

                print(json.dumps({
                    "event": (
                        "ask_jm8_semantic_"
                        "retrieval_failed"
                    ),
                    "failureCode": (
                        "SemanticQueryUnavailableError"
                    ),
                    "retryable": True,
                }))

                return response(503, {
                    "error": (
                        "AskJM8RetrievalUnavailable"
                    ),
                    "message": (
                        "JM8 could not retrieve "
                        "journal evidence right now."
                    ),
                    "retryable": True,
                    "retryAfterSeconds": 2,
                })

            except (
                SemanticQueryInputError,
                AskSemanticContextError,
                KeyError,
            ) as exc:
                fail_ask_usage(
                    user_id,
                    usage_reservation,
                )

                print(json.dumps({
                    "event": (
                        "ask_jm8_semantic_"
                        "retrieval_failed"
                    ),
                    "failureCode": (
                        type(exc).__name__
                    ),
                    "retryable": False,
                }))

                return response(502, {
                    "error": (
                        "AskJM8InvalidRetrieval"
                    ),
                    "message": (
                        "JM8 could not validate "
                        "the retrieved journal "
                        "evidence."
                    ),
                    "retryable": False,
                })

            try:
                answer = (
                    answer_journal_history(
                        ask_context
                    )
                )

            except AskAnswerInputError as exc:
                fail_ask_usage(
                    user_id,
                    usage_reservation,
                )

                print(json.dumps({
                    "event": (
                        "ask_jm8_input_"
                        "rejected"
                    ),
                    "failureCode": (
                        exc.code
                    ),
                }))

                return response(400, {
                    "error": exc.code,
                    "message": exc.message,
                })

            except AskAnswerInvocationError as exc:
                fail_ask_usage(
                    user_id,
                    usage_reservation,
                )

                print(json.dumps({
                    "event": (
                        "ask_jm8_answer_"
                        "failed"
                    ),
                    "failureCode": (
                        "AskAnswerInvocationError"
                    ),
                    "providerErrorCode": (
                        exc.error_code
                    ),
                    "retryable": (
                        exc.retryable
                    ),
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
                    "error": (
                        "AskJM8Unavailable"
                    ),
                    "message": (
                        "JM8 could not answer "
                        "this question right now."
                    ),
                    "retryable": (
                        exc.retryable
                    ),
                }

                if exc.retryable:
                    response_body[
                        "retryAfterSeconds"
                    ] = 2

                return response(
                    status_code,
                    response_body,
                )

            except AskAnswerResponseError as exc:
                fail_ask_usage(
                    user_id,
                    usage_reservation,
                )

                print(json.dumps({
                    "event": (
                        "ask_jm8_answer_"
                        "failed"
                    ),
                    "failureCode": (
                        type(exc).__name__
                    ),
                    "retryable": False,
                    "sdkRetryAttempts": 0,
                }))

                return response(502, {
                    "error": (
                        "AskJM8InvalidResponse"
                    ),
                    "message": (
                        "JM8 could not validate "
                        "the generated answer."
                    ),
                    "retryable": False,
                })

            try:
                guard_response = deletion_guard_response(user_id)
                if guard_response is not None:
                    return guard_response
                history = (
                    persist_ask_history(
                        user_id,
                        answer,
                    )
                )

            except (
                AskHistoryPersistenceUnavailableError
            ) as exc:
                fail_ask_usage(
                    user_id,
                    usage_reservation,
                )

                return response(
                    exc.status_code,
                    exc.payload,
                )

            try:
                complete_ask_usage(
                    user_id,
                    usage_reservation,
                )

            except AskUsageUnavailableError as exc:
                rollback_persisted_ask_history(
                    user_id,
                    history,
                )

                return response(
                    503,
                    exc.payload,
                )

            if is_grounded_answer(answer):
                record_product_outcome(
                    user_id,
                    "FirstGroundedAskCompleted",
                )

            return response(200, {
                "answer": answer,
                "history": {
                    "historyVersion": (
                        history[
                            "historyVersion"
                        ]
                    ),
                    "historyId": (
                        history["historyId"]
                    ),
                    "createdAt": (
                        history["createdAt"]
                    ),
                },
            })

        if (
            method == "GET"
            and path
            == "/insights/ask/history"
        ):
            query = (
                event.get(
                    "queryStringParameters"
                )
                or {}
            )

            try:
                history_page = (
                    list_ask_history_for_api(
                        user_id,
                        limit=query.get(
                            "limit"
                        ),
                        cursor=query.get(
                            "cursor"
                        ),
                    )
                )

            except AskHistoryApiError as exc:
                return response(
                    exc.status_code,
                    exc.payload,
                )

            return response(200, {
                "count": history_page[
                    "count"
                ],
                "history": history_page[
                    "items"
                ],
                "nextCursor": (
                    history_page[
                        "nextCursor"
                    ]
                ),
            })

        history_path_prefix = (
            "/insights/ask/history/"
        )

        if (
            method in {
                "GET",
                "DELETE",
            }
            and path.startswith(
                history_path_prefix
            )
        ):
            history_id = (
                path.removeprefix(
                    history_path_prefix
                )
                .strip("/")
            )

            if (
                not history_id
                or "/" in history_id
            ):
                return response(400, {
                    "error": (
                        "InvalidAskHistoryId"
                    ),
                    "message": (
                        "The Ask JM8 history "
                        "ID is invalid."
                    ),
                    "retryable": False,
                })

            if method == "GET":
                try:
                    history = (
                        get_ask_history_for_api(
                            user_id,
                            history_id,
                        )
                    )

                except AskHistoryApiError as exc:
                    return response(
                        exc.status_code,
                        exc.payload,
                    )

                if history is None:
                    return response(404, {
                        "error": (
                            "AskHistoryNotFound"
                        ),
                        "message": (
                            "Ask JM8 history "
                            "record not found."
                        ),
                    })

                return response(200, {
                    "history": history,
                })

            try:
                deleted = (
                    delete_ask_history_for_api(
                        user_id,
                        history_id,
                    )
                )

            except AskHistoryApiError as exc:
                return response(
                    exc.status_code,
                    exc.payload,
                )

            if not deleted:
                return response(404, {
                    "error": (
                        "AskHistoryNotFound"
                    ),
                    "message": (
                        "Ask JM8 history "
                        "record not found."
                    ),
                })

            return response(200, {
                "deleted": True,
                "historyId": history_id,
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

                guard_response = deletion_guard_response(user_id)
                if guard_response is not None:
                    return guard_response
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

                guard_response = deletion_guard_response(user_id)
                if guard_response is not None:
                    return guard_response
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
                analysis_usage_reservation = (
                    reserve_entry_analysis_usage(
                        user_id
                    )
                )

            except EntryAnalysisUsageLimitError as exc:
                return response(
                    429,
                    exc.payload,
                )

            except EntryAnalysisUsageUnavailableError as exc:
                return response(
                    503,
                    exc.payload,
                )

            try:
                analysis = analyze_journal_entry_llm(
                    journal_text
                )

            except AnalyzerInputError as exc:
                guard_response = deletion_guard_response(user_id)
                if guard_response is not None:
                    return guard_response
                fail_entry_analysis_usage(
                    user_id,
                    analysis_usage_reservation,
                )

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
                guard_response = deletion_guard_response(user_id)
                if guard_response is not None:
                    return guard_response
                fail_entry_analysis_usage(
                    user_id,
                    analysis_usage_reservation,
                )

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
                guard_response = deletion_guard_response(user_id)
                if guard_response is not None:
                    return guard_response
                fail_entry_analysis_usage(
                    user_id,
                    analysis_usage_reservation,
                )

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

            try:
                guard_response = deletion_guard_response(user_id)
                if guard_response is not None:
                    return guard_response
                updated_entry = update_entry_analysis(
                    user_id=user_id,
                    entry_id=entry_id,
                    analysis=analysis,
                )

            except Exception:
                fail_entry_analysis_usage(
                    user_id,
                    analysis_usage_reservation,
                )

                raise

            try:
                complete_entry_analysis_usage(
                    user_id,
                    analysis_usage_reservation,
                )

            except EntryAnalysisUsageUnavailableError as exc:
                return response(
                    503,
                    exc.payload,
                )

            record_product_outcome(
                user_id,
                "FirstAnalysisCompleted",
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
                guard_response = deletion_guard_response(user_id)
                if guard_response is not None:
                    return guard_response
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
                guard_response = deletion_guard_response(user_id)
                if guard_response is not None:
                    return guard_response
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
    2. Local-development identity header outside production
    3. Local-development fallback outside production

    Production accepts only a non-empty string Cognito subject. API Gateway
    validates the JWT before invoking authenticated routes; this additional
    check prevents direct invocation or malformed events from substituting a
    development identity.
    """
    request_context = event.get("requestContext", {})
    if not isinstance(request_context, dict):
        request_context = {}
    authorizer = request_context.get("authorizer", {})
    if not isinstance(authorizer, dict):
        authorizer = {}
    jwt = authorizer.get("jwt", {})
    if not isinstance(jwt, dict):
        jwt = {}
    claims = jwt.get("claims", {})
    if not isinstance(claims, dict):
        claims = {}

    subject = claims.get("sub")
    if isinstance(subject, str):
        subject = subject.strip()
        if subject and len(subject) <= 512:
            return subject

    if os.environ.get("STAGE", "").strip() == "prod":
        return None

    headers = event.get("headers") or {}
    normalized_headers = {
        str(key).lower(): value for key, value in headers.items()
    }

    return normalized_headers.get("x-user-id", "demo-user")


def get_verified_cognito_claims(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return only API Gateway verified JWT claims; never use dev identity fallbacks."""
    request_context = event.get("requestContext")
    if not isinstance(request_context, dict):
        return None
    authorizer = request_context.get("authorizer")
    if not isinstance(authorizer, dict):
        return None
    jwt = authorizer.get("jwt")
    if not isinstance(jwt, dict):
        return None
    claims = jwt.get("claims")
    if not isinstance(claims, dict):
        return None
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip() or len(subject.strip()) > 512:
        return None
    return {**claims, "sub": subject.strip()}


def get_user_email(
    event: dict[str, Any],
) -> str | None:
    claims = (
        event.get(
            "requestContext",
            {},
        )
        .get(
            "authorizer",
            {},
        )
        .get(
            "jwt",
            {},
        )
        .get(
            "claims",
            {},
        )
    )

    email = str(
        claims.get("email")
        or ""
    ).strip()

    if (
        not email
        or len(email) > 512
    ):
        return None

    return email


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
