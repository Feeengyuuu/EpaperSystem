# Sports dashboard English and YaHei preference

## [LRN-20260602-007] user_preference

**Logged**: 2026-06-02T18:03:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For `sports_dashboard`, keep user-facing sports page copy in English by default and use Microsoft YaHei as the preferred local font when available.

### Details
After trying Simplified Chinese labels on the World Cup and LPL dashboard, the user said the Chinese still did not look good and asked to switch back to English. The plugin now keeps English labels such as `UPCOMING`, `RECENT`, `LIVE`, `NEXT`, and AM/PM time chips while loading Microsoft YaHei from `plugins/sports_dashboard/fonts/` before falling back to other CJK fonts.

### Suggested Action
For future `sports_dashboard` visual edits, avoid re-localizing the page to Chinese unless explicitly requested again. If changing fonts, preserve the plugin-local YaHei font priority and verify the live Pi resolves `_font()` to `sports_dashboard/fonts/msyh.ttc`.

### Metadata
- Source: sports_dashboard World Cup/LPL visual iteration
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py
- Tags: sports_dashboard, worldcup, lpl, font, english, yahei, epaper
