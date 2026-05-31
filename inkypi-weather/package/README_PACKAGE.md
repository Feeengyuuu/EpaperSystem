# InkyPi Weather Package

This package is prepared for:

- Raspberry Pi with 40-pin GPIO
- Waveshare 7.3 inch Spectra 6 / E6 full-color e-paper HAT, `800x480`
- InkyPi with the built-in Weather plugin
- OpenWeatherMap key stored in `InkyPi/.env`

The real API key is stored only in `InkyPi/.env`. Do not commit or paste that file.

## PC Development Preview

Dependencies are installed inside:

```text
InkyPi/.pc-packages
```

Start the local development UI from PowerShell:

```powershell
cd G:\PersonalProjects\EpaperSystem\inkypi-weather\package
.\run_pc_dev.ps1
```

Then open:

```text
http://127.0.0.1:8080
```

InkyPi development mode uses a mock display and saves rendered output under `InkyPi/mock_display_output/`.

## Raspberry Pi Install

Before first boot, configure WiFi, SSH, username, password, locale, and timezone in Raspberry Pi Imager.

After booting the Pi and copying this package to it:

```bash
cd ~/inkypi-weather-package
bash install_on_pi.sh
```

The installer uses:

```bash
sudo bash install/install.sh -W epd7in3e
```

InkyPi then runs as the `inkypi` systemd service. With WiFi available, the Weather plugin can fetch and refresh weather data automatically on its schedule.

Useful commands on the Pi:

```bash
sudo systemctl status inkypi
sudo systemctl restart inkypi
journalctl -u inkypi -f
```

In the web UI, choose the Weather plugin, select `OpenWeatherMap`, set your coordinates, units, and refresh interval.

## OpenWeather Cost Guard

OpenWeather One Call 3.0 is pay-as-you-call. OpenWeather currently includes the first 1,000 One Call API requests per day for free, but their FAQ says a new One Call subscription defaults to a 2,000 calls/day limit. To avoid paid overage, log in to OpenWeather and set the One Call 3.0 daily limit to `1000` or lower in the Billing plan tab.

This package also enforces a local safety guard:

```text
OPENWEATHER_ONECALL_DAILY_LIMIT=900
OPENWEATHER_ONECALL_MIN_SECONDS=1800
OPENWEATHER_AUX_MIN_SECONDS=1800
OPENWEATHER_LOCATION_MIN_SECONDS=86400
```

The local guard defaults to at most 900 live One Call requests per UTC day and caches One Call responses for at least 30 minutes. The code clamps `OPENWEATHER_ONECALL_DAILY_LIMIT` to the official free maximum of 1,000 even if a larger value is entered.

The account-level OpenWeather daily limit is still required because the local guard cannot see requests made by other devices, browser tests, or future projects using the same OpenWeather account.

## OpenWeather Status

The packaged `.env` key is readable by `python-dotenv`. InkyPi's `OpenWeatherMap` provider calls One Call 3.0.

To inspect the local guard without sending a request:

```powershell
cd G:\PersonalProjects\EpaperSystem\inkypi-weather\package
.\check_openweather.ps1
```

To validate the OpenWeather key, run the live check. This sends exactly one One Call 3.0 request:

```powershell
.\check_openweather.ps1 -Live
```

If you do not want to consume any OpenWeather calls during UI work, use the Weather plugin's `Open-Meteo` provider for live weather without an API key.
