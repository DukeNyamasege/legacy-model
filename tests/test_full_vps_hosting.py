from __future__ import annotations

from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
