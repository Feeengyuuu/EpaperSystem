# NASAPics 当日 APOD × 宇宙天气设计

**日期：** 2026-07-22

**状态：** 视觉方向已批准；书面审阅待确认

**范围：** 原地升级 `apod / NASAPics`，不新建第二个插件

## 1. 问题

当前生产实例 `DailyDoseOfDay / apod / NASAPics` 仍保存
`{"randomizeApod":"true"}`，每天定时刷新一次。现有插件虽然已经从 NASA
APOD 响应中取得 `title`、`date`、`explanation`、`copyright` 和图片 URL，最终
画面却只显示全屏照片与左下角 NASA 标志，没有文字、来源或宇宙天气。

生产证据还显示一个需要纳入验收的刷新一致性问题：播放列表记录在
2026-07-22 刷新过，但实际 PNG 的 `Last-Modified` 仍停在 2026-07-15。这意味着
不能只用“任务执行过”证明页面已更新；必须同时验证最终图片文件、时间戳、哈希和
物理屏幕。

本设计把 NASAPics 从单张照片升级为一张每日宇宙简报，同时保留照片作为主视觉。

## 2. 已批准的用户选择

1. 每天展示当天 APOD，不再以随机历史 APOD 作为生产默认。
2. 页面镜像 DailyWiki：宇宙天气在左，APOD 图片在右。
3. 采用浅色“宇宙日报”视觉，但加入任务控制台级别的信息量。
4. 右侧图片下方固定显示中文译名和英文原题。
5. 右侧照片必须铺满照片边框，不出现黑边；允许按比例居中裁切少量边缘。
6. 左侧 Kp 区域的位置、字号和比例已经批准，不能因为图片铺满、标题长度、字段
   缺失或通用布局调整而移动。
7. APOD 每个设备自然日固定一张；普通数据缓存以 30 分钟为更新周期。
8. 数据任务可以更新离屏 PNG 缓存；NASAPics 的自主物理写屏只来自正常轮播的
   DISPLAY 命令。用户显式点击 `Display Now` 是允许的一次性例外，但 live、
   presentation、refresh-on-display、主题切换和 DATA_REFRESH 都不得追加刷屏。
9. 网络失败时保留最后成功页面或带真实时间戳的 last-good 数据，绝不伪造天气。

## 3. 目标

- 将当天 APOD、版权和双语标题组成稳定的右侧图片区。
- 在左侧展示当前 G/R/S、Kp、48 小时峰值、太阳风、Bz、Bt、概率和最高优先级
  预警。
- APOD 一天内不变，天气更新不会重复下载当天大图。
- 使用普通 DATA_REFRESH 生成离屏缓存；不启用 live、presentation 或
  refresh-on-display 刷屏通道。
- 各数据源独立缓存和回退，单个 NOAA 端点失败不能擦掉其他成功数据。
- 使用已经批准的单一高对比配色，并关闭会在日出/日落追加 DISPLAY 的主题重绘。
- 在 800×480 彩色墨水屏上使用深色粗体、固定坐标和可验证的最小字号。
- 成功刷新必须同时更新真实 PNG 与实例成功时间；失败不能冒充成功。

## 4. 非目标

- 不新建独立的“宇宙天气”播放列表插件。
- 首版不加入 OVATION 极光地图、太阳表面图或连续动态图。
- 不展示完整 APOD explanation；页面只保留标题、日期和必要版权。
- 不改造 DailyWiki，也不借本任务重构全局 RefreshTask。
- 不让 NASA DONKI 取代 NOAA；DONKI 只补充显著事件。
- 不在没有可靠译文时生成看似可信的伪中文标题。
- 不把缓存刷新等同于物理屏幕刷新，也不使用会导致第二次 DISPLAY 的
  refresh-on-display prepared flow。

## 5. 页面与几何约束

实现使用 PIL 原生绘制，不使用 HTML 截图。800×480 是基准画布；其他分辨率先按
800×480 完整排版，再在末端等比适配。

### 5.1 基准主矩形

| 区域 | 基准矩形 | 说明 |
| --- | --- | --- |
| 通栏页眉 | `(0, 0, 800, 65)` | 左侧品牌，右侧设备日期与数据更新时间 |
| 左信息区 | `(0, 65, 368, 480)` | 画面宽度 46% |
| 右照片区 | `(368, 65, 800, 364)` | 基准 432×299，满框 `cover` |
| 右双语题注 | `(368, 364, 800, 480)` | 基准 432×116，中文、英文、日期、版权 |
| 中间分隔线 | `x=366..367, y=65..479` | 画在左区内部，不侵占照片 |

右侧结构必须始终是“照片在上、双语题注在下”。不得把题注移到左侧，也不得让照片
覆盖题注。只有照片和题注之间的水平边界允许因超长标题在 `y=300..364` 内上移；两者
的 `x=368..800`、页眉、左信息区以及 Kp 矩形都不可变化。所有矩形使用右/下边界
排他的半开区间，避免 Pillow crop/paste 的 1px 歧义。

### 5.2 左侧固定分区

| 区域 | 固定矩形 | 内容 |
| --- | --- | --- |
| 当前 Kp 与摘要 | `(20, 77, 354, 180)` | Kp 数值、观测/估算标识、当前状态、48 小时峰值 |
| G/R/S 状态条 | `(20, 190, 354, 218)` | 当前 G、R、S 等级 |
| 四宫格指标 | `(20, 228, 354, 318)` | 太阳风、Bz、Bt、未来 G/Kp |
| 三项概率 | `(20, 326, 354, 364)` | R1–R2、R3–R5、S1+ 概率 |
| 预警摘要 | `(20, 374, 354, 414)` | 一条有效且最高优先级信息 |
| 来源与新鲜度 | `(20, 456, 354, 476)` | NOAA SWPC、观测时间、缓存状态 |

Kp 由专用绘制函数接收完整固定矩形，禁止依赖累计 `cursor_y`。标题长短、预警是否
存在、主题颜色或图片裁切都不能改变 Kp 矩形。字段缺失时在原单元格显示 `—`，后续
字段不得向上补位。

### 5.3 照片裁切

先测量题注并确定最终照片高度：

```python
caption_h = clamp(measured_caption_h, 116, 180)
caption_top = 480 - caption_h
photo_size = (432, caption_top - 65)
```

照片先执行 EXIF transpose，再转 RGB，使用 LANCZOS 和
`ImageOps.fit(source, photo_size, centering=(0.5, 0.5))` 居中等比裁切。必须把最终
`photo_size` 直接交给安全图片加载/解码路径，不能先 cover 成 800×480 再二次裁成
右上照片，否则会额外丢失画面。

硬约束：

- 右照片区四边都必须是源图像像素；不能有 letterbox、黑边、内边距或圆角。
- 不允许拉伸或改变宽高比。
- NASA 标志不再压在照片上；品牌只出现在通栏页眉。
- 将来若增加焦点设置，只能改变 `centering`，不能改变照片矩形或左侧布局。

### 5.4 双语题注与字体

右侧题注依次显示：

1. `TODAY'S ASTRONOMY PICTURE`；
2. 中文标题；
3. NASA 返回的英文原题；
4. `NASA APOD · 实际 APOD 日期` 与版权/摄影署名。

中文和英文分别测量，不用英文行高推断中文。先在各自字号范围内逐像素缩放，再增加
换行；不得因为标题变长而移动左侧 Kp 区。若基准 116px 题注无法容纳完整中英文，
允许题注向上扩到最多 180px并等量减少照片高度；照片仍在新的照片矩形内满框裁切。
英文原题、APOD 日期和版权不能与另一张图片的元数据混用。

题注降级顺序固定为：

1. 中文从 20px 逐级缩到 16px，英文从 14px 逐级缩到 11px；
2. 中文最多 3 行，英文可增加到 6 行，题注从 116px 扩到 150px；
3. 仍不足时隐藏可选的英文 kicker，并把版权压缩为 `© 姓名`；
4. 仍不足时只在右侧把题注扩到最多 180px，英文保持完整，左侧所有矩形不动；中文
   3 行、英文 6 行的上限在 180px 阶段也不放宽。

不得使用省略号或截掉 NASA 英文原题。实现应以至少 300 个英文字符和 60 个中文字符
的合成标题分别证明完整排版；组合题注必须先经过真实字体测量。即使字符数没有超过
该测试下限，只要最终仍无法在 180px 内完整放下，也视为无效候选，不得绘制越界文字
或覆盖当前有效 PNG。

字体使用 Microsoft YaHei/Base UI 的 Bold 或 Semibold：

- 页眉品牌 27–29px；最低 26px。
- Kp 数值 42–54px；最低 36px。
- Kp 状态 18–20px；最低 16px。
- 指标数值 17–19px；指标标签最低 12px。
- 中文题注 16–20px；英文题注 14px起、最低 11px；英文 kicker 12px Semibold。
- 预警最低 12px；日期、来源与版权最低 10px且必须使用 Semibold。

禁止 Thin/Light 和低对比浅灰正文。首版使用批准稿的单一高对比配色，并在 manifest
关闭昼夜主题能力，避免日出/日落在轮播之外触发额外物理刷新。

## 6. 数据源与字段语义

### 6.1 NASA APOD

使用现有 `NASA_SECRET` 调用 `https://api.nasa.gov/planetary/apod`。保留同一响应中
的 `date`、`title`、`explanation`、`copyright`、`media_type`、`url`、`hdurl`。

最终照片矩形为 432×235..299，因此优先使用标准 `url`，只有它缺失、分辨率不足或
无效时才尝试 `hdurl`。这既满足分辨率，又避免 Zero 2 W 重复解码超大图片。

### 6.2 NOAA SWPC

| 端点 | 归一化字段 | 网络刷新上限 |
| --- | --- | --- |
| `products/noaa-scales.json` | 当前 G/R/S、UTC 日期、R1–R2/R3–R5/S1+ 概率、未来 G | 30 分钟 |
| `products/noaa-planetary-k-index-forecast.json` | Kp、`observed/estimated/predicted`、NOAA scale、UTC 时段 | 30 分钟 |
| `products/summary/solar-wind-speed.json` | `proton_speed`、观测时间 | 30 分钟 |
| `products/summary/solar-wind-mag-field.json` | `bt`、`bz_gsm`、观测时间 | 30 分钟 |
| `products/alerts.json` | 有效 Alert/Warning/Watch/Summary | 30 分钟 |

归一化必须遵循以下语义：

- 所有 NOAA 日期和无 `Z` 的产品时间按 UTC 解析，另存本地抓取时间。
- scales 顶层 `-1` 是过去 24 小时最大观测，`0` 是最新观测，`1/2/3`
  是按 `DateStamp` 排序的预测，不能依赖 JSON 对象遍历顺序。
- `R.MinorProb` 显示为 R1–R2 概率；`R.MajorProb` 显示为 R3–R5；
  `S.Prob` 显示为 S1+。使用按日期排序后最早的有效预测日（通常为 key `1`），百分比
  字符串必须归一化并校验在 0..100；`DateStamp` 是预测有效日，不是抓取时间。
- Kp normalizer 同时接受当前对象数组和旧版“首行为表头”的二维数组。
- 当前 Kp 从 mode 属于 `observed/estimated` 且 `time_tag <= now + 5min` 的所有行中
  选择 `time_tag` 最大的一行；不能因为数组里残留了更旧的 `estimated` 就压过更新的
  `observed`。`time_tag` 是三小时 Kp 时段的起点，页面按获胜行显示“估算”或“观测”。
- 未来 48 小时峰值只从 `now < time_tag <= now + 48h` 且 mode 为 `predicted` 的行
  计算，并保留获胜行自己的 `noaa_scale`。
- 不用 `round(kp)` 自行推导 G 等级；直接使用 NOAA 的 `noaa_scale`。当前 G/R/S
  以 scales 的最新观测为准。
- 太阳风和磁场数组按 `time_tag` 排序取最新，不能固定读取 `[0]`。速度和磁场时间
  相差超过 5 分钟时分别标注时间，不把它们伪装为同一时刻。
- Bz 只按原始数值符号描述“北向/南向”，精确为 0 时显示“中性”；不使用人为风险
  阈值，也不把显示取整后的 0.0 当成 provider 风险结论。

### 6.3 NOAA 警报

`alerts.json` 只有 `product_id`、`issue_datetime` 和自由文本 `message`，不能假设
响应数组已排序，也不能假设每条消息都有 NOAA 声明的 `valid_until`。归一化先从
message 解析产品类型、serial、severity、`Valid To` / `Now Valid Until`、取消引用和
supersedes 语句；无法识别的消息忽略并记录，不猜测等级或有效期。然后按
`issue_datetime` UTC 升序折叠状态：

1. 普通消息按 serial/产品键加入候选；
2. `Cancel Serial Number` 移除被引用 serial；
3. extension 用新消息替代原 serial 并更新其有效期；
4. `THIS SUPERSEDES ANY/ALL PRIOR WATCHES` 先清除旧 watch 再加入新 watch；
5. 最后按当前 UTC 剔除已过 NOAA 有效期或本地展示期限的候选。

归一化的 `valid_until` 可为空；另存 `display_until` 作为保守的本地展示截止时间，并
明确标记它不是 NOAA 声明。确定规则为：有 `Valid To/Now Valid Until` 时直接采用；
WATCH 有逐日预测表时取最后一个预测日的 UTC 日末且最多不超过 issue+96h；ALERT 取
`issue_datetime + 3h`；SUMMARY 取 issue+24h；WARNING 缺失 provider 有效期时忽略而
不是猜。`Synoptic Period` 只存为事件的 `event_period_start/end`，不能当展示截止时间，
因为 ALERT 可能在该观测时段结束后才发布。过期消息即使缓存文件仍新鲜也不能展示。

当前 G/R/S 已在独立状态条展示，不再占用警报摘要。可折叠候选先按已解析的 NOAA
G/R/S 数字等级降序，再按消息类型 `ALERT > WARNING > WATCH > SUMMARY`，最后按
`issue_datetime` 降序。`显著` 的最低边界是可映射到 G1/R1/S1 或更高，或 NOAA 明确
发布且仍有效的 WWA；无法映射 severity 的自由文本不能靠关键词猜测。展示优先级为：

1. 最高等级且有效的 NOAA `ALERT/WARNING/WATCH`；
2. 近 24 小时且可映射等级的 `SUMMARY`；
3. NASA DONKI 的补充显著事件；
4. `暂无显著空间天气警报`。

合法空响应或成功折叠为空是一次成功结果，状态为 `confirmed_empty`，必须清除先前
已失效的选择。请求失败状态为 `unavailable`，不能把“源不可用”伪装成“没有警报”。

### 6.4 NASA DONKI

DONKI 复用 `NASA_SECRET`，是低优先级补充源。首版只请求官方 `FLR` 与 `CME`：FLR
查询 UTC today-1d..today，CME 查询 UTC today-7d..today。CME 从每条记录的
`cmeAnalyses[]` 中选择 `isMostAccurate=true`
的分析，再读取其嵌套 `enlilList`；不能调用字段不足的独立 `CMEAnalysis` 端点来假装
拥有 Earth-impact 信息。过滤边界为：

- FLR 只展示 peak 位于最近 24 小时且等级不低于 M5.0 的事件；X 级自然包含在内。
- CME 只接受 `isMostAccurate` 分析，且 ENLIL/impact 数据明确给出 Earth impact、
  `isEarthGB`、`isEarthMinorImpact` 或 `impactList.location == Earth`，预计到达位于
  `now - 6h .. now + 72h`。
- 页面标注 `NASA 实验/模型估计`；不得从 note、经纬度或方向文字自行推断撞击地球。

若有多条 DONKI 候选，唯一选择顺序固定为：先选符合条件的 Earth-impact CME，按预计
到达时间与 now 的绝对距离升序、分析提交时间降序；没有 CME 时才选 FLR，按 X 高于 M、
数值等级降序、peak 时间降序。相同事件 ID 只保留最新 most-accurate 分析。

DONKI 缓存 60 分钟，失败或没有满足条件的事件都不会降低 NOAA 核心页面的成功状态，
但失败与确认无事件在内部状态中保持可区分。

### 6.5 中文标题

英文标题始终以 NASA payload 为权威。中文标题使用设备已经配置的
`OPEN_AI_SECRET` / `OPENAI_API_KEY`，必要时回退 `GROQ_API_KEY`，只发送英文标题
做一次短翻译，不发送密钥、图片或 APOD explanation。

翻译按 `APOD date + SHA-256(English title)` 缓存，同一标题每天最多成功调用一次。
翻译失败时使用匹配同一英文标题的 last-good 译文；没有匹配译文时保留英文原题并
显示 `中文译名暂不可用`，不能编造译文。该失败不阻止 APOD 或天气更新。

## 7. 归一化数据模型

`ApodRecord` 至少包含：

- `selection_key`、`requested_device_date`、`date`、`media_type`；
- `title_en`、`title_zh`、`translation_state`；
- `copyright`、`image_url`、`image_cache_key`；
- `fetched_at_utc`、`source_state`、`warning`。

`SpaceWeatherSnapshot` 至少包含：

- `fetched_at_utc`、`oldest_core_observed_at_utc`；
- `current_scales: {g, r, s}`；
- `current_kp: {value, mode, time_tag, noaa_scale}`；
- `forecast_48h: {max_kp, noaa_scale, time_tag}`；
- `solar_wind: {speed_km_s, time_tag}`；
- `magnetic_field: {bt_nt, bz_nt, direction, time_tag}`；
- `probabilities: {r1_r2, r3_r5, s1_plus, forecast_date}`；
- `alert: {state, source_state, kind, severity, headline_zh, issue_time,
  event_period_start?, event_period_end?, valid_until?, display_until?}`；
- `donki_event`；
- `sources`（每源含 `fetched_at_utc`、provider 观测/发布时间、预测有效期、cache age、
  data age/validity）、`errors`、`aggregate_state`。

渲染只读取归一化对象，不在绘图函数里解析 provider JSON。

## 8. APOD 每日锁定规则

生产实例迁移为 `randomizeApod=false` 且清空 `customDate`。设备时区的自然日是页面
选择键；同一天的所有展示、天气更新和重复合成都必须复用同一 APOD payload 与媒体。

当前设置页会在没有保存值时把 `customDate` 自动填成今天；实施必须改为空字符串，
以“留空 = 每天当天”为唯一语义。否则用户下一次保存设置会把当天日期写成永久自选
日期，使 NASAPics 停在某一天。

为兼容其他实例，保留现有自选日期和随机模式，但改变随机语义：随机模式每个设备
自然日只抽一次并持久化选择，不得在每次展示时重新抽图。普通 `forceRefresh` 只重试
同一选择，不重抽；以后若需要重抽，必须是显式的 `reroll` 操作。

当天 APOD 是视频或图片不可解码时，最多向前查找 7 天内最近可用图片，并在题注明确
显示其真实日期和 `最近可用 APOD`。不得把前一天图片标成今天。当天 payload 明确为
视频时，该图片回退可锁定到次日；当天图片下载/解码失败时回退标记为 provisional，
后续 30 分钟 DATA_REFRESH 只重试当天媒体（遵守 HTTP 条件请求/退避），不重复下载
已经验证的回退图；当天图片一旦成功，只切换一次并锁定。

## 9. 缓存结构与新鲜度

不要使用一个 all-or-nothing 大文件。缓存位于插件运行时 cache 目录：

```text
apod/
  instances/<sha256(instance_uuid + ":" + structural_generation)>/
    apod.json
    translation.json
    scales.json
    kp.json
    wind_speed.json
    wind_mag.json
    alerts.json
    donki.json
    aggregate.json
  media/<sha256(url)>.<validated extension>
```

现有 `BasePlugin.cache_dir()` 只隔离到 plugin id，因此不能直接把上述 JSON 放在插件
根目录。生产渲染必须从 runtime 的可信 `current_instance_identity()` 取得非空
instance UUID 与 structural generation，计算不可逆安全目录名；不能相信普通 settings
里的同名字符串。若生产 DATA_REFRESH 缺少可信 identity，必须 fail closed 且不读写
任何共享 source/selection 状态。测试或本地预览若没有 identity，只能使用显式临时、
non-cacheable namespace。

随机日选择键单独放在同一 identity 对应的持久 data namespace，避免缓存清理导致同一
天换图，也避免当天/随机/自选实例互相覆盖。settings revision 不拆新目录，但 selection
record 必须包含 mode、requested date/custom date 和设置指纹，变化后旧选择失效。
媒体是唯一允许跨实例共享的内容寻址层，使用受预算管理的 namespace，至少保留当前和
上一张有效 APOD；不能每 30 分钟重复下载。同 URL 的媒体可以去重，但任何 metadata、
翻译、aggregate、错误或选择状态都不得跨实例复用。

同一个 scales 响应只写一个 `scales.json`，避免 current/forecast 两个文件出现不同
代际。每个数据文件包含 `schema`、`endpoint`、`fetched_at_utc`、provider 的
`issued_at_utc/observed_at_utc`（按产品可空）、预测 `valid_from/valid_until`（按产品
可空）、归一化 `payload` 和 `raw_digest`。成功响应原子替换自己的文件；失败只写
聚合错误，绝不覆盖 last-good。HTTP 200 但 payload 无法通过结构或时间语义校验也算
失败，不能给冻结的上游永久“续鲜”。

fresh/stale 使用两道门：缓存年龄按 `now - fetched_at_utc`，数据语义年龄/有效期按
provider 的观测、发布或预测时间单独判断。预测时间可以合法位于未来，绝不参与“未来
5 分钟异常”校验。只有 `fetched_at_utc`、发布和观测时间超过当前 UTC 5 分钟才异常；
scales 的源更新时间只取 key `0` 的 `DateStamp + TimeStamp`，不能取 key `1/2/3` 的
预测有效日。

| 数据 | fresh cache | 最长保留/可诊断 stale | 进入新候选时的行为 |
| --- | --- | --- | --- |
| APOD metadata | 当前设备自然日 | 48 小时 | 保留旧整页或明确显示真实旧日期 |
| scales | 抓取 ≤30 分钟且 key `0` provider 数据年龄 ≤30 分钟、未异常/未回退 | provider 数据年龄 30 分钟..2 小时 | stale 不进入可提升候选，单元格显示 `—` |
| Kp | 抓取 ≤30 分钟且最新非预测行语义有效 | 6 小时 | stale 不进入可提升候选；诊断文案为“最近 Kp（HH:mm UTC）” |
| wind speed / mag | 抓取 ≤30 分钟且观测年龄 ≤30 分钟 | 观测年龄 30..60 分钟 | stale 不进入可提升候选；对应单元格显示 `—` |
| alerts | 成功抓取 ≤30 分钟 | 2 小时且消息本身仍有效 | 有 fresh 有效缓存可显示；否则显示“警报数据暂不可用” |
| DONKI | 60 分钟 | 24 小时 | 隐藏补充事件 |
| 中文译名 | 标题哈希匹配 | 标题哈希匹配期间 | 回退英文，不跨标题复用 |

Kp 的 `time_tag` 是三小时时段起点，不能直接拿它与 30 分钟 cache TTL 比较。当前行的
语义有效期以该三小时时段为基础并允许最多 30 分钟发布宽限；超过后只可作为带时间的
“最近 Kp”，不能作为新页面的“当前 Kp”。wind/mag 的 `fetched_at` 和观测时间都必须
保存；每次 HTTP 200 若最新观测时间没有推进，只更新抓取记录，不能掩盖冻结状态。

警报使用三态：

- 本轮成功且有有效候选：`active`，显示唯一最高优先级警报；
- 本轮成功且折叠后为空：`confirmed_empty`，显示“暂无显著空间天气警报”；
- 本轮失败但有 ≤30 分钟且仍有效的缓存：仍为 `active`，但
  `source_state=fresh_cache`，显示缓存警报及其时间；本轮成功则
  `source_state=live`；
- 本轮失败且没有可接受 fresh 缓存：`unavailable`，显示“警报数据暂不可用”。

缓存中的空选择在下一次请求失败时不再代表当前确认无警报。被隐藏的过期可选字段不
拖累其他新数据；任何实际画到页面上的 stale provider 值都必须保留自己的观测时间。

mandatory core 定义为：与当天选择键一致且媒体可解码的 APOD、scales、Kp。日期未变
时 APOD 可来自匹配缓存；每次到期 DATA_REFRESH 的 scales 与 Kp 必须在本轮都得到
可归一化、语义有效的成功响应。合法空 alerts 是可选源成功，不是 core 失败。风速、
磁场、alerts、DONKI 和翻译是可选源；失败时使用 fresh 匹配缓存，否则固定位置显示
`—`/不可用或隐藏，不得用 stale 值把新候选包装成 live。

聚合 provenance 只使用现有四值，并对页面实际展示的 provider 值取最差状态：

- APOD 为当天选择键匹配缓存、scales/Kp 本轮 live，且其余已显示数据为
  live/fresh cache：`LIVE`；
- 全部已显示数据为 fresh cache：`FRESH_CACHE`；
- 任一实际显示值为 stale：`STALE_CACHE`；
- 没有 provider 值、只有结构化不可用壳：`LOCAL_FALLBACK`。

只有 `LIVE/FRESH_CACHE` 且通过本节 admission 的候选才允许覆盖 canonical 实例 PNG。
对本插件的到期 DATA_REFRESH，还必须满足本轮 scales 与 Kp 均成功，所以正常提升结果
应为 `LIVE`。`STALE_CACHE/LOCAL_FALLBACK` 或 core 失败必须显式设置
`inkypi_skip_cache`，或在返回图片前抛出受控异常；仅附加 provenance 不能阻止现有
runtime 先提升 PNG。首次没有 canonical PNG 且 core 失败时跳过 NASAPics，直到首个
健康缓存生成；首版不扩展全局 runtime 去支持“可显示但不算成功”的启动壳。

## 10. 30 分钟数据缓存、但不自行刷屏

本插件明确不使用 `RefreshOnDisplayPresentationMixin`。当前运行时中的 presentation
流程会先消费旧缓存，再准备新图并可能排入第二个 DISPLAY；这会在同一轮播目标上
造成两次物理写屏，不符合本设计。

生产实例使用普通 refresh cadence `{"interval": 1800}`：

1. DATA_REFRESH 读取设备时区并检查 APOD 日选择键。
2. 日期未变且 APOD 不是 provisional 时复用当天 payload 和磁盘媒体；日期变化时获取
   当天 APOD、媒体和标题翻译。provisional 媒体只按第 8 节重试当天图片。
3. 每次到期 DATA_REFRESH 都实际请求 scales 与 Kp，并独立请求/合并可选源。分源成功
   缓存即使本轮整体失败也保留，供下一次重试使用。
4. 只有 APOD admission 通过且本轮 scales/Kp 都成功，才生成并提升 canonical
   800×480 PNG、推进 `data.last_success_at`。任一 core 不满足时不提升 PNG、不推进
   成功时间，进入现有重试/退避；这使 lane 时钟不会晚于最老的未更新 core。
5. 在系统健康、非退避、无 restart request 且资源压力不是 HARD 的自动轮播路径中，
   到期实例按 `DATA_REFRESH -> DISPLAY_CACHE` 运行，并只产生一次物理写屏。
6. HARD 资源压力或 restart request 可按现有 runtime 直接显示已有 last-good；数据
   失败/退避时释放该候选并跳过 NASAPics，等待后续重试。这些都是首版不改全局
   RefreshTask 的明确例外。
7. 其他普通 background DATA_REFRESH 即使提前更新了 PNG，也只写离屏缓存。运行时
   只有 `CommandKind.DISPLAY` 才调用物理 `_display_image`。

manifest 使用准确层级：

```json
{
  "refresh_on_display": false,
  "capabilities": {
    "supports_presentation_refresh": false,
    "supports_live_refresh": false,
    "supports_day_night_theme": false
  }
}
```

插件不实现活跃 `get_live_refresh_state()`，不进入 live lane。关闭昼夜主题能力后，
日出/日落也不会为 NASAPics 产生 THEME_REDRAW + DISPLAY follow-up。

实例设置 `refreshOnDisplay` 的优先级高于 manifest，因此部署迁移必须确认目标实例中
该键不存在或严格为 `false`；设置页也不得把它写回 true。用户点击普通 `Display Now`
时只显示最新 canonical 成功缓存且只写屏一次，不额外访问 provider；若尚无 canonical
缓存则明确失败，不生成启动壳。若用户需要立即取新数据，应先执行一次只更新缓存的
DATA_REFRESH。严格的“Display Now 到期则先刷新再单次显示”复合路由不在首版范围内。

## 11. 故障处理

| 故障 | 行为 |
| --- | --- |
| NASA APOD 请求失败 | 复用当天匹配缓存；无当天缓存时不提升候选，保留既有 canonical 页面并记录失败 |
| 当天 APOD 为视频 | 向前查找最近 7 天图片，显示真实日期 |
| 图片下载/解码失败 | 不覆盖有效媒体或当前最终 PNG |
| scales 或 Kp 本轮失败 | 保留各分源成功缓存，但不提升最终 PNG、不推进成功时间并进入重试/退避 |
| 可选 NOAA 端点失败 | 复用该端点 fresh last-good；过期则固定单元格显示 `—`/不可用 |
| NOAA 合法空警报 | 清除旧警报并显示“暂无显著空间天气警报” |
| NOAA 警报源失败且无 fresh 缓存 | 显示“警报数据暂不可用”，不能显示确认无警报 |
| 所有核心天气不可用 | 不替换最终 PNG并进入重试/退避；首次无缓存时跳过 NASAPics 直到首个健康缓存 |
| DONKI 失败 | 隐藏补充事件，不影响 NOAA 页面成功 |
| 中文翻译失败 | 使用同标题缓存，否则显示英文和明确不可用文字 |
| cache JSON 损坏 | 隔离损坏文件并重新请求；不删除其他 last-good 组件 |
| 非预期 theme/presentation/live 调用 | manifest 能力关闭；测试保证不会排额外 DISPLAY |

所有 HTTP 请求使用共享 session、显式超时和现有取消预算。日志记录端点类别、状态、
耗时和 fallback，不记录 NASA/OpenAI/Groq 密钥、完整授权头或带密钥 URL。

## 12. 代码边界

建议窄范围拆分：

- `plugins/apod/apod.py`：可信 instance identity namespace、每日选择、普通 DATA_REFRESH
  聚合 orchestration、context。
- `plugins/apod/space_weather.py`：NOAA/DONKI 请求、归一化、警报有效性和分源缓存。
- `plugins/apod/apod_page.py`：固定几何、字体测量、照片裁切和 PIL 绘制。
- `plugins/apod/plugin-info.json`：显式关闭 presentation/live/refresh-on-display/theme。
- `plugins/apod/settings.html`：保留现有日期选项，不新增易丢失的隐藏刷新字段。
- `tests/test_apod.py`：新增插件专用数据、缓存、布局和普通 data-refresh 测试。
- `tests/test_refresh_task.py`：只补 NASAPics 的 cadence、单次 DISPLAY、skip-cache 和
  instance-setting precedence 回归；不改全局调度语义。

复用 `BasePlugin.cache_dir()` 后必须再加第 9 节的 instance namespace；同时复用 managed
media namespace、`utils.atomic_file` / `utils.plugin_cache`、`render_provenance` 和现有
DATA_REFRESH 原子缓存路径。不得为本插件复制一套全局刷新队列，也不得顺带改写
DailyWiki。

## 13. 测试驱动要求

实施按 TDD 进行，至少覆盖：

### 13.1 APOD 与翻译

- 生产默认请求当天 APOD，重复展示不重新选择或下载图片。
- 随机兼容模式同一自然日固定一张，次日才重抽。
- 手动强制刷新不重抽；自选日期变化会使选择键失效。
- 空 `customDate` 在设置载入和再次保存后仍为空，不能被当天日期意外固化。
- 视频回退显示真实 payload 日期，图片、标题、日期和版权来自同一响应。
- 图片下载/解码造成的 provisional 回退会按 cadence 重试当天媒体，成功后只切换一次；
  视频回退不会重复下载同一历史图。
- 标准 URL 优先；大图不会先解码为整屏再二次裁窄。
- 中文翻译一天最多成功请求一次，不跨英文标题复用。
- API 错误与日志均不泄露密钥。

### 13.2 NOAA 与缓存

- scales 的 `-1/0/1/2/3` 和 UTC 日期正确归一化；未来预测日不会被 +5 分钟检查误杀。
- scales current/forecast 同一响应原子写入一个文件；key `0` 冻结时 HTTP 200 不会
  永久续鲜，概率取最早有效预测日且只接受 0..100。
- Kp 对象数组与旧二维数组都能解析；在所有非 predicted 行中按时间选最新，旧
  estimated 不会压过更新 observed。
- Kp 当前三小时时段、30 分钟发布宽限及 `now < predicted <= now+48h` 边界均正确。
- `kp=4.67` 等边界不自行四舍五入推导 G。
- wind/mag 按最新 `time_tag` 选择，时间差过大时分别标记；上游冻结不会因新抓取时间
  被误判为新观测。
- R1–R2、R3–R5、S1+ 概率字段映射正确。
- 警报按 `issue_datetime` UTC 升序折叠，并用 serial 引用处理取消、替代、延期和
  supersedes；`valid_until` 可空，未知消息不猜测，Synoptic Period 不会把刚发布
  ALERT 立即判过期。
- `active`（live/fresh-cache）、`confirmed_empty` 和 `unavailable` 三态及文案严格区分。
- DONKI 的 FLR 1 天/CME 7 天查询窗、M5+/X、嵌套 most-accurate ENLIL、明确 Earth
  impact、`-6h..+72h` 到达窗、多事件唯一选择和失败隐藏均有固定 fixture 测试。
- 单源成功原子替换，单源失败保留 last-good，不覆盖其他成功组件。
- 两个 APOD 实例以当天/随机/自选模式交错刷新时，source、translation、aggregate 和
  selection 状态严格按 UUID+generation 隔离；相同图片只允许 media blob 去重。
- 缺少可信 instance identity 的生产刷新 fail closed；generation 变化不会误读旧实例
  状态，settings revision 变化则通过 selection fingerprint 精确失效。
- cache age、provider data age/validity、fresh/stale/unavailable 和四值 aggregate
  provenance 符合第 9 节。
- 任一显示 stale 值得到 `STALE_CACHE`；没有 provider 值得到 `LOCAL_FALLBACK`，二者
  都显式 skip-cache。
- disabled theme/presentation/live 路径不产生 NASA、NOAA、DONKI 或翻译请求。

### 13.3 布局与图片

- 输出严格为 RGB 800×480。
- header、info 和 Kp 矩形始终固定；短题注使用照片 `(368,65,800,364)` 与题注
  `(368,364,800,480)`，长题注只允许共享边界在 `y=300..364` 内移动。
- 长英文、长中文、无中文、无版权和字段缺失时 Kp 矩形不变。
- 宽图、竖图、EXIF 旋转图都以正确方向居中 cover，照片四边无空白或黑边。
- 中英题注始终在右图下方；标题变化不移动左侧字段。
- 所有左侧文字 bbox 不越过 `x=354`，关键字号不低于规定下限。
- 缺失指标显示 `—` 且 cell 坐标不变。
- 分别测试 116px 与 180px 题注端点，并直接捕获 `_draw_kp_panel` 的参数恒为
  `(20,77,354,180)`。
- 300 字符英文、60 字符中文及其组合经过真实字体测量后要么完整显示、要么整张候选
  拒绝；不得省略、越界或挤动左侧。
- 无黑边测试使用哨兵色合成宽图/竖图，不能把真实太空照片中的黑色误判为 letterbox。
- 断言短题注时 `y=363` 属于照片、`y=364` 属于题注；最大题注时 `y=299`
  属于照片、`y=300` 属于题注；`x=367` 属于左分隔线、`x=368` 属于右侧内容。

### 13.4 刷新语义

- manifest 明确关闭 refresh-on-display、presentation、live 和 day/night theme。
- 测试 manifest 顶层/`capabilities` 层级，并覆盖实例 `refreshOnDisplay` 对 manifest 的
  优先级；目标实例该键不存在或为 false。
- cadence 为 1800 秒；到期 DATA_REFRESH 更新天气但不重新下载已验证的当天 APOD，
  provisional 当天媒体重试除外。
- 健康、非退避、非重启、非 HARD 路径严格按 `DATA_REFRESH -> DISPLAY_CACHE`；HARD/
  restart 展示 last-good，core 失败/退避则跳过该实例。
- background DATA_REFRESH 只能更新 PNG 缓存，不能调用物理 display。
- mandatory core 本轮任一失败时，已成功分源缓存保留，但 canonical PNG 的哈希、mtime
  和 `data.last_success_at` 三者都不变。
- stale/local fallback 即使携带 provenance 也必须显式 skip-cache；首次失败且无
  canonical PNG 时不产生可显示启动壳。
- DISPLAY_CACHE 阶段 provider-free，且每次轮播只产生一次物理写屏。
- 日出/日落不会为 NASAPics 排 THEME_REDRAW 或 DISPLAY follow-up。
- Display Now 只显示最新成功缓存，不访问 provider、不触发第二次显示；无缓存时失败。

运行新增 APOD 测试、普通 DATA_REFRESH/Display 边界测试、manifest 能力回归、网络失败
测试，然后运行完整 InkyPi 套件与 `git diff --check`。

## 14. 生产迁移与实机验收

部署时执行一次精确、可审计的目标实例迁移，而不是按 `plugin_id == "apod"` 做全局
启动迁移：

1. 读取 `DailyDoseOfDay / apod / NASAPics` 的完整实例记录，核对 instance UUID、父播放
   列表和 revision/generation；若身份或版本不匹配则停止，不能按名称模糊写入。
2. 基于读取到的完整 settings 做 merge/CAS 原子更新，只改
   `randomizeApod=false`、`customDate=""`、`refreshOnDisplay=false` 和 cadence
   `interval=1800`；保留未知字段。
3. 回读目标记录并比较 revision，同时确认其他 APOD 实例的持久 settings/cadence 和
   播放列表顺序完全不变。插件级 manifest 能力和随机模式“每日锁定”代码会作用于所有
   APOD 实例，但不批量改写它们的持久配置。
4. 触发一次只更新离屏缓存的 DATA_REFRESH，确认健康 canonical PNG 后再由正常轮播
   显示；不要用 Display Now 暗中附带 provider 请求。

目标结果：

- 名称继续使用 `NASAPics`；
- `randomizeApod=false`；
- `customDate` 清空；
- refresh cadence 从 `scheduled: "00:00"` 改为 `interval: 1800`；
- 实例 `refreshOnDisplay=false`，且 manifest 的 `refresh_on_display=false` 生效；
- 不修改其他 APOD 实例的持久 settings/cadence 或任何播放列表顺序。

在 `ColoredEpaperFrame` 上必须证明：

1. 页面显示设备当天或明确标记的最近可用 APOD，右图无黑边。
2. 图片下方同时有中文标题、英文原题、真实日期和必要版权。
3. 左侧字段包含 Kp、G/R/S、太阳风、Bz、Bt、48 小时峰值、概率和有效预警。
4. Kp 区域与批准稿位置一致，满框图片改动未移动它。
5. 同一天所有数据刷新使用同一已验证 APOD，不重复下载大图；provisional 回退最多在
   当天图恢复后切换一次；天气按 1800 秒 cadence 更新。
6. 健康路径的自动轮播遇到到期实例时先更新缓存再单次显示；HARD/restart 使用
   last-good，失败/退避跳过；其他 DATA_REFRESH 即使更新 PNG，物理屏也不会因
   NASAPics 自行变化。
7. 断网或单源失败保留最后成功页面/组件，并显示真实数据时间。
8. 实际插件 PNG 的 mtime、哈希、HTTP `Last-Modified` 与实例成功时间一致；失败
   不会只更新 UI 时间而留下旧 PNG。
9. 检查生成图、当前显示图、服务日志和队列终态；一次 HTTP 200 不算视觉验收。
10. 服务保持 ready，无意外重启、内存峰值或重复大图下载。

## 15. 外部参考

- NASA APOD API：<https://api.nasa.gov/>
- NASA 图片与署名指南：<https://www.nasa.gov/nasa-brand-center/images-and-media/>
- NOAA SWPC 产品目录：<https://www.spaceweather.gov/products-and-data>
- NOAA scales JSON：<https://services.swpc.noaa.gov/products/noaa-scales.json>
- NOAA Kp forecast JSON：<https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json>
- NOAA solar-wind speed：<https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json>
- NOAA magnetic field：<https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json>
- NOAA alerts：<https://services.swpc.noaa.gov/products/alerts.json>
- NASA DONKI API：<https://ccmc.gsfc.nasa.gov/tools/DONKI/>
- NASA DONKI FLR：<https://api.nasa.gov/DONKI/FLR>
- NASA DONKI CME：<https://api.nasa.gov/DONKI/CME>
