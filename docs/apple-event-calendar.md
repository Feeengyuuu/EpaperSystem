# 苹果发布会自动日历

`simple_calendar` 的“自动同步苹果发布会”开关会把苹果正式公布的发布会加入现有月历。与游戏发布会使用同一套持久化检查机制，苹果开关和状态文件独立，个人日程、节假日及游戏类别保持各自设置。

## 来源与范围

两个官方来源，每次检查都重新读取：

- [Apple Events](https://www.apple.com/apple-events/)：重新发现官方“添加到日历”的 ICS 地址，不固定某一年的资产路径或发布日期。秋季等 Apple Event 从发邀请到开场期间页面会提供这个 ICS（2026-08-28 的存档页即为 `/v/apple-events/home/ak/built/assets/event/event.ics`，`SUMMARY:Apple Event`，2026-09-09 10:00 PT）；活动结束后链接消失。
- [Apple Newsroom](https://www.apple.com/newsroom/rss-feed.rss)：WWDC 前活动页一直停留在上一场活动、没有 ICS，直到主题演讲当天才更新，因此 WWDC 主题演讲从 Newsroom 公告读取。只跟进标题提到 WWDC / Worldwide Developers Conference / Apple Event / Keynote 的美国区稿件；正文里单独介绍 Keynote 的日程段落（如 “June 8, 10 a.m. PDT … The Keynote will be available to stream”）给出日期和时刻，只写 “kicks off with the Keynote … on Monday, June 8” 的稿件只给日期。Platforms State of the Union 的时刻不会被当成主题演讲时刻。发布日期在主题演讲当天或之后的稿件（播后报道）不会生成日程；“复制全文”组件的文字不参与解析。已发现的未来稿件在离开 20 条的 RSS 后继续按链接复查，每轮最多 6 篇。

接收 Apple Event 与明确的 WWDC keynote 日程，排除历史页面日期、播后回顾、ASL 重播、开发者课程、整场 WWDC 大会日期和商品预购日期。官网未提供正式日程时不推算日期。苹果网页大量使用不换行空格，匹配前统一规范化。

ICS 的 UID 决定活动身份，SEQUENCE 和带时区的 LAST-MODIFIED 用于处理修订；资源地址变更不会重复添加，同一个入口的新 UID 则保留为另一场活动。Newsroom 稿件没有 UID，以稿件 URL 区分；同一场主题演讲的多篇稿件（3 月只有日期、5 月补充时刻）合并成一条，带时刻的一篇优先。Apple 当前文件的浮动 DTSTAMP 不作为公告更新时间。明确取消的活动退出正常日历；日期确定而时刻未定时标注“时间待定”，浮动的具体时刻不猜测时区。

活动时间保存为 UTC，并按设备时区放入对应的日、月。来源公告日期保留原时区语义，不使用网页发布日期当作活动日期。

## 检查与缓存

- `showAppleEvents` 默认 `false`，在目标日历实例启用；不依赖 `showGameEvents`。
- 本次设备通过一次配置迁移启用已核验的 `DailyDoseOfDay / Date`：严格匹配实例身份、结构版本 1、设置版本 2 及每小时周期，仅在该字段不存在时写入 `true`。配置与迁移标记通过同一个 CAS 事务提交；其他设备、已有明确开关值及后续用户修改均不会被覆盖。
- 源正常每三小时检查一次，尝试前原子保存下次检查时间。失败、重复 DATA 刷新和进程重启都服从这个间隔；调度排队和退避可能延迟实际检查。
- 启用时 DATA 周期规范为每小时；已有更快、能整除一小时的周期保留。
- 访问失败、官方页面暂时没有日历链接或已知未来日程超过核验周期时，保留已知日程并标注“待核验”。页面上消失不等于取消。
- 轮播、主题重绘和设置页只读缓存，不访问苹果网站、不写状态。
- 苹果来源只允许 `apple.com` 与 `www.apple.com` 的 HTTPS 地址，跳转也要通过校验。每轮一个活动页入口、最多四个日历链接、一个 Newsroom RSS 和最多六篇稿件；沿用 4 MiB 响应、90 秒网络期限和 2 MiB 状态上限。活动页和 Newsroom 互不影响：一方失败只记录错误，另一方的日程照常更新。

状态位置：

```text
$INKYPI_DATA_DIR/plugins/simple_calendar/apple_events/state.json
```

默认数据根目录为 `/var/lib/inkypi/data`。游戏状态仍在 `game_events/state.json`，回滚旧版本不需要让旧游戏解析器读取苹果数据。

## 验证范围

自动测试覆盖动态 URL、UID 去重及新活动、时区跨日跨月、日期待定、修订回退、取消、独立开关和存储、重启后的三小时间隔、失败后恢复，以及个人日程和游戏活动共存。Newsroom 部分覆盖 WWDC 先有日期后补时刻、稿件离开 RSS 后的复查、播后报道不入历、复制全文组件不参与解析，以及 Newsroom 故障不影响活动页日程。真实渲染、主题展示与设置页的缓存读取也有集成测试。网站结构变化可能需要维护解析器。

2026-10-19 排查记录：2026-09-09 的 Apple Event 当时已被设备抓到（9/8 设备缓存的日历在 9 月 9 日有 GAME/APPLE），但那一版（3c3f06ac）议程最多三行，并把同一天的活动合并成一行；合并标签 “GAME/APPLE” 按固定宽度排版，压进了后面的时刻和标题，苹果发布会不易辨认。之后的议程改为每个活动单独一行并测量标签宽度，按设备设置重新渲染 9/8、9/9 时苹果发布会都完整可见。
