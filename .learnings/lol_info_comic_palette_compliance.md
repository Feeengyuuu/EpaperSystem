# LoLInfo comic palette compliance

## [LRN-20260604-002] best_practice

**Logged**: 2026-06-04
**Priority**: high
**Status**: active
**Area**: epaper, lol-info, color-ui

### Summary
LoLInfo should follow the vintage comic process-color rule instead of a blue/cyan dashboard palette.

### Details
The live LoLInfo page initially used a dark blue panel system with saturated cyan borders, which made the screen read as a generic digital dashboard rather than the EpaperSystem formal color rule. The correction keeps night readability but maps the UI to `docs/color-ui-guidelines.md`: process-black background, warm-paper text and linework, and limited flat accent colors for cyan, amber, red, and green.

### Suggested Action
For future LoLInfo color changes, avoid cyan as the dominant border or background color. Keep panel borders warm-paper, use black/dark grounds for night mode, and reserve cyan for small informational labels. Bump `STYLE_VERSION` after color-token changes so cached images do not mask the update.

### Metadata
- Source: production_visual_refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `docs/color-ui-guidelines.md`
- Tags: lol-info, color-ui, comic-palette, process-black, warm-paper
