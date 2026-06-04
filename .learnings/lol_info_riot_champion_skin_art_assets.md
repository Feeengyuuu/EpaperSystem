# LoLInfo Riot champion skin art assets

## [LRN-20260604-004] insight

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, riot-api, assets

### Summary
LoLInfo can use Riot Data Dragon for champion splash art, loading art, and skin art.

### Details
Riot's authenticated player APIs return account, match, ranked, mastery, and champion identifiers, but champion artwork is provided through Data Dragon static assets. Individual champion JSON files contain a `skins` array with each skin's `num`; splash art uses `/cdn/img/champion/splash/{ChampionKey}_{num}.jpg`, and loading art uses `/cdn/img/champion/loading/{ChampionKey}_{num}.jpg`. Some skin entries are chromas and may not have separate splash images; entries with `parentSkin` should be treated as chromas.

### Suggested Action
For future LoLInfo visual expansions, use Match-V5 or mastery data to choose champion keys, then use Data Dragon champion detail JSON to list skin `num` values. Cache splash/loading images locally and fall back to champion square icons if an art URL is missing or blocked. Do not imply the match API exposes the exact cosmetic skin used by the player unless verified separately.

### Metadata
- Source: official_riot_docs
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`
- Tags: lol-info, riot-api, data-dragon, splash-art, skin-art, champion-assets
