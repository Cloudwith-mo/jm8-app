from __future__ import annotations

import hashlib
import json
from typing import (
    Any,
    Mapping,
)

from ask_history_contract import (
    AskHistoryContractError,
    build_ask_history_item,
    build_public_ask_history_detail,
    normalize_ask_history_answer,
)
from ask_history_store import (
    AskHistoryStoreError,
    create_ask_history,
    delete_ask_history_by_key,
)


class AskHistoryPersistenceUnavailableError(
    RuntimeError
):
    def __init__(
        self,
        *,
        retryable: bool,
    ):
        super().__init__(
            "Ask JM8 history persistence "
            "is unavailable."
        )

        self.retryable = bool(
            retryable
        )

        self.status_code = (
            503
            if self.retryable
            else 500
        )

        self.payload = {
            "error": (
                "AskHistoryUnavailable"
                if self.retryable
                else (
                    "AskHistory"
                    "PersistenceFailed"
                )
            ),
            "message": (
                "JM8 could not save this "
                "answer to your private "
                "history right now."
            ),
            "retryable": self.retryable,
        }

        if self.retryable:
            self.payload[
                "retryAfterSeconds"
            ] = 2


def _log_history_event(
    event_name: str,
    **fields: Any,
) -> None:
    print(json.dumps({
        "event": event_name,
        **fields,
    }))


def _normalize_answer(
    answer: Any,
) -> dict[str, Any]:
    try:
        return (
            normalize_ask_history_answer(
                answer
            )
        )
    except AskHistoryContractError as error:
        _log_history_event(
            (
                "ask_jm8_history_"
                "validation_failed"
            ),
            failureCode=error.code,
            retryable=False,
        )

        raise (
            AskHistoryPersistenceUnavailableError(
                retryable=False
            )
        ) from error


def _stable_history_id_from_answer(
    normalized_answer: Mapping[
        str,
        Any,
    ],
) -> str:
    canonical = json.dumps(
        normalized_answer,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    digest = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:24]

    return (
        f"askhist_{digest}"
    )


def stable_ask_history_id(
    answer: Any,
) -> str:
    normalized_answer = (
        _normalize_answer(
            answer
        )
    )

    return (
        _stable_history_id_from_answer(
            normalized_answer
        )
    )


def _build_duplicate_detail(
    *,
    user_id: Any,
    normalized_answer: Mapping[
        str,
        Any,
    ],
    history_id: str,
) -> dict[str, Any]:
    item = build_ask_history_item(
        user_id=user_id,
        answer=normalized_answer,
        history_id=history_id,
    )

    return (
        build_public_ask_history_detail(
            item
        )
    )


def persist_ask_history(
    user_id: Any,
    answer: Any,
) -> dict[str, Any]:
    normalized_answer = (
        _normalize_answer(
            answer
        )
    )

    history_id = (
        _stable_history_id_from_answer(
            normalized_answer
        )
    )

    try:
        history = create_ask_history(
            user_id,
            normalized_answer,
            history_id=history_id,
        )

    except AskHistoryStoreError as error:
        if (
            error.code
            == "AskHistoryAlreadyExists"
        ):
            history = (
                _build_duplicate_detail(
                    user_id=user_id,
                    normalized_answer=(
                        normalized_answer
                    ),
                    history_id=history_id,
                )
            )

            history[
                "_createdInRequest"
            ] = False

            _log_history_event(
                (
                    "ask_jm8_history_"
                    "duplicate_reused"
                ),
                status=(
                    normalized_answer[
                        "status"
                    ]
                ),
            )

            return history

        _log_history_event(
            (
                "ask_jm8_history_"
                "store_failed"
            ),
            stage="save",
            failureCode=error.code,
            retryable=error.retryable,
        )

        raise (
            AskHistoryPersistenceUnavailableError(
                retryable=error.retryable
            )
        ) from error

    history[
        "_createdInRequest"
    ] = True

    _log_history_event(
        "ask_jm8_history_saved",
        status=(
            normalized_answer["status"]
        ),
        answerVersion=(
            normalized_answer[
                "answerVersion"
            ]
        ),
    )

    return history


def _log_invalid_rollback_reference(
) -> None:
    _log_history_event(
        (
            "ask_jm8_history_"
            "rollback_failed"
        ),
        failureCode=(
            "InvalidHistoryReference"
        ),
        retryable=False,
    )


def rollback_persisted_ask_history(
    user_id: Any,
    history: Any,
) -> bool:
    if not isinstance(
        history,
        Mapping,
    ):
        _log_invalid_rollback_reference()
        return False

    created_in_request = history.get(
        "_createdInRequest"
    )

    if created_in_request is False:
        _log_history_event(
            (
                "ask_jm8_history_"
                "rollback_skipped"
            ),
            reason="duplicateReused",
        )

        return True

    if created_in_request is not True:
        _log_invalid_rollback_reference()
        return False

    history_id = history.get(
        "historyId"
    )

    created_at = history.get(
        "createdAt"
    )

    if (
        not isinstance(
            history_id,
            str,
        )
        or not isinstance(
            created_at,
            str,
        )
    ):
        _log_invalid_rollback_reference()
        return False

    try:
        deleted = (
            delete_ask_history_by_key(
                user_id,
                created_at,
                history_id,
            )
        )

    except AskHistoryStoreError as error:
        _log_history_event(
            (
                "ask_jm8_history_"
                "rollback_failed"
            ),
            failureCode=error.code,
            retryable=error.retryable,
        )

        return False

    _log_history_event(
        (
            "ask_jm8_history_"
            "rolled_back"
        ),
        deleted=bool(deleted),
    )

    return bool(deleted)
