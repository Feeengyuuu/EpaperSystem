# API Keys

API keys are optional. InkyPi should install and start without them; plugins that
need a missing key will be unavailable, use cache, or fall back to a public/local
source.

Simplified Chinese version: [api_keys.zh-CN.md](./api_keys.zh-CN.md).

Keys are stored in `.env` at the InkyPi project root. Do not commit `.env`.

## Add Keys

Use the installer helper:

```bash
python3 install/configure_api_keys.py --env-file .env
```

Simplified Chinese prompts:

```bash
python3 install/configure_api_keys.py --env-file .env --lang zh-CN
```

Show every known key and registration URL:

```bash
python3 install/configure_api_keys.py --list
python3 install/configure_api_keys.py --list --lang zh-CN
```

Check what is configured:

```bash
python3 install/configure_api_keys.py --check
```

You can also open the web UI and go to:

```text
http://<your-pi>/api-keys
```

After changing keys, restart the service:

```bash
sudo systemctl restart inkypi
```

## Key Registry

This table is mirrored from `install/api_key_registry.json`, which is the
installer and web UI source of truth.

| Variable | Service | Enables | Get key |
| --- | --- | --- | --- |
| `OPEN_AI_SECRET` | OpenAI | AI Image, AI Text, Daily AI News, Epaper Pet, AI Image Multiverse | <https://platform.openai.com/api-keys> |
| `GROQ_API_KEY` | Groq | Daily AI News, Epaper Pet, AI Image Multiverse | <https://console.groq.com/keys> |
| `NANO_BANANA_KEY` | Google AI Studio / Gemini | AI Image Multiverse | <https://aistudio.google.com/app/apikey> |
| `AI_HORDE_KEY` | AI Horde | AI Image Multiverse | <https://aihorde.net/register> |
| `OPEN_WEATHER_MAP_SECRET` | OpenWeather | Weather, Mini Weather | <https://home.openweathermap.org/api_keys> |
| `NASA_SECRET` | NASA Open APIs | Astronomy Picture of the Day | <https://api.nasa.gov/> |
| `UNSPLASH_ACCESS_KEY` | Unsplash | Unsplash | <https://unsplash.com/developers> |
| `GITHUB_SECRET` | GitHub | GitHub Contributions, GitHub Sponsors | <https://github.com/settings/tokens> |
| `IMMICH_KEY` | Immich | Image Album | <https://immich.app/docs/features/command-line-interface/> |
| `STEAM_API_KEY` | Steam Web API | Steam Profile Dashboard | <https://steamcommunity.com/dev/apikey> |
| `COMIC_VINE_API_KEY` | Comic Vine | GCD Comic Covers | <https://comicvine.gamespot.com/api/> |
| `RIOT_API_KEY` | Riot Developer Portal | League of Legends Info | <https://developer.riotgames.com/> |
| `OPENDOTA_API_KEY` | OpenDota | Dota Profile Dashboard | <https://www.opendota.com/api-keys> |
| `FLIGHTAWARE_API_KEY` | FlightAware AeroAPI | Flight Radar | <https://flightaware.com/aeroapi/portal/> |
| `RAPIDAPI_KEY` | RapidAPI | Flight Radar, Daily Knowledge | <https://rapidapi.com/hub> |
| `GOOGLE_MAPS_API_KEY` | Google Maps Platform | Flight Radar map background | <https://console.cloud.google.com/google/maps-apis/credentials> |
| `EUROPEANA_API_KEY` | Europeana | Daily Art | <https://pro.europeana.eu/page/get-api> |
| `HARVARD_ART_MUSEUMS_API_KEY` | Harvard Art Museums | Daily Art | <https://harvardartmuseums.org/collections/api> |
| `TMDB_BEARER_TOKEN` | The Movie Database | Box Office Top Movies | <https://www.themoviedb.org/settings/api> |
| `MASSIVE_API_KEY` | Massive | Stock Tracker, Daily AI News market context | <https://massive.com/> |
| `FOOTBALL_DATA_API_KEY` | football-data.org | Sports Dashboard | <https://www.football-data.org/client/register> |
| `API_SPORTS_KEY` | API-SPORTS / API-Football | Sports Dashboard | <https://dashboard.api-football.com/register> |
| `THE_ODDS_API_KEY` | The Odds API | Sports Dashboard odds | <https://the-odds-api.com/#get-access> |
| `ODDS_API_IO_KEY` | Odds-API.io | Sports Dashboard odds | <https://odds-api.io/> |
| `BLIZZARD_CLIENT_ID` | Battle.net Developer Portal | WoW Profile Dashboard | <https://develop.battle.net/access/clients> |
| `BLIZZARD_CLIENT_SECRET` | Battle.net Developer Portal | WoW Profile Dashboard | <https://develop.battle.net/access/clients> |
| `BLIZZARD_USER_ACCESS_TOKEN` | Battle.net OAuth | WoW Profile Dashboard account mode | <https://develop.battle.net/documentation/guides/using-oauth> |

## Aliases

Several plugins accept legacy aliases for compatibility. The installer writes
the primary variable names above, but the app can still read aliases listed in:

```bash
python3 install/configure_api_keys.py --list
```

Prefer the primary names for new installs.
