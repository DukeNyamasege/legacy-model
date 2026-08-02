# Legacy Model Guardian Agent

## Purpose

The Guardian is a host-level, human-approved operations and coding agent for the
Legacy Model VPS. It monitors Docker service health, API readiness, recent
API/worker/database errors, repository state, and periodic model statistics. It
sends proposals only to one private Telegram chat.

This is **not the same persistent ChatGPT conversation** running inside the VPS.
ChatGPT account history is not automatically transferred into an API application.
The Guardian instead receives a committed project charter, current repository
files, redacted logs, health snapshots, and the exact incident being investigated.
That gives it durable project-specific context without exposing private chat
history or production secrets.

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
  -> existing safe VPS deployment script
  -> automatic revert commit and rollback deployment if deployment fails
  -> private Telegram completion/failure report
```

Strategy and performance observations never enter the patch pipeline. They are
advisory only and require a later explicit development decision.

## What it monitors

- `database`, `api`, and `worker` Docker Compose states;
- `/health/ready` response;
- recent Docker logs containing tracebacks, exceptions, failed operations,
  integrity errors, OAuth failures, connection failures, and unhealthy states;
- current Git commit and whether the live checkout has uncommitted changes;
- Demo and Real aggregate metrics at the configured strategy review interval;
- repeated incidents through a durable SQLite fingerprint/cooldown store.

The Guardian does not treat normal tick, prefilter, cadence or heartbeat messages
as incidents.

## Approval rules

Only the exact positive numeric `GUARDIAN_TELEGRAM_ADMIN_CHAT_ID` may approve or
reject. The dedicated Telegram bot sends only private messages and is never added
to a channel or group.

- **Approve fix:** starts isolated patching for an incident that the diagnostic
  agent classified as a code change.
- **Reject:** closes the incident without changing code or deployment.
- **Details:** sends stored redacted evidence and structured analysis.
- **Acknowledge:** records that strategy/performance advice was read. It does not
  change strategy code or configuration.
- `/status`: returns Guardian, API, Git and Docker status.

Approvals are atomic in SQLite. Repeated callbacks cannot start the same patch
twice, and only one remediation may run at a time. If the VPS or Guardian service
restarts during an approved remediation, that incident is marked `interrupted`
and will not resume without a new diagnosis and approval.

## Security boundaries

The Guardian cannot automatically modify:

- `.env` or `.env.*` files;
- tokens, users, runtime credentials or secrets;
- `model_artifacts`, deployment state or backups;
- Docker volumes or PostgreSQL data;
- account balances, OAuth sessions or Deriv credentials;
- Dockerfiles, Compose definitions, migrations, deployment/update scripts,
  systemd units, GitHub workflows, `config.yaml`, the project charter, or the
  Guardian's sandbox/security/patch-control modules.

Those infrastructure/control files remain manual-review work even when an
incident proposal is approved.

The Guardian rejects patches containing volume deletion, `down -v`, force pushes,
database drops/truncation, root deletion, or other destructive operations. It
never sends raw Authorization headers, OpenAI keys, Telegram tokens, Deriv tokens
or full account IDs to the model.

### Generated-code sandbox

Generated code is never executed directly as root on the VPS before it is trusted.
Every compilation/test runs in a disposable container with:

- no network;
- no Docker socket;
- no environment secrets;
- read-only root filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- process, CPU, memory and file-descriptor limits;
- the temporary Git worktree mounted read-only;
- a separate ephemeral in-memory copy for compilation/test writes.

Only Python compileall, unittest/pytest and shell syntax checks are accepted as
model-suggested tests. The existing production deployment script still performs
its normal full validation after an approved commit is pushed.

### Strategy-metrics privacy

Before scheduled strategy analysis, account-, balance-, token-, credential-,
session-, login-, email- and user-level structures are removed. Only aggregate
model-performance values are sent to OpenAI.

## Installation

### 1. Pull and deploy the repository release

Use the normal production deployment procedure first. The Guardian is a separate
systemd service and does not replace the API or worker containers.

### 2. Create a dedicated private Telegram bot

In Telegram, open **BotFather**, create a new bot, and retain the token privately.
Do not add this bot to the public channel. Open the new bot, press **Start**, and
send `/status` once.

### 3. Create an OpenAI API key

Create an API key for this service and keep it private. A ChatGPT subscription and
OpenAI API billing are separate. The Guardian uses configurable diagnosis, coding
and reviewer models through the Responses API.

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
It does not reinstall the application/Playwright stack.

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

The installer compiles the Guardian, runs safety tests, checks Git origin write
access, installs the hardened systemd unit, starts it, and prints status. A private
Telegram startup message should arrive.

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
callback offsets, cooldown fingerprints, result metadata and the daily API-call
counter—not production Deriv credentials.

## Model and cost configuration

Defaults:

```text
GUARDIAN_DIAGNOSIS_MODEL=gpt-5.4-mini
GUARDIAN_CODING_MODEL=gpt-5.3-codex
GUARDIAN_REVIEWER_MODEL=gpt-5.4-mini
GUARDIAN_MAX_AI_CALLS_PER_DAY=30
```

The daily limit includes diagnoses, coding, independent review and any structured
output fallback. When exhausted, deterministic monitoring continues but new AI
analysis waits for the next UTC day. Models and the limit can be changed in
`/etc/legacy-model-guardian.env`, followed by a service restart. Use model IDs
available to the OpenAI API project.

## Deployment behavior

An approved fix is based on the exact `origin/main` commit recorded during the
incident. If main moves before or during patching, the Guardian refuses the push
and requires a new diagnosis. It never force-pushes.

The live checkout must be clean. After push, `scripts/deploy_vps.sh` performs the
production validation and deployment. If deployment fails and `origin/main` is
still the Guardian commit, the Guardian creates a normal revert commit, pushes it,
and attempts the safe deployment again. It reports the original failure and the
rollback result privately.

## Strategy advisor boundaries

Every configured interval, the Guardian reviews both Demo and Real aggregate
metrics and recent redacted error events. Advice focuses on sample size, realized
win rate, payout/break-even economics, drawdown, missed executions and regime
evidence.

It does not:

- promise profit;
- silently change thresholds;
- activate Real accounts;
- change stakes, TP/SL or recovery settings;
- treat virtual wins as actual financial performance;
- promote an observation to production without a separate implementation request.

## What the operator remains responsible for

- keeping OpenAI API billing/key active;
- keeping the dedicated Telegram bot token private;
- maintaining Git write authentication on the VPS;
- reviewing every proposal before approval;
- deciding whether strategy advice deserves controlled testing;
- handling Deriv/provider incidents that cannot be fixed in repository code;
- reviewing failed deployments or rollbacks that require manual infrastructure
  intervention.

## Recommended first week

The implementation supports approved main pushes and deployments immediately, but
the safer commissioning mode is:

```text
GUARDIAN_DRY_RUN=true
GUARDIAN_ALLOW_MAIN_PUSH=false
GUARDIAN_AUTO_DEPLOY=false
```

Observe diagnoses and private Telegram behavior first. Then enable main push and
deployment one setting at a time. The private approval button remains mandatory in
all modes.
