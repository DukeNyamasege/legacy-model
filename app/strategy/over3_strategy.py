from __future__ import annotations

TEST2_SYMBOL = "1HZ100V"
TEST2_CONTRACT_TYPE = "DIGITOVER"
TEST2_BARRIER = "3"
TEST2_STAKE = 0.35
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
    actual = (
        contract_type,
        str(barrier),
        symbol,
        round(float(stake), 2),
        int(duration),
        duration_unit,
    )
    required = (
        TEST2_CONTRACT_TYPE,
        TEST2_BARRIER,
        TEST2_SYMBOL,
        TEST2_STAKE,
        TEST2_DURATION,
        TEST2_DURATION_UNIT,
    )
    if actual != required:
        raise ValueError(f"Rejected non-Test-2 contract parameters: {actual!r}")
