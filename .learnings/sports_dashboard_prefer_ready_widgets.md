# Sports dashboard prefer ready widgets

## [LRN-20260602-006] workflow

**Logged**: 2026-06-02T15:32:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
For dense sports dashboards, prefer a mature embeddable widget or screenshot source before hand-designing a new InkyPi layout.

### Details
The custom `sports_dashboard` layout was not visually acceptable after multiple passes. Sports pages combine long team names, live scores, stages, history, logos, and sidebars in a very constrained 800x480 e-paper canvas. A hand-drawn PIL layout is brittle and can degrade quickly. The project already has a `screenshot` plugin that can render a URL directly, and external sports widgets can provide more polished, maintained layouts for World Cup fixtures and live scores.

### Suggested Action
When asked for a sports/e-sports information page, first check whether an existing embeddable widget or website can be rendered through the `screenshot` plugin. Only build a custom PIL plugin when the target source cannot be embedded, the user explicitly wants a custom layout, or the widget cannot meet refresh/data requirements.

### Metadata
- Source: user correction during sports_dashboard layout iteration
- Related Files: inkypi-weather/package/InkyPi/src/plugins/screenshot/screenshot.py
- Tags: sports_dashboard, screenshot_plugin, ready_widget, epaper_layout, visual_quality
