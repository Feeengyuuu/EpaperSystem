# LiveRadar local test environment stubs

## [ERR-20260602-001] local_test_env

**Logged**: 2026-06-02T22:20:32-07:00
**Priority**: medium
**Status**: active
**Area**: tests

### Summary
Local LiveRadar verification may need a `.tmp` stub runner because the available Python environments can lack project test dependencies.

### Error
```text
C:\Python314\python.exe: No module named pytest
ModuleNotFoundError: No module named 'requests'
SyntaxError: invalid non-printable character U+FEFF
```

### Context
- `python -m pytest tests/test_live_radar.py` failed because default Python had no `pytest`.
- Project venvs and the bundled Codex Python also lacked `pytest`; imports through `BasePlugin` then needed `requests`.
- A PowerShell here-string piped to `python -` inserted a BOM on stdin in this session, so an inline smoke script failed before execution.
- A temporary `.tmp` Python runner with stubs for `plugins.base_plugin`, `plugins.context_cache`, and minimal `utils.*` modules successfully verified `_draw_snapshot_mini_card` without changing the environment.

### Suggested Fix
For narrow LiveRadar rendering checks on this machine, prefer an ASCII `.tmp` stub runner plus `PYTHONDONTWRITEBYTECODE=1` when pytest/dependencies are unavailable; delete the runner after use. Use full pytest only when a populated project test environment is available.

### Metadata
- Reproducible: unknown
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: live_radar, pytest, requests, powershell, bom, stub-runner

---
