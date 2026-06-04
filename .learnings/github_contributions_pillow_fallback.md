# GitHub Contributions Pillow fallback

## [LRN-20260531-001] project_quirk

**Logged**: 2026-05-31T17:05:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
On `ColoredEpaperFrame`, the stock GitHub Contributions plugin can fail through the HTML screenshot path and return no image, causing a later display update error like `'NoneType' object has no attribute 'convert'`.

### Details
The live `/update_now` call for plugin `github` with `githubType=contributions` and username `Feeengyuuu` reached the device but failed before the image reached the display. The fix was to render Contributions directly with Pillow by default and reserve the old HTML renderer only for an explicit `githubRenderer=html` setting.

### Suggested Action
For future GitHub Contributions work, keep the Pillow renderer as the production path on the Pi. Validate with remote `PYTHONPYCACHEPREFIX=/tmp/inkypi-pycache python3 -m py_compile`, restart `inkypi`, then verify `/update_now`, `/api/current_image`, and `journalctl -u inkypi` for an `Updating display` line.

### Metadata
- Source: live debug
- Related Files: inkypi-weather/package/InkyPi/src/plugins/github/github_contributions.py
- Tags: github, contributions, chromium, pillow, epaperpod, colored-epaper-frame
