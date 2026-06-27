#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${EPAPERSYSTEM_REPO_URL:-https://github.com/Feeengyuuu/EpaperSystem.git}"
INSTALL_PARENT="${EPAPERSYSTEM_INSTALL_PARENT:-/opt}"
CHECKOUT_DIR="${EPAPERSYSTEM_CHECKOUT_DIR:-$INSTALL_PARENT/EpaperSystem}"
RELATIVE_BOOTSTRAP="inkypi-weather/package/InkyPi/install/bootstrap.sh"

usage() {
  cat <<EOF
Usage:
  sudo bash install.sh [bootstrap options]
  curl -fsSL https://raw.githubusercontent.com/Feeengyuuu/EpaperSystem/main/install.sh | sudo bash -s -- [bootstrap options]

Examples:
  sudo bash install.sh
  sudo bash install.sh --lang zh-CN
  sudo bash install.sh -W epd7in5_V2
  sudo bash install.sh --pimoroni

This root installer delegates to:
  $RELATIVE_BOOTSTRAP

If it is run through curl outside a checkout, it clones or updates:
  $CHECKOUT_DIR

Set EPAPERSYSTEM_CHECKOUT_DIR to choose a different checkout path.
EOF
}

script_path="${BASH_SOURCE[0]:-}"
script_dir=""
if [[ -n "$script_path" && -f "$script_path" ]]; then
  script_dir="$(cd -P "$(dirname "$script_path")" >/dev/null 2>&1 && pwd)"
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  if [[ -n "$script_dir" && -f "$script_dir/$RELATIVE_BOOTSTRAP" ]]; then
    echo
    echo "Bootstrap options:"
    bash "$script_dir/$RELATIVE_BOOTSTRAP" --help || true
  fi
  exit 0
fi

if [[ "$EUID" -ne 0 ]]; then
  if [[ -n "$script_path" && -f "$script_path" ]]; then
    echo "This installer needs sudo. Re-running with sudo..."
    exec sudo -E bash "$script_path" "$@"
  fi
  echo "This installer needs sudo. Run:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/Feeengyuuu/EpaperSystem/main/install.sh | sudo bash -s -- $*" >&2
  exit 1
fi

if [[ -n "$script_dir" && -f "$script_dir/$RELATIVE_BOOTSTRAP" ]]; then
  exec bash "$script_dir/$RELATIVE_BOOTSTRAP" "$@"
fi

if ! command -v git >/dev/null 2>&1; then
  echo "Installing git and ca-certificates..."
  apt-get update
  apt-get install -y git ca-certificates
fi

if [[ -d "$CHECKOUT_DIR/.git" ]]; then
  echo "Updating existing EpaperSystem checkout at $CHECKOUT_DIR..."
  git -C "$CHECKOUT_DIR" pull --ff-only
elif [[ -e "$CHECKOUT_DIR" && -n "$(find "$CHECKOUT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
  echo "Refusing to overwrite non-empty non-Git directory: $CHECKOUT_DIR" >&2
  echo "Set EPAPERSYSTEM_CHECKOUT_DIR to another path and retry." >&2
  exit 1
else
  mkdir -p "$(dirname "$CHECKOUT_DIR")"
  echo "Cloning EpaperSystem into $CHECKOUT_DIR..."
  git clone "$REPO_URL" "$CHECKOUT_DIR"
fi

bootstrap="$CHECKOUT_DIR/$RELATIVE_BOOTSTRAP"
if [[ ! -f "$bootstrap" ]]; then
  echo "Bootstrap installer not found after checkout: $bootstrap" >&2
  exit 1
fi

exec bash "$bootstrap" "$@"
