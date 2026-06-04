# SpaceX API plugin fit

## [LRN-20260603-006] source-fit

**Logged**: 2026-06-03
**Priority**: medium
**Status**: proposed
**Area**: epaper, space, media-plugins

### Summary
There is no dedicated SpaceX plugin in the current InkyPi plugin list. The closest reusable patterns are `apod`, `natgeo_photo_of_the_day`, `image_url`, and shared image-loading/crop helpers.

### Source Fit
- The public r/SpaceX API exposes launch metadata and built-in image links such as mission patches and Flickr originals, so a SpaceX display should use real source imagery instead of AI-drawn or hand-drawn rockets.
- Good plugin direction: `spacex_launches` or `spacex_mission_poster`, rendering a mission patch or Flickr launch photo plus launch name, date, rocket, launchpad, success/upcoming state, webcast, and Wikipedia/article link metadata.
- Treat `https://api.spacexdata.com` as useful for historical SpaceX mission cards, but verify freshness before relying on it for current/latest launches. A 2026-06-03 probe of `/v5/launches/latest` returned `Crew-5` from 2022, so current-launch behavior may require a fresher source such as Launch Library 2.

### Reuse Guidance
- Reuse the APOD/NatGeo style of real-image-first composition.
- Keep short timeouts, cache downloaded images, and provide a text/patch fallback when Flickr originals are missing.
- Do not require an API key unless the chosen upstream source needs one; r/SpaceX public endpoints are unauthenticated.

### References
- r/SpaceX API docs: `https://docs.spacexdata.com/`
- r/SpaceX API GitHub: `https://github.com/r-spacex/SpaceX-API`
- SpaceX latest launch probe: `https://api.spacexdata.com/v5/launches/latest`
