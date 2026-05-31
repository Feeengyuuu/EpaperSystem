# Learning: Deploy pending InkyPi source without static overwrite, and guard MagazineCovers on Pi Zero

**Logged**: 2026-05-27T19:10:19-07:00
**Priority**: high
**Status**: active
**Area**: epaper

## Summary
Pending EpaperPod deploys should package only changed `InkyPi/src` runtime files, and MagazineCovers must skip oversized source images before PIL decode on Pi Zero W.

## Details
Deploying a full `InkyPi/src` zip can fail on the Pi because some `InkyPi/src/static/...` directories are not writable by the normal SSH user. Build deploy zips from pending runtime changes with Unix-style entries such as `InkyPi/src/plugins/...`, excluding `__pycache__`, plugin caches, tests, local learnings, and unchanged static assets.

During this deploy, `MagazineCovers` repeatedly killed `inkypi` with `status=4/ILL` when PIL began decoding large magazine images on the Pi Zero W. Python cannot catch that signal. The stable fix is to download each candidate to a temp file, read only the image header for dimensions, skip anything above a conservative Pi-safe pixel threshold, and then pass only small candidates into `AdaptiveImageLoader`. If no Pi-safe source is available, render a local fallback image rather than raising a fatal error.

## Suggested Action
For future EpaperPod deploys, prefer a reduced pending-source zip plus remote `python3 -m ast` checks and `sudo -n systemctl restart inkypi`. After any plugin that handles remote images is deployed, trigger its real `/display_plugin_instance` path once and inspect `journalctl -u inkypi` for `status=4/ILL`, `waitress: Serving`, and successful `Updating display` lines.

## Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/magazine_covers/magazine_covers.py`, `inkypi-weather/dist/epaperpod-pending-src-20260527-1834.zip`
- Tags: inkypi, epaperpod, deploy, static-permissions, magazine-covers, pi-zero, image-decode, sigill
