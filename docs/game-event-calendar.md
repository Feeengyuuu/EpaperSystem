# 游戏发布会日历

`simple_calendar` 可在 DATA 刷新时同步已确认的游戏发布会。原有月历布局、个人日程和节假日来源保持独立，游戏活动按设备时区合并到所属月份。

## 启用

在目标日历实例的设置页勾选“自动同步游戏发布会”，选择需要的类别并保存。旧实例默认关闭；首次启用默认选中六类发布会，并允许 Eurogamer / IGN 补充。保存启用设置时，实例的 DATA 周期调整为 3600 秒；已有不超过一小时、且能整除 3600 秒的周期保留。

| 设置字段 | 默认值 | 含义 |
| --- | --- | --- |
| `showGameEvents` | `false` | 总开关 |
| `gameEventSeries[]` | 六类全选 | `state_of_play`、`nintendo_direct`、`summer_game_fest`、`the_game_awards`、`xbox_showcase`、`gamescom_onl`；空列表表示全部不选 |
| `allowGameEventMedia` | `true` | 使用两家媒体补充已经确认的公告 |

设置页只读持久化缓存，展示来源最近成功核验时间、下次可检查时间、异常、活动来源链接及冲突。时间状态使用 UTC 标注；屏幕中的活动时刻使用设备时区。

本次实现没有部署、启用设备实例或修改现有播放列表。启用后的自动检查需要设备运行，且实例处于启用的轮播列表。

## 来源和判定

- State of Play：[PlayStation RSS](https://blog.playstation.com/tag/state-of-play/feed/) 发现公告，继续读取未来活动的正文。
- Nintendo Direct：[最新页面](https://www.nintendo.com/us/nintendo-direct/)和归档中的官方数据。正文的日期、时刻和时区优先；机器字段中的 `Z` 不直接作为可信直播时间。
- SGF / TGA：[Summer Game Fest](https://www.summergamefest.com/)关联的 AddEvent 日历，以及 [TGA 公告](https://thegameawards.com/news)和官方 FAQ。主办方直接公告优先于聚合日历。
- Xbox：[Xbox Wire RSS](https://news.xbox.com/en-us/feed/)及正文，覆盖 Showcase 和 Developer Direct。
- gamescom ONL：[官方节目页](https://www.gamescom.global/en/program)中的活动详情，排除预热节目、回顾和手语转播。
- 媒体：[Eurogamer](https://www.eurogamer.net/feed/news)、[IGN](https://www.ign.com/rss/articles/feed?tags=games)。报道必须明确说明已确认；单家必须给出对应的官方公告链接，否则需要两家独立报道一致。

不使用文章发布时间作为活动时间，不收录传闻、预测、游戏发售日期和播后回顾。只有日期时显示“时间待定”；相对描述不用于推算直播时刻。明确时间统一存成 UTC，同时保留来源日期及其时区。

活动以稳定身份归并。相同 URL / 官方活动 ID 的更新、对应官方链接，以及可确认的同场报道不会重复展示。独立改期公告明确给出原日期时，会在原日期只有一个匹配活动的情况下复用身份。取消和未给出新日期的延期记录仍保留在缓存中，但不出现在正常日程中。冲突保留较高优先级的信息，并标记待核验。

## 缓存与刷新

独立模块位于 `src/plugins/simple_calendar/game_event_sources.py` 和 `game_events.py`。数据存放在：

```text
$INKYPI_DATA_DIR/plugins/simple_calendar/game_events/state.json
```

未设置数据目录时使用 `/var/lib/inkypi/data`。缓存按来源分别记录上次尝试、成功时间、下一次检查、已知公告及错误；与个人日程、节假日缓存分开。

- 每个来源正常每三小时检查一次。请求前原子保存下一次检查时间，失败、DATA 重试和进程重启都不会绕过这个间隔。调度排队和退避可能延迟检查。
- 已发现的未来活动会复查原公告，即使已经离开 RSS。访问失败或列表缺项不会当作取消。
- 来源失败或活动超过核验周期时保留记录，并显示“待核验”。恢复后自动更新。过去活动不会永久拖累当前日历的新鲜度。
- 游戏来源失败时，个人日程和节假日仍能刷新。合并图片照常保存，同时保留 `STALE_CACHE` / `LOCAL_FALLBACK` 状态和现有重试行为。
- 轮播、主题重绘和设置页只读缓存，不请求游戏来源。活动随下一次正常轮播显示。
- 缓存最多 2 MiB，每来源最多 256 条；保留最近 32 天和未来两年的记录。每次检查最多访问每来源 16 个 URL，单响应最多 4 MiB，总网络阶段受 90 秒期限约束。仅访问明确列出的 HTTPS 主机，跳转也需通过校验。

## 本地验证

测试覆盖六类来源、Next.js 数据帧、RSS 正文复查、日期待定、补时刻、跨日/跨月/夏令时、任天堂机器日期不一致、媒体门槛、去重、改期、取消、ICS 修订顺序、断网恢复、持久化三小时间隔、展示零网络及实际 DATA 图片保存。

Windows 当前测试环境使用项目现有 Python 3.12 环境和 `.pc-packages`：

```powershell
# 工作目录：inkypi-weather/package/InkyPi
$env:PYTHONPATH="$PWD;$PWD\src;$PWD\.pc-packages"
$env:PYTHONDONTWRITEBYTECODE='1'
& '.venv-test\Scripts\python.exe' -m pytest -p no:cacheprovider `
    tests/test_game_event_sources.py tests/test_game_events.py `
    tests/test_simple_calendar_game_events.py tests/test_simple_calendar_holidays.py `
    tests/test_weather_calendar_contract.py tests/test_calendar.py `
    tests/test_refresh_validation.py tests/test_plugin_blueprint.py tests/test_playlist_blueprint.py -q
```

本地 800×480 日/夜预览在 `output/game-calendar/`。预览使用模拟活动，检查同日多事件、待定时刻、待核验标记及长标题；长标题沿用现有日历的截断规则。网站结构变化后可能需要维护解析器。

2026-09-02 本地结果：上述回归范围 282 项通过、2 项跳过；真实网络检查的 8 个来源入口均无错误。未进行设备部署或物理墨水屏刷新验证。
