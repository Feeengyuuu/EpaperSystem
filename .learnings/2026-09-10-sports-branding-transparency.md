# Sports display aliases and transparent event branding

## [LRN-20260910-SPORTS01] correction

**Logged**: 2026-09-10T22:34:05Z
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Same-event UPCOMING rows should show match details without repeating the event title or event logo already in the focus card.

### Details
The user clarified this display rule for the CS2 sidebar. Keep both team icons, kickoff, match format, and stale-data indications. Event schedule selection and the cross-sport sidebar priority remain independent of row decoration.

### Suggested Action
Test both themes and stale schedule cases at the full sidebar rendering boundary.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/esports_render.py
- Pattern-Key: simplify.same_event_upcoming_identity

---

## [LRN-20260910-SPORTS02] best_practice

**Logged**: 2026-09-10T22:34:05Z
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Validate cached localization content and check the compositor before blaming logo alpha.

### Details
FC Schalke 04 lacked an exact provider alias, and cached *_name_zh values containing English bypassed later lookup. Only an actual Chinese label should bypass localization; keep canonical source names and provider identity unchanged. An opaque rectangle can also be introduced after loading a transparent logo: the event renderer added a full contrast backing. A one-pixel alpha contour supplies contrast while keeping transparent margins and shared cached pixels intact.

### Suggested Action
Cover cached English names, exact provider aliases without cross-provider IDs, transparent margins in both themes, unchanged cached pixels, and preserved stale labels.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football_render.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/esports_render.py
- Pattern-Key: harden.cached_display_semantics
