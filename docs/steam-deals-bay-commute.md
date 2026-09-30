# Steam 游戏优惠与湾区出行

这两个插件面向 800×480 彩色墨水屏，以 Pillow 绘图并沿用现有日夜主题、资源调度和轮播。日常更新无需浏览器或图片生成服务。设计参考先由内置图片生成工具生成，再以代码实现；参考图中的示例数据不进入运行数据。

## 游戏优惠（game_deals）

- 默认只查询 CheapShark 的 Steam 商店（`storeID=1`），展示美国区美元报价。
- 双列三行，最多同时展示六款不同游戏。每款显示封面、游戏名、现价、原价和折扣。
- 推荐实例刷新周期为 7200 秒。封面采用有大小上限的本地缩略图缓存。
- 当前促销没有可用条目时显示空状态；取数失败保留带原始更新时间的旧数据，并明确标记过期。超过可展示期限的价格不继续冒充当前报价。
- 轮播与日夜重绘只读取本地缓存。数据刷新周期与屏幕显示周期分别管理，资源压力可能延后任务。
- 不把未查询的历史最低价、其他商店价格或游戏封面上的文字当作 Steam 报价。

来源：[CheapShark 官方 API 文档](https://www.postman.com/cheapshark/cheapshark-s-public-workspace/documentation/7h22uhl/cheapshark-api)。请求携带应用 User-Agent，并遵守共享 HTTP 客户端的有界请求及退避。购买跳转如被使用，应采用 CheapShark 官方 redirect URL，程序不访问跳转页。

## 湾区出行（bay_commute）

- 道路默认关注 I-880、I-680、SR-84，使用 Caltrans District 4 的公开 CSV；每小时获取一次。
- 道路部分区分已确认进行的封闭、当前计划和未来计划，优先保留不同关注路线的条目。已结束或取消的项目不再显示。
- 潮汐默认采用 Redwood City（NOAA 站号 `9414523`），以美国太平洋当地日期每天获取一次今天及明天的预测。高潮、低潮与曲线分别来自 NOAA 对应产品。
- 潮汐为预测，单位英尺、基准 MLLW；不是实时观测水位。跨日时间带日期说明。
- 道路与潮汐分别缓存、分别显示更新时间。一个来源失败不抹去另一个来源的可用数据。
- 页面每小时更新时可重算下一次高潮和低潮，无需再次请求当天已有的预测。轮播时也能只从缓存重绘。
- 路线、潮汐站编号和站名可通过设置调整。潮汐站需支持连续预测；只有高潮和低潮数据的站点不能提供真实完整曲线。
- 左侧静态区域地图使用随插件保存的 USGS 底图。编号位置来自 Caltrans 经纬度，与道路条目对应；无有效坐标或超出地图范围的项目不会被伪造到图内。Pi 日常无需请求地图服务。
- I-680、I-880、SR-84 采用按标准图纸制作的高清透明盾牌，原始 SVG、PNG、来源与许可记录随插件保存。没有继续使用手绘近似盾牌。

图形来源：[I-680](https://commons.wikimedia.org/wiki/File:I-680.svg)、[I-880](https://commons.wikimedia.org/wiki/File:I-880.svg)、[California 84](https://commons.wikimedia.org/wiki/File:California_84.svg)、[USGS National Map](https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer)。

来源：[Caltrans LCS 文档](https://cwwp2.dot.ca.gov/documentation/lcs/lcs.htm)、[NOAA CO-OPS API](https://api.tidesandcurrents.noaa.gov/api/prod/)。Caltrans 来源更新比页面周期更频繁，页面用于施工及封闭计划查看，不代表完整实时拥堵或导航。

## 实例设置

将两个实例加入已有播放列表，分别使用 2 小时和 1 小时的间隔，主题选自动。保持“轮播时刷新数据”关闭。NOAA 的每日周期由组合插件独立控制。

部署需验证来源数据时间、两种主题、缓存失效及跨日行为，然后分别完成真实刷新和写屏，核对 `hardware_written`、显示提交和当前图像。新增实例前后须验证其他实例配置未变。
