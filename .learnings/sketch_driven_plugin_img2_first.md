# Sketch-driven plugin pages use img-2 before code

## [LRN-20260605-002] visual concept gate

**Logged**: 2026-06-05T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper-plugin-ui

### Summary
When the user provides a hand-drawn plugin page sketch and asks for a visual layout, generate an img-2 concept first before continuing code layout work.

### Details
During the Starlink radar plugin prototype, the implementation moved into code and tests before the user clarified that they wanted an img-2 example based on the sketch first. The visual concept should become the layout reference before committing to renderer geometry.

### Suggested Action
- For sketch-driven e-paper plugin UI work, pause implementation and generate a concept image first.
- Use the sketch's composition as the primary source of truth: region placement, major shapes, accent colors, and density.
- Resume code only after the user confirms the concept direction or requests changes.

### Metadata
- Source: user_correction
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/starlink_radar/`
- Tags: starlink-radar, image-first, img-2, epaper-ui, sketch-reference
