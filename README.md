# RF-DIR5 Guarded v1

RF-DIR5 is a demo-first Rise/Fall continuation strategy for Deriv synthetic
markets. The production worker executes `CALL` and `PUT` contracts while a
separate shadow ledger evaluates both 5-tick and 10-tick outcomes.

## Strategy

- Six full quotes produce five completed price movements.
- A Rise candidate requires at least four positive movements; Fall is symmetric.
- Efficiency, normalized impulse, and exhaustion filters reject noisy windows.
- All ten configured markets enter a 200 ms candidate arbitration window.
- The selected demo contract uses five ticks; every qualified signal shadows
  both five and ten ticks.
- Proposal `ask_price`, `payout`, and optional commission are the economics
  source of truth. No phantom markup cost is added.
- Independent `Beta(0.5, 0.5)` posteriors are maintained per strategy version,
  market, direction, and duration from settled shadow outcomes only.

## Safety

- After two consecutive account losses, that account switches to $0 virtual
  observations until a virtual win confirms the next recovery entry.
- After the virtual win, one recovery attempt targets the recorded loss debt
  using the current proposal profit ratio. It never chains.
- Virtual contracts never increase stake, never change recovery debt, and their
  results never enter the demo execution ledger.
- A recovery stake may exceed the user's normal stake, but neither normal nor
  recovery execution can exceed the configured account balance cap and reserve.
  If full recovery does not fit, that account is skipped.
- One strategy contract can be open globally and one contract per account.
- Real execution remains disabled. Promotion requires at least 1,000 settled
  shadow outcomes for the exact market/direction/duration group plus a positive
  95% lower confidence-bound edge and forward validation.

Recovery is a loss-sizing mechanism, not a guarantee. The recovery contract can
lose, and its actual payout can differ from the indicative public proposal.

## Run

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.vps.yml logs -f --tail=100 worker
```

The API exposes `/metrics/rf-strategy`, `/metrics/model`, and the existing
personal/global dashboard endpoints. PostgreSQL stores demo trades, directional
signals, shadow contracts, virtual-guard state, and per-account risk state in
separate tables.
