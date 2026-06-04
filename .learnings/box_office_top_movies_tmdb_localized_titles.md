# Box office plugin TMDb localized titles

## [LRN-20260601-005] best_practice

**Logged**: 2026-06-01T02:26:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For `box_office_top_movies`, keep The Numbers/TMDb matching in English, then request TMDb `zh-CN` details for the Simplified Chinese display title.

### Details
The North America box-office source provides English titles, and English TMDb search is the most stable way to match the correct movie id and poster. After the id is known, `/movie/{id}?language=zh-CN` can provide the Simplified Chinese title; if that detail title is missing or not CJK, the plugin should fall back to TMDb alternative titles for region `CN`, then omit the localized line rather than inventing a translation.

### Suggested Action
When extending movie or TV chart plugins, separate search language from display language. Use `tmdbLanguage=en-US` for search, `localizedLanguage=zh-CN` for the secondary title, cache the localized title with the same movie entry, and bump the cache schema when adding localization fields.

### Metadata
- Source: feature_development
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/box_office_top_movies/box_office_top_movies.py`
- Tags: box-office, tmdb, localization, zh-cn, epaper
