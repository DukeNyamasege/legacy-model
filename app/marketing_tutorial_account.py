from __future__ import annotations

"""DOT-backed marketing/tutorial account projection.

The selector may present ROT92069206 as a Real-style account, but the durable
ClientSession and every provider private execution request remain bound to the
DOT93427967 demo account. This keeps tutorial contracts genuinely on Deriv demo
while making a real-money ROT BUY impossible from the marketing view.
"""

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

import app.api as base_api
import app.api_performance_hardening as performance
import app.final_linked_accounts_6f2 as linked_accounts
import app.vps_dashboard_latency_hotfix as dashboard_latency
from app.route_utils import remove_route
from app.token_store import decrypt_auth_payload


_INSTALLED = False
MARKETING_DOT_ACCOUNT_ID = "DOT93427967"
MARKETING_ROT_ACCOUNT_ID = "ROT92069206"
MARKETING_ROT_RATIO = 0.25
MARKETING_VIEW_COOKIE = "derivadmin_marketing_view_v1"


def _account_id(payload: dict[str, Any]) -> str:
    return str(payload.get("account_id") or "").strip().upper()


def _current_payload(account: dict[str, Any]) -> dict[str, Any] | None:
    row = base_api.REPOSITORY.managed_account(int(account["id"]))
    if not row:
        return None
    try:
        return decrypt_auth_payload(
            row["token_secret"],
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception:
        return None


def _pair_from_current(account: dict[str, Any]) -> tuple[tuple[Any, dict[str, Any]], tuple[Any, dict[str, Any]]] | None:
    payload = _current_payload(account)
    if not payload:
        return None
    rows = linked_accounts._linked_rows(payload)
    dot = next((item for item in rows if _account_id(item[1]) == MARKETING_DOT_ACCOUNT_ID), None)
    rot = next((item for item in rows if _account_id(item[1]) == MARKETING_ROT_ACCOUNT_ID), None)
    if dot is None or rot is None:
        return None
    return dot, rot


def _view(request: Request) -> str:
    return "rot" if str(request.cookies.get(MARKETING_VIEW_COOKIE) or "").lower() == "rot" else "dot"


def _dot_summary(dot_row: Any) -> dict[str, Any]:
    return base_api.REPOSITORY.account_summary(
        MARKETING_DOT_ACCOUNT_ID,
        managed_account_id=int(dot_row.id),
    )


def _rot_balance(dot_row: Any) -> float:
    return round(float(_dot_summary(dot_row).get("balance") or 0.0) * MARKETING_ROT_RATIO, 2)


def _marketing_metadata(*, view: str) -> dict[str, Any]:
    return {
        "marketing_tutorial": True,
        "simulation_only": True,
        "tutorial_mode": "rot_presentation_over_dot_demo",
        "tutorial_view": view,
        "tutorial_balance_ratio": MARKETING_ROT_RATIO,
        "tutorial_execution_account_id": MARKETING_DOT_ACCOUNT_ID,
        "tutorial_display_account_id": MARKETING_ROT_ACCOUNT_ID,
        "real_money_execution": False,
    }


def _project_rot_account(dot_row: Any, rot_row: Any, rot_payload: dict[str, Any], *, selected: bool) -> dict[str, Any]:
    payload = linked_accounts._account_payload(rot_row, rot_payload, int(rot_row.id))
    summary = _dot_summary(dot_row)
    payload.update(
        {
            "account_id_masked": base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID),
            "account_type": "real",
            "label": f"Real {base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID)}",
            "balance": round(float(summary.get("balance") or 0.0) * MARKETING_ROT_RATIO, 2),
            "currency": str(summary.get("currency") or "USD").upper(),
            "selected": bool(selected),
            "has_trading_api_token": False,
            **_marketing_metadata(view="rot"),
        }
    )
    return payload


def _project_dot_account(dot_row: Any, dot_payload: dict[str, Any], *, selected: bool) -> dict[str, Any]:
    payload = linked_accounts._account_payload(dot_row, dot_payload, int(dot_row.id))
    payload.update(
        {
            "account_id_masked": base_api.mask_account_id(MARKETING_DOT_ACCOUNT_ID),
            "account_type": "demo",
            "label": f"Demo {base_api.mask_account_id(MARKETING_DOT_ACCOUNT_ID)}",
            "selected": bool(selected),
            "marketing_tutorial_source": True,
            "tutorial_rot_available": True,
        }
    )
    return payload


def _project_me_payload(payload: dict[str, Any], dot_row: Any, *, view: str) -> dict[str, Any]:
    if view != "rot":
        return payload
    projected = dict(payload)
    projected.update(
        {
            "account_id": base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID),
            "account_id_masked": base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID),
            "account_id_full": MARKETING_ROT_ACCOUNT_ID,
            "login_id": MARKETING_ROT_ACCOUNT_ID,
            "display_account_id": MARKETING_ROT_ACCOUNT_ID,
            "account_type": "real",
            "account_type_label": "Real",
            "account_prefix": "ROT",
            "label": f"Real {base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID)}",
            "balance": _rot_balance(dot_row),
            **_marketing_metadata(view="rot"),
        }
    )
    return projected


def _ordinary_accounts(account: dict[str, Any]) -> dict[str, Any]:
    row = base_api.REPOSITORY.managed_account(int(account["id"]))
    if not row:
        raise HTTPException(status_code=404, detail="Managed account was not found")
    try:
        current_payload = decrypt_auth_payload(
            row["token_secret"],
            base_api.CONFIG.deriv.token_encryption_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Current account credential is unreadable") from exc
    rows = linked_accounts._linked_rows(current_payload)
    return {
        "authenticated": True,
        "scope": "linked_options_accounts",
        "selected_managed_account_id": int(account["id"]),
        "accounts": [
            linked_accounts._account_payload(linked_row, payload, int(account["id"]))
            for linked_row, payload in rows
        ],
    }


def install_marketing_tutorial_account(app: Any) -> None:
    """Install the full-DOT + quarter-DOT-as-ROT marketing projection."""

    global _INSTALLED
    if _INSTALLED:
        return

    remove_route(app, "/me/accounts", "GET")
    remove_route(app, "/me/switch-account", "POST")
    remove_route(app, "/me", "GET")

    @app.get("/me/accounts")
    def marketing_accounts(request: Request) -> dict[str, Any]:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        pair = _pair_from_current(account)
        if pair is None:
            return _ordinary_accounts(account)

        (dot_row, dot_payload), (rot_row, rot_payload) = pair
        view = _view(request)
        return {
            "authenticated": True,
            "scope": "marketing_tutorial_pair",
            "selected_managed_account_id": int(rot_row.id if view == "rot" else dot_row.id),
            "execution_managed_account_id": int(dot_row.id),
            "accounts": [
                _project_dot_account(dot_row, dot_payload, selected=view == "dot"),
                _project_rot_account(dot_row, rot_row, rot_payload, selected=view == "rot"),
            ],
            **_marketing_metadata(view=view),
        }

    @app.post("/me/switch-account")
    def marketing_switch_account(request: Request, body: linked_accounts.LinkedAccountSwitchRequest) -> Any:
        session_token = request.cookies.get(base_api.CLIENT_SESSION_COOKIE)
        account = base_api.get_current_account(request)
        if not account or not session_token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        pair = _pair_from_current(account)
        if pair is None:
            current_row = base_api.REPOSITORY.managed_account(int(account["id"]))
            if not current_row:
                raise HTTPException(status_code=404, detail="Managed account was not found")
            current_payload = linked_accounts._managed_payload(current_row)
            linked = linked_accounts._linked_rows(current_payload)
            target = None
            requested_type = base_api.normalize_account_type(body.account_type) if body.account_type else None
            if body.managed_account_id is not None:
                target = next((item for item in linked if int(item[0].id) == int(body.managed_account_id)), None)
                if target is not None and requested_type is not None and base_api.account_type_from_payload(target[1]) != requested_type:
                    raise HTTPException(status_code=422, detail="Selected account type does not match the account")
            elif requested_type is not None:
                target = next((item for item in linked if base_api.account_type_from_payload(item[1]) == requested_type), None)
            if target is None:
                raise HTTPException(status_code=404, detail="That account is not linked to this Deriv login")
            target_row, target_payload = target
            base_api.REPOSITORY.set_client_session_account(base_api.session_hash(session_token), int(target_row.id))
            return {
                "success": True,
                "managed_account_id": int(target_row.id),
                "account_id_masked": base_api.mask_account_id(_account_id(target_payload)),
                "account_type": base_api.account_type_from_payload(target_payload),
            }

        (dot_row, _dot_payload), (rot_row, _rot_payload) = pair
        requested_id = int(body.managed_account_id or 0)
        if body.managed_account_id is None and body.account_type is not None:
            requested_id = int(rot_row.id if base_api.normalize_account_type(body.account_type) == "real" else dot_row.id)
        if requested_id not in {int(dot_row.id), int(rot_row.id)}:
            raise HTTPException(status_code=404, detail="That account is not available in this tutorial workspace")

        # The financial session NEVER switches to ROT. ROT is presentation only.
        base_api.REPOSITORY.set_client_session_account(
            base_api.session_hash(session_token),
            int(dot_row.id),
        )
        view = "rot" if requested_id == int(rot_row.id) else "dot"
        response = JSONResponse(
            {
                "success": True,
                "managed_account_id": int(rot_row.id if view == "rot" else dot_row.id),
                "execution_managed_account_id": int(dot_row.id),
                "account_id_masked": base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID if view == "rot" else MARKETING_DOT_ACCOUNT_ID),
                "account_type": "real" if view == "rot" else "demo",
                **_marketing_metadata(view=view),
            }
        )
        response.set_cookie(
            key=MARKETING_VIEW_COOKIE,
            value=view,
            httponly=False,
            secure=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
        return response

    @app.get("/me")
    def marketing_me(request: Request) -> dict[str, Any]:
        account = dashboard_latency._selected_session_account(request)
        if not account:
            return {
                "authenticated": False,
                "performance_profile": "vps-constant-time-session-v1",
            }
        payload = performance._cached_me(account)
        payload["performance_profile"] = "vps-constant-time-session-v1"
        pair = _pair_from_current(account)
        if pair is None:
            return payload
        (dot_row, _dot_payload), _rot = pair
        return _project_me_payload(payload, dot_row, view=_view(request))

    app.state.marketing_tutorial_account_installed = True
    app.state.marketing_tutorial_execution_account = MARKETING_DOT_ACCOUNT_ID
    app.state.marketing_tutorial_display_account = MARKETING_ROT_ACCOUNT_ID
    app.state.marketing_tutorial_real_money_execution = False
    _INSTALLED = True
