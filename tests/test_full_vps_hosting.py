from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FullVpsHostingTests(unittest.TestCase):
    def test_compose_keeps_public_containers_loopback_only(self) -> None:
        source = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:8081:80"', source)
        self.assertIn("FRONTEND_HOSTING_MODE: vps", source)
        self.assertIn("PUBLIC_ORIGIN", source)
        self.assertIn("app.vps_backend_api:app", source)
        self.assertNotIn('"0.0.0.0:8081:80"', source)

        base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:8080:8080"', base)
        self.assertIn("test2_database:/var/lib/postgresql/data", base)

    def test_caddy_is_primary_same_origin_edge(self) -> None:
        source = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("derivadmin.site {", source)
        self.assertIn("handle_path /api/*", source)
        self.assertIn("handle /oauth/*", source)
        self.assertIn("handle /ws/*", source)
        self.assertIn("reverse_proxy 127.0.0.1:8080", source)
        self.assertIn("reverse_proxy 127.0.0.1:8081", source)
        self.assertIn("api.derivadmin.site {", source)

        installer = (ROOT / "scripts/install_full_vps_caddy.sh").read_text(encoding="utf-8")
        self.assertIn("caddy validate", installer)
        self.assertIn("systemctl reload caddy", installer)
        self.assertIn("Caddyfile.before_full_vps_", installer)

    def test_nginx_remains_a_supported_fallback_edge(self) -> None:
        https = (ROOT / "deploy/nginx/derivadmin.site.https.conf.template").read_text(encoding="utf-8")
        self.assertIn("location /api/", https)
        self.assertIn("proxy_pass http://127.0.0.1:8080/;", https)
        self.assertIn("location /oauth/", https)
        self.assertIn("location /ws/", https)
        self.assertIn('proxy_set_header Upgrade $http_upgrade;', https)
        self.assertIn('proxy_set_header Connection "upgrade";', https)
        self.assertIn("proxy_pass http://127.0.0.1:8081;", https)
        self.assertIn("ssl_certificate /etc/letsencrypt/live/__DOMAIN__/fullchain.pem;", https)

    def test_vps_build_reuses_proven_dashboard_but_removes_netlify_redirects(self) -> None:
        source = (ROOT / "scripts/build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn('await import(`./build-netlify.mjs?vps=${Date.now()}`);', source)
        self.assertIn('await rm(resolve(output, "_redirects"), { force: true });', source)
        self.assertIn("full-vps-same-origin-v1", source)
        self.assertIn('api_base: "/api"', source)
        self.assertIn('oauth_base: "/oauth"', source)

    def test_action1_automation_home_matches_approved_shell_and_preserves_trades(self) -> None:
        js = (ROOT / "dashboard" / "automation-home-v1.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "automation-home-v1.css").read_text(encoding="utf-8")
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")

        # Approved Action 1 information architecture remains present as later
        # actions extend the product around it.
        self.assertIn("Home of Automation", js)
        self.assertIn("Strategy Builder", js)
        self.assertIn("Text to Strategy", js)
        self.assertIn("Schedule Trading", js)
        self.assertIn("My Automation", js)
        self.assertIn("Strategy Library", js)
        self.assertIn("Built-in", js)
        self.assertIn("My Strategies", js)
        self.assertIn("AI Generated", js)
        self.assertIn('item("home", "home", "Home")', js)
        self.assertIn('item("builder", "cubes", "Builder")', js)
        self.assertIn('item("ai", "star", "AI")', js)
        self.assertIn('item("schedule", "calendar", "Schedule")', js)
        self.assertIn('item("profile", "profile", "Profile")', js)

        # Immediate execution remains owned by the existing data-main-action
        # controller; the new shell only routes the successful UX into Trades.
        self.assertIn("[data-main-action]", js)
        self.assertIn('navigate("trades")', js)
        self.assertIn('const desired = route === "trades" ? "trades" : "main";', js)
        self.assertNotIn("/me/resume-trading", js)
        self.assertNotIn("/me/stop-trading", js)

        # The exact mobile-first dark blue/cyan design language is a dedicated
        # authority and does not rewrite the worker or backend.
        self.assertIn("--automation-blue: #168cff", css)
        self.assertIn("--automation-cyan: #1fd2ff", css)
        self.assertIn(".foa-automation-features", css)
        self.assertIn(".foa-automation-bottom-nav", css)
        self.assertIn("@media (max-width: 720px)", css)

        # Full-VPS build keeps Action 1 assets while Action 2 advances the
        # aggregate authenticated UI version and installs its own compiler UI.
        self.assertIn("/automation-home-v1.css?v=20260817-1", build)
        self.assertIn("/automation-home-v1.js?v=20260817-1", build)
        self.assertIn('authenticated_ui: "automation-home-action2-v1"', build)
        self.assertIn('text_to_strategy: "nearest-supported-v1-250-words"', build)
        self.assertIn('public_landing: "mobile-automation-action2-v1"', build)
        self.assertIn("/text-to-strategy-v1.css?v=20260817-1", build)
        self.assertIn("/text-to-strategy-v1.js?v=20260817-1", build)

        syntax = subprocess.run(
            ["node", "--check", str(ROOT / "dashboard" / "automation-home-v1.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            syntax.returncode,
            0,
            msg=f"stdout:\n{syntax.stdout}\nstderr:\n{syntax.stderr}",
        )

    def test_full_deploy_preserves_database_and_builds_candidate_before_cutover(self) -> None:
        source = (ROOT / "scripts/deploy_full_vps.sh").read_text(encoding="utf-8")
        build = source.index("compose build frontend api worker")
        backup = source.index("DATABASE_BACKUP_CREATED")
        cutover = source.index("compose up -d --force-recreate api worker frontend")
        self.assertLess(build, cutover)
        self.assertLess(backup, cutover)
        self.assertIn("pg_dump --format=custom --no-owner --no-privileges", source)
        self.assertIn("alembic upgrade head", source)
        self.assertIn("command -v caddy", source)
        self.assertNotIn("docker compose down -v", source)

    def test_environment_example_is_same_origin_vps(self) -> None:
        env = (ROOT / ".env.vps.example").read_text(encoding="utf-8")
        self.assertIn("PUBLIC_ORIGIN=https://derivadmin.site", env)
        self.assertIn("FRONTEND_HOSTING_MODE=vps", env)
        self.assertIn("DERIV_OAUTH_REDIRECT_URL=https://derivadmin.site/oauth/callback", env)
        self.assertIn("TRUSTED_HOSTS=derivadmin.site,www.derivadmin.site", env)
        self.assertNotIn("https://your-site.netlify.app", env)

    def test_full_vps_uses_atomic_bounded_private_bootstrap(self) -> None:
        compose = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        self.assertIn("VPS_ACCOUNT_REFRESH_INTERVAL_SECONDS:-1", compose)
        self.assertIn("VPS_DERIV_HTTP_CONNECTOR_LIMIT:-24", compose)
        self.assertIn("VPS_DERIV_HTTP_LIMIT_PER_HOST:-12", compose)
        self.assertIn("VPS_DERIV_HTTP_CONCURRENCY:-12", compose)
        self.assertIn("VPS_PRIVATE_WS_CONNECT_INTERVAL_SECONDS:-0.15", compose)
        self.assertIn("VPS_PRIVATE_WS_HANDSHAKE_CONCURRENCY:-6", compose)
        self.assertIn("VPS_PRIVATE_WS_BOOTSTRAP_CONCURRENCY:-6", compose)
        self.assertIn("VPS_OTP_HTTP_CONCURRENCY:-8", compose)
        self.assertIn("VPS_PRIVATE_WS_OTP_FAILURE_BACKOFF_SECONDS:-1.5", compose)
        self.assertIn("VPS_PRIVATE_WS_TRANSIENT_BACKOFF_MAX_SECONDS:-12", compose)

        base = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("PRIVATE_WS_HANDSHAKE_CONCURRENCY", base)
        self.assertIn("PRIVATE_WS_RATE_LIMIT_BACKOFF_SECONDS", base)
        self.assertIn("PRIVATE_WS_MAX_BACKOFF_SECONDS", base)

    def test_low_latency_authority_matches_current_deriv_otp_contract(self) -> None:
        source = (ROOT / "app" / "vps_low_latency_runtime.py").read_text(
            encoding="utf-8"
        )
        worker = (ROOT / "app" / "custom_strategy_worker.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("happy_eyeballs_delay=0.25", source)
        self.assertIn("interleave=1", source)
        self.assertIn("_low_latency_normal_backoff", source)
        self.assertIn("VPS_PRIVATE_WS_TRANSIENT_BACKOFF_MAX_SECONDS", source)
        self.assertIn("provider_rate_limit_backoff=preserved", source)
        self.assertNotIn("private_ws._rate_backoff =", source)
        self.assertIn("_low_latency_fast_runtime_accounts", source)
        self.assertIn('current_status in {"starting", "validating"}', source)
        self.assertIn("market_selection_deduplicated=true", source)
        self.assertIn("_low_latency_contract_snapshot_once", source)
        self.assertIn("CONTRACT_SNAPSHOT_RESPONSE_TIMEOUT_SECONDS = 5.0", source)
        self.assertIn('response.get("errors")', source)
        self.assertIn("HTTP_500", source)
        self.assertIn("PRIVATE_WS_OTP_PROVIDER_ERROR", source)
        self.assertIn("class _VpsBootstrapScheduler", source)
        self.assertIn("otp_and_wss_atomic=true", source)
        self.assertIn("atomic_otp_wss=true", source)
        self.assertIn("urgent_priority=true", source)
        self.assertIn("ClientSession.connect_and_run = _vps_connect_and_run", source)
        self.assertIn("ClientSession.get_otp_url = _vps_get_otp_url", source)

        stampede = worker.index("install_custom_strategy_connection_stampede_guard()")
        low_latency = worker.index("install_vps_low_latency_runtime()")
        bot = worker.index("bot = RFDir5TradingBot()")
        self.assertLess(stampede, low_latency)
        self.assertLess(low_latency, bot)

    def test_clean_reset_preserves_history_and_invalidates_sessions(self) -> None:
        reset = (ROOT / "scripts" / "reset_all_user_sessions.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("delete(ClientSession)", reset)
        self.assertIn("delete(OAuthLoginState)", reset)
        self.assertIn("_hard_stop(", reset)
        self.assertIn("mark_history_reset=False", reset)
        self.assertIn("trade_history_preserved=true", reset)
        self.assertNotIn("delete(Trade)", reset)
        self.assertNotIn("delete(ManagedAccount)", reset)

    def test_vps_login_and_autotrade_telegram_observability_is_final(self) -> None:
        source = (ROOT / "app" / "vps_session_observability.py").read_text(
            encoding="utf-8"
        )
        entry = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")

        self.assertIn("DERIV USER LOGGED IN", source)
        self.assertIn("DEMO/DOT", source)
        self.assertIn("REAL/ROT", source)
        self.assertIn("AUTO TRADE STARTED", source)
        self.assertIn("/oauth/callback", source)
        self.assertIn("/me/auto-trade", source)
        self.assertIn("/me/resume-trading", source)
        self.assertIn("FRESH_USER_LOGIN_SESSION_CREATED", source)
        self.assertIn("install_vps_session_observability(app)", entry)
        self.assertIn("VPS_TELEGRAM_NOTIFICATIONS_SUSPENDED:-false", compose)

    def test_vps_telegram_inbox_autodiscovery_and_one_update_delivery(self) -> None:
        source = (ROOT / "app" / "vps_telegram_control.py").read_text(
            encoding="utf-8"
        )
        entry = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        config = (ROOT / "config.yaml").read_text(encoding="utf-8")
        env = (ROOT / ".env.vps.example").read_text(encoding="utf-8")

        self.assertIn("install_vps_telegram_control(app)", entry)
        self.assertIn("class VpsTelegramController", source)
        self.assertIn('"/update message', source)
        self.assertIn("SUBSCRIBER_PREFIX", source)
        self.assertIn("ADMIN_CHAT_KEY", source)
        self.assertIn("getUpdates", source)
        self.assertIn("Direct Model Updater alerts enabled", source)
        self.assertIn("force_mention_all=false", source)
        self.assertIn("await asyncio.sleep(0.05)", source)
        self.assertIn("test2_models:/app/model_artifacts", compose)
        self.assertIn("enabled: true", config[config.index("telegram:") :])
        self.assertIn("does NOT require a numeric chat ID", env)
        self.assertIn("TELEGRAM_BOT_USERNAME=modellegacyupdaterbot", env)
        self.assertNotIn("add_event_handler(", source)
        self.assertNotIn("on_event(", source)
        self.assertIn("app.router.lifespan_context", source)
        self.assertIn("@asynccontextmanager", source)
        self.assertIn("base_api.CONFIG,", source)

    def test_vps_production_api_imports_with_current_fastapi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env.update(
                {
                    "DATABASE_URL": f"sqlite:///{Path(temporary) / 'vps-import.db'}",
                    "DERIV_TOKEN_ENCRYPTION_KEY": "",
                    "TELEGRAM_BOT_TOKEN": "",
                    "TELEGRAM_NOTIFICATIONS_SUSPENDED": "true",
                    "VPS_TELEGRAM_NOTIFICATIONS_SUSPENDED": "true",
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import app.vps_backend_api as module; "
                        "assert module.app.state.vps_telegram_control_installed is True; "
                        "print('VPS_BACKEND_IMPORT_OK')"
                    ),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("VPS_BACKEND_IMPORT_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
