from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable, Iterable

import yaml


UTC = timezone.utc
EAT = timezone(timedelta(hours=3), name="EAT")
TRAIN = (0, 50_000)
VALIDATION = (50_000, 75_000)
TEST = (75_000, 100_000)
DEVELOPMENT_FOLDS = (
    (0, 15_000),
    (15_000, 30_000),
    (30_000, 45_000),
    (45_000, 60_000),
    (60_000, 75_000),
)


@dataclass(frozen=True, slots=True)
class Tick:
    sequence: int
    epoch: int
    quote: str
    digit: int


def load_ticks(path: Path) -> list[Tick]:
    ticks: list[Tick] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ticks.append(
                Tick(
                    sequence=int(row["sequence"]),
                    epoch=int(row["epoch"]),
                    quote=row["quote"],
                    digit=int(row["digit"]),
                )
            )
    if len(ticks) != 100_000:
        raise ValueError(f"Expected 100,000 ticks, found {len(ticks):,}")
    if any(tick.sequence != index for index, tick in enumerate(ticks, 1)):
        raise ValueError("Tick sequences are not continuous")
    if any(right.epoch - left.epoch != 1 for left, right in zip(ticks, ticks[1:])):
        raise ValueError("Tick epochs are not continuous")
    if any(tick.quote[-1] != str(tick.digit) for tick in ticks):
        raise ValueError("At least one extracted digit does not match its quote")
    return ticks


def property_value(name: str, digit: int) -> bool:
    if name == "over3":
        return digit > 3
    if name == "over4":
        return digit > 4
    if name == "at_most_2":
        return digit <= 2
    if name == "at_least_7":
        return digit >= 7
    if name == "even":
        return digit % 2 == 0
    raise ValueError(f"Unknown digit property: {name}")


def settle_occurrences(
    occurrences: list[int],
    digits: list[int],
    *,
    start: int,
    end: int,
    stake: float,
    payout: float,
    cooldown: bool,
    cooldown_rules: dict[str, int],
    ledger: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    left = bisect.bisect_left(occurrences, start)
    right = bisect.bisect_left(occurrences, end - 1)
    available_at = start
    consecutive_wins = 0
    consecutive_losses = 0
    wins = 0
    losses = 0
    trades: list[dict[str, Any]] = []
    win_net = payout - stake

    for signal_index in occurrences[left:right]:
        if signal_index < available_at:
            continue
        outcome = "win" if digits[signal_index + 1] > 3 else "loss"
        if outcome == "win":
            wins += 1
            consecutive_wins += 1
            consecutive_losses = 0
            cooldown_after = cooldown_rules["after_win_ticks"] if cooldown else 0
            profit = win_net
        else:
            losses += 1
            consecutive_losses += 1
            consecutive_wins = 0
            profit = -stake
            if not cooldown:
                cooldown_after = 0
            elif consecutive_losses >= 5:
                cooldown_after = cooldown_rules["after_five_consecutive_losses_ticks"]
            elif consecutive_losses >= 3:
                cooldown_after = cooldown_rules[
                    "after_three_consecutive_losses_ticks"
                ]
            else:
                cooldown_after = cooldown_rules["after_loss_ticks"]
        available_at = signal_index + 2 + cooldown_after
        if ledger:
            trades.append(
                {
                    "signal_index": signal_index,
                    "result_index": signal_index + 1,
                    "outcome": outcome,
                    "result_digit": digits[signal_index + 1],
                    "net_profit": profit,
                    "cooldown_after": cooldown_after,
                    "consecutive_wins_after": consecutive_wins,
                    "consecutive_losses_after": consecutive_losses,
                }
            )

    total = wins + losses
    net_profit = wins * win_net - losses * stake
    lower, upper = wilson_interval(wins, total)
    return (
        {
            "trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total if total else 0.0,
            "net_pnl": net_profit,
            "average_pnl": net_profit / total if total else 0.0,
            "ci_lower": lower,
            "ci_upper": upper,
        },
        trades,
    )


def wilson_interval(wins: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    z = NormalDist().inv_cdf(0.975)
    rate = wins / total
    denominator = 1 + z * z / total
    centre = (rate + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
        / denominator
    )
    return centre - margin, centre + margin


def binomial_upper_tail(total: int, wins: int, probability: float) -> float:
    return sum(
        math.comb(total, value)
        * probability**value
        * (1 - probability) ** (total - value)
        for value in range(wins, total + 1)
    )


def z_edge(rate: float, total: int, break_even: float) -> float:
    if total == 0:
        return -math.inf
    standard_error = math.sqrt(break_even * (1 - break_even) / total)
    return (rate - break_even) / standard_error


def occurrences_for_spec(spec: dict[str, Any], digits: list[int]) -> list[int]:
    kind = spec["kind"]
    result: list[int] = []
    if kind in {"exact_suffix", "binary_suffix", "parity_suffix", "categorical_suffix"}:
        pattern = tuple(spec["pattern"])
        length = len(pattern)
        for index in range(length - 1, len(digits) - 1):
            window = digits[index - length + 1 : index + 1]
            if kind == "exact_suffix":
                transformed = tuple(window)
            elif kind == "binary_suffix":
                threshold = int(spec["threshold"])
                transformed = tuple(int(digit <= threshold) for digit in window)
            elif kind == "parity_suffix":
                transformed = tuple(digit % 2 for digit in window)
            else:
                low, high = (int(value) for value in spec["boundaries"])
                transformed = tuple(
                    0 if digit <= low else 1 if digit <= high else 2
                    for digit in window
                )
            if transformed == pattern:
                result.append(index)
        return result

    if kind.startswith("window_count_"):
        window = int(spec["window"])
        target = int(spec["count"])
        values = [int(property_value(spec["property"], digit)) for digit in digits]
        rolling = sum(values[:window])
        for index in range(window - 1, len(digits) - 1):
            if index >= window:
                rolling += values[index] - values[index - window]
            relation = kind.removeprefix("window_count_")
            matches = (
                rolling == target
                if relation == "exact"
                else rolling >= target
                if relation == "at_least"
                else rolling <= target
            )
            if matches:
                result.append(index)
        return result

    if kind == "run_at_least":
        run = 0
        minimum = int(spec["length"])
        for index, digit in enumerate(digits[:-1]):
            if property_value(spec["property"], digit):
                run += 1
            else:
                run = 0
            if run >= minimum:
                result.append(index)
        return result
    raise ValueError(f"Unknown rule kind: {kind}")


class RuleSearch:
    def __init__(
        self,
        digits: list[int],
        *,
        stake: float,
        payout: float,
        cooldown_rules: dict[str, int],
    ) -> None:
        self.digits = digits
        self.stake = stake
        self.payout = payout
        self.break_even = stake / payout
        self.cooldown_rules = cooldown_rules
        self.results: list[dict[str, Any]] = []

    def evaluate(
        self,
        *,
        family: str,
        description: str,
        spec: dict[str, Any],
        occurrences: list[int],
        complexity: int,
    ) -> None:
        train, _ = settle_occurrences(
            occurrences,
            self.digits,
            start=TRAIN[0],
            end=TRAIN[1],
            stake=self.stake,
            payout=self.payout,
            cooldown=True,
            cooldown_rules=self.cooldown_rules,
        )
        validation, _ = settle_occurrences(
            occurrences,
            self.digits,
            start=VALIDATION[0],
            end=VALIDATION[1],
            stake=self.stake,
            payout=self.payout,
            cooldown=True,
            cooldown_rules=self.cooldown_rules,
        )
        full, _ = settle_occurrences(
            occurrences,
            self.digits,
            start=0,
            end=len(self.digits),
            stake=self.stake,
            payout=self.payout,
            cooldown=True,
            cooldown_rules=self.cooldown_rules,
        )
        if train["trades"] < 80 or validation["trades"] < 40:
            return
        folds = [
            settle_occurrences(
                occurrences,
                self.digits,
                start=start,
                end=end,
                stake=self.stake,
                payout=self.payout,
                cooldown=True,
                cooldown_rules=self.cooldown_rules,
            )[0]
            for start, end in DEVELOPMENT_FOLDS
        ]
        positive_folds = sum(fold["net_pnl"] > 0 for fold in folds)
        robust_score = (
            min(
                z_edge(train["win_rate"], train["trades"], self.break_even),
                z_edge(
                    validation["win_rate"],
                    validation["trades"],
                    self.break_even,
                ),
            )
            + 0.12 * positive_folds
            - 0.015 * complexity
        )
        self.results.append(
            {
                "family": family,
                "description": description,
                "spec_json": json.dumps(spec, separators=(",", ":"), sort_keys=True),
                "complexity": complexity,
                "positive_development_folds": positive_folds,
                "robust_score": robust_score,
                **{f"train_{key}": value for key, value in train.items()},
                **{
                    f"validation_{key}": value
                    for key, value in validation.items()
                },
                **{f"full_{key}": value for key, value in full.items()},
            }
        )

    def grouped_suffixes(
        self,
        *,
        family: str,
        lengths: Iterable[int],
        transform: Callable[[int], int],
        spec_factory: Callable[[tuple[int, ...]], dict[str, Any]],
        description_factory: Callable[[tuple[int, ...]], str],
    ) -> None:
        for length in lengths:
            groups: dict[tuple[int, ...], list[int]] = defaultdict(list)
            for index in range(length - 1, len(self.digits) - 1):
                pattern = tuple(
                    transform(digit)
                    for digit in self.digits[index - length + 1 : index + 1]
                )
                groups[pattern].append(index)
            for pattern, occurrences in groups.items():
                self.evaluate(
                    family=family,
                    description=description_factory(pattern),
                    spec=spec_factory(pattern),
                    occurrences=occurrences,
                    complexity=length,
                )

    def search(self) -> list[dict[str, Any]]:
        self.grouped_suffixes(
            family="exact_digits",
            lengths=(1, 2, 3),
            transform=lambda digit: digit,
            spec_factory=lambda pattern: {
                "kind": "exact_suffix",
                "pattern": list(pattern),
            },
            description_factory=lambda pattern: "last digits equal "
            + "-".join(str(value) for value in pattern),
        )

        for threshold in range(9):
            self.grouped_suffixes(
                family=f"binary_le_{threshold}",
                lengths=range(2, 9),
                transform=lambda digit, threshold=threshold: int(digit <= threshold),
                spec_factory=lambda pattern, threshold=threshold: {
                    "kind": "binary_suffix",
                    "threshold": threshold,
                    "pattern": list(pattern),
                },
                description_factory=lambda pattern, threshold=threshold: (
                    f"last {len(pattern)} digits match "
                    + "".join("L" if value else "H" for value in pattern)
                    + f", where L <= {threshold}"
                ),
            )

        for low, high in ((2, 5), (3, 6), (4, 7)):
            self.grouped_suffixes(
                family=f"three_bins_{low}_{high}",
                lengths=range(2, 7),
                transform=lambda digit, low=low, high=high: (
                    0 if digit <= low else 1 if digit <= high else 2
                ),
                spec_factory=lambda pattern, low=low, high=high: {
                    "kind": "categorical_suffix",
                    "boundaries": [low, high],
                    "pattern": list(pattern),
                },
                description_factory=lambda pattern, low=low, high=high: (
                    f"last {len(pattern)} digits have bin pattern "
                    + "".join(str(value) for value in pattern)
                    + f" for bins 0-{low}/{low + 1}-{high}/{high + 1}-9"
                ),
            )

        self.grouped_suffixes(
            family="parity",
            lengths=range(2, 9),
            transform=lambda digit: digit % 2,
            spec_factory=lambda pattern: {
                "kind": "parity_suffix",
                "pattern": list(pattern),
            },
            description_factory=lambda pattern: "last digits have parity "
            + "".join("O" if value else "E" for value in pattern),
        )

        properties = ("over3", "over4", "at_most_2", "at_least_7", "even")
        for property_name in properties:
            values = [
                int(property_value(property_name, digit)) for digit in self.digits
            ]
            for window in (3, 5, 7, 10, 15, 20, 30):
                groups: dict[int, list[int]] = defaultdict(list)
                rolling = sum(values[:window])
                for index in range(window - 1, len(self.digits) - 1):
                    if index >= window:
                        rolling += values[index] - values[index - window]
                    groups[rolling].append(index)
                for count, occurrences in groups.items():
                    self.evaluate(
                        family="window_count_exact",
                        description=(
                            f"exactly {count} of last {window} digits satisfy "
                            f"{property_name}"
                        ),
                        spec={
                            "kind": "window_count_exact",
                            "property": property_name,
                            "window": window,
                            "count": count,
                        },
                        occurrences=occurrences,
                        complexity=2,
                    )
                thresholds = sorted(
                    {
                        max(1, min(window - 1, round(window * fraction)))
                        for fraction in (0.25, 0.33, 0.4, 0.5, 0.6, 0.67, 0.75)
                    }
                )
                for count in thresholds:
                    for relation in ("at_least", "at_most"):
                        if relation == "at_least":
                            occurrences = sorted(
                                index
                                for key, indices in groups.items()
                                if key >= count
                                for index in indices
                            )
                            symbol = ">="
                        else:
                            occurrences = sorted(
                                index
                                for key, indices in groups.items()
                                if key <= count
                                for index in indices
                            )
                            symbol = "<="
                        self.evaluate(
                            family=f"window_count_{relation}",
                            description=(
                                f"{property_name} count in last {window} is "
                                f"{symbol} {count}"
                            ),
                            spec={
                                "kind": f"window_count_{relation}",
                                "property": property_name,
                                "window": window,
                                "count": count,
                            },
                            occurrences=occurrences,
                            complexity=2,
                        )

        for property_name in properties:
            run = 0
            by_minimum: dict[int, list[int]] = {
                minimum: [] for minimum in range(2, 11)
            }
            for index, digit in enumerate(self.digits[:-1]):
                if property_value(property_name, digit):
                    run += 1
                else:
                    run = 0
                for minimum in by_minimum:
                    if run >= minimum:
                        by_minimum[minimum].append(index)
            for minimum, occurrences in by_minimum.items():
                self.evaluate(
                    family="run",
                    description=f"{property_name} run is at least {minimum}",
                    spec={
                        "kind": "run_at_least",
                        "property": property_name,
                        "length": minimum,
                    },
                    occurrences=occurrences,
                    complexity=1,
                )
        return self.results


def add_ledger_fields(
    trades: list[dict[str, Any]],
    ticks: list[Tick],
    description: str,
) -> list[dict[str, Any]]:
    equity = 0.0
    peak = 0.0
    output: list[dict[str, Any]] = []
    for trade_number, trade in enumerate(trades, 1):
        signal = ticks[int(trade["signal_index"])]
        result = ticks[int(trade["result_index"])]
        equity += float(trade["net_profit"])
        peak = max(peak, equity)
        output.append(
            {
                "trade_number": trade_number,
                "condition": description,
                "block": (signal.sequence - 1) // 1000 + 1,
                "signal_sequence": signal.sequence,
                "signal_epoch": signal.epoch,
                "signal_utc": datetime.fromtimestamp(signal.epoch, UTC).isoformat(),
                "signal_eat": datetime.fromtimestamp(signal.epoch, EAT).isoformat(),
                "result_sequence": result.sequence,
                "result_digit": result.digit,
                "outcome": trade["outcome"],
                "net_profit": trade["net_profit"],
                "cumulative_pnl": equity,
                "drawdown": peak - equity,
                "cooldown_after": trade["cooldown_after"],
                "consecutive_wins_after": trade["consecutive_wins_after"],
                "consecutive_losses_after": trade["consecutive_losses_after"],
            }
        )
    return output


def max_drawdown(ledger: list[dict[str, Any]]) -> float:
    return max((float(row["drawdown"]) for row in ledger), default=0.0)


def longest_streak(ledger: list[dict[str, Any]], target: str) -> int:
    longest = 0
    current = 0
    for row in ledger:
        if row["outcome"] == target:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def per_thousand(
    ledger: list[dict[str, Any]], ticks: list[Tick]
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for trade in ledger:
        grouped[int(trade["block"])].append(trade)
    rows: list[dict[str, Any]] = []
    cumulative = 0.0
    for block in range(1, 101):
        trades = grouped[block]
        wins = sum(row["outcome"] == "win" for row in trades)
        losses = len(trades) - wins
        pnl = sum(float(row["net_profit"]) for row in trades)
        cumulative += pnl
        block_ticks = ticks[(block - 1) * 1000 : block * 1000]
        rows.append(
            {
                "block": block,
                "start_eat": datetime.fromtimestamp(
                    block_ticks[0].epoch, EAT
                ).isoformat(),
                "end_eat": datetime.fromtimestamp(
                    block_ticks[-1].epoch, EAT
                ).isoformat(),
                "trades": len(trades),
                "wins": wins,
                "losses": losses,
                "win_rate_pct": wins / len(trades) * 100 if trades else 0.0,
                "net_pnl": pnl,
                "cumulative_pnl": cumulative,
            }
        )
    return rows


def format_split(name: str, values: dict[str, Any]) -> str:
    return (
        f"| {name} | {values['trades']:,} | {values['wins']:,} | "
        f"{values['losses']:,} | {values['win_rate']:.2%} | "
        f"${values['net_pnl']:,.2f} | "
        f"{values['ci_lower']:.2%}-{values['ci_upper']:.2%} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ticks",
        type=Path,
        default=Path("data/1HZ100V_100000_continuous_ticks.csv"),
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--output", type=Path, default=Path("analysis/over3_research"))
    parser.add_argument("--payout", type=float, default=0.55)
    args = parser.parse_args()

    ticks = load_ticks(args.ticks)
    digits = [tick.digit for tick in ticks]
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    stake = float(config["strategy"]["initial_stake"])
    cooldown = config["cooldown"]
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
    search = RuleSearch(
        digits,
        stake=stake,
        payout=args.payout,
        cooldown_rules=cooldown_rules,
    )
    results = search.search()
    eligible = [
        row
        for row in results
        if int(row["train_trades"]) >= 150
        and int(row["validation_trades"]) >= 75
        and float(row["train_net_pnl"]) > 0
        and float(row["validation_net_pnl"]) > 0
        and int(row["positive_development_folds"]) >= 3
    ]
    eligible.sort(key=lambda row: float(row["robust_score"]), reverse=True)
    if not eligible:
        raise RuntimeError("No rule passed the predeclared development filters")
    champion = eligible[0]
    champion_spec = json.loads(champion["spec_json"])
    champion_occurrences = occurrences_for_spec(champion_spec, digits)

    split_results: dict[str, dict[str, Any]] = {}
    for name, (start, end) in {
        "Train": TRAIN,
        "Validation": VALIDATION,
        "Untouched test": TEST,
        "Full sample": (0, len(ticks)),
    }.items():
        split_results[name] = settle_occurrences(
            champion_occurrences,
            digits,
            start=start,
            end=end,
            stake=stake,
            payout=args.payout,
            cooldown=True,
            cooldown_rules=cooldown_rules,
        )[0]

    full_stats, full_trades = settle_occurrences(
        champion_occurrences,
        digits,
        start=0,
        end=len(ticks),
        stake=stake,
        payout=args.payout,
        cooldown=True,
        cooldown_rules=cooldown_rules,
        ledger=True,
    )
    no_cooldown_stats, _ = settle_occurrences(
        champion_occurrences,
        digits,
        start=0,
        end=len(ticks),
        stake=stake,
        payout=args.payout,
        cooldown=False,
        cooldown_rules=cooldown_rules,
    )
    ledger = add_ledger_fields(full_trades, ticks, champion["description"])
    blocks = per_thousand(ledger, ticks)
    profitable_blocks = sum(float(row["net_pnl"]) > 0 for row in blocks)
    ten_thousand_pnls = [
        sum(float(row["net_pnl"]) for row in blocks[offset : offset + 10])
        for offset in range(0, 100, 10)
    ]
    profitable_ten_thousand_periods = sum(value > 0 for value in ten_thousand_pnls)

    # This diagnostic intentionally uses all data and must never be deployed.
    hindsight_candidates = [
        row for row in results if int(row["full_trades"]) >= 300
    ]
    hindsight_candidates.sort(
        key=lambda row: float(row["full_net_pnl"]), reverse=True
    )
    hindsight = hindsight_candidates[0]

    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "locked_condition_trades.csv", ledger)
    write_csv(args.output / "locked_condition_per_1000.csv", blocks)
    write_csv(args.output / "development_rule_shortlist.csv", eligible[:250])
    write_csv(
        args.output / "hindsight_only_top_rules.csv", hindsight_candidates[:100]
    )

    break_even = stake / args.payout
    test_result = split_results["Untouched test"]
    test_p_vs_uniform = binomial_upper_tail(
        test_result["trades"], test_result["wins"], 0.60
    )
    test_p_vs_break_even = binomial_upper_tail(
        test_result["trades"], test_result["wins"], break_even
    )
    full_p_vs_uniform = binomial_upper_tail(
        full_stats["trades"], full_stats["wins"], 0.60
    )
    bonferroni_full_p = min(1.0, full_p_vs_uniform * len(results))
    total_staked = full_stats["trades"] * stake
    return_on_stake = full_stats["net_pnl"] / total_staked if total_staked else 0.0
    status = (
        "PASSED: profitable on the untouched test."
        if test_result["net_pnl"] > 0
        else "FAILED: lost money on the untouched test."
    )
    report = f"""# Over 3 Purchase-Condition Research

## Method

- Chronological train: ticks 1-50,000
- Chronological validation: ticks 50,001-75,000
- Untouched test: ticks 75,001-100,000
- Contract: `DIGITOVER 3`, one tick, $0.35 stake, assumed $0.55 gross payout
- Break-even win rate: {break_even:.2%}
- Rules searched: {len(results):,} interpretable exact-digit, threshold suffix, three-bin suffix, parity, rolling-count, and run conditions
- Selection used only train and validation. The test period was evaluated once after locking the rule.
- Existing adaptive cooldowns were retained.

## Locked development winner

**Purchase condition:** {champion['description']}.

| Period | Trades | Wins | Losses | Win rate | Net P/L | 95% interval |
|---|---:|---:|---:|---:|---:|---:|
{format_split('Train', split_results['Train'])}
{format_split('Validation', split_results['Validation'])}
{format_split('Untouched test', split_results['Untouched test'])}
{format_split('Full sample', split_results['Full sample'])}

**Honest test decision:** {status}

## Full-sample diagnostics for the locked rule

- Net P/L with current cooldown: ${full_stats['net_pnl']:,.2f}
- Net P/L without adaptive cooldown: ${no_cooldown_stats['net_pnl']:,.2f}
- Maximum drawdown: ${max_drawdown(ledger):,.2f}
- Longest winning streak: {longest_streak(ledger, 'win')}
- Longest losing streak: {longest_streak(ledger, 'loss')}
- Profitable 1,000-tick blocks: {profitable_blocks} of 100
- Profitable 10,000-tick periods: {profitable_ten_thousand_periods} of 10
- Return on total amount staked: {return_on_stake:.2%}
- Untouched-test probability of at least this many wins if the true rate is 60%: {test_p_vs_uniform:.4f}
- Untouched-test probability of at least this many wins if the true rate is break-even: {test_p_vs_break_even:.4f}
- Full-sample probability versus 60% before search correction: {full_p_vs_uniform:.6f}
- Full-sample probability after a conservative {len(results):,}-rule Bonferroni correction: {bonferroni_full_p:.4f}

## Hindsight warning

The best rule selected after examining all 100,000 outcomes was:

**{hindsight['description']}**

It shows {int(hindsight['full_trades']):,} trades, {float(hindsight['full_win_rate']):.2%} wins, and ${float(hindsight['full_net_pnl']):,.2f}. This is not valid evidence for future trading because the same outcomes were used to discover and score it. It is reported only to show how easily a profitable-looking rule can be manufactured through data mining.

## Recommendation

{"The locked condition earned money out of sample, but its confidence interval must still exceed the 63.64% break-even rate before live deployment is justified." if test_result['net_pnl'] > 0 else "Do not replace the live purchase logic with this rule. The strongest development condition did not survive the untouched period, so this dataset does not support a reliably profitable Over 3 purchase condition."}

If retained for research, run it in observation/demo mode until it produces at least 1,000 new forward trades. A live-trading review should require the lower bound of its 95% win-rate interval to exceed the current proposal's break-even rate, recalculated from the actual ask price and payout.
"""
    (args.output / "over3_condition_research_report.md").write_text(
        report, encoding="utf-8"
    )

    print(f"rules_evaluated={len(results)} eligible={len(eligible)}")
    print(f"locked_condition={champion['description']}")
    print(
        f"test trades={test_result['trades']} win_rate={test_result['win_rate']:.4%} "
        f"pnl={test_result['net_pnl']:.2f}"
    )
    print(
        f"full trades={full_stats['trades']} win_rate={full_stats['win_rate']:.4%} "
        f"pnl={full_stats['net_pnl']:.2f}"
    )
    print(
        f"hindsight={hindsight['description']} trades={hindsight['full_trades']} "
        f"win_rate={hindsight['full_win_rate']:.4%} "
        f"pnl={hindsight['full_net_pnl']:.2f}"
    )
    print(f"output={args.output.resolve()}")


if __name__ == "__main__":
    main()
