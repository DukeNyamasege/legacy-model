from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Sequence

from app.strategy.over2_strategy import TEST2_BARRIER, TEST2_PATTERN_RANGES, TEST2_TRIGGER


@dataclass(slots=True)
class CandidateSignal:
    signal_id: str
    run_id: str
    symbol: str
    contract_type: str
    barrier: str
    trigger_name: str
    trigger_digits: tuple[int, ...]
    signal_tick_epoch: int
    signal_tick_id: str
    signal_last_digit: int
    generated_at: str
    generated_monotonic: float
    connection_session_id: str
    tick_sequence: int
    consumed: bool = False

    def to_record(self) -> dict:
        value = asdict(self)
        value["trigger_digits"] = list(self.trigger_digits)
        return value


class Over2SignalDetector:
    def __init__(
        self,
        *,
        run_id: str,
        trigger_name: str = TEST2_TRIGGER,
        pattern_ranges: Sequence[Sequence[int]] = TEST2_PATTERN_RANGES,
        overlapping_signals_allowed: bool = False,
        require_pattern_reset: bool = True,
    ) -> None:
        self.run_id = run_id
        self.trigger_name = str(trigger_name)
        self.pattern_ranges = tuple(
            (int(bounds[0]), int(bounds[1])) for bounds in pattern_ranges
        )
        if self.pattern_ranges != TEST2_PATTERN_RANGES:
            raise ValueError(
                f"Only the Test 2 purchase pattern {TEST2_PATTERN_RANGES!r} is allowed"
            )
        self.overlapping_signals_allowed = overlapping_signals_allowed
        self.require_pattern_reset = require_pattern_reset
        self.last_emitted_tick_id: str | None = None

    def observe(
        self,
        ticks: Sequence[dict],
        *,
        connection_session_id: str,
        tick_sequence: int,
    ) -> CandidateSignal | None:
        required = len(self.pattern_ranges)
        if len(ticks) < required:
            return None

        trigger_digits = tuple(
            int(tick["last_digit"]) for tick in ticks[-required:]
        )
        matches = all(
            lower <= digit <= upper
            for digit, (lower, upper) in zip(
                trigger_digits, self.pattern_ranges, strict=True
            )
        )
        if not matches:
            return None

        tick = ticks[-1]
        tick_id = str(tick["tick_id"])
        if (
            not self.overlapping_signals_allowed
            and self.last_emitted_tick_id == tick_id
        ):
            return None

        newest_digit = trigger_digits[-1]
        signal = CandidateSignal(
            signal_id=str(uuid.uuid4()),
            run_id=self.run_id,
            symbol="1HZ100V",
            contract_type="DIGITOVER",
            barrier=TEST2_BARRIER,
            trigger_name=self.trigger_name,
            trigger_digits=trigger_digits,
            signal_tick_epoch=int(tick["epoch"]),
            signal_tick_id=tick_id,
            signal_last_digit=newest_digit,
            generated_at=datetime.now(timezone.utc).isoformat(),
            generated_monotonic=time.monotonic(),
            connection_session_id=connection_session_id,
            tick_sequence=tick_sequence,
        )
        self.last_emitted_tick_id = tick_id
        return signal

    def rearm(self) -> None:
        self.last_emitted_tick_id = None


# Preserve imports used by older integrations while the deployment moves to Over-2 naming.
Over3SignalDetector = Over2SignalDetector
