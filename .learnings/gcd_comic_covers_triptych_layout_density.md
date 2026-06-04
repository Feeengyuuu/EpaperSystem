# GCD Comic Covers Triptych Layout Density

## [LRN-20260603-002] user preference

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Comic cover triptychs should fill the panel with upright portrait covers and avoid small centered landscape strips.

### Details
When only two usable covers are available, dividing the layout by the fixed three-cover count makes the pair look too centered with excessive side whitespace. The user preferred larger imagery, so the triptych renderer should divide the screen by the actual visible cover count. Landscape comic strips can also render as small centered bands inside vertical slots; prefer portrait covers first and use wide covers only as fallback when there are not enough portrait candidates.

### Suggested Action
For `gcd_comic_covers`, keep `ImageOps.contain` to preserve cover art, but size columns by `len(images)` for partial triptychs and delay covers with `image.width > image.height * 1.15` until portrait candidates are exhausted.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`, `inkypi-weather/package/InkyPi/tests/test_gcd_comic_covers.py`
- Tags: inkypi, gcd, comics, triptych, layout, portrait-covers
