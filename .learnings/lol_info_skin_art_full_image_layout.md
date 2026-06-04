# LoLInfo skin art full image layout

## [LRN-20260604-005] best_practice

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, layout, visual

### Summary
LoLInfo skin splash art should render as an unobstructed image, with the Riot logo in the lower middle gap.

### Details
After adding the right-side skin-art pool, the first pass placed champion and skin-name labels over the bottom of the splash image and kept the Riot logo near the image's upper-left edge. The user corrected this: the skin artwork should fill its framed area without text overlays, and the Riot logo should sit in the lower empty space between the overview metrics and the skin image.

### Suggested Action
For future LoLInfo visual changes, do not overlay champion names, skin names, or source labels on top of the skin splash frame. Keep `_overview_layout()` responsible for positioning the large right-side art frame and the lower-middle Riot logo, and verify on the 800x480 render before deploying.

### Metadata
- Source: production_visual_refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `inkypi-weather/package/InkyPi/tests/test_lol_info.py`
- Tags: lol-info, riot-logo, skin-art, no-overlay, layout, 800x480
