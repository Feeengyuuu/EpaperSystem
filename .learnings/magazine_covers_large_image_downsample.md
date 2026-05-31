# Magazine Covers large image downsample and WebP guard

## [LRN-20260528-001] best_practice

**Logged**: 2026-05-28T00:03:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
MagazineCovers should downsample oversized non-WebP source images before display, but oversized WebP must be skipped on the Pi Zero.

### Details
The MagazineCovers fallback happened because the random queue was stuck on WIRED Japan and its cover candidates were oversized WebP files such as 2268x2858. Attempting to decode those WebP files on the Pi killed `inkypi` with `status=4/ILL`, so PIL-based downsampling is not safe for oversized WebP on this device. The working fix is to downsample large JPEG/ordinary images to a Pi-safe temporary JPEG, skip oversized WebP candidates without decoding them, remove failed random-queue sources, and continue trying other magazine sources in the same refresh.

### Suggested Action
For future e-paper image plugins, inspect source format and dimensions before full decode. Downsample safe formats to below the Pi-safe pixel budget, but avoid decoding oversized WebP on the current Pi Zero runtime unless an external converter is proven safe. Always continue to fallback sources instead of ending the refresh on one failed source.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/magazine_covers/magazine_covers.py, inkypi-weather/package/InkyPi/tests/test_magazine_covers.py
- Tags: inkypi, epaperpod, magazine-covers, pi-zero, webp, sigill, downsample
