#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${BASH_SOURCE[0]}
while [[ -h "$SOURCE" ]]; do
  DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)

INSTALL_DIR="/opt/inkypi/current"
SERVICE_NAME="inkypi.service"
RUNTIME_ENV_FILE="/etc/inkypi/inkypi.env"
FAILURES=0
WARNINGS=0
LANG_MODE="${INKYPI_LANG:-en}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lang)
      [[ $# -ge 2 ]] || { echo "--lang requires en or zh-CN" >&2; exit 1; }
      LANG_MODE="$2"
      shift 2
      ;;
    --zh-cn)
      LANG_MODE="zh-CN"
      shift
      ;;
    -h|--help)
      echo "Usage: bash install/healthcheck.sh [--lang en|zh-CN]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

ok() {
  echo "OK    $1"
}

warn() {
  WARNINGS=$((WARNINGS + 1))
  echo "WARN  $1"
}

fail() {
  FAILURES=$((FAILURES + 1))
  echo "FAIL  $1"
}

check_command() {
  if command -v "$1" >/dev/null 2>&1; then
    ok "Command available: $1"
  else
    fail "Missing command: $1"
  fi
}

check_path() {
  if [[ -e "$1" ]]; then
    ok "$2: $1"
  else
    fail "$2 missing: $1"
  fi
}

echo "InkyPi health check ($LANG_MODE)"
check_command python3
check_command curl
check_path "$INSTALL_DIR/src/inkypi.py" "Release source"
check_path "$INSTALL_DIR/install/inkypi-update" "Release updater"
check_path "$INSTALL_DIR/venv_inkypi/bin/python" "Release virtualenv"
check_path "$INSTALL_DIR/.release-id" "Release identity"
check_path "/usr/local/bin/inkypi" "Launcher"
check_path "/usr/local/sbin/inkypi-update" "Update command"
check_path "/var/lib/inkypi/config/device.json" "Device configuration"

if [[ -f "$RUNTIME_ENV_FILE" ]]; then
  ok "Runtime environment exists: $RUNTIME_ENV_FILE"
else
  warn "Runtime environment is absent; optional provider keys are unavailable."
fi

if [[ -x "$INSTALL_DIR/venv_inkypi/bin/python" && -f "$INSTALL_DIR/install/configure_api_keys.py" ]]; then
  if ! "$INSTALL_DIR/venv_inkypi/bin/python" \
    "$INSTALL_DIR/install/configure_api_keys.py" \
    --env-file "$RUNTIME_ENV_FILE" \
    --check \
    --lang "$LANG_MODE"; then
    warn "API key diagnostics failed."
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-enabled --quiet "$SERVICE_NAME"; then
    ok "systemd service is enabled"
  else
    warn "systemd service is not enabled"
  fi
  if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "systemd service is active"
  else
    fail "systemd service is not active"
  fi
  if systemctl is-active --quiet inkypi-privileged.socket; then
    ok "privileged broker socket is active"
  else
    fail "privileged broker socket is not active"
  fi
else
  warn "systemctl is unavailable; service checks skipped"
fi

EXPECTED_RELEASE=""
if [[ -f "$INSTALL_DIR/.release-id" ]]; then
  IFS= read -r EXPECTED_RELEASE < "$INSTALL_DIR/.release-id"
fi

if ready_json=$(curl --max-time 5 --fail --silent http://127.0.0.1/readyz); then
  if ready_values=$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print(d.get("release_id", "")); print(d.get("status", ""))' <<< "$ready_json"); then
    READY_RELEASE=$(sed -n '1p' <<< "$ready_values")
    READY_STATUS=$(sed -n '2p' <<< "$ready_values")
    if [[ -n "$EXPECTED_RELEASE" && "$READY_RELEASE" == "$EXPECTED_RELEASE" && ( "$READY_STATUS" == "ready" || "$READY_STATUS" == "degraded" ) ]]; then
      ok "readyz matches release $EXPECTED_RELEASE ($READY_STATUS)"
    else
      fail "readyz release/status mismatch: expected=$EXPECTED_RELEASE actual=$READY_RELEASE status=$READY_STATUS"
    fi
  else
    fail "readyz returned invalid JSON"
  fi
else
  fail "readyz did not respond"
fi

if curl --max-time 5 --fail --silent http://127.0.0.1/api/current_image >/dev/null; then
  ok "Current-image endpoint responded"
else
  warn "Current-image endpoint has no committed image yet"
fi

if [[ "$FAILURES" -eq 0 ]]; then
  ok "Health check passed with $WARNINGS warning(s)."
  exit 0
fi

echo "Health check failed: $FAILURES failure(s), $WARNINGS warning(s)." >&2
echo "Inspect with: sudo journalctl -u $SERVICE_NAME -n 120 --no-pager" >&2
exit 1
