from app.provider_contract_spots import (
    provider_contract_digit,
    provider_contract_number,
    provider_contract_spot,
)


def test_exact_provider_spots_preserve_trailing_zero_parity() -> None:
    contract = {"entry_spot": "8241.48", "exit_spot": "8242.80"}

    entry = provider_contract_spot(contract, "entry")
    exit_ = provider_contract_spot(contract, "exit")

    assert entry == "8241.48"
    assert exit_ == "8242.80"
    assert provider_contract_digit(entry, 2) == 8
    assert provider_contract_digit(exit_, 2) == 0
    assert provider_contract_number(exit_) == 8242.8


def test_tick_stream_display_beats_precision_losing_numeric_fallback() -> None:
    contract = {
        "entry_spot": 8241.4,
        "exit_spot": 8242.8,
        "tick_stream": [
            {"tick": 8241.4, "tick_display_value": "8241.40"},
            {"tick": 8242.8, "tick_display_value": "8242.80"},
        ],
    }

    assert provider_contract_spot(contract, "entry") == "8241.40"
    assert provider_contract_spot(contract, "exit") == "8242.80"
    assert provider_contract_digit(provider_contract_spot(contract, "exit"), 2) == 0


def test_legacy_numeric_spot_uses_market_precision() -> None:
    contract = {"entry_tick": 100.5, "exit_tick": 101.2}

    assert provider_contract_digit(provider_contract_spot(contract, "entry"), 2) == 0
    assert provider_contract_digit(provider_contract_spot(contract, "exit"), 2) == 0
