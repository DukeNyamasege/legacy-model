from __future__ import annotations

import copy
import logging
from typing import Any

import app.seamless_execution_runtime as seamless


_INSTALLED = False
_VERSION = "bulk-member-reconciliation-v5"
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
_MEMBER_WRAPPERS = (
    "transaction",
    "buy",
    "contract",
    "result",
    "data",
    "response",
    "purchase",
)
_CONTRACT_ID_KEYS = ("contract_id", "contractId", "contractid")
_TRANSACTION_ID_KEYS = (
    "transaction_id",
    "transactionId",
    "transactionid",
    "buy_transaction_id",
)
_ACCOUNT_ID_KEYS = ("account_id", "loginid", "login_id", "account")
_MAX_MEMBER_DEPTH = 5


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
    """Promote provider success aliases to a container the core normalizer reads."""

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
    """Return the authoritative transaction member array when available."""

    data = response.get("data")
    if isinstance(data, dict) and isinstance(data.get("transactions"), list):
        return data["transactions"], "data.transactions"
    if isinstance(response.get("transactions"), list):
        return response["transactions"], "transactions"
    return None, ""


def _walk_member_dicts(
    value: Any,
    *,
    path: str = "member",
    depth: int = 0,
) -> list[tuple[str, dict[str, Any]]]:
    """Walk only known provider wrappers; never recurse through arbitrary values."""

    if not isinstance(value, dict) or depth > _MAX_MEMBER_DEPTH:
        return []
    found: list[tuple[str, dict[str, Any]]] = [(path, value)]
    if depth == _MAX_MEMBER_DEPTH:
        return found
    for key in _MEMBER_WRAPPERS:
        nested = value.get(key)
        if isinstance(nested, dict):
            found.extend(
                _walk_member_dicts(
                    nested,
                    path=f"{path}.{key}",
                    depth=depth + 1,
                )
            )
    return found


def _first_explicit_value(
    dictionaries: list[tuple[str, dict[str, Any]]],
    keys: tuple[str, ...],
) -> tuple[Any, str]:
    for path, item in dictionaries:
        for key in keys:
            value = item.get(key)
            if value not in {None, ""}:
                return value, f"{path}.{key}"
    return None, ""


def _nested_error(
    dictionaries: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str]:
    for path, item in dictionaries:
        error = item.get("error")
        if isinstance(error, dict):
            return dict(error), f"{path}.error"
        if item.get("code") and item.get("message"):
            return {
                "code": item.get("code"),
                "message": item.get("message"),
                **({"status": item.get("status")} if item.get("status") is not None else {}),
            }, path
    return None, ""


def _normalize_nested_transaction_member(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Expose only explicit nested provider IDs/errors at the member top level.

    A generic nested ``id`` is deliberately never promoted to ``contract_id``.
    That avoids turning a request, transaction or response identifier into a
    financial contract identifier.
    """

    member = dict(raw)
    dictionaries = _walk_member_dicts(member)
    shapes: list[str] = []

    if not seamless._account_id(member):
        account, path = _first_explicit_value(dictionaries, _ACCOUNT_ID_KEYS)
        if account not in {None, ""}:
            member["account_id"] = str(account).strip()
            shapes.append(f"account_id:{path}")

    if not member.get("contract_id"):
        contract_id, path = _first_explicit_value(dictionaries, _CONTRACT_ID_KEYS)
        if contract_id not in {None, ""}:
            member["contract_id"] = contract_id
            shapes.append(f"contract_id:{path}")

    if not member.get("transaction_id"):
        transaction_id, path = _first_explicit_value(dictionaries, _TRANSACTION_ID_KEYS)
        if transaction_id not in {None, ""}:
            member["transaction_id"] = transaction_id
            shapes.append(f"transaction_id:{path}")

    if not isinstance(member.get("error"), dict):
        error, path = _nested_error(dictionaries)
        if error is not None:
            member["error"] = error
            shapes.append(f"error:{path}")

    return member, shapes


def _normalize_nested_transaction_members(
    response: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    members, container_name = _transaction_members(response)
    if members is None:
        return response, []

    normalized_members: list[dict[str, Any]] = []
    markers: list[str] = []
    for index, raw in enumerate(members):
        if not isinstance(raw, dict):
            normalized_members.append(raw)
            continue
        member, shapes = _normalize_nested_transaction_member(raw)
        normalized_members.append(member)
        markers.extend(f"{container_name}[{index}].{shape}" for shape in shapes)

    if not markers:
        return response, []

    output = dict(response)
    if container_name == "data.transactions":
        data = dict(output.get("data") or {})
        data["transactions"] = normalized_members
        output["data"] = data
    else:
        output["transactions"] = normalized_members
    return output, markers


def _positionally_correlate_transaction_members(
    response: dict[str, Any],
    request_body: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Attach account identity only for an exact, conflict-free member array."""

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


def _mark_unresolved_transaction_members(
    response: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Turn structurally present but undecodable members into explicit errors.

    Deriv documents each HTTP-200 transaction member as either a purchase or a
    per-account error. A member containing neither after safe normalization is an
    unresolved provider shape, not a missing account. It must not be retried.
    """

    members, container_name = _transaction_members(response)
    if members is None:
        return response, []

    changed = False
    normalized_members: list[dict[str, Any]] = []
    markers: list[str] = []
    for index, raw in enumerate(members):
        if not isinstance(raw, dict):
            normalized_members.append(raw)
            continue
        item = dict(raw)
        if item.get("contract_id") or isinstance(item.get("error"), dict):
            normalized_members.append(item)
            continue
        if item.get("code") and item.get("message"):
            normalized_members.append(item)
            continue

        keys = sorted(
            str(key)
            for key in item.keys()
            if str(key).lower() not in {"token", "authorization", "access_token", "pat_token"}
        )
        item["error"] = {
            "code": "BULK_MEMBER_UNRESOLVED",
            "message": (
                "Deriv returned a transaction member without an explicit contract "
                "identifier or per-account error. The purchase was not retried."
            ),
        }
        normalized_members.append(item)
        changed = True
        markers.append(f"{container_name}[{index}]:unresolved_member")
        logging.getLogger(__name__).error(
            "BULK_MEMBER_UNRESOLVED_SHAPE member_index=%s keys=%s "
            "financial_retry=false credentials_logged=false",
            index,
            keys or ["none"],
        )

    if not changed:
        return response, []

    output = dict(response)
    if container_name == "data.transactions":
        data = dict(output.get("data") or {})
        data["transactions"] = normalized_members
        output["data"] = data
    else:
        output["transactions"] = normalized_members
    return output, markers


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
    canonical, nested_shapes = _normalize_nested_transaction_members(canonical)
    shapes.extend(nested_shapes)
    canonical, positional_shapes = _positionally_correlate_transaction_members(
        canonical,
        request_body,
    )
    shapes.extend(positional_shapes)
    canonical, unresolved_shapes = _mark_unresolved_transaction_members(canonical)
    shapes.extend(unresolved_shapes)

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
            "nested_member_contracts=true nested_member_errors=true "
            "unresolved_members=explicit_no_retry "
            "blind_retry=false credentials_logged=false",
            _VERSION,
        )
    _INSTALLED = True
