# Steam profile dashboard helper assertions

## [LRN-20260531-002] environment

**Logged**: 2026-05-31
**Priority**: low
**Status**: active
**Area**: epaper

### Summary
For tiny `steam_profile_dashboard.py` helper checks, avoid importing the full plugin when dependency setup is not needed.

### Details
Direct import of `plugins.steam_profile_dashboard.steam_profile_dashboard` with the default Python can fail before reaching the target helper because transitive imports require packages such as `requests`. `python -m py_compile` can also fail in this workspace while writing `__pycache__`.

### Suggested Action
For narrow helper behavior, compile the source bytes for syntax and extract only the target function body for assertions. This avoids pyc writes and unrelated dependency imports.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Tags: inkypi, steam-profile-dashboard, python, permissions, dependencies

---
