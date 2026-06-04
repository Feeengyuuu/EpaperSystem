# LoLInfo bottom-right reserved region

## [LRN-20260604-003] best_practice

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, layout

### Summary
LoLInfo bottom overview should reserve the right side for future refresh content.

### Details
The user requested that the lower overview area be squeezed left so the right side remains open for a planned future refresh module. The implemented layout keeps the bottom panel full width, but constrains the Riot logo and six overview metrics to the left portion by reserving roughly a quarter of the panel width on the right.

### Suggested Action
For future LoLInfo bottom-panel edits, preserve the right reserved area unless the user explicitly fills it. Keep the existing left-packed metric layout and bump `STYLE_VERSION` after coordinate changes so cached images do not hide the update.

### Metadata
- Source: production_visual_refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`
- Tags: lol-info, bottom-panel, reserved-region, layout, 800x480
