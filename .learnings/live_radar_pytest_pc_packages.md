# LiveRadar pytest uses pc-packages

## [LRN-20260604-001] local_test_env

**Logged**: 2026-06-04T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: tests

### Summary
LiveRadar focused pytest can run locally by adding `.pc-packages` and `src` to `PYTHONPATH`.

### Details
The local `.venv`, `.venv-test`, `.venv-local`, and `.venv-codex` Python environments report `No module named pytest` when run directly. However, `inkypi-weather/package/InkyPi/.pc-packages` contains pytest and its dependencies, so the focused LiveRadar test suite runs with:

`$env:PYTHONPATH='.pc-packages;src'; .venv-test\Scripts\python.exe -m pytest tests\test_live_radar.py -q`

This passed `29` LiveRadar tests during the snapshot mini-card layout fix. Pytest may still warn that `.pytest_cache` is not writable; that warning does not block the test run.

### Suggested Action
For future LiveRadar test work, prefer the `.pc-packages` PYTHONPATH route before falling back to stub runners.

### Metadata
- Source: local verification
- Related Files: inkypi-weather/package/InkyPi/tests/test_live_radar.py
- Tags: live_radar, pytest, pc-packages, local-test-env
