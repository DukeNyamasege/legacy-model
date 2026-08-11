from __future__ import annotations

import json
from typing import Any

from app.models import RuntimePreference, utc_now


AIDR_TRAP_PREFIX = "aidr_adaptive_trap:"
MAX_TRAP_SCORE = 5


def _key(managed_account_id: int) -> str:
    return f"{AIDR_TRAP_PREFIX}{int(managed_account_id)}"


def _base_repo(repo: Any) -> Any:
    return getattr(repo, "base", repo)


def _normalize_state(data: dict[str, Any]) -> dict[str, Any]:
    score = int(float(data.get("trap_score") or 0))
    return {
        "trap_score": max(0, min(MAX_TRAP_SCORE, score)),
        "post_virtual_recovery_losses": max(
            0,
            int(float(data.get("post_virtual_recovery_losses") or 0)),
        ),
    }


def _read_state(repo: Any, managed_account_id: int) -> dict[str, Any]:
    try:
        raw = str(_base_repo(repo).runtime_preference(_key(managed_account_id)) or "")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    return _normalize_state(data)


def _read_state_from_session(session: Any, managed_account_id: int) -> dict[str, Any]:
    row = session.get(RuntimePreference, _key(managed_account_id))
    if row is None:
        return _normalize_state({})
    try:
        data = json.loads(str(row.preference_value or ""))
    except Exception:
        data = {}
    return _normalize_state(data)


def _write_state(repo: Any, managed_account_id: int, state: dict[str, Any]) -> None:
    payload = {
        "trap_score": max(
            0,
            min(MAX_TRAP_SCORE, int(float(state.get("trap_score") or 0))),
        ),
        "post_virtual_recovery_losses": max(
            0,
            int(float(state.get("post_virtual_recovery_losses") or 0)),
        ),
    }
    try:
        _base_repo(repo).set_runtime_preference(
            _key(managed_account_id),
            json.dumps(payload, separators=(",", ":")),
        )
    except Exception:
        pass


def _write_state_to_session(
    session: Any,
    managed_account_id: int,
    state: dict[str, Any],
) -> None:
    payload = _normalize_state(state)
    row = session.get(RuntimePreference, _key(managed_account_id))
    if row is None:
        row = RuntimePreference(preference_key=_key(managed_account_id))
        session.add(row)
    row.preference_value = json.dumps(payload, separators=(",", ":"))
    row.updated_at = utc_now()


def reset_adaptive_trap(repo: Any, managed_account_id: int) -> None:
    _write_state(
        repo,
        int(managed_account_id),
        {"trap_score": 0, "post_virtual_recovery_losses": 0},
    )


def record_post_virtual_recovery_loss(
    repo: Any,
    managed_account_id: int,
    *,
    debt: float,
) -> dict[str, Any]:
    """Increase protection after the account reaches the 3-loss trap threshold."""

    state = _read_state(repo, int(managed_account_id))
    state["trap_score"] = min(
        MAX_TRAP_SCORE,
        int(state["trap_score"]) + 1,
    )
    state["post_virtual_recovery_losses"] = int(
        state["post_virtual_recovery_losses"]
    ) + 1
    _write_state(repo, int(managed_account_id), state)
    return state


def record_post_virtual_recovery_loss_in_session(
    session: Any,
    managed_account_id: int,
    *,
    debt: float,
) -> dict[str, Any]:
    state = _read_state_from_session(session, int(managed_account_id))
    state["trap_score"] = min(
        MAX_TRAP_SCORE,
        int(state["trap_score"]) + 1,
    )
    state["post_virtual_recovery_losses"] = int(
        state["post_virtual_recovery_losses"]
    ) + 1
    _write_state_to_session(session, int(managed_account_id), state)
    return state


def adaptive_trap_state(repo: Any, managed_account_id: int) -> dict[str, Any]:
    return _read_state(repo, int(managed_account_id))


def adaptive_virtual_wins_required(
    repo: Any,
    managed_account_id: int,
    *,
    default_wins: int = 1,
    recovery_debt: float = 0.0,
) -> int:
    """Return account-specific confirmation wins for virtual OVER-4.

    The normal path remains one virtual win. If an account keeps losing the real
    post-virtual recovery until 3 consecutive real losses are recorded, the guard
    marks an alternating trap and requires stronger virtual evidence before
    risking money again.
    """

    return adaptive_virtual_wins_required_for_state(
        _read_state(repo, int(managed_account_id)),
        default_wins=default_wins,
        recovery_debt=recovery_debt,
    )


def adaptive_virtual_wins_required_for_state(
    trap_state: dict[str, Any],
    *,
    default_wins: int = 1,
    recovery_debt: float = 0.0,
) -> int:
    score = int((trap_state or {}).get("trap_score") or 0)
    del recovery_debt
    base = max(1, int(default_wins or 1))
    if score >= 3:
        return max(base, 3)
    if score >= 1:
        return max(base, 2)
    return base
