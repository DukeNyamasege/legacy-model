from __future__ import annotations

import asyncio
import hashlib
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
from app.database import Database
from app.oauth_client import (
    build_authorization_url,
    build_pkce_pair,
    exchange_code_for_tokens,
    refresh_access_token,
    token_is_expiring,
)
from app.repositories.test2_repository import Test2Repository, mask_account_id
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

    for token in global_runtime_tokens():
        refresh_from_token(token)

    for row in REPOSITORY.list_managed_accounts():
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
        token = str(payload.get("access_token", "")).strip()
        if token:
            refresh_from_token(
                token,
                preferred_account_id=str(payload.get("account_id", "")).strip(),
            )

    GLOBAL_ACCOUNT_REFRESH["last"] = now
    GLOBAL_ACCOUNT_REFRESH["accounts"] = updated_accounts
    return updated_accounts

app = FastAPI(
    title="Underdog Legacy Model",
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
    token_payload["account_id"] = account_id
    token_payload["auth_source"] = "deriv_oauth"
    token_secret = encrypt_auth_payload(token_payload, CONFIG.deriv.token_encryption_key)

    existing_id = None
    existing_enabled = None
    for row in REPOSITORY.list_managed_accounts():
        try:
            stored = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
        except Exception:
            continue
        if str(stored.get("account_id", "")).strip() == account_id:
            existing_id = int(row.id)
            existing_enabled = bool(row.enabled)
            break
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
        return {
            "id": account["id"],
            "account_id": account_id,
            "account_id_masked": mask_account_id(account_id),
            "label": account["label"],
            "enabled": account["enabled"],
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
                return {
                    "id": row.id,
                    "account_id": account_id,
                    "account_id_masked": mask_account_id(account_id),
                    "label": row.label,
                    "enabled": row.enabled,
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
    personal = REPOSITORY.account_summary(account["account_id"])
            
    return {
        "authenticated": True,
        "account_id": personal["account"],
        "label": account["label"],
        "enabled": account["enabled"],
        "balance": personal["balance"],
        "currency": personal["currency"],
        "status": personal["status"],
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
    
    REPOSITORY.set_managed_account_enabled(account["id"], body.enabled)
    return {"success": True, "enabled": body.enabled}

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
    return summary


@app.get("/metrics/recent-trades")
def recent_trades(limit: int = 50) -> dict:
    return {"trades": REPOSITORY.recent_trades(max(1, min(limit, 200)))}


@app.get("/metrics/recent-signals")
def recent_signals(limit: int = 50) -> dict:
    return {"signals": REPOSITORY.recent_signals(max(1, min(limit, 200)))}


@app.get("/metrics/model")
def model_metrics() -> dict:
    wins, losses = REPOSITORY.completed_outcomes()
    return {
        "bayesian": {
            "mode": CONFIG.bayesian.mode,
            "completed_trades": wins + losses,
            "minimum_completed_trades": CONFIG.bayesian.minimum_completed_trades,
            "ready": (wins + losses) >= CONFIG.bayesian.minimum_completed_trades,
        },
        "hmm": {
            "mode": CONFIG.hmm.mode,
            "observed_ticks": REPOSITORY.current_tick_sequence(),
            "minimum_training_ticks": CONFIG.hmm.minimum_training_ticks,
            "ready": REPOSITORY.current_tick_sequence()
            >= CONFIG.hmm.minimum_training_ticks,
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
    return REPOSITORY.summary()


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
    request: Request, actor: str = Depends(require_control_auth)
) -> dict:
    return apply_control(
        request=request,
        actor=actor,
        target_status="EMERGENCY_STOP",
        reason="ADMINISTRATIVE_EMERGENCY_STOP",
    )


@app.get("/settings/accounts")
def settings_accounts() -> dict:
    accounts = []
    for row in REPOSITORY.list_managed_accounts():
        auth_type = "pat"
        account_id_masked = ""
        try:
            payload = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
            auth_type = str(payload.get("auth_type", "pat")).strip() or "pat"
            account_id = str(payload.get("account_id", "")).strip()
            if len(account_id) > 6:
                account_id_masked = f"{account_id[:3]}***{account_id[-3:]}"
        except Exception:
            pass
        accounts.append(
            {
                "id": row.id,
                "label": row.label or f"Account {row.id}",
                "enabled": row.enabled,
                "token_masked": "Stored securely",
                "auth_type": auth_type,
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


@app.get("/ws/dashboard")
async def ws_dashboard(ws: WebSocket) -> None:
    await BROADCASTER.connect(ws)
    try:
        await ws.send_json({"type": "snapshot", "data": REPOSITORY.summary()})
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
                summary = REPOSITORY.summary()
                await BROADCASTER.broadcast({"type": "snapshot", "data": summary})
            except Exception:
                pass
            await asyncio.sleep(interval)

    asyncio.create_task(loop())
