#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE=${BASH_SOURCE[0]}
while [[ -h "$SOURCE" ]]; do
  DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)
PROJECT_DIR=$(cd -P "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)
SRC_PATH="$PROJECT_DIR/src"

APPNAME="inkypi"
INSTALL_ROOT="/opt/$APPNAME"
INSTALL_PATH="$INSTALL_ROOT/current"
BINPATH="/usr/local/bin"
STATE_ROOT="/var/lib/inkypi"
CACHE_ROOT="/var/cache/inkypi"
ENV_ROOT="/etc/inkypi"
CONFIG_DIR="/var/lib/inkypi/config"
DATA_DIR="/var/lib/inkypi/data"
DISPLAY_DIR="/var/lib/inkypi/display"
PLUGIN_DIR="/var/lib/inkypi/plugins"
RUNTIME_ENV_FILE="/etc/inkypi/inkypi.env"
APT_REQUIREMENTS_FILE="$SCRIPT_DIR/debian-requirements.txt"
PRIVILEGED_UNIT_DIR="$SCRIPT_DIR/privileged"
PRIVILEGED_BROKER_TARGET="/usr/local/libexec/inkypi-privileged"

WS_TYPE=""
REBOOT_PROMPT=true
TEMP_ROOT=""

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && tput colors >/dev/null 2>&1; then
  bold=$(tput bold)
  normal=$(tput sgr0)
  red=$(tput setaf 1)
  green=$(tput setaf 2)
else
  bold=""
  normal=""
  red=""
  green=""
fi

usage() {
  cat <<'EOF'
Usage: sudo bash install/install.sh [options]

Options:
  -W, --waveshare <model>  Use a packaged Waveshare driver, for example epd7in3e.
  --no-reboot-prompt      Do not ask to reboot after installation.
  -h, --help              Show this help.
EOF
}

success() {
  echo -e "${green}$1${normal}"
}

fail() {
  echo -e "${red}$1${normal}" >&2
  exit 1
}

cleanup() {
  if [[ -n "$TEMP_ROOT" && -d "$TEMP_ROOT" ]]; then
    rm -rf "$TEMP_ROOT"
  fi
}
trap cleanup EXIT

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -W|--waveshare)
        [[ $# -ge 2 ]] || fail "Option $1 requires a Waveshare model."
        WS_TYPE="$2"
        shift 2
        ;;
      --no-reboot-prompt)
        REBOOT_PROMPT=false
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown option: $1"
        ;;
    esac
  done
  if [[ -n "$WS_TYPE" && ! "$WS_TYPE" =~ ^epd[A-Za-z0-9_]+$ ]]; then
    fail "Unsafe Waveshare model name: $WS_TYPE"
  fi
}

check_permissions() {
  [[ "$EUID" -eq 0 ]] || fail "Installation requires root privileges."
}

ensure_service_user() {
  if ! id -u "$APPNAME" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/inkypi --create-home \
      --shell /usr/sbin/nologin "$APPNAME"
  fi
  for group in gpio spi video render; do
    if getent group "$group" >/dev/null 2>&1; then
      usermod -a -G "$group" "$APPNAME"
    fi
  done
  install -d -o root -g root -m 0755 "$INSTALL_ROOT"
  install -d -o inkypi -g inkypi -m 0750 \
    "$STATE_ROOT" \
    "$CONFIG_DIR" \
    "$DATA_DIR" \
    "$DISPLAY_DIR" \
    "$PLUGIN_DIR" \
    "$STATE_ROOT/update" \
    "$CACHE_ROOT"
  chown -R -h inkypi:inkypi "$STATE_ROOT" "$CACHE_ROOT"
  chmod -R u+rwX,go-rwx "$STATE_ROOT" "$CACHE_ROOT"
  normalize_durable_font_permissions
  install -d -o root -g root -m 0700 "$STATE_ROOT/update"
  install -d -o root -g inkypi -m 0770 "$ENV_ROOT"
  if [[ ! -e "$RUNTIME_ENV_FILE" ]]; then
    install -o inkypi -g inkypi -m 0600 /dev/null "$RUNTIME_ENV_FILE"
  fi

  local -a legacy_env_candidates=("/usr/local/inkypi/.env")
  local -a merge_args=()
  local legacy_src=""
  local env_candidate
  if [[ -e "/usr/local/inkypi/src" ]]; then
    legacy_src="$(readlink -f -- "/usr/local/inkypi/src")"
    if [[ -d "$legacy_src" && "$(basename "$legacy_src")" == "src" ]]; then
      legacy_env_candidates+=("$(dirname "$legacy_src")/.env")
    fi
  fi
  legacy_env_candidates+=("$PROJECT_DIR/.env")
  for env_candidate in "${legacy_env_candidates[@]}"; do
    if [[ -f "$env_candidate" && ! -L "$env_candidate" ]]; then
      merge_args+=(--merge-from "$env_candidate")
    fi
  done
  if ((${#merge_args[@]})); then
    python3 "$SCRIPT_DIR/configure_api_keys.py" --env-file "$RUNTIME_ENV_FILE" \
      "${merge_args[@]}"
  fi
  chown inkypi:inkypi "$RUNTIME_ENV_FILE"
  chmod 0600 "$RUNTIME_ENV_FILE"
}

normalize_durable_font_permissions() {
  python3 "$SCRIPT_DIR/lib/font_permissions.py" "$DATA_DIR"
}

validate_packaged_driver() {
  [[ -n "$WS_TYPE" ]] || return 0
  local driver="$SRC_PATH/display/waveshare_epd/$WS_TYPE.py"
  local epdconfig="$SRC_PATH/display/waveshare_epd/epdconfig.py"
  [[ -f "$driver" ]] || fail "Waveshare driver is not packaged: $driver"
  [[ -f "$epdconfig" ]] || fail "Packaged Waveshare epdconfig.py is missing."
}

enable_interfaces() {
  local boot_config="/boot/firmware/config.txt"
  if [[ -f "$boot_config" ]]; then
    sed -i 's/^#\?dtparam=spi=.*/dtparam=spi=on/' "$boot_config"
    sed -i 's/^#\?dtparam=i2c_arm=.*/dtparam=i2c_arm=on/' "$boot_config"
    if [[ -n "$WS_TYPE" ]] && ! grep -Eq '^[[:space:]]*dtoverlay=spi0-2cs' "$boot_config"; then
      printf '\ndtoverlay=spi0-2cs\n' >> "$boot_config"
    fi
  fi
  if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_spi 0
    raspi-config nonint do_i2c 0
  fi
}

install_system_dependencies() {
  [[ -f "$APT_REQUIREMENTS_FILE" ]] || fail "Missing $APT_REQUIREMENTS_FILE"
  apt-get update
  xargs -r -a "$APT_REQUIREMENTS_FILE" apt-get install -y
  apt-get install -y earlyoom
  systemctl enable --now earlyoom
  if [[ -r /etc/os-release ]] && grep -q '^VERSION_ID="\?12"\?$' /etc/os-release; then
    apt-get install -y zram-tools
    printf 'ALGO=zstd\nPERCENT=60\n' > /etc/default/zramswap
    systemctl enable --now zramswap
  fi
}

install_config() {
  local target="$CONFIG_DIR/device.json"
  if [[ -f "$target" ]]; then
    chown inkypi:inkypi "$target"
    chmod 0600 "$target"
    return
  fi
  local source="$SCRIPT_DIR/config_base/device.json"
  local candidate
  for candidate in "/usr/local/inkypi/src/config/device.json" "$SRC_PATH/config/device.json"; do
    if [[ -f "$candidate" ]] && python3 -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); raise SystemExit(0 if isinstance(value, dict) else 1)' "$candidate"; then
      source="$candidate"
      break
    fi
  done
  install -o inkypi -g inkypi -m 0600 "$source" "$target"
  if [[ -n "$WS_TYPE" ]]; then
    python3 - "$target" "$WS_TYPE" <<'PY'
import json
import os
from pathlib import Path
import sys
import tempfile

path = Path(sys.argv[1])
model = sys.argv[2]
document = json.loads(path.read_text(encoding="utf-8"))
document["display_type"] = model
defaults = {
    "epd7in3e": {"resolution": [800, 480], "orientation": "horizontal"},
    "epd7in5_V2": {"resolution": [800, 480], "orientation": "horizontal"},
}
document.update(defaults.get(model, {}))
fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".device.", suffix=".tmp")
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
    chown inkypi:inkypi "$target"
    chmod 0600 "$target"
  fi
}

install_privileged_broker() {
  install -d -o root -g root -m 0755 /usr/local/libexec
  install -o root -g root -m 0755 \
    "$PRIVILEGED_UNIT_DIR/inkypi_privileged.py" "$PRIVILEGED_BROKER_TARGET"
  install -o root -g root -m 0644 \
    "$PRIVILEGED_UNIT_DIR/inkypi-privileged.socket" \
    /etc/systemd/system/inkypi-privileged.socket
  install -o root -g root -m 0644 \
    "$PRIVILEGED_UNIT_DIR/inkypi-privileged.service" \
    /etc/systemd/system/inkypi-privileged.service
  systemctl daemon-reload
  if systemctl is-active --quiet inkypi-privileged.service; then
    systemctl stop inkypi-privileged.service
  fi
  systemctl enable --now inkypi-privileged.socket
}

build_release_artifact() {
  TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/inkypi-install.XXXXXX")
  local artifact="$TEMP_ROOT/inkypi-release.zip"
  python3 "$SCRIPT_DIR/lib/release_archive.py" "$PROJECT_DIR" "$artifact"
  local sha256
  sha256=$(sha256sum "$artifact" | awk '{print $1}')
  local release_id
  release_id="$(date -u +%Y%m%dT%H%M%SZ)-${sha256:0:12}"
  python3 "$SCRIPT_DIR/inkypi-update" \
    --artifact "$artifact" \
    --sha256 "$sha256" \
    --release-id "$release_id"
}

ask_for_reboot() {
  success "${bold}InkyPi installation committed.${normal}"
  echo "Web UI: http://$(hostname).local"
  echo "Administrator token: sudo cat /var/lib/inkypi/data/security/bootstrap_admin.token"
  if [[ "$REBOOT_PROMPT" != "true" ]]; then
    echo "Reboot prompt skipped. Reboot once before relying on SPI/I2C hardware."
    return
  fi
  read -r -p "Reboot now? [y/N] " answer
  if [[ "${answer,,}" == "y" ]]; then
    reboot
  else
    echo "Reboot later with: sudo reboot"
  fi
}

parse_arguments "$@"
check_permissions
ensure_service_user
validate_packaged_driver
enable_interfaces
install_system_dependencies
install_config
install_privileged_broker
build_release_artifact
ask_for_reboot
