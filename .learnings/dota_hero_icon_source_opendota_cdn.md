# Dota Hero Icon Source Uses OpenDota Metadata And Steam CDN Assets

## Learning
The `Austinparisi42/Dota2HeroDatabase` page does not ship its own hero portrait archive. Its JavaScript calls `https://api.opendota.com/api/heroStats` and reads each hero's `icon` and `img` fields.

## Context
- Repo inspected: `Austinparisi42/Dota2HeroDatabase`
- Useful endpoint: `https://api.opendota.com/api/heroStats`
- Current working asset base: `https://cdn.cloudflare.steamstatic.com`
- Old repo JS asset base: `https://api.opendota.com`

## Recommended Pattern
- Use OpenDota `heroStats` for hero metadata.
- For relative `icon` or `img` paths, try `https://cdn.cloudflare.steamstatic.com` first.
- Keep `https://api.opendota.com` as a fallback only, because the old `api.opendota.com + icon` URL returned 404 during the 2026-06-03 local probe.

## Verification
`/api/heroStats` returned 127 heroes. The first hero had `icon=/apps/dota2/images/dota_react/heroes/icons/antimage.png?`; the Steam CDN URL returned HTTP 200 while the old OpenDota asset URL returned 404.
