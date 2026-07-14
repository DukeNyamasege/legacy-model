from __future__ import annotations

TEST2_SYMBOL = "1HZ100V"
TEST2_CONTRACT_TYPE = "DIGITOVER"
TEST2_BARRIER = "2"
TEST2_STAKE = 0.50
TEST2_DURATION = 1
TEST2_DURATION_UNIT = "t"
TEST2_TRIGGER = "BIN22001x5"
TEST2_PATTERN_RANGES = (
    (6, 9),
    (6, 9),
    (0, 2),
    (0, 2),
    (3, 5),
)


def validate_contract_parameters(
    *,
    contract_type: str,
    barrier: str,
    symbol: str,
    stake: float,
    duration: int,
    duration_unit: str,
) -> None:
    if (
        contract_type != TEST2_CONTRACT_TYPE
        or str(barrier) != TEST2_BARRIER
        or symbol != TEST2_SYMBOL
        or int(duration) != TEST2_DURATION
        or duration_unit != TEST2_DURATION_UNIT
    ):
        raise ValueError(
            "Rejected non-Over-2 contract parameters: "
            f"{(contract_type, str(barrier), symbol, int(duration), duration_unit)!r}"
        )
    if round(float(stake), 2) < TEST2_STAKE:
        raise ValueError(f"Rejected stake below Over-2 base stake: {stake!r}")
