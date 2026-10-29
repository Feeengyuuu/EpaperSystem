# API Keys

API keys are optional. InkyPi installs and starts without them; a page that needs a
missing key is unavailable, shows its cache, or falls back to a public/local source.
Pages that do not appear below need no API key (a few take addresses or accounts in
their own settings, such as a calendar's ICS link).

Simplified Chinese version: [api_keys.zh-CN.md](./api_keys.zh-CN.md).

## Where keys live

On the Raspberry Pi the running service reads `/etc/inkypi/inkypi.env`, readable only
by the service account. Do not create another `.env` in the source tree as runtime
configuration.

For local development you can keep a `.env` at the project root
(`inkypi-weather/package/InkyPi/.env`). When the installer runs it merges the values
that are **missing** from `/etc/inkypi/inkypi.env` and never overwrites existing ones.
`.env` is ignored by Git; do not commit it.

## Add or change keys

**Web UI**: open `http://<your-pi>/api-keys` and fill in keys grouped by service.

**During installation**: the installer offers the common keys; press Enter to skip.
`--all-keys` asks for every key, and `--skip-keys` skips them so you can add them in
the web UI later.

**Command line** (from any directory):

```bash
# Common keys interactively; add --all to be asked for every key
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --env-file /etc/inkypi/inkypi.env

# Every key with its registration URL and accepted aliases
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --list

# Which keys are configured
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --check --env-file /etc/inkypi/inkypi.env
```

Add `--lang zh-CN` for Simplified Chinese prompts. After changing keys on the command
line, restart the service:

```bash
sudo systemctl restart inkypi
```

## All keys

These are all the keys the installer and the web UI know about, matching
`src/config/secret_schema.json` (and `install/api_key_registry.json`, generated from it).

### AI and generation

| Variable | Service | Used by | Get it |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | OpenAI | AI Image, AI Text, Daily AI News, E-Paper Pet, AI Image Multiverse | <https://platform.openai.com/api-keys> |
| `GROQ_API_KEY` | Groq | Daily AI News, E-Paper Pet, AI Image Multiverse | <https://console.groq.com/keys> |
| `NANO_BANANA_KEY` | Google AI Studio / Gemini | AI Image Multiverse | <https://aistudio.google.com/app/apikey> |
| `AI_HORDE_KEY` | AI Horde | AI Image Multiverse | <https://aihorde.net/register> |

### Weather, astronomy and maps

| Variable | Service | Used by | Get it |
| --- | --- | --- | --- |
| `OPEN_WEATHER_MAP_SECRET` | OpenWeather | Weather, Mini Weather | <https://home.openweathermap.org/api_keys> |
| `NASA_SECRET` | NASA Open APIs | Astronomy Picture of the Day | <https://api.nasa.gov/> |
| `GOOGLE_MAPS_API_KEY` | Google Maps Platform | Flight Radar map background, Vehicle Status map and address | <https://console.cloud.google.com/google/maps-apis/credentials> |
| `EBIRD_API_KEY` | eBird API 2.0 | Nature Nearby (notable birds; optional) | <https://ebird.org/api/keygen> |
| `FIRMS_MAP_KEY` | NASA FIRMS | Bay Alerts (satellite fire hotspots; optional) | <https://firms.modaps.eosdis.nasa.gov/api/map_key/> |

### Images and art

| Variable | Service | Used by | Get it |
| --- | --- | --- | --- |
| `UNSPLASH_ACCESS_KEY` | Unsplash | Unsplash | <https://unsplash.com/developers> |
| `IMMICH_KEY` | Immich | Image Album | <https://immich.app/docs/features/command-line-interface/> |
| `EUROPEANA_API_KEY` | Europeana | Daily Art | <https://pro.europeana.eu/page/get-api> |
| `HARVARD_ART_MUSEUMS_API_KEY` | Harvard Art Museums | Daily Art | <https://harvardartmuseums.org/collections/api> |
| `COMIC_VINE_API_KEY` | Comic Vine | GCD Comic Covers | <https://comicvine.gamespot.com/api/> |
| `PIXIV_PHPSESSID` | Pixiv | Pixiv R-18 Ranking (the general ranking needs none) | <https://www.pixiv.net/settings/viewing> |
| `WEIBO_COOKIE` | Weibo | 全网热搜 (optional: adds Weibo's hot search; the `SUB` cookie of a signed-in weibo.com session) | <https://weibo.com/> |

### Sports and esports

| Variable | Service | Used by | Get it |
| --- | --- | --- | --- |
| `FOOTBALL_DATA_API_KEY` | football-data.org | Sports Dashboard | <https://www.football-data.org/client/register> |
| `API_SPORTS_KEY` | API-SPORTS / API-Football | Sports Dashboard | <https://dashboard.api-football.com/register> |
| `THE_ODDS_API_KEY` | The Odds API | Sports Dashboard odds | <https://the-odds-api.com/#get-access> |
| `ODDS_API_IO_KEY` | Odds-API.io | Sports Dashboard odds | <https://odds-api.io/> |
| `PANDASCORE_API_KEY` | PandaScore | Sports Dashboard CS2 | <https://app.pandascore.co/signup> |

### Games and streaming

| Variable | Service | Used by | Get it |
| --- | --- | --- | --- |
| `STEAM_API_KEY` | Steam Web API | Steam Profile Dashboard | <https://steamcommunity.com/dev/apikey> |
| `RIOT_API_KEY` | Riot Developer Portal | League of Legends Info | <https://developer.riotgames.com/> |
| `OPENDOTA_API_KEY` | OpenDota | Dota Profile Dashboard | <https://www.opendota.com/api-keys> |
| `BLIZZARD_CLIENT_ID` | Battle.net Developer Portal | WoW Profile Dashboard | <https://develop.battle.net/access/clients> |
| `BLIZZARD_CLIENT_SECRET` | Battle.net Developer Portal | WoW Profile Dashboard | <https://develop.battle.net/access/clients> |
| `BLIZZARD_USER_ACCESS_TOKEN` | Battle.net OAuth | WoW Profile Dashboard account mode | <https://develop.battle.net/documentation/guides/using-oauth> |
| `TWITCH_CLIENT_ID` | Twitch application client ID | LiveRadar, Tech & Play | <https://dev.twitch.tv/console/apps> |
| `TWITCH_CLIENT_SECRET` | Twitch application client secret | LiveRadar, Tech & Play | <https://dev.twitch.tv/console/apps> |

### Finance

| Variable | Service | Used by | Get it |
| --- | --- | --- | --- |
| `MASSIVE_API_KEY` | Massive | Stock Tracker, Daily AI News market strip | <https://massive.com/> |
| `FINNHUB_API_KEY` | Finnhub | Money Radar (holdings' earnings dates) | <https://finnhub.io/dashboard> |
| `FRED_API_KEY` | FRED (St. Louis Fed) | Money Radar (CPI, unemployment, Fed funds, 30-year mortgage rate) | <https://fredaccount.stlouisfed.org/apikeys> |

### Film, events and news

| Variable | Service | Used by | Get it |
| --- | --- | --- | --- |
| `TMDB_BEARER_TOKEN` | The Movie Database | Box Office Top Movies | <https://www.themoviedb.org/settings/api> |
| `TMDB_API_KEY` | The Movie Database API key | Box Office Top Movies | <https://www.themoviedb.org/settings/api> |
| `TICKETMASTER_API_KEY` | Ticketmaster Discovery API | Ticketmaster local events | <https://developer.ticketmaster.com/products-and-docs/apis/getting-started/> |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API | Telegram Digest bot mode | <https://core.telegram.org/bots/tutorial> |
| `TELEGRAM_API_ID` | Telegram API ID | Telegram Digest account mode | <https://my.telegram.org/apps> |
| `TELEGRAM_API_HASH` | Telegram API hash | Telegram Digest account mode | <https://my.telegram.org/apps> |
| `TELEGRAM_SESSION_PATH` | Telegram session file path | Telegram Digest account mode | <https://docs.telethon.dev/en/stable/concepts/sessions.html> |
| `REDDIT_CLIENT_ID` | Reddit OAuth client ID | Reddit Rule34 Hot | <https://www.reddit.com/prefs/apps> |
| `REDDIT_CLIENT_SECRET` | Reddit OAuth client secret | Reddit Rule34 Hot | <https://www.reddit.com/prefs/apps> |
| `REDDIT_REFRESH_TOKEN` | Reddit OAuth refresh token | Reddit Rule34 Hot | <https://github.com/reddit-archive/reddit/wiki/OAuth2> |

### Travel and devices

| Variable | Service | Used by | Get it |
| --- | --- | --- | --- |
| `FLIGHTAWARE_API_KEY` | FlightAware AeroAPI | Flight Radar | <https://flightaware.com/aeroapi/portal/> |
| `OPENSKY_USERNAME` | OpenSky Network username | Flight Radar | <https://opensky-network.org/> |
| `OPENSKY_PASSWORD` | OpenSky Network password | Flight Radar | <https://opensky-network.org/> |
| `RAPIDAPI_KEY` | RapidAPI | Flight Radar, Daily Knowledge | <https://rapidapi.com/hub> |
| `BAMBU_ACCESS_CODE` | Bambu Lab printer (LAN access code shown on the printer's screen) | Bambu Monitor | <https://wiki.bambulab.com/en/general/find-sn> |
| `EPAPER_VEHICLE_BRIDGE_TOKEN` | Your own Tesla bridge Worker (read-only token) | Vehicle Status | [cloudflare/vehicle-auth-bridge](../../../../cloudflare/vehicle-auth-bridge) |
| `EPAPER_VEHICLE_BRIDGE_ORIGIN` | Address of your own bridge Worker, such as `https://vehicle-bridge.<you>.workers.dev` (empty uses the project maintainer's bridge) | Vehicle Status | [cloudflare/vehicle-auth-bridge](../../../../cloudflare/vehicle-auth-bridge) |

### Developers

| Variable | Service | Used by | Get it |
| --- | --- | --- | --- |
| `GITHUB_SECRET` | GitHub | GitHub Contributions, GitHub Sponsors | <https://github.com/settings/tokens> |

## Aliases

Some plugins still accept older variable names; for example `OPEN_AI_SECRET` is the
same as `OPENAI_API_KEY`. New installations should use the names above. To list every
alias:

```bash
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --list
```
