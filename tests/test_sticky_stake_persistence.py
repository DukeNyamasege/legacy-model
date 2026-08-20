from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "scripts" / "finalize-sticky-stake-v1.mjs"
PERSISTENCE = ROOT / "dashboard" / "direct-strategy-persistence-v1.js"
SHELL = ROOT / "dashboard" / "final-ui-shell-v2.js"
DOCKERFILE = ROOT / "Dockerfile.frontend"


class StickyStakePersistenceTests(unittest.TestCase):
    def test_builder_stake_is_a_real_builder_field(self) -> None:
        shell = SHELL.read_text(encoding="utf-8")
        self.assertIn('data-builder="money.stake"', shell)
        self.assertIn('"money.stake"', shell)
        self.assertIn("builderDraftFromDom", shell)

    def test_finalizer_makes_explicit_stake_canonical_until_changed(self) -> None:
        source = FINALIZER.read_text(encoding="utf-8")
        for marker in (
            'STICKY_STAKE_KEY = "derivadmin-sticky-stake-v1"',
            "syncExplicitStakeFromDom",
            "enforceStickyStake",
            "rememberStickyStake",
            "applyStake(state.selectedStrategy, stake)",
            "template/strategy click may replace selectedStrategy",
            "20260820-sticky-stake-v1",
        ):
            self.assertIn(marker, source)

    def test_existing_persistence_still_saves_builder_draft(self) -> None:
        source = PERSISTENCE.read_text(encoding="utf-8")
        self.assertIn('const BUILDER_DRAFT_KEY = "derivadmin-builder-draft-v2"', source)
        self.assertIn("persistBuilderState", source)
        self.assertIn("selectedStrategy", source)
        self.assertIn("/api/me/custom-strategy", source)

    def test_frontend_build_runs_sticky_stake_finalizer_last(self) -> None:
        docker = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("COPY scripts/finalize-sticky-stake-v1.mjs", docker)
        self.assertIn("node --check scripts/finalize-sticky-stake-v1.mjs", docker)
        self.assertIn("node scripts/finalize-sticky-stake-v1.mjs", docker)
        safety = docker.index("node scripts/finalize-runtime-safety-v2.mjs")
        sticky = docker.index("node scripts/finalize-sticky-stake-v1.mjs")
        self.assertLess(safety, sticky)


if __name__ == "__main__":
    unittest.main()
