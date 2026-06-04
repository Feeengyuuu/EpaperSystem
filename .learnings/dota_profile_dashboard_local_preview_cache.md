# Dota Profile Dashboard Local Preview Cache Fallback

## Learning
When creating local preview caches for new EpaperSystem plugins on this Windows machine, `Path.replace()` can fail with `PermissionError` inside `.tmp`. Cache writers should fall back to direct `write_text()` after writing the temporary file.

## Context
- Plugin: `inkypi-weather/package/InkyPi/src/plugins/dota_profile_dashboard/dota_profile_dashboard.py`
- Preview script: `tools/preview_dota_profile_dashboard.py`
- Failure: preview image rendered, but JSON cache commit failed at `tmp.replace(path)` with `[WinError 5] Access is denied`.

## Recommended Pattern
- Keep the atomic temp-file write path for normal environments.
- Catch `PermissionError` around the replace/rename step.
- On fallback, copy the temp JSON contents into the final path with `write_text()`, then best-effort unlink the temp file.
- Keep local preview and tests network-free with mock data when the requested task is UI-first and deployment is explicitly deferred.

## Verification
Run `python tools/preview_dota_profile_dashboard.py`; it should produce `.tmp/dota_profile_dashboard_preview.png` without requiring OpenDota network calls or pytest.
