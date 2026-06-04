# ColoredEpaperFrame shutdown stability

## [LRN-20260531-003] deployment

**Logged**: 2026-05-31
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
After calling the InkyPi `/shutdown` reboot path on `ColoredEpaperFrame`, wait for a full stable service window before triggering manual plugin display refreshes.

### Details
During a `steam_profile_dashboard` deploy to `192.168.1.188`, `/shutdown` returned success and `/playlist` briefly returned HTTP 200 after the first restart. A manual `SteamDaily` refresh succeeded in the logs, but the earlier shutdown flow still caused a delayed `Stopping inkypi.service`, `stop-sigterm` timeout, SIGKILL, and another automatic service start. Final readiness required waiting for the second `waitress - Serving on http://0.0.0.0:80` and a fresh `/playlist` 200.

### Suggested Action
For future deploys on `ColoredEpaperFrame`, after using `/shutdown`, do not trigger `POST /display_plugin_instance` until `systemctl is-active inkypi` is active, `/playlist` returns 200, and recent `journalctl -u inkypi` shows the post-restart `waitress` process with no pending `Stopping inkypi.service` lines.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Tags: colored-epaper-frame, epaperpod, deploy, shutdown, systemd, waitress, verification

---

## [LRN-20260601-002] deployment

**Logged**: 2026-06-01T00:20:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Do not call `/shutdown` without `{"reboot": true}` on ColoredEpaperFrame.

### Details
The InkyPi `/shutdown` route executes `sudo shutdown -h now` unless the JSON body contains `{"reboot": true}`. Calling it with `{}` powers off `ColoredEpaperFrame`; after that, ping, SSH, and HTTP are unreachable until the device is physically power-cycled. The safe remote restart form is `POST /shutdown` with JSON `{"reboot": true}`, or avoid this endpoint entirely when only a Flask code reload is needed.

### Suggested Action
For future `ColoredEpaperFrame` deploys, use `Invoke-WebRequest -Method Post -ContentType application/json -Body '{"reboot": true}' http://192.168.1.188/shutdown` if a full reboot is intentional. Never use an empty JSON body for restart verification.

### Metadata
- Source: production_debug
- Related Files: `inkypi-weather/package/InkyPi/src/blueprints/settings.py`
- Tags: colored-epaper-frame, epaperpod, deploy, shutdown, reboot
