"""Phase 3C3B integration boundary for blocking writes during deletion."""

from __future__ import annotations

import re
from typing import Callable

from account_deletion_store import DeletionStoreUnavailable, has_active_deletion


DELETION_GUARD_ENFORCEMENT_ACTIVE = True
AVAILABLE_DURING_DELETION = (
    ("GET", r"^/account/deletion-requests/del_[a-f0-9]{32}$"),
)
PENDING_MUTATING_ROUTE_CLASSES = {
    "entries": (
        ("POST", r"^/entries$"),
        ("PUT", r"^/entries/[^/]+/review$"),
        ("DELETE", r"^/entries/[^/]+$"),
    ),
    "uploads": (("POST", r"^/upload-url$"),),
    "ocr": (
        ("POST", r"^/entries/[^/]+/ocr$"),
        ("POST", r"^/entries/[^/]+/ocr/retry$"),
    ),
    "analysis": (
        ("POST", r"^/entries/[^/]+/analyze$"),
        ("POST", r"^/analysis/reanalysis/jobs$"),
        ("POST", r"^/analysis/reanalysis/jobs/[^/]+/retry$"),
    ),
    "askJm8": (("POST", r"^/insights/ask$"),),
    "askHistory": (("DELETE", r"^/insights/ask/history/[^/]+$"),),
    "billing": (
        ("POST", r"^/billing/checkout$"),
        ("POST", r"^/billing/portal$"),
    ),
    "accountExports": (("POST", r"^/account/exports$"),),
}


def deletion_pending(
    subject: str,
    *,
    active_lookup: Callable[[str], bool] = has_active_deletion,
) -> bool:
    """Return whether a privacy-safe active deletion lock exists."""
    return active_lookup(subject)


class AccountDeletionInProgress(RuntimeError):
    pass


class DeletionGuardUnavailable(RuntimeError):
    pass


def ensure_user_mutation_allowed(
    subject: str,
    *,
    active_lookup: Callable[[str], bool] = has_active_deletion,
) -> None:
    try:
        active = active_lookup(subject)
    except DeletionStoreUnavailable:
        raise DeletionGuardUnavailable() from None
    if active:
        raise AccountDeletionInProgress()


def route_requires_deletion_guard(method: str, path: str) -> bool:
    normalized_method = str(method or "").upper()
    normalized_path = str(path or "")
    return any(
        normalized_method == route_method
        and re.fullmatch(pattern, normalized_path) is not None
        for routes in PENDING_MUTATING_ROUTE_CLASSES.values()
        for route_method, pattern in routes
    )


def route_remains_available_during_deletion(method: str, path: str) -> bool:
    normalized_method = str(method or "").upper()
    normalized_path = str(path or "")
    return any(
        normalized_method == route_method
        and re.fullmatch(pattern, normalized_path) is not None
        for route_method, pattern in AVAILABLE_DURING_DELETION
    )
