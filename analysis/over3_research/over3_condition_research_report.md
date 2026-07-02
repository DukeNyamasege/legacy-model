# Over 3 Purchase-Condition Research

## Method

- Chronological train: ticks 1-50,000
- Chronological validation: ticks 50,001-75,000
- Untouched test: ticks 75,001-100,000
- Contract: `DIGITOVER 3`, one tick, $0.35 stake, assumed $0.55 gross payout
- Break-even win rate: 63.64%
- Rules searched: 5,756 interpretable exact-digit, threshold suffix, three-bin suffix, parity, rolling-count, and run conditions
- Selection used only train and validation. The test period was evaluated once after locking the rule.
- Existing adaptive cooldowns were retained.

## Locked development winner

**Purchase condition:** last 5 digits have bin pattern 22001 for bins 0-2/3-5/6-9.

| Period | Trades | Wins | Losses | Win rate | Net P/L | 95% interval |
|---|---:|---:|---:|---:|---:|---:|
| Train | 208 | 143 | 65 | 68.75% | $5.85 | 62.16%-74.66% |
| Validation | 110 | 79 | 31 | 71.82% | $4.95 | 62.79%-79.38% |
| Untouched test | 101 | 64 | 37 | 63.37% | $-0.15 | 53.64%-72.11% |
| Full sample | 419 | 286 | 133 | 68.26% | $10.65 | 63.65%-72.53% |

**Honest test decision:** FAILED: lost money on the untouched test.

## Full-sample diagnostics for the locked rule

- Net P/L with current cooldown: $10.65
- Net P/L without adaptive cooldown: $10.30
- Maximum drawdown: $2.10
- Longest winning streak: 11
- Longest losing streak: 6
- Profitable 1,000-tick blocks: 54 of 100
- Profitable 10,000-tick periods: 9 of 10
- Return on total amount staked: 7.26%
- Untouched-test probability of at least this many wins if the true rate is 60%: 0.2795
- Untouched-test probability of at least this many wins if the true rate is break-even: 0.5671
- Full-sample probability versus 60% before search correction: 0.000286
- Full-sample probability after a conservative 5,756-rule Bonferroni correction: 1.0000

## Hindsight warning

The best rule selected after examining all 100,000 outcomes was:

**last 5 digits have bin pattern 22001 for bins 0-2/3-5/6-9**

It shows 419 trades, 68.26% wins, and $10.65. This is not valid evidence for future trading because the same outcomes were used to discover and score it. It is reported only to show how easily a profitable-looking rule can be manufactured through data mining.

## Recommendation

Do not replace the live purchase logic with this rule. The strongest development condition did not survive the untouched period, so this dataset does not support a reliably profitable Over 3 purchase condition.

If retained for research, run it in observation/demo mode until it produces at least 1,000 new forward trades. A live-trading review should require the lower bound of its 95% win-rate interval to exceed the current proposal's break-even rate, recalculated from the actual ask price and payout.
