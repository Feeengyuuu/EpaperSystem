# GCD Comic Covers Triptych Display

## [LRN-20260603-001] user preference

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
GCD comic covers should use the same display semantics as the BacktotheDate Chinese poster plugin: a plain three-vertical-image triptych over a soft blurred backdrop.

### Details
The user asked to change the comic-cover plugin display to match the Chinese traditional poster plugin. The intended visual rule is to show up to three upright portrait covers in columns, paste the covers plainly, and avoid frame, shadow, matte card, or text label overlays. Existing single-cover fit modes can remain as explicit alternatives, but the default generation path should prefer triptych.

### Suggested Action
When modifying `gcd_comic_covers`, keep default `fitMode` as `triptych`, preserve the BacktotheDate-style blurred backdrop, and avoid reintroducing bottom info labels in triptych output.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`, `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/settings.html`, `inkypi-weather/package/InkyPi/tests/test_gcd_comic_covers.py`
- Tags: inkypi, gcd, comics, triptych, backtothedate, display-rule
