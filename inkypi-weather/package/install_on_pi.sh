#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INKYPI_DIR="$SCRIPT_DIR/InkyPi"

cd "$INKYPI_DIR"

if [[ ! -f ".env" ]]; then
  echo "ERROR: Missing InkyPi/.env. Create it with OPEN_WEATHER_MAP_SECRET before installing." >&2
  exit 1
fi

if ! grep -q '^OPEN_WEATHER_MAP_SECRET=' ".env"; then
  echo "ERROR: InkyPi/.env does not contain OPEN_WEATHER_MAP_SECRET." >&2
  exit 1
fi

echo "Installing InkyPi for Waveshare 7.3-inch Spectra 6 / E6 full-color display..."
echo "This assumes WiFi was already configured in Raspberry Pi Imager or raspi-config."
sudo bash install/install.sh -W epd7in3e
