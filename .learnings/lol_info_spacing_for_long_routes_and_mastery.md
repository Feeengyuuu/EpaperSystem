# LoLInfo spacing for long route labels and mastery rows

## [LRN-20260604-001] best_practice

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, layout

### Summary
LoLInfo needs compact fitted route labels and explicit bottom padding in the mastery panel.

### Details
On the live `LoLInfo` page for `NA1 / AMERICAS`, the route label looked slightly misaligned when rendered as a larger fixed-position string, and the third mastery row sat too close to the right panel's bottom border. The fix was to render the route with `_single()` using the tiny font and a slash separator, move the mastery list upward, reduce icon size and row step, and use a larger right padding for mastery bars.

### Suggested Action
For future LoLInfo layout edits, keep route labels width-fitted because `AMERICAS` is much longer than `ASIA`. Maintain at least several pixels of visible bottom and right padding around the third mastery row on the 800x480 layout.

### Metadata
- Source: production_visual_refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`
- Tags: lol-info, spacing, route-label, mastery-panel, 800x480
