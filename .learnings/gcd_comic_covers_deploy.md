# GCD Comic Covers Deploy

## [LRN-20260529-001] workflow

**Logged**: 2026-05-29T20:20:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Validate deployed InkyPi plugin imports with `/usr/local/inkypi/venv_inkypi/bin/python`, not bare `python3`.

### Details
During GCD Comic Covers deployment, bare remote `python3` failed with `ModuleNotFoundError: No module named 'PIL'`, but the service venv imported the plugin successfully. `/usr/local/inkypi/src/...` resolves to the current package path under `/home/feeengyuuu/inkypi-weather-pi-package-20260524-3/InkyPi/src/...`, so uploading to the package path also updated the service-loaded files.

### Suggested Action
For future EpaperPod plugin deploy verification, run:

```bash
cd /usr/local/inkypi/src
PYTHONDONTWRITEBYTECODE=1 /usr/local/inkypi/venv_inkypi/bin/python -c "from plugins.<id>.<id> import <Class>"
```

Do not treat bare `python3` dependency failures as service-runtime failures.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`
- Tags: inkypi, epaperpod, deploy, python-venv, plugin-import

## [LRN-20260529-002] workflow

**Logged**: 2026-05-29T21:35:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For EpaperPodBeta demos, prefer InkyPi's local HTTP endpoints over direct root display scripts.

### Details
The `feeengyuuu` SSH account could upload files and edit the app-owned device config, but `sudo -n` required a password for root display scripts and service restarts. The working path for real-device demos was to call `http://127.0.0.1/update_now` or `display_plugin_instance` from the Pi so the already-root InkyPi service performs the display write. Startup can take several minutes before Waitress serves port 80 after a restart.

### Suggested Action
For future real-device previews, first wait for `journalctl -u inkypi` to show `Serving on http://0.0.0.0:80`, then trigger the plugin through the local HTTP API and fetch `/static/images/current_image.png` for visual confirmation.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/blueprints/plugin.py`, `inkypi-weather/package/InkyPi/src/refresh_task.py`
- Tags: inkypi, epaperpod, ssh, sudo, update-now, real-device-preview

## [LRN-20260530-001] workflow

**Logged**: 2026-05-30T00:30:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Do not use `image_url` with a `127.0.0.1` InkyPi URL inside `/update_now`; use `image_upload` for local generated previews.

### Details
The production Waitress server runs with `threads=1`. Calling `/update_now` for `image_url` with a URL served by the same InkyPi process can deadlock or return HTTP 500 because the request handler blocks while trying to fetch from itself. Uploading the generated PNG through the `image_upload` plugin avoids the self-request and reliably updates the display.

### Suggested Action
For local preview images generated on EpaperPodBeta, call:

```bash
curl -F plugin_id=image_upload -F 'imageFiles[]=@/path/to/preview.png;type=image/png' http://127.0.0.1/update_now
```

Then fetch `/static/images/current_image.png` immediately for visual evidence before the 300-second playlist cycle overwrites it.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/image_url/image_url.py`, `inkypi-weather/package/InkyPi/src/plugins/image_upload/image_upload.py`
- Tags: inkypi, epaperpod, image-upload, image-url, waitress, single-thread
