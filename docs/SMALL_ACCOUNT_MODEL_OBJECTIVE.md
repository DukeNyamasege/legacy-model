# Small-Account Model Objective

This is the permanent optimization objective for the production trading model.

## Priority order

1. Protect small accounts first. The primary target user should be able to start around $10-$50 without the strategy depending on large recovery stakes.
2. Minimize maximum stake, drawdown, recovery debt and stake escalation.
3. Maximize profit efficiency: make the most defensible profit per dollar staked, not the largest headline P/L.
4. Win rate is secondary. A lower win rate is acceptable when the resulting strategy materially lowers drawdown and tail risk.

## Product preference

Prefer a strategy that can remain around $0.35-$1.00 for a $10 account and make smaller, steadier gains over a strategy that occasionally escalates from $0.50 to $10, $50 or $100.

The model should be attractive to small-scale traders. A $10 trader being able to pursue an additional $5-$10 over time is a product goal, not a guaranteed return.

## Research policy

- Historical trade data must not be reset going forward.
- Study losing streaks, when they happen, which markets and hours produce them, how recovery debt grows, and how account stakes escalate.
- Use canonical model outcomes for strategy evaluation so copied accounts do not multiply the signal sample.
- Use actual account executions separately to measure real stake escalation and account-level risk.
- Use pre-trade features only when training loss-risk models; do not leak settlement or recovery outcomes into model inputs.
- Validate any proposed filter on later/out-of-sample data before changing live execution.
- AI/ML may recommend changes but must never silently self-modify the live strategy.
- Strategy improvements must be versioned, reviewed, and activated deliberately, preferably at the next 00:00 Africa/Nairobi boundary so before/after results remain traceable.

## Core optimization metrics

- maximum actual stake
- maximum stake / base-stake multiple
- maximum drawdown
- minimum surviving balance for $10, $20 and $50 starting accounts
- longest loss streak
- recovery debt peak
- total stake volume
- net P/L per dollar staked
- flat-stake P/L
- out-of-sample P/L and drawdown after proposed filters

A change is not considered an improvement merely because it increases win rate or total P/L. It must improve the small-account risk profile without destroying expectancy.
