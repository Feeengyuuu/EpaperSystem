# New Board Migration Baseline

Last verified: 2026-05-26 18:15 PDT

This document freezes the current EpaperSystem beta state as the target baseline
for installing a future production board. It intentionally does not contain API
keys or private `.env` values.

## Target Outcome

Bring a fresh Raspberry Pi / mainboard to the current beta end state:

- InkyPi running as the `inkypi` systemd service.
- Waveshare 7.5 inch V2 black/white display configured as `epd7in5_V2`.
- Resolution `800x480`, horizontal orientation.
- Web UI reachable on the LAN.
- Playlist `DailyDoseOfDay` active all day.
- No-repeat random plugin rotation every 5 minutes.
- Per-plugin content refresh intervals preserved.
- The selected display plugin refreshes first when needed, then other due
  plugin images refresh in a non-overlapping background cache pass.
- Current custom plugins and patched scheduler behavior installed.

## Current Beta Device

- Hostname: `EpaperPodBeta`
- Current SSH target: `feeengyuuu@192.168.1.183`
- Current package path on Pi: `~/inkypi-weather-pi-package-20260524-3`
- Current config path on Pi:
  `/home/feeengyuuu/inkypi-weather-pi-package-20260524-3/InkyPi/src/config/device.json`
- Service entrypoint: `/usr/local/bin/inkypi run`
- systemd unit: `/etc/systemd/system/inkypi.service`
- Service state at baseline: `active`

The service can take roughly 2-4 minutes to become HTTP-ready after restart on
the beta board. Treat `systemctl is-active inkypi` as process readiness only;
wait for HTTP readiness before sending UI/API actions.

## Device Settings

```json
{
  "name": "EpaperBeta",
  "display_type": "epd7in5_V2",
  "resolution": [800, 480],
  "orientation": "horizontal",
  "timezone": "America/Los_Angeles",
  "time_format": "24h",
  "plugin_cycle_interval_seconds": 300,
  "image_settings": {
    "saturation": 1.0,
    "brightness": 1.0,
    "sharpness": 1.0,
    "contrast": 1.0
  }
}
```

## Playlist Baseline

Playlist name: `DailyDoseOfDay`

Window: `00:00` to `24:00`

Rotation mode: shuffled no-repeat plugin selection. Each active plugin should be
shown once per shuffled round before the next round begins; if possible, the
first item of a new round must not be the plugin that was just displayed.

| Instance | Plugin ID | Content refresh |
| --- | --- | --- |
| NASAPics | `apod` | daily at `00:00` |
| DailyComic | `comic` | daily at `00:00` |
| ChinaDaily | `newspaper` | daily at `15:00` |
| WikiDaily | `wpotd` | daily at `00:00` |
| RandomPics | `unsplash` | every 60 minutes |
| NovalTime | `literature_clock` | every 5 minutes |
| Daily AI News | `daily_ai_news` | daily at `07:30` |
| SteamDaily | `steam_profile_dashboard` | every 5 minutes |
| ChineseClock | `chinese_literature_clock` | every 5 minutes |
| Dots | `flow_progress` | every 5 minutes |
| Date | `simple_calendar` | daily at `00:00` |
| Weather | `mini_weather` | every 30 minutes |
| Robot | `epaper_pet` | every 15 minutes |
| Money | `stocktracker` | daily at `00:00` |
| Bambu | `bambu_monitor` | every 5 minutes |
| SteamDailyArt | `steam_daily_art` | every 60 minutes |
| Anal | `image_upload` | daily at `00:00` |
| DailyImage | `image_upload` | every 5 minutes |

## Key Plugin Settings

These values were present in the latest beta `device.json` and should be
preserved when moving to a new board:

- `Robot` / `epaper_pet`: `pet_name=Loki`, `pet_id=loki`,
  `personality=quiet, curious, active, low-refresh e-paper companion`,
  `tick_minutes=15`, `care_profile=normal`, `event_density=expressive`,
  `autonomous_care=on`, `show_journal=on`.
- `Money` / `stocktracker`: `tickers=AAPL, SPY, NTDOY, TSLA, NVDA, VTI,
  VXUS, VGIT, TTWO, GOOGL`, `shares=246.3, 17.35, 771, 83.27, 245.29,
  51.4, 80.52, 60.08, 10.6, 2`, `period=1mo`.
- `Bambu` / `bambu_monitor`: `host=192.168.1.137`,
  `serialNumber=03919D530909663`, `accessCodeEnv=BAMBU_ACCESS_CODE`,
  `port=8883`, `timeoutSeconds=8`, `cacheSeconds=60`,
  `cameraEnabled=on`, `cameraPort=6000`, `cameraTimeoutSeconds=18`,
  `requestFullUpdate=on`, refresh every 5 minutes. Do not store the
  Access Code in `plugin_settings`. The A1 camera frame is refreshed on
  each Bambu render, even when the status payload is served from cache. If
  the camera cannot return frames for multiple consecutive refreshes, the
  plugin falls back to bundled `camera_waiting.png`. The rendered plugin UI
  should remain English by default.
- `Daily AI News` / `daily_ai_news`: `force_refresh=on`,
  `daily_api_limit=2`, RSS feeds preserved, scheduled at `07:30`.
- `SteamDailyArt` / `steam_daily_art`: `sourceCategory=fresh_frontpage`,
  `selectionMode=daily_rotation`, `rotationCadence=hourly`,
  `imageMode=library_hero`, `logoOverlay=show`,
  `logoPosition=empty_space`, `logoSize=normal`, `countryCode=US`,
  `language=english`.
- `Anal` / `image_upload`: `padImage=true`, `backgroundColor=#ffffff`,
  `imageFiles[]=/usr/local/inkypi/src/static/images/saved/May 26, 2026,
  03_21_28 AM.png`.
- `DailyImage` / `image_upload`: `padImage=true`,
  `displayMode=no_repeat_random`, `backgroundOption=blur`, refresh every
  5 minutes. Preserve the saved image files referenced in current `device.json`.

## Internet Freshness Matrix

The scheduler first selects the next display plugin and refreshes that plugin
synchronously if needed, preserving real-time display freshness. Other due
plugin caches then refresh in a non-overlapping background pass so slow
internet sources do not block screen rotation or stack API bursts.

| Instance | Source type | Freshness behavior |
| --- | --- | --- |
| NASAPics | NASA API + image URL | Daily scheduled fetch. `randomizeApod=true` selects a new APOD date when due. |
| DailyComic | comic provider image URL | Daily scheduled comic fetch. |
| ChinaDaily | Freedom Forum front page image | Daily scheduled fetch at `15:00`; latest smoke found the next available China Daily cover. |
| WikiDaily | Wikipedia API + image URL | Daily scheduled Wikipedia POTD fetch. |
| RandomPics | Unsplash API + image URL | Hourly random Unsplash fetch. |
| Daily AI News | RSS + market data + OpenAI | Daily scheduled RSS/OpenAI refresh at `07:30`, with forced same-day cache bypass and a limit of 2 API calls/day. |
| SteamDaily | Steam Web API + Store API | Five-minute live status refresh; heavier profile data remains cached for 15 minutes. |
| Weather | OpenWeatherMap | 30-minute weather refresh with OpenWeather cost guards. |
| Money | Yahoo Finance via `yfinance` | Daily scheduled portfolio data fetch. |
| Bambu | Bambu local MQTT/TLS + camera TLS | Five-minute read-only local printer status refresh against `192.168.1.137:8883`; each render also captures one A1 camera frame from `192.168.1.137:6000`; keep Bambu Cloud/mobile access enabled when possible. |
| SteamDailyArt | Steam Store front page + CDN | Hourly fresh Steam front-page art fetch with no-repeat selection. |
| NovalTime | local quote/time data | No internet source; refreshes local time-based text every 5 minutes. |
| ChineseClock | local quote/time data | No internet source; refreshes local time-based text every 5 minutes. |
| Dots | local progress data | No internet source; refreshes local progress every 5 minutes. |
| Date | local calendar | No internet source; refreshes daily. |
| Robot | local autonomous pet state | No internet source; refreshes local pet state every 15 minutes. |
| Anal | uploaded local image | No internet source; refresh only reloads the saved uploaded image. |
| DailyImage | uploaded local image batch | No internet source; refresh picks from saved uploaded images with no-repeat random mode every 5 minutes. |

## Important Current Caveats

- `Money` is the configured `stocktracker` instance. Preserve the non-empty
  `tickers`, `shares`, and `period` values above; do not reintroduce the old
  unconfigured `Stock` placeholder.
- `Bambu` is a read-only `bambu_monitor` instance. Keep the Access Code in
  `.env` as `BAMBU_ACCESS_CODE`, not in `device.json`. Do not add pause, stop,
  heat, or G-code control actions. Prefer the current cloud-connected printer
  mode; only enable LAN Only / Developer Mode if the user explicitly accepts
  the Bambu Cloud / Bambu Handy trade-off. For the A1 camera stream, use an
  explicit 80-byte little-endian auth packet before reading the 16-byte frame
  header and JPEG payload; native `struct.pack("IIL", ...)` can produce the
  wrong 76-byte packet on this Pi.
- `Anal` and `DailyImage` depend on saved uploaded image files. Copy the saved
  image paths from the current `device.json` to the new board or re-upload the
  images before relying on those playlist entries.
- `stocktracker` depends on vendored/imported finance libraries. Keep its direct
  render smoke test in the migration checklist.
- The current Pi service runs as root and the plugin image cache directory may
  not be readable by the normal user.
- Do not migrate or paste secret values into docs. Recreate `.env` on the new
  board from the user's private API keys.

## Required Secret Environment Keys

Create `InkyPi/.env` on the new board with the needed keys. At minimum this
project has used:

```text
OPEN_WEATHER_MAP_SECRET=...
OPENWEATHER_ONECALL_DAILY_LIMIT=900
OPENWEATHER_ONECALL_MIN_SECONDS=1800
OPENWEATHER_AUX_MIN_SECONDS=1800
OPENWEATHER_LOCATION_MIN_SECONDS=86400
NASA_SECRET=...
OPEN_AI_SECRET=...
STEAM_API_KEY=...
UNSPLASH_ACCESS_KEY=...
BAMBU_ACCESS_CODE=...
```

Only include keys for plugins that remain enabled.

## Migration Procedure

1. Prepare the new board with Raspberry Pi OS, WiFi, SSH, locale, timezone, and
   the intended username.
2. Install the Codex SSH key using
   `inkypi-weather/dist/epaperpod_codex_bootstrap.sh`, or manually install the
   same public key and limited sudo rule.
3. Copy the current local package folder to the new board. Use a new package
   directory name if needed, but keep it explicit in deploy scripts.
4. Create `InkyPi/.env` on the new board with the private API keys and cost
   guard values.
5. From the copied package root, run:

   ```bash
   bash install_on_pi.sh
   ```

   The installer runs:

   ```bash
   sudo bash install/install.sh -W epd7in5_V2
   ```

6. Restore `src/config/device.json` to the baseline settings and playlist above.
7. Restart the service:

   ```bash
   sudo systemctl restart inkypi
   ```

8. Wait for HTTP readiness:

   ```bash
   until curl -fsS http://127.0.0.1/playlist >/dev/null; do sleep 10; done
   ```

9. Validate the playlist page, plugin thumbnails, and current display image.
10. Run a short scheduler smoke by temporarily setting
    `plugin_cycle_interval_seconds` to `20`, observing at least one automatic
    `Determined next plugin` and `Updating display` entry before background
    `Refreshing due plugin instance cache` entries, then restore `300`.

## Post-Migration Validation

Run these checks from the Codex machine:

```powershell
.\tools\epaperpod-test-key.ps1 -HostName <new-board-ip> -UserName <user>
```

Verify HTTP:

```powershell
C:\Windows\System32\curl.exe --noproxy '*' -sS -o NUL -w '%{http_code}\n' http://<new-board-ip>/playlist
```

Verify config:

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path('~/inkypi-weather-pi-package-20260524-3/InkyPi/src/config/device.json').expanduser()
data = json.loads(p.read_text(encoding='utf-8'))
print(data['plugin_cycle_interval_seconds'])
for playlist in data['playlist_config']['playlists']:
    print(playlist['name'], playlist['start_time'], playlist['end_time'])
    for plugin in playlist['plugins']:
        print(plugin['name'], plugin['plugin_id'], plugin['refresh'])
PY
```

Journal checks:

```bash
systemctl is-active inkypi
journalctl -u inkypi --since=-20min --no-pager
```

## Acceptance Criteria

- `systemctl is-active inkypi` returns `active`.
- `/playlist` returns HTTP `200`.
- `plugin_cycle_interval_seconds` is restored to `300` after smoke testing.
- `DailyDoseOfDay` contains the 17 active baseline instances above.
- The `Money` / `stocktracker` instance has non-empty `tickers`, `shares`, and
  `period` values.
- The selected display plugin refreshes before non-selected due cache refresh.
- The `Bambu` instance displays a fresh camera frame in the `LIVE VIEW` panel
  during a manual or scheduled render.
- If the Bambu camera repeatedly fails and no fresh frame is available, the
  `LIVE VIEW` panel displays the bundled waiting image instead of a blank box.
- The `Bambu` screen renders English labels on the Pi.
- Background due cache refresh does not overlap; if a previous cache pass is
  still running, the next pass logs a skip instead of stacking requests.
- A short-cycle smoke proves automatic shuffled no-repeat rotation.
- No plugin in the active random playlist has missing required settings.
- Weather API cost guard remains configured.
- Display shows a valid rendered image after at least one playlist refresh.
