# ColoredEpaperFrame Chromium screenshot support

## [LRN-20260531-008] environment

**Logged**: 2026-05-31
**Priority**: high
**Status**: active
**Area**: infra

### Summary
`ColoredEpaperFrame` can run Chromium-based HTML screenshots; verify by executing a real screenshot, not just checking the binary path.

### Details
The old Raspberry Pi Zero W board had `/usr/bin/chromium-headless-shell`, but prior work found it could fail with `Illegal instruction`. On `ColoredEpaperFrame` at `192.168.1.188`, `chromium-headless-shell --version` returned a Chromium version, and an InkyPi runtime smoke test using `take_screenshot_html()` generated an `(800, 480)` RGB image successfully.

### Suggested Action
When deciding whether to enable browser-backed plugins on `ColoredEpaperFrame`, treat Chromium screenshot support as available, but still smoke test new web-heavy plugins because Zero 2 W memory is limited. Use the InkyPi runtime with an explicit `PYTHONPATH` when running scripts from `~/incoming`.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/utils/image_utils.py`
- Tags: colored-epaper-frame, chromium, screenshot, html-render, zero2w, inkypi

---
