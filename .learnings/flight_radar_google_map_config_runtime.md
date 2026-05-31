# Flight radar Google map key and config write order

## [LRN-20260530-132] correction

**Logged**: 2026-05-30T22:35:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
When changing `device.json` for InkyPi plugin settings on `ColoredEpaperFrame`, stop the service before writing config; otherwise the running process can write its old in-memory config back during shutdown.

### Details
`Google_KEY` in `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/.env` was validated against Google Static Maps without printing the key. The `flight_radar` plugin needed to recognize `Google_KEY` as an alias for `GOOGLE_MAPS_API_KEY`.

An initial config update to switch `SkyRadar` to `mapMode=google_static` was overwritten during service restart because the old `inkypi` process still held the previous `device.json` state. The correct order is:

1. `sudo -n systemctl stop inkypi`
2. Run the config update script.
3. Inspect `device.json` directly.
4. `sudo -n systemctl start inkypi`
5. Wait for Waitress or `/playlist`; `systemctl is-active` is not enough.

For direct render smoke tests, pass a real or test `device_config` into `_render(...)`; otherwise map rendering cannot read `.env` keys and will silently fall back to the offline stylized map.

### Suggested Action
For future remote plugin config changes, do not edit `device.json` while `inkypi` is running. Use temporary scripts uploaded via `scp`, avoid printing secrets, and validate actual rendered previews on the Pi runtime.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/flight_radar/flight_radar.py`, `.tmp/update_flight_radar_zoom_settings.py`, `.tmp/flight_radar_remote_test.py`
- Tags: flight-radar, google-static-maps, google-key, device-json, service-restart, colored-epaper-frame

---
