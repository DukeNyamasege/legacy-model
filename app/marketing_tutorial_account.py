from __future__ import annotations

"""DOT-backed marketing/tutorial account projection.

For one explicitly configured marketing login the selector presents two views:

* DOT93427967 — the normal Deriv demo account with its full provider balance.
* ROT92069206 — a Real-styled tutorial presentation whose displayed balance is
  exactly 25% of the DOT demo balance.

ROT is presentation only. The durable ClientSession and every browser-direct
financial control path stay bound to DOT93427967. Provider OTP, WebSocket,
proposal, BUY and settlement therefore remain Deriv demo operations even while
the UI is showing the ROT tutorial view.
"""

import inspect
from typing import Any, Callable

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

import app.api as base_api
import app.browser_direct_deriv_transport_v3 as browser_direct
import app.final_linked_accounts_6f2 as linked_accounts
import app.vps_direct_execution_api as direct_api
from app.route_utils import remove_route
from app.token_store import decrypt_auth_payload


_INSTALLED = False
MARKETING_DOT_ACCOUNT_ID = "DOT93427967"
MARKETING_ROT_ACCOUNT_ID = "ROT92069206"
MARKETING_ROT_RATIO = 0.25
MARKETING_VIEW_COOKIE = "derivadmin_marketing_view_v1"


def _account_id(payload: dict[str, Any]) -> str:
    return str(payload.get("account_id") or "").strip().upper()


def _capture_endpoint(app: Any, path: str, method: str) -> Callable[..., Any]:
    expected = str(method).upper()
    matches = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == path
        and expected in set(getattr(route, "methods", set()) or set())
        and callable(getattr(route, "endpoint", None))
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Marketing tutorial authority expected one {expected} {path} route, found {len(matches)}"
        )
    return matches[0].endpoint


async def _invoke(endpoint: Callable[..., Any], *args: Any) -> Any:
    result = endpoint(*args)
    if inspect.isawaitable(result):
        return await result
    return result


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


def _pair_from_current(
    account: dict[str, Any],
) -> tuple[tuple[Any, dict[str, Any]], tuple[Any, dict[str, Any]]] | None:
    """Resolve the special pair only for its two account IDs.

    Ordinary users return before linked-account enumeration, preserving the O(1)
    dashboard/account-switch path installed by the VPS latency authority.
    """

    payload = _current_payload(account)
    if not payload:
        return None
    current_id = _account_id(payload)
    if current_id not in {MARKETING_DOT_ACCOUNT_ID, MARKETING_ROT_ACCOUNT_ID}:
        return None
    rows = linked_accounts._linked_rows(payload)
    dot = next((item for item in rows if _account_id(item[1]) == MARKETING_DOT_ACCOUNT_ID), None)
    rot = next((item for item in rows if _account_id(item[1]) == MARKETING_ROT_ACCOUNT_ID), None)
    if dot is None or rot is None:
        return None
    return dot, rot


def _view(request: Request) -> str:
    return "rot" if str(request.cookies.get(MARKETING_VIEW_COOKIE) or "").lower() == "rot" else "dot"


def _ensure_dot_session(request: Request, account: dict[str, Any], dot_row: Any) -> None:
    """Keep the financial session on DOT even when the visible view is ROT."""

    if int(account.get("id") or 0) == int(dot_row.id):
        return
    session_token = str(request.cookies.get(base_api.CLIENT_SESSION_COOKIE) or "")
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    base_api.REPOSITORY.set_client_session_account(
        base_api.session_hash(session_token),
        int(dot_row.id),
    )


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


def _project_dot_account(
    dot_row: Any,
    dot_payload: dict[str, Any],
    *,
    selected: bool,
) -> dict[str, Any]:
    payload = linked_accounts._account_payload(dot_row, dot_payload, int(dot_row.id))
    payload.update(
        {
            "account_id": MARKETING_DOT_ACCOUNT_ID,
            "account_id_masked": base_api.mask_account_id(MARKETING_DOT_ACCOUNT_ID),
            "account_type": "demo",
            "label": f"Demo {base_api.mask_account_id(MARKETING_DOT_ACCOUNT_ID)}",
            "selected": bool(selected),
            "marketing_tutorial_source": True,
            "tutorial_rot_available": True,
        }
    )
    return payload


def _project_rot_account(
    dot_row: Any,
    rot_row: Any,
    rot_payload: dict[str, Any],
    *,
    selected: bool,
) -> dict[str, Any]:
    payload = linked_accounts._account_payload(rot_row, rot_payload, int(rot_row.id))
    summary = _dot_summary(dot_row)
    payload.update(
        {
            "account_id": MARKETING_ROT_ACCOUNT_ID,
            "account_id_masked": base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID),
            "account_type": "real",
            "label": f"Real {base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID)}",
            "balance": round(float(summary.get("balance") or 0.0) * MARKETING_ROT_RATIO, 2),
            "currency": str(summary.get("currency") or "USD").upper(),
            "selected": bool(selected),
            # This row must never advertise itself as a financial credential.
            "has_trading_api_token": False,
            **_marketing_metadata(view="rot"),
        }
    )
    return payload


def _project_me_payload(
    payload: dict[str, Any],
    dot_row: Any,
    rot_row: Any,
    *,
    view: str,
) -> dict[str, Any]:
    if view != "rot":
        return payload
    projected = dict(payload)
    projected.update(
        {
            # managed_account_id intentionally stays the DOT execution row.
            "presentation_managed_account_id": int(rot_row.id),
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


def install_marketing_tutorial_account(app: Any) -> None:
    """Install the full-DOT + quarter-DOT-as-ROT marketing projection."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Capture the already-final production authorities so every ordinary account
    # delegates to them unchanged. Marketing is a narrow wrapper, not a replacement
    # for the fast linked-account or browser-direct architecture.
    previous_accounts = _capture_endpoint(app, "/me/accounts", "GET")
    previous_switch = _capture_endpoint(app, "/me/switch-account", "POST")
    previous_me = _capture_endpoint(app, "/me", "GET")
    previous_bootstrap = _capture_endpoint(app, "/me/direct-execution/bootstrap", "POST")
    previous_arm = _capture_endpoint(app, "/me/direct-execution/arm", "POST")
    previous_receipt = _capture_endpoint(app, "/me/direct-execution/receipt", "POST")

    for path, method in (
        ("/me/accounts", "GET"),
        ("/me/switch-account", "POST"),
        ("/me", "GET"),
        ("/me/direct-execution/bootstrap", "POST"),
        ("/me/direct-execution/arm", "POST"),
        ("/me/direct-execution/receipt", "POST"),
    ):
        remove_route(app, path, method)

    @app.get("/me/accounts")
    async def marketing_accounts(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> Any:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        pair = _pair_from_current(account)
        if pair is None:
            return await _invoke(previous_accounts, request, background_tasks)

        (dot_row, dot_payload), (rot_row, rot_payload) = pair
        _ensure_dot_session(request, account, dot_row)
        view = _view(request)
        return {
            "authenticated": True,
            "scope": "marketing_tutorial_pair",
            "selected_managed_account_id": int(rot_row.id if view == "rot" else dot_row.id),
            "execution_managed_account_id": int(dot_row.id),
            # Deliberately exactly two visible accounts. No underlying/extra real
            # account is exposed as a third selector row in this workspace.
            "accounts": [
                _project_dot_account(dot_row, dot_payload, selected=view == "dot"),
                _project_rot_account(dot_row, rot_row, rot_payload, selected=view == "rot"),
            ],
            "linked_accounts_loading": False,
            **_marketing_metadata(view=view),
        }

    @app.post("/me/switch-account")
    async def marketing_switch_account(
        request: Request,
        body: linked_accounts.LinkedAccountSwitchRequest,
    ) -> Any:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        pair = _pair_from_current(account)
        if pair is None:
            return await _invoke(previous_switch, request, body)

        (dot_row, _dot_payload), (rot_row, _rot_payload) = pair
        requested_id = int(body.managed_account_id or 0)
        if body.managed_account_id is None and body.account_type is not None:
            requested_id = int(
                rot_row.id
                if base_api.normalize_account_type(body.account_type) == "real"
                else dot_row.id
            )
        if requested_id not in {int(dot_row.id), int(rot_row.id)}:
            raise HTTPException(
                status_code=404,
                detail="That account is not available in this tutorial workspace",
            )

        _ensure_dot_session(request, account, dot_row)
        view = "rot" if requested_id == int(rot_row.id) else "dot"
        response = JSONResponse(
            {
                "success": True,
                "managed_account_id": int(rot_row.id if view == "rot" else dot_row.id),
                "execution_managed_account_id": int(dot_row.id),
                "account_id": MARKETING_ROT_ACCOUNT_ID if view == "rot" else MARKETING_DOT_ACCOUNT_ID,
                "account_id_masked": base_api.mask_account_id(
                    MARKETING_ROT_ACCOUNT_ID if view == "rot" else MARKETING_DOT_ACCOUNT_ID
                ),
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
    async def marketing_me(request: Request) -> Any:
        account = base_api.get_current_account(request)
        if not account:
            return await _invoke(previous_me, request)
        pair = _pair_from_current(account)
        if pair is None:
            return await _invoke(previous_me, request)
        (dot_row, _dot_payload), (rot_row, _rot_payload) = pair
        _ensure_dot_session(request, account, dot_row)
        payload = await _invoke(previous_me, request)
        if not isinstance(payload, dict):
            return payload
        return _project_me_payload(payload, dot_row, rot_row, view=_view(request))

    @app.post("/me/direct-execution/bootstrap")
    async def marketing_browser_bootstrap(
        request: Request,
        body: browser_direct.DirectBootstrapRequest,
    ) -> Any:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        pair = _pair_from_current(account)
        if pair is not None:
            (dot_row, _dot_payload), _rot = pair
            _ensure_dot_session(request, account, dot_row)
        response = await _invoke(previous_bootstrap, request, body)
        # Do not rewrite the bootstrap payload to ROT. It must truthfully carry
        # DOT93427967 so the browser requests a Deriv DEMO OTP/WebSocket.
        return response

    @app.post("/me/direct-execution/arm")
    async def marketing_arm(
        request: Request,
        body: direct_api.DirectArmRequest,
    ) -> Any:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        pair = _pair_from_current(account)
        if pair is not None:
            (dot_row, _dot_payload), _rot = pair
            _ensure_dot_session(request, account, dot_row)
        return await _invoke(previous_arm, request, body)

    @app.post("/me/direct-execution/receipt")
    async def marketing_receipt(
        request: Request,
        body: browser_direct.DirectTradeReceipt,
    ) -> Any:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        pair = _pair_from_current(account)
        if pair is not None:
            (dot_row, _dot_payload), _rot = pair
            _ensure_dot_session(request, account, dot_row)
        return await _invoke(previous_receipt, request, body)

    app.state.marketing_tutorial_account_installed = True
    app.state.marketing_tutorial_execution_account = MARKETING_DOT_ACCOUNT_ID
    app.state.marketing_tutorial_display_account = MARKETING_ROT_ACCOUNT_ID
    app.state.marketing_tutorial_real_money_execution = False
    _INSTALLED = True
