from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLOCK_PATH = ROOT / "app" / "shared_system_strategy_clock.py"
METADATA_PATH = ROOT / "app" / "standardized_signal_metadata.py"


def _clock_source() -> str:
    return CLOCK_PATH.read_text(encoding="utf-8")


def test_manual_per_tick_generator_is_disabled() -> None:
    source = _clock_source()

    assert "_disable_parallel_manual_tick_generator" in source
    assert (
        "multi._queue_non_aidr_signals = "
        "_disable_parallel_manual_tick_generator"
    ) in source
    assert "entry_gate=system_aidr manual_tick_generator=false" in source
    assert "SKIP_NEWER_SAME_ACCOUNT_GROUP_SIGNAL" not in source


def test_every_strategy_uses_aidr_account_clock_and_contract_routing() -> None:
    source = _clock_source()

    assert "aidr._enabled_accounts = _all_strategy_accounts" in source
    assert "aidr._buy_for_scope = _shared_clock_buy_for_scope" in source
    assert "continuation._buy_for_scope = _shared_clock_buy_for_scope" in source
    assert 'return "DIGITOVER", f"OVER_{prediction}", str(prediction)' in source
    assert 'return "DIGITUNDER", f"UNDER_{prediction}", str(prediction)' in source
    assert '"DIGITEVEN" if side == "even" else "DIGITODD"' in source
    assert '"CALL" if side == "rise" else "PUT"' in source
    assert "_ORIGINAL_BUY_FOR_SCOPE(" in source
    assert "bot.repository.record_candidate(clone)" in source
    assert "bot.repository.record_proposal(clone, economics)" in source


def test_manual_contracts_do_not_add_a_second_entry_gate() -> None:
    source = _clock_source()

    assert "minimum_edge=0.0" in source
    assert "Qualification has already been" in source
    assert "The System AIDR gate is the qualification authority" in source
    assert "multi._proposal_for(bot, clone, predicted)" in source
    assert "SKIP_SHARED_CLOCK_INVALID_PROPOSAL" in source


def test_recovery_and_virtual_scopes_remain_account_exact() -> None:
    source = _clock_source()

    assert 'return "NORMAL"' in source
    assert 'return "RECOVERY"' in source
    assert 'return "VIRTUAL"' in source
    assert 'return "POST_VIRTUAL"' in source
    assert "delivery_role" in source
    assert "scope_ids=set(ids)" in source


def test_shared_clock_installs_before_final_delivery_wrappers_capture_hooks() -> None:
    source = METADATA_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "install_shared_system_strategy_clock" in source
    assert "install_shared_system_strategy_clock()" in source
    assert any(
        isinstance(node, ast.FunctionDef)
        and node.name == "install_standardized_signal_metadata"
        for node in ast.walk(tree)
    )
