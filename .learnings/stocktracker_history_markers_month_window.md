# StockTracker history markers and month window

## [LRN-20260604-003] visual behavior

**Logged**: 2026-06-04T17:56:00-07:00
**Priority**: medium
**Status**: active
**Area**: stocktracker

### Summary
For the `Money` StockTracker instance, the portfolio trend curve remains the original market/portfolio trend for the selected period. Saved history snapshots are decorative markers sampled along that curve, not the data source that defines the curve shape.

### Details
The user corrected that historical access/snapshot points should accumulate as dots on the existing trend line. Do not replace the trend line with the saved snapshot values. The live `Money` instance should track the month window by setting `period` to `1mo`, and the holdings table should show the visible label `WINDOW: LAST MONTH`.

### Suggested Action
- Keep marker coordinates sampled from the rendered portfolio curve.
- Preserve daily snapshot persistence unless the user explicitly asks for per-refresh history.
- After StockTracker changes, verify locally with `tests/test_stocktracker.py`, then deploy to `/usr/local/inkypi` and trigger `DailyDoseOfDay / stocktracker / Money`.

### Metadata
- Source: live_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`, `inkypi-weather/package/InkyPi/tests/test_stocktracker.py`
- Tags: stocktracker, money, history-markers, last-month, ColoredEpaperFrame
