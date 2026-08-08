from __future__ import annotations

import copy
import logging
from typing import Any

import app.seamless_execution_runtime as seamless


_INSTALLED = False
_VERSION = "bulk-member-reconciliation-v4"
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
_CANONICAL_SUCCESS_KEYS = (
    "transactions",
    "results",
    "contracts",
    "purchases",
    "items",
)
_SUCCESS_ALIAS_KEYS = ("successes", "members")


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


def _promote_success_aliases(output: dict[str, Any]) -> list[str]:
    """Promote provider success aliases to a container the core normalizer reads.

    Deriv bulk responses have appeared with success members under ``successes`` or
    ``members``. The seamless normalizer intentionally accepts only the canonical
    transaction/result/contract/purchase/item containers. If no canonical success
    container is populated, copy the alias members to ``transactions`` so a real
    provider purchase cannot be reported as BULK_MEMBER_MISSING merely because of
    response shape. Existing canonical data always wins, and this function never
    manufactures a contract or retries a financial request.
    """

    if any(isinstance(output.get(key), list) and output.get(key) for key in _CANONICAL_SUCCESS_KEYS):
        return []

    promoted: list[dict[str, Any]] = []
    markers: list[str] = []
    for key in _SUCCESS_ALIAS_KEYS:
        value = output.get(key)
        if not isinstance(value, list) or not value:
            continue
        promoted.extend(dict(item) for item in value if isinstance(item, dict))
        markers.append(f"{key}:promoted_to_transactions")

    if promoted:
        output["transactions"] = promoted
    return markers


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
    converted.extend(_promote_success_aliases(output))
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


def _transaction_members(
    response: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, str]:
    """Return the authoritative complete transaction member array when available."""

    data = response.get("data")
    if isinstance(data, dict) and isinstance(data.get("transactions"), list):
        return data["transactions"], "data.transactions"
    if isinstance(response.get("transactions"), list):
        return response["transactions"], "transactions"
    return None, ""


def _positionally_correlate_transaction_members(
    response: dict[str, Any],
    request_body: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Attach account identity to a complete Deriv transaction array safely.

    The current Bulk Purchase contract reports one transaction member per request
    account; a member can be either a successful purchase or that account's error.
    Some provider shapes omit ``account_id`` from those members. Correlate by the
    original request position only when the transaction cardinality exactly equals
    the request cardinality and every explicit identity (if present) already agrees
    with its request position. Any conflict or partial response remains untouched.

    This function never creates a contract, changes an error, or retries a purchase.
    """

    requested = _requested_accounts(request_body)
    if not requested or len(set(requested)) != len(requested):
        return response, []

    members, container_name = _transaction_members(response)
    if members is None or len(members) != len(requested):
        return response, []
    if any(not isinstance(item, dict) for item in members):
        return response, []

    explicit = 0
    for index, item in enumerate(members):
        account = seamless._account_id(item)
        if not account:
            continue
        explicit += 1
        if account != requested[index]:
            logging.getLogger(__name__).warning(
                "BULK_RESPONSE_POSITIONAL_CORRELATION_SKIPPED requested=%s members=%s "
                "container=%s reason=explicit_position_conflict explicit=%s "
                "assigned=0 safe=true duplicate_retry=false credentials_logged=false",
                len(requested),
                len(members),
                container_name,
                explicit,
            )
            return response, []

    assigned = 0
    correlated: list[dict[str, Any]] = []
    for index, raw in enumerate(members):
        item = dict(raw)
        if not seamless._account_id(item):
            item["account_id"] = requested[index]
            assigned += 1
        correlated.append(item)

    if not assigned:
        return response, []

    output = dict(response)
    if container_name == "data.transactions":
        data = dict(output.get("data") or {})
        data["transactions"] = correlated
        output["data"] = data
    else:
        output["transactions"] = correlated

    logging.getLogger(__name__).warning(
        "BULK_RESPONSE_POSITIONAL_CORRELATION requested=%s members=%s container=%s "
        "explicit=%s assigned=%s safe=true duplicate_retry=false credentials_logged=false",
        len(requested),
        len(correlated),
        container_name,
        explicit,
        assigned,
    )
    return output, [f"{container_name}:request_position_account_id"]


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
    canonical, positional_shapes = _positionally_correlate_transaction_members(
        canonical,
        request_body,
    )
    shapes.extend(positional_shapes)
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
            "account_keyed_successes=true success_aliases=true account_keyed_errors=true "
            "positional_transaction_members=safe_exact_cardinality "
            "blind_retry=false credentials_logged=false",
            _VERSION,
        )
    _INSTALLED = True
