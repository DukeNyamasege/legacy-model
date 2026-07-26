from __future__ import annotations

import asyncio
import time
import unittest
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from app.recovery import calculate_recovery_stake
from app.observed_performance import ObservedExecution, observed_martingale_cohort

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.config import TelegramSettings, load_test2_config
from app.database import Database
from app.deriv.http import deriv_headers
from app.model.bayesian_probability import BayesianGroupKey, KeyedBayesianProbability
from app.model.directional_regime_hmm import (
    DirectionalHmmInference,
    DirectionalRegimeHmm,
)
from app.models import AccountRiskState, ManagedAccount, ShadowContract, Trade, VirtualTrade
from app.repositories.rf_dir5_repository import RFDir5Repository, VIRTUAL_MODE
from app.repositories.test2_repository import Test2Repository
from app.rf_dir5_bot import RFDir5TradingBot
from app.services.telegram_alerts import TelegramAlertClient
from app.token_store import decrypt_auth_payload, encrypt_auth_payload
from app.strategy.decision_engine import (
    ProposalEconomics,
    RiseFallDecisionEngine,
    parse_proposal_economics,
)
from app.strategy.rise_fall_strategy import (
    RF_DIR5_VERSION,
    build_five_move_features,
    detect_fall_candidate,
    detect_rise_candidate,
    make_signal_event,
    shadow_outcome,
)
from enhanced_bot import TradingBot, sanitize_log_value
from scripts.reset_test_data import reset_database


def features(prices: list[str]):
    return build_five_move_features(
        prices,
        normalization_movements=[Decimal("0.10")] * 100,
    )


def signal(direction: str = "RISE", tick_sequence: int = 200):
    values = (
        ["100.00", "100.10", "100.20", "100.30", "100.25", "100.40"]
        if direction == "RISE"
        else ["100.40", "100.30", "100.20", "100.10", "100.15", "100.00"]
    )
    item = features(values)
    return make_signal_event(
        run_id="rf-test",
        symbol="1HZ100V",
        direction=direction,
        duration_ticks=5,
        features=item,
        quality_score=7,
        signal_tick_epoch=1_700_000_000,
        signal_tick_id=f"tick-{tick_sequence}",
        connection_session_id="connection-1",
        tick_sequence=tick_sequence,
    )


class RiseFallFeatureTests(unittest.TestCase):
    def test_six_quotes_create_exactly_five_movements(self) -> None:
        item = features(["1.00", "1.10", "1.20", "1.30", "1.40", "1.50"])
        self.assertEqual(len(item.analysis_quotes), 6)
        self.assertEqual(len(item.movements), 5)

    def test_rise_and_fall_rules_are_symmetric(self) -> None:
        rise = features(["1.00", "1.10", "1.05", "1.15", "1.25", "1.40"])
        fall = features(["1.40", "1.30", "1.35", "1.25", "1.15", "1.00"])
        self.assertTrue(detect_rise_candidate(rise))
        self.assertFalse(detect_fall_candidate(rise))
        self.assertTrue(detect_fall_candidate(fall))
        self.assertFalse(detect_rise_candidate(fall))
        self.assertAlmostEqual(rise.efficiency, fall.efficiency)

    def test_high_frequency_rule_accepts_three_of_five_directional_moves(self) -> None:
        rise = features(["100", "101", "100.5", "100", "101", "102"])
        fall = features(["102", "101", "101.5", "102", "101", "100"])

        self.assertEqual(rise.up_count, 3)
        self.assertEqual(fall.down_count, 3)
        self.assertTrue(detect_rise_candidate(rise))
        self.assertTrue(detect_fall_candidate(fall))
        self.assertFalse(detect_rise_candidate(rise, minimum_directional_moves=4))
        self.assertFalse(detect_fall_candidate(fall, minimum_directional_moves=4))

    def test_final_two_ticks_must_confirm_the_trade_direction(self) -> None:
        rise_pullback = features(["1.00", "1.10", "1.20", "1.30", "1.25", "1.40"])
        fall_pullback = features(["1.40", "1.30", "1.20", "1.10", "1.15", "1.00"])

        self.assertFalse(detect_rise_candidate(rise_pullback))
        self.assertFalse(detect_fall_candidate(fall_pullback))

    def test_tight_rule_requires_final_three_moves_in_direction(self) -> None:
        rise_with_late_pullback = features(
            ["100.00", "100.20", "100.40", "100.30", "100.50", "100.70"]
        )
        fall_with_late_pullback = features(
            ["100.70", "100.50", "100.30", "100.40", "100.20", "100.00"]
        )

        self.assertTrue(detect_rise_candidate(rise_with_late_pullback))
        self.assertTrue(detect_fall_candidate(fall_with_late_pullback))
        self.assertFalse(
            detect_rise_candidate(
                rise_with_late_pullback,
                minimum_recent_directional_moves=3,
            )
        )
        self.assertFalse(
            detect_fall_candidate(
                fall_with_late_pullback,
                minimum_recent_directional_moves=3,
            )
        )

    def test_flat_window_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            features(["1", "1", "1", "1", "1", "1"])

    def test_equal_expiry_is_a_loss_for_both_directions(self) -> None:
        entry = Decimal("100.00")
        self.assertEqual(shadow_outcome("RISE", entry, entry), "LOSS")
        self.assertEqual(shadow_outcome("FALL", entry, entry), "LOSS")


class RiseFallContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = object.__new__(RFDir5TradingBot)
        self.bot.currency = "USD"
        self.bot.app_markup_percentage = 3.0

    def test_rise_and_fall_contracts_have_no_digit_or_barrier(self) -> None:
        rise = signal("RISE")
        fall = signal("FALL")
        rise_params = self.bot._contract_parameters_for(rise, 0.50, 5)
        fall_params = self.bot._contract_parameters_for(fall, 0.50, 10)
        self.assertEqual(rise_params["contract_type"], "CALL")
        self.assertEqual(fall_params["contract_type"], "PUT")
        self.assertEqual(rise_params["duration"], 5)
        self.assertEqual(fall_params["duration"], 10)
        for params in (rise_params, fall_params):
            self.assertNotIn("barrier", params)
            self.assertNotIn("prediction", params)
            self.assertNotIn("app_markup_percentage", params)

    def test_direct_buy_places_markup_only_in_authenticated_buy_parameters(self) -> None:
        request = self.bot._direct_buy_request(signal("RISE"), 0.50)
        self.assertNotIn("app_markup_percentage", request)
        self.assertEqual(request["parameters"]["app_markup_percentage"], 3.0)
        self.assertNotIn("barrier", request["parameters"])

    def test_public_contract_validation_allows_rest_purchase_when_private_cache_missing(self) -> None:
        self.bot.rf_account_supported_contracts = {}
        self.bot.rf_supported_contracts = {"R_100": {"PUT"}}

        self.assertTrue(
            self.bot._account_supports_contract(
                account_id="DOT123422",
                symbol="R_100",
                contract_type="PUT",
            )
        )
        self.assertFalse(
            self.bot._account_supports_contract(
                account_id="DOT123422",
                symbol="R_100",
                contract_type="CALL",
            )
        )

    def test_rf_execution_has_no_artificial_post_trade_spacing(self) -> None:
        config = load_test2_config(Path(__file__).with_name("config.yaml"))
        self.assertEqual(config.rf_strategy.minimum_trade_interval_seconds, 0)

    def test_live_config_uses_bounded_ai_cadence_relaxation(self) -> None:
        config = load_test2_config(Path(__file__).with_name("config.yaml"))

        self.assertEqual(config.rf_strategy.allowed_direction, "FALL")
        self.assertEqual(
            config.rf_strategy.markets,
            ("R_10", "R_100", "R_75", "1HZ10V", "1HZ75V"),
        )
        self.assertEqual(config.rf_strategy.minimum_directional_moves, 3)
        self.assertEqual(config.rf_strategy.minimum_recent_directional_moves, 2)
        self.assertEqual(config.rf_strategy.minimum_efficiency, 0.45)
        self.assertEqual(config.rf_strategy.minimum_directional_score, 5)
        self.assertEqual(config.rf_strategy.cadence_relax_after_seconds, 60)
        self.assertEqual(
            config.rf_strategy.relaxed_bayesian_minimum_samples,
            10,
        )
        self.assertEqual(
            config.rf_strategy.relaxed_bayesian_minimum_probability,
            0.35,
        )
        self.assertEqual(
            config.rf_strategy.relaxed_minimum_expected_return_on_stake,
            -0.25,
        )
        self.assertGreater(
            config.rf_strategy.bayesian_minimum_edge_confidence,
            config.rf_strategy.relaxed_bayesian_minimum_edge_confidence,
        )
        self.assertGreater(
            config.rf_strategy.hmm_minimum_fall_probability,
            config.rf_strategy.relaxed_hmm_minimum_fall_probability,
        )
        self.assertTrue(config.risk.recovery_enabled)
        self.assertEqual(config.risk.recovery_trigger_losses, 1)
        self.assertEqual(config.risk.maximum_recovery_balance_fraction, 1.0)
        self.assertEqual(config.virtual_protection.exit_after_wins, 2)

    def test_proposal_values_accept_strings_numbers_and_missing_commission(self) -> None:
        economics = parse_proposal_economics(
            {"proposal": {"id": "p1", "ask_price": "0.50", "payout": 0.92}},
            stake=0.50,
            predicted_probability=0.55,
            requested_monotonic=1.0,
            received_monotonic=1.1,
        )
        self.assertAlmostEqual(economics.potential_profit, 0.42)
        self.assertAlmostEqual(economics.potential_loss, 0.50)
        self.assertAlmostEqual(economics.break_even_probability, 0.50 / 0.92)

    def test_missing_payout_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_proposal_economics(
                {"proposal": {"id": "p1", "ask_price": "0.50"}},
                stake=0.50,
                predicted_probability=0.55,
                requested_monotonic=1.0,
                received_monotonic=1.1,
            )

    def test_recovery_debt_cannot_change_fixed_stake(self) -> None:
        self.bot.cfg = {"strategy": {"initial_stake": 0.50}}
        self.bot._client_state_for_token = lambda *_args, **_kwargs: {
            "base_stake": 0.75,
            "recovery_loss_pool": 1000.0,
            "oscar_debt": 1000.0,
            "single_recovery_pending": True,
        }
        self.assertEqual(
            self.bot._planned_stake_for_account("token", "DOT123", 0.01),
            0.75,
        )

    def test_deriv_headers_require_and_preserve_exact_app_id(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "DERIV_APP_ID is required"):
            deriv_headers("")
        headers = deriv_headers(
            "33MmAtDICSKcC7LAZj7JO",
            bearer_token="oauth-token",
        )
        self.assertEqual(headers["Deriv-App-ID"], "33MmAtDICSKcC7LAZj7JO")
        self.assertEqual(headers["Authorization"], "Bearer oauth-token")

    def test_telegram_hourly_report_contains_only_model_summary(self) -> None:
        message = TelegramAlertClient.format_hourly_report(
            {
                "timezone": "Africa/Nairobi",
                "window_start": "2026-07-21T10:00:00+03:00",
                "window_end": "2026-07-21T11:00:00+03:00",
                "hourly_trades": 10,
                "hourly_martingale_pnl": 1.25,
                "hourly_flat_pnl": -0.50,
                "active_accounts": 3,
                "today_martingale_pnl": 4.50,
            }
        )
        self.assertEqual(
            message,
            "\n".join(
                (
                    "Hourly Model Update — Nairobi (EAT)",
                    "Period: 21 Jul 2026, 10:00 EAT → 11:00 EAT",
                    "Trades this hour: 10",
                    "$0.50 + Martingale P/L: +1.25 USD",
                    "$0.50 without Martingale P/L: -0.50 USD",
                    "Active traders (Demo + Real): 3",
                    "Today's total P/L ($0.50 + Martingale): +4.50 USD",
                )
            ),
        )

    def test_telegram_hour_window_is_aligned_to_nairobi_clock(self) -> None:
        start, end = RFDir5TradingBot.telegram_hour_window(
            datetime(2026, 7, 21, 7, 35, tzinfo=timezone.utc)
        )
        self.assertEqual(start.isoformat(), "2026-07-21T10:00:00+03:00")
        self.assertEqual(end.isoformat(), "2026-07-21T11:00:00+03:00")

    def test_current_consecutive_streaks_use_latest_master_results(self) -> None:
        self.assertEqual(
            Test2Repository.current_consecutive_streaks(
                ["WIN", "WIN", "WIN", "LOSS", "WIN"]
            ),
            (3, 0),
        )
        self.assertEqual(
            Test2Repository.current_consecutive_streaks(
                ["LOSS", "LOSS", "WIN", "LOSS"]
            ),
            (0, 2),
        )

    def test_telegram_hourly_report_attaches_live_dashboard(self) -> None:
        settings = TelegramSettings(enabled=True)
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_CHAT_ID": "-1001234567890",
            },
        ):
            client = TelegramAlertClient(settings, MagicMock())
        client.dashboard_capture.capture = AsyncMock(return_value=b"png-image")
        client._send_photo = AsyncMock(return_value=True)
        client._send_text = AsyncMock(return_value=True)

        sent = asyncio.run(client.send_hourly_report(self._telegram_report()))

        self.assertTrue(sent)
        client._send_photo.assert_awaited_once()
        self.assertEqual(client._send_photo.await_args.args[0], b"png-image")
        client._send_text.assert_not_awaited()

    def test_telegram_photo_failure_falls_back_to_text(self) -> None:
        settings = TelegramSettings(enabled=True)
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_CHAT_ID": "-1001234567890",
            },
        ):
            client = TelegramAlertClient(settings, MagicMock())
        client.dashboard_capture.capture = AsyncMock(return_value=b"png-image")
        client._send_photo = AsyncMock(return_value=False)
        client._send_text = AsyncMock(return_value=True)

        sent = asyncio.run(client.send_hourly_report(self._telegram_report()))

        self.assertTrue(sent)
        client._send_photo.assert_awaited_once()
        client._send_text.assert_awaited_once()

    def test_dashboard_exposes_capture_boundary_after_live_metrics_render(self) -> None:
        dashboard = (Path(__file__).parent / "dashboard" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="global-dashboard-snapshot"', dashboard)
        self.assertIn('data-snapshot-state="loading"', dashboard)
        self.assertIn(
            'snapshot.dataset.snapshotReady = "true";',
            dashboard,
        )
        self.assertIn('snapshot.dataset.snapshotState = "ready";', dashboard)
        self.assertIn('snapshot.dataset.snapshotState = "error";', dashboard)
        self.assertIn('id="telegram-dashboard-snapshot"', dashboard)
        self.assertIn('dashboard:snapshot-ready', dashboard)

    def test_telegram_screenshot_waits_for_complete_desktop_dashboard(self) -> None:
        from app.services.dashboard_screenshot import (
            DESKTOP_VIEWPORT,
            WAIT_FOR_DASHBOARD_READY,
        )

        settings = TelegramSettings()
        self.assertEqual(settings.dashboard_screenshot_timeout_seconds, 300.0)
        self.assertEqual(settings.dashboard_selector, "#telegram-dashboard-snapshot")
        self.assertEqual(DESKTOP_VIEWPORT, {"width": 1440, "height": 980})
        self.assertIn("MutationObserver", WAIT_FOR_DASHBOARD_READY)
        self.assertIn('dashboard:snapshot-ready', WAIT_FOR_DASHBOARD_READY)
        self.assertIn('dataset.snapshotState !== "ready"', WAIT_FOR_DASHBOARD_READY)
        self.assertIn('loader?.classList.contains("active")', WAIT_FOR_DASHBOARD_READY)
        self.assertIn('"model-maximum-stake"', WAIT_FOR_DASHBOARD_READY)

    def test_public_simulator_and_viewer_reset_are_wired_safely(self) -> None:
        api_source = (Path(__file__).parent / "app" / "api.py").read_text(
            encoding="utf-8"
        )
        endpoint = api_source.split(
            '@app.get("/metrics/system-performance")', 1
        )[1].split('@app.websocket("/ws/dashboard")', 1)[0]
        self.assertNotIn("require_control_auth", endpoint)
        self.assertIn('min(1000.0, max(0.50', endpoint)

        dashboard = (Path(__file__).parent / "dashboard" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="model-maximum-stake"', dashboard)
        self.assertIn('id="model-flat-stake"', dashboard)
        self.assertIn("model_data_version", dashboard)
        self.assertIn("resetSimulationToViewerDefault();", dashboard)
        self.assertIn("me.settings?.stake_amount ?? 0.50", dashboard)
        self.assertIn("Math.min(1000, value)", dashboard)
        self.assertIn(
            '$("risk-longest-win-streak").textContent = number(baseToday.longest_win_streak || 0);',
            dashboard,
        )
        self.assertNotIn("allTime.longest_loss_streak", dashboard)

    def test_canonical_write_occurs_only_after_registered_contracts(self) -> None:
        source = (Path(__file__).parent / "app" / "rf_dir5_bot.py").read_text(
            encoding="utf-8"
        )
        purchase_flow = source.split(
            "async def _buy_selected_accounts", 1
        )[1].split("async def _purchase_accounts_by_stake", 1)[0]
        no_contract_guard = purchase_flow.index("if not contracts:")
        canonical_write = purchase_flow.index(
            "self.repository.record_system_model_trade("
        )
        self.assertGreater(canonical_write, no_contract_guard)
        self.assertIn("is_virtual=False", purchase_flow[canonical_write:])

    def test_dashboard_has_accessible_official_risk_disclaimer(self) -> None:
        dashboard = (Path(__file__).parent / "dashboard" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="risk-disclaimer-toggle"', dashboard)
        self.assertIn('aria-controls="risk-disclaimer-panel"', dashboard)
        self.assertIn('id="risk-disclaimer-panel"', dashboard)
        self.assertIn('id="risk-disclaimer-close"', dashboard)
        self.assertIn(
            "https://deriv.com/terms-and-conditions/risk-disclosure",
            dashboard,
        )
        self.assertIn('event.key === "Escape"', dashboard)

    @staticmethod
    def _telegram_report() -> dict[str, object]:
        return {
            "timezone": "Africa/Nairobi",
            "window_start": "2026-07-21T10:00:00+03:00",
            "window_end": "2026-07-21T11:00:00+03:00",
            "hourly_trades": 10,
            "hourly_martingale_pnl": 1.25,
            "hourly_flat_pnl": -0.50,
            "active_accounts": 3,
            "today_martingale_pnl": 4.50,
        }

    def test_telegram_channel_is_discovered_from_admin_or_post_update(self) -> None:
        chat_id, title = TelegramAlertClient.channel_from_updates(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 1,
                        "my_chat_member": {
                            "chat": {
                                "id": -1001234567890,
                                "type": "channel",
                                "title": "MR.DUKE",
                            }
                        },
                    }
                ],
            }
        )

        self.assertEqual(chat_id, "-1001234567890")
        self.assertEqual(title, "MR.DUKE")

    def test_discovered_telegram_channel_survives_container_restart(self) -> None:
        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "telegram-channel.json"
            settings = TelegramSettings(
                enabled=True,
                channel_cache_path=str(cache_path),
            )
            with patch.dict(
                "os.environ",
                {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": ""},
            ):
                first = TelegramAlertClient(settings, MagicMock())
                first.chat_id = "-1001234567890"
                first.chat_title = "MR.DUKE"
                first._cache_channel()

                restarted = TelegramAlertClient(settings, MagicMock())

        self.assertEqual(restarted.chat_id, "-1001234567890")
        self.assertEqual(restarted.chat_title, "MR.DUKE")


class RFLiveMarketDisplayTests(unittest.TestCase):
    def test_live_output_contains_exact_last_five_quotes_and_scan_state(self) -> None:
        bot = object.__new__(RFDir5TradingBot)
        bot.symbol = "1HZ100V"
        bot.live_market_symbol = "R_25"
        bot.rf_config = SimpleNamespace(minimum_history_movements=200)
        market = SimpleNamespace(
            symbol="R_25",
            live_ticks_history=deque(
                (
                    {"quote": Decimal(f"100.0{index}"), "display": f"100.0{index}"}
                    for index in range(1, 7)
                ),
                maxlen=5,
            ),
            ticks_history=[None] * 201,
        )
        bot.market_states = {"1HZ100V": market, "R_25": market}
        handler = MagicMock()
        bot._get_live_console_handler = lambda: handler

        bot._render_live_ticks()

        output = handler.set_status.call_args.args[0]
        self.assertIn(
            "last5=[100.02 | 100.03 | 100.04 | 100.05 | 100.06]",
            output,
        )
        self.assertIn("state=SCANNING", output)
        self.assertNotIn("strategy=", output)

    def test_history_bootstrap_preloads_rolling_strategy_and_display_windows(self) -> None:
        bot = object.__new__(RFDir5TradingBot)
        bot.rf_last_epoch = {}
        bot.rf_last_tick_id = {}
        market = SimpleNamespace(
            symbol="R_25",
            pip_size=3,
            ticks_history=deque(maxlen=216),
            live_ticks_history=deque(maxlen=5),
        )
        bot.market_states = {"R_25": market}
        prices = [Decimal(index) / Decimal("1000") for index in range(1, 202)]
        times = list(range(1_700_000_001, 1_700_000_202))

        bot._on_public_history(
            symbol="R_25",
            prices=prices,
            times=times,
            pip_size=3,
        )

        self.assertEqual(len(market.ticks_history), 201)
        self.assertEqual(len(market.live_ticks_history), 5)
        self.assertEqual(
            [item["quote"] for item in market.live_ticks_history],
            prices[-5:],
        )
        self.assertEqual(bot.rf_last_epoch["R_25"], times[-1])


class RFTickStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_constant_subscription_id_does_not_reject_new_ticks(self) -> None:
        bot = object.__new__(RFDir5TradingBot)
        market = SimpleNamespace(
            symbol="1HZ100V",
            pip_size=2,
            tick_sequence=0,
            ticks_history=deque(maxlen=216),
            live_ticks_history=deque(maxlen=5),
        )
        bot.symbol = "1HZ100V"
        bot.market_states = {"1HZ100V": market}
        bot.rf_last_epoch = {}
        bot.rf_last_tick_id = {}
        bot.live_market_symbol = "1HZ100V"
        bot.tick_sequence = 0
        bot.connection_session_id = "connection-1"
        bot.repository = MagicMock()
        bot.rf_repository = MagicMock()
        bot.rf_repository.settle_due_shadows.return_value = []
        bot.rf_supported_contracts = {}
        bot.logger = MagicMock()
        bot._mark_tick_received = MagicMock()
        bot._render_live_ticks = MagicMock()

        for offset in range(6):
            await bot._on_tick(
                {
                    "tick": {
                        "symbol": "1HZ100V",
                        "epoch": 1_700_000_001 + offset,
                        "quote": 100 + offset,
                        "id": "constant-subscription-id",
                    }
                }
            )

        self.assertEqual(len(market.ticks_history), 6)
        self.assertEqual(
            [item["quote"] for item in market.live_ticks_history],
            [Decimal(value) for value in range(101, 106)],
        )
        self.assertEqual(bot.repository.record_tick.call_count, 6)
        bot.logger.warning.assert_not_called()

    async def test_qualified_live_signal_never_creates_shadow_contracts(self) -> None:
        bot = object.__new__(RFDir5TradingBot)
        market = SimpleNamespace(
            symbol="1HZ100V",
            pip_size=2,
            tick_sequence=0,
            ticks_history=deque(maxlen=216),
            live_ticks_history=deque(maxlen=5),
        )
        bot.symbol = market.symbol
        bot.market_states = {market.symbol: market}
        bot.rf_config = SimpleNamespace(
            minimum_history_movements=100,
            normalization_movements=100,
            minimum_directional_moves=4,
            minimum_efficiency=0.65,
            minimum_impulse=0.75,
            maximum_impulse=3.0,
            maximum_move_ratio=3.0,
            minimum_directional_score=7,
            demo_duration_ticks=5,
        )
        bot.test2_config = SimpleNamespace(
            model=SimpleNamespace(run_id="direct-demo-test"),
        )
        bot.rf_last_epoch = {}
        bot.rf_last_tick_id = {}
        bot.live_market_symbol = market.symbol
        bot.tick_sequence = 0
        bot.connection_session_id = "connection-1"
        bot.repository = MagicMock()
        bot.rf_repository = MagicMock()
        bot.rf_supported_contracts = {market.symbol: {"CALL", "PUT"}}
        bot.rf_candidate_queue = []
        bot.logger = MagicMock()
        bot._mark_tick_received = MagicMock()
        bot._render_live_ticks = MagicMock()
        bot._schedule_candidate_arbitration = MagicMock()

        for offset in range(106):
            await bot._on_tick(
                {
                    "tick": {
                        "symbol": market.symbol,
                        "epoch": 1_700_000_001 + offset,
                        "quote": 300 - offset,
                    }
                }
            )

        self.assertGreater(bot.rf_repository.record_signal.call_count, 0)
        self.assertGreater(len(bot.rf_candidate_queue), 0)
        bot.rf_repository.create_shadow_contracts.assert_not_called()


class RFCandidateArbitrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_suspended_loss_market_is_rejected_before_proposal(self) -> None:
        bot = object.__new__(RFDir5TradingBot)
        blocked = signal("FALL", tick_sequence=20)
        bot.rf_candidate_queue = [blocked]
        bot.rf_config = SimpleNamespace(candidate_window_ms=0)
        bot.market_states = {blocked.symbol: SimpleNamespace(tick_sequence=20)}
        bot.loss_rotation_blocked_market = blocked.symbol
        bot.loss_rotation_blocked_markets = [blocked.symbol]
        bot._mark_rf_decision = MagicMock()
        bot.logger = MagicMock()

        await bot._arbitrate_candidates()

        bot._mark_rf_decision.assert_called_once_with(
            blocked,
            "SKIP_LOSS_MARKET_ROTATION",
            "market waits for one purchase on a different market",
        )
        bot.logger.info.assert_called_once()

    async def test_only_highest_ranked_fresh_market_requests_a_proposal(self) -> None:
        bot = object.__new__(RFDir5TradingBot)
        weaker = signal("RISE", tick_sequence=10)
        stronger = signal("RISE", tick_sequence=20)
        stronger.symbol = "R_25"
        stronger.quality_score = weaker.quality_score + 1
        bot.rf_candidate_queue = [weaker, stronger]
        bot.rf_config = SimpleNamespace(
            candidate_window_ms=0,
            demo_duration_ticks=5,
            minimum_trade_interval_seconds=60,
        )
        bot.market_states = {
            weaker.symbol: SimpleNamespace(tick_sequence=weaker.tick_sequence),
            stronger.symbol: SimpleNamespace(tick_sequence=stronger.tick_sequence),
        }
        bot.repository = MagicMock()
        bot.repository.control_state.return_value = ("MANUAL_PAUSE", "legacy pause")
        bot.rf_repository = MagicMock()
        bot.rf_repository.shadow_group_counts.return_value = (0, 0)
        bot.rf_repository.guard_state.return_value = {"state": "DEMO_LIVE"}
        bot.keyed_bayesian = KeyedBayesianProbability(minimum_completed_trades=1000)
        bot.test2_config = SimpleNamespace(
            bayesian=SimpleNamespace(
                required_edge_margin=0.01,
                minimum_shadow_outcomes=1000,
            ),
            execution=SimpleNamespace(demo_enabled=True),
        )
        bot.rf_decision_engine = RiseFallDecisionEngine(
            minimum_score=4,
            stale_signal_after_ms=1800,
        )
        bot.environment = "demo"
        bot.is_trading_locked = False
        bot.pending_contracts_for_current_cycle = set()
        bot.rf_last_purchase_monotonic = 0.0
        bot._prune_stale_pending_contracts = MagicMock()
        bot._mark_rf_decision = MagicMock()
        economics = ProposalEconomics(
            proposal_id="proposal-1",
            stake=0.50,
            payout=0.90,
            potential_profit=0.40,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.90,
            predicted_win_probability=0.50,
            expected_value=-0.05,
            expected_return_on_stake=-0.10,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        bot._proposal_for_duration = AsyncMock(
            return_value=(economics, time.monotonic(), time.monotonic())
        )
        bot._buy_selected_accounts = AsyncMock()

        await bot._arbitrate_candidates()

        bot._proposal_for_duration.assert_awaited_once_with(stronger, 5)
        bot._buy_selected_accounts.assert_awaited_once()
        bot.repository.control_state.assert_not_called()
        bot.rf_repository.shadow_group_counts.assert_not_called()
        bot._mark_rf_decision.assert_any_call(
            weaker,
            "SKIP_MARKET_ARBITRATION",
            "another market ranked higher",
            selected=False,
        )

class RFRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        config = load_test2_config(Path(__file__).with_name("config.yaml"))
        config.model.run_id = "rf-unit-test"
        self.database = Database(f"sqlite:///{Path(self.temp.name) / 'rf.db'}")
        self.database.create_schema()
        self.base = Test2Repository(self.database, config)
        self.repository = RFDir5Repository(self.base)

    def tearDown(self) -> None:
        self.database.engine.dispose()
        self.temp.cleanup()

    def create_signal_and_shadows(self):
        item = signal()
        self.repository.record_signal(item)
        self.repository.create_shadow_contracts(item, (5, 10))
        return item

    def create_managed_account(self, label: str = "Risk") -> int:
        with self.database.session() as session:
            account = ManagedAccount(label=label, token_secret="encrypted", enabled=True)
            session.add(account)
            session.flush()
            return account.id

    def settle_model_sequence(self, outcomes: str) -> list[bool]:
        virtual_flags = []
        for index, outcome in enumerate(outcomes):
            item = signal("RISE", tick_sequence=2_000 + index * 10)
            self.repository.record_signal(item)
            contract_id = f"canonical-contract-{item.signal_id}"
            self.base.register_purchase(
                signal_id=item.signal_id,
                contract_id=contract_id,
                transaction_id=f"canonical-transaction-{item.signal_id}",
                account_id=f"DOT{index:08d}",
                purchase_time=datetime.now(timezone.utc),
                aligned_with_signal=True,
                buy_price=0.50,
                payout=0.91,
            )
            virtual_flags.append(self.base.record_system_model_trade(
                signal_id=item.signal_id,
                symbol=item.symbol,
                direction=item.direction,
                contract_type=item.contract_type,
                duration_ticks=item.duration_ticks,
                entry_tick_sequence=item.tick_sequence,
                entry_spot=100.0,
                expected_profit_ratio=0.82,
                reference_base_stake=0.50,
            ))
            self.base.settle_due_system_model_trades(
                symbol=item.symbol,
                tick_sequence=item.tick_sequence + item.duration_ticks,
                exit_spot=101.0 if outcome == "W" else 99.0,
            )
            self.base.settle_trade(
                contract_id=contract_id,
                profit=0.41 if outcome == "W" else -0.50,
                outcome="win" if outcome == "W" else "loss",
                entry_tick=100.0,
                exit_tick=101.0 if outcome == "W" else 99.0,
                exit_digit=1 if outcome == "W" else 9,
                buy_price=0.50,
                payout=0.91 if outcome == "W" else 0.0,
            )
        return virtual_flags

    def test_canonical_model_ledger_never_infers_account_virtual_mode(self) -> None:
        flags = self.settle_model_sequence("LWLLLWLWWW")
        self.assertEqual(flags, [False] * 10)
        canonical = self.base.system_model_trades(
            start=datetime(2000, 1, 1, tzinfo=timezone.utc),
            end=datetime(2100, 1, 1, tzinfo=timezone.utc),
            include_virtual=False,
        )
        self.assertEqual(len(canonical), 10)
        self.assertTrue(all(not trade["is_virtual"] for trade in canonical))

    def test_canonical_statistics_require_at_least_one_actual_purchase(self) -> None:
        virtual_only = signal("RISE", tick_sequence=3_000)
        self.repository.record_signal(virtual_only)
        self.base.record_system_model_trade(
            signal_id=virtual_only.signal_id,
            symbol=virtual_only.symbol,
            direction=virtual_only.direction,
            contract_type=virtual_only.contract_type,
            duration_ticks=virtual_only.duration_ticks,
            entry_tick_sequence=virtual_only.tick_sequence,
            entry_spot=100.0,
            expected_profit_ratio=0.82,
            is_virtual=False,
        )
        self.base.settle_due_system_model_trades(
            symbol=virtual_only.symbol,
            tick_sequence=virtual_only.tick_sequence + virtual_only.duration_ticks,
            exit_spot=101.0,
        )

        mixed = signal("RISE", tick_sequence=3_100)
        self.repository.record_signal(mixed)
        self.base.register_purchase(
            signal_id=mixed.signal_id,
            contract_id="mixed-real-contract",
            transaction_id="mixed-real-transaction",
            account_id="DOTREAL001",
            purchase_time=datetime.now(timezone.utc),
            aligned_with_signal=True,
            buy_price=0.50,
            payout=0.91,
        )
        self.base.record_system_model_trade(
            signal_id=mixed.signal_id,
            symbol=mixed.symbol,
            direction=mixed.direction,
            contract_type=mixed.contract_type,
            duration_ticks=mixed.duration_ticks,
            entry_tick_sequence=mixed.tick_sequence,
            entry_spot=100.0,
            expected_profit_ratio=0.82,
            # A stale caller value cannot reclassify a financially executed
            # canonical row. Account virtual observations remain separate.
            is_virtual=True,
        )
        self.base.settle_due_system_model_trades(
            symbol=mixed.symbol,
            tick_sequence=mixed.tick_sequence + mixed.duration_ticks,
            exit_spot=101.0,
        )

        canonical = self.base.system_model_trades(
            start=datetime(2000, 1, 1, tzinfo=timezone.utc),
            end=datetime(2100, 1, 1, tzinfo=timezone.utc),
            include_virtual=False,
        )
        self.assertEqual([trade["signal_id"] for trade in canonical], [mixed.signal_id])
        self.assertFalse(canonical[0]["is_virtual"])

    def test_model_statistics_exclude_virtual_runs_and_are_idempotent(self) -> None:
        self.settle_model_sequence("LLLWW")
        start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        end = datetime(2100, 1, 1, tzinfo=timezone.utc)
        summary = self.base.system_performance_summary(start=start, end=end)
        self.assertEqual(summary["total_trades"], 5)
        self.assertEqual(summary["total_trades"], summary["wins"] + summary["losses"])
        self.assertEqual(summary["wins"], 2)
        self.assertEqual(summary["losses"], 3)
        self.assertEqual(self.base.open_system_model_trade_count(), 0)

        canonical = self.base.system_model_trades(
            start=start, end=end, include_virtual=False
        )
        with patch.object(
            self.base,
            "system_model_trades",
            side_effect=AssertionError("preloaded replay must not query again"),
        ):
            replay = self.base.system_performance_summary(
                start=start,
                end=end,
                trades=canonical,
            )
        self.assertEqual(replay["total_trades"], summary["total_trades"])

    def test_safe_reset_clears_canonical_ledger_and_preserves_traders(self) -> None:
        account_id = self.create_managed_account("Preserved trader")
        self.settle_model_sequence("WL")

        removed = reset_database(self.database, self.base.config.model.run_id)

        self.assertEqual(removed["system_model_trades"], 2)
        self.assertEqual(
            self.base.system_performance_summary(
                start=datetime(2000, 1, 1, tzinfo=timezone.utc),
                end=datetime(2100, 1, 1, tzinfo=timezone.utc),
            )["total_trades"],
            0,
        )
        self.assertIsNotNone(self.base.managed_account(account_id))

    def test_account_risk_state_uses_managed_identity_not_display_mask(self) -> None:
        first_id = self.create_managed_account("First masked account")
        second_id = self.create_managed_account("Second masked account")
        with self.database.session() as session:
            session.add_all(
                [
                    AccountRiskState(
                        managed_account_id=first_id,
                        account_id_masked="DOT***271",
                        consecutive_losses=1,
                    ),
                    AccountRiskState(
                        managed_account_id=second_id,
                        account_id_masked="DOT***271",
                        consecutive_losses=5,
                    ),
                ]
            )

        first = self.base.account_summary(
            "DOT00000271",
            managed_account_id=first_id,
        )
        second = self.base.account_summary(
            "DOT99999271",
            managed_account_id=second_id,
        )
        self.assertEqual(
            first["virtual_protection"]["consecutive_actual_losses"],
            1,
        )
        self.assertEqual(
            second["virtual_protection"]["consecutive_actual_losses"],
            5,
        )

    def test_model_stake_simulation_is_read_only_and_replays_requested_stake(self) -> None:
        account_id = self.create_managed_account("Independent user")
        with self.database.session() as session:
            session.get(ManagedAccount, account_id).stake_amount = 300.0
        self.settle_model_sequence("WL")
        start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        end = datetime(2100, 1, 1, tzinfo=timezone.utc)
        reference = self.base.system_performance_summary(
            start=start, end=end, simulated_base_stake=0.50
        )
        simulated = self.base.system_performance_summary(
            start=start, end=end, simulated_base_stake=300.0
        )
        self.assertEqual(reference["simulated_base_stake"], 0.50)
        self.assertEqual(simulated["simulated_base_stake"], 300.0)
        self.assertNotEqual(reference["fixed_pnl"], simulated["fixed_pnl"])
        account = self.base.managed_account(account_id)
        self.assertEqual(account["stake_amount"], 300.0)

    def test_realized_settlement_calibrates_one_canonical_signal_once(self) -> None:
        item = signal("RISE", tick_sequence=4_000)
        self.repository.record_signal(item)
        self.base.record_system_model_trade(
            signal_id=item.signal_id,
            symbol=item.symbol,
            direction=item.direction,
            contract_type=item.contract_type,
            duration_ticks=item.duration_ticks,
            entry_tick_sequence=item.tick_sequence,
            entry_spot=100.0,
            expected_profit_ratio=0.92,
            reference_base_stake=30.0,
        )
        purchased_at = datetime.now(timezone.utc)
        copied_contracts = (
            ("first", 0.50, 0.41, purchased_at),
            ("copy", 30.0, 24.90, purchased_at + timedelta(seconds=1)),
        )
        for suffix, actual_stake, _profit, provider_purchase_time in copied_contracts:
            self.base.register_purchase(
                signal_id=item.signal_id,
                contract_id=f"realized-{suffix}",
                transaction_id=f"transaction-{suffix}",
                account_id=f"DOT{suffix}",
                purchase_time=provider_purchase_time,
                aligned_with_signal=True,
                buy_price=actual_stake,
                payout=0.91,
                provider_purchase_time=provider_purchase_time,
            )
        self.base.settle_due_system_model_trades(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + item.duration_ticks,
            exit_spot=101.0,
        )
        # The later-purchased copy settles first. Once the earliest purchase
        # settles, it deterministically becomes the one canonical reference.
        for suffix, actual_stake, actual_profit, provider_purchase_time in reversed(
            copied_contracts
        ):
            self.assertTrue(
                self.base.settle_trade(
                    contract_id=f"realized-{suffix}",
                    profit=actual_profit,
                    outcome="win",
                    entry_tick=100.0,
                    exit_tick=101.0,
                    exit_digit=1,
                    buy_price=actual_stake,
                    payout=0.91,
                    app_markup_amount=0.015,
                    provider_purchase_time=provider_purchase_time,
                )
            )

        start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        end = datetime(2100, 1, 1, tzinfo=timezone.utc)
        canonical = self.base.system_model_trades(
            start=start,
            end=end,
            include_virtual=False,
        )
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0]["reference_base_stake"], 0.50)
        self.assertAlmostEqual(canonical[0]["fixed_stake_profit"], 0.41)

        default = self.base.system_performance_summary(start=start, end=end)
        self.assertEqual(default["fixed_pnl"], 0.41)
        self.assertEqual(default["martingale_pnl"], 0.0)
        self.assertEqual(default["simulated_martingale_pnl"], 0.41)
        self.assertEqual(default["flat_stake"], 0.50)
        self.assertEqual(default["maximum_martingale_stake"], 0.0)
        self.assertEqual(default["simulated_maximum_martingale_stake"], 0.50)

        viewer = self.base.system_performance_summary(
            start=start,
            end=end,
            simulated_base_stake=30.0,
        )
        self.assertAlmostEqual(viewer["fixed_pnl"], 24.60)
        self.assertAlmostEqual(viewer["simulated_martingale_pnl"], 24.60)
        self.assertEqual(viewer["flat_stake"], 30.0)
        self.assertEqual(viewer["simulated_maximum_martingale_stake"], 30.0)

        large_simulation = self.base.system_performance_summary(
            start=start,
            end=end,
            simulated_base_stake=757.0,
        )
        self.assertAlmostEqual(large_simulation["fixed_pnl"], 620.74)
        self.assertEqual(large_simulation["flat_stake"], 757.0)

    def test_thirty_dollar_actual_contract_normalizes_to_fifty_cents(self) -> None:
        item = signal("RISE", tick_sequence=4_100)
        self.repository.record_signal(item)
        self.base.register_purchase(
            signal_id=item.signal_id,
            contract_id="thirty-dollar-contract",
            transaction_id="thirty-dollar-transaction",
            account_id="DOT30000000",
            purchase_time=datetime.now(timezone.utc),
            aligned_with_signal=True,
            buy_price=30.0,
            payout=54.60,
        )
        self.base.record_system_model_trade(
            signal_id=item.signal_id,
            symbol=item.symbol,
            direction=item.direction,
            contract_type=item.contract_type,
            duration_ticks=item.duration_ticks,
            entry_tick_sequence=item.tick_sequence,
            entry_spot=100.0,
            expected_profit_ratio=0.82,
            is_virtual=False,
        )
        self.base.settle_due_system_model_trades(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + item.duration_ticks,
            exit_spot=101.0,
        )
        self.base.settle_trade(
            contract_id="thirty-dollar-contract",
            profit=24.60,
            outcome="win",
            entry_tick=100.0,
            exit_tick=101.0,
            exit_digit=1,
            buy_price=30.0,
            payout=54.60,
        )
        canonical = self.base.system_model_trades(
            start=datetime(2000, 1, 1, tzinfo=timezone.utc),
            end=datetime(2100, 1, 1, tzinfo=timezone.utc),
            include_virtual=False,
        )
        self.assertEqual(len(canonical), 1)
        self.assertAlmostEqual(canonical[0]["fixed_stake_profit"], 0.41)

    def test_five_and_ten_tick_shadows_expire_on_exact_market_ticks(self) -> None:
        item = self.create_signal_and_shadows()
        self.assertEqual(
            self.repository.settle_due_shadows(
                symbol=item.symbol,
                tick_sequence=item.tick_sequence + 4,
                expiry_quote=Decimal("101"),
            ),
            [],
        )
        first = self.repository.settle_due_shadows(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + 5,
            expiry_quote=Decimal("101"),
        )
        self.assertEqual([row["duration_ticks"] for row in first], [5])
        second = self.repository.settle_due_shadows(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + 10,
            expiry_quote=Decimal("99"),
        )
        self.assertEqual([row["duration_ticks"] for row in second], [10])

    def test_duplicate_shadow_settlement_is_idempotent(self) -> None:
        item = self.create_signal_and_shadows()
        first = self.repository.settle_due_shadows(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + 5,
            expiry_quote=Decimal("101"),
        )
        second = self.repository.settle_due_shadows(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + 5,
            expiry_quote=Decimal("101"),
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_shadow_and_demo_ledgers_are_separate(self) -> None:
        self.create_signal_and_shadows()
        with self.database.session() as session:
            self.assertEqual(session.scalar(select(func.count(ShadowContract.id))), 2)
            self.assertEqual(session.scalar(select(func.count(Trade.id))), 0)

    def test_virtual_guard_loss_win_and_restart_state_machine(self) -> None:
        first = self.create_signal_and_shadows()
        self.repository.activate_after_demo_loss()
        self.assertEqual(self.repository.guard_state()["state"], "WAITING_FOR_VIRTUAL_WIN")
        self.assertTrue(self.repository.start_virtual_contract(first.signal_id, 5))
        loss = self.repository.settle_due_shadows(
            symbol=first.symbol,
            tick_sequence=first.tick_sequence + 5,
            expiry_quote=Decimal("99"),
        )[0]
        self.assertEqual(self.repository.apply_virtual_settlement(loss), "WAITING_FOR_VIRTUAL_WIN")

        second = signal(tick_sequence=300)
        self.repository.record_signal(second)
        self.repository.create_shadow_contracts(second, (5, 10))
        self.assertTrue(self.repository.start_virtual_contract(second.signal_id, 5))
        settled = self.repository.settle_due_shadows(
            symbol=second.symbol,
            tick_sequence=second.tick_sequence + 5,
            expiry_quote=Decimal("101"),
        )
        win = next(row for row in settled if row["signal_id"] == second.signal_id)
        self.assertEqual(self.repository.apply_virtual_settlement(win), "ARMED_AFTER_VIRTUAL_WIN")
        restarted = RFDir5Repository(self.base)
        self.assertEqual(restarted.guard_state()["state"], "ARMED_AFTER_VIRTUAL_WIN")
        self.assertEqual(restarted.guard_state()["active_signal_id"], "")

    def test_virtual_guard_can_be_reset_without_removing_trade_data(self) -> None:
        self.repository.activate_after_demo_loss()

        self.repository.reset_guard()

        self.assertEqual(self.repository.guard_state()["state"], "DEMO_LIVE")

    def test_two_actual_losses_enter_account_virtual_mode(self) -> None:
        account_id = self.create_managed_account("Virtual")

        first = self.repository.record_account_outcome(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            profit=-2.0,
            current_balance=98.0,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            virtual_protection_enabled=True,
            virtual_trigger_actual_losses=2,
        )
        second = self.repository.record_account_outcome(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            profit=-2.0,
            current_balance=96.0,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            virtual_protection_enabled=True,
            virtual_trigger_actual_losses=2,
        )

        self.assertEqual(first["protection_mode"], "NORMAL_MODE")
        self.assertEqual(second["protection_mode"], "VIRTUAL_MODE")
        self.assertAlmostEqual(second["recovery_loss_debt"], 4.0)
        protection = self.repository.virtual_protection_for_account(
            managed_account_id=account_id
        )
        self.assertEqual(protection["mode"], "VIRTUAL_MODE")
        self.assertEqual(protection["consecutive_actual_losses"], 2)

    def test_real_win_resets_virtual_entry_loss_sequence(self) -> None:
        account_id = self.create_managed_account("Loss win loss")
        for profit, balance in ((-1.0, 99.0), (1.0, 100.0), (-1.0, 99.0)):
            result = self.repository.record_account_outcome(
                managed_account_id=account_id,
                profit=profit,
                current_balance=balance,
                recovery_enabled=True,
                virtual_protection_enabled=True,
                virtual_trigger_actual_losses=2,
            )
        self.assertEqual(result["protection_mode"], "NORMAL_MODE")
        self.assertEqual(result["consecutive_losses"], 1)

    def test_virtual_losses_do_not_change_actual_recovery_debt(self) -> None:
        account_id = self.create_managed_account("Virtual Losses")
        for balance in (98.0, 96.0):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                account_id_masked="DOT***422",
                profit=-2.0,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=1,
                virtual_protection_enabled=True,
                virtual_trigger_actual_losses=2,
            )
        item = signal("RISE", tick_sequence=500)
        self.repository.record_signal(item)

        opened = self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=item,
            configured_stake=2.0,
            simulated_stake=2.0,
            expected_payout=3.6,
        )
        self.assertIsNotNone(opened)

        settled = self.repository.settle_due_virtual_trades(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + item.duration_ticks,
            exit_quote=Decimal("99.00"),
        )
        duplicate = self.repository.settle_due_virtual_trades(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + item.duration_ticks,
            exit_quote=Decimal("99.00"),
        )

        self.assertEqual(len(settled), 1)
        self.assertEqual(settled[0]["result"], "VIRTUAL_LOSS")
        self.assertEqual(duplicate, [])
        protection = self.repository.virtual_protection_for_account(
            managed_account_id=account_id
        )
        self.assertEqual(protection["mode"], "VIRTUAL_MODE")
        self.assertEqual(protection["virtual_losses"], 1)
        self.assertAlmostEqual(protection["actual_recovery_debt"], 4.0)
        with self.database.session() as session:
            state = session.get(AccountRiskState, account_id)
            virtual = session.scalar(select(VirtualTrade))
            self.assertEqual(state.recovery_loss_debt, 4.0)
            self.assertEqual(virtual.amount_charged, 0.0)
            self.assertEqual(virtual.actual_profit_loss, 0.0)
            self.assertEqual(virtual.recovery_debt_change, 0.0)

    def test_virtual_win_arms_real_recovery_without_changing_debt(self) -> None:
        account_id = self.create_managed_account("Virtual Win")
        for balance in (98.0, 96.0):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                account_id_masked="DOT***422",
                profit=-2.0,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=1,
                virtual_protection_enabled=True,
                virtual_trigger_actual_losses=2,
            )
        item = signal("RISE", tick_sequence=700)
        self.repository.record_signal(item)
        self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=item,
            configured_stake=2.0,
            simulated_stake=2.0,
            expected_payout=3.6,
        )
        settled = self.repository.settle_due_virtual_trades(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + item.duration_ticks,
            exit_quote=Decimal("101.00"),
            exit_after_wins=2,
        )
        protection = self.repository.virtual_protection_for_account(
            managed_account_id=account_id
        )
        self.assertEqual(protection["mode"], "VIRTUAL_MODE")
        self.assertEqual(protection["virtual_wins"], 1)
        second = signal("RISE", tick_sequence=710)
        self.repository.record_signal(second)
        self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=second,
            configured_stake=2.0,
            simulated_stake=2.0,
            expected_payout=3.6,
        )
        second_settled = self.repository.settle_due_virtual_trades(
            symbol=second.symbol,
            tick_sequence=second.tick_sequence + second.duration_ticks,
            exit_quote=Decimal("101.00"),
            exit_after_wins=2,
        )
        protection = self.repository.virtual_protection_for_account(
            managed_account_id=account_id
        )
        plan = self.repository.plan_stake(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            current_balance=96.0,
            requested_stake=2.0,
            proposal_profit_ratio=0.50,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            minimum_stake=0.50,
            maximum_recovery_balance_fraction=0.25,
            minimum_balance_reserve=0.50,
        )

        self.assertEqual(second_settled[0]["result"], "VIRTUAL_WIN")
        self.assertEqual(protection["mode"], "RECOVERY_PENDING")
        self.assertEqual(protection["virtual_wins"], 2)
        self.assertAlmostEqual(protection["actual_recovery_debt"], 4.0)
        self.assertTrue(plan.is_recovery)
        self.assertAlmostEqual(plan.required_recovery_stake, 8.0)

    def test_exit_after_wins_can_require_multiple_virtual_wins(self) -> None:
        account_id = self.create_managed_account("Virtual Confirmations")
        for balance in (98.0, 96.0):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                account_id_masked="DOT***422",
                profit=-2.0,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=1,
                virtual_protection_enabled=True,
                virtual_trigger_actual_losses=2,
            )
        first = signal("RISE", tick_sequence=720)
        second = signal("RISE", tick_sequence=730)
        self.repository.record_signal(first)
        self.repository.record_signal(second)

        self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=first,
            configured_stake=0.50,
            simulated_stake=0.50,
            expected_payout=0.90,
        )
        first_settled = self.repository.settle_due_virtual_trades(
            symbol=first.symbol,
            tick_sequence=first.tick_sequence + first.duration_ticks,
            exit_quote=Decimal("101.00"),
            # Even a stale/unsafe caller setting cannot weaken the exact rule.
            exit_after_wins=1,
        )
        self.assertEqual(first_settled[0]["result"], "VIRTUAL_WIN")
        self.assertEqual(
            self.repository.virtual_protection_for_account(
                managed_account_id=account_id
            )["mode"],
            "VIRTUAL_MODE",
        )

        self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=second,
            configured_stake=0.50,
            simulated_stake=0.50,
            expected_payout=0.90,
        )
        second_settled = self.repository.settle_due_virtual_trades(
            symbol=second.symbol,
            tick_sequence=second.tick_sequence + second.duration_ticks,
            exit_quote=Decimal("101.00"),
            exit_after_wins=2,
        )

        self.assertEqual(second_settled[0]["result"], "VIRTUAL_WIN")
        protection = self.repository.virtual_protection_for_account(
            managed_account_id=account_id
        )
        self.assertEqual(protection["mode"], "RECOVERY_PENDING")
        self.assertEqual(protection["virtual_wins"], 2)

    def test_virtual_loss_resets_two_win_confirmation_sequence(self) -> None:
        account_id = self.create_managed_account("Consecutive confirmations")
        for balance in (98.0, 96.0):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                account_id_masked="DOT***422",
                profit=-2.0,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=1,
                virtual_protection_enabled=True,
                virtual_trigger_actual_losses=2,
            )

        exit_quotes = (
            Decimal("101.00"),
            Decimal("99.00"),
            Decimal("101.00"),
            Decimal("101.00"),
        )
        expected_modes = (
            "VIRTUAL_MODE",
            "VIRTUAL_MODE",
            "VIRTUAL_MODE",
            "RECOVERY_PENDING",
        )
        expected_confirmations = (1, 0, 1, 2)
        for index, (exit_quote, expected_mode, confirmations) in enumerate(
            zip(exit_quotes, expected_modes, expected_confirmations, strict=True)
        ):
            item = signal("RISE", tick_sequence=800 + index * 10)
            self.repository.record_signal(item)
            self.repository.start_virtual_trade(
                managed_account_id=account_id,
                account_id_masked="DOT***422",
                signal=item,
                configured_stake=0.50,
                simulated_stake=0.50,
                expected_payout=0.90,
            )
            self.repository.settle_due_virtual_trades(
                symbol=item.symbol,
                tick_sequence=item.tick_sequence + item.duration_ticks,
                exit_quote=exit_quote,
                exit_after_wins=2,
            )
            protection = self.repository.virtual_protection_for_account(
                managed_account_id=account_id
            )
            self.assertEqual(protection["mode"], expected_mode)
            self.assertEqual(protection["virtual_wins"], confirmations)

    def test_virtual_mode_blocks_affordable_recovery_until_virtual_win(self) -> None:
        account_id = self.create_managed_account("Recovery priority")
        for balance in (99.50, 99.00):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                account_id_masked="DOT***422",
                profit=-0.50,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=1,
                virtual_protection_enabled=True,
                virtual_trigger_actual_losses=2,
            )
        self.assertEqual(
            self.repository.virtual_protection_for_account(
                managed_account_id=account_id
            )["mode"],
            "VIRTUAL_MODE",
        )

        plan = self.repository.plan_stake(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            current_balance=99.00,
            requested_stake=0.50,
            proposal_profit_ratio=0.40,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            minimum_stake=0.50,
            maximum_recovery_balance_fraction=1.0,
            minimum_balance_reserve=0.50,
        )

        self.assertIsNone(plan.stake)
        self.assertTrue(plan.is_recovery)
        self.assertIn("virtual protection waiting for virtual win", plan.reason)
        self.assertEqual(
            self.repository.virtual_protection_for_account(
                managed_account_id=account_id
            )["mode"],
            "VIRTUAL_MODE",
        )

    def test_only_one_open_virtual_observation_per_account(self) -> None:
        account_id = self.create_managed_account("One Virtual")
        for balance in (98.0, 96.0):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                account_id_masked="DOT***422",
                profit=-2.0,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=1,
                virtual_protection_enabled=True,
                virtual_trigger_actual_losses=2,
            )
        first = signal("RISE", tick_sequence=800)
        second = signal("RISE", tick_sequence=801)
        self.repository.record_signal(first)
        self.repository.record_signal(second)

        opened = self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=first,
            configured_stake=2.0,
            simulated_stake=2.0,
            expected_payout=3.6,
        )
        blocked = self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=second,
            configured_stake=2.0,
            simulated_stake=2.0,
            expected_payout=3.6,
        )

        self.assertIsNotNone(opened)
        self.assertIsNone(blocked)
        with self.database.session() as session:
            self.assertEqual(session.scalar(select(func.count(VirtualTrade.id))), 1)

    def test_recovery_loss_returns_to_virtual_mode_and_recovery_win_resets(self) -> None:
        account_id = self.create_managed_account("Recovery Loop")
        for balance in (98.0, 96.0):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                account_id_masked="DOT***422",
                profit=-2.0,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=1,
                virtual_protection_enabled=True,
                virtual_trigger_actual_losses=2,
            )
        item = signal("RISE", tick_sequence=850)
        self.repository.record_signal(item)
        self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=item,
            configured_stake=2.0,
            simulated_stake=2.0,
            expected_payout=3.6,
        )
        self.repository.settle_due_virtual_trades(
            symbol=item.symbol,
            tick_sequence=item.tick_sequence + item.duration_ticks,
            exit_quote=Decimal("101.00"),
            exit_after_wins=2,
        )
        second = signal("RISE", tick_sequence=860)
        self.repository.record_signal(second)
        self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=second,
            configured_stake=2.0,
            simulated_stake=2.0,
            expected_payout=3.6,
        )
        self.repository.settle_due_virtual_trades(
            symbol=second.symbol,
            tick_sequence=second.tick_sequence + second.duration_ticks,
            exit_quote=Decimal("101.00"),
            exit_after_wins=2,
        )
        self.assertEqual(
            self.repository.virtual_protection_for_account(
                managed_account_id=account_id
            )["mode"],
            "RECOVERY_PENDING",
        )

        self.repository.mark_recovery_attempt_started(account_id)
        loss = self.repository.record_account_outcome(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            profit=-8.0,
            current_balance=88.0,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            virtual_protection_enabled=True,
            virtual_trigger_actual_losses=2,
        )
        self.assertEqual(loss["protection_mode"], "VIRTUAL_MODE")
        self.assertAlmostEqual(loss["recovery_loss_debt"], 12.0)

        win = self.repository.record_account_outcome(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            profit=12.0,
            current_balance=100.0,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            virtual_protection_enabled=True,
            virtual_trigger_actual_losses=2,
        )
        self.assertEqual(win["protection_mode"], "VIRTUAL_MODE")
        self.assertEqual(win["consecutive_losses"], 0)
        self.assertAlmostEqual(win["recovery_loss_debt"], 0.0)

    def test_recent_activity_separates_actual_and_virtual_rows(self) -> None:
        account_id = self.create_managed_account("Feed")
        for balance in (98.0, 96.0):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                account_id_masked="DOT***422",
                profit=-2.0,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=1,
                virtual_protection_enabled=True,
                virtual_trigger_actual_losses=2,
            )
        item = signal("RISE", tick_sequence=900)
        self.repository.record_signal(item)
        self.repository.start_virtual_trade(
            managed_account_id=account_id,
            account_id_masked="DOT***422",
            signal=item,
            configured_stake=2.0,
            simulated_stake=2.0,
            expected_payout=3.6,
        )

        virtual_rows = self.base.recent_activity(
            50,
            account_id="DOT123422",
            activity_type="virtual",
        )
        actual_rows = self.base.recent_activity(
            50,
            account_id="DOT123422",
            activity_type="actual",
        )
        all_rows = self.base.recent_activity(
            50,
            account_id="DOT123422",
            activity_type="all",
        )

        self.assertEqual(len(virtual_rows), 1)
        self.assertEqual(virtual_rows[0]["activity_type"], "VIRTUAL_TRADE")
        self.assertEqual(virtual_rows[0]["profit"], 0.0)
        self.assertEqual(actual_rows, [])
        self.assertEqual(len(all_rows), 1)

    def test_configured_stake_is_not_reduced_by_automatic_drawdown_caps(self) -> None:
        account_id = self.create_managed_account()
        stake, reason = self.repository.effective_stake(
            managed_account_id=account_id,
            current_balance=1000.0,
            requested_stake=20.0,
            minimum_stake=0.50,
        )
        self.assertEqual(reason, "")
        self.assertEqual(stake, 20.0)

    def test_insufficient_balance_skips_only_that_account(self) -> None:
        account_id = self.create_managed_account("Small")
        stake, reason = self.repository.effective_stake(
            managed_account_id=account_id,
            current_balance=0.49,
            requested_stake=0.50,
            minimum_stake=0.50,
        )
        self.assertIsNone(stake)
        self.assertIn("insufficient account balance", reason)

    def test_quarantine_disables_only_target_account_and_preserves_secret(self) -> None:
        target_id = self.create_managed_account("Expired")
        healthy_id = self.create_managed_account("Healthy")

        self.base.quarantine_managed_account(
            target_id,
            "credential_error",
            "Invalid or expired token",
        )

        with self.database.session() as session:
            target = session.get(ManagedAccount, target_id)
            healthy = session.get(ManagedAccount, healthy_id)
            self.assertFalse(target.enabled)
            self.assertEqual(target.execution_status, "credential_error")
            self.assertEqual(target.token_secret, "encrypted")
            self.assertTrue(healthy.enabled)

    def test_rejected_shared_pat_is_removed_and_oauth_identity_is_retained(self) -> None:
        key = Fernet.generate_key().decode("utf-8")
        self.base.config.deriv.token_encryption_key = key
        first_id = self.create_managed_account("Expired Demo")
        second_id = self.create_managed_account("Expired Real")
        for account_id, account_type in ((first_id, "demo"), (second_id, "real")):
            payload = {
                "auth_type": "pat",
                "access_token": "expired-shared-pat",
                "account_id": f"VRTC{account_id}",
                "account_type": account_type,
                "auth_source": "deriv_oauth_with_pat",
                "oauth_access_token": "oauth-access",
                "oauth_refresh_token": "oauth-refresh",
                "oauth_expires_at": "2099-01-01T00:00:00+00:00",
                "oauth_scope": "trade",
                "pat_token_set": True,
            }
            with self.database.session() as session:
                session.get(ManagedAccount, account_id).token_secret = (
                    encrypt_auth_payload(payload, key)
                )

        affected = self.base.discard_rejected_trading_token(
            first_id,
            reason="Deriv API token expired or was rejected. Enter a new active token.",
        )

        self.assertEqual(affected, [first_id, second_id])
        with self.database.session() as session:
            for account_id in affected:
                row = session.get(ManagedAccount, account_id)
                payload = decrypt_auth_payload(row.token_secret, key)
                self.assertEqual(payload["auth_type"], "oauth")
                self.assertEqual(payload["access_token"], "oauth-access")
                self.assertEqual(payload["refresh_token"], "oauth-refresh")
                self.assertFalse(payload["pat_token_set"])
                self.assertNotIn("expired-shared-pat", row.token_secret)
                self.assertFalse(row.enabled)
                self.assertEqual(row.execution_status, "token_required")
                self.assertIn("new active token", row.execution_status_reason)

    def test_two_losses_arm_exactly_one_recovery_attempt(self) -> None:
        account_id = self.create_managed_account("Recovery")
        first = self.repository.record_account_outcome(
            managed_account_id=account_id,
            profit=-0.50,
            current_balance=999.50,
            recovery_enabled=True,
            recovery_trigger_losses=2,
            virtual_protection_enabled=False,
        )
        second = self.repository.record_account_outcome(
            managed_account_id=account_id,
            profit=-0.50,
            current_balance=999.00,
            recovery_enabled=True,
            recovery_trigger_losses=2,
            virtual_protection_enabled=False,
        )
        self.assertFalse(first["recovery_pending"])
        self.assertTrue(second["recovery_pending"])
        self.assertEqual(second["recovery_loss_debt"], 1.0)

        plan = self.repository.plan_stake(
            managed_account_id=account_id,
            current_balance=999.00,
            requested_stake=0.50,
            proposal_profit_ratio=0.40,
            recovery_enabled=True,
            recovery_trigger_losses=2,
            minimum_stake=0.50,
        )
        self.assertTrue(plan.is_recovery)
        self.assertEqual(plan.stake, 2.50)
        self.assertTrue(self.repository.mark_recovery_attempt_started(account_id))

        settled = self.repository.record_account_outcome(
            managed_account_id=account_id,
            profit=1.00,
            current_balance=1000.00,
            recovery_enabled=True,
            recovery_trigger_losses=2,
            virtual_protection_enabled=False,
        )
        self.assertTrue(settled["settled_recovery_attempt"])
        self.assertFalse(settled["recovery_pending"])
        self.assertFalse(settled["recovery_attempt_active"])
        self.assertEqual(settled["recovery_loss_debt"], 0.0)
        self.assertEqual(settled["consecutive_losses"], 0)

    def test_first_loss_arms_next_trade_to_recover_full_debt_once(self) -> None:
        account_id = self.create_managed_account("Immediate recovery")
        settled = self.repository.record_account_outcome(
            managed_account_id=account_id,
            profit=-0.50,
            current_balance=999.50,
            recovery_enabled=True,
            recovery_trigger_losses=1,
        )

        self.assertTrue(settled["recovery_pending"])
        self.assertEqual(settled["recovery_loss_debt"], 0.50)

        plan = self.repository.plan_stake(
            managed_account_id=account_id,
            current_balance=999.50,
            requested_stake=0.50,
            proposal_profit_ratio=0.38,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            minimum_stake=0.50,
        )

        self.assertTrue(plan.is_recovery)
        self.assertEqual(plan.stake, 1.32)
        self.assertGreaterEqual(plan.stake * 0.38, settled["recovery_loss_debt"])

        self.assertTrue(self.repository.mark_recovery_attempt_started(account_id))
        recovery = self.repository.record_account_outcome(
            managed_account_id=account_id,
            profit=0.50,
            current_balance=1000.00,
            recovery_enabled=True,
            recovery_trigger_losses=1,
        )
        self.assertTrue(recovery["settled_recovery_attempt"])
        self.assertFalse(recovery["recovery_pending"])
        self.assertEqual(recovery["recovery_loss_debt"], 0.0)

    def test_failed_recovery_keeps_cumulative_debt_for_next_contract(self) -> None:
        account_id = self.create_managed_account("Cumulative recovery")
        for balance in (999.50, 999.00):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                profit=-0.50,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=2,
                virtual_protection_enabled=False,
            )
        self.assertTrue(self.repository.mark_recovery_attempt_started(account_id))
        settled = self.repository.record_account_outcome(
            managed_account_id=account_id,
            profit=-2.50,
            current_balance=996.50,
            recovery_enabled=True,
            recovery_trigger_losses=2,
            virtual_protection_enabled=False,
        )
        self.assertTrue(settled["settled_recovery_attempt"])
        self.assertEqual(settled["consecutive_losses"], 3)
        self.assertEqual(settled["recovery_loss_debt"], 3.5)
        self.assertTrue(settled["recovery_pending"])

        next_plan = self.repository.plan_stake(
            managed_account_id=account_id,
            current_balance=996.50,
            requested_stake=0.50,
            proposal_profit_ratio=0.40,
            recovery_enabled=True,
            recovery_trigger_losses=2,
            minimum_stake=0.50,
        )
        self.assertTrue(next_plan.is_recovery)
        self.assertEqual(next_plan.stake, 8.75)

    def test_unaffordable_recovery_is_quarantined_without_erasing_debt(self) -> None:
        account_id = self.create_managed_account("Recovery fallback")
        for balance in (1.50, 1.00):
            self.repository.record_account_outcome(
                managed_account_id=account_id,
                profit=-0.50,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=2,
                virtual_protection_enabled=False,
            )
        plan = self.repository.plan_stake(
            managed_account_id=account_id,
            current_balance=1.00,
            requested_stake=0.50,
            proposal_profit_ratio=0.40,
            recovery_enabled=True,
            recovery_trigger_losses=2,
            minimum_stake=0.50,
        )
        self.assertIsNone(plan.stake)
        self.assertTrue(plan.is_recovery)
        self.assertIn("safety cap", plan.reason)
        self.assertEqual(plan.recovery_debt, 1.0)

        next_plan = self.repository.plan_stake(
            managed_account_id=account_id,
            current_balance=1.00,
            requested_stake=0.50,
            proposal_profit_ratio=0.40,
            recovery_enabled=True,
            recovery_trigger_losses=2,
            minimum_stake=0.50,
        )
        self.assertIsNone(next_plan.stake)
        self.assertTrue(next_plan.is_recovery)
        self.assertEqual(next_plan.recovery_debt, 1.0)

    def test_seven_losses_are_carried_into_the_next_recovery_plan(self) -> None:
        account_id = self.create_managed_account("Seven losses")
        balance = 1000.0
        for loss in (0.50, 1.00, 2.00, 4.00, 8.00, 16.00, 32.00):
            balance -= loss
            state = self.repository.record_account_outcome(
                managed_account_id=account_id,
                profit=-loss,
                current_balance=balance,
                recovery_enabled=True,
                recovery_trigger_losses=1,
                virtual_protection_enabled=False,
            )

        self.assertEqual(state["recovery_loss_debt"], 63.50)
        plan = self.repository.plan_stake(
            managed_account_id=account_id,
            current_balance=balance,
            requested_stake=0.50,
            proposal_profit_ratio=0.80,
            recovery_enabled=True,
            recovery_trigger_losses=1,
            minimum_stake=0.50,
        )
        self.assertTrue(plan.is_recovery)
        self.assertEqual(plan.stake, 79.38)

    def test_three_losses_never_disable_the_account(self) -> None:
        bot = object.__new__(RFDir5TradingBot)
        bot.repository = MagicMock()
        bot.repository.account_summary.return_value = {"balance": 100.0}
        bot.rf_repository = MagicMock()
        bot.rf_repository.record_account_outcome.return_value = {
            "settled_recovery_attempt": False,
            "recovery_pending": True,
            "consecutive_losses": 3,
            "recovery_loss_debt": 1.50,
        }
        bot.risk_config = SimpleNamespace(
            recovery_enabled=True,
            recovery_trigger_losses=2,
        )
        bot.logger = MagicMock()
        state = {
            "managed_account_id": 7,
            "account_id": "DOT90000422",
            "base_stake": 0.50,
        }

        bot._update_client_recovery_state(state, outcome="loss", profit=-0.50)

        bot.repository.set_managed_account_enabled.assert_not_called()
        bot.repository.set_managed_account_execution_status.assert_not_called()
        self.assertTrue(
            any(
                call.args
                and "RF_ACCOUNT_CONTINUES_AFTER_LOSSES" in str(call.args[0])
                for call in bot.logger.warning.call_args_list
            )
        )


class RFVirtualHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_virtual_mode_opens_observation_before_stake_plan(self) -> None:
        bot = object.__new__(RFDir5TradingBot)
        bot.cfg = {"strategy": {"initial_stake": 0.50}}
        bot.virtual_config = SimpleNamespace(enabled=True)
        bot.risk_config = SimpleNamespace(
            recovery_enabled=True,
            recovery_trigger_losses=1,
            maximum_recovery_balance_fraction=1.0,
            minimum_balance_reserve=0.50,
        )
        bot.logger = MagicMock()
        bot.repository = MagicMock()
        bot.repository.account_summary = MagicMock()
        bot.rf_repository = MagicMock()
        bot.rf_repository.virtual_protection_for_account.return_value = {
            "mode": VIRTUAL_MODE,
        }
        bot.rf_repository.start_virtual_trade.return_value = {
            "account": "DOT***422",
            "recovery_debt": 1.0,
        }
        bot.rf_repository.plan_stake = MagicMock()
        bot._eligible_purchase_accounts = MagicMock(
            return_value=[("token-1", "DOT123422")]
        )
        bot._account_supports_contract = MagicMock(return_value=True)
        bot._client_state_for_token = MagicMock(return_value={"base_stake": 0.50})
        bot._managed_account_id_for_token = MagicMock(return_value=7)
        bot._set_account_execution_status = MagicMock()

        item = signal("RISE", tick_sequence=1400)
        economics = ProposalEconomics(
            proposal_id="proposal-1",
            stake=0.50,
            payout=0.90,
            potential_profit=0.40,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.90,
            predicted_win_probability=0.50,
            expected_value=-0.05,
            expected_return_on_stake=-0.10,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )

        await bot._buy_selected_accounts(item, economics)

        bot.repository.account_summary.assert_not_called()
        bot.rf_repository.plan_stake.assert_not_called()
        kwargs = bot.rf_repository.start_virtual_trade.call_args.kwargs
        self.assertEqual(kwargs["configured_stake"], 0.50)
        self.assertEqual(kwargs["simulated_stake"], 0.50)
        self.assertEqual(kwargs["expected_payout"], 0.90)
        bot.repository.mark_signal.assert_called_once()
        self.assertEqual(
            bot.repository.mark_signal.call_args.kwargs["status"],
            "VIRTUAL_TRADE",
        )
        self.assertFalse(
            bot.repository.mark_signal.call_args.kwargs["purchase_requested"]
        )
        bot.repository.record_system_model_trade.assert_not_called()

        bot.repository.reset_mock()
        bot.rf_repository.start_virtual_trade.return_value = None
        waiting = signal("RISE", tick_sequence=1410)
        await bot._buy_selected_accounts(waiting, economics)
        bot.repository.record_system_model_trade.assert_not_called()
        self.assertEqual(
            bot.repository.mark_signal.call_args.kwargs["status"],
            "VIRTUAL_WAITING_SETTLEMENT",
        )


class RFDecisionTests(unittest.TestCase):
    def test_directional_hmm_identifies_persistent_fall_regime(self) -> None:
        model = DirectionalRegimeHmm(minimum_observations=100)
        movements = (
            [Decimal("-1")] * 180
            + [Decimal("1"), Decimal("-1")] * 80
            + [Decimal("1")] * 180
            + [Decimal("-1")] * 220
        )

        self.assertTrue(model.train(movements))
        inference = model.inference()

        self.assertTrue(inference.ready)
        self.assertEqual(inference.state, "FALL_CONTINUATION")
        self.assertGreater(
            inference.probabilities["FALL_CONTINUATION"],
            inference.probabilities["RISE_REVERSAL"],
        )

    def test_keyed_bayesian_groups_never_mix(self) -> None:
        model = KeyedBayesianProbability(minimum_completed_trades=2)
        rise = BayesianGroupKey(RF_DIR5_VERSION, "1HZ100V", "RISE", 5)
        fall = BayesianGroupKey(RF_DIR5_VERSION, "1HZ100V", "FALL", 5)
        model.update(rise, True)
        model.update(rise, True)
        model.update(fall, False)
        self.assertEqual(model.counts(rise), (2, 0))
        self.assertEqual(model.counts(fall), (0, 1))

    def test_stale_signal_cannot_be_purchased(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=6,
            stale_signal_after_ms=900,
        )
        economics = ProposalEconomics(
            proposal_id="p1",
            stake=0.50,
            payout=0.90,
            potential_profit=0.40,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.90,
            predicted_win_probability=0.50,
            expected_value=-0.05,
            expected_return_on_stake=-0.10,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        decision = engine.decide(
            quality_score=7,
            signal_age_ms=901,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
        )
        self.assertEqual(decision.action, "SKIP_STALE_SIGNAL")

    def test_demo_purchase_does_not_require_shadow_evidence(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=7,
            stale_signal_after_ms=900,
        )
        economics = ProposalEconomics(
            proposal_id="p1",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.50,
            expected_value=-0.02,
            expected_return_on_stake=-0.04,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )

        decision = engine.decide(
            quality_score=8,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
        )
        self.assertEqual(decision.action, "BUY_EXECUTION")
        self.assertEqual(decision.reasons, ("direct_execution",))

    def test_strict_model_gate_blocks_when_bayesian_is_not_ready(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=7,
            stale_signal_after_ms=900,
            require_bayesian=True,
            bayesian_safety_margin=0.02,
            bayesian_minimum_edge_confidence=0.90,
            require_hmm=True,
            hmm_minimum_fall_probability=0.78,
        )
        economics = ProposalEconomics(
            proposal_id="strict-p1",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.50,
            expected_value=-0.02,
            expected_return_on_stake=-0.04,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )

        decision = engine.decide(
            quality_score=8,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
        )

        self.assertEqual(decision.action, "SKIP_BAYESIAN_NOT_READY")

    def test_strict_model_gate_requires_bayesian_and_hmm_agreement(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=7,
            stale_signal_after_ms=900,
            require_bayesian=True,
            bayesian_safety_margin=0.02,
            bayesian_minimum_edge_confidence=0.90,
            require_hmm=True,
            hmm_minimum_fall_probability=0.78,
        )
        key = BayesianGroupKey(RF_DIR5_VERSION, "1HZ100V", "FALL", 5)
        model = KeyedBayesianProbability(
            prior_alpha=1,
            prior_beta=1,
            minimum_completed_trades=60,
        )
        model.restore(key, wins=90, losses=10)
        economics = ProposalEconomics(
            proposal_id="strict-p2",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.50,
            expected_value=-0.02,
            expected_return_on_stake=-0.04,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        bayesian = model.snapshot(
            key,
            break_even_probability=economics.break_even_probability,
            safety_margin=0.02,
        )
        hmm = DirectionalHmmInference(
            ready=True,
            state="FALL_CONTINUATION",
            probabilities={
                "FALL_CONTINUATION": 0.90,
                "CHOPPY": 0.07,
                "RISE_REVERSAL": 0.03,
            },
            observation_count=1000,
        )

        decision = engine.decide(
            quality_score=8,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            hmm=hmm,
        )

        self.assertEqual(decision.action, "BUY_EXECUTION")
        self.assertEqual(decision.reasons, ("strict_model_agreement",))
        self.assertGreater(float(decision.expected_value or 0), 0)

    def test_strict_model_gate_rejects_rising_hmm_regime(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=7,
            stale_signal_after_ms=900,
            require_bayesian=True,
            bayesian_safety_margin=0.02,
            bayesian_minimum_edge_confidence=0.90,
            require_hmm=True,
            hmm_minimum_fall_probability=0.78,
        )
        key = BayesianGroupKey(RF_DIR5_VERSION, "R_100", "FALL", 5)
        model = KeyedBayesianProbability(
            prior_alpha=1,
            prior_beta=1,
            minimum_completed_trades=60,
        )
        model.restore(key, wins=90, losses=10)
        economics = ProposalEconomics(
            proposal_id="strict-p3",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.50,
            expected_value=-0.02,
            expected_return_on_stake=-0.04,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        bayesian = model.snapshot(
            key,
            break_even_probability=economics.break_even_probability,
            safety_margin=0.02,
        )
        hmm = DirectionalHmmInference(
            ready=True,
            state="RISE_REVERSAL",
            probabilities={
                "FALL_CONTINUATION": 0.05,
                "CHOPPY": 0.10,
                "RISE_REVERSAL": 0.85,
            },
            observation_count=1000,
        )

        decision = engine.decide(
            quality_score=8,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            hmm=hmm,
        )

        self.assertEqual(decision.action, "SKIP_HMM_NOT_FAVOURABLE")

    def test_idle_relaxation_keeps_positive_edge_and_hmm_direction_gates(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=6,
            stale_signal_after_ms=900,
            require_bayesian=True,
            bayesian_safety_margin=0.01,
            bayesian_minimum_edge_confidence=0.80,
            require_hmm=True,
            hmm_minimum_fall_probability=0.78,
            cadence_relax_after_seconds=300,
            relaxed_bayesian_safety_margin=0.0,
            relaxed_bayesian_minimum_edge_confidence=0.65,
            relaxed_hmm_minimum_fall_probability=0.60,
        )
        key = BayesianGroupKey(RF_DIR5_VERSION, "R_75", "FALL", 5)
        model = KeyedBayesianProbability(
            prior_alpha=1,
            prior_beta=1,
            minimum_completed_trades=40,
        )
        model.restore(key, wins=80, losses=20)
        economics = ProposalEconomics(
            proposal_id="cadence-p1",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.50,
            expected_value=-0.02,
            expected_return_on_stake=-0.04,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        bayesian = model.snapshot(
            key,
            break_even_probability=economics.break_even_probability,
            safety_margin=0.0,
        )
        fall_hmm = DirectionalHmmInference(
            ready=True,
            state="FALL_CONTINUATION",
            probabilities={
                "FALL_CONTINUATION": 0.65,
                "CHOPPY": 0.25,
                "RISE_REVERSAL": 0.10,
            },
            observation_count=1000,
        )

        strict = engine.decide(
            quality_score=7,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            hmm=fall_hmm,
            idle_seconds=60,
        )
        relaxed = engine.decide(
            quality_score=7,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            hmm=fall_hmm,
            idle_seconds=600,
        )
        reversal = engine.decide(
            quality_score=7,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            hmm=DirectionalHmmInference(
                ready=True,
                state="RISE_REVERSAL",
                probabilities={
                    "FALL_CONTINUATION": 0.05,
                    "CHOPPY": 0.10,
                    "RISE_REVERSAL": 0.85,
                },
                observation_count=1000,
            ),
            idle_seconds=600,
        )

        self.assertEqual(strict.action, "SKIP_HMM_NOT_FAVOURABLE")
        self.assertEqual(relaxed.action, "BUY_EXECUTION")
        self.assertEqual(
            relaxed.reasons,
            ("cadence_relaxed_model_agreement",),
        )
        self.assertEqual(reversal.action, "SKIP_HMM_NOT_FAVOURABLE")

    def test_cadence_fallback_can_use_positive_cold_start_evidence(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=6,
            stale_signal_after_ms=900,
            require_bayesian=True,
            bayesian_safety_margin=0.01,
            bayesian_minimum_edge_confidence=0.80,
            require_hmm=True,
            hmm_minimum_fall_probability=0.70,
            cadence_relax_after_seconds=120,
            relaxed_bayesian_minimum_samples=20,
            relaxed_bayesian_safety_margin=0.0,
            relaxed_bayesian_minimum_edge_confidence=0.60,
            relaxed_hmm_minimum_fall_probability=0.30,
        )
        key = BayesianGroupKey(RF_DIR5_VERSION, "R_10", "FALL", 5)
        model = KeyedBayesianProbability(
            prior_alpha=1,
            prior_beta=1,
            minimum_completed_trades=40,
        )
        model.restore(key, wins=16, losses=11)
        economics = ProposalEconomics(
            proposal_id="cadence-cold-start",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.50,
            expected_value=-0.02,
            expected_return_on_stake=-0.04,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        bayesian = model.snapshot(
            key,
            break_even_probability=economics.break_even_probability,
            safety_margin=0.0,
        )
        choppy_hmm = DirectionalHmmInference(
            ready=True,
            state="CHOPPY",
            probabilities={
                "FALL_CONTINUATION": 0.35,
                "CHOPPY": 0.50,
                "RISE_REVERSAL": 0.15,
            },
            observation_count=1000,
        )

        strict = engine.decide(
            quality_score=7,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            hmm=choppy_hmm,
            idle_seconds=60,
        )
        relaxed = engine.decide(
            quality_score=7,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            hmm=choppy_hmm,
            idle_seconds=121,
        )

        self.assertEqual(strict.action, "SKIP_BAYESIAN_NOT_READY")
        self.assertEqual(relaxed.action, "BUY_EXECUTION")
        self.assertEqual(
            relaxed.reasons,
            ("cadence_relaxed_model_agreement",),
        )
        self.assertGreater(float(relaxed.expected_value or 0), 0)

    def test_cadence_fallback_never_buys_negative_expected_value(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=6,
            stale_signal_after_ms=900,
            require_bayesian=True,
            bayesian_minimum_edge_confidence=0.80,
            cadence_relax_after_seconds=120,
            relaxed_bayesian_minimum_samples=20,
            relaxed_bayesian_minimum_edge_confidence=0.60,
        )
        key = BayesianGroupKey(RF_DIR5_VERSION, "R_100", "FALL", 5)
        model = KeyedBayesianProbability(
            prior_alpha=1,
            prior_beta=1,
            minimum_completed_trades=40,
        )
        model.restore(key, wins=10, losses=10)
        economics = ProposalEconomics(
            proposal_id="cadence-negative-edge",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.50,
            expected_value=-0.02,
            expected_return_on_stake=-0.04,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        bayesian = model.snapshot(
            key,
            break_even_probability=economics.break_even_probability,
            safety_margin=0.0,
        )

        decision = engine.decide(
            quality_score=8,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            idle_seconds=121,
        )

        self.assertEqual(decision.action, "SKIP_NEGATIVE_EXPECTED_VALUE")

    def test_bounded_cadence_fallback_allows_logged_idle_case(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=5,
            stale_signal_after_ms=1800,
            require_bayesian=True,
            bayesian_minimum_edge_confidence=0.65,
            require_hmm=True,
            hmm_minimum_fall_probability=0.60,
            cadence_relax_after_seconds=60,
            relaxed_bayesian_minimum_samples=10,
            relaxed_bayesian_minimum_edge_confidence=0.0,
            relaxed_bayesian_minimum_probability=0.35,
            relaxed_minimum_expected_return_on_stake=-0.25,
            relaxed_hmm_minimum_fall_probability=0.20,
        )
        key = BayesianGroupKey(RF_DIR5_VERSION, "R_10", "FALL", 5)
        model = KeyedBayesianProbability(
            prior_alpha=1,
            prior_beta=1,
            minimum_completed_trades=40,
        )
        model.restore(key, wins=10, losses=16)
        economics = ProposalEconomics(
            proposal_id="bounded-cadence",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.50,
            expected_value=-0.02,
            expected_return_on_stake=-0.04,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        bayesian = model.snapshot(
            key,
            break_even_probability=economics.break_even_probability,
            safety_margin=0.0,
        )
        choppy_hmm = DirectionalHmmInference(
            ready=True,
            state="CHOPPY",
            probabilities={
                "FALL_CONTINUATION": 0.28,
                "CHOPPY": 0.62,
                "RISE_REVERSAL": 0.10,
            },
            observation_count=1000,
        )

        strict = engine.decide(
            quality_score=7,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            hmm=choppy_hmm,
            idle_seconds=30,
        )
        relaxed = engine.decide(
            quality_score=7,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            hmm=choppy_hmm,
            idle_seconds=61,
        )

        self.assertEqual(strict.action, "SKIP_BAYESIAN_NOT_READY")
        self.assertEqual(relaxed.action, "BUY_EXECUTION")
        self.assertEqual(
            relaxed.reasons,
            ("cadence_relaxed_bounded_fallback",),
        )
        self.assertLess(float(relaxed.expected_value or 0), 0)

    def test_bounded_cadence_fallback_rejects_excessive_negative_edge(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=5,
            stale_signal_after_ms=1800,
            require_bayesian=True,
            require_hmm=False,
            cadence_relax_after_seconds=60,
            relaxed_bayesian_minimum_samples=10,
            relaxed_bayesian_minimum_probability=0.35,
            relaxed_minimum_expected_return_on_stake=-0.25,
        )
        key = BayesianGroupKey(RF_DIR5_VERSION, "R_100", "FALL", 5)
        model = KeyedBayesianProbability(
            prior_alpha=1,
            prior_beta=1,
            minimum_completed_trades=40,
        )
        model.restore(key, wins=6, losses=20)
        economics = ProposalEconomics(
            proposal_id="excessive-negative-edge",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.50,
            expected_value=-0.02,
            expected_return_on_stake=-0.04,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )
        bayesian = model.snapshot(
            key,
            break_even_probability=economics.break_even_probability,
            safety_margin=0.0,
        )

        decision = engine.decide(
            quality_score=8,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="demo",
            trading_locked=False,
            bayesian=bayesian,
            idle_seconds=61,
        )

        self.assertEqual(decision.action, "SKIP_NEGATIVE_EXPECTED_VALUE")

    def test_real_execution_uses_the_same_model_decision(self) -> None:
        engine = RiseFallDecisionEngine(
            minimum_score=7,
            stale_signal_after_ms=900,
        )
        economics = ProposalEconomics(
            proposal_id="p2",
            stake=0.50,
            payout=0.96,
            potential_profit=0.46,
            potential_loss=0.50,
            break_even_probability=0.50 / 0.96,
            predicted_win_probability=0.55,
            expected_value=0.028,
            expected_return_on_stake=0.056,
            requested_monotonic=time.monotonic(),
            received_monotonic=time.monotonic(),
        )

        decision = engine.decide(
            quality_score=9,
            signal_age_ms=1,
            proposal_age_ms=1,
            proposal_economics=economics,
            execution_mode="real",
            trading_locked=False,
        )

        self.assertEqual(decision.action, "BUY_EXECUTION")
        self.assertEqual(decision.reasons, ("direct_execution",))

    def test_stale_contract_isolated_without_stopping_account_monitoring(self) -> None:
        bot = object.__new__(TradingBot)
        bot.pending_contracts_for_current_cycle = {42}
        bot.pending_contract_started_at = {
            42: datetime.now(timezone.utc),
        }
        bot.logger = MagicMock()
        bot._save_state = MagicMock()

        self.assertTrue(
            bot._isolate_stale_contract_from_global_cycle(42, "unit_test")
        )
        self.assertNotIn(42, bot.pending_contracts_for_current_cycle)
        bot._save_state.assert_called_once()
        bot.logger.warning.assert_called_once()
        bot.logger.error.assert_not_called()

    def test_stale_account_pending_contract_remains_isolated_until_settled(self) -> None:
        bot = object.__new__(RFDir5TradingBot)
        bot.valid_clients = [("token-a", "DOT90000001")]
        bot.sessions = {
            "token-a": SimpleNamespace(
                account_id="DOT90000001",
                pending_contracts={42},
            )
        }
        bot.pending_contract_started_at = {
            42: datetime.now(timezone.utc) - timedelta(seconds=90)
        }
        bot.max_open_trade_seconds = 30
        bot.pending_contracts_for_current_cycle = {42}
        bot.unresolved_contracts_from_state = {42}
        bot.unregistered_contracts = set()
        bot.contract_symbols = {42: "R_10"}
        bot.contract_signal_ids = {42: "signal-1"}
        bot.pending_by_signal = {"signal-1": {42}}
        bot.outcomes_by_signal = {"signal-1": {}}
        bot.signal_master_account_ids = {"signal-1": "DOT90000001"}
        bot.signal_symbols = {"signal-1": "R_10"}
        bot.delayed_contracts_logged = {42}
        bot.logger = MagicMock()
        bot._copytrading_master_account_id = MagicMock(return_value="DOT90000001")

        self.assertEqual(bot._eligible_purchase_accounts(), [])
        self.assertEqual(bot.sessions["token-a"].pending_contracts, {42})

    def test_log_sanitizer_redacts_pat_tokens(self) -> None:
        secret = "pat_" + "abcdefghijklmnopqrstuvwxyz0123456789"
        self.assertNotIn(secret, sanitize_log_value(KeyError(secret)))
        self.assertIn("[REDACTED_TOKEN]", sanitize_log_value(KeyError(secret)))


class SharedRecoveryStakeTests(unittest.TestCase):
    def test_recovery_uses_pre_trade_ratio_and_cent_rounding(self) -> None:
        calculation = calculate_recovery_stake(
            base_stake=0.50,
            recovery_debt=0.50,
            pre_trade_profit_ratio=0.82,
            minimum_stake=0.50,
        )
        self.assertEqual(calculation.requested_stake, 0.61)
        self.assertEqual(calculation.required_recovery_stake, 0.61)

    def test_recovery_safety_cap_preserves_pending_debt(self) -> None:
        calculation = calculate_recovery_stake(
            base_stake=0.50,
            recovery_debt=10.0,
            pre_trade_profit_ratio=0.82,
            minimum_stake=0.50,
            spendable_balance=9.50,
            current_balance=10.0,
            maximum_recovery_balance_fraction=0.10,
        )
        self.assertFalse(calculation.allowed)
        self.assertIn("safety cap", calculation.reason)


class ObservedMartingaleCohortTests(unittest.TestCase):
    def test_forensic_dominant_cohort_uses_actual_profit_and_maximum_stake(self) -> None:
        start = datetime(2026, 7, 26, tzinfo=timezone.utc)
        known = [
            (0.50, -0.50), (0.55, 0.45), (0.50, -0.50),
            (0.60, -0.60), (1.25, -1.25), (2.61, 2.13),
            (0.50, -0.50), (0.84, -0.84), (1.75, -1.75),
            (3.66, 2.99), (0.50, -0.50), (0.95, -0.95),
            (1.98, 1.62), (0.50, -0.50), (0.77, -0.77),
            (1.60, 1.31), (0.50, -0.50), (0.72, -0.72),
            (1.50, -1.50), (3.14, -3.14), (6.55, 5.36),
        ]
        # Complete the audited 44-trade invariants: $52.00 volume, +$0.91 P/L.
        dominant_path = known + [(0.50, 0.41), (0.50, -0.50)] * 10
        dominant_path += [(3.51, 0.82), (3.51, 0.82), (3.51, 0.83)]
        self.assertEqual(len(dominant_path), 44)
        self.assertAlmostEqual(sum(stake for stake, _profit in dominant_path), 52.00)
        self.assertAlmostEqual(sum(profit for _stake, profit in dominant_path), 0.91)

        executions = []
        for account_id in range(1, 44):
            for index, (stake, profit) in enumerate(dominant_path):
                executions.append(ObservedExecution(
                    account_id=account_id,
                    trade_id=account_id * 100 + index,
                    signal_id=f"signal-{index:02d}",
                    symbol="1HZ10V" if index == 20 else "R_10",
                    purchased_at=start + timedelta(seconds=index),
                    buy_price=stake,
                    payout=round(stake + profit, 2) if profit > 0 else 0.0,
                    profit=profit,
                    outcome="WIN" if profit > 0 else "LOSS",
                ))

        # One account follows a 45-trade, $6.27-max divergent trajectory.
        divergent_stakes = [1.16] + [1.0] * 43 + [6.27]
        divergent_profits = [0.95] + [0.10, -0.10] * 22
        for index, (stake, profit) in enumerate(zip(divergent_stakes, divergent_profits)):
            executions.append(ObservedExecution(
                account_id=999,
                trade_id=99900 + index,
                signal_id=f"divergent-{index:02d}",
                symbol="R_75",
                purchased_at=start + timedelta(seconds=index),
                buy_price=stake,
                payout=round(stake + profit, 2) if profit > 0 else 0.0,
                profit=profit,
                outcome="WIN" if profit > 0 else "LOSS",
            ))

        observed = observed_martingale_cohort(executions)
        self.assertEqual(observed["martingale_cohort_size"], 43)
        self.assertEqual(observed["martingale_population"], 44)
        self.assertEqual(observed["martingale_cohort_trade_count"], 44)
        self.assertEqual(observed["observed_martingale_pnl"], 0.91)
        self.assertEqual(observed["observed_martingale_stake_volume"], 52.00)
        self.assertEqual(observed["observed_maximum_stake"], 6.55)
        self.assertAlmostEqual(observed["martingale_cohort_confidence"], 43 / 44, places=4)
        self.assertNotEqual(observed["observed_martingale_pnl"], 43 * 0.91)

    def test_one_account_is_explicitly_low_sample(self) -> None:
        observed = observed_martingale_cohort([
            ObservedExecution(
                account_id=1,
                trade_id=1,
                signal_id="isolated",
                symbol="R_10",
                purchased_at=datetime.now(timezone.utc),
                buy_price=0.55,
                payout=0.0,
                profit=-0.55,
                outcome="LOSS",
            )
        ])
        self.assertEqual(observed["martingale_cohort_status"], "LOW_SAMPLE_EXECUTION")
        self.assertFalse(observed["martingale_cohort_sample_sufficient"])


class ObservedAndSimulatedFieldIsolationTests(unittest.TestCase):
    def test_synthetic_maximum_cannot_overwrite_observed_maximum(self) -> None:
        repository = object.__new__(Test2Repository)
        observed = {
            "observed_martingale_pnl": 0.91,
            "observed_maximum_stake": 6.55,
            "observed_martingale_stake_volume": 52.0,
            "observed_current_drawdown": 0.0,
            "observed_max_drawdown": 4.0,
            "martingale_cohort_size": 43,
            "martingale_population": 44,
            "martingale_cohort_confidence": round(43 / 44, 4),
            "martingale_cohort_trade_count": 44,
            "martingale_cohort_status": "OBSERVED_DOMINANT_COHORT",
            "martingale_cohort_sample_sufficient": True,
            "martingale_dominant_signature": "forensic",
        }
        repository.observed_martingale_performance = MagicMock(return_value=observed)
        start = datetime(2026, 7, 26, tzinfo=timezone.utc)
        trades = [
            {
                "signal_id": "loss",
                "signal_timestamp": start.isoformat(),
                "outcome": "LOSS",
                "is_virtual": False,
                "reference_base_stake": 0.50,
                "fixed_stake_profit": -0.50,
                "expected_profit_ratio": 0.82,
            },
            {
                "signal_id": "win",
                "signal_timestamp": (start + timedelta(seconds=1)).isoformat(),
                "outcome": "WIN",
                "is_virtual": False,
                "reference_base_stake": 0.50,
                "fixed_stake_profit": 0.41,
                "expected_profit_ratio": 0.82,
            },
        ]
        calculation = MagicMock(requested_stake=5.63)
        with patch(
            "app.repositories.test2_repository.calculate_recovery_stake",
            return_value=calculation,
        ):
            summary = repository.system_performance_summary(
                start=start,
                end=start + timedelta(days=1),
                trades=trades,
            )
        self.assertEqual(summary["martingale_pnl"], 0.91)
        self.assertEqual(summary["maximum_martingale_stake"], 6.55)
        self.assertEqual(summary["simulated_maximum_martingale_stake"], 5.63)


if __name__ == "__main__":
    unittest.main()
