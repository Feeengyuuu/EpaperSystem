# Daily AI News theme-aware cache refresh

## [LRN-20260527-056] environment

**Logged**: 2026-05-27T22:50:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Theme-aware InkyPi plugin images can display stale day/night colors if the playlist reuses an old cached PNG and the plugin instance is not configured to refresh on display.

### Details
`daily_ai_news` correctly computes `get_theme_context()` as night when the device `active_theme` is night, but the playlist can still show an older PNG generated during the day. The symptom is a current screen showing `DAY BRIEF` even though `device.json` has `active_theme=night`. In the observed case, the image footer showed `生成: 2026-05-27 16:03`, confirming it was a stale rendered image rather than a current theme computation failure.

### Suggested Action
For theme-aware plugins that render cached content, set `refreshOnDisplay=true` and keep expensive content refresh flags such as `force_refresh=false` unless the user explicitly wants a new API/news summary. This redraws the same cached content with the current theme without spending extra API calls.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py, inkypi-weather/package/InkyPi/src/refresh_task.py
- Tags: inkypi, epaperpod, daily-ai-news, theme, cache, refresh-on-display
