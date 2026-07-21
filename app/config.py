from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.strategy.over2_strategy import TEST2_SYMBOLS
from app.strategy.rise_fall_strategy import RF_SYMBOLS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelIdentity(StrictModel):
    name: str
    version: str
    brand: str
    run_id: str = "test2"


class DerivSettings(StrictModel):
    app_id: str = Field(min_length=1)
    app_markup_percentage: float = Field(default=3.0, ge=0, le=3)
    environment: Literal["demo", "real"] = "demo"
    public_ws_url: str
    rest_base_url: str
    trading_enabled: bool = True
    allow_real_trading: bool = False
    production_acknowledgement: str = ""
    oauth_client_id: str = ""
    oauth_redirect_url: str = ""
    token_encryption_key: str = ""

    @model_validator(mode="after")
    def validate_application_identity(self) -> "DerivSettings":
        self.app_id = self.app_id.strip()
        if not self.app_id:
            raise ValueError("DERIV_APP_ID is required")
        if self.oauth_client_id and self.oauth_client_id.strip() != self.app_id:
            raise ValueError("OAuth client ID must match the registered Deriv App ID")
        return self


class StrategySettings(StrictModel):
    symbol: Literal["1HZ100V"] = "1HZ100V"
    symbols: tuple[str, ...] = TEST2_SYMBOLS
    contract_type: Literal["DIGITOVER"] = "DIGITOVER"
    prediction: Literal[2] = 2
    duration: Literal[1] = 1
    duration_unit: Literal["t"] = "t"
    pattern_length: Literal[5] = 5
    currency: Literal["USD"] = "USD"
    initial_stake: float = 0.50

    @model_validator(mode="after")
    def enforce_test2_contract(self) -> "StrategySettings":
        if abs(self.initial_stake - 0.50) > 1e-9:
            raise ValueError("Over 2 recovery requires a base stake of exactly 0.50 USD")
        if not self.symbols:
            raise ValueError("At least one Over-2 market must be configured")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("Over-2 markets must not contain duplicates")
        unsupported = [symbol for symbol in self.symbols if symbol not in TEST2_SYMBOLS]
        if unsupported:
            raise ValueError(f"Unsupported Over-2 markets: {unsupported!r}")
        if self.symbol not in self.symbols:
            raise ValueError("The primary Over-2 market must be included in symbols")
        return self


class SignalSettings(StrictModel):
    trigger_name: Literal["BIN22001x5"] = "BIN22001x5"
    pattern_ranges: tuple[tuple[int, int], ...] = (
        (6, 9),
        (6, 9),
        (0, 2),
        (0, 2),
        (3, 5),
    )
    overlapping_signals_allowed: bool = False
    require_pattern_reset: bool = True

    @model_validator(mode="after")
    def enforce_purchase_pattern(self) -> "SignalSettings":
        required = ((6, 9), (6, 9), (0, 2), (0, 2), (3, 5))
        if self.pattern_ranges != required:
            raise ValueError(f"Purchase pattern must be exactly {required!r}")
        return self


class ExecutionSettings(StrictModel):
    reject_if_new_tick_arrives: bool = False
    require_rising_ticks: Literal[True] = True
    rising_policy: Literal[
        "strict_last_three_quotes",
        "soft_rising_momentum",
        "high_frequency_momentum",
    ] = "soft_rising_momentum"
    maximum_signal_age_ms: int = Field(default=2500, gt=0)
    maximum_proposal_age_ms: int = Field(default=900, gt=0)
    demo_enabled: bool = True
    real_enabled: bool = False


class BayesianSettings(StrictModel):
    enabled: bool = True
    mode: Literal["shadow", "gate"] = "gate"
    prior_alpha: float = Field(default=108.0, gt=0)
    prior_beta: float = Field(default=19.0, gt=0)
    credible_interval: float = Field(default=0.95, gt=0, lt=1)
    safety_margin_probability: float = Field(default=0.02, ge=0, lt=1)
    minimum_completed_trades: int = Field(default=0, ge=0)
    minimum_probability_edge_confidence: float = Field(default=0.95, gt=0, le=1)
    scope: Literal["global", "per_market_direction_duration"] = "global"
    minimum_shadow_outcomes: int = Field(default=1000, ge=1)
    real_gate_enabled: bool = False
    required_edge_margin: float = Field(default=0.01, ge=0, lt=1)


class HmmSettings(StrictModel):
    enabled: bool = True
    mode: Literal["shadow", "gate"] = "shadow"
    minimum_training_ticks: int = Field(default=5000, ge=100)
    retrain_every_ticks: int = Field(default=1000, ge=1)
    favourable_state: Literal["MEAN_REVERSION"] = "MEAN_REVERSION"
    minimum_favourable_state_probability: float = Field(default=0.70, gt=0, le=1)


class CooldownSettings(StrictModel):
    after_win_ticks: int = Field(default=1, ge=0)
    after_loss_ticks: int = Field(default=3, ge=0)
    after_three_consecutive_losses_ticks: int = Field(default=15, ge=0)
    after_five_consecutive_losses_ticks: int = Field(default=50, ge=0)
    require_pattern_reset: bool = True


class RecoverySettings(StrictModel):
    enabled: bool = False
    mode: Literal["single_step", "two_run"] = "two_run"
    recovery_runs: int = Field(default=2, ge=1, le=10)
    debt_threshold: float = Field(default=0.50, ge=0)
    deep_debt_threshold: float = Field(default=2.00, ge=0)
    ladder_stakes: tuple[float, ...] = (0.50,)
    maximum_stake: float = Field(default=1000.00, gt=0)
    regime_guard_enabled: bool = False
    rolling_window_trades: int = Field(default=30, ge=1)
    pause_below_win_rate: float = Field(default=0.58, ge=0, le=1)
    resume_above_shadow_win_rate: float = Field(default=0.70, ge=0, le=1)
    shadow_min_samples: int = Field(default=30, ge=1)
    shadow_consecutive_wins_required: int = Field(default=2, ge=0)
    pause_after_consecutive_losses: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def enforce_ladder(self) -> "RecoverySettings":
        if not self.ladder_stakes:
            raise ValueError("Recovery ladder must contain at least one stake")
        if abs(self.ladder_stakes[0] - 0.50) > 1e-9:
            raise ValueError("Recovery ladder must start with the 0.50 base stake")
        if any(stake <= 0 for stake in self.ladder_stakes):
            raise ValueError("Recovery ladder stakes must be positive")
        if any(stake > self.maximum_stake for stake in self.ladder_stakes):
            raise ValueError("Recovery ladder cannot exceed maximum_stake")
        if self.shadow_min_samples > self.rolling_window_trades:
            raise ValueError("shadow_min_samples cannot exceed rolling_window_trades")
        if self.deep_debt_threshold < self.debt_threshold:
            raise ValueError("deep_debt_threshold must be >= debt_threshold")
        return self


class RiseFallStrategySettings(StrictModel):
    name: Literal["RF-PUT5-PREMIUM-V7"] = "RF-PUT5-PREMIUM-V7"
    allowed_direction: Literal["FALL"] = "FALL"
    markets: tuple[str, ...] = (
        "R_10",
        "R_100",
        "R_75",
        "1HZ10V",
        "1HZ75V",
    )
    analysis_movements: Literal[5] = 5
    required_quotes: Literal[6] = 6
    minimum_history_movements: int = Field(default=100, ge=50)
    normalization_movements: int = Field(default=100, ge=50)
    demo_duration_ticks: Literal[5, 10] = 5
    minimum_directional_moves: int = Field(default=3, ge=3, le=5)
    minimum_recent_directional_moves: int = Field(default=2, ge=2, le=5)
    minimum_efficiency: float = Field(default=0.35, ge=0, le=1)
    minimum_impulse: float = Field(default=0.15, ge=0)
    maximum_impulse: float = Field(default=4.50, gt=0)
    maximum_move_ratio: float = Field(default=6.00, gt=0)
    minimum_directional_score: int = Field(default=4, ge=1, le=10)
    candidate_window_ms: int = Field(default=75, ge=50, le=1000)
    stale_signal_after_ms: int = Field(default=1800, ge=100)
    # RF execution is gated by the active contract cycle, not an artificial
    # post-settlement delay. This prevents valid signals being discarded for
    # minutes after every completed trade.
    minimum_trade_interval_seconds: Literal[0] = 0
    maximum_open_strategy_contracts: Literal[1] = 1

    @model_validator(mode="after")
    def validate_rf_strategy(self) -> "RiseFallStrategySettings":
        if not self.markets or len(set(self.markets)) != len(self.markets):
            raise ValueError("RF-DIR5 markets must be non-empty and unique")
        unsupported = [symbol for symbol in self.markets if symbol not in RF_SYMBOLS]
        if unsupported:
            raise ValueError(f"Unsupported RF-DIR5 markets: {unsupported!r}")
        if self.maximum_impulse < self.minimum_impulse:
            raise ValueError("maximum_impulse must be >= minimum_impulse")
        if self.minimum_recent_directional_moves > self.analysis_movements:
            raise ValueError(
                "minimum_recent_directional_moves cannot exceed analysis_movements"
            )
        return self


class RiskSettings(StrictModel):
    recovery_enabled: bool = True
    recovery_trigger_losses: Literal[1] = 1
    maximum_recovery_balance_fraction: float = Field(default=0.10, gt=0, le=0.25)
    minimum_balance_reserve: float = Field(default=0.50, ge=0)
    maximum_open_contracts_per_account: Literal[1] = 1


class TelegramSettings(StrictModel):
    enabled: bool = True
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    channel_cache_path: str = "/app/model_artifacts/telegram_channel.json"
    interval_seconds: int = Field(default=3600, ge=60)
    initial_delay_seconds: int = Field(default=15, ge=0)
    request_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    dashboard_screenshot_enabled: bool = True
    dashboard_url: str = "http://api:8080/"
    dashboard_selector: str = "#global-dashboard-snapshot"
    dashboard_screenshot_timeout_seconds: float = Field(default=20.0, gt=0, le=60)


class StorageSettings(StrictModel):
    database_url_env: str = "DATABASE_URL"
    local_database_url: str = "sqlite:///data/test2.db"
    export_directory: str = "exports/test2"
    database_required: bool = True


class FileSettings(StrictModel):
    tokens: str = "tokens.txt"
    state: str = "bot_state.json"
    users: str = "users.json"


class LoggingSettings(StrictModel):
    level: str = "INFO"
    file: str = "trading_bot.log"


class TradeSettings(StrictModel):
    # Tick contracts are provider-timed. This is the operational target for
    # receiving final status, not a promise about wall-clock timing.
    settlement_sla_seconds: float = Field(default=15.0, gt=0)
    settle_wait_seconds: int = 6
    max_tick_silence_seconds: int = 45
    reconnect_delay_seconds: int = 10
    max_open_trade_seconds: int = 30
    reconciliation_poll_seconds: int = 1


class Test2Config(StrictModel):
    deriv: DerivSettings
    model: ModelIdentity
    files: FileSettings
    logging: LoggingSettings
    strategy: StrategySettings
    signal: SignalSettings
    execution: ExecutionSettings
    bayesian: BayesianSettings
    hmm: HmmSettings
    cooldown: CooldownSettings
    recovery: RecoverySettings = Field(default_factory=RecoverySettings)
    rf_strategy: RiseFallStrategySettings = Field(default_factory=RiseFallStrategySettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    storage: StorageSettings
    trade: TradeSettings

    @property
    def database_url(self) -> str:
        return os.getenv(self.storage.database_url_env, self.storage.local_database_url)

    @model_validator(mode="after")
    def enforce_real_trading_acknowledgement(self) -> "Test2Config":
        env_mode = os.getenv("TRADING_MODE", self.deriv.environment).lower()
        allow_real = os.getenv(
            "ALLOW_REAL_TRADING", str(self.deriv.allow_real_trading)
        ).lower() in {"1", "true", "yes"}
        acknowledgement = os.getenv(
            "PRODUCTION_ACKNOWLEDGEMENT", self.deriv.production_acknowledgement
        )
        if self.deriv.environment == "real":
            if (
                env_mode != "real"
                or not allow_real
                or acknowledgement != "I_ACKNOWLEDGE_REAL_MONEY_TRADING"
            ):
                raise ValueError(
                    "Real trading requires TRADING_MODE=real, ALLOW_REAL_TRADING=true, "
                    "and PRODUCTION_ACKNOWLEDGEMENT=I_ACKNOWLEDGE_REAL_MONEY_TRADING"
                )
        return self


def load_test2_config(path: str | Path = "config.yaml") -> Test2Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if os.getenv("DERIV_APP_ID"):
        raw.setdefault("deriv", {})["app_id"] = os.environ["DERIV_APP_ID"]
    if os.getenv("DERIV_APP_MARKUP_PERCENTAGE"):
        raw.setdefault("deriv", {})["app_markup_percentage"] = float(
            os.environ["DERIV_APP_MARKUP_PERCENTAGE"]
        )
    if os.getenv("DERIV_OAUTH_CLIENT_ID"):
        raw.setdefault("deriv", {})["oauth_client_id"] = os.environ[
            "DERIV_OAUTH_CLIENT_ID"
        ]
    if os.getenv("DERIV_OAUTH_REDIRECT_URL"):
        raw.setdefault("deriv", {})["oauth_redirect_url"] = os.environ[
            "DERIV_OAUTH_REDIRECT_URL"
        ]
    if os.getenv("DERIV_ENVIRONMENT"):
        raw.setdefault("deriv", {})["environment"] = os.environ["DERIV_ENVIRONMENT"].lower()
    if os.getenv("DERIV_PUBLIC_WS_URL"):
        raw.setdefault("deriv", {})["public_ws_url"] = os.environ["DERIV_PUBLIC_WS_URL"]
    if os.getenv("DERIV_REST_BASE_URL"):
        raw.setdefault("deriv", {})["rest_base_url"] = os.environ["DERIV_REST_BASE_URL"]
    if os.getenv("DERIV_TOKEN_ENCRYPTION_KEY"):
        raw.setdefault("deriv", {})["token_encryption_key"] = os.environ[
            "DERIV_TOKEN_ENCRYPTION_KEY"
        ]
    if os.getenv("DERIV_TRADING_ENABLED"):
        raw.setdefault("deriv", {})["trading_enabled"] = os.environ[
            "DERIV_TRADING_ENABLED"
        ].lower() in {"1", "true", "yes"}
    if os.getenv("ALLOW_REAL_TRADING"):
        raw.setdefault("deriv", {})["allow_real_trading"] = os.environ[
            "ALLOW_REAL_TRADING"
        ].lower() in {"1", "true", "yes"}
    if os.getenv("PRODUCTION_ACKNOWLEDGEMENT"):
        raw.setdefault("deriv", {})["production_acknowledgement"] = os.environ[
            "PRODUCTION_ACKNOWLEDGEMENT"
        ]
    if os.getenv("RF_STRATEGY_RUN_ID"):
        raw.setdefault("model", {})["run_id"] = os.environ["RF_STRATEGY_RUN_ID"]
    elif os.getenv("TEST_RUN_ID") and "rf_strategy" not in raw:
        raw.setdefault("model", {})["run_id"] = os.environ["TEST_RUN_ID"]
    if os.getenv("MARKET_SYMBOLS"):
        raw.setdefault("strategy", {})["symbols"] = tuple(
            symbol.strip()
            for symbol in os.environ["MARKET_SYMBOLS"].split(",")
            if symbol.strip()
        )
    if os.getenv("RF_MARKET_SYMBOLS"):
        raw.setdefault("rf_strategy", {})["markets"] = tuple(
            symbol.strip()
            for symbol in os.environ["RF_MARKET_SYMBOLS"].split(",")
            if symbol.strip()
        )
    if os.getenv("REQUIRE_RISING_TICKS"):
        raw.setdefault("execution", {})["require_rising_ticks"] = os.environ[
            "REQUIRE_RISING_TICKS"
        ].lower() in {"1", "true", "yes"}
    if os.getenv("RISING_POLICY"):
        raw.setdefault("execution", {})["rising_policy"] = os.environ[
            "RISING_POLICY"
        ].strip()
    if os.getenv("BAYESIAN_MIN_EDGE_CONFIDENCE"):
        raw.setdefault("bayesian", {})[
            "minimum_probability_edge_confidence"
        ] = float(os.environ["BAYESIAN_MIN_EDGE_CONFIDENCE"])
    if os.getenv("TELEGRAM_ALERTS_ENABLED"):
        raw.setdefault("telegram", {})["enabled"] = os.environ[
            "TELEGRAM_ALERTS_ENABLED"
        ].lower() in {"1", "true", "yes"}
    if os.getenv("TELEGRAM_ALERT_INTERVAL_SECONDS"):
        raw.setdefault("telegram", {})["interval_seconds"] = int(
            os.environ["TELEGRAM_ALERT_INTERVAL_SECONDS"]
        )
    if os.getenv("TELEGRAM_DASHBOARD_SCREENSHOT_ENABLED"):
        raw.setdefault("telegram", {})["dashboard_screenshot_enabled"] = os.environ[
            "TELEGRAM_DASHBOARD_SCREENSHOT_ENABLED"
        ].lower() in {"1", "true", "yes"}
    if os.getenv("TELEGRAM_DASHBOARD_URL"):
        raw.setdefault("telegram", {})["dashboard_url"] = os.environ[
            "TELEGRAM_DASHBOARD_URL"
        ].strip()
    return Test2Config.model_validate(raw)
