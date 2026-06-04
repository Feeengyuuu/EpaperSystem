# Steam Profile Dashboard YaHei Font Reuse

## [LRN-20260603-010] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, steam-profile-dashboard

### Summary
Steam profile dashboard Chinese text can reuse the already deployed Microsoft YaHei fonts from `sports_dashboard`.

### Details
The Steam dashboard previously listed `C:/Windows/Fonts/msyh.ttc` after LXGW/Noto fallbacks, which did not help on the live Pi. The live device already had `msyh.ttc`, `msyhbd.ttc`, and `msyhl.ttc` under `src/plugins/sports_dashboard/fonts/`. Updating `steam_profile_dashboard._font()` to prefer `../sports_dashboard/fonts/msyh*.ttc` changed Chinese rendering to YaHei without copying new font files. Bumping `STEAM_DASHBOARD_STYLE_VERSION` forced a new cache key so the existing cached image did not keep the old font.

### Suggested Action
For future plugin font changes, first inspect existing deployed plugin font folders before adding font assets. When changing a rendered dashboard font, include the font/style version in the cache key or bump the existing style version so live verification renders the new font immediately.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`, `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/fonts/msyh.ttc`
- Tags: steam-profile-dashboard, yahei, fonts, cache-key, epaperpod, coloredepaperframe
