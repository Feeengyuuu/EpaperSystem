# Epaper Existing Instance Settings Migration

## [LRN-20260603-009] best_practice

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
When a plugin gains new default settings, existing playlist instances keep their old settings until explicitly updated.

### Details
During the multi-plugin deploy, `ChineseClock` had the new `settings.html` default of `quote_selection=source_random` and Open Library enrichment enabled, but the live `ChineseClock` instance still had the old persisted setting `quote_selection=shortest`. Because `update_plugin_instance` replaces the entire settings payload, the migration needed to read the current non-secret settings, preserve visual values such as font, background, and highlight style, then update only the desired behavior fields.

### Suggested Action
After deploying settings-bearing plugin changes, inspect the live `device.json` for key instances. Do not assume UI defaults affect existing instances. Use the app API to update the existing instance with a complete form payload that preserves prior visual settings and adds the new behavior settings.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/settings.html`, `inkypi-weather/package/InkyPi/src/blueprints/plugin.py`
- Tags: inkypi, playlist, settings, migration, chinese-clock, deploy
