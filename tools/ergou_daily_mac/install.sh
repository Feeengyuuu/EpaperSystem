#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${HOME}/.ergou-daily"
LAUNCH_AGENT="${HOME}/Library/LaunchAgents/com.ergou.daily-news.plist"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${APP_DIR}/.venv"

mkdir -p "${APP_DIR}" "${HOME}/Library/LaunchAgents" "${HOME}/Pictures/ErgouDaily"
cp "${SOURCE_DIR}/ergou_daily.py" "${APP_DIR}/ergou_daily.py"
cp "${SOURCE_DIR}/requirements.txt" "${APP_DIR}/requirements.txt"
mkdir -p "${APP_DIR}/rules"
cp "${SOURCE_DIR}/rules/ergou_daily_rules.md" "${APP_DIR}/rules/ergou_daily_rules.md"

if [ ! -f "${APP_DIR}/config.json" ]; then
  cp "${SOURCE_DIR}/config.example.json" "${APP_DIR}/config.json"
fi

if [ ! -f "${APP_DIR}/.env" ]; then
  cat > "${APP_DIR}/.env" <<'EOF'
# Add your key, then run: ~/.ergou-daily/run_now.sh
OPENAI_API_KEY=
# Optional if the Codex imagegen CLI is not under ~/.codex:
# ERGOU_IMAGE_GEN_CLI=/path/to/image_gen.py
EOF
  chmod 600 "${APP_DIR}/.env"
fi

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"

cat > "${APP_DIR}/run_now.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
"${VENV_DIR}/bin/python" "${APP_DIR}/ergou_daily.py" --config "${APP_DIR}/config.json" --env "${APP_DIR}/.env" "\$@"
EOF
chmod +x "${APP_DIR}/run_now.sh"

cat > "${APP_DIR}/uninstall.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
launchctl unload "${LAUNCH_AGENT}" 2>/dev/null || true
rm -f "${LAUNCH_AGENT}"
echo "Unloaded ${LAUNCH_AGENT}"
EOF
chmod +x "${APP_DIR}/uninstall.sh"

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  -e "s#__HOME__#${HOME}#g" \
  "${SOURCE_DIR}/launchd/com.ergou.daily-news.plist.template" > "${LAUNCH_AGENT}"

launchctl unload "${LAUNCH_AGENT}" 2>/dev/null || true
launchctl load "${LAUNCH_AGENT}"

if [ ! -f "${HOME}/.codex/skills/.system/imagegen/scripts/image_gen.py" ]; then
  echo "Warning: default img-2 CLI was not found at ~/.codex/skills/.system/imagegen/scripts/image_gen.py"
  echo "Set ERGOU_IMAGE_GEN_CLI in ${APP_DIR}/.env before running real generation."
fi

echo "Installed Ergou Daily automation."
echo "Config: ${APP_DIR}/config.json"
echo "Env:    ${APP_DIR}/.env"
echo "Run:    ${APP_DIR}/run_now.sh --dry-run"
echo "Daily:  12:00 local Mac time"
