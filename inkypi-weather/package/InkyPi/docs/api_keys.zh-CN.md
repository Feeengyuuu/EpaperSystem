# API Key 获取与配置

API Key 都是可选的。不填也能正常安装和启动；用到某个 Key 的页面会暂时不可用、显示缓存，或退回到公开/本地数据源。下表没有出现的页面都不需要 API Key（个别页面要在自己的设置里填写地址或账号，例如日历的 ICS 链接）。

## 密钥存放位置

在树莓派上，运行中的密钥保存在 `/etc/inkypi/inkypi.env`（只有服务账户可读）。不要在源码目录另建一份 `.env` 当作运行配置。

本地开发时可以在项目根目录（`inkypi-weather/package/InkyPi/.env`）放一份 `.env`；之后运行安装器时，它会把其中**缺少**的值合并进 `/etc/inkypi/inkypi.env`，已有的值不会被覆盖。`.env` 已被 Git 忽略，不要提交到 GitHub。

## 添加或修改 Key

**网页**：打开 `http://<你的树莓派>/api-keys`，按服务分组填写。

**安装时**：安装器会询问常用的 Key，直接回车即可跳过。加 `--all-keys` 会逐个询问全部 Key，加 `--skip-keys` 则完全跳过、以后在网页里填。

**命令行**（可在任意目录运行）：

```bash
# 交互填写常用 Key；加 --all 询问全部 Key
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --env-file /etc/inkypi/inkypi.env --lang zh-CN

# 查看全部 Key、获取地址和兼容别名
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --list --lang zh-CN

# 检查哪些 Key 已经配置
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --check --env-file /etc/inkypi/inkypi.env --lang zh-CN
```

用命令行修改后重启服务：

```bash
sudo systemctl restart inkypi
```

## Key 一览

下面列出安装器和网页使用的全部 Key，内容与 `src/config/secret_schema.json`（以及由它生成的 `install/api_key_registry.json`）一致。

### AI 与生成

| 变量名 | 服务 | 用于 | 获取地址 |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | OpenAI | AI 图片、AI 文本、AI每日新闻（整点新闻）、E-Paper Pet、AI Image Multiverse | <https://platform.openai.com/api-keys> |
| `GROQ_API_KEY` | Groq | AI每日新闻（整点新闻）、E-Paper Pet、AI Image Multiverse | <https://console.groq.com/keys> |
| `NANO_BANANA_KEY` | Google AI Studio / Gemini | AI Image Multiverse | <https://aistudio.google.com/app/apikey> |
| `AI_HORDE_KEY` | AI Horde | AI Image Multiverse | <https://aihorde.net/register> |

### 天气、天文与地图

| 变量名 | 服务 | 用于 | 获取地址 |
| --- | --- | --- | --- |
| `OPEN_WEATHER_MAP_SECRET` | OpenWeather | Weather、Mini Weather | <https://home.openweathermap.org/api_keys> |
| `NASA_SECRET` | NASA Open APIs | Astronomy Picture of the Day | <https://api.nasa.gov/> |
| `GOOGLE_MAPS_API_KEY` | Google Maps Platform | Flight Radar 地图背景、Vehicle Status 地图与地址 | <https://console.cloud.google.com/google/maps-apis/credentials> |

### 图片与艺术

| 变量名 | 服务 | 用于 | 获取地址 |
| --- | --- | --- | --- |
| `UNSPLASH_ACCESS_KEY` | Unsplash | Unsplash | <https://unsplash.com/developers> |
| `IMMICH_KEY` | Immich | Image Album | <https://immich.app/docs/features/command-line-interface/> |
| `EUROPEANA_API_KEY` | Europeana | Daily Art | <https://pro.europeana.eu/page/get-api> |
| `HARVARD_ART_MUSEUMS_API_KEY` | Harvard Art Museums | Daily Art | <https://harvardartmuseums.org/collections/api> |
| `COMIC_VINE_API_KEY` | Comic Vine | GCD Comic Covers | <https://comicvine.gamespot.com/api/> |
| `PIXIV_PHPSESSID` | Pixiv | Pixiv R-18 Ranking（普通榜单不需要） | <https://www.pixiv.net/settings/viewing> |

### 体育与电竞

| 变量名 | 服务 | 用于 | 获取地址 |
| --- | --- | --- | --- |
| `FOOTBALL_DATA_API_KEY` | football-data.org | Sports Dashboard | <https://www.football-data.org/client/register> |
| `API_SPORTS_KEY` | API-SPORTS / API-Football | Sports Dashboard | <https://dashboard.api-football.com/register> |
| `THE_ODDS_API_KEY` | The Odds API | Sports Dashboard 赔率 | <https://the-odds-api.com/#get-access> |
| `ODDS_API_IO_KEY` | Odds-API.io | Sports Dashboard 赔率 | <https://odds-api.io/> |
| `PANDASCORE_API_KEY` | PandaScore | Sports Dashboard CS2 | <https://app.pandascore.co/signup> |

### 游戏与直播

| 变量名 | 服务 | 用于 | 获取地址 |
| --- | --- | --- | --- |
| `STEAM_API_KEY` | Steam Web API | Steam Profile Dashboard | <https://steamcommunity.com/dev/apikey> |
| `RIOT_API_KEY` | Riot Developer Portal | League of Legends Info | <https://developer.riotgames.com/> |
| `OPENDOTA_API_KEY` | OpenDota | Dota Profile Dashboard | <https://www.opendota.com/api-keys> |
| `BLIZZARD_CLIENT_ID` | Battle.net Developer Portal | WoW Profile Dashboard | <https://develop.battle.net/access/clients> |
| `BLIZZARD_CLIENT_SECRET` | Battle.net Developer Portal | WoW Profile Dashboard | <https://develop.battle.net/access/clients> |
| `BLIZZARD_USER_ACCESS_TOKEN` | Battle.net OAuth | WoW Profile Dashboard 账号模式 | <https://develop.battle.net/documentation/guides/using-oauth> |
| `TWITCH_CLIENT_ID` | Twitch 应用 Client ID | LiveRadar、科技与游戏 | <https://dev.twitch.tv/console/apps> |
| `TWITCH_CLIENT_SECRET` | Twitch 应用 Client Secret | LiveRadar、科技与游戏 | <https://dev.twitch.tv/console/apps> |

### 财经

| 变量名 | 服务 | 用于 | 获取地址 |
| --- | --- | --- | --- |
| `MASSIVE_API_KEY` | Massive | Stock Tracker、AI每日新闻行情条 | <https://massive.com/> |
| `FINNHUB_API_KEY` | Finnhub | 资金雷达（持仓财报日） | <https://finnhub.io/dashboard> |
| `FRED_API_KEY` | FRED（圣路易斯联储） | 资金雷达（CPI、失业率、联邦基金利率、30 年房贷利率） | <https://fredaccount.stlouisfed.org/apikeys> |

### 影视、活动与资讯

| 变量名 | 服务 | 用于 | 获取地址 |
| --- | --- | --- | --- |
| `TMDB_BEARER_TOKEN` | The Movie Database | Box Office Top Movies | <https://www.themoviedb.org/settings/api> |
| `TMDB_API_KEY` | The Movie Database（API Key） | Box Office Top Movies | <https://www.themoviedb.org/settings/api> |
| `TICKETMASTER_API_KEY` | Ticketmaster Discovery API | Ticketmaster 本地活动 | <https://developer.ticketmaster.com/products-and-docs/apis/getting-started/> |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API | Telegram Digest 机器人模式 | <https://core.telegram.org/bots/tutorial> |
| `TELEGRAM_API_ID` | Telegram API ID | Telegram Digest 账号模式 | <https://my.telegram.org/apps> |
| `TELEGRAM_API_HASH` | Telegram API Hash | Telegram Digest 账号模式 | <https://my.telegram.org/apps> |
| `TELEGRAM_SESSION_PATH` | Telegram 会话文件路径 | Telegram Digest 账号模式 | <https://docs.telethon.dev/en/stable/concepts/sessions.html> |
| `REDDIT_CLIENT_ID` | Reddit OAuth Client ID | Reddit Rule34 Hot | <https://www.reddit.com/prefs/apps> |
| `REDDIT_CLIENT_SECRET` | Reddit OAuth Client Secret | Reddit Rule34 Hot | <https://www.reddit.com/prefs/apps> |
| `REDDIT_REFRESH_TOKEN` | Reddit OAuth Refresh Token | Reddit Rule34 Hot | <https://github.com/reddit-archive/reddit/wiki/OAuth2> |

### 出行与设备

| 变量名 | 服务 | 用于 | 获取地址 |
| --- | --- | --- | --- |
| `FLIGHTAWARE_API_KEY` | FlightAware AeroAPI | Flight Radar | <https://flightaware.com/aeroapi/portal/> |
| `OPENSKY_USERNAME` | OpenSky Network 用户名 | Flight Radar | <https://opensky-network.org/> |
| `OPENSKY_PASSWORD` | OpenSky Network 密码 | Flight Radar | <https://opensky-network.org/> |
| `RAPIDAPI_KEY` | RapidAPI | Flight Radar、Daily Knowledge | <https://rapidapi.com/hub> |
| `BAMBU_ACCESS_CODE` | Bambu Lab 打印机（局域网访问码，在打印机屏幕上查看） | Bambu Monitor | <https://wiki.bambulab.com/en/general/find-sn> |
| `EPAPER_VEHICLE_BRIDGE_TOKEN` | 自建的 Tesla 桥接 Worker（只读令牌） | Vehicle Status | [cloudflare/vehicle-auth-bridge](../../../../cloudflare/vehicle-auth-bridge) |
| `EPAPER_VEHICLE_BRIDGE_ORIGIN` | 自建桥接 Worker 的地址，如 `https://vehicle-bridge.<你的子域>.workers.dev`（不填则使用项目维护者的桥接） | Vehicle Status | [cloudflare/vehicle-auth-bridge](../../../../cloudflare/vehicle-auth-bridge) |

### 开发者

| 变量名 | 服务 | 用于 | 获取地址 |
| --- | --- | --- | --- |
| `GITHUB_SECRET` | GitHub | GitHub Contributions、GitHub Sponsors | <https://github.com/settings/tokens> |

## 兼容别名

部分插件还兼容旧变量名，例如 `OPEN_AI_SECRET` 等同于 `OPENAI_API_KEY`。新安装请使用上表的主变量名；查看全部别名：

```bash
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --list --lang zh-CN
```
