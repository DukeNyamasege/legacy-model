# Guardian Project Charter — Father of Automation Legacy Model

## Operator and product

- Operator: Duke / Mr Duke / Risk Commander.
- Production repository: `DukeNyamasege/legacy-model`.
- VPS checkout: `/root/legacy-model`.
- Public domain: `https://derivadmin.site`.
- Stack: PostgreSQL, FastAPI API, one continuously running Python trading worker,
  Docker Compose, Caddy/public HTTPS outside this repository.
- Current product identity: **AI Digit Recovery V2 (AIDR)**, Father of Automation
  Series.

## Active trading strategy contract

The authoritative machine-readable strategy contract is
`app/aidr_strategy_contract.json`. The trading runtime and Guardian both load that
same file. If this prose ever conflicts with the machine-readable contract, the
machine-readable contract wins. The active strategy is digit OVER only. PUT is
disabled.

1. Normal real execution uses `DIGITOVER` barrier `1` at the account's configured
   base stake.
2. The first actual real loss creates monetary recovery debt.
3. The next qualifying real entry uses `DIGITOVER` barrier `3` as one exact
   recovery attempt.
4. If that exact real recovery loses, the account enters virtual protection.
5. Virtual protection performs hypothetical `DIGITOVER` barrier `4` observations
   with `$0.00` charged and no provider purchase.
6. A virtual loss resets the consecutive virtual-win counter to `0/2`.
7. Two consecutive virtual wins arm one real post-virtual recovery.
8. The following real OVER-4 recovery targets all recorded debt in one winning
   profit target.
9. A real post-virtual recovery loss adds only that actual monetary loss to debt and sends
   the account back to virtual protection.
10. When debt is cleared, return to normal OVER-1 execution.

Virtual trades never change account balance, never add recovery debt, never count
as actual trades/wins/losses/P&L, and never enter the canonical monetary model
ledger.

## Account lifecycle contract

- **Pause -> Resume:** preserve session P/L, recovery debt, recovery mode, split
  targets, open/settled history and virtual-win progress. Resume continues where
  the account stopped temporarily.
- **Stop -> Start:** stop execution, clear debt and recovery state, cancel open
  virtual observations at `$0`, create a new personal-session boundary, and start
  later from base stake with `0` personal session trades and `0/2` virtual wins.
- **Reset Today / Reset All:** clear the selected personal history scope, clear all
  recovery/virtual state and leave the account stopped. A new Start is required.
- A fresh Start must be refused while an older actual provider contract for that
  account remains unresolved.
- Stopped or paused rows must never be automatically promoted by worker health,
  OAuth refresh, balance refresh or late settlement code.

## Account identity and mode isolation

- Use immutable `managed_account_id` as the database identity.
- Masked account IDs are display values and are not unique keys.
- Demo and Real sibling accounts have separate execution state, balances,
  settings, risk state and history.
- Starting, stopping or pausing one mode must not change the sibling mode.
- Every Real account uses its own encrypted OAuth/PAT credential path. Never
  introduce a shared execution token.
- Real provider purchases must not include `app_markup_percentage`.

## Execution safety

- One open actual contract per account unless an explicitly reviewed strategy
  revision changes the invariant.
- Do not create duplicate provider purchases while retrying, reconnecting,
  refreshing or recovering from partial bulk responses.
- Do not convert a virtual signal into a real purchase.
- Do not hide provider errors or present a failed purchase as a win/trade.
- Preserve PostgreSQL named volumes, users, credentials, balances, settings and
  OAuth sessions during code deployments.
- Never use `docker compose down -v`, volume deletion/prune, force push, database
  drop/truncate or destructive root removal.

## Strategy-advisor rules

- Advice is based on observed sample size, payout/break-even economics, expectancy,
  drawdown, execution coverage, missed trades and regime evidence.
- Never promise profit or describe a strategy as guaranteed.
- Strategy thresholds, stakes, recovery rules, TP/SL and Real activation are
  advisory-only. They are not auto-applied by Guardian incident approval.
- Prefer shadow/demo validation and walk-forward evidence before recommending a
  Real strategy change.
- Virtual wins must not inflate performance statistics.

## Telegram rules

- Existing channel publishing remains disabled.
- The Guardian uses a dedicated bot and one positive numeric private chat ID.
- Never publish Guardian incidents, releases, balances or account information to a
  Telegram channel or group.
- Code changes require a private Approve callback from the configured chat ID.
- Strategy/performance messages provide Acknowledge, not automatic application.

## Git and deployment rules

- Diagnose against the exact current `origin/main` commit.
- Create approved changes in an isolated temporary Git worktree.
- Refuse work when main moved or the live checkout is dirty.
- Modify only safe repository source/test/documentation paths.
- Compile, run allow-listed tests, validate the diff and perform independent review.
- Push normally to `main`; never force-push.
- Deploy through `scripts/deploy_vps.sh` only after push.
- If the approved commit fails deployment and main has not moved, create a normal
  revert commit, push it and attempt the safe deployment again.

## Secret and privacy rules

Never send these to an OpenAI model, Telegram message, Git commit, audit event or
normal application log:

- OpenAI API keys;
- Telegram bot tokens;
- Deriv OAuth/PAT/access/refresh tokens;
- Authorization headers;
- database passwords or encryption keys;
- full account login IDs;
- `.env` contents;
- private user balances or personally identifying account data when not required
  for a local deterministic check.

Redact first, send the minimum evidence needed, and fail closed when redaction or
identity is uncertain.
