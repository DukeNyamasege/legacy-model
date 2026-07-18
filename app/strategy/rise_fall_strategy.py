from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from statistics import median
from typing import Sequence


RF_DIR5_VERSION = "RF-DIR5-HF-V3"
RF_SYMBOLS = (
    "1HZ100V",
    "1HZ10V",
    "1HZ25V",
    "1HZ50V",
    "1HZ75V",
    "R_10",
    "R_25",
    "R_50",
    "R_75",
    "R_100",
)


@dataclass(frozen=True, slots=True)
class FiveMoveFeatures:
    analysis_quotes: tuple[Decimal, ...]
    movements: tuple[Decimal, ...]
    up_count: int
    down_count: int
    equal_count: int
    net_move: Decimal
    absolute_move: Decimal
    efficiency: float
    last_move: Decimal
    last_two_move: Decimal
    normal_move: Decimal
    impulse: float
    largest_move_ratio: float

    def to_dict(self) -> dict:
        value = asdict(self)
        value["analysis_quotes"] = [str(item) for item in self.analysis_quotes]
        value["movements"] = [str(item) for item in self.movements]
        for key in (
            "net_move",
            "absolute_move",
            "last_move",
            "last_two_move",
            "normal_move",
        ):
            value[key] = str(value[key])
        return value


@dataclass(slots=True)
class SignalEvent:
    signal_id: str
    run_id: str
    strategy_version: str
    symbol: str
    direction: str
    contract_type: str
    duration_ticks: int
    reference_entry_quote: Decimal
    features: FiveMoveFeatures
    quality_score: int
    signal_tick_epoch: int
    signal_tick_id: str
    generated_at: str
    generated_monotonic: float
    connection_session_id: str
    tick_sequence: int
    consumed: bool = False
    proposal_ask_price: float | None = None
    proposal_payout: float | None = None
    break_even_probability: float | None = None
    validated_edge: float | None = None

    # Compatibility fields keep the existing durable execution envelope usable.
    barrier: str = ""
    trigger_name: str = RF_DIR5_VERSION
    trigger_digits: tuple[int, ...] = ()
    signal_last_digit: int = -1

    def to_record(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "run_id": self.run_id,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "direction": self.direction,
            "contract_type": self.contract_type,
            "duration_ticks": self.duration_ticks,
            "reference_entry_quote": str(self.reference_entry_quote),
            "features": self.features.to_dict(),
            "quality_score": self.quality_score,
            "signal_tick_epoch": self.signal_tick_epoch,
            "signal_tick_id": self.signal_tick_id,
            "generated_at": self.generated_at,
            "connection_session_id": self.connection_session_id,
            "tick_sequence": self.tick_sequence,
        }


def decimal_prices(values: Sequence[Decimal | str | float | int]) -> list[Decimal]:
    return [value if isinstance(value, Decimal) else Decimal(str(value)) for value in values]


def build_five_move_features(
    prices: Sequence[Decimal | str | float | int],
    *,
    normalization_movements: Sequence[Decimal | str | float | int],
) -> FiveMoveFeatures:
    if len(prices) != 6:
        raise ValueError("RF-DIR5 requires exactly six quotes for five movements")
    quotes = decimal_prices(prices)
    moves = tuple(
        later - earlier
        for earlier, later in zip(quotes[:-1], quotes[1:], strict=True)
    )
    absolute_move = sum((abs(move) for move in moves), Decimal("0"))
    if absolute_move == 0:
        raise ValueError("RF-DIR5 cannot analyze a flat six-quote window")

    normal_values = [abs(value) for value in decimal_prices(normalization_movements) if value != 0]
    if not normal_values:
        raise ValueError("RF-DIR5 requires non-zero normalization movements")
    normal_move = median(normal_values[-100:])
    if normal_move <= 0:
        raise ValueError("RF-DIR5 normal movement must be positive")

    net_move = sum(moves, Decimal("0"))
    return FiveMoveFeatures(
        analysis_quotes=tuple(quotes),
        movements=moves,
        up_count=sum(move > 0 for move in moves),
        down_count=sum(move < 0 for move in moves),
        equal_count=sum(move == 0 for move in moves),
        net_move=net_move,
        absolute_move=absolute_move,
        efficiency=float(abs(net_move) / absolute_move),
        last_move=moves[-1],
        last_two_move=moves[-1] + moves[-2],
        normal_move=normal_move,
        impulse=float(abs(net_move) / (normal_move * Decimal(5).sqrt())),
        largest_move_ratio=float(max(abs(move) for move in moves) / normal_move),
    )


def detect_rise_candidate(
    features: FiveMoveFeatures,
    *,
    minimum_directional_moves: int = 3,
    minimum_efficiency: float = 0.35,
) -> bool:
    return (
        features.up_count >= minimum_directional_moves
        and features.net_move > 0
        and features.last_move > 0
        and features.movements[-2] > 0
        and features.last_two_move > 0
        and features.efficiency >= minimum_efficiency
        and features.equal_count <= 1
    )


def detect_fall_candidate(
    features: FiveMoveFeatures,
    *,
    minimum_directional_moves: int = 3,
    minimum_efficiency: float = 0.35,
) -> bool:
    return (
        features.down_count >= minimum_directional_moves
        and features.net_move < 0
        and features.last_move < 0
        and features.movements[-2] < 0
        and features.last_two_move < 0
        and features.efficiency >= minimum_efficiency
        and features.equal_count <= 1
    )


def check_volatility_filter(
    features: FiveMoveFeatures,
    *,
    minimum_impulse: float,
    maximum_impulse: float,
) -> bool:
    return minimum_impulse <= features.impulse <= maximum_impulse


def check_exhaustion_filter(
    features: FiveMoveFeatures,
    *,
    maximum_move_ratio: float,
) -> bool:
    return features.largest_move_ratio <= maximum_move_ratio


def calculate_directional_score(
    features: FiveMoveFeatures,
    *,
    direction: str,
    volatility_ok: bool,
    exhaustion_ok: bool,
    validated_edge: bool = False,
) -> int:
    directional_count = features.up_count if direction == "RISE" else features.down_count
    score = 3 if directional_count == 5 else 2
    score += 2 if features.efficiency >= 0.70 else 1
    if (direction == "RISE" and features.last_two_move > 0) or (
        direction == "FALL" and features.last_two_move < 0
    ):
        score += 1
    score += int(volatility_ok)
    score += int(exhaustion_ok)
    score += 2 if validated_edge else 0
    return score


def make_signal_event(
    *,
    run_id: str,
    symbol: str,
    direction: str,
    duration_ticks: int,
    features: FiveMoveFeatures,
    quality_score: int,
    signal_tick_epoch: int,
    signal_tick_id: str,
    connection_session_id: str,
    tick_sequence: int,
) -> SignalEvent:
    normalized = direction.upper()
    if normalized not in {"RISE", "FALL"}:
        raise ValueError(f"Unsupported RF-DIR5 direction: {direction!r}")
    return SignalEvent(
        signal_id=str(uuid.uuid4()),
        run_id=run_id,
        strategy_version=RF_DIR5_VERSION,
        symbol=symbol,
        direction=normalized,
        contract_type="CALL" if normalized == "RISE" else "PUT",
        duration_ticks=int(duration_ticks),
        reference_entry_quote=features.analysis_quotes[-1],
        features=features,
        quality_score=int(quality_score),
        signal_tick_epoch=int(signal_tick_epoch),
        signal_tick_id=str(signal_tick_id),
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        generated_monotonic=time.monotonic(),
        connection_session_id=connection_session_id,
        tick_sequence=int(tick_sequence),
    )


def shadow_outcome(direction: str, entry_quote: Decimal, expiry_quote: Decimal) -> str:
    if direction.upper() == "RISE":
        return "WIN" if expiry_quote > entry_quote else "LOSS"
    if direction.upper() == "FALL":
        return "WIN" if expiry_quote < entry_quote else "LOSS"
    raise ValueError(f"Unsupported RF-DIR5 direction: {direction!r}")
