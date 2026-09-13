#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Share the beginner wizard; provider credentials can be added after login.
exec bash "$SCRIPT_DIR/InkyPi/install/bootstrap.sh" "$@"
