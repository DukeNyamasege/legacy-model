# Test 2 Backtest: 100,000 Continuous Ticks

## Scope and assumptions

- Source: `data/1HZ100V_100000_continuous_ticks.csv`
- Symbol: `1HZ100V`
- Period UTC: 2026-07-01T08:12:45+00:00 to 2026-07-02T11:59:24+00:00
- Period EAT: 2026-07-01T11:12:45+03:00 to 2026-07-02T14:59:24+03:00
- Data integrity: 100,000 unique one-second ticks, zero gaps
- Signal: `[6-9], [6-9], [0-2], [0-2], [3-5]` (`BIN22001x5`)
- Contract: `DIGITOVER 3`, one tick, stake $0.35
- Settlement assumption: the tick immediately after the signal
- Economics assumption: payout $0.55 including stake; win net $0.20, loss $-0.35
- Natural `DIGITOVER 3` outcome rate under uniform digits: 60.00%; paid break-even rate: 63.64%
- Bayesian and HMM modes are `shadow`, matching the current configuration, so neither blocks a trade
- Network latency, stale proposals, rejected purchases, slippage, and payout changes cannot be reconstructed from tick history

## Overall comparison

| Metric | Raw signal model | Current in-place model |
|---|---:|---:|
| Trades | 420 | 419 |
| Wins | 286 | 286 |
| Losses | 134 | 133 |
| Win rate | 68.10% | 68.26% |
| 95% win-rate interval | 63.49% to 72.37% | 63.65% to 72.53% |
| Break-even win rate | 63.64% | 63.64% |
| Net P/L | $10.30 | $10.65 |
| Total staked | $147.00 | $146.65 |
| Return on amount staked | 7.01% | 7.26% |
| Profit factor | 1.220 | 1.229 |
| Maximum drawdown | $2.10 | $2.10 |
| Longest win streak | 11 | 11 |
| Longest loss streak | 6 | 6 |

The current model consumed 887 ticks in cooldown. Unscored end-of-file signals: raw 0, current 0.

The cooldown removed 1 trades (0.24%), reduced the simulated loss by $0.35, and reduced maximum drawdown by $0.00. It did not improve the win rate or return per dollar staked.

## Per-1,000-tick stability

- Profitable blocks: 54 of 100
- Losing blocks: 46 of 100
- Flat blocks: 0 of 100
- Best block: #92 with $1.05, 8 trades, 87.50% wins
- Worst block: #50 with $-0.80, 7 trades, 42.86% wins

## Pattern and digit checks

- Full digit counts: 0=9,936, 1=10,074, 2=9,912, 3=9,986, 4=10,055, 5=9,994, 6=9,925, 7=9,977, 8=10,118, 9=10,023
- Uniform-digit chi-square statistic: 4.118 with 9 degrees of freedom
- Best trigger pair with at least 30 current trades: `77`, 30 trades, 80.00% wins, $2.70
- Worst trigger pair with at least 30 current trades: `99`, 30 trades, 60.00% wins, $-0.60
- Profitable trigger pairs: 11 of 16; pairs whose full 95% interval exceeds break-even: 0
- Profitable EAT hour groups: 17 of 24
- Least-negative/best EAT hour: 01:00, 18 trades, 83.33% wins, $1.95
- Worst EAT hour: 08:00, 11 trades, 45.45% wins, $-1.10

## Finding

The current model was profitable under these assumptions. Its full 95% win-rate interval is above break-even. This historically selected condition still requires confirmation with new forward data before it can be treated as a durable edge. Pair, hour, and 1,000-tick block results are diagnostic slices rather than independent validation.

## Output files

- `test2_100000_per_1000.csv`: all 100 chronological blocks
- `test2_100000_current_trades.csv`: every simulated trade from the in-place model
- `test2_100000_raw_trades.csv`: every raw pattern-reset trade without cooldown
- `test2_100000_pair_performance.csv`: trigger-pair outcomes
- `test2_100000_hourly_eat.csv`: current-model outcomes by EAT hour
