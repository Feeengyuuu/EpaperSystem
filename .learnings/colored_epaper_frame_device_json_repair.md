# ColoredEpaperFrame device.json truncation repair

## [LRN-20260601-003] production_debug

**Logged**: 2026-06-01T00:45:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
If ColoredEpaperFrame has SSH but no HTTP, check `device.json` validity before assuming a network outage.

### Details
After the device was powered back on, `192.168.1.188` responded to ping and SSH but port 80 was closed. `inkypi.service` was stuck in `activating (auto-restart)` because `/usr/local/inkypi/src/config/device.json` had been truncated at 57600 bytes during a prior shutdown, producing `json.decoder.JSONDecodeError` at line 591. Restoring a valid `device.json`, validating it with `python3 -m json.tool`, and waiting for systemd's next retry brought Waitress back on port 80.

### Suggested Action
For future ColoredEpaperFrame checks, distinguish layers quickly: ping plus SSH proves device/network, port 80 plus `/playlist` proves InkyPi HTTP, and `journalctl -u inkypi` reveals config parse failures. Before replacing a broken config, back it up, validate the replacement JSON locally and remotely, then atomically move it into `/usr/local/inkypi/src/config/device.json`.

### Metadata
- Source: production_debug
- Related Files: `inkypi-weather/package/InkyPi/src/config.py`, `inkypi-weather/package/InkyPi/src/config/device.json`
- Tags: colored-epaper-frame, epaperpod, device-json, config-repair, service-startup
