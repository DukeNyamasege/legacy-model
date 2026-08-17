from __future__ import annotations

"""Keep Telegram login observability from scanning the entire account fleet.

A fresh OAuth callback updates every Options account returned for that one Deriv
login immediately before the client session is created. We therefore only need to
inspect a narrow recent-update cohort around the selected account, not every
historical ManagedAccount preserved in PostgreSQL.
"""

import os
from datetime import timedelta
from typing import Any

from sqlalchemy import select

import app.api as base_api
import app.vps_session_observability as observability
from app.models import ManagedAccount
from app.token_store import decrypt_auth_payload


_INSTALLED = False


def _bounded_linked_login_contexts(selected_managed_id: int) -> list[dict[str, Any]]:
    selected = observability._managed_context(int(selected_managed_id))
    if selected is None:
        return []

    identity = base_api.login_identity_from_payload(selected["payload"])
    selected_row = base_api.REPOSITORY.managed_account(int(selected_managed_id)) or {}
    selected_updated = selected_row.get("updated_at")
    if selected_updated is None:
        return [selected]

    window_seconds = max(
        2.0,
        min(30.0, float(os.getenv("VPS_LOGIN_LINK_WINDOW_SECONDS", "8"))),
    )
    candidate_limit = max(
        8,
        min(256, int(os.getenv("VPS_LOGIN_LINK_CANDIDATE_LIMIT", "96"))),
    )
    lower_bound = selected_updated - timedelta(seconds=window_seconds)
    upper_bound = selected_updated + timedelta(seconds=window_seconds)

    with base_api.DATABASE.session() as session:
        candidates = list(
            session.scalars(
                select(ManagedAccount)
                .where(
                    ManagedAccount.updated_at >= lower_bound,
                    ManagedAccount.updated_at <= upper_bound,
                )
                .order_by(ManagedAccount.updated_at.desc(), ManagedAccount.id.desc())
                .limit(candidate_limit)
            ).all()
        )

    matched_ids: list[int] = []
    for row in candidates:
        managed_id = int(row.id)
        try:
            payload = decrypt_auth_payload(
                row.token_secret,
                base_api.CONFIG.deriv.token_encryption_key,
            )
        except Exception:
            continue
        if identity:
            if base_api.login_identity_from_payload(payload) != identity:
                continue
        elif managed_id != int(selected_managed_id):
            continue
        matched_ids.append(managed_id)

    if int(selected_managed_id) not in matched_ids:
        matched_ids.append(int(selected_managed_id))

    contexts: list[dict[str, Any]] = []
    seen: set[int] = set()
    for managed_id in matched_ids:
        if managed_id in seen:
            continue
        context = observability._managed_context(managed_id)
        if context is None:
            continue
        seen.add(managed_id)
        contexts.append(context)

    contexts.sort(
        key=lambda item: (
            0 if item["account_type"] == "demo" else 1,
            item["account_id"],
        )
    )
    observability.LOGGER.info(
        "TELEGRAM_LOGIN_CONTEXT_BOUNDED selected_managed_id=%s candidates=%s linked=%s "
        "window_seconds=%.1f fleet_scan=false",
        selected_managed_id,
        len(candidates),
        len(contexts),
        window_seconds,
    )
    return contexts or [selected]


def install_vps_login_observability_hotfix() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    observability._linked_login_contexts = _bounded_linked_login_contexts
    observability.LOGGER.warning(
        "VPS_LOGIN_OBSERVABILITY_HOTFIX_ACTIVE fleet_scan=false recent_oauth_cohort=true"
    )
    _INSTALLED = True
