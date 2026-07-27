# Hybrid O2/U7 + PUT Recovery

Production invariants for `HYBRID-O2-U7-PUTREC-V1`:

- Primary contract family: one-tick `DIGITOVER 2` or `DIGITUNDER 7`.
- Markets compete; only one strongest qualifying primary candidate is purchased per contract cycle.
- Primary windows: 100 / 500 / 1000 final digits.
- Live proposal economics are authoritative. Entry requires all configured probability margins plus the 95% Wilson lower bound to exceed live break-even.
- A primary digit loss changes the system to `PUT_RECOVERY`; it does not force an immediate PUT.
- Recovery uses the existing strict 15-context -> 5-move FALL -> one-tick confirmation PUT model.
- No new platform-wide daily loss cap is introduced.
- No live 1,000-trade shadow prerequisite is introduced.
- Existing per-account two-actual-loss virtual protection remains authoritative.
- Existing small-account recovery protection remains authoritative.
- Accounts that join while the model is recovering do not inherit the recovery group or its stake. They wait until recovery is complete, then start on a new primary digit trade at their own configured base stake.
- Historical trades are retained. The hybrid model uses a new run id so its canonical statistics start as a new strategy generation without deleting prior runs.
