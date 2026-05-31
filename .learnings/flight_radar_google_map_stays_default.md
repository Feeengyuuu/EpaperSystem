# Flight radar Google map default

- For `flight_radar` / `SkyRadar`, keep the radar map layer on `google_static` unless the user explicitly asks to switch away from Google Maps.
- Do not infer a desired switch to the local calibrated map from a settings-page screenshot; verify the live `device.json` first.
- Keep Google Static Maps first/selected in `settings.html` so opening the settings page does not suggest the local map is the intended default.
