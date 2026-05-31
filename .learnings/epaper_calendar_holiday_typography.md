# Simple Calendar Holiday Typography

## [LRN-20260529-110] correction

**Logged**: 2026-05-29T01:39:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For the Simple Calendar bottom holiday list, use regular-weight Chinese sans typography in pure black rather than simulated bold.

### Details
The user found the bottom holiday names too light when grey, but simulated bold via stroke became too heavy and blurry on the e-paper render. The preferred compromise is a Microsoft YaHei/Noto Sans SC style regular font with pure black fill and no stroke.

### Suggested Action
When adjusting Simple Calendar holiday/event text, keep the title column at normal weight, pure black, and avoid `stroke_width` unless explicitly requested. If Microsoft YaHei is unavailable on the Pi, use `NotoSansSC-VF.ttf` as the fallback.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`
- Tags: inkypi, simple-calendar, typography, epaper, readability
