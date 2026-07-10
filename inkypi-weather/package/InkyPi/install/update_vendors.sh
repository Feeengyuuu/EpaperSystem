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

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      echo "Usage: bash install/update_vendors.sh"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
fi

declare -a VENDORS=(
  "Select2 CSS|https://cdnjs.cloudflare.com/ajax/libs/select2/4.1.0-beta.1/css/select2.min.css|907f4395f54e25a1da1181672f1a498e98b26f7bfc6dcb6c209a737472451e49|src/static/styles/select2.min.css"
  "Select2 JS|https://cdnjs.cloudflare.com/ajax/libs/select2/4.1.0-beta.1/js/select2.min.js|9c04b5c034013c1a9ad5f9d9abcc1dd59e8237e3e09875cb15d328d20da961fd|src/static/scripts/select2.min.js"
  "jQuery|https://code.jquery.com/jquery-3.6.0.min.js|ff1523fb7389539c84c65aba19260648793bb4f5e29329d2ee8804bc37a3fe6e|src/static/scripts/jquery.min.js"
  "Chart.js|https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js|206b6e8bb00fc7bba2c7ee80ca41db3e9e05ba7be0aa35abeba9cfd5357f5d0e|src/static/scripts/chart.js"
  "FullCalendar|https://cdn.jsdelivr.net/npm/fullcalendar@6.1.17/index.global.min.js|f9fa1addb8dea87e99616898f3422e6ddf931f097e80c031c3e0deafbce91074|src/static/scripts/calendar.min.js"
)

for vendor in "${VENDORS[@]}"; do
  IFS='|' read -r name url expected_sha relative_output <<< "$vendor"
  output="$PROJECT_DIR/$relative_output"
  output_dir=$(dirname "$output")
  mkdir -p "$output_dir"
  temporary=$(mktemp "$output_dir/.vendor.XXXXXX")
  cleanup() {
    rm -f "$temporary"
  }
  trap cleanup EXIT

  echo "Fetching pinned $name..."
  curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --proto '=https' \
    --tlsv1.2 \
    --output "$temporary" \
    "$url"
  actual_sha=$(sha256sum "$temporary" | awk '{print $1}')
  if [[ "$actual_sha" != "$expected_sha" ]]; then
    echo "SHA256 mismatch for $name: expected $expected_sha, got $actual_sha" >&2
    exit 1
  fi
  chmod 0644 "$temporary"
  mv -f "$temporary" "$output"
  trap - EXIT
  echo "Verified $relative_output"
done

echo "All pinned vendor assets are verified."
