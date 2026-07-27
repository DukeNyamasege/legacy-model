from __future__ import annotations

from typing import Any

from app.models import DirectionalSignal
from app.repositories.test2_repository import Test2Repository


HYBRID_VERSION = "HYBRID-O2-U7-PUTREC-V1"
_INSTALLED = False


def install_hybrid_data_integrity() -> None:
    """Ensure every executable hybrid digit candidate has its durable FK anchor.

    SystemModelTrade.signal_id references directional_signals.signal_id.  The hybrid
    O2/U7 path also records CandidateSignalRecord rows for dashboard compatibility,
    so this wrapper mirrors only hybrid digit candidates into DirectionalSignal before
    any purchase can create the canonical system-model trade row.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    original_record_candidate = Test2Repository.record_candidate

    def record_candidate_with_hybrid_anchor(
        self: Test2Repository,
        signal: Any,
    ) -> None:
        original_record_candidate(self, signal)

        if str(getattr(signal, "strategy_version", "")) != HYBRID_VERSION:
            return
        contract_type = str(getattr(signal, "contract_type", "")).upper()
        if contract_type not in {"DIGITOVER", "DIGITUNDER"}:
            return

        signal_id = str(getattr(signal, "signal_id"))
        with self.database.session() as session:
            if session.get(DirectionalSignal, signal_id) is not None:
                return
            trigger_digits = [
                int(value)
                for value in tuple(getattr(signal, "trigger_digits", ()) or ())
            ]
            session.add(
                DirectionalSignal(
                    signal_id=signal_id,
                    run_id=self.run_id,
                    strategy_version=HYBRID_VERSION,
                    symbol=str(getattr(signal, "symbol", "")),
                    direction=str(getattr(signal, "direction", "")),
                    contract_type=contract_type,
                    duration_ticks=int(getattr(signal, "duration_ticks", 1)),
                    signal_epoch=int(getattr(signal, "signal_tick_epoch", 0)),
                    signal_tick_id=str(getattr(signal, "signal_tick_id", "")),
                    tick_sequence=int(getattr(signal, "tick_sequence", 0)),
                    reference_entry_quote=float(getattr(signal, "reference_entry_quote", 0.0)),
                    analysis_quotes=[str(value) for value in trigger_digits],
                    movements=[],
                    feature_values={
                        "barrier": str(getattr(signal, "barrier", "")),
                        "p100": float(getattr(signal, "p100", 0.0)),
                        "p500": float(getattr(signal, "p500", 0.0)),
                        "p1000": float(getattr(signal, "p1000", 0.0)),
                        "lower95": float(getattr(signal, "lower95", 0.0)),
                        "weighted_probability": float(
                            getattr(signal, "weighted_probability", 0.0)
                        ),
                    },
                    quality_score=int(getattr(signal, "quality_score", 1)),
                    validated_edge=getattr(signal, "validated_edge", None),
                    selected_for_execution=False,
                    execution_decision="PENDING",
                    execution_reason="hybrid digit candidate",
                )
            )

    Test2Repository.record_candidate = record_candidate_with_hybrid_anchor
    _INSTALLED = True
