# EpaperSystem

![EpaperSystem 彩色墨水屏信息站：天气、日历、体育、新闻与科技、游戏与影视、艺术与知识、太空与天文、生活与设备；支持自动轮播、展示前天气更新、本地图片缓存和 Web 管理。](docs/images/epaper-system-overview.png)

EpaperSystem 是基于 **Raspberry Pi + 彩色墨水屏** 的开源信息站。把天气、日程、赛事和关注的内容集中在一块屏幕上，通过局域网 Web 界面配置页面与轮播。

An open-source Raspberry Pi e-paper dashboard for weather, calendars, sports, news, culture and connected devices, with a web interface and configurable playlists.

介绍图为功能与硬件示意；下方的截图入口提供程序样例和历史实机画面。部分内容需要配置第三方服务或 API Key。

## 内容与使用

- **天气与日历**：天气在展示前重新取数；日历整合节日、个人日程、苹果与游戏发布会，按空间补充后续事项。
- **体育与资讯**：直播优先的体育分区、同赛事后续赛程、赛事标识与队徽缓存，以及新闻、AI 动态、Telegram 摘要和直播雷达。
- **游戏、影视与文化**：Steam 动态、电影海报、漫画、杂志、每日艺术、历史今天、词句与百科。
- **太空与设备**：NASA 影像、空间天气、轨道信息、行情、Bambu 打印机与车辆状态等内容，按对应服务配置启用。
- **日常运行**：页面轮播、主题切换、图片本地缓存、健康检查、异常重试与发布版本回退。天气之外的内容按各自刷新策略更新，数据及时性取决于来源与连接状态。

## 开始使用

先用 Raspberry Pi Imager 写入 Raspberry Pi OS Lite（优先 64 位），设置好 Wi-Fi、用户名和 SSH。登录树莓派后，粘贴这一条命令：

```bash
curl -fsSL https://raw.githubusercontent.com/Feeengyuuu/EpaperSystem/main/install.sh | sudo bash -s -- --lang zh-CN
```

按提示选择屏幕即可。默认是 **Waveshare 7.3 英寸彩色墨水屏，800 × 480，驱动 `epd7in3e`**，也可选择其他内置 Waveshare 驱动或 Pimoroni Inky。API Key 可以全部跳过，稍后按需要添加。

安装完成后，按终端显示的局域网地址打开网页，使用一次性配对码设置管理员密码，再选择插件、填写自己的位置与内容、创建轮播。首次安装请按提示重启一次。脚本会检查环境、安装依赖并等待服务启动；已有设备重新运行时保留个人配置和密钥。

想先查看脚本或只做环境检查，可手动下载：

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Feeengyuuu/EpaperSystem.git
cd EpaperSystem
bash install.sh --lang zh-CN --check
sudo bash install.sh --lang zh-CN
```

- [从零安装（简体中文）](inkypi-weather/package/InkyPi/docs/install_from_zero.zh-CN.md) · [Installation guide (English)](inkypi-weather/package/InkyPi/docs/install_from_zero.md)
- [API Key 获取与配置](inkypi-weather/package/InkyPi/docs/api_keys.zh-CN.md) · [API keys (English)](inkypi-weather/package/InkyPi/docs/api_keys.md)
- [开发与本地测试](docs/development.md) · [编写插件](inkypi-weather/package/InkyPi/docs/building_plugins.md)

## 界面截图

- [体育页面：公开样例渲染](inkypi-weather/package/InkyPi/docs/images/readme/screens/actual-sports-dashboard-800x480.png)
- [历史实机截图合集](inkypi-weather/package/InkyPi/docs/images/readme/epaper-system-real-screens.png)
- [插件展示墙](inkypi-weather/package/InkyPi/docs/images/readme/epaper-system-plugin-wall.png)

## 开源基础与许可

EpaperSystem 建立在开源 [InkyPi](https://github.com/fatihak/InkyPi) 之上，感谢原项目维护者和社区提供的应用、插件架构与安装体系。可运行的应用位于 `inkypi-weather/package/InkyPi`。

The InkyPi package is distributed under **GPL-3.0**. See [LICENSE](inkypi-weather/package/InkyPi/LICENSE).
