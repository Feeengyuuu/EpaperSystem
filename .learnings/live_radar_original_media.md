# LiveRadar source media should stay unprocessed

## [LRN-20260528-001] user_preference

**Logged**: 2026-05-28T00:49:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
LiveRadar should keep live screenshots and avatars visually original; avoid grayscale, autocontrast, thresholding, or whole-image black-white cleanup on fetched media.

### Details
The user accepted the current card layout but corrected that live screenshots and avatars should use the original fetched imagery. A prior attempt to remove e-paper dot patterns by thresholding the full generated image made screenshots and avatars too processed. The better pattern is to keep UI chrome in solid black/white where possible while only resizing/cropping source media to fit the card.

### Suggested Action
When tuning LiveRadar for e-paper, do not apply `ImageOps.grayscale`, `ImageOps.autocontrast`, or final image thresholding to stream covers or avatars. Preserve source RGB content and use binary UI colors to reduce non-media dithering.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: inkypi, epaperpod, live-radar, media, avatar, cover, user-preference
