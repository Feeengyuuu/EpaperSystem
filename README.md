<div align="center">

<img src="inkypi-weather/package/InkyPi/src/static/icons/logo.svg" width="88" alt="ColoredEpaper Frame">

# EpaperSystem · ColoredEpaper Frame

**把天气、日程、赛事、新闻和你在意的一切，装进一块会自己换页的彩色墨水屏。**

Raspberry Pi + 六色墨水屏 · 69 个插件自动轮播 · 局域网网页管理 · **一行命令安装**

</div>

![设备在 2026 年 10 月的实机画面：体育看板、现代天气、轨道信号、今日热榜、技术圈与 AI 脉搏、环球此刻、LiveRadar 直播雷达、Steam 每日美图、全球票房双城榜、NASA 天文图、回到那天、每日名画。](docs/images/epaper-system-latest-screens.jpg)

<p align="center"><sub>以上都是设备刚刚刷新的实机画面（800 × 480，六色墨水屏），个人信息已打码。</sub></p>

## 它会显示什么

| 主题 | 内容 |
|---|---|
| 🌤 **天气与日历** | 随天气和昼夜换插画的现代天气页；节假日、个人日程与发布会日历 |
| ⚽ **体育与电竞** | 足球、NFL、MLB、NBA / WNBA、F1、PGA 各有专属版式，直播优先；LoL / CS2 / Dota 赛事与赔率 |
| 📰 **资讯与科技** | AI 摘要新闻、Hacker News 与 AI 榜单、Telegram 精选、今日热榜（B 站 / 新番 / 维基 / 热歌 / 棋题 / NASA 地球） |
| 🎮 **游戏与影视** | Steam 资料与好友、Steam 排行与每日美图、游戏优惠、直播雷达、全球票房双城榜 |
| 🚀 **太空与天空** | NASA 每日天文图、火箭发射倒计时与预测市场、卫星云图与空间站、机场航班雷达 |
| 🎨 **艺术与知识** | 每日名画、历史海报、杂志与漫画封面、维基每日、每日一词 |
| 📍 **生活与本地** | 湾区预警、路况摄像头、出行与潮汐、本地活动、3D 打印监控、车辆状态、理财看板 |

多数画面用官方 logo 标明数据来源；入门的几页不需要 API Key，其余按需在网页里按服务填写。

## 为什么好用

- **自己会换页**：按播放列表自动轮播，白天 / 夜间主题自动切换，天气等页面在上屏前即时刷新。
- **装好就有内容**：新设备首次启动自带 9 页无需 API Key、也不依赖位置的入门轮播。
- **网页管理**：局域网打开即可管理插件和播放列表，首页实时显示当前画面，iPhone 可加到主屏幕。
- **稳定**：数据源不可用时先显示缓存再自动重试；断网自恢复；更新失败会回到上一版本。

## 一行命令安装

用 Raspberry Pi Imager 写入 **Raspberry Pi OS Lite**，设好 Wi-Fi 和 SSH，登录后粘贴：

```bash
curl -fsSL https://raw.githubusercontent.com/Feeengyuuu/EpaperSystem/main/install.sh | sudo bash -s -- --lang zh-CN
```

按提示选屏幕（默认 Waveshare 7.3 英寸六色屏 `epd7in3e`，也支持其他 Waveshare 与 Pimoroni Inky），装完打开终端给出的网址、用一次性配对码设好密码，就完成了。

- **再跑一次就是更新**：同一条命令会更新到最新版，配置、轮播和密钥都保留。
- 想从空播放列表开始，在命令末尾加 `--no-starter`；English installer: `--lang en`。

<details>
<summary>手动安装、文档与开发</summary>

```bash
git clone --depth 1 https://github.com/Feeengyuuu/EpaperSystem.git
cd EpaperSystem
bash install.sh --lang zh-CN --check   # 只做环境检查
sudo bash install.sh --lang zh-CN
```

- [从零安装](inkypi-weather/package/InkyPi/docs/install_from_zero.zh-CN.md) · [Installation guide](inkypi-weather/package/InkyPi/docs/install_from_zero.md) · [API Key 获取](inkypi-weather/package/InkyPi/docs/api_keys.zh-CN.md) · [API keys](inkypi-weather/package/InkyPi/docs/api_keys.md)
- [开发与本地测试](docs/development.md) · [编写插件](inkypi-weather/package/InkyPi/docs/building_plugins.md)
- 应用、插件和测试在 `inkypi-weather/package/InkyPi`（Python 3.11，六千多项测试用模拟硬件运行），手机简报页 Worker 在 `cloudflare/`。

</details>

## 致谢与许可

基于开源项目 [InkyPi](https://github.com/fatihak/InkyPi) 构建，感谢原作者与社区。InkyPi 部分以 **GPL-3.0** 发布，见 [LICENSE](inkypi-weather/package/InkyPi/LICENSE)。

<sub>An open-source Raspberry Pi colour e-paper dashboard: 69 plugins for weather, calendars, sports, news, games, space and local life, auto-rotating with a web UI. Install with the one command above (`--lang en` for English).</sub>
