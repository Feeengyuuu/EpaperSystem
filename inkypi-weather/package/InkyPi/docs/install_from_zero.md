# Install From Zero

This guide starts with a blank Raspberry Pi and ends with the InkyPi web UI
running on your network.

## What You Need

- Raspberry Pi with Wi-Fi or Ethernet.
- MicroSD card, 16 GB or larger recommended.
- E-paper display.
- A computer on the same network as the Pi.
- Raspberry Pi Imager: <https://www.raspberrypi.com/software/>

The default beginner installer assumes a Waveshare 7.3 inch color display using
driver model `epd7in3e`. Other Waveshare and Pimoroni displays are still
supported.

## 1. Flash Raspberry Pi OS

1. Open Raspberry Pi Imager on your computer.
2. Choose your Raspberry Pi model.
3. Choose Raspberry Pi OS Lite 64-bit when available.
4. Choose the target microSD card.
5. Click the settings gear or "Edit Settings".
6. Set:
   - Hostname, for example `inkypi`.
   - Username and password.
   - Wi-Fi SSID and password.
   - Locale/time zone.
7. Enable SSH.
8. Write the card, eject it, insert it into the Pi, and power on the Pi.

Wait 2-5 minutes for the first boot.

## 2. SSH Into The Pi

From your computer:

```bash
ssh <username>@inkypi.local
```

If `.local` name lookup does not work, find the Pi IP address in your router and
use:

```bash
ssh <username>@<pi-ip-address>
```

## 3. Install Git

```bash
sudo apt-get update
sudo apt-get install -y git
```

## 4. Download This Project

```bash
git clone https://github.com/Feeengyuuu/EpaperSystem.git
cd EpaperSystem/inkypi-weather/package/InkyPi
```

If this project is published with `InkyPi` as the repository root, use:

```bash
cd <your-repo>
```

## 5. Run The Beginner Installer

For the default Waveshare 7.3 inch color panel:

```bash
sudo bash install/bootstrap.sh
```

For Simplified Chinese prompts:

```bash
sudo bash install/bootstrap.sh --lang zh-CN
```

For a different Waveshare model:

```bash
sudo bash install/bootstrap.sh -W epd7in5_V2
```

For Pimoroni Inky displays:

```bash
sudo bash install/bootstrap.sh --pimoroni
```

The installer will:

1. Install Linux packages.
2. Enable SPI and I2C.
3. Create `/usr/local/inkypi`.
4. Create the Python virtual environment.
5. Install and enable the `inkypi` systemd service.
6. Create `.env` if it does not exist.
7. Offer optional API key setup.
8. Start the service and run a health check.

API keys are optional. Press Enter to skip them during install. Add them later
from the web UI or command line.

## 6. Reboot Once

Fresh Pi installs should reboot once so SPI/I2C changes are fully active:

```bash
sudo reboot now
```

Wait 1-2 minutes, then SSH back in:

```bash
ssh <username>@inkypi.local
cd EpaperSystem/inkypi-weather/package/InkyPi
```

## 7. Verify

```bash
bash install/healthcheck.sh
```

If the health check passes, open one of these in your browser:

```text
http://inkypi.local
http://<pi-ip-address>
```

## 8. Add API Keys Later

Web UI:

```text
http://<pi-ip-address>/api-keys
```

Command line:

```bash
python3 install/configure_api_keys.py --env-file .env
```

Simplified Chinese prompts:

```bash
python3 install/configure_api_keys.py --env-file .env --lang zh-CN
```

List registration URLs:

```bash
python3 install/configure_api_keys.py --list
python3 install/configure_api_keys.py --list --lang zh-CN
```

After changing keys:

```bash
sudo systemctl restart inkypi
```

Full API key details are in [api_keys.md](./api_keys.md) and
[api_keys.zh-CN.md](./api_keys.zh-CN.md).

## 9. Debugging Commands

Run these and copy the output into a GitHub issue:

```bash
bash install/healthcheck.sh
sudo systemctl status inkypi --no-pager
sudo journalctl -u inkypi -n 120 --no-pager
```

Common fixes:

- Web UI does not open: run `sudo systemctl restart inkypi`, then `bash install/healthcheck.sh`.
- Display stays blank after first install: run `sudo reboot now`.
- API plugin says missing key: open `/api-keys` or run `python3 install/configure_api_keys.py --list`.
- Wrong Waveshare model: rerun `sudo bash install/bootstrap.sh -W <model>`.
