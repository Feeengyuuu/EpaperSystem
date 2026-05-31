# Steam Daily Art solid-color source images

## [LRN-20260529-114] best_practice

**Logged**: 2026-05-29T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Steam Daily Art can receive valid HTTP 200 artwork that is effectively a solid-color placeholder, especially from `library_hero.jpg`.

### Details
During Steam Daily Art diagnosis, the current Steam frontpage pool included Top Sellers app `3892270` (`Gamble With Your Friends`) whose `https://cdn.cloudflare.steamstatic.com/steam/apps/3892270/library_hero.jpg` decoded as a 1920x620 image with mean RGB around `(190, 64, 65)` and zero pixel range after sampling. The plugin accepted it because `_download_image(...)` only validates HTTP status and image decoding, then `_download_first_available_image(...)` stops at the first successful candidate.

### Suggested Action
Add source-image quality validation before accepting a Steam artwork candidate: reject near-solid images, very low-detail images, or extreme single-color placeholders, then continue to `large_capsule_image`, `header_image`, or `capsule_616x353` for the same app before selecting another item.

### Metadata
- Source: investigation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/steam_daily_art/steam_daily_art.py`
- Tags: inkypi, epaperpod, steam-daily-art, image-fetching, source-validation
