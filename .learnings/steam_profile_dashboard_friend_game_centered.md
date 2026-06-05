# Steam Profile Dashboard friend game titles are centered

## [LRN-20260604-003] user_preference

**Logged**: 2026-06-04T20:36:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
In `steam_profile_dashboard`, friend rows must always preserve the friend name; currently-playing game titles are secondary and should use smaller bounded text.

### Details
The old layout drew `playing` label, marker/icon, and game title on one compact status line. Long game names could overflow the friend panel boundary. A later centered-title attempt made names disappear and crowded the text. The corrected preference is: keep the friend name on the first line, draw the online marker on the second line, start `正在游玩：` at the same left edge as the normal `在线` text directly after the green dot, keep the game icon after that prefix, and place that icon directly against the left side of the game name. This is separate from the main account current-game title, which remains a single-line title by previous preference.

### Suggested Action
For future friend-status edits, do not hide the friend name to make room for game titles, and do not drop or detach the game icon. Use a two-line row: friend name first, then green status dot plus `正在游玩：` plus game icon plus small game title, bounded to the row width. The prefix must align like `在线`: immediately to the right of the green dot, not centered in the row. The icon belongs immediately before the game title, not before the prefix. Bump `STEAM_DASHBOARD_STYLE_VERSION`, and verify with `/display_plugin_instance` for `DailyDoseOfDay` / `steam_profile_dashboard` / `SteamDaily`.

### Metadata
- Source: user correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py, inkypi-weather/package/InkyPi/tests/test_steam_profile_dashboard_friend_status.py
- Tags: inkypi, epaperpod, steam-profile-dashboard, friends, game-title, layout
