# Newspaper source screenshot policy

## [LRN-20260531-006] correction

**Logged**: 2026-05-31
**Priority**: medium
**Status**: active
**Area**: epaperpod-newspaper

### Summary
For the `newspaper` plugin, do not add a news source to production if the source cannot produce an acceptable screenshot, unless the user explicitly asks for a text/headline fallback.

### Details
The user rejected adding `参考消息` when the official screenshot route only produced a loading page and the working fallback was a rendered headline/text page. The production media list should favor actual screenshot-capable URL sources and existing newspaper-cover sources.

### Suggested Action
- Test candidate web sources with the real Chromium screenshot path before adding them.
- If a site is blank, app-gated, ad-heavy, or only usable through a headline/text fallback, report that and leave it out by default.
- Keep fallback renderers disabled from production media lists unless the user explicitly chooses that tradeoff.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/newspaper/newspaper.py`, `.tmp/update_newspaper_web_sources.py`
- Tags: epaperpod, newspaper, screenshots, source-selection, ColoredEpaperFrame

---
