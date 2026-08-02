from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import patch

from guardian.config import GuardianConfig
from guardian.patcher import GuardianPatcher


class GuardianGitOnlyBoundaryTests(unittest.TestCase):
    def test_environment_cannot_enable_vps_deployment(self) -> None:
        with patch.dict(os.environ, {"GUARDIAN_AUTO_DEPLOY": "true"}, clear=False):
            config = GuardianConfig.from_env()
        self.assertFalse(config.auto_deploy)

    def test_patcher_contains_no_vps_deployment_execution(self) -> None:
        source = inspect.getsource(GuardianPatcher)
        self.assertNotIn("deploy_vps.sh", source)
        self.assertNotIn("DEPLOY_PREVIOUS_COMMIT", source)
        self.assertIn('"git", "push", "origin", "HEAD:main"', source)
        self.assertIn('"vps_update": "not_performed"', source)


if __name__ == "__main__":
    unittest.main()
