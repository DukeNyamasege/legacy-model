from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
import requests
from dotenv import load_dotenv

from app.config import load_test2_config
from app.database import Database
from app.oauth_client import build_authorization_url, exchange_code_for_tokens
from app.repositories.test2_repository import Test2Repository
from app.token_store import (
    decrypt_auth_payload,
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
OAUTH_STATE_COOKIE = "deriv_oauth_state"
OAUTH_VERIFIER_COOKIE = "deriv_oauth_code_verifier"


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
        return forwarded == "https"
    return request.url.scheme == "https"


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
    allow_credentials=False,
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
def dashboard() -> FileResponse:
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
    state = os.urandom(16).hex()
    authorization_url, code_verifier = build_authorization_url(
        client_id=oauth_client_id(),
        redirect_uri=oauth_redirect_url(),
        state=state,
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
) -> HTMLResponse:
    if error:
        raise HTTPException(
            status_code=400,
            detail=error_description or error,
        )
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE, "")
    code_verifier = request.cookies.get(OAUTH_VERIFIER_COOKIE, "")
    if not code or not expected_state or not code_verifier:
        raise HTTPException(status_code=400, detail="OAuth session is incomplete or expired")
    if state != expected_state:
        raise HTTPException(status_code=400, detail="OAuth state validation failed")

    try:
        token_payload = exchange_code_for_tokens(
            client_id=oauth_client_id(),
            redirect_uri=oauth_redirect_url(),
            code=code,
            code_verifier=code_verifier,
        )
        accounts = load_options_accounts(token_payload["access_token"])
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=400, detail=f"OAuth token exchange failed: {detail}") from exc

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
    for row in REPOSITORY.list_managed_accounts():
        try:
            stored = decrypt_auth_payload(row.token_secret, CONFIG.deriv.token_encryption_key)
        except Exception:
            continue
        if str(stored.get("account_id", "")).strip() == account_id:
            existing_id = int(row.id)
            break
    if existing_id is None:
        REPOSITORY.add_managed_account(label=label, token_secret=token_secret)
    else:
        REPOSITORY.update_managed_account(existing_id, label=label, token_secret=token_secret)

    REPOSITORY.audit(
        "OAUTH_ACCOUNT_LINKED",
        "oauth-callback",
        request.client.host if request.client else "unknown",
        {"account_id_masked": label, "mode": runtime_mode_value},
    )

    response = HTMLResponse(
        "<html><body><h2>Deriv account linked</h2>"
        "<p>Your OAuth account has been stored for the bot.</p>"
        "<p>You can close this tab and return to your dashboard.</p></body></html>"
    )
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.delete_cookie(OAUTH_VERIFIER_COOKIE)
    return response


@app.get("/metrics/summary")
def metrics_summary() -> dict:
    return REPOSITORY.summary()


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
