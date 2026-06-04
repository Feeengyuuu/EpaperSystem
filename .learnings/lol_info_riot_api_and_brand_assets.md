# LoLInfo Riot API Probe And Brand Asset Handling

## Learning
Riot's current League API can build an account dashboard without the older encrypted summoner id. Use Riot ID to PUUID, then PUUID-based Summoner, League, Mastery, Match, Challenges, and Spectator endpoints.

## Context
- Plugin: `lol_info`
- Live key variable observed: `Riot_KEY`
- Probe account: `Hide on bush#KR1`
- User-provided assets: `league-of-legends-logo.png`, `riot-games-logo.png`

## Recommended Pattern
- Account: `/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}` on regional route.
- Summoner: `/lol/summoner/v4/summoners/by-puuid/{puuid}` on platform route.
- Ranked: `/lol/league/v4/entries/by-puuid/{puuid}`.
- Mastery: `/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top`.
- Recent matches: `/lol/match/v5/matches/by-puuid/{puuid}/ids` then `/lol/match/v5/matches/{matchId}`.
- Optional: `/lol/challenges/v1/player-data/{puuid}`, `/lol/spectator/v5/active-games/by-summoner/{puuid}`, `/lol/status/v4/platform-data`.
- Use Data Dragon `zh_CN` champion data for Simplified Chinese champion names and icons.
- The provided Riot logo file has an opaque light/checkerboard background; remove near-white pixels and tint the remaining mark before rendering on dark UI.

## Verification
The live key returned 200 for account, summoner, league-by-puuid, mastery, match ids/details, challenges, rotations, and platform status. Spectator returned 404 for the probe account, which should be treated as "not in game". Local LoLInfo preview rendered with both provided logos.
