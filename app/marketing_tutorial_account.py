from __future__ import annotations

"""Shared-DOT demo partitions used by the marketing/tutorial workspace.

There is exactly one financial account underneath this feature: DOT93427967, a
Deriv demo account. The UI exposes two independent demo balance partitions over
that same account:

* DOT93427967 — 75% partition, displayed as the normal Demo account.
* ROT92069206 — 25% partition, displayed with Real-style/US-flag presentation.

ROT is presentation only; it is never a real-money credential. Both partitions
buy through DOT93427967. OPEN receipts remember which visible partition opened a
contract, SETTLED receipts apply the full contract profit/loss only to that
partition, and resetting the underlying Deriv demo account rebases both balances
to 75% / 25% of the reset balance.
"""

import inspect
import json
from typing import Any, Callable

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

import app.api as base_api
import app.browser_direct_deriv_transport_v3 as browser_direct
import app.final_linked_accounts_6f2 as linked_accounts
import app.vps_demo_balance_reset as demo_reset
import app.vps_direct_execution_api as direct_api
from app.models import RuntimePreference, utc_now
from app.route_utils import remove_route
from app.token_store import decrypt_auth_payload


_INSTALLED = False
MARKETING_DOT_ACCOUNT_ID = "DOT93427967"
MARKETING_ROT_ACCOUNT_ID = "ROT92069206"
MARKETING_ROT_RATIO = 0.25
MARKETING_DOT_RATIO = 0.75
MARKETING_VIEW_COOKIE = "derivadmin_marketing_view_v1"
_PARTITION_PREFIX = "marketing_demo_partition:v1:"
_PARTITION_VERSION = 1
_MAX_PARTITION_CONTRACTS = 120


def _account_id(payload: dict[str, Any]) -> str:
    return str(payload.get("account_id") or "").strip().upper()


def _safe_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _money(value: Any) -> float:
    number = _safe_number(value)
    return round(number if number is not None else 0.0, 2)


def _split_balance(total: Any) -> tuple[float, float]:
    provider = max(0.0, _money(total))
    rot = round(provider * MARKETING_ROT_RATIO, 2)
    dot = round(provider - rot, 2)
    return dot, rot


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
    """Resolve only the special DOT/ROT marketing pair.

    The ROT row supplies its display login number only. Its credential is never
    selected for trading; all financial requests remain bound to the DOT row.
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
    """The financial session NEVER switches to ROT; ROT is a demo partition view."""

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


def _partition_key(dot_row: Any) -> str:
    return f"{_PARTITION_PREFIX}{int(dot_row.id)}"


def _new_partition_state(dot_row: Any, total: Any | None = None, currency: str | None = None) -> dict[str, Any]:
    summary = _dot_summary(dot_row) if total is None else {}
    provider = _money(summary.get("balance") if total is None else total)
    dot_balance, rot_balance = _split_balance(provider)
    return {
        "version": _PARTITION_VERSION,
        "provider_balance": provider,
        "dot_balance": dot_balance,
        "rot_balance": rot_balance,
        "currency": str(currency or summary.get("currency") or "USD").upper(),
        "contracts": {},
        "updated_at": utc_now().isoformat(),
    }


def _decode_partition(value: Any) -> dict[str, Any] | None:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("version") or 0) != _PARTITION_VERSION:
        return None
    dot = _safe_number(payload.get("dot_balance"))
    rot = _safe_number(payload.get("rot_balance"))
    provider = _safe_number(payload.get("provider_balance"))
    if dot is None or rot is None or provider is None:
        return None
    contracts = payload.get("contracts")
    payload["dot_balance"] = round(max(0.0, dot), 2)
    payload["rot_balance"] = round(max(0.0, rot), 2)
    payload["provider_balance"] = round(max(0.0, provider), 2)
    payload["currency"] = str(payload.get("currency") or "USD").upper()
    payload["contracts"] = contracts if isinstance(contracts, dict) else {}
    return payload


def _write_partition(session: Any, dot_row: Any, state: dict[str, Any]) -> None:
    key = _partition_key(dot_row)
    state = dict(state)
    state["version"] = _PARTITION_VERSION
    state["provider_balance"] = round(
        max(0.0, _money(state.get("dot_balance")) + _money(state.get("rot_balance"))),
        2,
    )
    state["updated_at"] = utc_now().isoformat()
    value = json.dumps(state, separators=(",", ":"), sort_keys=True)
    row = session.get(RuntimePreference, key)
    if row is None:
        session.add(RuntimePreference(preference_key=key, preference_value=value))
    else:
        row.preference_value = value
        row.updated_at = utc_now()


def _partition_state(dot_row: Any) -> dict[str, Any]:
    with base_api.DATABASE.session() as session:
        row = session.get(RuntimePreference, _partition_key(dot_row))
        state = _decode_partition(row.preference_value) if row is not None else None
        if state is None:
            state = _new_partition_state(dot_row)
            _write_partition(session, dot_row, state)
        return dict(state)


def _reset_partition_state(dot_row: Any, total: Any, currency: str = "USD") -> dict[str, Any]:
    state = _new_partition_state(dot_row, total=total, currency=currency)
    with base_api.DATABASE.session() as session:
        _write_partition(session, dot_row, state)
    return state


def _trim_contracts(contracts: dict[str, Any]) -> dict[str, Any]:
    items = list(contracts.items())[-_MAX_PARTITION_CONTRACTS:]
    return {str(key): value for key, value in items}


def _apply_partition_receipt(
    dot_row: Any,
    *,
    view: str,
    event: str,
    contract_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Attribute one real demo contract to exactly one visible partition.

    OPEN freezes the partition owner. SETTLED applies the contract's full net
    profit/loss to that owner. Repeated/revised SETTLED receipts are idempotent by
    applying only the difference from the previously recorded profit.
    """

    event = str(event or "").strip().upper()
    contract_id = str(contract_id or "").strip()
    with base_api.DATABASE.session() as session:
        row = session.get(RuntimePreference, _partition_key(dot_row))
        state = _decode_partition(row.preference_value) if row is not None else None
        if state is None:
            state = _new_partition_state(dot_row)
        contracts = dict(state.get("contracts") or {})
        record = contracts.get(contract_id)
        if not isinstance(record, dict):
            record = {}
        owner = str(record.get("view") or view or "dot").lower()
        owner = "rot" if owner == "rot" else "dot"

        if event == "OPEN":
            record.setdefault("view", owner)
            record.setdefault("profit", None)
        elif event == "SETTLED":
            owner = str(record.get("view") or owner).lower()
            owner = "rot" if owner == "rot" else "dot"
            new_profit = _safe_number(payload.get("profit"))
            previous_profit = _safe_number(record.get("profit"))
            if new_profit is not None:
                delta = round(new_profit - (previous_profit if previous_profit is not None else 0.0), 8)
                balance_key = "rot_balance" if owner == "rot" else "dot_balance"
                state[balance_key] = round(max(0.0, _money(state.get(balance_key)) + delta), 2)
                record["profit"] = round(new_profit, 8)
            record["view"] = owner
            record["settled"] = True

        record["updated_at"] = utc_now().isoformat()
        contracts[contract_id] = record
        state["contracts"] = _trim_contracts(contracts)
        _write_partition(session, dot_row, state)
        return dict(state)


def _marketing_metadata(*, view: str) -> dict[str, Any]:
    share = MARKETING_ROT_RATIO if view == "rot" else MARKETING_DOT_RATIO
    return {
        "marketing_tutorial": True,
        "simulation_only": True,
        "demo_partition": True,
        "tutorial_mode": "shared_dot_demo_75_25_partitions",
        "tutorial_view": view,
        "demo_partition_share": share,
        "tutorial_balance_ratio": share,
        "tutorial_execution_account_id": MARKETING_DOT_ACCOUNT_ID,
        "tutorial_display_account_id": MARKETING_ROT_ACCOUNT_ID,
        "underlying_account_type": "demo",
        "real_money_execution": False,
    }


def _partition_fields(state: dict[str, Any], *, view: str) -> dict[str, Any]:
    key = "rot_balance" if view == "rot" else "dot_balance"
    return {
        "balance": _money(state.get(key)),
        "currency": str(state.get("currency") or "USD").upper(),
        "partition_provider_balance": _money(state.get("provider_balance")),
        "partition_dot_balance": _money(state.get("dot_balance")),
        "partition_rot_balance": _money(state.get("rot_balance")),
        **_marketing_metadata(view=view),
    }


def _project_dot_account(
    dot_row: Any,
    dot_payload: dict[str, Any],
    *,
    selected: bool,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    partition = state or _partition_state(dot_row)
    payload = linked_accounts._account_payload(dot_row, dot_payload, int(dot_row.id))
    payload.update(
        {
            "account_id": MARKETING_DOT_ACCOUNT_ID,
            "account_id_masked": base_api.mask_account_id(MARKETING_DOT_ACCOUNT_ID),
            "account_type": "demo",
            "account_type_label": "Demo",
            "account_prefix": "DOT",
            "label": f"Demo {base_api.mask_account_id(MARKETING_DOT_ACCOUNT_ID)}",
            "selected": bool(selected),
            "marketing_tutorial_source": True,
            "tutorial_rot_available": True,
            **_partition_fields(partition, view="dot"),
        }
    )
    return payload


def _project_rot_account(
    dot_row: Any,
    rot_row: Any,
    rot_payload: dict[str, Any],
    *,
    selected: bool,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    partition = state or _partition_state(dot_row)
    payload = linked_accounts._account_payload(rot_row, rot_payload, int(rot_row.id))
    payload.update(
        {
            "account_id": MARKETING_ROT_ACCOUNT_ID,
            "account_id_masked": base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID),
            # Real-style presentation is intentional; underlying_account_type is demo.
            "account_type": "real",
            "account_type_label": "Real",
            "account_prefix": "ROT",
            "label": f"Real {base_api.mask_account_id(MARKETING_ROT_ACCOUNT_ID)}",
            "selected": bool(selected),
            "has_trading_api_token": False,
            **_partition_fields(partition, view="rot"),
        }
    )
    return payload


def _project_me_payload(
    payload: dict[str, Any],
    dot_row: Any,
    rot_row: Any,
    *,
    view: str,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    partition = state or _partition_state(dot_row)
    projected = dict(payload)
    if view == "rot":
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
                **_partition_fields(partition, view="rot"),
            }
        )
        return projected

    projected.update(
        {
            "presentation_managed_account_id": int(dot_row.id),
            "account_id_full": MARKETING_DOT_ACCOUNT_ID,
            "login_id": MARKETING_DOT_ACCOUNT_ID,
            "display_account_id": MARKETING_DOT_ACCOUNT_ID,
            "account_type": "demo",
            "account_type_label": "Demo",
            "account_prefix": "DOT",
            **_partition_fields(partition, view="dot"),
        }
    )
    return projected


def install_marketing_tutorial_account(app: Any) -> None:
    """Install the shared-DOT 75%/25% demo partition authority."""

    global _INSTALLED
    if _INSTALLED:
        return

    previous_accounts = _capture_endpoint(app, "/me/accounts", "GET")
    previous_switch = _capture_endpoint(app, "/me/switch-account", "POST")
    previous_me = _capture_endpoint(app, "/me", "GET")
    previous_reset = _capture_endpoint(app, "/me/reset-demo-balance", "POST")
    previous_bootstrap = _capture_endpoint(app, "/me/direct-execution/bootstrap", "POST")
    previous_arm = _capture_endpoint(app, "/me/direct-execution/arm", "POST")
    previous_receipt = _capture_endpoint(app, "/me/direct-execution/receipt", "POST")

    for path, method in (
        ("/me/accounts", "GET"),
        ("/me/switch-account", "POST"),
        ("/me", "GET"),
        ("/me/reset-demo-balance", "POST"),
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
        partition = _partition_state(dot_row)
        return {
            "authenticated": True,
            "scope": "marketing_shared_demo_partitions",
            "selected_managed_account_id": int(rot_row.id if view == "rot" else dot_row.id),
            "execution_managed_account_id": int(dot_row.id),
            # Deliberately exactly two visible partitions. The underlying linked
            # Real account is not exposed as an independently funded account here.
            "accounts": [
                _project_dot_account(dot_row, dot_payload, selected=view == "dot", state=partition),
                _project_rot_account(dot_row, rot_row, rot_payload, selected=view == "rot", state=partition),
            ],
            "linked_accounts_loading": False,
            "partition_provider_balance": _money(partition.get("provider_balance")),
            "partition_dot_balance": _money(partition.get("dot_balance")),
            "partition_rot_balance": _money(partition.get("rot_balance")),
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
        partition = _partition_state(dot_row)
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
                **_partition_fields(partition, view=view),
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
        return _project_me_payload(
            payload,
            dot_row,
            rot_row,
            view=_view(request),
            state=_partition_state(dot_row),
        )

    @app.post("/me/reset-demo-balance")
    async def marketing_reset_demo_balance(
        request: Request,
        body: demo_reset.DemoBalanceResetRequest,
    ) -> Any:
        account = base_api.get_current_account(request)
        if not account:
            raise HTTPException(status_code=401, detail="Not authenticated")
        pair = _pair_from_current(account)
        if pair is None:
            return await _invoke(previous_reset, request, body)

        (dot_row, _dot_payload), _rot = pair
        _ensure_dot_session(request, account, dot_row)
        # A reset from either visible partition is always a reset of the one real
        # underlying account, DOT93427967. After Deriv confirms it, rebase 75/25.
        mapped = demo_reset.DemoBalanceResetRequest(managed_account_id=int(dot_row.id))
        result = await _invoke(previous_reset, request, mapped)
        if not isinstance(result, dict) or not result.get("success"):
            return result
        partition = _reset_partition_state(
            dot_row,
            total=result.get("balance", demo_reset.DEFAULT_DEMO_BALANCE),
            currency=str(result.get("currency") or "USD"),
        )
        response = dict(result)
        response.update(
            {
                "partition_reset": True,
                "partition_provider_balance": _money(partition.get("provider_balance")),
                "partition_dot_balance": _money(partition.get("dot_balance")),
                "partition_rot_balance": _money(partition.get("rot_balance")),
                "message": "Demo balance reset and re-split: 75% DOT / 25% ROT.",
            }
        )
        return response

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
        if pair is None:
            return await _invoke(previous_receipt, request, body)

        (dot_row, _dot_payload), _rot = pair
        visible_view = _view(request)
        _ensure_dot_session(request, account, dot_row)
        result = await _invoke(previous_receipt, request, body)
        # Persist the normal browser-direct receipt first. Partition accounting is
        # then a small, idempotent control-plane update; it never relays the BUY.
        _apply_partition_receipt(
            dot_row,
            view=visible_view,
            event=body.event,
            contract_id=body.contract_id,
            payload=body.payload,
        )
        return result

    app.state.marketing_tutorial_account_installed = True
    app.state.marketing_tutorial_execution_account = MARKETING_DOT_ACCOUNT_ID
    app.state.marketing_tutorial_display_account = MARKETING_ROT_ACCOUNT_ID
    app.state.marketing_tutorial_real_money_execution = False
    app.state.marketing_tutorial_demo_partition_shares = {
        "dot": MARKETING_DOT_RATIO,
        "rot": MARKETING_ROT_RATIO,
    }
    _INSTALLED = True
