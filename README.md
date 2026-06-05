# EpaperSystem

![EpaperSystem hero](inkypi-weather/package/InkyPi/docs/images/readme/epaper-system-hero.png)

Free open-source Raspberry Pi e-paper dashboard based on InkyPi, packaged with
a beginner installer, plugin bundle, API key helper, Chinese install flow, and
health checks.

简体中文：这是一个可开源发布的树莓派墨水屏信息台项目，内置插件系统、一键安装脚本、API Key 配置助手、中文安装流程和健康检查。

## Built On InkyPi

EpaperSystem is built on top of the open-source
[InkyPi](https://github.com/fatihak/InkyPi) project. Thanks to the InkyPi
maintainers and community; this project would not exist without that foundation.

简体中文：EpaperSystem 的一切都建立在开源
[InkyPi](https://github.com/fatihak/InkyPi) 项目的基础上。感谢 InkyPi
维护者和社区提供的基础工程、插件架构和安装体系。

The runnable app lives here:

```text
inkypi-weather/package/InkyPi
```

README screen content is captured from the real `ColoredEpaperFrame` device.
img-2 was used only for the desk/device scene and empty display frames.

![Plugin wall](inkypi-weather/package/InkyPi/docs/images/readme/epaper-system-plugin-wall.png)

![Actual captures](inkypi-weather/package/InkyPi/docs/images/readme/epaper-system-real-screens.png)

## Quick Install

On a fresh Raspberry Pi:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/Feeengyuuu/EpaperSystem.git
cd EpaperSystem/inkypi-weather/package/InkyPi
sudo bash install/bootstrap.sh
```

简体中文安装流程：

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/Feeengyuuu/EpaperSystem.git
cd EpaperSystem/inkypi-weather/package/InkyPi
sudo bash install/bootstrap.sh --lang zh-CN
```

The default installer targets a Waveshare 7.3 inch color e-paper display using
driver `epd7in3e`.

Other display examples:

```bash
sudo bash install/bootstrap.sh -W epd7in5_V2
sudo bash install/bootstrap.sh --pimoroni
```

Full guides:

- English: [Install From Zero](inkypi-weather/package/InkyPi/docs/install_from_zero.md)
- 简体中文：[从零安装](inkypi-weather/package/InkyPi/docs/install_from_zero.zh-CN.md)

## API Keys

API keys are optional. You can add them during install, in the web UI, or later
from the command line.

```bash
cd inkypi-weather/package/InkyPi
python3 install/configure_api_keys.py --list
python3 install/configure_api_keys.py --list --lang zh-CN
python3 install/configure_api_keys.py --env-file .env
```

Web UI after install:

```text
http://<your-pi>/api-keys
```

Key guides:

- English: [API Keys](inkypi-weather/package/InkyPi/docs/api_keys.md)
- 简体中文：[API Key 获取地址](inkypi-weather/package/InkyPi/docs/api_keys.zh-CN.md)

## Health Check

```bash
cd inkypi-weather/package/InkyPi
bash install/healthcheck.sh
bash install/healthcheck.sh --lang zh-CN
```

## Before Publishing

Do not publish local secrets or runtime state. This repo ignores `.env`,
`.ssh/`, `.secrets-backup/`, `.tmp/`, `tmp/`, caches, and Python bytecode, but
you should still verify Git history before making a public repository.

Checklist: [Open Source Release Checklist](docs/open_source_release_checklist.md)

## License

The InkyPi package is distributed under GPL-3.0. See
[LICENSE](inkypi-weather/package/InkyPi/LICENSE).
