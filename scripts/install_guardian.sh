#!/usr/bin/env sh
set -eu

REPO_DIR=${GUARDIAN_REPO_DIR:-/root/legacy-model}
INSTALL_DIR=/opt/legacy-model-guardian
VENV_DIR="$INSTALL_DIR/venv"
STATE_DIR=/var/lib/legacy-model-guardian
ENV_FILE=/etc/legacy-model-guardian.env
SERVICE_FILE=/etc/systemd/system/legacy-model-guardian.service

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "Run this installer as root."
[ -d "$REPO_DIR/.git" ] || fail "Repository not found at $REPO_DIR"

for command in git docker python3 systemctl; do
  command -v "$command" >/dev/null 2>&1 || fail "$command is required"
done

echo "============================================================"
echo "LEGACY MODEL GUARDIAN INSTALLER"
echo "============================================================"

echo "1. Create protected Guardian directories"
install -d -m 0700 "$INSTALL_DIR" "$STATE_DIR"

echo "2. Create isolated Python environment"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install -r "$REPO_DIR/requirements.txt"

echo "3. Prepare private environment file"
if [ ! -f "$ENV_FILE" ]; then
  install -m 0600 "$REPO_DIR/.env.guardian.example" "$ENV_FILE"
  echo "Created $ENV_FILE"
else
  chmod 0600 "$ENV_FILE"
  echo "Preserved existing $ENV_FILE"
fi

has_value() {
  key=$1
  value=$(sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r')
  [ -n "$value" ]
}

if ! has_value OPENAI_API_KEY \
  || ! has_value GUARDIAN_TELEGRAM_BOT_TOKEN \
  || ! has_value GUARDIAN_TELEGRAM_ADMIN_CHAT_ID; then
  echo ""
  echo "Guardian files are prepared but the service was not started."
  echo "Edit this protected file and add the three required private values:"
  echo "  nano $ENV_FILE"
  echo ""
  echo "Required:"
  echo "  OPENAI_API_KEY"
  echo "  GUARDIAN_TELEGRAM_BOT_TOKEN"
  echo "  GUARDIAN_TELEGRAM_ADMIN_CHAT_ID"
  echo ""
  echo "Do not paste those values into ChatGPT, GitHub, logs or screenshots."
  echo "After saving them, rerun:"
  echo "  sh $REPO_DIR/scripts/install_guardian.sh"
  exit 2
fi

echo "4. Validate repository and Guardian tests"
cd "$REPO_DIR"
"$VENV_DIR/bin/python" -m compileall -q guardian
"$VENV_DIR/bin/python" -m unittest -q guardian.tests.test_guardian

echo "5. Verify Git identity and origin access"
if ! git config user.name >/dev/null 2>&1; then
  git config user.name "Legacy Model Guardian"
fi
if ! git config user.email >/dev/null 2>&1; then
  git config user.email "guardian@derivadmin.site"
fi
git fetch origin main
git push --dry-run origin HEAD:main >/dev/null

echo "6. Install and start systemd service"
install -m 0644 "$REPO_DIR/deploy/legacy-model-guardian.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable legacy-model-guardian.service >/dev/null
systemctl restart legacy-model-guardian.service
sleep 5

echo "7. Verify service"
systemctl --no-pager --full status legacy-model-guardian.service || {
  journalctl -u legacy-model-guardian.service --no-pager -n 120
  fail "Guardian service did not start"
}

echo ""
echo "============================================================"
echo "GUARDIAN INSTALLED"
echo "============================================================"
echo "Service : legacy-model-guardian.service"
echo "State   : $STATE_DIR"
echo "Secrets : $ENV_FILE"
echo "Logs    : journalctl -u legacy-model-guardian -f"
echo "Status  : send /status to the private Guardian Telegram bot"
