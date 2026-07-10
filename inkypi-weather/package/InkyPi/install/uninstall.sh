#!/usr/bin/env bash
set -Eeuo pipefail

APPNAME="inkypi"
INSTALL_ROOT="/opt/$APPNAME"
LEGACY_INSTALL_ROOT="/usr/local/inkypi"
ETC_ROOT="/etc/inkypi"
STATE_ROOT="/var/lib/inkypi"
CACHE_ROOT="/var/cache/inkypi"
LAUNCHER="/usr/local/bin/inkypi"
UPDATE_BIN="/usr/local/sbin/inkypi-update"
MAIN_UNIT="/etc/systemd/system/inkypi.service"
PRIVILEGED_SOCKET_FILE="/etc/systemd/system/inkypi-privileged.socket"
PRIVILEGED_SERVICE_FILE="/etc/systemd/system/inkypi-privileged.service"
PRIVILEGED_BROKER="/usr/local/libexec/inkypi-privileged"

PURGE=false
ASSUME_YES=false

usage() {
  cat <<'EOF'
Usage: sudo bash install/uninstall.sh [--purge] [--yes]

By default, releases and service files are removed while /etc/inkypi,
/var/lib/inkypi, and /var/cache/inkypi are preserved. --purge removes those
mutable paths and requires an additional explicit confirmation.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge)
      PURGE=true
      shift
      ;;
    --yes)
      ASSUME_YES=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

[[ "$EUID" -eq 0 ]] || { echo "Uninstall requires root privileges." >&2; exit 1; }

if [[ "$ASSUME_YES" != "true" ]]; then
  read -r -p "Remove InkyPi application releases and services? [y/N] " confirmation
  [[ "${confirmation,,}" == "y" ]] || { echo "Uninstall cancelled."; exit 1; }
fi

if [[ "$PURGE" == "true" ]]; then
  if [[ "$ASSUME_YES" != "true" ]]; then
    read -r -p "Type PURGE to permanently delete configuration, data, and cache: " purge_confirmation
    [[ "$purge_confirmation" == "PURGE" ]] || { echo "Purge cancelled."; exit 1; }
  fi
fi

if systemctl is-active --quiet inkypi.service; then
  systemctl stop inkypi.service
fi
if systemctl is-enabled --quiet inkypi.service; then
  systemctl disable inkypi.service
fi
if systemctl is-active --quiet inkypi-privileged.service; then
  systemctl stop inkypi-privileged.service
fi
if systemctl is-active --quiet inkypi-privileged.socket; then
  systemctl stop inkypi-privileged.socket
fi
if systemctl is-enabled --quiet inkypi-privileged.socket; then
  systemctl disable inkypi-privileged.socket
fi

rm -f \
  "$MAIN_UNIT" \
  "$PRIVILEGED_SOCKET_FILE" \
  "$PRIVILEGED_SERVICE_FILE" \
  "$PRIVILEGED_BROKER" \
  "$LAUNCHER" \
  "$UPDATE_BIN"
rm -rf "$INSTALL_ROOT" "$LEGACY_INSTALL_ROOT"
systemctl daemon-reload

if [[ "$PURGE" == "true" ]]; then
  rm -rf "$ETC_ROOT" "$STATE_ROOT" "$CACHE_ROOT"
  if id -u inkypi >/dev/null 2>&1; then
    userdel inkypi
  fi
  echo "InkyPi application and mutable state were purged."
else
  echo "Preserving /etc/inkypi, /var/lib/inkypi, and /var/cache/inkypi."
  echo "InkyPi application files were removed. Use --purge for explicit data deletion."
fi
