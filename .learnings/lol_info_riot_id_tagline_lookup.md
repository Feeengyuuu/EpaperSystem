# LoLInfo Riot ID tagline lookup

## [LRN-20260603-021] insight

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, riot-api

### Summary
LoLInfo needs the full Riot ID tag line for reliable account lookup.

### Details
When looking up the North America account name from only its gameName, the common guessed Riot ID tags `NA1`, `NA`, `US1`, and `US` all returned 404 from Account-V1. The legacy Summoner-V4 by-name endpoint returned 403 on the live key, so it cannot be used as a reliable fallback for discovering the PUUID from only a display name. After the user supplied the exact tagLine `pog`, `Account-V1 /accounts/by-riot-id/.../pog` returned the account PUUID and the live LoLInfo instance refreshed successfully.

### Suggested Action
For future LoLInfo account changes, ask for the full Riot ID in the form `gameName#tagLine` before updating the live plugin instance. Use `platformRoute=na1` and `regionalRoute=americas` for North America after the exact tag is known.

### Metadata
- Source: production_probe
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `.tmp/riot_lookup_name.py`
- Tags: lol-info, riot-id, tag-line, na1, account-lookup
