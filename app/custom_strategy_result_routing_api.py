from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException, Request
from pydantic import ValidationError

import app.api as base_api
from app.custom_strategy_result_routing import (
    AFTER_LOSS,
    describe_result_routing,
    normalize_result_routing,
    read_result_routing,
    write_result_routing,
)
from app.custom_strategy_runtime_api import DirectCustomStrategyRequest
from app.final_public_controls import _current_account_payload


_INSTALLED = False


def _route_endpoint(app: Any, path: str, method: str) -> Callable[..., Any] | None:
    expected = method.upper()
    for route in reversed(list(app.router.routes)):
        if (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        ):
            endpoint = getattr(route, "endpoint", None)
            if callable(endpoint):
                return endpoint
    return None


def _remove_route(app: Any, path: str, method: str) -> None:
    expected = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and expected in set(getattr(route, "methods", set()) or set())
        )
    ]


def _attach_routing(payload: dict[str, Any], routing: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload or {})
    result["result_routing"] = routing
    config = result.get("config")
    if isinstance(config, dict):
        # Include route configuration inside the strategy config as well. The
        # browser's existing server-strategy hash therefore notices a route-only
        # edit made on another device without changing the canonical base strategy.
        result["config"] = {**config, "result_routing": routing}
    supported = result.get("supported")
    if isinstance(supported, dict):
        result["supported"] = {
            **supported,
            "result_routing": {
                "enabled": True,
                "after_win": "primary",
                "after_loss": "custom_contract_and_conditions",
                "loss_route_contracts": [
                    "over",
                    "under",
                    "matches",
                    "differs",
                    "odd",
                    "even",
                    "rise",
                    "fall",
                ],
            },
        }
    preview = str(result.get("preview") or "").strip()
    route_preview = describe_result_routing(routing)
    result["result_routing_preview"] = route_preview
    if preview:
        result["preview"] = f"{preview}. {route_preview}"
    return result


def install_custom_strategy_result_routing_api(app: Any) -> None:
    """Extend the final Custom Strategy API without changing existing clients.

    Existing saves that omit ``result_routing`` preserve the account's current
    route preference. New builder clients explicitly send enabled=false when the
    feature is off. This keeps every existing trader on the exact old single-route
    behavior until they intentionally enable Result-Based Trading.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    original_get = _route_endpoint(app, "/me/custom-strategy", "GET")
    original_post = _route_endpoint(app, "/me/custom-strategy", "POST")
    if original_get is None or original_post is None:
        raise RuntimeError("Final Custom Strategy routes must be installed before result routing")

    _remove_route(app, "/me/custom-strategy", "GET")
    _remove_route(app, "/me/custom-strategy", "POST")

    @app.get("/me/custom-strategy")
    def custom_strategy_with_result_routing(request: Request) -> dict[str, Any]:
        account = _current_account_payload(request)
        payload = original_get(request)
        routing = read_result_routing(base_api.DATABASE, int(account["id"]))
        return _attach_routing(dict(payload or {}), routing)

    @app.post("/me/custom-strategy")
    def save_custom_strategy_with_result_routing(
        request: Request,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        account = _current_account_payload(request)
        managed_id = int(account["id"])
        source = dict(body or {})
        has_routing = "result_routing" in source
        raw_routing = source.pop("result_routing", None)
        previous_routing = read_result_routing(base_api.DATABASE, managed_id)

        if has_routing:
            try:
                routing = normalize_result_routing(
                    raw_routing,
                    fallback_after_loss=previous_routing.get(AFTER_LOSS),
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            # Backwards compatibility: older browsers do not know this field and
            # must not silently disable a route configured on another device.
            routing = previous_routing

        try:
            parsed = DirectCustomStrategyRequest.model_validate(source)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

        payload = dict(original_post(request, parsed) or {})
        if has_routing:
            with base_api.DATABASE.session() as session:
                routing = write_result_routing(session, managed_id, routing)
            try:
                base_api.REPOSITORY.audit(
                    "PERSONAL_CUSTOM_RESULT_ROUTING_CHANGED",
                    "personal_dashboard",
                    request.client.host if request.client else "unknown",
                    {
                        "managed_account_id": managed_id,
                        "enabled": bool(routing.get("enabled")),
                        "after_loss_trade_type": str(
                            (routing.get(AFTER_LOSS) or {}).get("trade_type") or ""
                        ),
                    },
                )
            except Exception:
                base_api.LOGGER.exception(
                    "CUSTOM_RESULT_ROUTING_AUDIT_FAILED managed_id=%s",
                    managed_id,
                )
        return _attach_routing(payload, routing)

    app.state.custom_strategy_result_routing_installed = True
    _INSTALLED = True
