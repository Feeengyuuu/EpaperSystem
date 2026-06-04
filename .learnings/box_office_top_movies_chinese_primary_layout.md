# Box office plugin Chinese-primary layout

## [LRN-20260601-006] user_feedback

**Logged**: 2026-06-01T02:31:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For `box_office_top_movies`, Simplified Chinese titles should be visually primary and English titles secondary.

### Details
After adding TMDb `zh-CN` titles, the first deployed layout showed English as the main bold title and Chinese as the smaller gold subtitle. The user corrected the hierarchy: Chinese should be more important, English secondary, and the font treatment should be swapped. The final deployed layout uses bold white CJK titles as the primary text and smaller gold English subtitles.

### Suggested Action
Keep the box-office display Chinese-first when `localized_title` is available. Use English as a secondary identifier beneath it, and only fall back to English as the primary title when TMDb has no usable Simplified Chinese title.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/box_office_top_movies/box_office_top_movies.py`
- Tags: box-office, tmdb, zh-cn, layout, typography, epaper
