# Steam Profile Dashboard Current Game Single-Line Title

## Learning
The top current-game title in `steam_profile_dashboard` should stay on one line. If the available width is tight after adding the square Steam game icon, shrink the game-title font with `_draw_single_line_text()` instead of wrapping the title onto a second line.

## Context
- Plugin: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Live instance: `DailyDoseOfDay` / `SteamDaily`
- User correction: the title should not wrap; smaller font is acceptable as long as readability remains intact.

## Recommended Pattern
- Keep the "正在玩：" label and game icon on the same row as the game title.
- Use `_draw_single_line_text(..., min_size=10)` for the game title.
- Return at least the icon height from the row helper so the stats line below does not collide with the icon.
- Bump `STEAM_DASHBOARD_STYLE_VERSION` after changing title layout so the live device does not reuse a stale cached image.

## Verification
After deploy, trigger `SteamDaily` with `/display_plugin_instance` and inspect `/static/images/current_image.png`. A valid result keeps names such as `Farthest Frontier` on one line beside the icon.
