# Sports dashboard football-data.org World Cup 2026 probe

## [LRN-20260603-001] project_quirk

**Logged**: 2026-06-03T00:00:00-07:00
**Priority**: medium
**Status**: superseded
**Area**: epaper

### Summary
The device `FOOTBALL_DATA` key works with football-data.org for FIFA World Cup 2026 current-season data and is suitable as a primary `sports_dashboard` World Cup source with caching.

### Details
Live probe on `ColoredEpaperFrame` returned `200` for `/v4/competitions/WC`, `/v4/competitions/WC/matches?season=2026`, `/v4/competitions/WC/teams?season=2026`, and `/v4/competitions/WC/standings?season=2026`. The 2026 matches endpoint returned 104 matches from 2026-06-11 to 2026-07-19, and the teams endpoint returned 48 teams with crests. A historical check for `/v4/competitions/WC/matches?season=2022&status=FINISHED` returned `403`, so do not rely on this free key for historical World Cup scores. Free football-data.org scores/schedules are delayed rather than truly live.

### Suggested Action
Use football-data.org as the first API source for 2026 World Cup schedule/basic scores in `sports_dashboard`, with `FOOTBALL_DATA` read through `load_env_key`, a 6-hour normal cache, and tighter but still capped matchday refresh. Keep `worldcup26.ir` and the current SportBusy screenshot path as fallbacks.

### Metadata
- Source: live_api_probe
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: sports_dashboard, football-data, world-cup, api, free-tier, coloredepaperframe

---

## [LRN-20260603-003] user_preference

**Logged**: 2026-06-03T02:25:00-07:00
**Priority**: medium
**Status**: superseded
**Area**: epaper

### Summary
The `sports_dashboard` World Cup row layout should emphasize centered country names and bare flat flags, with 24-hour time on the left and date on the far right.

### Details
The preferred row structure is: left-side compact 24-hour time, center matchup with enlarged Simplified Chinese country names and `flagsapi.com` flat flags, and right-side date. Loaded flag images should be pasted directly without a surrounding badge or panel background; only fallback text badges need a background.

### Suggested Action
When continuing World Cup UI tweaks, preserve this row structure unless the user explicitly asks for a different layout. Do not reintroduce `TODAY/TOMORROW` time labels or flag background boxes.

### Superseded By
LRN-20260603-004 moves the group label to the far left and places time next to the date on the far right.

### Metadata
- Source: user_preference
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: sports_dashboard, world-cup, layout, flagsapi, 24-hour-time

---

## [LRN-20260603-004] user_preference

**Logged**: 2026-06-03T02:35:00-07:00
**Priority**: high
**Status**: superseded
**Area**: epaper

### Summary
The `sports_dashboard` World Cup row layout should be: group label far left, matchup centered, and 24-hour time plus date together on the far right.

### Details
The user marked the desired layout directly on the device photo. Country names and bare flat flags stay in the center matchup area. The group label, such as `Group A`, should occupy the left-side row block. The actual 24-hour match time should move to the right side and sit immediately beside the date.

### Suggested Action
For future World Cup layout tweaks, keep the row order as `Group | flag country vs country flag | time date`. Do not move time back to the far left unless the user explicitly asks.

### Superseded By
LRN-20260603-005 swaps the right-side order to date then time and vertically centers the matchup row.

### Metadata
- Source: user_marked_photo
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: sports_dashboard, world-cup, layout, group-left, time-right

---

## [LRN-20260603-005] user_preference

**Logged**: 2026-06-03T02:55:00-07:00
**Priority**: high
**Status**: superseded
**Area**: epaper

### Summary
The `sports_dashboard` World Cup row layout should keep the matchup vertically centered, with the right-side order as date then 24-hour time.

### Details
The user corrected the previous layout: flags and Simplified Chinese country names were too low, so the center matchup should be placed on the row's vertical middle line. The right-side compact boxes should be ordered as date first and time second, sitting next to each other on the far right.

### Suggested Action
For future World Cup row tweaks, keep the row order as `Group | flag country vs country flag | date time`, and compute the matchup y offset from row height rather than using a fixed low y position.

### Superseded By
LRN-20260603-006 restores the right-side order to time then date, and adds the fixed non-overlap regions plus the revised LPL focus-card structure.

### Metadata
- Source: user_correction
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: sports_dashboard, world-cup, layout, vertical-center, date-time-order

---

## [LRN-20260603-006] user_preference

**Logged**: 2026-06-03T03:25:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
The final `sports_dashboard` split layout should use fixed non-overlap World Cup row regions and an LPL focus-card sidebar with larger logos.

### Details
World Cup rows should be laid out as `Group | flag country vs country flag | time date`, with the date box farthest right and the matchup vertically centered. Keep the matchup region bounded before the time/date boxes so long Simplified Chinese country names and flags cannot overlap the right-side boxes. The World Cup/LPL divider should stay thin and quiet. The LPL sidebar should prioritize a single `LIVE NOW` or `NEXT MATCH` focus card with larger team logos, then compact upcoming rows, then recent results without overlapping section headers.

### Suggested Action
Future tweaks should preserve `_worldcup_row_regions()` as the source of truth for row geometry, avoid returning to free-positioned country/date text, and keep the LPL focus card as the dominant right-side element.

### Metadata
- Source: deployed_ui_verification
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: sports_dashboard, world-cup, lpl, layout, no-overlap, focus-card

---

## [LRN-20260603-002] implementation_pattern

**Logged**: 2026-06-03T02:15:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For `sports_dashboard` World Cup country flags, prefer `https://flagsapi.com/{ISO2}/flat/64.png` over football-data crest URLs.

### Details
The user requested the flat flag style from flagsapi.com. football-data.org team crests may be SVG or visually inconsistent, while flagsapi flat PNGs load directly in Pillow and match the e-paper comic layout better. Map football-data FIFA TLA values, such as `MEX`, `RSA`, `KOR`, and `URY`, to ISO alpha-2 country codes before rendering.

### Suggested Action
Keep the TLA-to-Simplified-Chinese and TLA-to-ISO mappings near the `sports_dashboard` plugin code, and only fetch flags for the selected visible rows. Cache flag images in memory and fall back to a small text badge if a flag request fails.

### Metadata
- Source: user_correction
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: sports_dashboard, world-cup, flagsapi, football-data, chinese-country-names

---
