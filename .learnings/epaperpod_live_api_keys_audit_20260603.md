# EpaperPod Live API Keys Audit 2026-06-03

## [LRN-20260603-002] audit

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Live `ColoredEpaperFrame` API keys were audited without exposing secret values.

### Details
Read-only SSH audit of `feeengyuuu@192.168.1.188` found the active `.env` at `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/.env`, size `1730`, modified `2026-06-03 15:42:19`. `/usr/local/inkypi/.env` resolved to the same active file. Values were not printed; only key name, value length, and SHA-256 prefix were recorded.

Configured live entries:

| Key | Length | SHA-256 prefix | Current code consumption |
| --- | ---: | --- | --- |
| `OPENWEATHER_AUX_MIN_SECONDS` | 4 | `8b34042b` | used by weather throttling |
| `OPENWEATHER_LOCATION_MIN_SECONDS` | 5 | `b045dd9c` | used by weather throttling |
| `OPEN_WEATHER_MAP_SECRET` | 32 | `a38b229e` | used by weather and mini_weather |
| `OPEN_WEATHER_ONESHOT_MIN` | 32 | `a38b229e` | not found in current code |
| `OPENWEATHER_ONECALL_DAILY_LIMIT` | 3 | `bdc5d8a4` | used by weather throttling |
| `OPENWEATHER_ONECALL_MIN_SECONDS` | 4 | `8b34042b` | used by weather throttling |
| `NASA_SECRET` | 40 | `a45054a0` | used by APOD |
| `GITHUB_SECRET` | 93 | `226637f2` | used by GitHub plugins |
| `OPEN_AI_SECRET` | 164 | `5db04a2e` | used by AI image/text/news/pet plugins |
| `UNSPLASH_ACCESS_KEY` | 43 | `f7dd2786` | used by Unsplash |
| `STEAM_API_KEY` | 32 | `306ddf32` | used by Steam dashboard |
| `BAMBU_ACCESS_CODE` | 8 | `06e27a13` | used by Bambu monitor |
| `GROQ_KEY` | 56 | `4cc87ce2` | not consumed; `daily_ai_news` expects `GROQ_API_KEY` |
| `Google_KEY` | 39 | `7a81c7b5` | used by flight_radar as Google Maps fallback |
| `Github_Secret` | 40 | `a0581b57` | not consumed; code expects `GITHUB_SECRET` |
| `TMDB_Access_Token` | 239 | `6aa889bd` | used by box_office_top_movies |
| `TMDB_API_KEY` | 32 | `3013eff6` | used by box_office_top_movies |
| `World_CUP` | 60 | `33df4a04` | used by sports_dashboard API-Sports fallback |
| `API_FPPTBALL_KEY` | 32 | `165aacd9` | used by sports_dashboard despite typo |
| `FOOTBALL_DATA` | 32 | `42300026` | used by sports_dashboard football-data.org |
| `ODDS_API_IO_KEY` | 64 | `6c061be1` | used by sports_dashboard odds-api.io |
| `Marvel_KEY` | 30 | `5e93f682` | not consumed by current comic-cover plugin |
| `Massive_Ecnomic_Key` | 32 | `88c1096d` | not found in current code |
| `Fun_Fact` | 50 | `4129ef60` | not found in current code |
| `Riot_KEY` | 42 | `57a84e69` | not found in current code |

The local checkout `.env` at `inkypi-weather/package/InkyPi/.env` was not recently updated; it only contains the OpenWeather key and throttling values. The live device file is the source of truth for the updated API keys.

### Suggested Action
If Groq should be active for `daily_ai_news`, add or rename the live entry to `GROQ_API_KEY`. If Marvel cover API integration is implemented later, wire `Marvel_KEY` explicitly and avoid committing the value. Prefer exact uppercase env names to avoid duplicate unused entries such as `Github_Secret`.

### Metadata
- Source: live_ssh_audit
- Related Files: `inkypi-weather/package/InkyPi/src/blueprints/apikeys.py`, `inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py`, `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`, `inkypi-weather/package/InkyPi/src/plugins/box_office_top_movies/box_office_top_movies.py`
- Tags: inkypi, colored-epaper-frame, api-keys, secrets, audit
