# Steam Profile Dashboard All Game Name Icons

## Learning
In `steam_profile_dashboard`, every rendered real game name should get a Steam game logo on its left when an `appid` is available. The icon size should be derived from the font/line height of that specific row, not a single global size.

## Context
- Plugin: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Live instance: `DailyDoseOfDay` / `SteamDaily`
- User correction: apply game logos to all game names in the Steam page, with size based on the text size.

## Recommended Pattern
- Keep structured item data with `appid`, `name`, and optional prefix/suffix instead of flattening all rows into strings.
- Use a helper like `_game_icon_size(draw, font, ...)` so current game, recent rows, TOP games, and friend activity can each size icons from their own font.
- Use Steam `img_icon_url` first, then fall back to `https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/capsule_184x69.jpg` for app ids not present in owned/recent game records.
- For rows that may wrap or be skipped due to space, check fit before drawing the icon; never leave an orphan icon without its game text.
- Bump `STEAM_DASHBOARD_STYLE_VERSION` after changing game-name icon coverage.

## Verification
After deploy, trigger `SteamDaily` and inspect `/static/images/current_image.png`. Check top current/friend activity, lower `最近 / 实时`, and right `常玩 TOP 3` for icons beside game names.
