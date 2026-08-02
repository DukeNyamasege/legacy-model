# Legacy Model Guardian Agent

## Purpose

The Guardian is a host-level, human-approved monitoring and coding agent for the
Legacy Model project. It observes Docker service health, API readiness, recent
API/worker/database errors, repository state, and periodic aggregate model
statistics. It sends proposals only to Duke's private Telegram chat.

The Guardian is not a persistent ChatGPT conversation running on the VPS. It uses
the OpenAI Responses API with a committed project charter, selected repository
files, redacted logs, health snapshots, and the exact incident being reviewed.
Private ChatGPT history and production secrets are not transferred into it.

## Permanent operating boundary

The Guardian is **Git-only**.

It may, after explicit private Telegram approval:

1. create a temporary Git worktree from the exact diagnosed `origin/main` commit;
2. generate complete replacement files only inside the diagnosed scope;
3. run protected-path and destructive-operation checks;
4. run compilation and tests in the isolated no-network sandbox;
5. request an independent AI review of the diff;
6. create a normal Git commit; and
7. push that commit to `main` without force-pushing.

It does **not** update, restart, rebuild, or deploy the VPS. It does not call
`scripts/deploy_vps.sh`. Duke reviews the completed Git commit and updates the VPS
manually only after the full release is ready.

`GUARDIAN_AUTO_DEPLOY` cannot grant deployment authority. The runtime forces
automatic deployment off, and the deployment execution path has been removed from
the patcher.

## Workflow

```text
Docker/API/log/metrics observation
  -> deterministic filtering and secret redaction
  -> OpenAI Responses API diagnosis
  -> private Telegram proposal
  -> Duke selects Approve, Reject, Details, or Acknowledge
  -> approved code fix uses a temporary Git worktree
  -> complete replacement files are generated only for diagnosed paths
  -> protected-path and destructive-operation policy checks
  -> no-network, read-only Docker sandbox tests
  -> independent AI diff review
  -> normal non-force Git push to main
  -> Guardian reports the commit and stops
  -> Duke updates the VPS manually later
```

Strategy and performance observations never enter the automatic patch pipeline.
They are advisory only and require a separate development decision.

## What it monitors

- `database`, `api`, and `worker` Docker Compose states;
- `/health/ready` response;
- recent Docker logs containing tracebacks, exceptions, failed operations,
  integrity errors, OAuth failures, connection failures, and unhealthy states;
- current Git commit and whether the live checkout has uncommitted changes;
- Demo and Real aggregate metrics at the configured review interval;
- repeated incidents through a durable SQLite fingerprint/cooldown store.

Normal tick, prefilter, cadence, and heartbeat messages are not treated as
incidents.

## Approval rules

Only the exact positive numeric `GUARDIAN_TELEGRAM_ADMIN_CHAT_ID` may approve or
reject. The dedicated Telegram bot sends private messages only and must not be
added to a public channel or group.

- **Approve fix:** starts isolated patching for an incident classified as a code
  change. A successful result may be pushed to Git main only.
- **Reject:** closes the incident without changing code.
- **Details:** sends stored redacted evidence and structured analysis.
- **Acknowledge:** records that strategy/performance advice was read. It does not
  change strategy code or configuration.
- `/status`: returns Guardian, API, Git, and Docker status.

Approvals are atomic in SQLite. Repeated callbacks cannot start the same patch
twice, and only one remediation may run at a time. If the service restarts during
an approved remediation, the incident is marked `interrupted` and does not resume
without a new diagnosis and approval.

## Security boundaries

The Guardian cannot automatically modify:

- `.env` or `.env.*` files;
- tokens, users, runtime credentials, or secrets;
- `model_artifacts`, deployment state, or backups;
- Docker volumes or PostgreSQL data;
- account balances, OAuth sessions, or Deriv credentials;
- Dockerfiles, Compose definitions, migrations, deployment/update scripts,
  systemd units, GitHub workflows, `config.yaml`, the project charter, or the
  Guardian's sandbox/security/patch-control modules.

The Guardian rejects patches containing volume deletion, `down -v`, force pushes,
database drops/truncation, root deletion, or other destructive operations. It
never sends raw Authorization headers, OpenAI keys, Telegram tokens, Deriv tokens,
or full account IDs to the model.

### Generated-code sandbox

Generated code is never executed directly as root before it is trusted. Every
compilation/test runs in a disposable container with:

- no network;
- no Docker socket;
- no environment secrets;
- read-only root filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- process, CPU, memory, and file-descriptor limits;
- the temporary Git worktree mounted read-only; and
- a separate ephemeral in-memory copy for compilation/test writes.

Only Python compileall, unittest/pytest, and shell syntax checks are accepted as
model-suggested tests.

### Strategy-metrics privacy

Before scheduled strategy analysis, account-, balance-, token-, credential-,
session-, login-, email-, and user-level structures are removed. Only aggregate
model-performance values are sent to OpenAI.

## Installation

### 1. Pull the repository release

Use the normal repository update procedure. Installing or updating the Guardian
does not authorize it to deploy the application.

### 2. Create a dedicated private Telegram bot

In Telegram, open **BotFather**, create a new bot, and retain the token privately.
Do not add this bot to the public channel. Open the new bot, press **Start**, and
send `/status` once.

### 3. Create an OpenAI API key

Create an API key for this service and keep it private. ChatGPT subscription
billing and OpenAI API billing are separate.

### 4. Run the installer once to prepare files

```bash
cd /root/legacy-model
sh scripts/install_guardian.sh
```

The first run installs only `guardian/requirements.txt` into an isolated virtual
environment and creates:

- `/opt/legacy-model-guardian/venv`;
- `/var/lib/legacy-model-guardian`;
- `/etc/legacy-model-guardian.env` with mode `0600`.

It stops before starting the service while required private values are missing.

### 5. Add the OpenAI key and Telegram bot token

```bash
nano /etc/legacy-model-guardian.env
```

Set:

```text
OPENAI_API_KEY=...
GUARDIAN_TELEGRAM_BOT_TOKEN=...
```

Do not put these values in the main project `.env`, GitHub, screenshots, Telegram
messages, or ChatGPT.

### 6. Discover the private Telegram chat ID locally

After sending a message to the new bot:

```bash
/opt/legacy-model-guardian/venv/bin/python \
  /root/legacy-model/scripts/guardian_discover_telegram_chat.py
```

Copy only your positive numeric private `chat_id` into:

```text
GUARDIAN_TELEGRAM_ADMIN_CHAT_ID=...
```

### 7. Complete installation

```bash
cd /root/legacy-model
sh scripts/install_guardian.sh
```

The installer compiles the Guardian, runs the full safety test suite, checks Git
origin write access, installs the hardened systemd unit, starts it, and prints
status. A private Telegram startup message should arrive.

## Commissioning settings

Start with:

```text
GUARDIAN_DRY_RUN=true
GUARDIAN_ALLOW_MAIN_PUSH=false
```

This allows monitoring, diagnosis, and private proposals without changing Git.
After observing the behavior, enable Git pushes only:

```text
GUARDIAN_DRY_RUN=false
GUARDIAN_ALLOW_MAIN_PUSH=true
```

No setting enables VPS deployment. VPS updates remain manual.

## Operations

```bash
# Service status
systemctl status legacy-model-guardian --no-pager

# Live Guardian logs
journalctl -u legacy-model-guardian -f

# Restart after changing /etc/legacy-model-guardian.env
systemctl restart legacy-model-guardian

# Stop monitoring and approvals
systemctl stop legacy-model-guardian

# Disable startup
systemctl disable --now legacy-model-guardian
```

The service state files are:

```text
/var/lib/legacy-model-guardian/guardian.sqlite3
/var/lib/legacy-model-guardian/openai-call-budget.json
```

Do not delete them during normal upgrades. They contain incident/approval state,
callback offsets, cooldown fingerprints, result metadata, and the daily API-call
counter—not production Deriv credentials.

## Model and cost configuration

Defaults:

```text
GUARDIAN_DIAGNOSIS_MODEL=gpt-5.4-mini
GUARDIAN_CODING_MODEL=gpt-5.3-codex
GUARDIAN_REVIEWER_MODEL=gpt-5.4-mini
GUARDIAN_MAX_AI_CALLS_PER_DAY=30
```

The daily limit includes diagnoses, coding, independent review, and structured
output fallback. When exhausted, deterministic monitoring continues but new AI
analysis waits for the next UTC day.

## Git behavior

An approved fix is based on the exact `origin/main` commit recorded during the
incident. If main moves before or during patching, the Guardian refuses the push
and requires a new diagnosis. It never force-pushes.

The live checkout must be clean before the worktree is created. After a successful
push, the Guardian reports:

- the base commit;
- the new commit;
- changed files;
- tests executed;
- independent review result; and
- that the VPS was not updated.

## Strategy advisor boundaries

The Guardian may review Demo and Real aggregate metrics and recent redacted error
events. Advice may discuss sample size, realized win rate, payout/break-even
economics, drawdown, missed executions, and regime evidence.

It does not:

- promise profit;
- silently change thresholds;
- activate Real accounts;
- change stakes, TP/SL, or recovery settings;
- treat virtual wins as actual financial performance; or
- promote an observation to production without a separate implementation request.

## What Duke remains responsible for

- keeping OpenAI API billing and the API key active;
- keeping the dedicated Telegram bot token private;
- maintaining Git write authentication on the VPS;
- reviewing every proposal before approval;
- reviewing every completed Git commit;
- deciding when the full release is ready;
- manually updating the VPS after that decision;
- handling Deriv/provider incidents that cannot be fixed in repository code; and
- reviewing infrastructure changes that remain outside Guardian authority.
