# Legacy Model — RF-PUT5 AI Trading Bot

## Project Overview

Python/FastAPI Deriv trading application (Father of Automation Series). Normally deployed on a VPS via Docker with three services: PostgreSQL, a FastAPI dashboard/API, and a continuously-running RF-DIR5 trading worker.

**This Replit is used as a code editing and debugging environment.** The app is not run here — it deploys to a VPS via Docker at `derivadmin.site`.

### Stack
- **Language:** Python 3.12
- **API:** FastAPI + Uvicorn (port 8080 in Docker)
- **Database:** PostgreSQL 17 via SQLAlchemy + Alembic migrations
- **Worker:** `python -m app.worker` → `RFDir5TradingBot`
- **Proxy:** Caddy (HTTPS, `derivadmin.site`)
- **Deployment:** Docker Compose (`docker-compose.yml` + `docker-compose.vps.yml`)

### Key files
| Path | Purpose |
|------|---------|
| `app/worker.py` | Worker entry point |
| `app/rf_dir5_bot.py` | Core RF strategy logic |
| `app/repositories/rf_dir5_repository.py` | RF signals, risk, stake planning |
| `app/repositories/test2_repository.py` | Persistence, summaries, canonical ledger |
| `app/api.py` | FastAPI dashboard + WebSocket endpoints |
| `app/models.py` | SQLAlchemy schema |
| `config.yaml` | Checked-in defaults (env vars override) |
| `dashboard/index.html` | Dashboard UI |
| `migrations/versions/` | Alembic history |
| `docker-compose.yml` | Main Docker stack |
| `scripts/deploy_vps.sh` | VPS build/migrate/deploy script |

## User Preferences
- Use Replit for editing and debugging only; do not set up a run workflow.
- Push fixes directly to `main` on `https://github.com/DukeNyamasege/legacy-model`.
