# DailyArt Museum API Sources

## [LRN-20260603-003] best_practice

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
DailyArt should treat museum APIs as a best-effort source pool and tolerate real device key-name drift.

### Details
The initial DailyArt rollout used The Met and Art Institute of Chicago as no-key sources, with Harvard Art Museums and Europeana enabled only when their keys are present. On the live device, the Harvard key was stored as `Harverd_Key`, so the plugin needs to support this typo alongside standard Harvard API key names. Europeana was not present in the live `.env` during rollout, so it should be skipped quietly until a Europeana variable is added.

### Suggested Action
For museum-image plugins, list API key variable names without values before debugging auth. Keep sources best-effort: use Met/AIC without keys, include Harvard when `Harverd_Key` or standard Harvard key aliases are present, and include Europeana only when a Europeana key alias is present. Do not let one source failure block the refresh.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/daily_art/daily_art.py`, `inkypi-weather/package/InkyPi/tests/test_daily_art.py`
- Tags: inkypi, daily-art, museum-api, harvard, europeana, api-keys
