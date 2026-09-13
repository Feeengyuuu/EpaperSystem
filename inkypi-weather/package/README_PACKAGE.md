# InkyPi package

This directory contains the EpaperSystem application. The default screen is Waveshare 7.3-inch colour HAT E (`epd7in3e`, 800 × 480). Other packaged Waveshare drivers and Pimoroni Inky can be selected during installation.

## Raspberry Pi installation

Prepare Raspberry Pi OS and SSH using the [English setup guide](InkyPi/docs/install_from_zero.md) or [中文从零安装指南](InkyPi/docs/install_from_zero.zh-CN.md). After copying this package to the Pi, run from this directory:

```bash
bash install_on_pi.sh --lang zh-CN
```

This uses the same beginner wizard as the root installer. API keys are optional; no personal credentials or preconfigured `.env` are required.

Open the printed LAN address, pair at `/auth/setup`, and set your administrator password. Add your own pages and playlists. Optional keys go in `/api-keys`; the installed runtime stores them in `/etc/inkypi/inkypi.env`.

```bash
sudo bash /opt/inkypi/current/install/healthcheck.sh --wait 120
sudo systemctl status inkypi
sudo journalctl -u inkypi -n 120 --no-pager
```

For weather without a key, choose Open-Meteo. For other providers see the [API key guide](InkyPi/docs/api_keys.md).

## PC development preview

See [Development](../../docs/development.md) to prepare dependencies. From this package directory on Windows:

```powershell
.\run_pc_dev.ps1
```

Open `http://127.0.0.1:8080`. Development mode uses a mock display and saves output under `InkyPi/mock_display_output/`.
