# Hybrid O2/U7 + Fixed PUT Recovery V3

Production invariants for `HYBRID-O2-U7-RECENT20-PUTFIX-V3`:

- Primary contract family: one-tick `DIGITOVER 2` or `DIGITUNDER 7`.
- Markets compete; only one strongest qualifying primary candidate is purchased per contract cycle.
- Primary entry uses only the latest 20 final digits. The old 100 / 500 / 1000 alignment and Wilson lower-bound gate are not production entry rules.
- A side must have at least 75% recent support, at least a 5 percentage-point advantage over the opposite side, and at least a 2 percentage-point edge above the current Deriv proposal break-even probability.
- A primary digit loss changes the model to `PUT_RECOVERY`; it does not force an immediate PUT.
- Recovery uses the existing strict 15-context -> 5-move FALL -> one-tick confirmation PUT model.
- Recovery debt is retained for accounting and completion logic only. It never increases the monetary stake.
- Every V3 recovery purchase uses that account's configured base stake, subject to affordability/reserve checks.
- Defense in depth caps generic recovery sizing at 10% of account balance even though V3 disables debt-derived recovery sizing.
- Two actual losses enter per-account virtual mode. Two consecutive virtual wins are required before a real recovery purchase can resume.
- Virtual observations have $0 financial impact and cannot produce a monetary purchase while the account remains in virtual mode.
- Accounts that join while the model is recovering do not inherit the recovery group or an elevated stake. They wait until recovery completes and then start at their own configured base stake.
- Canonical model accounting is account-independent at a $0.50 reference stake. Copier settlement timing cannot overwrite canonical outcome/P&L.
- V3 uses run id `hybrid_o2u7_put_v2` and a new hybrid runtime-state epoch so old PUT recovery state cannot be inherited.
- The initial V3 rollout is demo-only. Real execution is disabled until a separate deliberate promotion after demo verification.
- Before V3 deployment, the authorized clean-ledger reset removes old trades, model outcomes, recovery debt, virtual state, dashboard snapshots and hybrid runtime state while preserving managed accounts, encrypted credentials, account settings, sessions and general non-trading configuration.
