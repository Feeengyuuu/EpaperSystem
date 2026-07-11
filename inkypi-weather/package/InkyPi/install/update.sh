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

RELEASE_ID=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-id)
      [[ $# -ge 2 ]] || { echo "--release-id requires a value" >&2; exit 1; }
      RELEASE_ID="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: sudo bash install/update.sh [--release-id SAFE_ID]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

[[ "$EUID" -eq 0 ]] || { echo "update.sh must run as root" >&2; exit 1; }
if [[ -n "$RELEASE_ID" && ! "$RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
  echo "Unsafe release id: $RELEASE_ID" >&2
  exit 1
fi

TEMP_ROOT=$(mktemp -d /tmp/inkypi-update.XXXXXX)
cleanup() {
  rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT
ARTIFACT="$TEMP_ROOT/inkypi-release.zip"

python3 "$SCRIPT_DIR/lib/release_archive.py" "$PROJECT_DIR" "$ARTIFACT"

SHA256=$(sha256sum "$ARTIFACT" | awk '{print $1}')
if [[ -z "$RELEASE_ID" ]]; then
  RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-${SHA256:0:12}"
fi

UPDATER="/usr/local/sbin/inkypi-update"
if [[ ! -x "$UPDATER" ]]; then
  UPDATER="$SCRIPT_DIR/inkypi-update"
fi
python3 "$UPDATER" \
  --artifact "$ARTIFACT" \
  --sha256 "$SHA256" \
  --release-id "$RELEASE_ID"

echo "Update committed: $RELEASE_ID"
