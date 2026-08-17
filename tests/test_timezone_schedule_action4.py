from __future__ import annotations

from pathlib import Path
import unittest

from app.automation_preferences_api import (
    DEFAULT_TIMEZONE,
    _preference_key,
    _timezone_meta,
    _validated_timezone,
)


ROOT = Path(__file__).resolve().parents[1]


class TimezoneScheduleAction4Tests(unittest.TestCase):
    def test_nairobi_is_the_default_and_is_eat(self) -> None:
        self.assertEqual(DEFAULT_TIMEZONE, "Africa/Nairobi")
        self.assertEqual(_validated_timezone(DEFAULT_TIMEZONE), DEFAULT_TIMEZONE)
        meta = _timezone_meta(DEFAULT_TIMEZONE)
        self.assertEqual(meta["timezone"], DEFAULT_TIMEZONE)
        self.assertEqual(meta["abbreviation"], "EAT")
        self.assertEqual(meta["utc_offset"], "UTC+03:00")

    def test_timezone_preference_key_is_stable_and_bounded(self) -> None:
        account_id = "CR1234567"
        first = _preference_key(account_id)
        second = _preference_key(account_id.lower())
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("automation_timezone:"))
        self.assertLessEqual(len(first), 80)

    def test_backend_preferences_are_stable_across_linked_options_accounts(self) -> None:
        source = (ROOT / "app" / "automation_preferences_api.py").read_text(encoding="utf-8")
        entry = (ROOT / "app" / "vps_backend_api.py").read_text(encoding="utf-8")
        self.assertIn("base_api.login_identity_from_payload(payload)", source)
        self.assertIn('scope": "linked_options_accounts"', source)
        self.assertIn("OAuth tokens can rotate at a later login", source)
        self.assertIn("for account_id in account_ids", source)
        self.assertIn('@app.get("/me/automation-preferences")', source)
        self.assertIn('@app.post("/me/automation-preferences/timezone")', source)
        self.assertIn("ZoneInfo(name)", source)
        self.assertIn("install_automation_preferences_api(app)", entry)

    def test_timezone_onboarding_is_mobile_first_and_globally_changeable(self) -> None:
        js = (ROOT / "dashboard" / "timezone-schedule-v1.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "timezone-schedule-v1.css").read_text(encoding="utf-8")
        self.assertIn("Set your timezone", js)
        self.assertIn("Africa/Nairobi", js)
        self.assertIn("Use Nairobi default", js)
        self.assertIn("global scheduling timezone", js)
        self.assertIn("linked DOT and ROT accounts", js)
        self.assertIn('Intl.supportedValuesOf("timeZone")', js)
        self.assertIn("/me/automation-preferences/timezone", js)
        self.assertIn(".foa-timezone-onboarding", css)
        self.assertIn("@media (max-width: 430px)", css)

    def test_schedule_workspace_matches_action_four_contract(self) -> None:
        js = (ROOT / "dashboard" / "timezone-schedule-v1.js").read_text(encoding="utf-8")
        css = (ROOT / "dashboard" / "timezone-schedule-v1.css").read_text(encoding="utf-8")
        for text in (
            "Schedule Trading",
            "Automate a future trading session",
            "Built-in",
            "My Strategies",
            "AI Generated",
            "Date",
            "Time",
            "Timezone",
            "Stake",
            "Take Profit",
            "Stop Loss",
            "Wait until previous session finishes",
            "Skip this scheduled session",
            "Stop previous and start this one",
            "SESSION PREVIEW",
            "Schedule Session",
            "Trade Now Instead",
            "Upcoming Sessions",
        ):
            self.assertIn(text, js)
        self.assertIn('status:"prepared_for_scheduler"', js)
        self.assertIn("persistent VPS scheduler is activated in Action 5", js)
        self.assertNotIn("/me/resume-trading", js)
        self.assertNotIn("/me/auto-trade", js)
        self.assertIn('data-automation-scaffold="schedule" hidden', js)
        self.assertIn(".foa-schedule-preview", css)
        self.assertIn(".foa-schedule-overlap", css)

    def test_home_timezone_tracks_the_global_preference(self) -> None:
        sync = (ROOT / "dashboard" / "timezone-home-sync-v1.js").read_text(encoding="utf-8")
        self.assertIn("FOA_AUTOMATION_TIMEZONE", sync)
        self.assertIn("foa:timezone-changed", sync)
        self.assertIn("foa-user-timezone-v1", sync)
        self.assertIn("foa-staged-schedules-action4-v1", sync)

    def test_full_vps_build_installs_action_four_without_scheduler_execution(self) -> None:
        build = (ROOT / "scripts" / "build-vps.mjs").read_text(encoding="utf-8")
        self.assertIn("/timezone-schedule-v1.css?v=20260817-1", build)
        self.assertIn("/timezone-schedule-v1.js?v=20260817-2", build)
        self.assertIn("/timezone-home-sync-v1.js?v=20260817-1", build)
        self.assertIn('authenticated_ui: "automation-home-action4-v1"', build)
        self.assertIn('automation_timezone: "linked-options-global-africa-nairobi-default-v1"', build)
        self.assertIn('schedule_execution: "deferred-to-action5"', build)
        self.assertIn("Africa/Nairobi (EAT) default", build)


if __name__ == "__main__":
    unittest.main()
