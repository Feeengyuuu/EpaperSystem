# RefreshTask manual update in-flight request

## [LRN-20260602-001] deployment

**Logged**: 2026-06-02T23:20:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Manual display requests can arrive while the refresh thread is still finishing a playlist display. The refresh loop must check pending manual requests before sleeping for the next playlist interval, otherwise the HTTP retry can appear blocked even though the service is alive.

### Details
`RefreshTask._run` previously waited for the next interval before checking `manual_update_request`. If `/display_plugin_instance` was called while a scheduled display was in flight, the notify could be missed and the request would wait until timeout or the next cycle. A per-request event plus a pre-sleep manual-request check keeps retries bounded and lets the next loop process the manual request immediately.

### Suggested Action
When debugging stuck display/retry behavior, check whether a manual request arrived during an in-flight playlist display. Verify `manual_update` has its own completion event and that `_run` skips the sleep when `manual_update_request` is already pending.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/refresh_task.py`, `inkypi-weather/package/InkyPi/tests/test_refresh_task.py`
- Tags: colored-epaper-frame, refresh-task, manual-update, retry, concurrency
