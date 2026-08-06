from __future__ import annotations

import copy
import logging
from typing import Any

import app.seamless_execution_runtime as seamless


_INSTALLED = False
_VERSION = "bulk-member-reconciliation-v2"
_SUCCESS_KEYS = (
    "transactions",
    "results",
    "contracts",
    "purchases",
    "successes",
    "members",
    "items",
)
_FAILURE_KEYS = ("errors", "failures")


def _account_member(account_id: Any, value: Any, *, failure: bool) -> dict[str, Any] | None:
    account = str(account_id or "").strip()
    if isinstance(value, dict):
        member = dict(value)
    elif failure:
        member = {"message": str(value or "Deriv rejected this account")}
    else:
        return None
    if account:
        member.setdefault("account_id", account)
    return member


def _mapping_to_members(value: dict[str, Any], *, failure: bool) -> list[dict[str, Any]]:
    """Turn an account-keyed provider object into ordinary member records."""

    members: list[dict[str, Any]] = []
    for account_id, raw in value.items():
        member = _account_member(account_id, raw, failure=failure)
        if member is not None:
            members.append(member)
    return members


def _canonicalize_container(container: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    output = dict(container)
    converted: list[str] = []
    for key in _SUCCESS_KEYS:
        value = output.get(key)
        if isinstance(value, dict):
            output[key] = _mapping_to_members(value, failure=False)
            converted.append(f"{key}:mapping")
        elif isinstance(value, tuple):
            output[key] = list(value)
            converted.append(f"{key}:tuple")
    for key in _FAILURE_KEYS:
        value = output.get(key)
        if isinstance(value, dict):
            output[key] = _mapping_to_members(value, failure=True)
            converted.append(f"{key}:mapping")
        elif isinstance(value, tuple):
            output[key] = list(value)
            converted.append(f"{key}:tuple")
    return output, converted


def _canonicalize_response(response: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Canonicalize only structural containers; never inspect or log credentials."""

    normalized = copy.deepcopy(response)
    normalized, shapes = _canonicalize_container(normalized)
    data = normalized.get("data")
    if isinstance(data, dict):
        canonical_data, data_shapes = _canonicalize_container(data)
        normalized["data"] = canonical_data
        shapes.extend(f"data.{value}" for value in data_shapes)
    return normalized, shapes


def _requested_accounts(request_body: dict[str, Any] | None) -> list[str]:
    return [
        str(item.get("account_id") or "").strip()
        for item in list((request_body or {}).get("accounts") or [])
        if isinstance(item, dict) and str(item.get("account_id") or "").strip()
    ]


def _accounted_accounts(response: dict[str, Any]) -> set[str]:
    accounted: set[str] = set()
    data = response.get("data")
    transactions = data.get("transactions") if isinstance(data, dict) else []
    for item in list(transactions or []):
        if isinstance(item, dict):
            account = seamless._account_id(item)
            if account:
                accounted.add(account)
    for item in list(response.get("errors") or []):
        if isinstance(item, dict):
            account = seamless._account_id(item)
            if account:
                accounted.add(account)
    return accounted


def _reconciled_normalize_bulk_response(
    response: dict[str, Any],
    request_body: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(response, dict):
        return response
    canonical, shapes = _canonicalize_response(response)
    normalized = _ORIGINAL_NORMALIZER(canonical, request_body)
    if not isinstance(normalized, dict) or isinstance(normalized.get("error"), dict):
        return normalized

    requested = _requested_accounts(request_body)
    accounted = _accounted_accounts(normalized)
    missing = [account for account in requested if account not in accounted]
    logging.getLogger(__name__).warning(
        "BULK_RESPONSE_RECONCILED requested=%s accounted=%s missing=%s "
        "converted_shapes=%s credentials_logged=false duplicate_retry=false",
        len(requested),
        len(accounted),
        len(missing),
        shapes or ["none"],
    )
    return normalized


_ORIGINAL_NORMALIZER = seamless._normalize_bulk_response


def install_bulk_response_member_reconciliation() -> None:
    """Install after the seamless REST wrapper captures its request function."""

    global _INSTALLED, _ORIGINAL_NORMALIZER
    current = seamless._normalize_bulk_response
    if getattr(current, "_bulk_member_reconciliation", False):
        return
    _ORIGINAL_NORMALIZER = current
    _reconciled_normalize_bulk_response._bulk_member_reconciliation = True  # type: ignore[attr-defined]
    seamless._normalize_bulk_response = _reconciled_normalize_bulk_response
    if not _INSTALLED:
        logging.getLogger(__name__).warning(
            "BULK_RESPONSE_MEMBER_RECONCILIATION_INSTALLED version=%s "
            "account_keyed_successes=true account_keyed_errors=true "
            "blind_retry=false credentials_logged=false",
            _VERSION,
        )
    _INSTALLED = True
