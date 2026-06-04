# Dota Profile Live Deploy Key And OpenDota Data Limits

## Learning
For `ColoredEpaperFrame` at `192.168.1.188`, default Windows SSH identities may be absent even when the host is reachable. Use the project deploy key `.ssh/epaperpod_codex_20260525` with `IdentitiesOnly=yes` for SSH/SCP.

## Context
- Plugin: `dota_profile_dashboard`
- Device: `ColoredEpaperFrame`
- Host: `192.168.1.188`
- Runtime path: `/usr/local/inkypi/src/plugins/dota_profile_dashboard`
- Instance: `DailyDoseOfDay / DotaProfile`

## Recommended Pattern
- Use `C:\Windows\System32\OpenSSH\ssh.exe -i .ssh\epaperpod_codex_20260525 -o IdentitiesOnly=yes`.
- Upload plugin runtime files with `scp.exe` to `/usr/local/inkypi/src/plugins/<plugin_id>/`.
- Validate with `/usr/local/inkypi/venv_inkypi/bin/python -m py_compile` and an import smoke test.
- Restart `inkypi`, wait for `/playlist`, then call `/add_plugin` and `/display_plugin_instance`.
- Compare `/plugin_instance_image/...` with `/api/current_image` by hash after the manual display.

## Verification
`DotaProfile` was added to `DailyDoseOfDay`, rendered through `POST /display_plugin_instance`, and `/plugin_instance_image/DailyDoseOfDay/dota_profile_dashboard/DotaProfile` matched `/api/current_image` by SHA256. OpenDota returned profile/avatar data but no public recent match/win-loss totals for account `216121110`; records endpoints returned 404 and the plugin degraded without blocking render.
