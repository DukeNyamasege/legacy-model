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
  -> Python compilation and allow-listed tests
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
- Demo and Real public metrics at the configured strategy review interval;
- repeated incidents through a durable SQLite fingerprint/cooldown store.

The Guardian does not treat normal tick, prefilter, cadence or heartbeat messages
as incidents.

## Approval rules

Only the exact numeric `GUARDIAN_TELEGRAM_ADMIN_CHAT_ID` may approve or reject.
The Telegram bot is dedicated to private Guardian messages and does not publish to
a channel.

- **Approve fix:** starts isolated patching for an incident that the diagnostic
  agent classified as a code change.
- **Reject:** closes the incident without changing code or deployment.
- **Details:** sends the stored redacted evidence and structured analysis.
- **Acknowledge:** records that strategy/performance advice was read. It does not
  change strategy code or configuration.
- `/status`: returns Guardian, API, Git and Docker status.

Approvals are atomic in SQLite. Repeated callbacks cannot start the same patch
twice, and only one remediation may run at a time.

## Security boundaries

The Guardian cannot intentionally modify:

- `.env` or `.env.*` files;
- tokens, users, runtime credentials or secrets;
- `model_artifacts`, deployment state or backups;
- Docker volumes or PostgreSQL data;
- account balances, OAuth sessions or Deriv credentials.

It rejects patches containing volume deletion, `down -v`, force pushes, database
drops/truncation, root deletion, or other destructive operations. It never sends
raw Authorization headers, OpenAI keys, Telegram tokens, Deriv tokens or full
account IDs to the model.

Model-generated test commands are limited to compilation, unittest/pytest,
`docker compose config`, shell syntax checks and JavaScript syntax checks. The
model cannot execute arbitrary shell commands.

## Installation

### 1. Pull and deploy the repository release

Use the normal production deployment procedure first. The Guardian is a separate
systemd service and does not replace the API or worker containers.

### 2. Create a dedicated private Telegram bot

In Telegram, open **BotFather**, create a new bot, and retain the token privately.
Do not add this bot to the public channel. Open the new bot, press **Start**, and
send `/status` once.

### 3. Create an OpenAI API key

Create an API key for this service and keep it private. The ChatGPT subscription
and API billing are separate. The Guardian uses configurable diagnosis, coding and
reviewer models.

### 4. Run the installer once to prepare files

```bash
cd /root/legacy-model
sh scripts/install_guardian.sh
```

The first run creates:

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

Copy only your numeric private `chat_id` into:

```text
GUARDIAN_TELEGRAM_ADMIN_CHAT_ID=...
```

### 7. Complete installation

```bash
cd /root/legacy-model
sh scripts/install_guardian.sh
```

The installer compiles the Guardian, runs safety tests, checks Git origin access,
installs the systemd unit, starts it, and prints status. A private Telegram startup
message should arrive.

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

The service state database is:

```text
/var/lib/legacy-model-guardian/guardian.sqlite3
```

Do not delete it during normal upgrades. It contains incident status, approval
state, callback offset, cooldown fingerprints and result metadata—not production
Deriv credentials.

## Model configuration

Defaults:

```text
GUARDIAN_DIAGNOSIS_MODEL=gpt-5.4-mini
GUARDIAN_CODING_MODEL=gpt-5.3-codex
GUARDIAN_REVIEWER_MODEL=gpt-5.4-mini
```

Models can be changed in `/etc/legacy-model-guardian.env`, followed by a service
restart. Use a model available to the API project.

## Deployment behavior

An approved fix is based on the exact `origin/main` commit recorded during the
incident. If main moves before or during patching, the Guardian refuses the push
and requests a new diagnosis. It never force-pushes.

The live checkout must be clean. After push, the existing
`scripts/deploy_vps.sh` performs production validation and deployment. If that
deployment fails and `origin/main` is still the Guardian commit, the Guardian
creates a normal revert commit, pushes it, and attempts the safe deployment again.
It reports both the original failure and rollback result privately.

## Strategy advisor boundaries

Every configured interval, the Guardian reviews both Demo and Real public metrics
and recent redacted error events. Advice should focus on evidence such as sample
size, realized win rate, payout economics, drawdown, missed executions and regime
changes.

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

Run with:

```text
GUARDIAN_DRY_RUN=true
GUARDIAN_ALLOW_MAIN_PUSH=false
GUARDIAN_AUTO_DEPLOY=false
```

Observe diagnoses and Telegram behavior. After confidence is established, enable
main push and deployment one at a time. The approval button remains mandatory in
all modes.
