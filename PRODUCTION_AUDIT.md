# Production Architecture and Deployment Audit

Audit date: 2026-07-31

This document describes the authoritative runtime assembled by `app.api_v3:app` and `app.worker`. Older comments or README sections describing REST bulk execution, reserve-balance admission, or demo-only dashboard WebSockets are not the production behavior.

## 1. Browser authentication and account linking

1. The browser starts at `/oauth/start`.
2. The API creates a cryptographically random OAuth state and PKCE verifier.
3. The state hash, encrypted verifier and exact redirect URI are persisted in PostgreSQL and also bound to secure browser cookies.
4. The browser is redirected to `https://auth.deriv.com/oauth2/auth` with `response_type=code`, PKCE `S256`, and least-privilege scopes `trade application_read`.
5. `/oauth/callback` accepts the authorization code only when all of the following match:
   - browser state cookie;
   - submitted state;
   - unexpired one-use database state;
   - browser PKCE verifier cookie;
   - encrypted database verifier;
   - exact configured HTTPS redirect URI.
6. The API exchanges the code server-side and requests the user's Options accounts with the registered Deriv App ID.
7. Login identity and account metadata are encrypted before storage. Raw access tokens and PATs are never returned to the dashboard.
8. OAuth establishes identity and account discovery. AutoTrade additionally requires a verified Personal Access Token with `trade` scope for that selected account.

## 2. Worker account loading

1. `app.worker` loads enabled managed accounts from PostgreSQL.
2. Encrypted credentials are decrypted only inside the worker.
3. Duplicate accounts, invalid credentials and disabled accounts are isolated per account; one bad account does not stop other traders.
4. Account balance and account ownership are validated using the current Options accounts endpoint.
5. Admission requires only the user's selected stake. There is no recovery reserve or safety-reserve requirement.
6. When a later requested stake cannot be funded, the purchase is sent to Deriv and the provider's insufficient-funds response pauses only that account while preserving recovery state.

## 3. Market data, proposal and purchase path

1. Public market data uses `wss://api.derivws.com/trading/v1/options/ws/public`.
2. The worker subscribes to the configured markets, maintains tick history and runs the strategy gates.
3. A qualifying signal is persisted before execution.
4. A public `proposal` request validates current contract economics using `underlying_symbol`, duration, barrier, currency and stake.
5. Every production account obtains a short-lived authenticated WebSocket URL from `POST /trading/v1/options/accounts/{accountId}/otp` using `Deriv-App-ID` and the account's Bearer token.
6. OTP requests are concurrency-limited to reduce provider rate limiting. Private WebSockets send a keepalive every 30 seconds and automatically reconnect.
7. Production purchases are private WebSocket direct buys. The request uses `buy: "1"`, `price`, and direct contract `parameters`; no removed `loginid` field is sent.
8. Account purchases are concurrent but isolated. A partial failure does not cancel successful contracts for other accounts.
9. A successful provider contract is immediately persisted with account, signal, economics, timing and transport metadata.

## 4. Contract monitoring and settlement

1. Each purchased contract is subscribed through `proposal_open_contract` on its authenticated account WebSocket.
2. The worker also polls unresolved contracts and can open a fresh OTP connection for reconciliation if the original stream is interrupted.
3. Stale account contracts are isolated without globally locking healthy accounts.
4. Terminal updates are processed idempotently; duplicate settlement messages cannot double-count a trade.
5. Provider numeric fields are normalized, and settlement uses current fields including `exit_spot` when available.
6. Trade outcome, profit, buy price, payout, markup, commission, entry/exit values and provider timestamps are committed to PostgreSQL.
7. Recovery and virtual-protection state are updated per managed account.
8. The private balance is refreshed after settlement.

## 5. Database-to-dashboard delivery

1. The worker calls the internal API settlement-refresh endpoint after the committed settlement.
2. That notification retries up to three times.
3. The worker publishes again after the final balance reconciliation so personal balance and trade data cannot remain one settlement behind.
4. The API marks the relevant dashboard caches dirty and rebuilds verified demo and real snapshots.
5. A snapshot is published only when the dashboard consistency invariant passes, including `total_trades = wins + losses`.
6. Snapshots are persisted with a monotonically increasing version and source watermark.
7. WebSocket clients subscribe with `/ws/dashboard?mode=demo` or `/ws/dashboard?mode=real` and receive only that account mode.
8. Settlement events trigger immediate WebSocket publication. A 20-second publication heartbeat protects against missed events.
9. The browser rejects mismatched-mode snapshots, keeps separate last-good snapshots for demo and real, reconnects when account mode changes, and refreshes `/me` plus recent contracts after each live settlement update.
10. A 30-second REST refresh remains as a fallback if WebSocket delivery is unavailable.

## 6. Deployment and runtime security

- PostgreSQL is started and health-checked before migrations.
- Alembic runs once before the API and worker are replaced.
- API and worker images run as a non-root user.
- The API binds to `127.0.0.1:8080`; Caddy is the public HTTPS boundary.
- Trusted-host, CORS, mutation-origin, secure-cookie, request-size, rate-limit and security-header controls remain active.
- API documentation routes are disabled in production.
- Secrets are supplied only through `.env`; `.env` is ignored by Git.
- Real-money execution requires `TRADING_MODE=real`, `ALLOW_REAL_TRADING=true`, `execution.real_enabled=true`, and the exact production acknowledgement. The default deployment remains demo-safe.

## 7. Automatic release gates

`scripts/deploy_vps.sh` now refuses to pass deployment unless all of these succeed:

1. Docker Compose validation.
2. Python compilation for `app` and `scripts`.
3. Dashboard JavaScript syntax validation when Node is installed on the host.
4. API and worker image builds.
5. PostgreSQL health and Docker DNS resolution.
6. Alembic migrations.
7. API liveness.
8. Worker process stability and heartbeat readiness.
9. Token-encryption and control-key checks.
10. Exact OAuth redirect, PKCE, state and scope checks.
11. Demo and real dashboard invariants.
12. Demo and real dashboard WebSocket snapshots.
13. Public Deriv WebSocket ping.
14. Dashboard hardening-script injection.
15. Fatal traceback and integration-log scan.

The deployment script preserves named volumes and never runs `docker compose down`.

## 8. Required VPS preparation

Copy `.env.vps.example` to `.env`, replace every placeholder, and keep real trading disabled for the first deployment. Generate independent secrets, for example:

```bash
python3 - <<'PY'
from cryptography.fernet import Fernet
import secrets
print('DERIV_TOKEN_ENCRYPTION_KEY=' + Fernet.generate_key().decode())
print('CONTROL_API_KEY=' + secrets.token_urlsafe(48))
print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(36))
PY
```

The registered Deriv redirect URI must be exactly:

```text
https://derivadmin.site/oauth/callback
```

## 9. Expected execution evidence

A healthy signal-to-settlement cycle produces logs similar to:

```text
SIGNAL_CREATED
PROPOSAL_REQUESTED
MODEL_DECISION ... action=PURCHASE
PURCHASE_REQUESTED
WEBSOCKET_ONLY_EXECUTION
PRIVATE_PURCHASE_REQUEST
PRIVATE_PURCHASE_RESPONSE
PURCHASE_CONFIRMED
CONTRACT_REGISTERED
CONTRACT_ECONOMICS
ACCOUNT_CONTRACT_SETTLED
```

Dashboard delivery problems surface explicitly as `DASHBOARD_SETTLEMENT_PUSH_FAILED` or `MODE_AWARE_DASHBOARD_BROADCAST_FAILED`; they are no longer silent.
