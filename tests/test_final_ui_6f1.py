from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FinalUi6F1HistoricalTests(unittest.TestCase):
    def test_6f1_source_is_retained_but_not_loaded_or_shipped(self) -> None:
        html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertTrue((ROOT / "dashboard" / "final-ui-shell-v1.js").exists())
        self.assertTrue((ROOT / "dashboard" / "final-ui-shell-v1.css").exists())
        self.assertIn('frontend-runtime" content="direct-vps-final-ui-6f2"', html)
        self.assertIn('ui_authority: "final-ui-shell-v2"', build)
        self.assertNotIn('/final-ui-shell-v1.js', html)
        self.assertNotIn('/final-ui-shell-v1.css', html)
        self.assertNotIn('"final-ui-shell-v1.js",', build)
        self.assertNotIn('"final-ui-shell-v1.css",', build)

    def test_direct_vps_and_database_safety_contract_survives_6f2(self) -> None:
        caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        compose_vps = (ROOT / "docker-compose.vps.yml").read_text(encoding="utf-8")
        deploy = (ROOT / "scripts" / "deploy_full_vps.sh").read_text(encoding="utf-8")
        package = (ROOT / "package.json").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")

        self.assertIn("handle_path /api/*", caddy)
        self.assertIn("handle /oauth/*", caddy)
        self.assertIn("handle /ws/*", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:8081", caddy)
        self.assertIn("test2_database:/var/lib/postgresql/data", compose)
        self.assertIn('"127.0.0.1:8081:80"', compose_vps)
        self.assertNotIn("docker volume prune", deploy)
        self.assertNotIn("docker system prune --volumes", deploy)
        self.assertNotIn("docker compose down -v", deploy)
        self.assertNotIn("build-netlify.mjs", package)
        self.assertNotIn("build-netlify.mjs", dockerfile)
        self.assertIn('"build": "node scripts/build-vps.mjs"', package)
        self.assertIn("RUN node scripts/build-vps.mjs", dockerfile)


if __name__ == "__main__":
    unittest.main()
