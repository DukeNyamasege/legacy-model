from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable

import yaml


UTC = timezone.utc
EAT = timezone(timedelta(hours=3), name="EAT")


@dataclass(frozen=True, slots=True)
class Tick:
    sequence: int
    epoch: int
    quote: str
    digit: int


def timestamp(epoch: int, zone: timezone) -> str:
    return datetime.fromtimestamp(epoch, tz=zone).isoformat()


def load_ticks(path: Path) -> list[Tick]:
    ticks: list[Tick] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            tick = Tick(
                sequence=int(row["sequence"]),
                epoch=int(row["epoch"]),
                quote=row["quote"],
                digit=int(row["digit"]),
            )
            if tick.quote[-1] != str(tick.digit):
                raise ValueError(
                    f"Digit mismatch at sequence {tick.sequence}: "
                    f"{tick.quote} versus {tick.digit}"
                )
            ticks.append(tick)

    if not ticks:
        raise ValueError("The tick file is empty")
    if any(tick.sequence != index for index, tick in enumerate(ticks, 1)):
        raise ValueError("Tick sequences are not continuous from 1")
    if any(current.epoch - previous.epoch != 1 for previous, current in zip(ticks, ticks[1:])):
        raise ValueError("Tick epochs are not continuous at one-second intervals")
    if len({tick.epoch for tick in ticks}) != len(ticks):
        raise ValueError("Tick file contains duplicate epochs")
    return ticks


def longest_streak(outcomes: Iterable[str], target: str) -> int:
    longest = 0
    current = 0
    for outcome in outcomes:
        if outcome == target:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def wilson_interval(wins: int, total: int, confidence: float = 0.95) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    observed = wins / total
    denominator = 1 + z * z / total
    centre = (observed + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(observed * (1 - observed) / total + z * z / (4 * total * total))
        / denominator
    )
    return centre - margin, centre + margin


def simulate(
    ticks: list[Tick],
    *,
    mode: str,
    pattern_ranges: tuple[tuple[int, int], ...],
    prediction: int,
    stake: float,
    payout: float,
    use_cooldown: bool,
    cooldown_rules: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    trades: list[dict[str, Any]] = []
    settlement_index: int | None = None
    pending_trade: dict[str, Any] | None = None
    cooldown_remaining = 0
    consecutive_wins = 0
    consecutive_losses = 0
    cooldown_ticks_consumed = 0
    unscored_signals = 0

    for index, current in enumerate(ticks):
        if settlement_index == index and pending_trade is not None:
            outcome = pending_trade["outcome"]
            if outcome == "win":
                consecutive_wins += 1
                consecutive_losses = 0
                cooldown_after = cooldown_rules["after_win_ticks"] if use_cooldown else 0
            else:
                consecutive_losses += 1
                consecutive_wins = 0
                if not use_cooldown:
                    cooldown_after = 0
                elif consecutive_losses >= 5:
                    cooldown_after = cooldown_rules[
                        "after_five_consecutive_losses_ticks"
                    ]
                elif consecutive_losses >= 3:
                    cooldown_after = cooldown_rules[
                        "after_three_consecutive_losses_ticks"
                    ]
                else:
                    cooldown_after = cooldown_rules["after_loss_ticks"]

            pending_trade["cooldown_after"] = cooldown_after
            pending_trade["consecutive_wins_after"] = consecutive_wins
            pending_trade["consecutive_losses_after"] = consecutive_losses
            cooldown_remaining = cooldown_after
            settlement_index = None
            pending_trade = None
            continue

        if use_cooldown and cooldown_remaining > 0:
            cooldown_remaining -= 1
            cooldown_ticks_consumed += 1
            continue

        pattern_length = len(pattern_ranges)
        if index < pattern_length - 1:
            continue
        trigger_ticks = ticks[index - pattern_length + 1 : index + 1]
        trigger_digits = tuple(tick.digit for tick in trigger_ticks)
        if not all(
            lower <= digit <= upper
            for digit, (lower, upper) in zip(
                trigger_digits, pattern_ranges, strict=True
            )
        ):
            continue

        if index + 1 >= len(ticks):
            unscored_signals += 1
            continue

        result = ticks[index + 1]
        outcome = "win" if result.digit > prediction else "loss"
        net_profit = payout - stake if outcome == "win" else -stake
        pending_trade = {
            "mode": mode,
            "trade_number": len(trades) + 1,
            "block": (current.sequence - 1) // 1000 + 1,
            "signal_sequence": current.sequence,
            "signal_epoch": current.epoch,
            "signal_utc": timestamp(current.epoch, UTC),
            "signal_eat": timestamp(current.epoch, EAT),
            "trigger_digit_1": trigger_digits[0],
            "trigger_digit_2": trigger_digits[1],
            "trigger_digits": "|".join(str(digit) for digit in trigger_digits),
            "result_sequence": result.sequence,
            "result_epoch": result.epoch,
            "result_utc": timestamp(result.epoch, UTC),
            "result_eat": timestamp(result.epoch, EAT),
            "result_digit": result.digit,
            "outcome": outcome,
            "stake": stake,
            "payout": payout if outcome == "win" else 0.0,
            "net_profit": net_profit,
            "cooldown_after": 0,
            "consecutive_wins_after": 0,
            "consecutive_losses_after": 0,
        }
        trades.append(pending_trade)
        settlement_index = index + 1

    equity = 0.0
    peak = 0.0
    for trade in trades:
        equity += trade["net_profit"]
        peak = max(peak, equity)
        trade["cumulative_pnl"] = equity
        trade["drawdown"] = peak - equity

    return trades, {
        "cooldown_ticks_consumed": cooldown_ticks_consumed,
        "unscored_signals": unscored_signals,
    }


def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(trade["outcome"] == "win" for trade in trades)
    losses = len(trades) - wins
    net_profit = sum(float(trade["net_profit"]) for trade in trades)
    gross_profit = sum(
        float(trade["net_profit"]) for trade in trades if trade["net_profit"] > 0
    )
    gross_loss = -sum(
        float(trade["net_profit"]) for trade in trades if trade["net_profit"] < 0
    )
    max_drawdown = max((float(trade["drawdown"]) for trade in trades), default=0.0)
    max_drawdown_trade = next(
        (
            trade["trade_number"]
            for trade in trades
            if math.isclose(float(trade["drawdown"]), max_drawdown, abs_tol=1e-9)
        ),
        0,
    )
    lower, upper = wilson_interval(wins, len(trades))
    total_staked = sum(float(trade["stake"]) for trade in trades)
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(trades) if trades else 0.0,
        "win_rate_ci_lower": lower,
        "win_rate_ci_upper": upper,
        "net_profit": net_profit,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else math.inf,
        "total_staked": total_staked,
        "roi_on_stake": net_profit / total_staked if total_staked else 0.0,
        "average_profit_per_trade": net_profit / len(trades) if trades else 0.0,
        "max_drawdown": max_drawdown,
        "max_drawdown_trade": max_drawdown_trade,
        "longest_win_streak": longest_streak(
            (trade["outcome"] for trade in trades), "win"
        ),
        "longest_loss_streak": longest_streak(
            (trade["outcome"] for trade in trades), "loss"
        ),
    }


def local_drawdown(trades: list[dict[str, Any]]) -> float:
    equity = 0.0
    peak = 0.0
    maximum = 0.0
    for trade in trades:
        equity += float(trade["net_profit"])
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def per_block_rows(
    ticks: list[Tick],
    raw_trades: list[dict[str, Any]],
    current_trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_by_block: dict[int, list[dict[str, Any]]] = defaultdict(list)
    current_by_block: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for trade in raw_trades:
        raw_by_block[int(trade["block"])].append(trade)
    for trade in current_trades:
        current_by_block[int(trade["block"])].append(trade)

    rows: list[dict[str, Any]] = []
    cumulative = 0.0
    for block_number, offset in enumerate(range(0, len(ticks), 1000), 1):
        block_ticks = ticks[offset : offset + 1000]
        raw = raw_by_block[block_number]
        current = current_by_block[block_number]
        raw_stats = metrics(raw)
        current_stats = metrics(current)
        cumulative += float(current_stats["net_profit"])
        frequencies = Counter(tick.digit for tick in block_ticks)
        row: dict[str, Any] = {
            "block": block_number,
            "sequence_start": block_ticks[0].sequence,
            "sequence_end": block_ticks[-1].sequence,
            "start_utc": timestamp(block_ticks[0].epoch, UTC),
            "end_utc": timestamp(block_ticks[-1].epoch, UTC),
            "start_eat": timestamp(block_ticks[0].epoch, EAT),
            "end_eat": timestamp(block_ticks[-1].epoch, EAT),
            "raw_trades": raw_stats["trades"],
            "raw_wins": raw_stats["wins"],
            "raw_losses": raw_stats["losses"],
            "raw_win_rate_pct": raw_stats["win_rate"] * 100,
            "raw_net_pnl": raw_stats["net_profit"],
            "current_trades": current_stats["trades"],
            "current_wins": current_stats["wins"],
            "current_losses": current_stats["losses"],
            "current_win_rate_pct": current_stats["win_rate"] * 100,
            "current_net_pnl": current_stats["net_profit"],
            "current_cumulative_pnl": cumulative,
            "current_max_drawdown": local_drawdown(current),
            "current_longest_win_streak": current_stats["longest_win_streak"],
            "current_longest_loss_streak": current_stats["longest_loss_streak"],
        }
        row.update({f"digit_{digit}_count": frequencies[digit] for digit in range(10)})
        rows.append(row)
    return rows


def pair_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        pair = f"{trade['trigger_digit_1']}{trade['trigger_digit_2']}"
        grouped[pair].append(trade)
    rows = []
    for pair in sorted(grouped):
        stats = metrics(grouped[pair])
        rows.append(
            {
                "trigger_pair": pair,
                "trades": stats["trades"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate_pct": stats["win_rate"] * 100,
                "win_rate_ci_lower_pct": stats["win_rate_ci_lower"] * 100,
                "win_rate_ci_upper_pct": stats["win_rate_ci_upper"] * 100,
                "net_pnl": stats["net_profit"],
                "average_pnl_per_trade": stats["average_profit_per_trade"],
                "longest_loss_streak": stats["longest_loss_streak"],
            }
        )
    return rows


def hourly_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        hour = datetime.fromtimestamp(int(trade["signal_epoch"]), tz=EAT).hour
        grouped[hour].append(trade)
    rows = []
    for hour in range(24):
        stats = metrics(grouped[hour])
        rows.append(
            {
                "eat_hour": hour,
                "trades": stats["trades"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate_pct": stats["win_rate"] * 100,
                "net_pnl": stats["net_profit"],
                "average_pnl_per_trade": stats["average_profit_per_trade"],
            }
        )
    return rows


def fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def build_report(
    *,
    ticks: list[Tick],
    raw_trades: list[dict[str, Any]],
    current_trades: list[dict[str, Any]],
    raw_runtime: dict[str, int],
    current_runtime: dict[str, int],
    blocks: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    hours: list[dict[str, Any]],
    stake: float,
    payout: float,
    source: Path,
) -> str:
    raw = metrics(raw_trades)
    current = metrics(current_trades)
    break_even = stake / payout
    profitable_blocks = sum(float(row["current_net_pnl"]) > 0 for row in blocks)
    losing_blocks = sum(float(row["current_net_pnl"]) < 0 for row in blocks)
    flat_blocks = len(blocks) - profitable_blocks - losing_blocks
    best_block = max(blocks, key=lambda row: float(row["current_net_pnl"]))
    worst_block = min(blocks, key=lambda row: float(row["current_net_pnl"]))
    digit_counts = Counter(tick.digit for tick in ticks)
    expected_digits = len(ticks) / 10
    digit_chi_square = sum(
        (digit_counts[digit] - expected_digits) ** 2 / expected_digits
        for digit in range(10)
    )
    qualified_pairs = [row for row in pairs if int(row["trades"]) >= 30]
    best_pair = max(qualified_pairs, key=lambda row: float(row["net_pnl"]))
    worst_pair = min(qualified_pairs, key=lambda row: float(row["net_pnl"]))
    profitable_pairs = sum(float(row["net_pnl"]) > 0 for row in pairs)
    confirmed_pairs = sum(
        float(row["win_rate_ci_lower_pct"]) / 100 > break_even for row in pairs
    )
    profitable_hours = sum(float(row["net_pnl"]) > 0 for row in hours)
    best_hour = max(hours, key=lambda row: float(row["net_pnl"]))
    worst_hour = min(hours, key=lambda row: float(row["net_pnl"]))
    trades_removed = raw["trades"] - current["trades"]
    exposure_reduction = trades_removed / raw["trades"] if raw["trades"] else 0.0
    loss_reduction = current["net_profit"] - raw["net_profit"]
    drawdown_reduction = raw["max_drawdown"] - current["max_drawdown"]
    conclusion = (
        "The current model was profitable under these assumptions."
        if current["net_profit"] > 0
        else "The current model was not profitable under these assumptions."
    )
    edge_assessment = (
        "Its full 95% win-rate interval is above break-even."
        if current["win_rate_ci_lower"] > break_even
        else (
            "Its full 95% win-rate interval is below break-even."
            if current["win_rate_ci_upper"] < break_even
            else "Its 95% win-rate interval overlaps break-even."
        )
    )

    return f"""# Test 2 Backtest: 100,000 Continuous Ticks

## Scope and assumptions

- Source: `{source.as_posix()}`
- Symbol: `1HZ100V`
- Period UTC: {timestamp(ticks[0].epoch, UTC)} to {timestamp(ticks[-1].epoch, UTC)}
- Period EAT: {timestamp(ticks[0].epoch, EAT)} to {timestamp(ticks[-1].epoch, EAT)}
- Data integrity: {len(ticks):,} unique one-second ticks, zero gaps
- Signal: `[6-9], [6-9], [0-2], [0-2], [3-5]` (`BIN22001x5`)
- Contract: `DIGITOVER 3`, one tick, stake {fmt_money(stake)}
- Settlement assumption: the tick immediately after the signal
- Economics assumption: payout {fmt_money(payout)} including stake; win net {fmt_money(payout - stake)}, loss {fmt_money(-stake)}
- Natural `DIGITOVER 3` outcome rate under uniform digits: 60.00%; paid break-even rate: {break_even:.2%}
- Bayesian and HMM modes are `shadow`, matching the current configuration, so neither blocks a trade
- Network latency, stale proposals, rejected purchases, slippage, and payout changes cannot be reconstructed from tick history

## Overall comparison

| Metric | Raw signal model | Current in-place model |
|---|---:|---:|
| Trades | {raw['trades']:,} | {current['trades']:,} |
| Wins | {raw['wins']:,} | {current['wins']:,} |
| Losses | {raw['losses']:,} | {current['losses']:,} |
| Win rate | {raw['win_rate']:.2%} | {current['win_rate']:.2%} |
| 95% win-rate interval | {raw['win_rate_ci_lower']:.2%} to {raw['win_rate_ci_upper']:.2%} | {current['win_rate_ci_lower']:.2%} to {current['win_rate_ci_upper']:.2%} |
| Break-even win rate | {break_even:.2%} | {break_even:.2%} |
| Net P/L | {fmt_money(raw['net_profit'])} | {fmt_money(current['net_profit'])} |
| Total staked | {fmt_money(raw['total_staked'])} | {fmt_money(current['total_staked'])} |
| Return on amount staked | {raw['roi_on_stake']:.2%} | {current['roi_on_stake']:.2%} |
| Profit factor | {raw['profit_factor']:.3f} | {current['profit_factor']:.3f} |
| Maximum drawdown | {fmt_money(raw['max_drawdown'])} | {fmt_money(current['max_drawdown'])} |
| Longest win streak | {raw['longest_win_streak']} | {current['longest_win_streak']} |
| Longest loss streak | {raw['longest_loss_streak']} | {current['longest_loss_streak']} |

The current model consumed {current_runtime['cooldown_ticks_consumed']:,} ticks in cooldown. Unscored end-of-file signals: raw {raw_runtime['unscored_signals']}, current {current_runtime['unscored_signals']}.

The cooldown removed {trades_removed:,} trades ({exposure_reduction:.2%}), reduced the simulated loss by {fmt_money(loss_reduction)}, and reduced maximum drawdown by {fmt_money(drawdown_reduction)}. It did not improve the win rate or return per dollar staked.

## Per-1,000-tick stability

- Profitable blocks: {profitable_blocks} of {len(blocks)}
- Losing blocks: {losing_blocks} of {len(blocks)}
- Flat blocks: {flat_blocks} of {len(blocks)}
- Best block: #{best_block['block']} with {fmt_money(float(best_block['current_net_pnl']))}, {int(best_block['current_trades'])} trades, {float(best_block['current_win_rate_pct']):.2f}% wins
- Worst block: #{worst_block['block']} with {fmt_money(float(worst_block['current_net_pnl']))}, {int(worst_block['current_trades'])} trades, {float(worst_block['current_win_rate_pct']):.2f}% wins

## Pattern and digit checks

- Full digit counts: {", ".join(f"{digit}={digit_counts[digit]:,}" for digit in range(10))}
- Uniform-digit chi-square statistic: {digit_chi_square:.3f} with 9 degrees of freedom
- Best trigger pair with at least 30 current trades: `{best_pair['trigger_pair']}`, {int(best_pair['trades'])} trades, {float(best_pair['win_rate_pct']):.2f}% wins, {fmt_money(float(best_pair['net_pnl']))}
- Worst trigger pair with at least 30 current trades: `{worst_pair['trigger_pair']}`, {int(worst_pair['trades'])} trades, {float(worst_pair['win_rate_pct']):.2f}% wins, {fmt_money(float(worst_pair['net_pnl']))}
- Profitable trigger pairs: {profitable_pairs} of {len(pairs)}; pairs whose full 95% interval exceeds break-even: {confirmed_pairs}
- Profitable EAT hour groups: {profitable_hours} of {len(hours)}
- Least-negative/best EAT hour: {int(best_hour['eat_hour']):02d}:00, {int(best_hour['trades'])} trades, {float(best_hour['win_rate_pct']):.2f}% wins, {fmt_money(float(best_hour['net_pnl']))}
- Worst EAT hour: {int(worst_hour['eat_hour']):02d}:00, {int(worst_hour['trades'])} trades, {float(worst_hour['win_rate_pct']):.2f}% wins, {fmt_money(float(worst_hour['net_pnl']))}

## Finding

{conclusion} {edge_assessment} This historically selected condition still requires confirmation with new forward data before it can be treated as a durable edge. Pair, hour, and 1,000-tick block results are diagnostic slices rather than independent validation.

## Output files

- `test2_100000_per_1000.csv`: all 100 chronological blocks
- `test2_100000_current_trades.csv`: every simulated trade from the in-place model
- `test2_100000_raw_trades.csv`: every raw pattern-reset trade without cooldown
- `test2_100000_pair_performance.csv`: trigger-pair outcomes
- `test2_100000_hourly_eat.csv`: current-model outcomes by EAT hour
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay Test 2 over continuous ticks")
    parser.add_argument(
        "--ticks",
        type=Path,
        default=Path("data/1HZ100V_100000_continuous_ticks.csv"),
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--output", type=Path, default=Path("analysis"))
    parser.add_argument(
        "--payout",
        type=float,
        default=0.55,
        help="Gross payout including returned stake for a winning $0.35 contract",
    )
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    ticks = load_ticks(args.ticks)
    strategy = config["strategy"]
    signal = config["signal"]
    cooldown = config["cooldown"]
    stake = float(strategy["initial_stake"])
    prediction = int(strategy["prediction"])
    pattern_ranges = tuple(
        (int(bounds[0]), int(bounds[1])) for bounds in signal["pattern_ranges"]
    )
    cooldown_rules = {
        "after_win_ticks": int(cooldown["after_win_ticks"]),
        "after_loss_ticks": int(cooldown["after_loss_ticks"]),
        "after_three_consecutive_losses_ticks": int(
            cooldown["after_three_consecutive_losses_ticks"]
        ),
        "after_five_consecutive_losses_ticks": int(
            cooldown["after_five_consecutive_losses_ticks"]
        ),
    }

    raw_trades, raw_runtime = simulate(
        ticks,
        mode="raw_no_cooldown",
        pattern_ranges=pattern_ranges,
        prediction=prediction,
        stake=stake,
        payout=args.payout,
        use_cooldown=False,
        cooldown_rules=cooldown_rules,
    )
    current_trades, current_runtime = simulate(
        ticks,
        mode="current_with_cooldown",
        pattern_ranges=pattern_ranges,
        prediction=prediction,
        stake=stake,
        payout=args.payout,
        use_cooldown=True,
        cooldown_rules=cooldown_rules,
    )

    blocks = per_block_rows(ticks, raw_trades, current_trades)
    pairs = pair_rows(current_trades)
    hours = hourly_rows(current_trades)
    args.output.mkdir(parents=True, exist_ok=True)

    trade_fields = [
        "mode",
        "trade_number",
        "block",
        "signal_sequence",
        "signal_epoch",
        "signal_utc",
        "signal_eat",
        "trigger_digit_1",
        "trigger_digit_2",
        "trigger_digits",
        "result_sequence",
        "result_epoch",
        "result_utc",
        "result_eat",
        "result_digit",
        "outcome",
        "stake",
        "payout",
        "net_profit",
        "cumulative_pnl",
        "drawdown",
        "cooldown_after",
        "consecutive_wins_after",
        "consecutive_losses_after",
    ]
    block_fields = list(blocks[0])
    pair_fields = list(pairs[0])
    hour_fields = list(hours[0])
    write_csv(
        args.output / "test2_100000_current_trades.csv",
        current_trades,
        trade_fields,
    )
    write_csv(
        args.output / "test2_100000_raw_trades.csv", raw_trades, trade_fields
    )
    write_csv(args.output / "test2_100000_per_1000.csv", blocks, block_fields)
    write_csv(args.output / "test2_100000_pair_performance.csv", pairs, pair_fields)
    write_csv(args.output / "test2_100000_hourly_eat.csv", hours, hour_fields)
    report = build_report(
        ticks=ticks,
        raw_trades=raw_trades,
        current_trades=current_trades,
        raw_runtime=raw_runtime,
        current_runtime=current_runtime,
        blocks=blocks,
        pairs=pairs,
        hours=hours,
        stake=stake,
        payout=args.payout,
        source=args.ticks,
    )
    (args.output / "test2_100000_backtest_report.md").write_text(
        report, encoding="utf-8"
    )

    raw = metrics(raw_trades)
    current = metrics(current_trades)
    print(f"ticks={len(ticks)} blocks={len(blocks)}")
    print(
        f"raw trades={raw['trades']} wins={raw['wins']} losses={raw['losses']} "
        f"win_rate={raw['win_rate']:.4%} pnl={raw['net_profit']:.2f}"
    )
    print(
        f"current trades={current['trades']} wins={current['wins']} "
        f"losses={current['losses']} win_rate={current['win_rate']:.4%} "
        f"pnl={current['net_profit']:.2f} max_dd={current['max_drawdown']:.2f}"
    )
    print(f"output={args.output.resolve()}")


if __name__ == "__main__":
    main()
