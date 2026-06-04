# LPL logo future-recent sidebar

## [LRN-20260602-007] project_state

**Logged**: 2026-06-02T16:35:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
The LPL sidebar should keep both upcoming matches and recent results, and team logos can be read from the LoL Esports schedule payload.

### Details
The LoL Esports persisted schedule response includes `match.teams[].image`, for example `http://static.lolesports.com/teams/...png`. When rendering a compact e-paper sidebar, use these images as small cached logos beside team codes. The user explicitly corrected that `recent` should remain visible even after narrowing the information architecture to the next three future matches.

### Suggested Action
For future sports dashboard revisions, preserve `NEXT` and `RECENT` as separate compact sections. If adding logos or larger type, reduce per-row density before deleting either section.

### Metadata
- Source: sports_dashboard LPL sidebar correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py
- Tags: sports_dashboard, lpl, team_logo, recent_results, epaper
