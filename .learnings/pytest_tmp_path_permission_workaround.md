# Pytest tmp_path permission workaround

## [LRN-20260602-001] project_state

**Logged**: 2026-06-02T01:10:00-07:00
**Priority**: medium
**Status**: active
**Area**: testing

### Summary
On this Windows EpaperSystem checkout, pytest `tmp_path` and `--basetemp` can create directories that later become unreadable, causing permission errors unrelated to plugin behavior.

### Details
While preparing the push for the LiveRadar/GitHub queue work, the affected plugin suite reached the final BoxOffice smoke test but failed during `tmp_path` setup under `C:\Users\super\AppData\Local\Temp\pytest-of-LocalTest`. A rerun with `--basetemp=tmp\pytest-push-run-...` also failed during pytest teardown because that generated basetemp directory became unreadable. Replacing the test fixture with a fixed, ignored workspace directory (`tmp/box_office_render_chart_smoke`) made the suite pass.

### Suggested Action
For future Windows-side smoke tests in this repo, prefer explicit fixed directories under the ignored workspace `tmp/` tree over pytest `tmp_path` or `--basetemp`, and leave cleanup to normal ignored-runtime-state handling unless a specific directory is known safe to remove.

### Metadata
- Source: local test run
- Related Files: inkypi-weather/package/InkyPi/tests/test_box_office_top_movies.py
- Tags: pytest, tmp_path, windows, permissions, epapersystem
