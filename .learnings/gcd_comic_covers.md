# GCD Comic Covers

## [LRN-20260529-001] correction

**Logged**: 2026-05-29T20:05:00-07:00
**Priority**: medium
**Status**: superseded
**Area**: epaper

### Summary
For the GCD comic-cover plugin, include modern comics by default and cap the newest date at the device's current day.

### Details
The user changed the scope from only old American comics to both old and modern comics. The default year range should start at 1938 and end at the current year, while current-year candidates with on-sale dates after the device's current date should not be selected.

### Suggested Action
When changing or rebuilding `gcd_comic_covers`, keep the default newest year dynamic and preserve the future-date guard in the candidate filter.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`, `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/settings.html`
- Tags: inkypi, gcd, comics, date-filter, modern-comics

## [LRN-20260529-002] correction

**Logged**: 2026-05-29T21:35:00-07:00
**Priority**: medium
**Status**: superseded
**Area**: epaper

### Summary
GCD comic covers should default to full, no-rotation rendering; cropped or auto-rotated cover layouts are not acceptable for this playlist.

### Details
The user explicitly corrected the display rule: do not rotate comic cover images, and show the full image even when the cover orientation does not match the horizontal e-paper screen. The acceptable treatment is contain/letterbox with background fill. Real-device preview used `fitMode=full` on Action Comics #14 and showed the full portrait cover centered with blurred side fill.

### Suggested Action
Keep `gcd_comic_covers` default `fitMode` as `full`. When debugging no-cover cases, prioritize candidates with direct `cover_url` values before API-only candidates because the GCD issue API can return issue metadata without usable cover URLs.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`, `inkypi-weather/package/InkyPi/tests/test_gcd_comic_covers.py`
- Tags: inkypi, gcd, comics, fit-mode, no-rotate, cover-url

## [LRN-20260530-001] correction

**Logged**: 2026-05-30T00:30:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
GCD comic covers should display as a horizontal width-fill crop by default, not as centered full-cover letterboxing.

### Details
The user corrected the comic-cover display rule after seeing the full centered portrait layout. The desired e-paper treatment is: do not rotate the source cover, scale it to the horizontal screen width, place it from the top-left of the landscape canvas, and crop overflow vertically. This avoids centered poster-style side margins.

### Suggested Action
Keep `gcd_comic_covers` default `fitMode` as `horizontal`, with aliases `width` and `full_width`. Do not revert to centered `full` contain unless the user explicitly asks for complete-cover letterboxing.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`, `inkypi-weather/package/InkyPi/tests/test_gcd_comic_covers.py`
- Tags: inkypi, gcd, comics, fit-mode, horizontal, no-rotate, width-fill

## [LRN-20260530-002] correction

**Logged**: 2026-05-30T00:45:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
GCD comic covers should default to a counterclockwise 90-degree rotation on the landscape e-paper screen.

### Details
The user clarified the intended "horizontal display" behavior: rotate the source cover image counterclockwise by 90 degrees. Previous interpretations as full-cover letterboxing or unrotated width-crop were incorrect. The verified device preview used `fitMode=rotate_ccw` and showed the comic cover rotated left into landscape orientation.

### Suggested Action
Keep `gcd_comic_covers` default `fitMode` as `rotate_ccw`. Do not change it back to `full` or `horizontal` unless the user explicitly asks for unrotated display.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`, `inkypi-weather/package/InkyPi/tests/test_gcd_comic_covers.py`
- Tags: inkypi, gcd, comics, fit-mode, rotate-ccw, landscape
