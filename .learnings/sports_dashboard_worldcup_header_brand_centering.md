# SportsDashboard World Cup header brand centering

## [LRN-20260603-001] correction

**Logged**: 2026-06-03T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
The World Cup header logo and `2026 World Cup` title should render as one centered horizontal brand group.

### Details
For the SportsDashboard World Cup panel, the uploaded FIFA/World Cup logo and the `2026 World Cup` title should be laid out on the same baseline row and centered as a combined group within the World Cup panel header. Avoid returning to a left-anchored logo/title pair. Secondary source/status text can be smaller and placed at the header's lower right so it does not disturb the centered brand group.

### Suggested Action
Use the shared World Cup header-brand drawing helper instead of hardcoded logo/title coordinates in API and fallback render paths.

### Metadata
- Source: user_correction
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: sports-dashboard, world-cup, header, logo, alignment
