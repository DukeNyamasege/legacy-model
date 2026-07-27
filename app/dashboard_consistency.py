from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


BASELINE_STAKE = 0.50


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _periods(api_module: Any, as_of: datetime) -> dict[str, tuple[datetime, datetime]]:
    tz = api_module._dashboard_timezone()
    local_now = as_of.astimezone(tz)
    today_start = datetime(
        local_now.year,
        local_now.month,
        local_now.day,
        tzinfo=tz,
    ).astimezone(timezone.utc)
    yesterday_start = today_start - timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = datetime(
        local_now.year,
        local_now.month,
        1,
        tzinfo=tz,
    ).astimezone(timezone.utc)
    return {
        "today": (today_start, as_of),
        "yesterday": (yesterday_start, today_start),
        "week": (week_start, as_of),
        "month": (month_start, as_of),
    }


def _canonicalize_model_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove copier timing/P&L from the account-independent model ledger.

    Only settled canonical WIN/LOSS rows are included. Open model rows must not be
    displayed as losses while a provider contract is still open; the dashboard has
    a separate open-trades counter for that.
    """
    result: list[dict[str, Any]] = []
    for original in trades:
        trade = dict(original)
        outcome = str(trade.get("outcome") or "").upper()
        if outcome not in {"WIN", "LOSS"}:
            continue
        ratio = max(0.0, float(trade.get("expected_profit_ratio") or 0.0))
        trade["reference_base_stake"] = BASELINE_STAKE
        trade["fixed_stake_profit"] = (
            ratio * BASELINE_STAKE if outcome == "WIN" else -BASELINE_STAKE
        )
        trade["execution_source"] = "canonical_model"
        result.append(trade)
    return result


def _canonical_model_performance(
    api_module: Any,
    *,
    start: datetime,
    end: datetime,
    base_stake: float,
) -> dict[str, Any]:
    """Replay one account-independent model sequence at one explicit base stake."""
    stake = min(1000.0, max(BASELINE_STAKE, float(base_stake)))
    trades = _canonicalize_model_rows(
        api_module.REPOSITORY.system_model_trades(
            start=start,
            end=end,
            include_virtual=False,
        )
    )
    raw = api_module.REPOSITORY.system_performance_summary(
        start=start,
        end=end,
        simulated_base_stake=stake,
        include_virtual=False,
        trades=trades,
        # Never let a copied account cohort replace the canonical model sequence.
        observed_executions=[],
    )

    total = int(raw.get("total_trades") or 0)
    wins = int(raw.get("wins") or 0)
    losses = int(raw.get("losses") or 0)
    if total != wins + losses:
        raise RuntimeError("canonical model totals invariant violated")

    fixed_pnl = round(float(raw.get("fixed_pnl") or 0.0), 2)
    recovery_pnl = round(float(raw.get("simulated_martingale_pnl") or 0.0), 2)
    base_exposure = stake * total

    raw.update(
        {
            "source": "canonical_system_model_ledger",
            "accounting_contract": "one_model_sequence_one_snapshot",
            "reference_account": "SYSTEM MODEL",
            "reference_account_type": "global",
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total if total else 0.0,
            "viewer_actual_trades": 0,
            "simulated_trades": total,
            "martingale_pnl": recovery_pnl,
            "observed_martingale_pnl": recovery_pnl,
            "maximum_martingale_stake": float(
                raw.get("simulated_maximum_martingale_stake") or stake
            ),
            "observed_maximum_stake": float(
                raw.get("simulated_maximum_martingale_stake") or stake
            ),
            "max_drawdown_martingale": float(
                raw.get("simulated_max_drawdown_martingale") or 0.0
            ),
            "current_drawdown_martingale": float(
                raw.get("simulated_current_drawdown_martingale") or 0.0
            ),
            "max_drawdown_martingale_pct": float(
                raw.get("simulated_max_drawdown_martingale_pct") or 0.0
            ),
            "current_drawdown_martingale_pct": float(
                raw.get("simulated_current_drawdown_martingale_pct") or 0.0
            ),
            "martingale_return_pct": round(
                recovery_pnl / base_exposure * 100.0,
                2,
            ) if base_exposure else 0.0,
            "fixed_return_pct": round(
                fixed_pnl / base_exposure * 100.0,
                2,
            ) if base_exposure else 0.0,
            "martingale_cohort_size": 0,
            "martingale_population": 0,
            "martingale_cohort_confidence": "CANONICAL_MODEL",
            "martingale_cohort_trade_count": total,
            "martingale_cohort_status": "CANONICAL_MODEL",
            "martingale_cohort_sample_sufficient": bool(total),
            "martingale_dominant_signature": "canonical-model-sequence",
            "flat_stake": stake,
            "simulated_base_stake": stake,
            "simulation_stake": stake,
            "simulated_fixed_pnl": fixed_pnl,
            "simulated_martingale_pnl": recovery_pnl,
        }
    )
    return raw


def reference_performance_summary(
    api_module: Any,
    *,
    account_type: str,
    start: datetime,
    end: datetime,
    simulated_base_stake: float = BASELINE_STAKE,
) -> dict[str, Any]:
    del account_type
    return _canonical_model_performance(
        api_module,
        start=_aware(start),
        end=_aware(end),
        base_stake=simulated_base_stake,
    )


def build_consistent_dashboard_snapshot(api_module: Any, account_type: str):
    target = api_module.normalize_account_type(account_type)
    as_of = datetime.now(timezone.utc)
    periods = _periods(api_module, as_of)

    performance = {
        name: _canonical_model_performance(
            api_module,
            start=start,
            end=end,
            base_stake=BASELINE_STAKE,
        )
        for name, (start, end) in periods.items()
    }
    today = performance["today"]
    if int(today["total_trades"]) != int(today["wins"]) + int(today["losses"]):
        raise RuntimeError("dashboard canonical totals invariant violated")

    result = api_module.filter_summary_to_trading_ready_accounts(
        api_module.REPOSITORY.summary(),
        account_type=target,
    )
    result["total_traders"] = api_module.REPOSITORY.managed_account_count()
    result["registered_traders"] = result["total_traders"]
    result["purchased_trades"] = int(today["total_trades"])
    result["wins"] = int(today["wins"])
    result["losses"] = int(today["losses"])
    result["win_rate"] = float(today["win_rate"])
    result["net_profit"] = float(today["martingale_pnl"])
    result["maximum_drawdown"] = float(today["max_drawdown_martingale"])
    result["longest_win_streak"] = int(today["longest_win_streak"])
    result["longest_loss_streak"] = int(today["longest_loss_streak"])
    result["open_trades"] = api_module.REPOSITORY.open_system_model_trade_count()
    result["system_performance"] = {
        **performance,
        "timezone": str(api_module._dashboard_timezone()),
        "daily_reset_hour": periods["today"][0].astimezone(api_module._dashboard_timezone()).hour,
        "next_session_close_at": (periods["today"][0] + timedelta(days=1)).isoformat(),
    }
    result["data_consistency"] = {
        "version": 2,
        "ledger": "canonical_system_model_ledger_settled_only",
        "reference_account": "SYSTEM MODEL",
        "reference_account_type": "global",
        "settled_trades": int(today["total_trades"]),
        "wins_plus_losses": int(today["wins"]) + int(today["losses"]),
        "open_trades": int(result["open_trades"] or 0),
        "pnl": float(today["martingale_pnl"]),
        "as_of": as_of.isoformat(),
        "invariant_ok": True,
    }
    watermark = {
        "canonical_trade_count": int(today["total_trades"]),
        "model_data_version": today.get("model_data_version", ""),
        "as_of": as_of.isoformat(),
    }
    return result, as_of, watermark


def consistent_period_response(
    api_module: Any,
    *,
    request: Any,
    period: str,
    simulated_base_stake: float,
) -> dict[str, Any]:
    normalized = str(period or "today").strip().lower()
    if normalized not in {"today", "yesterday", "week", "month"}:
        raise ValueError("Unsupported performance period")

    start, end = api_module._system_period_bounds(normalized)
    response = _canonical_model_performance(
        api_module,
        start=start,
        end=end,
        base_stake=simulated_base_stake,
    )
    viewer = api_module.get_current_account(request)
    account_type = api_module.normalize_account_type(
        viewer.get("account_type") if viewer else "demo"
    )
    cached = api_module._cached_dashboard_payload(account_type) or {}
    response.update(
        {
            "period": normalized,
            "timezone": str(api_module._system_reporting_timezone()),
            "minimum_stake": BASELINE_STAKE,
            "maximum_stake": 1000.0,
            "snapshot_version": int(cached.get("snapshot_version") or 0),
            "snapshot_generated_at": cached.get("generated_at"),
        }
    )
    return response


def install_dashboard_consistency(api_module: Any) -> None:
    """Install the settled-only global dashboard builder into the API module.

    ``app.api_account_lifecycle`` imports this symbol at startup.  The previous
    hotfix accidentally removed it, which caused the API container to restart with
    ImportError before serving the dashboard.  The installer keeps HTTP and
    WebSocket dashboard consumers on the same canonical settled-only source.
    """
    api_module._build_dashboard_snapshot = (  # noqa: SLF001 - intentional patch point
        lambda account_type: build_consistent_dashboard_snapshot(api_module, account_type)
    )
    api_module.reference_performance_summary = (  # retained for older callers
        lambda *, account_type, start, end, simulated_base_stake=BASELINE_STAKE: reference_performance_summary(
            api_module,
            account_type=account_type,
            start=start,
            end=end,
            simulated_base_stake=simulated_base_stake,
        )
    )
