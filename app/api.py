from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return False

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
import requests
import json

from app.config import load_test2_config
from app.dashboard_metrics import build_execution_summary
from app.database import Database
from app.oauth_client import (
    build_authorization_url,
    build_pkce_pair,
    exchange_code_for_tokens,
    refresh_access_token,
    token_is_expiring,
)
from app.repositories.test2_repository import Test2Repository, mask_account_id
from app.repositories.rf_dir5_repository import RFDir5Repository
from app.token_store import (
    decrypt_auth_payload,
    decrypt_token,
    encrypt_auth_payload,
    encrypt_token,
    has_encryption_key,
    parse_token_lines,
)

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
CONFIG = load_test2_config(os.getenv("DERIV_BOT_CONFIG", ROOT / "config.yaml"))
DATABASE = Database(CONFIG.database_url)
DATABASE.create_schema()
REPOSITORY = Test2Repository(DATABASE, CONFIG)
RF_REPOSITORY = RFDir5Repository(REPOSITORY)
CONTROL_RATE: dict[str, deque[float]] = defaultdict(deque)
GLOBAL_ACCOUNT_REFRESH: dict[str, object] = {"last": 0.0, "accounts": []}
OAUTH_STATE_COOKIE = "deriv_oauth_state"
OAUTH_VERIFIER_COOKIE = "deriv_oauth_code_verifier"
CLIENT_SESSION_COOKIE = "client_session"
CLIENT_SESSION_DAYS = int(os.getenv("CLIENT_SESSION_DAYS", "30"))


class DashboardBroadcaster:
    """Tracks WebSocket dashboard clients and pushes snapshots to all of them."""

    def __init__(self) -> None:
        self._clients: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._clients:
                self._clients.remove(ws)

    async def broadcast(self, payload: dict) -> None:
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.disconnect(ws)


BROADCASTER = DashboardBroadcaster()


class ModeUpdateRequest(BaseModel):
    mode: str


class TokenImportRequest(BaseModel):
    tokens_text: str
    label_prefix: str = "Account"


class PersonalApiTokenRequest(BaseModel):
    api_token: str


class PersonalTradingSettingsRequest(BaseModel):
    stake_amount: float
    take_profit: float = 0.0
    stop_loss: float = 0.0


def oauth_client_id() -> str:
    value = str(CONFIG.deriv.oauth_client_id or CONFIG.deriv.app_id).strip()
    if not value:
        raise HTTPException(status_code=500, detail="OAuth client ID is not configured")
    return value


def oauth_redirect_url() -> str:
    value = str(CONFIG.deriv.oauth_redirect_url or "").strip()
    if not value:
        raise HTTPException(status_code=500, detail="OAuth redirect URL is not configured")
    return value


def oauth_cookie_secure(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").lower()
    if forwarded:
        return forwarded.split(",", 1)[0].strip() == "https"
    host = (request.url.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return request.url.scheme == "https"
    # VPS/custom-domain traffic can arrive at the app over an internal HTTP hop.
    # For public hosts we still need Secure cookies so browsers keep the session.
    return True


def session_hash(session_token: str) -> str:
    return hashlib.sha256(str(session_token).encode("utf-8")).hexdigest()


def redirect_with_oauth_error(message: str) -> RedirectResponse:
    return RedirectResponse(
        url="/?" + urlencode({"oauth_error": str(message or "OAuth failed")[:700]}),
        status_code=303,
    )


def redirect_to_dashboard(request: Request) -> RedirectResponse:
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.delete_cookie(OAUTH_VERIFIER_COOKIE)
    return response


def session_cookie_samesite() -> str:
    value = os.getenv("CLIENT_SESSION_SAMESITE", "lax").strip().lower()
    return value if value in {"lax", "strict", "none"} else "lax"


def session_cookie_domain() -> str | None:
    return os.getenv("CLIENT_SESSION_COOKIE_DOMAIN", "").strip() or None


def public_request_url_without_query(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    scheme = (
        forwarded_proto.split(",", 1)[0].strip()
        if forwarded_proto
        else request.url.scheme
    )
    forwarded_host = request.headers.get("x-forwarded-host", "")
    host = (
        forwarded_host.split(",", 1)[0].strip()
        if forwarded_host
        else request.headers.get("host", "")
    )
    if not host:
        host = request.url.netloc
    return f"{scheme}://{host}{request.url.path}"


def oauth_redirect_candidates(request: Request, landed_redirect_uri: str = "") -> list[str]:
    candidates: list[str] = []
    configured = oauth_redirect_url()
    actual = public_request_url_without_query(request)
    landed = str(landed_redirect_uri or "").strip()
    ordered = (landed, actual, configured) if landed else (actual, configured)
    for value in ordered:
        normalized = str(value or "").strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def build_oauth_state(code_verifier: str) -> str:
    payload = {
        "n": secrets.token_urlsafe(16),
        "v": code_verifier,
        "t": int(time.time()),
    }
    return encrypt_token(
        json.dumps(payload, separators=(",", ":")),
        CONFIG.deriv.token_encryption_key,
    )


def code_verifier_from_state(state: str) -> str:
    try:
        payload = json.loads(decrypt_token(state, CONFIG.deriv.token_encryption_key))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    issued_at = int(payload.get("t", 0) or 0)
    if not issued_at or time.time() - issued_at > 900:
        return ""
    return str(payload.get("v", "")).strip()


def load_options_accounts(access_token: str) -> list[dict]:
    response = requests.get(
        f"{CONFIG.deriv.rest_base_url.rstrip('/')}/trading/v1/options/accounts",
        headers={
            "Deriv-App-ID": str(CONFIG.deriv.app_id),
            "Authorization": f"Bearer {access_token}",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", [])


def trading_api_token_from_payload(payload: dict) -> str:
    explicit_pat = str(payload.get("pat_token", "")).strip()
    if explicit_pat:
        return explicit_pat
    auth_type = str(payload.get("auth_type", "pat")).strip().lower() or "pat"
    access_token = str(payload.get("access_token", "")).strip()
    if auth_type != "oauth":
        return access_token
    return ""


def has_trading_api_token(payload: dict) -> bool:
    return bool(trading_api_token_from_payload(payload))


def trading_ready_account_ids() -> set[str]:
    account_ids: set[str] = set()
    for row in REPOSITORY.list_managed_accounts():
        if not row.enabled:
            continue
        try:
            payload = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
        except Exception:
            continue
        if not has_trading_api_token(payload):
            continue
        account_id = str(payload.get("account_id", "")).strip()
        if account_id:
            account_ids.add(account_id)
    return account_ids


def linked_trading_account_ids() -> set[str]:
    account_ids: set[str] = set()
    for row in REPOSITORY.list_managed_accounts():
        try:
            payload = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
        except Exception:
            continue
        if not has_trading_api_token(payload):
            continue
        account_id = str(payload.get("account_id", "")).strip()
        if account_id:
            account_ids.add(account_id)
    return account_ids


def actively_executing_account_ids() -> set[str]:
    stale_seconds = max(15, int(os.getenv("ACCOUNT_EXECUTION_STALE_SECONDS", "45")))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    account_ids: set[str] = set()
    for row in REPOSITORY.list_managed_accounts():
        if not row.enabled or str(row.execution_status) != "active":
            continue
        updated_at = row.execution_status_updated_at
        if updated_at is None:
            continue
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if updated_at < cutoff:
            continue
        try:
            payload = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
        except Exception:
            continue
        if not has_trading_api_token(payload):
            continue
        account_id = str(payload.get("account_id", "")).strip()
        if account_id:
            account_ids.add(account_id)
    return account_ids


def master_account_context() -> tuple[object | None, dict, str]:
    rows = REPOSITORY.list_managed_accounts()
    configured = os.getenv("COPYTRADING_MASTER_ACCOUNT_ID", "").strip()
    fallback: tuple[object | None, dict, str] = (None, {}, "")
    for row in rows:
        try:
            payload = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
        except Exception:
            continue
        account_id = str(payload.get("account_id", "")).strip()
        if not account_id:
            continue
        context = (row, payload, account_id)
        if configured and account_id == configured:
            return context
        fallback_row = fallback[0]
        if fallback_row is None or (
            row.enabled and not bool(getattr(fallback_row, "enabled", False))
        ):
            fallback = context
    return fallback


def application_oauth_token() -> tuple[str, str]:
    row, payload, _ = master_account_context()
    if row is None:
        raise HTTPException(status_code=409, detail="Master account is not configured")

    is_pat = str(payload.get("auth_type", "")).strip().lower() == "pat"
    prefix = "oauth_" if is_pat else ""
    access_token = str(payload.get(f"{prefix}access_token", "")).strip()
    refresh_token_value = str(payload.get(f"{prefix}refresh_token", "")).strip()
    expires_at = str(payload.get(f"{prefix}expires_at", "")).strip()
    scope = str(payload.get(f"{prefix}scope", "")).strip()
    if not access_token:
        raise HTTPException(
            status_code=409,
            detail="Master account OAuth application credential is unavailable",
        )
    if "application_read" not in set(scope.split()):
        raise HTTPException(
            status_code=409,
            detail="Master OAuth credential requires the application_read scope",
        )

    if token_is_expiring({"expires_at": expires_at}):
        if not refresh_token_value:
            raise HTTPException(
                status_code=409,
                detail="Master OAuth application credential has expired",
            )
        try:
            refreshed = refresh_access_token(
                client_id=oauth_client_id(),
                refresh_token=refresh_token_value,
            )
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not refresh the Deriv application credential: {exc}",
            ) from exc
        access_token = str(refreshed.get("access_token", "")).strip()
        scope = str(refreshed.get("scope") or scope).strip()
        if is_pat:
            payload.update(
                {
                    "oauth_access_token": access_token,
                    "oauth_refresh_token": str(
                        refreshed.get("refresh_token") or refresh_token_value
                    ).strip(),
                    "oauth_expires_at": str(refreshed.get("expires_at", "")).strip(),
                    "oauth_scope": scope,
                }
            )
        else:
            payload.update(refreshed)
        REPOSITORY.update_managed_account(
            int(row.id),
            token_secret=encrypt_auth_payload(
                payload,
                CONFIG.deriv.token_encryption_key,
            ),
            enabled=bool(row.enabled),
        )
    return access_token, scope


def filter_summary_to_trading_ready_accounts(summary: dict) -> dict:
    active_ids = actively_executing_account_ids()
    linked_ids = linked_trading_account_ids()
    summaries_by_mask = {
        str(account.get("account", "")): dict(account)
        for account in list(summary.get("accounts") or [])
    }

    def existing_account_summary(account_id: str) -> dict:
        masked = mask_account_id(account_id)
        return summaries_by_mask.get(
            masked,
            {
                "account": masked,
                "balance": 0.0,
                "currency": "USD",
                "status": "linked",
                "updated_at": None,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "profit": 0.0,
            },
        )

    active_accounts = [
        existing_account_summary(account_id) for account_id in sorted(active_ids)
    ]
    all_linked_accounts = [
        existing_account_summary(account_id) for account_id in sorted(linked_ids)
    ]
    _, _, master_account_id = master_account_context()
    if not master_account_id and linked_ids:
        master_account_id = sorted(linked_ids)[0]
    master = REPOSITORY.account_summary(master_account_id) if master_account_id else None
    return build_execution_summary(
        summary,
        active_accounts=active_accounts,
        linked_accounts=all_linked_accounts,
        master=master,
    )


def merge_oauth_payload(existing: dict, oauth_payload: dict, account_id: str) -> dict:
    merged = dict(oauth_payload)
    merged["account_id"] = account_id
    merged["auth_source"] = "deriv_oauth"
    trading_token = trading_api_token_from_payload(existing)
    if trading_token:
        merged.update(
            {
                "auth_type": "pat",
                "access_token": trading_token,
                "pat_token_set": True,
                "pat_verified_at": str(existing.get("pat_verified_at", "")).strip(),
                "oauth_access_token": str(oauth_payload.get("access_token", "")).strip(),
                "oauth_refresh_token": str(oauth_payload.get("refresh_token", "")).strip(),
                "oauth_expires_at": str(oauth_payload.get("expires_at", "")).strip(),
                "oauth_scope": str(oauth_payload.get("scope", "")).strip(),
            }
        )
    return merged


def decrypt_runtime_token(token: str) -> str:
    value = str(token or "").strip()
    if not value or not CONFIG.deriv.token_encryption_key:
        return value
    try:
        return decrypt_token(value, CONFIG.deriv.token_encryption_key)
    except Exception:
        return value


def global_runtime_tokens() -> list[str]:
    raw_tokens: list[str] = []
    env_tokens = os.getenv("DERIV_TOKENS", "")
    if env_tokens:
        raw_tokens.extend(
            token.strip()
            for token in re.split(r"[\r\n,]+", env_tokens)
            if token.strip()
        )
    env_token = os.getenv("DERIV_TOKEN", "").strip()
    if env_token:
        raw_tokens.append(env_token)

    token_file = os.getenv("DERIV_TOKENS_FILE", CONFIG.files.tokens)
    token_path = Path(token_file)
    if not token_path.is_absolute():
        token_path = ROOT / token_path
    if token_path.exists():
        raw_tokens.extend(parse_token_lines(token_path.read_text(encoding="utf-8")))

    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in raw_tokens:
        token = decrypt_runtime_token(raw_token)
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def legacy_global_tokens_enabled() -> bool:
    return os.getenv("COPYTRADING_ALLOW_LEGACY_GLOBAL_TOKENS", "false").lower() in {
        "1",
        "true",
        "yes",
    }


def refresh_global_account_snapshots(*, force: bool = False) -> list[dict]:
    ttl_seconds = max(5, int(os.getenv("GLOBAL_ACCOUNT_REFRESH_SECONDS", "30")))
    now = time.monotonic()
    if (
        not force
        and now - float(GLOBAL_ACCOUNT_REFRESH.get("last") or 0.0) < ttl_seconds
    ):
        return list(GLOBAL_ACCOUNT_REFRESH.get("accounts") or [])

    runtime_mode_value = REPOSITORY.runtime_mode()
    updated_accounts: list[dict] = []
    seen_accounts: set[str] = set()

    def refresh_from_token(token: str, preferred_account_id: str = "") -> None:
        try:
            accounts = load_options_accounts(token)
        except requests.RequestException:
            return
        matched = None
        if preferred_account_id:
            matched = next(
                (
                    account
                    for account in accounts
                    if str(account.get("account_id", "")).strip() == preferred_account_id
                ),
                None,
            )
        if matched is None:
            matched = next(
                (account for account in accounts if account.get("account_type") == runtime_mode_value),
                accounts[0] if accounts else None,
            )
        if not matched:
            return
        account_id = str(matched.get("account_id", "")).strip()
        if not account_id or account_id in seen_accounts:
            return
        try:
            REPOSITORY.update_account_balance(
                account_id=account_id,
                balance=float(matched.get("balance", 0.0)),
                currency=str(matched.get("currency", "USD")),
                status=str(matched.get("status", "active")),
            )
            updated_accounts.append(REPOSITORY.account_summary(account_id))
            seen_accounts.add(account_id)
        except (TypeError, ValueError):
            return

    managed_accounts = REPOSITORY.list_managed_accounts()
    if not managed_accounts and legacy_global_tokens_enabled():
        for token in global_runtime_tokens():
            refresh_from_token(token)

    for row in managed_accounts:
        if not row.enabled:
            continue
        try:
            payload = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
        except Exception:
            continue
        if str(payload.get("auth_type", "pat")).strip() == "oauth" and token_is_expiring(payload):
            refresh_token_value = str(payload.get("refresh_token", "")).strip()
            if refresh_token_value:
                try:
                    payload.update(
                        refresh_access_token(
                            client_id=oauth_client_id(),
                            refresh_token=refresh_token_value,
                        )
                    )
                    REPOSITORY.update_managed_account(
                        int(row.id),
                        token_secret=encrypt_auth_payload(
                            payload,
                            CONFIG.deriv.token_encryption_key,
                        ),
                        enabled=bool(row.enabled),
                    )
                except Exception:
                    pass
        token = trading_api_token_from_payload(payload)
        if token:
            refresh_from_token(
                token,
                preferred_account_id=str(payload.get("account_id", "")).strip(),
            )

    GLOBAL_ACCOUNT_REFRESH["last"] = now
    GLOBAL_ACCOUNT_REFRESH["accounts"] = updated_accounts
    return updated_accounts


def refresh_account_snapshot(
    access_token: str,
    *,
    preferred_account_id: str = "",
) -> dict | None:
    runtime_mode_value = REPOSITORY.runtime_mode()
    try:
        accounts = load_options_accounts(access_token)
    except requests.RequestException:
        return None

    matched = None
    if preferred_account_id:
        matched = next(
            (
                account
                for account in accounts
                if str(account.get("account_id", "")).strip() == preferred_account_id
            ),
            None,
        )
    if matched is None:
        matched = next(
            (account for account in accounts if account.get("account_type") == runtime_mode_value),
            accounts[0] if accounts else None,
        )
    if not matched:
        return None

    account_id = str(matched.get("account_id", "")).strip()
    if not account_id:
        return None
    try:
        REPOSITORY.update_account_balance(
            account_id=account_id,
            balance=float(matched.get("balance", 0.0)),
            currency=str(matched.get("currency", "USD")),
            status=str(matched.get("status", "active")),
        )
    except (TypeError, ValueError):
        return None
    return REPOSITORY.account_summary(account_id)


def refresh_personal_account_snapshot(account: dict) -> dict | None:
    try:
        row = REPOSITORY.managed_account(int(account["id"]))
    except Exception:
        return None
    if not row:
        return None
    try:
        payload = decrypt_auth_payload(row["token_secret"], CONFIG.deriv.token_encryption_key)
    except Exception:
        return None
    access_token = trading_api_token_from_payload(payload)
    if not access_token:
        access_token = str(payload.get("access_token", "")).strip()
    if not access_token:
        access_token = str(payload.get("token", "")).strip()
    if not access_token:
        return None
    return refresh_account_snapshot(
        access_token,
        preferred_account_id=str(account.get("account_id", "")).strip(),
    )

app = FastAPI(
    title="RF-DIR5 Guarded",
    version=CONFIG.model.version,
    description=CONFIG.model.brand,
)

frontend_origins = [
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://127.0.0.1:8080,http://localhost:8080,https://derivadmin.site",
    ).split(",")
    if origin.strip()
]
frontend_origin_regex = os.getenv("FRONTEND_ORIGIN_REGEX", "").strip() or None
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_origin_regex=frontend_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


def require_control_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> str:
    client_host = request.client.host if request.client else ""
    local_control = os.getenv("LOCAL_CONTROL_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    if local_control and client_host in {"127.0.0.1", "::1", "localhost"}:
        return "local-administrator"
    expected = os.getenv("CONTROL_API_KEY", "")
    supplied = x_api_key or (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else ""
    )
    if not expected or supplied != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid control API authentication is required",
        )
    return "administrator"


def enforce_control_rate_limit(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    history = CONTROL_RATE[client]
    while history and now - history[0] > 60:
        history.popleft()
    if len(history) >= 20:
        raise HTTPException(status_code=429, detail="Control rate limit exceeded")
    history.append(now)


@app.get("/")
def dashboard(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    if code or error:
        return oauth_callback(
            request,
            code=code,
            state=state,
            error=error,
            error_description=error_description,
        )
    return FileResponse(ROOT / "dashboard" / "index.html")


@app.get("/health/live")
def health_live() -> dict:
    return {"status": "live", "service": "test2-api"}


@app.get("/health")
def health() -> dict:
    return health_live()


@app.get("/health/ready")
def health_ready() -> dict:
    if not DATABASE.ping():
        raise HTTPException(status_code=503, detail="Database unavailable")
    summary = REPOSITORY.summary()
    heartbeat = summary.get("last_heartbeat")
    if not heartbeat:
        raise HTTPException(status_code=503, detail="Worker heartbeat unavailable")
    heartbeat_time = datetime.fromisoformat(heartbeat)
    if heartbeat_time.tzinfo is None:
        heartbeat_time = heartbeat_time.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - heartbeat_time).total_seconds()
    if age_seconds > 45:
        raise HTTPException(status_code=503, detail="Worker heartbeat is stale")
    return {
        "status": "ready",
        "database": "connected",
        "worker_heartbeat": heartbeat,
    }


@app.get("/status")
def bot_status() -> dict:
    summary = REPOSITORY.summary()
    return {
        "model": CONFIG.model.name,
        "version": CONFIG.model.version,
        "brand": CONFIG.model.brand,
        "mode": summary.get("mode", CONFIG.deriv.environment),
        "trading_enabled": CONFIG.deriv.trading_enabled,
        **summary,
    }


@app.get("/api/status")
def legacy_status_alias() -> dict:
    return bot_status()


@app.get("/runtime")
def runtime_mode(request: Request) -> dict:
    client_host = request.client.host if request.client else ""
    local_control = os.getenv("LOCAL_CONTROL_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    return {
        "local_control": local_control
        and client_host in {"127.0.0.1", "::1", "localhost"},
        "read_only_dashboard": False,
    }


@app.get("/oauth/start")
def oauth_start(request: Request) -> RedirectResponse:
    if not has_encryption_key(CONFIG.deriv.token_encryption_key):
        raise HTTPException(
            status_code=409,
            detail="Set DERIV_TOKEN_ENCRYPTION_KEY before linking OAuth accounts.",
        )
    code_verifier, _ = build_pkce_pair()
    state = build_oauth_state(code_verifier)
    authorization_url, code_verifier = build_authorization_url(
        client_id=oauth_client_id(),
        redirect_uri=oauth_redirect_url(),
        state=state,
        code_verifier=code_verifier,
    )
    REPOSITORY.create_oauth_login_state(
        state_hash=session_hash(state),
        code_verifier_secret=encrypt_token(code_verifier, CONFIG.deriv.token_encryption_key),
        redirect_uri=oauth_redirect_url(),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    response = RedirectResponse(url=authorization_url, status_code=302)
    secure = oauth_cookie_secure(request)
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=900,
    )
    response.set_cookie(
        OAUTH_VERIFIER_COOKIE,
        code_verifier,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=900,
    )
    return response


@app.get("/oauth/callback")
def oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    landed_redirect_uri: str = "",
) -> RedirectResponse:
    if error:
        return redirect_with_oauth_error(error_description or error)
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE, "")
    code_verifier = request.cookies.get(OAUTH_VERIFIER_COOKIE, "")
    stored_state = REPOSITORY.oauth_login_state(session_hash(state)) if state else None
    state_code_verifier = code_verifier_from_state(state) if state else ""
    if not code or not state:
        return redirect_with_oauth_error("OAuth session is incomplete or expired")
    if expected_state and state != expected_state and not state_code_verifier:
        return redirect_with_oauth_error("OAuth state validation failed")
    if not code_verifier and stored_state:
        try:
            code_verifier = decrypt_token(
                stored_state["code_verifier_secret"],
                CONFIG.deriv.token_encryption_key,
            )
        except Exception:
            code_verifier = ""
    if not code_verifier:
        code_verifier = state_code_verifier
    if not code_verifier:
        return redirect_with_oauth_error("OAuth session is incomplete or expired")

    token_payload = None
    exchange_error = ""
    for redirect_uri in oauth_redirect_candidates(request, landed_redirect_uri):
        try:
            token_payload = exchange_code_for_tokens(
                client_id=oauth_client_id(),
                redirect_uri=redirect_uri,
                code=code,
                code_verifier=code_verifier,
            )
            break
        except requests.HTTPError as exc:
            exchange_error = exc.response.text if exc.response is not None else str(exc)
    if state:
        REPOSITORY.delete_oauth_login_state(session_hash(state))
    if token_payload is None:
        if get_current_account(request):
            return redirect_to_dashboard(request)
        return redirect_with_oauth_error(f"OAuth token exchange failed: {exchange_error}")
    try:
        accounts = load_options_accounts(token_payload["access_token"])
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        return redirect_with_oauth_error(f"OAuth account lookup failed: {detail}")

    runtime_mode_value = REPOSITORY.runtime_mode()
    matched = next(
        (account for account in accounts if account.get("account_type") == runtime_mode_value),
        accounts[0] if accounts else None,
    )
    if not matched:
        raise HTTPException(
            status_code=400,
            detail=f"No Options account is available for runtime mode {runtime_mode_value}",
        )

    account_id = str(matched.get("account_id", "")).strip()
    label = f"OAuth {account_id[:3]}***{account_id[-3:]}" if len(account_id) > 6 else "OAuth Account"

    existing_id = None
    existing_enabled = None
    existing_payload: dict = {}
    for row in REPOSITORY.list_managed_accounts():
        try:
            stored = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
        except Exception:
            continue
        if str(stored.get("account_id", "")).strip() == account_id:
            existing_id = int(row.id)
            existing_enabled = bool(row.enabled)
            existing_payload = stored
            break
    token_secret = encrypt_auth_payload(
        merge_oauth_payload(existing_payload, token_payload, account_id),
        CONFIG.deriv.token_encryption_key,
    )
    if existing_id is None:
        account_row = REPOSITORY.add_managed_account(
            label=label,
            token_secret=token_secret,
            enabled=False,
        )
    else:
        account_row = REPOSITORY.update_managed_account(
            existing_id,
            label=label,
            token_secret=token_secret,
            enabled=existing_enabled,
        )

    try:
        REPOSITORY.update_account_balance(
            account_id=account_id,
            balance=float(matched.get("balance", 0.0)),
            currency=str(matched.get("currency", "USD")),
            status=str(matched.get("status", "active")),
        )
    except (TypeError, ValueError):
        pass

    REPOSITORY.audit(
        "OAUTH_ACCOUNT_LINKED",
        "oauth-callback",
        request.client.host if request.client else "unknown",
        {"account_id_masked": label, "mode": runtime_mode_value},
    )

    raw_session_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=CLIENT_SESSION_DAYS)
    REPOSITORY.create_client_session(
        session_hash=session_hash(raw_session_token),
        managed_account_id=int(account_row["id"]),
        expires_at=expires_at,
    )

    response = RedirectResponse(url="/", status_code=303)
    secure = oauth_cookie_secure(request)
    same_site = session_cookie_samesite()
    response.set_cookie(
        key=CLIENT_SESSION_COOKIE,
        value=raw_session_token,
        httponly=True,
        secure=True if same_site == "none" else secure,
        samesite=same_site,
        max_age=86400 * CLIENT_SESSION_DAYS,
        domain=session_cookie_domain(),
    )
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.delete_cookie(OAUTH_VERIFIER_COOKIE)
    return response

def get_current_account(request: Request) -> dict | None:
    session_token = request.cookies.get(CLIENT_SESSION_COOKIE)
    if not session_token:
        return None
    account = REPOSITORY.client_session_account(session_hash(session_token))
    if account:
        try:
            stored = decrypt_auth_payload(account["token_secret"], CONFIG.deriv.token_encryption_key)
        except Exception:
            return None
        account_id = str(stored.get("account_id", "")).strip()
        if not account_id:
            return None
        token_ready = has_trading_api_token(stored)
        return {
            "id": account["id"],
            "account_id": account_id,
            "account_id_masked": mask_account_id(account_id),
            "label": account["label"],
            "enabled": account["enabled"],
            "stake_amount": float(account.get("stake_amount", 0.50)),
            "take_profit": float(account.get("take_profit", 0.0)),
            "stop_loss": float(account.get("stop_loss", 0.0)),
            "execution_status": str(account.get("execution_status", "inactive")),
            "execution_status_reason": str(
                account.get("execution_status_reason", "")
            ),
            "has_trading_api_token": token_ready,
            "requires_api_token": not token_ready,
            "created_at": account["created_at"],
        }

    # Backward-compatible fallback for browsers that received the previous
    # encrypted account-id cookie before server-side sessions existed.
    try:
        decrypted = decrypt_token(session_token, CONFIG.deriv.token_encryption_key)
        payload = json.loads(decrypted)
        account_id = payload.get("account_id")
        if not account_id:
            return None
        
        for row in REPOSITORY.list_managed_accounts():
            try:
                stored = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
            except Exception:
                continue
            if str(stored.get("account_id", "")).strip() == account_id:
                token_ready = has_trading_api_token(stored)
                return {
                    "id": row.id,
                    "account_id": account_id,
                    "account_id_masked": mask_account_id(account_id),
                    "label": row.label,
                    "enabled": row.enabled,
                    "stake_amount": float(row.stake_amount),
                    "take_profit": float(row.take_profit),
                    "stop_loss": float(row.stop_loss),
                    "execution_status": str(row.execution_status),
                    "execution_status_reason": str(row.execution_status_reason),
                    "has_trading_api_token": token_ready,
                    "requires_api_token": not token_ready,
                    "created_at": row.created_at,
                }
    except Exception:
        pass
    return None

class AutoTradeRequest(BaseModel):
    enabled: bool

@app.get("/me")
def get_me(request: Request) -> dict:
    account = get_current_account(request)
    if not account:
        return {"authenticated": False}
    refresh_personal_account_snapshot(account)
    personal = REPOSITORY.account_summary(account["account_id"])
            
    return {
        "authenticated": True,
        "account_id": personal["account"],
        "label": f"Account {account['account_id_masked']}",
        "enabled": account["enabled"],
        "has_trading_api_token": account.get("has_trading_api_token", False),
        "requires_api_token": account.get("requires_api_token", True),
        "balance": personal["balance"],
        "currency": personal["currency"],
        "status": personal["status"],
        "execution_status": account.get("execution_status", "inactive"),
        "execution_status_reason": account.get("execution_status_reason", ""),
        "settings": {
            "stake_amount": float(account.get("stake_amount", 0.50)),
            "take_profit": float(account.get("take_profit", 0.0)),
            "stop_loss": float(account.get("stop_loss", 0.0)),
        },
        "stats": {
            "trades": personal["trades"],
            "wins": personal["wins"],
            "losses": personal["losses"],
            "profit": personal["profit"],
        },
    }

@app.post("/me/auto-trade")
def toggle_auto_trade(request: Request, body: AutoTradeRequest) -> dict:
    account = get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if body.enabled and not account.get("has_trading_api_token", False):
        raise HTTPException(
            status_code=409,
            detail="Save a Deriv API token for this account before joining auto trading.",
        )
    
    REPOSITORY.set_managed_account_enabled(account["id"], body.enabled)
    if body.enabled:
        REPOSITORY.set_status("RUNNING", "")
    elif not trading_ready_account_ids():
        REPOSITORY.set_status("STOPPED", "")
    return {"success": True, "enabled": body.enabled}


@app.post("/me/trading-settings")
def update_personal_trading_settings(
    request: Request,
    body: PersonalTradingSettingsRequest,
) -> dict:
    account = get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not account.get("has_trading_api_token", False):
        raise HTTPException(
            status_code=409,
            detail="Save a Deriv API token before configuring trading controls.",
        )

    values = (body.stake_amount, body.take_profit, body.stop_loss)
    if not all(math.isfinite(float(value)) for value in values):
        raise HTTPException(status_code=400, detail="Trading settings must be finite numbers.")
    minimum_stake = float(CONFIG.strategy.initial_stake)
    maximum_stake = 1_000_000.0
    stake_amount = round(float(body.stake_amount), 2)
    take_profit = round(float(body.take_profit), 2)
    stop_loss = round(float(body.stop_loss), 2)
    if not minimum_stake <= stake_amount <= maximum_stake:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Stake must be between {minimum_stake:.2f} and "
                f"{maximum_stake:.2f} USD."
            ),
        )
    if take_profit < 0 or stop_loss < 0:
        raise HTTPException(
            status_code=400,
            detail="Take profit and stop loss must be 0 or greater.",
        )
    if take_profit > 1_000_000 or stop_loss > 1_000_000:
        raise HTTPException(status_code=400, detail="Risk limits are too large.")

    settings = REPOSITORY.update_account_execution_settings(
        int(account["id"]),
        stake_amount=stake_amount,
        take_profit=take_profit,
        stop_loss=stop_loss,
    )
    REPOSITORY.audit(
        "PERSONAL_TRADING_SETTINGS_UPDATED",
        "account-dashboard",
        request.client.host if request.client else "unknown",
        {
            "account_id_masked": account["account_id_masked"],
            **settings,
        },
    )
    return {"success": True, "settings": settings}


@app.post("/me/api-token")
def save_personal_api_token(request: Request, body: PersonalApiTokenRequest) -> dict:
    account = get_current_account(request)
    if not account:
        raise HTTPException(status_code=401, detail="Not authenticated")
    api_token = str(body.api_token or "").strip()
    if not api_token:
        raise HTTPException(status_code=400, detail="Enter a Deriv API token.")
    if not has_encryption_key(CONFIG.deriv.token_encryption_key):
        raise HTTPException(
            status_code=409,
            detail="DERIV_TOKEN_ENCRYPTION_KEY is required before storing API tokens.",
        )
    row = REPOSITORY.managed_account(int(account["id"]))
    if not row:
        raise HTTPException(status_code=404, detail="Managed account was not found.")
    try:
        payload = decrypt_auth_payload(row["token_secret"], CONFIG.deriv.token_encryption_key)
    except Exception:
        payload = {
            "auth_type": "oauth",
            "account_id": str(account["account_id"]).strip(),
        }
    if has_trading_api_token(payload):
        raise HTTPException(
            status_code=409,
            detail="A Deriv API token is already connected for this account.",
        )
    try:
        accounts = load_options_accounts(api_token)
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=400, detail=f"API token verification failed: {detail}")
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"API token verification failed: {exc}")

    account_id = str(account["account_id"]).strip()
    runtime_mode_value = REPOSITORY.runtime_mode()
    matched = next(
        (
            item
            for item in accounts
            if str(item.get("account_id", "")).strip() == account_id
            and str(item.get("account_type", "")).strip() == runtime_mode_value
        ),
        None,
    )
    if not matched:
        raise HTTPException(
            status_code=400,
            detail=(
                "This API token does not match the logged-in Options "
                f"{runtime_mode_value} account."
            ),
        )

    if str(payload.get("auth_type", "")).strip().lower() == "oauth":
        payload["oauth_access_token"] = str(payload.get("access_token", "")).strip()
        payload["oauth_refresh_token"] = str(payload.get("refresh_token", "")).strip()
        payload["oauth_expires_at"] = str(payload.get("expires_at", "")).strip()
        payload["oauth_scope"] = str(payload.get("scope", "")).strip()
    payload.update(
        {
            "auth_type": "pat",
            "access_token": api_token,
            "account_id": account_id,
            "auth_source": "deriv_oauth_with_pat",
            "pat_token_set": True,
            "pat_verified_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    token_secret = encrypt_auth_payload(payload, CONFIG.deriv.token_encryption_key)
    label = f"Account {account_id[:3]}***{account_id[-3:]}" if len(account_id) > 6 else "Account"
    REPOSITORY.update_managed_account(
        int(account["id"]),
        label=label,
        token_secret=token_secret,
        enabled=bool(account.get("enabled", False)),
    )
    REPOSITORY.set_managed_account_execution_status(
        int(account["id"]),
        "connecting" if account.get("enabled", False) else "disabled",
        (
            "Trading API token verified"
            if account.get("enabled", False)
            else "Trading API token verified; auto trading is disabled"
        ),
    )
    try:
        REPOSITORY.update_account_balance(
            account_id=account_id,
            balance=float(matched.get("balance", 0.0)),
            currency=str(matched.get("currency", "USD")),
            status=str(matched.get("status", "active")),
        )
    except (TypeError, ValueError):
        pass
    REPOSITORY.audit(
        "PERSONAL_API_TOKEN_SAVED",
        "account-dashboard",
        request.client.host if request.client else "unknown",
        {"account_id_masked": mask_account_id(account_id), "mode": runtime_mode_value},
    )
    return {
        "success": True,
        "has_trading_api_token": True,
        "requires_api_token": False,
        "account_id": mask_account_id(account_id),
    }

@app.post("/me/logout")
def logout(request: Request) -> JSONResponse:
    session_token = request.cookies.get(CLIENT_SESSION_COOKIE)
    if session_token:
        REPOSITORY.delete_client_session(session_hash(session_token))
    response = JSONResponse({"success": True})
    response.delete_cookie(CLIENT_SESSION_COOKIE, domain=session_cookie_domain())
    return response


@app.get("/metrics/summary")
def metrics_summary() -> dict:
    summary = REPOSITORY.summary()
    if not summary.get("accounts") or float(summary.get("account_balance_total") or 0.0) <= 0:
        refresh_global_account_snapshots(force=True)
        summary = REPOSITORY.summary()
    else:
        if refresh_global_account_snapshots():
            summary = REPOSITORY.summary()
    filtered = filter_summary_to_trading_ready_accounts(summary)
    guard = RF_REPOSITORY.guard_state()
    groups = RF_REPOSITORY.shadow_groups()
    filtered.update(
        {
            "strategy_name": CONFIG.rf_strategy.name,
            "execution_phase": "EXPLORATION",
            "virtual_guard_state": guard["state"],
            "shadow_settled": sum(group["wins"] + group["losses"] for group in groups),
            "shadow_profit": sum(group["profit"] for group in groups),
        }
    )
    return filtered


@app.get("/metrics/recent-trades")
def recent_trades(request: Request, limit: int = 50) -> dict:
    current = get_current_account(request)
    if current:
        account_id = str(current["account_id"])
        viewer = "personal"
    else:
        _, _, account_id = master_account_context()
        viewer = "master"
    if not account_id:
        return {"viewer": viewer, "account": "", "trades": [], "markup": {}}
    return {
        "viewer": viewer,
        "account": mask_account_id(account_id),
        "trades": REPOSITORY.recent_trades(
            max(1, min(limit, 50)),
            account_id=account_id,
        ),
        "markup": REPOSITORY.markup_summary(account_id=account_id),
    }


@app.get("/metrics/recent-signals")
def recent_signals(limit: int = 50) -> dict:
    return {"signals": REPOSITORY.recent_signals(max(1, min(limit, 200)))}


@app.get("/metrics/model")
def model_metrics() -> dict:
    return {
        "bayesian": {
            "mode": "per_market_direction_duration",
            "prior_alpha": CONFIG.bayesian.prior_alpha,
            "prior_beta": CONFIG.bayesian.prior_beta,
            "minimum_shadow_outcomes": CONFIG.bayesian.minimum_shadow_outcomes,
            "groups": RF_REPOSITORY.shadow_groups(),
        },
        "strategy": {
            "name": CONFIG.rf_strategy.name,
            "phase": "EXPLORATION",
            "hmm_enabled": False,
            "martingale_enabled": CONFIG.risk.recovery_enabled,
            "recovery_trigger_losses": CONFIG.risk.recovery_trigger_losses,
            "maximum_recovery_attempts": CONFIG.risk.maximum_recovery_attempts,
            "recovery_risk_cap_percent": CONFIG.risk.maximum_stake_balance_percent,
            "virtual_guard": RF_REPOSITORY.guard_state(),
        },
    }


@app.get("/metrics/rf-strategy")
def rf_strategy_metrics() -> dict:
    return {
        "strategy": CONFIG.rf_strategy.name,
        "phase": "EXPLORATION",
        "virtual_guard": RF_REPOSITORY.guard_state(),
        "shadow_groups": RF_REPOSITORY.shadow_groups(),
    }


@app.get("/control/markup-statistics")
def markup_statistics(
    date_from: str | None = None,
    date_to: str | None = None,
    _: str = Depends(require_control_auth),
) -> dict:
    today = datetime.now(timezone.utc).date()
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else today
        end = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Dates must use YYYY-MM-DD") from exc
    if end < start or (end - start).days > 183:
        raise HTTPException(status_code=400, detail="Date range must be 0 to 183 days")

    access_token, _scope = application_oauth_token()
    try:
        response = requests.get(
            f"{CONFIG.deriv.rest_base_url.rstrip('/')}/applications/v1/markup-statistics",
            params={"date_from": start.isoformat(), "date_to": end.isoformat()},
            headers={
                "Deriv-App-ID": str(CONFIG.deriv.app_id),
                "Authorization": f"Bearer {access_token}",
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = exc.response.text[:700] if exc.response is not None else str(exc)
        raise HTTPException(
            status_code=502,
            detail=f"Deriv markup-statistics request failed: {detail}",
        ) from exc
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Deriv markup-statistics request failed: {exc}",
        ) from exc

    try:
        payload = response.json()
    except requests.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail="Deriv markup-statistics returned invalid JSON",
        ) from exc
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    breakdown = list(data.get("breakdown") or [])
    app_row = next(
        (
            item
            for item in breakdown
            if str(item.get("app_id", "")).strip() == str(CONFIG.deriv.app_id)
        ),
        breakdown[0] if len(breakdown) == 1 else {},
    )
    app_markup_usd = float(app_row.get("app_markup_usd") or 0.0)
    contract_count = int(
        app_row.get("contract_count") or data.get("total_contract_count") or 0
    )
    if contract_count == 0:
        collection_status = "no_contracts_in_period"
    elif app_markup_usd > 0:
        collection_status = "collecting"
    else:
        collection_status = "not_collecting"
    return {
        "source": "deriv_markup_statistics",
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "app_id": str(CONFIG.deriv.app_id),
        "expected_markup_percentage": float(CONFIG.deriv.app_markup_percentage),
        "collection_status": collection_status,
        "app": app_row,
        "totals": {
            "app_markup_usd": float(data.get("total_app_markup_usd") or 0.0),
            "volume_usd": float(data.get("total_volume_usd") or 0.0),
            "payout_usd": float(data.get("total_payout_usd") or 0.0),
            "contract_count": int(data.get("total_contract_count") or 0),
            "client_count": int(data.get("total_client_count") or 0),
        },
    }


def apply_control(
    *,
    request: Request,
    actor: str,
    target_status: str,
    reason: str,
) -> dict:
    enforce_control_rate_limit(request)
    REPOSITORY.set_status(target_status, reason)
    REPOSITORY.audit(
        target_status,
        actor,
        request.client.host if request.client else "unknown",
        {"reason": reason},
    )
    return filter_summary_to_trading_ready_accounts(REPOSITORY.summary())


@app.post("/control/pause")
def pause(
    request: Request, actor: str = Depends(require_control_auth)
) -> dict:
    return apply_control(
        request=request,
        actor=actor,
        target_status="MANUAL_PAUSE",
        reason="ADMINISTRATIVE_PAUSE",
    )


@app.post("/control/resume")
def resume(
    request: Request, actor: str = Depends(require_control_auth)
) -> dict:
    return apply_control(
        request=request,
        actor=actor,
        target_status="RUNNING",
        reason="",
    )


@app.post("/control/emergency-stop")
def emergency_stop(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    account = get_current_account(request)
    if account:
        enforce_control_rate_limit(request)
        REPOSITORY.set_managed_account_enabled(account["id"], False)
        remaining_accounts = trading_ready_account_ids()
        current_status, _ = REPOSITORY.control_state()
        if not remaining_accounts:
            REPOSITORY.set_status("STOPPED", "")
        elif current_status != "MANUAL_PAUSE":
            REPOSITORY.set_status("RUNNING", "")
        REPOSITORY.audit(
            "PERSONAL_EMERGENCY_STOP",
            str(account.get("account_id_masked", "account")),
            request.client.host if request.client else "unknown",
            {"managed_account_id": account["id"]},
        )
        summary = filter_summary_to_trading_ready_accounts(REPOSITORY.summary())
        summary["personal_emergency_stop"] = {
            "success": True,
            "account": account.get("account_id_masked", ""),
            "enabled": False,
        }
        return summary

    actor = require_control_auth(
        request,
        authorization=authorization,
        x_api_key=x_api_key,
    )
    REPOSITORY.set_status("RUNNING", "")
    REPOSITORY.audit(
        "GLOBAL_EMERGENCY_STOP_IGNORED",
        actor,
        request.client.host if request.client else "unknown",
        {"reason": "Emergency stop is account-scoped"},
    )
    return filter_summary_to_trading_ready_accounts(REPOSITORY.summary())


@app.get("/settings/accounts")
def settings_accounts() -> dict:
    accounts = []
    for row in REPOSITORY.list_managed_accounts():
        auth_type = "pat"
        account_id_masked = ""
        try:
            payload = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
            auth_type = str(payload.get("auth_type", "pat")).strip() or "pat"
            token_ready = has_trading_api_token(payload)
            account_id = str(payload.get("account_id", "")).strip()
            if len(account_id) > 6:
                account_id_masked = f"{account_id[:3]}***{account_id[-3:]}"
        except Exception:
            token_ready = False
        accounts.append(
            {
                "id": row.id,
                "label": row.label or f"Account {row.id}",
                "enabled": row.enabled,
                "token_masked": "Stored securely",
                "auth_type": auth_type,
                "has_trading_api_token": token_ready,
                "can_receive_trades": bool(row.enabled and token_ready),
                "account_id_masked": account_id_masked,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
        )
    return {
        "mode": REPOSITORY.runtime_mode(),
        "accounts": accounts,
        "token_storage_secure": has_encryption_key(CONFIG.deriv.token_encryption_key),
        "oauth_client_id": oauth_client_id(),
        "oauth_redirect_url": oauth_redirect_url(),
        "read_only_dashboard": False,
    }


@app.post("/settings/mode")
def update_mode(
    payload: ModeUpdateRequest,
    request: Request,
    actor: str = Depends(require_control_auth),
) -> dict:
    enforce_control_rate_limit(request)
    running_status, _ = REPOSITORY.control_state()
    if running_status == "RUNNING":
        raise HTTPException(
            status_code=409,
            detail="Stop the bot before switching trading mode.",
        )
    mode = REPOSITORY.set_runtime_mode(payload.mode)
    REPOSITORY.audit(
        "RUNTIME_MODE_CHANGED",
        actor,
        request.client.host if request.client else "unknown",
        {"mode": mode},
    )
    return settings_accounts()


@app.post("/settings/accounts/import")
def import_accounts(
    payload: TokenImportRequest,
    request: Request,
    actor: str = Depends(require_control_auth),
) -> dict:
    enforce_control_rate_limit(request)
    running_status, _ = REPOSITORY.control_state()
    if running_status == "RUNNING":
        raise HTTPException(
            status_code=409,
            detail="Stop the bot before adding tokens for a clean next start.",
        )
    if not has_encryption_key(CONFIG.deriv.token_encryption_key):
        raise HTTPException(
            status_code=409,
            detail="Set DERIV_TOKEN_ENCRYPTION_KEY before storing dashboard-managed tokens.",
        )
    tokens = parse_token_lines(payload.tokens_text)
    if not tokens:
        raise HTTPException(status_code=400, detail="Add at least one token.")
    imported = []
    for index, token in enumerate(tokens, start=1):
        imported.append(
            REPOSITORY.add_managed_account(
                label=f"{payload.label_prefix.strip() or 'Account'} {index}",
                token_secret=encrypt_token(token, CONFIG.deriv.token_encryption_key),
            )
        )
    REPOSITORY.audit(
        "MANAGED_ACCOUNTS_IMPORTED",
        actor,
        request.client.host if request.client else "unknown",
        {"count": len(imported)},
    )
    return {
        **settings_accounts(),
        "imported_count": len(imported),
    }


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket) -> None:
    await BROADCASTER.connect(ws)
    try:
        await ws.send_json({
            "type": "snapshot",
            "data": filter_summary_to_trading_ready_accounts(REPOSITORY.summary()),
        })
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await BROADCASTER.disconnect(ws)


@app.on_event("startup")
async def _start_broadcaster_loop() -> None:
    async def loop() -> None:
        interval = max(2, float(os.getenv("WS_BROADCAST_INTERVAL_SECONDS", "3")))
        while True:
            try:
                summary = filter_summary_to_trading_ready_accounts(REPOSITORY.summary())
                await BROADCASTER.broadcast({"type": "snapshot", "data": summary})
            except Exception:
                pass
            await asyncio.sleep(interval)

    asyncio.create_task(loop())
