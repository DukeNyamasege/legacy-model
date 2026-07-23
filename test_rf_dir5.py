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
        self.assertEqual(config.rf_strategy.minimum_directional_moves, 4)
        self.assertEqual(config.rf_strategy.minimum_recent_directional_moves, 2)
        self.assertGreaterEqual(config.rf_strategy.minimum_efficiency, 0.70)
        self.assertEqual(config.rf_strategy.cadence_relax_after_seconds, 120)
        self.assertEqual(
            config.rf_strategy.relaxed_bayesian_minimum_samples,
            20,
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

    def test_telegram_hourly_report_contains_execution_totals(self) -> None:
        message = TelegramAlertClient.format_hourly_report(
            {
                "window_minutes": 60,
                "mode": "demo",
                "strategy": "RF-PUT5-PREMIUM-V7",
                "direction": "FALL",
                "contract_type": "PUT",
                "active_accounts": 3,
                "excluded_accounts": 2,
                "master_account": "DOT***422",
                "master_trades": 10,
                "master_wins": 7,
                "master_losses": 3,
                "master_profit": 1.25,
                "consecutive_wins": 3,
                "consecutive_losses": 0,
                "all_account_runs": 30,
                "all_account_profit": 3.75,
                "open_contracts": 0,
                "generated_at": "2026-07-21T10:00:00+00:00",
            }
        )
        self.assertEqual(
            message,
            "\n".join(
                (
                    "Test our model: https://derivadmin.site/",
                    "Total trades: 10",
                    "Trade type: FALL (PUT)",
                    "Per-account profit: 1.25 USD",
                    "Total profit: 3.75 USD",
                    "Consecutive wins/losses: 3/0",
                    "Join other traders and let's train the future.",
                )
            ),
        )

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
        self.assertIn(
            '$("global-dashboard-snapshot").dataset.snapshotReady = "true";',
            dashboard,
        )

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
            "direction": "FALL",
            "contract_type": "PUT",
            "master_trades": 10,
            "master_profit": 1.25,
            "consecutive_wins": 3,
            "consecutive_losses": 0,
            "all_account_profit": 3.75,
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
        bot.rf_repository.settle_due_shadows.assert_not_called()
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
        bot.rf_repository.settle_due_shadows.assert_not_called()


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
            "market suspended after master loss until another market wins",
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

        self.assertEqual(settled[0]["result"], "VIRTUAL_WIN")
        self.assertEqual(protection["mode"], "RECOVERY_PENDING")
        self.assertEqual(protection["virtual_wins"], 1)
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
            exit_after_wins=2,
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
        self.assertEqual(win["protection_mode"], "NORMAL_MODE")
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


if __name__ == "__main__":
    unittest.main()
