# Steam Profile Dashboard Recent Game Icons

## Learning
The `steam_profile_dashboard` lower-left `最近 / 实时` list should show small square Steam game icons for game rows when an `appid` is available. Use the green bullet only as a fallback for non-game rows or missing icon data.

## Context
- Plugin: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Live instance: `DailyDoseOfDay` / `SteamDaily`
- User correction: after adding the top current-game icon, the lower `最近 / 实时` section should also have a small avatar/icon.

## Recommended Pattern
- Generate structured recent items with `text` and optional `appid`.
- Render game rows through `_draw_recent_item()` so `_game_square_icon()` can reuse Steam `img_icon_url` icon fetching and cache behavior.
- Preserve wrapped text for the lower list because recent rows can contain Chinese names plus playtime details.
- Bump `STEAM_DASHBOARD_STYLE_VERSION` after changing lower-list visuals.

## Verification
Trigger `SteamDaily` via `/display_plugin_instance` and inspect `/static/images/current_image.png`. The lower-left game rows should show square app icons before their text, while metadata rows still use bullets.
