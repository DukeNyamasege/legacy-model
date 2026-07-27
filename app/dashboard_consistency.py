from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.models import Trade


REFERENCE_KEY_PREFIX = "dashboard_reference_managed_account:"
BASELINE_STAKE = 0.50


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _reference_context(api_module: Any, account_type: str):
    """Return one stable execution path for the selected account environment.

    The previous dashboard mixed a highest-balance account, canonical model rows,
    and an observed Martingale cohort. We deliberately choose one account once,
    persist that choice, and keep every model-performance card on that exact
    execution sequence until the reference account is no longer available.
    """
    target = api_module.normalize_account_type(account_type)
    contexts = list(api_module.environment_account_contexts(target))
    if not contexts:
        return None

    preference_key = f"{REFERENCE_KEY_PREFIX}{target}"
    saved = api_module.REPOSITORY.runtime_preference(preference_key).strip()
    if saved:
        try:
            saved_id = int(saved)
        except ValueError:
            saved_id = -1
        matched = next(
            (context for context in contexts if int(context[0].id) == saved_id),
            None,
        )
        if matched is not None:
            return matched

    enabled = [context for context in contexts if bool(getattr(context[0], "enabled", False))]
    candidates = enabled or contexts

    # Match the dashboard path users were already treating as the trustworthy
    # reference: the strongest funded account in the selected environment. The
    # chosen managed-account id is then persisted so later balance changes cannot
    # silently switch the model ledger to somebody else's execution history.
    def balance(context: tuple[Any, dict, str]) -> float:
        try:
            return float(api_module.REPOSITORY.account_summary(context[2]).get("balance") or 0.0)
        except Exception:
            return 0.0

    selected = max(candidates, key=lambda context: (balance(context), -int(context[0].id)))
    api_module.REPOSITORY.set_runtime_preference(preference_key, str(int(selected[0].id)))
    return selected


def _reference_trade_rows(
    repository: Any,
    managed_account_id: int,
    *,
    start: datetime,
    end: datetime,
) -> list[Trade]:
    start = _aware(start)
    end = _aware(end)
    purchased_at = func.coalesce(Trade.provider_purchase_time, Trade.purchase_time)
    with repository.database.session() as session:
        rows = session.scalars(
            select(Trade)
            .where(
                Trade.managed_account_id == int(managed_account_id),
                Trade.settlement_time.is_not(None),
                Trade.outcome.in_(["WIN", "LOSS"]),
                Trade.profit.is_not(None),
                Trade.buy_price.is_not(None),
                Trade.buy_price > 0,
                *repository._current_run_trade_filter(),
                purchased_at >= start,
                purchased_at < end,
            )
            .order_by(purchased_at.asc(), Trade.id.asc())
        ).all()
    return list(rows)


def _canonical_payload_from_reference(rows: list[Trade]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for trade in rows:
        buy_price = max(0.01, float(trade.buy_price or 0.0))
        profit = float(trade.profit or 0.0)
        payout = float(trade.payout or 0.0)
        purchased_at = _aware(trade.provider_purchase_time or trade.purchase_time)
        if payout > buy_price:
            proposal_profit_ratio = max(0.01, (payout - buy_price) / buy_price)
        elif profit > 0:
            proposal_profit_ratio = max(0.01, profit / buy_price)
        else:
            proposal_profit_ratio = 0.90
        payload.append(
            {
                "signal_id": str(trade.signal_id or trade.id),
                "symbol": "",
                "direction": "",
                "contract_type": "",
                "duration_ticks": int(trade.contract_duration or 1),
                "signal_timestamp": purchased_at.isoformat(),
                "settlement_timestamp": (
                    _aware(trade.settlement_time).isoformat()
                    if trade.settlement_time
                    else None
                ),
                "outcome": str(trade.outcome or "").upper(),
                "is_virtual": False,
                "reference_base_stake": BASELINE_STAKE,
                "fixed_stake_profit": round(
                    profit * BASELINE_STAKE / buy_price,
                    8,
                ),
                "execution_source": "reference_execution",
                "expected_profit_ratio": proposal_profit_ratio,
                "martingale_stake": buy_price,
                "martingale_profit": profit,
                "martingale_level": 0,
                "recovery_debt_before": 0.0,
                "recovery_debt_after": 0.0,
            }
        )
    return payload


def _actual_path_metrics(rows: list[Trade]) -> dict[str, float]:
    pnl = 0.0
    peak = 0.0
    max_drawdown = 0.0
    stake_volume = 0.0
    maximum_stake = 0.0
    for trade in rows:
        stake = max(0.0, float(trade.buy_price or 0.0))
        profit = float(trade.profit or 0.0)
        stake_volume += stake
        maximum_stake = max(maximum_stake, stake)
        pnl += profit
        peak = max(peak, pnl)
        max_drawdown = max(max_drawdown, peak - pnl)
    return {
        "pnl": round(pnl, 2),
        "stake_volume": round(stake_volume, 2),
        "maximum_stake": round(maximum_stake, 2),
        "current_drawdown": round(max(0.0, peak - pnl), 2),
        "max_drawdown": round(max_drawdown, 2),
    }


def reference_performance_summary(
    api_module: Any,
    *,
    account_type: str,
    start: datetime,
    end: datetime,
    simulated_base_stake: float = BASELINE_STAKE,
) -> dict[str, Any]:
    target = api_module.normalize_account_type(account_type)
    context = _reference_context(api_module, target)
    if context is None:
        return api_module.REPOSITORY.system_performance_summary(
            start=start,
            end=end,
            simulated_base_stake=BASELINE_STAKE,
            include_virtual=False,
        )

    row, _payload, account_id = context
    managed_account_id = int(row.id)
    rows = _reference_trade_rows(
        api_module.REPOSITORY,
        managed_account_id,
        start=start,
        end=end,
    )
    trade_payload = _canonical_payload_from_reference(rows)

    # Baseline cards are always the same $0.50 replay, regardless of who is
    # viewing the dashboard or what stake their personal account uses.
    baseline = api_module.REPOSITORY.system_performance_summary(
        start=start,
        end=end,
        simulated_base_stake=BASELINE_STAKE,
        include_virtual=False,
        trades=trade_payload,
        observed_executions=[],
    )

    selected_stake = min(1000.0, max(BASELINE_STAKE, float(simulated_base_stake)))
    simulation = (
        baseline
        if abs(selected_stake - BASELINE_STAKE) < 1e-9
        else api_module.REPOSITORY.system_performance_summary(
            start=start,
            end=end,
            simulated_base_stake=selected_stake,
            include_virtual=False,
            trades=trade_payload,
            observed_executions=[],
        )
    )
    actual = _actual_path_metrics(rows)
    settled_count = int(baseline.get("wins") or 0) + int(baseline.get("losses") or 0)
    flat_staked = BASELINE_STAKE * settled_count

    baseline.update(
        {
            "source": "stable_reference_execution_ledger",
            "accounting_contract": "one_reference_sequence_one_snapshot",
            "reference_account": api_module.REPOSITORY.account_summary(account_id).get("account", ""),
            "reference_account_type": target,
            "reference_managed_account_id": managed_account_id,
            "total_trades": settled_count,
            "viewer_actual_trades": settled_count,
            "simulated_trades": 0,
            # 'With Martingale' is the observed monetary path of this exact
            # reference sequence. This is the number the user identified as the
            # trustworthy Today's Model P/L.
            "martingale_pnl": actual["pnl"],
            "observed_martingale_pnl": actual["pnl"],
            "maximum_martingale_stake": actual["maximum_stake"],
            "observed_maximum_stake": actual["maximum_stake"],
            "observed_martingale_stake_volume": actual["stake_volume"],
            "martingale_return_pct": round(
                actual["pnl"] / actual["stake_volume"] * 100.0,
                2,
            ) if actual["stake_volume"] else 0.0,
            "max_drawdown_martingale": actual["max_drawdown"],
            "current_drawdown_martingale": actual["current_drawdown"],
            "max_drawdown_martingale_pct": round(
                actual["max_drawdown"] / actual["stake_volume"] * 100.0,
                2,
            ) if actual["stake_volume"] else 0.0,
            "current_drawdown_martingale_pct": round(
                actual["current_drawdown"] / actual["stake_volume"] * 100.0,
                2,
            ) if actual["stake_volume"] else 0.0,
            "martingale_cohort_size": 1 if rows else 0,
            "martingale_population": 1 if rows else 0,
            "martingale_cohort_confidence": "REFERENCE_PATH" if rows else "NO_DATA",
            "martingale_cohort_trade_count": settled_count,
            "martingale_cohort_status": "REFERENCE_PATH" if rows else "NO_DATA",
            "martingale_cohort_sample_sufficient": bool(rows),
            "martingale_dominant_signature": "stable-reference-account",
            # Baseline/Without-Martingale is always a $0.50 normalization of the
            # same trades and outcomes, never a second population.
            "flat_stake": BASELINE_STAKE,
            "simulated_base_stake": BASELINE_STAKE,
            "fixed_return_pct": round(
                float(baseline.get("fixed_pnl") or 0.0) / flat_staked * 100.0,
                2,
            ) if flat_staked else 0.0,
            # Simulator fields replay the exact same sequence at the requested
            # hypothetical stake. The main baseline cards never use these.
            "simulation_stake": round(selected_stake, 2),
            "simulated_fixed_pnl": round(float(simulation.get("fixed_pnl") or 0.0), 2),
            "simulated_martingale_pnl": round(
                float(simulation.get("simulated_martingale_pnl") or 0.0),
                2,
            ),
            "simulated_maximum_martingale_stake": float(
                simulation.get("simulated_maximum_martingale_stake") or selected_stake
            ),
        }
    )
    version_material = "|".join(
        f"{trade.id}:{trade.outcome}:{float(trade.profit or 0.0):.8f}:"
        f"{float(trade.buy_price or 0.0):.8f}"
        for trade in rows
    )
    baseline["model_data_version"] = hashlib.sha256(
        version_material.encode("utf-8")
    ).hexdigest()[:16]
    return baseline


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


def build_consistent_dashboard_snapshot(api_module: Any, account_type: str):
    target = api_module.normalize_account_type(account_type)
    context = _reference_context(api_module, target)
    if context is None:
        return api_module._dashboard_consistency_original_builder(target)

    as_of = datetime.now(timezone.utc)
    periods = _periods(api_module, as_of)
    performance = {
        name: reference_performance_summary(
            api_module,
            account_type=target,
            start=start,
            end=end,
            simulated_base_stake=BASELINE_STAKE,
        )
        for name, (start, end) in periods.items()
    }
    today = performance["today"]
    if int(today["total_trades"]) != int(today["wins"]) + int(today["losses"]):
        raise RuntimeError("dashboard reference totals invariant violated")

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
        "ledger": "stable_reference_execution_ledger",
        "reference_account": today.get("reference_account", ""),
        "reference_account_type": target,
        "settled_trades": int(today["total_trades"]),
        "wins_plus_losses": int(today["wins"]) + int(today["losses"]),
        "pnl": float(today["martingale_pnl"]),
        "as_of": as_of.isoformat(),
        "invariant_ok": True,
    }
    watermark = {
        "reference_account": today.get("reference_account", ""),
        "reference_trade_count": int(today["total_trades"]),
        "reference_model_data_version": today.get("model_data_version", ""),
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

    viewer = api_module.get_current_account(request)
    account_type = api_module.normalize_account_type(
        viewer.get("account_type") if viewer else "demo"
    )
    snapshot = api_module.dashboard_summary(account_type=account_type)
    cached = (snapshot.get("system_performance") or {}).get(normalized) or {}
    if cached.get("start") and cached.get("end"):
        start = datetime.fromisoformat(str(cached["start"]))
        end = datetime.fromisoformat(str(cached["end"]))
    else:
        start, end = api_module._system_period_bounds(normalized)

    response = reference_performance_summary(
        api_module,
        account_type=account_type,
        start=start,
        end=end,
        simulated_base_stake=simulated_base_stake,
    )
    response.update(
        {
            "period": normalized,
            "timezone": str(api_module._system_reporting_timezone()),
            "minimum_stake": BASELINE_STAKE,
            "maximum_stake": 1000.0,
            "snapshot_version": int(snapshot.get("snapshot_version") or 0),
            "snapshot_generated_at": snapshot.get("generated_at"),
        }
    )
    return response


def install_dashboard_consistency(api_module: Any) -> None:
    if getattr(api_module, "_dashboard_consistency_installed", False):
        return
    api_module._dashboard_consistency_original_builder = api_module._build_dashboard_snapshot

    def builder(account_type: str):
        return build_consistent_dashboard_snapshot(api_module, account_type)

    api_module._build_dashboard_snapshot = builder
    api_module._dashboard_consistency_installed = True
