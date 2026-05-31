# Flight radar multi-source fallback and service stop timeout

## [LRN-20260530-131] best_practice

**Logged**: 2026-05-30T21:25:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Use ADSB.lol, Airplanes.live, then OpenSky as the free flight-radar source order; keep FlightAware and RapidAPI as key-gated optional fallbacks.

### Details
On `ColoredEpaperFrame` at `192.168.1.188`, the new `flight_radar` plugin rendered an `(800, 480)` preview and live-tested the Bay Area source set. ADSB.lol returned 127 aircraft in 789 ms, Airplanes.live returned 135 in 735 ms, and OpenSky returned 207 in 793 ms. FlightAware and RapidAPI were correctly skipped without keys. The plugin should keep short per-source timeouts and stale-cache fallback so a bad aviation API does not block display rotation.

During deployment, `sudo -n systemctl stop inkypi` timed out after 240 seconds because an existing Chrome screenshot refresh for `AwesomeWeather` had already timed out and left child processes for systemd to kill. Treat stop timeout as a possible Chromium-child cleanup issue, verify `systemctl is-active`, then continue only after the service is stopped or restarted.

### Suggested Action
For future flight-radar work, test sources individually on the Pi runtime before changing playlist config. Avoid inline PowerShell `python -c` quoting for remote HTTP checks; write a temporary script with `apply_patch`, upload with `scp`, and run it with `/usr/local/inkypi/venv_inkypi/bin/python`.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/flight_radar/flight_radar.py`, `inkypi-weather/package/InkyPi/src/plugins/flight_radar/settings.html`
- Tags: flight-radar, adsb-lol, airplanes-live, opensky, flightaware, rapidapi, service-stop, chromium, colored-epaper-frame

---
