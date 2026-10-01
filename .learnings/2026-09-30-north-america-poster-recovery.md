## [LRN-20260930-NAP] insight

**Logged**: 2026-09-30T17:04:12-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Persistent poster files do not ensure complete movie pages when upstream metadata has no poster and the plugin is omitted from media repair scheduling.

### Details
The North America chart used a TMDb record for Avengers Endgame: Encore (1785181) whose poster_path was null and images endpoint contained zero posters. The live chart had four readable posters out of five, with an empty poster URL/path for rank one. Its persistent directory still contained 19 images; there was no evidence that the missing movie's poster had been deleted. The same chart movie URL identified the original 2019 film on The Numbers and its detail page supplied a real poster.

The shared renderer recorded poster_status ready=4, total=5 and a retry time, but North America lacked supports_live_refresh and was absent from background LIVE eligibility. Existing movie repair tests and the previous acceptance report covered mainland repair only. New charts also rebuilt movie objects without retaining the North America chart's verified media by exact provider identity.

### Suggested Action
Use exact provider movie identity to retain verified local media, validate a source-owned poster fallback when TMDb lacks one, and connect the actual North America instance to bounded media-only LIVE repair. Prove repair changes the media/canonical image while keeping chart facts, generated_at and DATA freshness unchanged; then verify the physical display. Do not describe a missing metadata URL as a cache eviction without file history.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/china_box_office_top_movies/china_box_office_top_movies.py, inkypi-weather/package/InkyPi/src/plugins/box_office_top_movies/box_office_top_movies.py, inkypi-weather/package/InkyPi/src/runtime/background_live.py
- Tags: movie-posters, retained-media, provider-identity, live-repair, source-freshness
- Pattern-Key: harden.poster_completeness_independent_of_data_success
- Recurrence-Count: 1
- First-Seen: 2026-09-30
- Last-Seen: 2026-09-30

### Resolution
- **Resolved**: 2026-09-30
- **Notes**: Added provider-identity media retention, bounded missing-only repair, a validated The Numbers fallback and the North America background LIVE integration. Combined relevant regression: 275 passed, 1 skipped; architecture and Ruff checks passed. Device acceptance remains a separate release gate and is recorded under outputs/north-america-posters-20260930.
- **Parser evidence**: The real movie page has a site-wide og:url pointing to the publisher homepage. Ignore only this exact default OG value; continue rejecting mismatched explicit canonical, movie-level OG, response URL, heading, image path or alt identity. Test real publisher markup before relying on idealized fixtures.
