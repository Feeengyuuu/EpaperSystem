# Install from zero and make it your own

Run these commands on the Raspberry Pi after connecting over SSH.

## Prepare the Pi

Use a Raspberry Pi, suitable power supply, networking, a matching Waveshare or Pimoroni Inky display, and a microSD card (16 GB or larger recommended). Keep at least 2 GiB free under `/opt` and 512 MiB under `/var`.

Write Raspberry Pi OS Lite (preferably 64-bit) using [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Python 3.11 or newer is required. Customise the hostname, username, password, network, locale and timezone, and enable SSH under remote access. Follow the [official setup guide](https://www.raspberrypi.com/documentation/computers/getting-started.html). Connect the display with power off, then boot.

From your computer terminal or Windows PowerShell:

```bash
ssh <your-username>@inkypi.local
```

If the hostname does not resolve, substitute the IP address shown by your router.

## One-command installation

Paste this in the Pi SSH terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/Feeengyuuu/EpaperSystem/main/install.sh | sudo bash -s -- --lang en
```

If curl is missing, first run `sudo apt-get update && sudo apt-get install -y curl ca-certificates`.

The wizard downloads to `/opt/EpaperSystem`, asks for the screen, checks prerequisites, installs dependencies and the startup service, offers optional API keys, and waits for readiness. Press Enter to skip keys. Wait for completion; download speed and Pi performance affect duration. After the first installation, run `sudo reboot` to activate SPI/I2C changes. The installer never reboots automatically.

The default is Waveshare 7.3-inch colour HAT E, `epd7in3e`, 800 × 480. Append `-W epd7in5_V2` for that model, or `--pimoroni` for Inky automatic detection. Choose the exact model and revision; packaged drivers have not all been physically tested.

Unattended tasks require `--non-interactive --skip-keys`; explicitly select your display or the default will be used. Interactive mode without a terminal stops with guidance; use `ssh -t` for a terminal.

### Inspect first or check prerequisites only

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Feeengyuuu/EpaperSystem.git
cd EpaperSystem
bash install.sh --list-displays
bash install.sh --check
sudo bash install.sh --lang en
```

In a local checkout, `--check` reads prerequisites without installing software or changing services/configuration. Add a display option to check a different model. It does not test physical output; full installation needs internet access.

## First login and personal setup

Open the printed LAN address. First-time visitors reach `/auth/setup`. Read the pairing token in SSH:

```bash
sudo cat /var/lib/inkypi/data/security/bootstrap_admin.token
```

Enter the token in the browser and choose your administrator password. Existing users use `/auth/login`. For an expired token, run `sudo inkypi admin bootstrap` and read the replacement. Keep tokens and keys private.

1. Set your device name, timezone and orientation in Settings.
2. Add a page requiring no key, such as an image or Weather using Open-Meteo; configure your location and units.
3. Preview and display it to check the actual screen.
4. Add more plugins and configure their sources.
5. Create a playlist with your pages and rotation intervals.

A new installation can have no committed image yet. Display a page before assessing the screen. Application readiness does not prove every third-party source is available.

Add optional keys at `/api-keys` or run from any directory:

```bash
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --env-file /etc/inkypi/inkypi.env --lang en
sudo systemctl restart inkypi
```

See [API keys](api_keys.md). Runtime keys live in `/etc/inkypi/inkypi.env`, not a new source-checkout `.env`.

## Updates and data

Online installations can rerun the same command. A different repository or uncommitted local changes stop automatic updates; commit or preserve personal source changes first. Manual clones should run `git pull --ff-only`, then `sudo bash install.sh --lang en`.

Reinstallation preserves existing device/display configuration, passwords and API keys. Display arguments do not overwrite an existing model. To correct a wrong model, stop the service, edit `/var/lib/inkypi/config/device.json` with sudoedit, change `display_type`, remove the old `resolution` for automatic detection, then start the service. Rerun installation and reboot if the model needs SPI enabled.

| Contents | Location |
| --- | --- |
| Current and previous application | `/opt/inkypi/current`, `/opt/inkypi/previous` |
| Releases | `/opt/inkypi/releases`; current plus one previous after completed updates |
| Device and playlist configuration | `/var/lib/inkypi/config` |
| Images and administrator data | `/var/lib/inkypi/data` |
| Runtime cache | `/var/cache/inkypi` |
| Credentials | `/etc/inkypi/inkypi.env` |

Temporary ZIPs live in `/opt/inkypi/.tmp` and are cleaned on exit. For a personal fork, set `EPAPERSYSTEM_REPO_URL` and a separate `EPAPERSYSTEM_CHECKOUT_DIR`. A local clone uses its own source directly.

## Troubleshooting

Run from any directory:

```bash
sudo bash /opt/inkypi/current/install/healthcheck.sh --lang en --wait 120
sudo systemctl status inkypi --no-pager
sudo journalctl -u inkypi -n 120 --no-pager
```

- Browser unavailable: check the LAN, use the IP address, then inspect the service.
- Forgotten password: run `sudo inkypi admin recover`, read the recovery token as instructed, then visit `/auth/recover`.
- Blank screen: check wiring/model, reboot after first install, and display a page.
- Missing key: configure that provider at `/api-keys`; unused providers can stay blank.
- Download failure: check network, clock, GitHub access and package mirrors, then retry.
- Readiness `degraded`: the app is running with a degraded feature/source; inspect logs.

The default web interface is for a trusted LAN. Configure a private VPN or HTTPS access separately for remote use.
