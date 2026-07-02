from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class CooldownState:
    ticks_remaining: int = 0
    reason: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    consecutive_wins: int = 0
    consecutive_losses: int = 0


class AdaptiveCooldown:
    def __init__(
        self,
        *,
        after_win_ticks: int,
        after_loss_ticks: int,
        after_three_consecutive_losses_ticks: int,
        after_five_consecutive_losses_ticks: int,
    ) -> None:
        self.after_win_ticks = int(after_win_ticks)
        self.after_loss_ticks = int(after_loss_ticks)
        self.after_three_losses = int(after_three_consecutive_losses_ticks)
        self.after_five_losses = int(after_five_consecutive_losses_ticks)
        self.state = CooldownState()

    @property
    def active(self) -> bool:
        return self.state.ticks_remaining > 0

    def restore(
        self, *, ticks_remaining: int, consecutive_wins: int, consecutive_losses: int
    ) -> None:
        self.state.ticks_remaining = max(0, int(ticks_remaining))
        self.state.consecutive_wins = max(0, int(consecutive_wins))
        self.state.consecutive_losses = max(0, int(consecutive_losses))

    def register_outcome(self, outcome: str) -> CooldownState:
        if outcome == "win":
            self.state.consecutive_wins += 1
            self.state.consecutive_losses = 0
            ticks = self.after_win_ticks
            reason = "AFTER_WIN"
        else:
            self.state.consecutive_losses += 1
            self.state.consecutive_wins = 0
            if self.state.consecutive_losses >= 5:
                ticks = self.after_five_losses
                reason = "AFTER_FIVE_CONSECUTIVE_LOSSES"
            elif self.state.consecutive_losses >= 3:
                ticks = self.after_three_losses
                reason = "AFTER_THREE_CONSECUTIVE_LOSSES"
            else:
                ticks = self.after_loss_ticks
                reason = "AFTER_LOSS"
        self.state.ticks_remaining = max(0, ticks)
        self.state.reason = reason
        self.state.started_at = datetime.now(timezone.utc).isoformat()
        self.state.ended_at = None
        return self.state

    def observe_tick(self) -> bool:
        if self.state.ticks_remaining <= 0:
            return False
        self.state.ticks_remaining -= 1
        if self.state.ticks_remaining == 0:
            self.state.ended_at = datetime.now(timezone.utc).isoformat()
            return True
        return False
