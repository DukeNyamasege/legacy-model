from __future__ import annotations

import unittest

from app.custom_martingale import (
    CUSTOM_MODE,
    FLAT_MODE,
    SYSTEM_MODE,
    custom_martingale_stake,
    normalize_martingale_settings,
)
from app.oauth_session_proof import validate_oauth_callback_proof


class CustomMartingaleTests(unittest.TestCase):
    def test_system_is_default_for_existing_enabled_accounts(self) -> None:
        settings = normalize_martingale_settings({}, legacy_enabled=True, base_stake=0.50)
        self.assertEqual(settings["mode"], SYSTEM_MODE)
        self.assertEqual(settings["policy"], "system_exact_debt_recovery")
        self.assertTrue(settings["martingale_enabled"])

    def test_flat_is_default_for_existing_disabled_martingale_accounts(self) -> None:
        settings = normalize_martingale_settings({}, legacy_enabled=False, base_stake=0.50)
        self.assertEqual(settings["mode"], FLAT_MODE)
        self.assertFalse(settings["martingale_enabled"])

    def test_custom_starts_only_at_selected_loss_trigger(self) -> None:
        before, before_level, _ = custom_martingale_stake(
            base_stake=0.50,
            consecutive_losses=1,
            trigger_losses=2,
            multiplier=2.0,
            max_levels=6,
            max_stake=1000.0,
        )
        active, active_level, _ = custom_martingale_stake(
            base_stake=0.50,
            consecutive_losses=2,
            trigger_losses=2,
            multiplier=2.0,
            max_levels=6,
            max_stake=1000.0,
        )
        self.assertEqual((before, before_level), (0.50, 0))
        self.assertEqual((active, active_level), (1.00, 1))

    def test_custom_advances_one_level_for_each_additional_actual_loss(self) -> None:
        stake, level, capped = custom_martingale_stake(
            base_stake=0.50,
            consecutive_losses=3,
            trigger_losses=1,
            multiplier=2.1,
            max_levels=6,
            max_stake=1000.0,
        )
        self.assertEqual(level, 3)
        self.assertEqual(stake, 4.64)
        self.assertFalse(capped)

    def test_user_maximum_stake_caps_custom_sequence(self) -> None:
        stake, level, capped = custom_martingale_stake(
            base_stake=1.00,
            consecutive_losses=5,
            trigger_losses=1,
            multiplier=3.0,
            max_levels=10,
            max_stake=20.0,
        )
        self.assertEqual(level, 5)
        self.assertEqual(stake, 20.0)
        self.assertTrue(capped)

    def test_custom_values_are_normalized_to_supported_ranges(self) -> None:
        settings = normalize_martingale_settings(
            {
                "mode": CUSTOM_MODE,
                "trigger_losses": 99,
                "multiplier": 99,
                "max_levels": 99,
                "max_stake": 0.01,
            },
            base_stake=0.75,
        )
        self.assertEqual(settings["mode"], CUSTOM_MODE)
        self.assertEqual(settings["trigger_losses"], 10)
        self.assertEqual(settings["multiplier"], 10.0)
        self.assertEqual(settings["max_levels"], 10)
        self.assertEqual(settings["max_stake"], 0.75)


class OAuthSessionProofTests(unittest.TestCase):
    def test_normal_browser_cookie_pkce_proof_is_accepted(self) -> None:
        valid, source = validate_oauth_callback_proof(
            returned_state="state-1",
            cookie_state="state-1",
            cookie_verifier="verifier-1",
            stored_verifier="verifier-1",
            state_verifier="",
        )
        self.assertTrue(valid)
        self.assertEqual(source, "browser_cookie")

    def test_server_state_pkce_recovers_missing_browser_cookies(self) -> None:
        valid, source = validate_oauth_callback_proof(
            returned_state="state-1",
            cookie_state="",
            cookie_verifier="",
            stored_verifier="verifier-1",
            state_verifier="verifier-1",
        )
        self.assertTrue(valid)
        self.assertEqual(source, "server_state")

    def test_invalid_cookie_and_state_proofs_are_rejected(self) -> None:
        valid, source = validate_oauth_callback_proof(
            returned_state="state-1",
            cookie_state="wrong-state",
            cookie_verifier="wrong-verifier",
            stored_verifier="verifier-1",
            state_verifier="wrong-verifier",
        )
        self.assertFalse(valid)
        self.assertEqual(source, "invalid")


if __name__ == "__main__":
    unittest.main()
