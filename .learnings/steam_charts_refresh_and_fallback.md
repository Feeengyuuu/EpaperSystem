# Steam Charts refresh config and Pi render fallback

## [LRN-20260527-051] best_practice

**Logged**: 2026-05-27T18:20:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Update running InkyPi plugin instance refresh settings through the Web API, and keep a PIL fallback for SteamCharts rendering on the Pi.

### Details
Directly editing `/usr/local/inkypi/src/config/device.json` while the InkyPi service is running can be overwritten by the service's in-memory `DeviceConfig` on the next write. For the `Steam Charts` instance, changing refresh from 7 days to 6 hours only persisted after calling `/update_plugin_instance/Steam%20Charts`. The Pi also returned screenshot code 132 for the SteamCharts HTML render, so the plugin needs a PIL fallback renderer to avoid returning `None` and failing image hashing.

### Suggested Action
For future InkyPi plugin instance updates, prefer the HTTP API for refresh settings once the service is live. When adding HTML-rendered plugins that must run on the Waveshare Pi, make `generate_image` tolerate `render_image(...) is None` and return a direct PIL image fallback.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_charts/steam_charts.py, inkypi-weather/package/InkyPi/tests/test_steam_charts.py
- Tags: inkypi, epaperpod, steam-charts, refresh, device-json, html-render, pil-fallback

## [LRN-20260527-052] environment

**Logged**: 2026-05-27T19:58:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Bundle a CJK sans font for Steam Charts on the Pi, and reject non-CJK `fc-match` fallbacks.

### Details
The Pi image only had DejaVu/Liberation/NotoColorEmoji available under `/usr/share/fonts`, so `fc-match "Microsoft YaHei"` returned a non-CJK sans font. PIL then rendered simplified Chinese game names as square missing-glyph boxes. Bundling `NotoSansSC-VF.ttf` inside `plugins/steam_charts/fonts/` fixed Chinese rendering, and setting the variable weight axis made the text readable on the black-white e-paper screen.

### Suggested Action
For Steam Charts and future bilingual e-paper plugins, prefer Microsoft YaHei on Windows but include a plugin-local CJK sans fallback for Pi. Validate the pulled `/api/current_image` after restarting the service, because direct plugin preview can be correct while the running service still has the old module loaded.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_charts/steam_charts.py, inkypi-weather/package/InkyPi/src/plugins/steam_charts/fonts/NotoSansSC-VF.ttf
- Tags: inkypi, epaperpod, steam-charts, fonts, cjk, pi, pil-fallback

## [LRN-20260527-053] user_preference

**Logged**: 2026-05-27T20:27:00-07:00
**Priority**: low
**Status**: active
**Area**: epaper

### Summary
For the Steam Charts header, interpret logo/text alignment requests as horizontal same-row layout unless the user explicitly asks for vertical centering.

### Details
When the user asked for the Steam mark and title text to align on the same line, the intended meaning was a horizontal title row: logo on the left, `STEAM CHARTS` text immediately to its right. It was not a request to tune vertical centerline positioning.

### Suggested Action
Keep the Steam Charts header as a simple same-row title layout and avoid over-adjusting vertical alignment unless the user asks for that specifically.

### Metadata
- Source: user correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_charts/steam_charts.py, inkypi-weather/package/InkyPi/src/plugins/steam_charts/render/steam_charts.css
- Tags: inkypi, epaperpod, steam-charts, ui, alignment, user-preference
