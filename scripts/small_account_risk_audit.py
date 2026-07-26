from __future__ import annotations

import argparse
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.config import load_test2_config
from app.database import Database

EAT = ZoneInfo("Africa/Nairobi")


def money(value: float) -> str:
    return f"${value:,.2f}"


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def as_eat(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(EAT)


def flatten_features(value: Any, prefix: str = "feature") -> dict[str, Any]:
    output: dict[str, Any] = {}
    if not isinstance(value, dict):
        return output
    for key, raw in value.items():
        name = f"{prefix}.{key}"
        if isinstance(raw, bool):
            output[name] = int(raw)
        elif isinstance(raw, (int, float)) and math.isfinite(float(raw)):
            output[name] = float(raw)
        elif isinstance(raw, str) and len(raw) <= 48:
            output[name] = raw
        elif isinstance(raw, dict):
            for child_key, child_value in raw.items():
                child_name = f"{name}.{child_key}"
                if isinstance(child_value, bool):
                    output[child_name] = int(child_value)
                elif isinstance(child_value, (int, float)) and math.isfinite(float(child_value)):
                    output[child_name] = float(child_value)
                elif isinstance(child_value, str) and len(child_value) <= 48:
                    output[child_name] = child_value
    return output


def longest_streak(rows: list[dict[str, Any]], target: str = "LOSS") -> tuple[int, int, int]:
    best_len = 0
    best_start = -1
    best_end = -1
    current_len = 0
    current_start = -1
    for index, row in enumerate(rows):
        if str(row.get("outcome", "")).upper() == target:
            if current_len == 0:
                current_start = index
            current_len += 1
            if current_len > best_len:
                best_len = current_len
                best_start = current_start
                best_end = index
        else:
            current_len = 0
            current_start = -1
    return best_len, best_start, best_end


def max_drawdown(pnls: Iterable[float], starting_balance: float = 0.0) -> tuple[float, float, float]:
    equity = starting_balance
    peak = starting_balance
    min_equity = starting_balance
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        min_equity = min(min_equity, equity)
        worst = max(worst, peak - equity)
    return worst, equity, min_equity


@dataclass
class SimulationResult:
    name: str
    starting_balance: float
    ending_balance: float
    net_profit: float
    max_drawdown: float
    minimum_balance: float
    max_stake: float
    total_staked: float
    wins: int
    losses: int
    ruined: bool

    @property
    def efficiency(self) -> float:
        return self.net_profit / self.total_staked if self.total_staked > 0 else 0.0


def canonical_profit_per_dollar(row: dict[str, Any]) -> float:
    base = safe_float(row.get("reference_base_stake"), 0.50)
    pnl = safe_float(row.get("fixed_stake_profit"), 0.0)
    return pnl / base if base > 0 else 0.0


def simulate_flat(rows: list[dict[str, Any]], *, starting_balance: float, stake: float) -> SimulationResult:
    balance = starting_balance
    peak = balance
    min_balance = balance
    max_dd = 0.0
    total_staked = 0.0
    wins = losses = 0
    ruined = False
    for row in rows:
        if balance < stake:
            ruined = True
            break
        pnl = canonical_profit_per_dollar(row) * stake
        total_staked += stake
        balance += pnl
        peak = max(peak, balance)
        min_balance = min(min_balance, balance)
        max_dd = max(max_dd, peak - balance)
        if str(row.get("outcome", "")).upper() == "WIN":
            wins += 1
        else:
            losses += 1
    return SimulationResult(
        name=f"flat_{stake:.2f}",
        starting_balance=starting_balance,
        ending_balance=balance,
        net_profit=balance - starting_balance,
        max_drawdown=max_dd,
        minimum_balance=min_balance,
        max_stake=stake,
        total_staked=total_staked,
        wins=wins,
        losses=losses,
        ruined=ruined,
    )


def simulate_capped_recovery(
    rows: list[dict[str, Any]],
    *,
    starting_balance: float,
    base_stake: float,
    cap_stake: float,
) -> SimulationResult:
    balance = starting_balance
    peak = balance
    min_balance = balance
    max_dd = 0.0
    total_staked = 0.0
    max_stake = base_stake
    debt = 0.0
    wins = losses = 0
    ruined = False

    for row in rows:
        profit_ratio = max(0.01, safe_float(row.get("expected_profit_ratio"), 0.80))
        required = debt / profit_ratio if debt > 0 else base_stake
        stake = min(cap_stake, max(base_stake, math.ceil(required * 100) / 100))
        if balance < stake:
            ruined = True
            break
        outcome = str(row.get("outcome", "")).upper()
        if outcome == "WIN":
            pnl = stake * profit_ratio
            debt = max(0.0, debt - pnl)
            wins += 1
        else:
            pnl = -stake
            debt += stake
            losses += 1
        total_staked += stake
        max_stake = max(max_stake, stake)
        balance += pnl
        peak = max(peak, balance)
        min_balance = min(min_balance, balance)
        max_dd = max(max_dd, peak - balance)

    return SimulationResult(
        name=f"capped_{base_stake:.2f}_to_{cap_stake:.2f}",
        starting_balance=starting_balance,
        ending_balance=balance,
        net_profit=balance - starting_balance,
        max_drawdown=max_dd,
        minimum_balance=min_balance,
        max_stake=max_stake,
        total_staked=total_staked,
        wins=wins,
        losses=losses,
        ruined=ruined,
    )


def query_rows(db: Database, sql: str) -> list[dict[str, Any]]:
    with db.engine.connect() as connection:
        result = connection.execute(text(sql))
        return [dict(row) for row in result.mappings().all()]


def load_data(db: Database) -> dict[str, list[dict[str, Any]]]:
    canonical = query_rows(
        db,
        """
        SELECT smt.id, smt.run_id, smt.signal_id, smt.symbol, smt.direction,
               smt.duration_ticks, smt.signal_timestamp, smt.entry_timestamp,
               smt.settlement_timestamp, smt.outcome, smt.reference_base_stake,
               smt.expected_profit_ratio, smt.fixed_stake_profit,
               smt.martingale_stake, smt.martingale_profit, smt.martingale_level,
               smt.recovery_debt_before, smt.recovery_debt_after,
               ds.strategy_version, ds.quality_score, ds.validated_edge,
               ds.feature_values
        FROM system_model_trades smt
        LEFT JOIN directional_signals ds ON ds.signal_id = smt.signal_id
        WHERE smt.is_virtual = FALSE
          AND smt.outcome IN ('WIN', 'LOSS')
        ORDER BY smt.signal_timestamp, smt.id
        """,
    )
    actual = query_rows(
        db,
        """
        SELECT t.id, t.managed_account_id, t.signal_id, t.account_id_masked,
               COALESCE(t.provider_purchase_time, t.purchase_time) AS purchase_time,
               t.settlement_time, t.buy_price, t.payout, t.profit, t.outcome,
               t.model_version, smt.symbol, ds.strategy_version
        FROM trades t
        LEFT JOIN system_model_trades smt ON smt.signal_id = t.signal_id
        LEFT JOIN directional_signals ds ON ds.signal_id = t.signal_id
        WHERE t.outcome IN ('WIN', 'LOSS')
          AND t.buy_price IS NOT NULL
          AND t.profit IS NOT NULL
        ORDER BY t.managed_account_id,
                 COALESCE(t.provider_purchase_time, t.purchase_time), t.id
        """,
    )
    shadow = query_rows(
        db,
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE outcome = 'WIN') AS wins,
               COUNT(*) FILTER (WHERE outcome = 'LOSS') AS losses,
               MIN(created_at) AS earliest,
               MAX(COALESCE(settled_at, created_at)) AS latest
        FROM shadow_contracts
        WHERE outcome IN ('WIN', 'LOSS')
        """,
    )
    runs = query_rows(
        db,
        """
        SELECT run_name, model_version, strategy_version, start_time,
               end_time, status, environment
        FROM test_runs
        ORDER BY start_time
        """,
    )
    return {"canonical": canonical, "actual": actual, "shadow": shadow, "runs": runs}


def describe_streaks(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    streaks: list[dict[str, Any]] = []
    start = None
    for index, row in enumerate(rows + [{"outcome": "END"}]):
        if str(row.get("outcome", "")).upper() == "LOSS":
            if start is None:
                start = index
            continue
        if start is None:
            continue
        chunk = rows[start:index]
        streaks.append(
            {
                "length": len(chunk),
                "start": as_eat(chunk[0].get("signal_timestamp")),
                "end": as_eat(chunk[-1].get("signal_timestamp")),
                "symbols": dict(Counter(str(x.get("symbol") or "unknown") for x in chunk)),
                "max_martingale_stake": max(safe_float(x.get("martingale_stake"), 0.0) for x in chunk),
                "max_recovery_debt_after": max(safe_float(x.get("recovery_debt_after"), 0.0) for x in chunk),
            }
        )
        start = None
    streaks.sort(key=lambda item: (item["length"], item["max_martingale_stake"]), reverse=True)
    return streaks[:limit]


def actual_account_risk(actual: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in actual:
        key = str(row.get("managed_account_id") or row.get("account_id_masked") or "unknown")
        grouped[key].append(row)

    account_summaries: list[dict[str, Any]] = []
    spike_rows: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        stakes = [safe_float(row.get("buy_price"), 0.0) for row in rows if safe_float(row.get("buy_price"), 0.0) > 0]
        if not stakes:
            continue
        floor = min(stakes)
        max_stake = max(stakes)
        streak_len, _, _ = longest_streak(rows, "LOSS")
        pnl = sum(safe_float(row.get("profit"), 0.0) for row in rows)
        account_summaries.append(
            {
                "account": rows[0].get("account_id_masked") or key,
                "trades": len(rows),
                "floor": floor,
                "max_stake": max_stake,
                "max_to_floor": max_stake / floor if floor > 0 else 0.0,
                "longest_loss_streak": streak_len,
                "profit": pnl,
            }
        )
        for row in rows:
            stake = safe_float(row.get("buy_price"), 0.0)
            if floor > 0 and (stake >= 5.0 or stake / floor >= 10):
                spike_rows.append(
                    {
                        "account": row.get("account_id_masked") or key,
                        "time": as_eat(row.get("purchase_time")),
                        "stake": stake,
                        "floor": floor,
                        "multiple": stake / floor,
                        "outcome": row.get("outcome"),
                        "profit": safe_float(row.get("profit"), 0.0),
                        "symbol": row.get("symbol"),
                    }
                )

    account_summaries.sort(key=lambda x: (x["max_stake"], x["max_to_floor"]), reverse=True)
    spike_rows.sort(key=lambda x: x["stake"], reverse=True)
    return {
        "accounts": account_summaries,
        "spikes": spike_rows,
        "max_observed_stake": max((x["max_stake"] for x in account_summaries), default=0.0),
        "median_account_max_stake": statistics.median([x["max_stake"] for x in account_summaries]) if account_summaries else 0.0,
    }


def grouped_performance(rows: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row))].append(row)
    output = []
    for key, items in groups.items():
        wins = sum(1 for x in items if str(x.get("outcome", "")).upper() == "WIN")
        flat_pnl = sum(safe_float(x.get("fixed_stake_profit"), 0.0) for x in items)
        output.append(
            {
                "key": key,
                "trades": len(items),
                "win_rate": wins / len(items) if items else 0.0,
                "flat_pnl": flat_pnl,
            }
        )
    output.sort(key=lambda x: (x["flat_pnl"], x["trades"]))
    return output


def build_ml_rows(canonical: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int]]:
    X: list[dict[str, Any]] = []
    y: list[int] = []
    for row in canonical:
        timestamp = as_eat(row.get("signal_timestamp"))
        if timestamp is None:
            continue
        features: dict[str, Any] = {
            "symbol": str(row.get("symbol") or "unknown"),
            "direction": str(row.get("direction") or "unknown"),
            "strategy_version": str(row.get("strategy_version") or "unknown"),
            "hour_eat": timestamp.hour,
            "weekday_eat": timestamp.weekday(),
            "quality_score": safe_float(row.get("quality_score"), 0.0),
            "validated_edge": safe_float(row.get("validated_edge"), 0.0),
            "expected_profit_ratio": safe_float(row.get("expected_profit_ratio"), 0.0),
        }
        features.update(flatten_features(row.get("feature_values")))
        X.append(features)
        y.append(1 if str(row.get("outcome", "")).upper() == "LOSS" else 0)
    return X, y


def ml_analysis(canonical: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import accuracy_score, roc_auc_score
    except Exception as exc:
        return {"available": False, "reason": f"scikit-learn unavailable: {type(exc).__name__}: {exc}"}

    X_dict, y = build_ml_rows(canonical)
    if len(y) < 120 or len(set(y)) < 2:
        return {"available": False, "reason": f"insufficient canonical samples for ML: {len(y)}"}

    split = max(80, int(len(y) * 0.70))
    if split >= len(y) - 20:
        split = len(y) - 20
    vectorizer = DictVectorizer(sparse=False)
    X_train = vectorizer.fit_transform(X_dict[:split])
    X_test = vectorizer.transform(X_dict[split:])
    y_train = y[:split]
    y_test = y[split:]

    model = RandomForestClassifier(
        n_estimators=350,
        max_depth=8,
        min_samples_leaf=8,
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)
    probabilities = model.predict_proba(X_test)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    accuracy = float(accuracy_score(y_test, predictions))
    auc = float(roc_auc_score(y_test, probabilities)) if len(set(y_test)) == 2 else float("nan")

    names = vectorizer.get_feature_names_out()
    importances = sorted(
        ((str(name), float(importance)) for name, importance in zip(names, model.feature_importances_)),
        key=lambda item: item[1],
        reverse=True,
    )[:18]

    test_rows = canonical[split:]
    filters = []
    ordered = sorted(range(len(probabilities)), key=lambda i: probabilities[i], reverse=True)
    for skip_fraction in (0.10, 0.20, 0.30, 0.40):
        skip_count = int(len(ordered) * skip_fraction)
        skipped = set(ordered[:skip_count])
        kept = [row for i, row in enumerate(test_rows) if i not in skipped]
        if not kept:
            continue
        wins = sum(1 for row in kept if str(row.get("outcome", "")).upper() == "WIN")
        pnl = [safe_float(row.get("fixed_stake_profit"), 0.0) for row in kept]
        dd, ending, _ = max_drawdown(pnl)
        longest, _, _ = longest_streak(kept, "LOSS")
        filters.append(
            {
                "skip_fraction": skip_fraction,
                "trades_kept": len(kept),
                "win_rate": wins / len(kept),
                "flat_pnl": ending,
                "max_drawdown": dd,
                "longest_loss_streak": longest,
            }
        )

    return {
        "available": True,
        "samples": len(y),
        "train_samples": len(y_train),
        "test_samples": len(y_test),
        "accuracy": accuracy,
        "roc_auc": auc,
        "top_features": importances,
        "risk_filters": filters,
    }


def render_report(data: dict[str, list[dict[str, Any]]], ml: dict[str, Any]) -> str:
    canonical = data["canonical"]
    actual = data["actual"]
    runs = data["runs"]
    shadow = data["shadow"][0] if data["shadow"] else {}
    lines: list[str] = [
        "# Small-Account Risk & Strategy Audit",
        "",
        "**Objective:** preserve small accounts first. Prefer low, controlled stakes and lower drawdown over headline profit. No recommendation in this report changes the live strategy automatically.",
        "",
    ]

    if not canonical:
        lines.append("No settled canonical monetary trades were found.")
        return "\n".join(lines)

    wins = sum(1 for row in canonical if str(row.get("outcome", "")).upper() == "WIN")
    losses = len(canonical) - wins
    flat_pnl = sum(safe_float(row.get("fixed_stake_profit"), 0.0) for row in canonical)
    mg_pnl = sum(safe_float(row.get("martingale_profit"), 0.0) for row in canonical)
    mg_max = max(safe_float(row.get("martingale_stake"), 0.0) for row in canonical)
    max_level = max(int(row.get("martingale_level") or 0) for row in canonical)
    earliest = as_eat(canonical[0].get("signal_timestamp"))
    latest = as_eat(canonical[-1].get("signal_timestamp"))
    lines.extend(
        [
            "## Historical coverage",
            f"- Canonical monetary trades: **{len(canonical):,}**",
            f"- Actual account executions: **{len(actual):,}**",
            f"- Canonical period: **{earliest} → {latest} EAT**",
            f"- Test runs retained in DB: **{len(runs):,}**",
            f"- Settled shadow contracts retained: **{int(shadow.get('total') or 0):,}** ({int(shadow.get('wins') or 0):,} wins / {int(shadow.get('losses') or 0):,} losses)",
            "",
            "## Canonical performance",
            f"- Wins / losses: **{wins:,} / {losses:,}**",
            f"- Win rate: **{pct(wins / len(canonical))}**",
            f"- Flat $0.50 ledger P/L: **{money(flat_pnl)}**",
            f"- Historical Martingale ledger P/L: **{money(mg_pnl)}**",
            f"- Largest canonical recovery stake: **{money(mg_max)}**",
            f"- Highest canonical recovery level: **{max_level}**",
            "",
            "## Worst canonical losing streaks",
        ]
    )

    for index, streak in enumerate(describe_streaks(canonical), 1):
        lines.append(
            f"{index}. **{streak['length']} losses** | {streak['start']} → {streak['end']} EAT | "
            f"max recovery stake {money(streak['max_martingale_stake'])} | debt peak {money(streak['max_recovery_debt_after'])} | "
            f"markets {streak['symbols']}"
        )

    risk = actual_account_risk(actual)
    lines.extend(
        [
            "",
            "## Actual account stake escalation",
            f"- Largest actual purchase observed: **{money(risk['max_observed_stake'])}**",
            f"- Median of each account's maximum stake: **{money(risk['median_account_max_stake'])}**",
            f"- Stake spikes (≥$5 or ≥10× observed account floor): **{len(risk['spikes']):,}**",
        ]
    )
    for item in risk["spikes"][:15]:
        lines.append(
            f"  - {item['time']} EAT | {item['account']} | {item['symbol']} | stake {money(item['stake'])} = "
            f"{item['multiple']:.1f}× observed floor {money(item['floor'])} | {item['outcome']} | P/L {money(item['profit'])}"
        )

    lines.extend(["", "## Small-account stress tests on the same canonical outcomes"])
    simulations: list[SimulationResult] = []
    for balance in (10.0, 20.0, 50.0):
        simulations.extend(
            [
                simulate_flat(canonical, starting_balance=balance, stake=0.35),
                simulate_flat(canonical, starting_balance=balance, stake=0.50),
                simulate_capped_recovery(canonical, starting_balance=balance, base_stake=0.35, cap_stake=max(0.50, balance * 0.05)),
                simulate_capped_recovery(canonical, starting_balance=balance, base_stake=0.35, cap_stake=max(1.00, balance * 0.10)),
            ]
        )
    for sim in simulations:
        lines.append(
            f"- start {money(sim.starting_balance)} | {sim.name} | net {money(sim.net_profit)} | end {money(sim.ending_balance)} | "
            f"max DD {money(sim.max_drawdown)} | min balance {money(sim.minimum_balance)} | max stake {money(sim.max_stake)} | "
            f"stake efficiency {sim.efficiency:.4f} | ruined={'YES' if sim.ruined else 'NO'}"
        )

    market = grouped_performance(canonical, lambda row: row.get("symbol") or "unknown")
    hour = grouped_performance(
        canonical,
        lambda row: f"{(as_eat(row.get('signal_timestamp')) or datetime.now(EAT)).hour:02d}:00",
    )
    lines.extend(["", "## Weakest markets by flat canonical P/L"])
    for item in market[:8]:
        lines.append(f"- {item['key']}: {item['trades']} trades | WR {pct(item['win_rate'])} | flat P/L {money(item['flat_pnl'])}")
    lines.extend(["", "## Weakest EAT hours by flat canonical P/L"])
    for item in hour[:8]:
        lines.append(f"- {item['key']}: {item['trades']} trades | WR {pct(item['win_rate'])} | flat P/L {money(item['flat_pnl'])}")

    lines.extend(["", "## Machine-learning loss-risk audit"])
    if not ml.get("available"):
        lines.append(f"- ML not run: {ml.get('reason', 'unknown reason')}")
    else:
        lines.append(f"- Time-ordered split: {ml['train_samples']} train / {ml['test_samples']} holdout samples")
        lines.append(f"- Holdout accuracy: **{pct(ml['accuracy'])}**")
        auc = ml.get("roc_auc")
        lines.append(f"- Holdout ROC-AUC: **{auc:.3f}**" if math.isfinite(auc) else "- Holdout ROC-AUC: unavailable")
        lines.append("- Strongest pre-trade loss-risk features:")
        for name, importance in ml["top_features"][:12]:
            lines.append(f"  - {name}: {importance:.4f}")
        lines.append("- Holdout what-if filters (skip highest predicted-loss-risk trades):")
        for item in ml["risk_filters"]:
            lines.append(
                f"  - skip {int(item['skip_fraction']*100)}% | keep {item['trades_kept']} | WR {pct(item['win_rate'])} | "
                f"flat P/L {money(item['flat_pnl'])} | max DD {money(item['max_drawdown'])} | longest loss streak {item['longest_loss_streak']}"
            )

    lines.extend(
        [
            "",
            "## Decision rule for the next strategy version",
            "1. **Do not optimize for win rate alone.** Optimize for survival, maximum drawdown, maximum stake and profit per dollar staked.",
            "2. For the $10 target account, treat **$0.50 as 5% of capital** and **$1.00 as 10%**. Stakes that require $5-$100 are incompatible with the small-account product goal.",
            "3. Prefer **flat or tightly capped recovery** when it materially lowers drawdown and stake spikes, even if total profit falls.",
            "4. Any market/hour/feature restriction must first improve the **out-of-sample holdout** risk metrics. Do not tighten a rule solely because it fits historical losses.",
            "5. AI/ML may recommend candidate filters; **manual/Codex review must implement them as a new strategy version**, scheduled for the next 00:00 EAT boundary. Never self-modify live execution.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="", help="Optional report path. The report is always printed to stdout.")
    args = parser.parse_args()
    config_path = os.getenv("DERIV_BOT_CONFIG", "/app/config.yaml")
    config = load_test2_config(config_path)
    db = Database(config.database_url)
    data = load_data(db)
    ml = ml_analysis(data["canonical"])
    report = render_report(data, ml)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
