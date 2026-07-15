# InkyPi 插件刷新契约

审计日期：2026-07-15  
实机：`ColoredEpaperFrame`（`192.168.1.196`）  
持久化播放列表：`DailyDoseOfDay`

## 三类刷新不能混为一谈

1. **基础数据刷新**：插件实例保存的 `interval` 或 `scheduled` 规则，负责从远端取数并生成缓存图。
2. **展示时刷新**：轮播或手动切到插件时，根据 manifest 的 `refresh_on_display` / `presentation` 能力，从已有数据重新选择或生成当前展示内容。它不能代替直播数据刷新。
3. **直播覆盖刷新**：只有声明 `supports_live_refresh` 的插件进入活动状态后才启用；只对当前正在展示的直播插件运行，并在生成后安排精确跟随展示。

全局自动轮播间隔为 **300 秒**。轮播到期始终先于独立直播刷新，确保体育直播不会永久占住屏幕；离开体育页后，体育直播快刷停止，普通插件继续按各自基础规则运行。

## 当前 26 个启用实例

| 插件实例 | plugin_id | 基础数据规则 | 展示规则 | 直播覆盖 |
| --- | --- | ---: | --- | --- |
| NASAPics | `apod` | 每日 00:00 | 展示缓存 | 无 |
| BacktotheDate | `backtothedate` | 每日 00:00 | 展示时 presentation | 无 |
| Bambu | `bambu_monitor` | 300 秒 | 展示缓存 | 无 |
| BoxOfficeTopMovies | `box_office_top_movies` | 21600 秒 | 展示缓存 | 无 |
| China Movie Hot | `china_box_office_top_movies` | 21600 秒 | 展示缓存 | 无 |
| Daily AI News | `daily_ai_news` | 每日 07:30 | 展示缓存 | 无 |
| DailyArt | `daily_art` | 300 秒 | 展示时 presentation | 无 |
| DailyWiki | `daily_wiki_page` | 每日 00:15 | 展示时 presentation | 无 |
| DailyWord | `daily_word_poem` | 300 秒 | 展示缓存 | 无 |
| ComicCovers | `gcd_comic_covers` | 300 秒 | 展示时 presentation | 无 |
| LiveRadar | `live_radar` | 120 秒 | 展示时 presentation | 当前页检测到直播且数据年龄达到 60 秒时，60 秒快刷 |
| LoLInfo | `lol_info` | 7200 秒 | 展示时 presentation | 无 |
| MagazineCovers | `magazine_covers` | 300 秒 | 展示时 presentation | 无 |
| ChinaDaily | `newspaper` | 每日 15:00 | 支持 presentation，但未启用展示触发 | 无 |
| DailyPorn | `pixiv_r18_ranking` | 21600 秒 | 展示时 presentation | 无 |
| Date | `simple_calendar` | 21600 秒 | 展示时 presentation | 无 |
| SpeciesRadar | `species_radar` | 21600 秒 | 展示时 presentation | 无 |
| SportsDashboard | `sports_dashboard` | 900 秒 | 直接展示缓存；内部 presentation 刷新已关闭 | 当前页有任何已接入的活动赛事时，统一进入 60 秒快刷通道；新图生成后立即展示 |
| Steam Charts | `steam_charts` | 3600 秒 | 展示缓存 | 无 |
| SteamDailyArt | `steam_daily_art` | 3600 秒 | 展示时 presentation | 无 |
| SteamDaily | `steam_profile_dashboard` | 300 秒 | 展示缓存 | 无 |
| Money | `stocktracker` | 每日 13:10 | 展示时 presentation | 无 |
| TechPulse | `tech_pulse` | 1800 秒 | 展示触发完整生成（无 presentation 快速通道） | 无 |
| Telegram Digest | `telegram_digest` | 21600 秒 | 展示时 presentation | 无 |
| DailyShow | `ticketmaster_events` | 每日 00:00 | 展示缓存 | 无 |
| AwesomeWeather | `weather` | 300 秒 | 展示缓存 | 无 |

## SportsDashboard 直播契约

- 基础取数仍为 **900 秒**；它负责非直播时的常规赛程与结果更新。
- 直播覆盖不是 World Cup 特例。当前统一接入的直播状态包括：
  - 足球：World Cup（ESPN）；
  - 篮球及综合美国赛事：NBA，以及 Offseason Hub 中的 MLB、WNBA、PGA、NFL、NCAA；
  - 英雄联盟：LPL、LCK、MSI；
  - 电竞：EWC、Valve CS/Dota；
  - 赛车：F1。
- 上述赛事的直播图像刷新默认均为 **60 秒**，设置允许范围为 60–900 秒；多个赛事同时直播时取最短的已配置间隔。
- 直播状态来自各赛事状态文件与提供方实时响应，而不是仅根据开赛时间猜测。World Cup 只是本次实机验证恰好可用的 ESPN 样本。
- 直播快刷只在 SportsDashboard 当前正在屏幕上展示时运行，离开页面即停止独立快刷。
- SportsDashboard 自己的 `refresh_on_display` 与 presentation capability 已关闭；轮播或手动进入页面不会额外触发内部面板翻页/重生成。实时赛事的新数据图仍由直播通道生成，并通过精确跟随展示立即上屏。
- 当前页直播到期时：
  - `HEALTHY` / `SOFT` 资源层：直播刷新可以抢占普通后台数据刷新；
  - `HARD` 资源层：阻止直播生成，避免内存耗尽；当前阈值为可用内存低于 70 MB 或 swap 达到 75%；
  - 全局 300 秒轮播到期：轮播仍先执行，防止体育页饿死其他页面。
- 每次 `live_refresh` 成功生成新图后，调度器保留一个与该实例绑定的 `live/display_cache` 跟随展示，不把别的插件缓存误显示到屏幕。
- 直播刷新与跟随展示不会推进普通数据公平性锚点或全局轮播锚点。

## 2026-07-15 实机执行证据

- ESPN 官方数据在验证窗口内由 `87' 1-1` 推进到 `90'+9' 1-2`。
- `13:50:33`：`source=live, intent=live_refresh, plugin_id=sports_dashboard`。
- `13:50:40`：同一实例立即进入 `source=live, intent=display_cache`，中间没有普通后台插件插队。
- `13:51:36`：Waveshare 完成物理刷新并进入休眠。
- 全赛事发布重启后，`13:57:01` 再次执行 `live_refresh`，`13:57:38` 进入精确跟随展示；后续 60 秒轮次继续运行。
- `/api/current_image` 与该轮 SportsDashboard 插件缓存 SHA-256 都为 `DCA689F9BBC20A3C3B08E1932657D1997426CC87D4072E5BF64C8E82CDAD2209`；画面显示 `ESPN DATA 1:58 PM`、`ESPN 90'+6' 1-2`。
- 发布身份精确匹配 `deploy-20260715-all-sports-live-12d2f390`，服务 `NRestarts=0`。

## 已知外部数据源告警

`api.csapi.de` 在此次验证期间返回证书过期错误，影响 SportsDashboard 的 CSAPI Major 数据块。它不影响本场 World Cup 的 ESPN 数据链路，但必须作为独立提供方故障处理，不能把它误判成 ESPN 直播调度失效。
