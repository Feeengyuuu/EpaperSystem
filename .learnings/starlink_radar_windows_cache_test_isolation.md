# Starlink radar tests use unique cache files on Windows

## [LRN-20260605-003] test cache isolation

**Logged**: 2026-06-05T00:00:00-07:00
**Priority**: low
**Status**: active
**Area**: epaper-plugin-tests

### Summary
On Windows, plugin tests that write JSON cache files should prefer unique per-test cache filenames over deleting and reusing the same file.

### Details
While validating `starlink_radar`, a focused pytest run hit `PermissionError: [WinError 5]` when trying to unlink a previous temporary cache file. Switching the test helper to a UUID-suffixed cache filename avoided transient Windows file locking and kept the cache behavior test isolated.

### Suggested Action
- For new plugin cache tests, inject a per-test unique cache path.
- Avoid relying on `Path.unlink()` cleanup as the first step of a test when the previous run may still hold a handle.
- Keep runtime cache directories ignored by git.

### Metadata
- Source: observed_failure
- Related Files: `inkypi-weather/package/InkyPi/tests/test_starlink_radar.py`
- Tags: windows, pytest, cache, permission-error, starlink-radar
