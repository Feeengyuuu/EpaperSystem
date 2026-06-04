# E-paper dashboard imagegen reference first

## [LRN-20260602-006] project_state

**Logged**: 2026-06-02T15:35:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For visually dense e-paper dashboard pages, use an image-generation reference before finalizing Pillow layout when the first coded layout feels weak.

### Details
The first `sports_dashboard` implementation was functionally correct but looked like loose text blocks. The user asked to generate an `img-2` reference and then lay out the plugin according to that image. The generated reference clarified the stronger composition: main event card, sports-broadcast header, compact stat strip, process rail, prominent live-score module, and a disciplined sidebar.

### Suggested Action
For future new dashboard-style plugins, especially sports, finance, or data-heavy pages, create a visual reference first when visual quality is important. Then translate the reference into deterministic Pillow code while preserving e-paper constraints: no scrolling, no overlap, high contrast, and clean fallback behavior.

### Metadata
- Source: sports_dashboard visual redesign
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py
- Tags: imagegen, sports_dashboard, layout, epaper, pillow
