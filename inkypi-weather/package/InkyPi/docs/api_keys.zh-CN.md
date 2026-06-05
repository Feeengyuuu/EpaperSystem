# API Key 获取地址

API Key 都是可选的。没有 Key 时，系统仍应能安装和启动；需要 Key 的插件会不可用、使用缓存，或降级到公开/本地来源。

Key 存在 InkyPi 项目根目录的 `.env` 文件里。不要把 `.env` 提交到 GitHub。

## 添加 Key

安装时可以直接按提示填写。之后也可以运行：

```bash
python3 install/configure_api_keys.py --env-file .env --lang zh-CN
```

查看所有 Key 和注册地址：

```bash
python3 install/configure_api_keys.py --list --lang zh-CN
```

检查当前配置：

```bash
python3 install/configure_api_keys.py --check --lang zh-CN
```

网页方式：

```text
http://<你的树莓派>/api-keys
```

修改后重启服务：

```bash
sudo systemctl restart inkypi
```

## Key 注册表

这张表来自 `install/api_key_registry.json`，安装脚本和网页都会读取同一份注册表。

| 变量名 | 服务 | 启用功能 | 获取地址 |
| --- | --- | --- | --- |
| `OPEN_AI_SECRET` | OpenAI | AI 图片、AI 文本、AI 每日新闻、E-Paper Pet、AI Image Multiverse | <https://platform.openai.com/api-keys> |
| `GROQ_API_KEY` | Groq | AI 每日新闻、E-Paper Pet、AI Image Multiverse | <https://console.groq.com/keys> |
| `NANO_BANANA_KEY` | Google AI Studio / Gemini | AI Image Multiverse | <https://aistudio.google.com/app/apikey> |
| `AI_HORDE_KEY` | AI Horde | AI Image Multiverse | <https://aihorde.net/register> |
| `OPEN_WEATHER_MAP_SECRET` | OpenWeather | Weather、Mini Weather | <https://home.openweathermap.org/api_keys> |
| `NASA_SECRET` | NASA Open APIs | Astronomy Picture of the Day | <https://api.nasa.gov/> |
| `UNSPLASH_ACCESS_KEY` | Unsplash | Unsplash 图片插件 | <https://unsplash.com/developers> |
| `GITHUB_SECRET` | GitHub | GitHub Contributions、GitHub Sponsors | <https://github.com/settings/tokens> |
| `IMMICH_KEY` | Immich | Image Album | <https://immich.app/docs/features/command-line-interface/> |
| `STEAM_API_KEY` | Steam Web API | Steam Profile Dashboard | <https://steamcommunity.com/dev/apikey> |
| `COMIC_VINE_API_KEY` | Comic Vine | GCD Comic Covers | <https://comicvine.gamespot.com/api/> |
| `RIOT_API_KEY` | Riot Developer Portal | League of Legends Info | <https://developer.riotgames.com/> |
| `OPENDOTA_API_KEY` | OpenDota | Dota Profile Dashboard | <https://www.opendota.com/api-keys> |
| `FLIGHTAWARE_API_KEY` | FlightAware AeroAPI | Flight Radar | <https://flightaware.com/aeroapi/portal/> |
| `RAPIDAPI_KEY` | RapidAPI | Flight Radar、Daily Knowledge | <https://rapidapi.com/hub> |
| `GOOGLE_MAPS_API_KEY` | Google Maps Platform | Flight Radar 地图背景 | <https://console.cloud.google.com/google/maps-apis/credentials> |
| `EUROPEANA_API_KEY` | Europeana | Daily Art | <https://pro.europeana.eu/page/get-api> |
| `HARVARD_ART_MUSEUMS_API_KEY` | Harvard Art Museums | Daily Art | <https://harvardartmuseums.org/collections/api> |
| `TMDB_BEARER_TOKEN` | The Movie Database | Box Office Top Movies | <https://www.themoviedb.org/settings/api> |
| `MASSIVE_API_KEY` | Massive | Stock Tracker、AI 每日新闻市场信息 | <https://massive.com/> |
| `FOOTBALL_DATA_API_KEY` | football-data.org | Sports Dashboard | <https://www.football-data.org/client/register> |
| `API_SPORTS_KEY` | API-SPORTS / API-Football | Sports Dashboard | <https://dashboard.api-football.com/register> |
| `THE_ODDS_API_KEY` | The Odds API | Sports Dashboard 赔率 | <https://the-odds-api.com/#get-access> |
| `ODDS_API_IO_KEY` | Odds-API.io | Sports Dashboard 赔率 | <https://odds-api.io/> |
| `BLIZZARD_CLIENT_ID` | Battle.net Developer Portal | WoW Profile Dashboard | <https://develop.battle.net/access/clients> |
| `BLIZZARD_CLIENT_SECRET` | Battle.net Developer Portal | WoW Profile Dashboard | <https://develop.battle.net/access/clients> |
| `BLIZZARD_USER_ACCESS_TOKEN` | Battle.net OAuth | WoW Profile Dashboard 账号模式 | <https://develop.battle.net/documentation/guides/using-oauth> |

## 别名

部分插件还兼容旧变量名。新安装建议使用上表的主变量名；如果要查看兼容别名，运行：

```bash
python3 install/configure_api_keys.py --list --lang zh-CN
```
