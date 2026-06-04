# NatGeo multi-image daily rotation preference

## [LRN-20260601-006] user_preference

**Logged**: 2026-06-01T14:50:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
NatGeoDaily should change images multiple times per day, not behave as a once-per-day photo slot.

### Details
The user clarified that the desired `natgeo_photo_of_the_day` behavior is "一天多换点图". On ColoredEpaperFrame, the live `NatGeoDaily` instance was changed from a daily scheduled refresh to `{"interval": 10800}` so it refreshes about every 3 hours. The plugin already keeps no-repeat source state across `natgeo` and `discovery`; after the change, live config showed `daily_photo_source_last=natgeo` and `daily_photo_source_queue=["discovery"]`.

### Suggested Action
For future NatGeoDaily work, preserve multi-image-per-day rotation. Prefer interval refreshes around 3 hours unless the user asks for a different cadence, and keep no-repeat source/image queues intact.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/natgeo_photo_of_the_day/natgeo_photo_of_the_day.py`
- Tags: colored-epaper-frame, natgeo, discovery, daily-photos, refresh-cadence
