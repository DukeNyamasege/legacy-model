from __future__ import annotations

"""Expose one canonical executable ManagedAccount per exact Deriv account identity.

Historical duplicate rows remain in PostgreSQL for audit/trade history. They simply
stop appearing as independent executable accounts, and future OAuth/PAT enrollment
updates the canonical row instead of adding another duplicate.
"""

from typing import Any

from app.repositories.test2_repository import Test2Repository
from app.token_store import decrypt_auth_payload


_INSTALLED = False
_ORIGINAL_LIST: Any = None
_ORIGINAL_ADD: Any = None


def _identity(repository: Test2Repository, row: Any) -> tuple[str, str] | None:
    key = str(repository.config.deriv.token_encryption_key or "").strip()
    if not key:
        return None
    try:
        payload = decrypt_auth_payload(row.token_secret, key)
    except Exception:
        return None
    account_id = str(payload.get("account_id") or "").strip().upper()
    if not account_id:
        return None
    account_type = str(payload.get("account_type") or "").strip().lower()
    if account_type not in {"demo", "real"}:
        account_type = "demo" if account_id.startswith(("VRTC", "DOT")) else "real"
    return account_type, account_id


def _rank(row: Any) -> tuple[int, float, int]:
    updated = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
    try:
        timestamp = float(updated.timestamp()) if updated is not None else 0.0
    except Exception:
        timestamp = 0.0
    return (1 if bool(getattr(row, "enabled", False)) else 0, timestamp, int(getattr(row, "id", 0)))


def _canonical_rows(repository: Test2Repository, rows: list[Any]) -> list[Any]:
    passthrough: list[Any] = []
    grouped: dict[tuple[str, str], list[Any]] = {}
    for row in rows:
        identity = _identity(repository, row)
        if identity is None:
            passthrough.append(row)
            continue
        grouped.setdefault(identity, []).append(row)

    canonical = list(passthrough)
    for candidates in grouped.values():
        canonical.append(max(candidates, key=_rank))
    canonical.sort(key=lambda row: (getattr(row, "created_at", None), int(getattr(row, "id", 0))))
    return canonical


def install_account_identity_canonical_authority() -> None:
    global _INSTALLED, _ORIGINAL_LIST, _ORIGINAL_ADD
    if _INSTALLED:
        return

    _ORIGINAL_LIST = Test2Repository.list_managed_accounts
    _ORIGINAL_ADD = Test2Repository.add_managed_account

    def list_canonical_accounts(self: Test2Repository) -> list[Any]:
        original = _ORIGINAL_LIST
        rows = list(original(self) if original is not None else [])
        return _canonical_rows(self, rows)

    def add_or_refresh_canonical_account(
        self: Test2Repository,
        *,
        label: str,
        token_secret: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        original_list = _ORIGINAL_LIST
        original_add = _ORIGINAL_ADD
        if original_add is None:
            raise RuntimeError("Managed-account enrollment authority is unavailable")

        class _Incoming:
            pass

        incoming = _Incoming()
        incoming.token_secret = str(token_secret)
        identity = _identity(self, incoming)
        if identity is not None and original_list is not None:
            candidates = [
                row
                for row in list(original_list(self) or [])
                if _identity(self, row) == identity
            ]
            if candidates:
                canonical = max(candidates, key=_rank)
                # Re-enrollment refreshes credentials/label on the canonical row.
                # It does not silently Start trading; preserve the row's current
                # enabled lifecycle unless this is the first enrollment path.
                return self.update_managed_account(
                    int(canonical.id),
                    label=str(label or canonical.label or ""),
                    token_secret=str(token_secret),
                    enabled=bool(canonical.enabled),
                )

        return original_add(
            self,
            label=label,
            token_secret=token_secret,
            enabled=enabled,
        )

    Test2Repository.list_managed_accounts = list_canonical_accounts
    Test2Repository.add_managed_account = add_or_refresh_canonical_account
    Test2Repository._account_identity_canonical_authority_installed = True
    _INSTALLED = True
