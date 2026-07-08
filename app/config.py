from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelIdentity(StrictModel):
    name: str
    version: str
    brand: str
    run_id: str = "test2"


class DerivSettings(StrictModel):
    app_id: str
    environment: Literal["demo", "real"] = "demo"
    public_ws_url: str
    rest_base_url: str
    trading_enabled: bool = True
    allow_real_trading: bool = False
    production_acknowledgement: str = ""
    oauth_client_id: str = ""
    oauth_redirect_url: str = ""
    token_encryption_key: str = ""


class StrategySettings(StrictModel):
    symbol: Literal["1HZ100V"] = "1HZ100V"
    contract_type: Literal["DIGITOVER"] = "DIGITOVER"
    prediction: Literal[4] = 4
    duration: Literal[1] = 1
    duration_unit: Literal["t"] = "t"
    pattern_length: Literal[3] = 3
    currency: Literal["USD"] = "USD"
    initial_stake: float = 0.50

    @model_validator(mode="after")
    def enforce_test2_contract(self) -> "StrategySettings":
        if abs(self.initial_stake - 0.50) > 1e-9:
            raise ValueError("Over 4 recovery requires a base stake of exactly 0.50 USD")
        return self


class SignalSettings(StrictModel):
    trigger_name: Literal["BIN201x3"] = "BIN201x3"
    pattern_ranges: tuple[tuple[int, int], ...] = (
        (6, 9),
        (1, 2),
        (3, 5),
    )
    overlapping_signals_allowed: bool = False
    require_pattern_reset: bool = True

    @model_validator(mode="after")
    def enforce_purchase_pattern(self) -> "SignalSettings":
        required = ((6, 9), (1, 2), (3, 5))
        if self.pattern_ranges != required:
            raise ValueError(f"Purchase pattern must be exactly {required!r}")
        return self


class ExecutionSettings(StrictModel):
    reject_if_new_tick_arrives: bool = False
    maximum_signal_age_ms: int = Field(default=2500, gt=0)
    maximum_proposal_age_ms: int = Field(default=900, gt=0)


class BayesianSettings(StrictModel):
    enabled: bool = True
    mode: Literal["shadow", "gate"] = "shadow"
    prior_alpha: float = Field(default=3.0, gt=0)
    prior_beta: float = Field(default=2.0, gt=0)
    credible_interval: float = Field(default=0.95, gt=0, lt=1)
    safety_margin_probability: float = Field(default=0.02, ge=0, lt=1)
    minimum_completed_trades: int = Field(default=300, ge=1)
    minimum_probability_edge_confidence: float = Field(default=0.95, gt=0, le=1)


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
    mode: Literal["single_step"] = "single_step"
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
    settle_wait_seconds: int = 3
    max_tick_silence_seconds: int = 45
    reconnect_delay_seconds: int = 10


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
    if os.getenv("TEST_RUN_ID"):
        raw.setdefault("model", {})["run_id"] = os.environ["TEST_RUN_ID"]
    return Test2Config.model_validate(raw)
