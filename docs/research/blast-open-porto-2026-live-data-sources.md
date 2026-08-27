# Sports Dashboard data-source research: BLAST Premier Open Porto 2026

Date: 2026-08-26 (America/Los_Angeles)

## Decision

BLAST Premier Open Porto 2026 can technically be added to SportsDashboard with
current schedule, bracket, explicit live/final state, best-of-series score, and
map round score from BLAST's own website backend.

The production implementation does **not** call that website-internal backend.
It uses PandaScore's documented, authenticated tournament-match endpoint for
the three Porto stages and limits the free-plan claim to schedule, provider
reported running/final state, and best-of-series score. This avoids treating
anonymous technical reachability as a redistribution licence. The BLAST probe
below remains research evidence and a possible future option only after written
permission.

The strongest observed source is:

- <https://api.blast.tv/v2/games/cs/tournaments/open-2026-season-2/brackets>

It returned HTTP 200 without a token, cookie, account, or custom header and
contained `isLive`, `isCompleted`, series scores, map scores, teams, UTC times,
bracket progression, and stream links. This is first-party data and is a good
technical fit for the compact e-paper card.

However, this is an undocumented website-internal endpoint, not a published
developer API. BLAST publishes no schema-stability promise, rate limit, cache
contract, freshness target, or uptime SLA for it. More importantly, BLAST's
website terms prohibit reverse engineering and copying or exploiting website
content. A durable/public integration should therefore obtain BLAST's written
permission or use a licensed documented provider. The endpoint can be isolated
as an experimental provider, but its current technical accessibility must not
be described as permission to redistribute or as an API contract.

For a documented alternative, PandaScore's free Fixtures plan can provide
schedule, running-match discovery, and results after token registration. It is
not a free in-map live feed: current round score, clock, and player state require
a paid Live plan. PandaScore also requires source attribution and a private
server-side token.

## What "live" means here

The sources support different levels of freshness and must not share one generic
`LIVE` claim:

| Capability | BLAST internal bracket endpoint | PandaScore free Fixtures | PandaScore Live Basic/Pro |
| --- | --- | --- | --- |
| Future schedule and teams | Yes | Yes | Yes |
| Explicit match running/final state | Yes: `isLive`, `isCompleted` | Running-match endpoint and match lifecycle | Yes |
| Best-of series score | Yes: `teamAScore`, `teamBScore` | Match results/lifecycle | Yes |
| Current/completed map round score | Present as map `teamAScore`, `teamBScore`; live latency was not measured | Not a documented free-plan guarantee | Yes |
| Round clock, CT/T side, bomb/paused state | Not in the bracket response | No | WebSocket frames, about every two seconds |
| Kill feed and detailed player state | No | No | Pro Live for play-by-play and all fields |
| Published freshness/SLA | No | No instant-update guarantee | Stream-synchronised, still subject to provider/event delay |

The practical SportsDashboard requirement is the first four rows, not a
tick-by-tick broadcast overlay. BLAST's endpoint is sufficient for a card such
as `LIVE - MAP 2 - G2 1-0 AURORA - 8-6`, provided the response is fresh. It is
not evidence for a kill feed or exact round clock.

## Primary-source probes on 2026-08-26

### Official tournament page

Official event page:
<https://blast.tv/cs/tournaments/open-2026-season-2>

The page identifies the event as Counter-Strike 2, August 26 through September
6, 2026, with 16 teams and a USD 1.1 million prize pool. Its page environment
declares `https://api.blast.tv` as `API_BASE_URL`, and its shipped bracket client
calls `v2/games/{game}/tournaments/{tournament}/brackets`:

- current bracket client asset (build-hashed and therefore replaceable):
  <https://blast.tv/ssr-assets/2024-10-15/useGetTournamentBrackets-BrTByxpC.js>
- official event announcement and venue split:
  <https://blast.tv/cs/news/blast-reveal-porto-open-for-2026>

The client-side schema validates the exact match fields described below and
uses a five-second React Query `staleTime`. That value is browser cache policy,
not a documented polling allowance or source freshness SLA.

### Official BLAST tournament endpoints

Observed endpoints:

| Purpose | URL | 2026-08-26 observation |
| --- | --- | --- |
| Event metadata | <https://api.blast.tv/v2/games/cs/tournaments/open-2026-season-2> | HTTP 200, no authentication; name, date range, location, prize pool, stages and circuit metadata |
| Complete bracket | <https://api.blast.tv/v2/games/cs/tournaments/open-2026-season-2/brackets> | HTTP 200, no authentication; 3 stage objects and 29 match nodes, including future TBD nodes |
| Known-team matches | <https://api.blast.tv/v2/games/cs/tournaments/open-2026-season-2/matches> | HTTP 200, no authentication; 12 currently materialised match records |
| One match by short ID | <https://api.blast.tv/v2/games/cs/matches/88837129> | HTTP 200, no authentication; teams, stage, series score, maps and stream URL |
| Currently active BLAST broadcasts | <https://api.blast.tv/v1/broadcasts/live> | HTTP 200 and `[]` after day-one play had ended; broadcast state is not a match-status authority |

At 2026-08-26 22:14 PDT / 2026-08-27 05:14 UTC, the bracket response had:

- stage labels `Group A`, `Group B`, and `Playoffs`;
- 29 total bracket match nodes;
- 12 nodes with both teams known;
- 4 completed matches;
- 0 explicitly live matches, consistent with day-one play being over;
- no `Cache-Control`, `ETag`, or rate-limit header exposed in the response.

Three additional unauthenticated probes completed in 646-1,083 ms from this
workstation and returned the same 20,746-byte body. This shows that minute-scale
server-side polling was technically reachable during the observation; it is not
an availability, latency, or safe-rate guarantee.

The four completed day-one series in that response were:

| Series | Series result | Maps |
| --- | --- | --- |
| Aurora vs G2 | 0-2 | Inferno 5-13; Anubis 12-16 |
| Spirit vs DENDELE | 2-0 | Ancient 13-8; Cache 13-7 |
| Natus Vincere vs M80 | 1-2 | Anubis 13-4; Mirage 6-13; Inferno 6-13 |
| FURIA vs paiN | 2-0 | Mirage 13-2; Cache 13-6 |

No match was in progress during the probe, so the probe did not measure how
many seconds elapse between a game-server round and a bracket-score update. The
presence of explicit live fields and the fact that the official page consumes
the endpoint establish that it is the site's live-state source; they do not
establish a fixed latency guarantee.

### BLAST bracket response contract as observed

Top-level stage object fields:

- `tournamentUuid`, `tournamentName`, `parentTournamentName`;
- `circuitName`, `startDate`, `endDate`, `index`, `label`, `format`;
- `numberOfTeams`, `metadata`, and `matches`.

Match fields:

- identity/context: `uuid`, `type`, `index`, `name`, `timeOfSeries`;
- participants: `teamA`, `teamB` with UUID, name, shorthand and location;
- best-of score: `teamAScore`, `teamBScore`;
- lifecycle: `isLive`, `isCompleted`;
- bracket links: `winnerGoesTo`, `loserGoesTo`;
- presentation: `metadata.externalStreamUrl`;
- current/completed maps: `maps`.

Map fields:

- `uuid`, `name`, `scheduledStartTime`, `actualStartTime`, `matchEndedTime`;
- `teamAScore`, `teamBScore`, and `externalId`.

Fields are optional in practice. For example, one completed Aurora-G2 map had a
null `matchEndedTime`, although its score and the series `isCompleted` flag were
final. Do not infer series completion solely from every map having an end time.

The `/matches` response is useful for rich tournament/stage/team context, but it
does not expose the explicit `isLive` and `isCompleted` fields present in the
bracket response and currently omits future matches whose teams are still TBD.
Use `/brackets` as the lifecycle authority and treat `/matches` as optional
enrichment, not as the only schedule.

## Access, authentication, and legal boundary

The direct probes sent no credential and received HTTP 200. A browser `Origin`
header was reflected in `Access-Control-Allow-Origin`, and server-side access
from the Pi does not require CORS in any event. There was no published quota or
rate-limit response header.

Technical access is not the same as a licence. BLAST's current Terms of Use:

- say website intellectual-property and database rights belong to BLAST or its
  licensors;
- prohibit reverse engineering the website;
- prohibit copying or exploiting any part of the website or its content;
- provide no public API licence or automated-use exception.

Source: <https://blast.tv/privacy-policy>

Consequently:

1. Do not call this a public/open API in code or UI; call it an undocumented
   first-party website endpoint.
2. Do not mirror raw responses, offer them to third parties, or expose the
   endpoint through our own API.
3. For a private personal e-paper display, keep traffic minimal and request
   written permission from BLAST before treating the integration as durable.
4. If permission is not obtained, use a provider subscription whose terms
   explicitly cover the intended display.

This is a product/legal risk classification, not legal advice.

## Documented provider alternatives

### PandaScore

PandaScore documents the following Counter-Strike endpoints as available on all
plans, including its free Fixtures plan:

- `GET https://api.pandascore.co/csgo/matches/upcoming`
- `GET https://api.pandascore.co/csgo/matches/running`
- `GET https://api.pandascore.co/csgo/matches/past`
- `GET https://api.pandascore.co/csgo/tournaments/running`

Primary documentation:

- getting started and lifecycle:
  <https://developers.pandascore.co/docs/getting-started>
- Counter-Strike plan reference:
  <https://developers.pandascore.co/docs/plan-reference>
- running CS matches endpoint:
  <https://developers.pandascore.co/reference/get_csgo_matches_running-1>
- authentication:
  <https://developers.pandascore.co/docs/authentication>
- rate limits:
  <https://developers.pandascore.co/docs/rate-and-connections-limits>
- current pricing:
  <https://www.pandascore.co/pricing>
- terms:
  <https://www.pandascore.co/terms-and-condition>

Current access model:

- every REST request requires a private token; use a server-side Bearer header,
  never a client-visible query string;
- free Fixtures: EUR 0 per videogame per month and 1,000 requests/hour;
- Historical: from EUR 400 per videogame per month;
- Live Basic: from EUR 1,000 per videogame per month, WebSocket frames about
  every two seconds for CS, and at most three connections per live match;
- Live Pro: contact sales for play-by-play and all live fields.

PandaScore's free running-match endpoint is not equivalent to its Live API. The
free match record can expose schedule, lifecycle and series result; the plan
reference reserves games within a match for Historical and reserves live frames
such as map round score, side, clock, bomb/paused state and player data for Live
Basic or Pro.

PandaScore requires `Source: PandaScore` on any medium reproducing its data and
forbids sharing the token or raw database access. Its terms also do not promise
instant updates. A public page that labels this exact Porto event as sourced
from PandaScore exists, which is supporting evidence of fixture coverage, but
the exact tournament record was not independently queried because no account
token was used in this research:
<https://ematchboard.com/csgo/tournament/21715>.

The public PandaScore-attributed event pages identify the three tournament
records used by the adapter:

- Group A: `21714`
- Group B: `21715`
- Playoffs: `21716`

Those IDs remain configurable because they are provider identities, and every
returned match is additionally checked for `BLAST`, `Porto`, and `2026` before
it can become a display card.

### Liquipedia

Liquipedia currently has the exact event and is useful for low-frequency
schedule/result fallback:
<https://liquipedia.net/counterstrike/BLAST/Open/2026/Fall>.

Its official API terms require caching/reuse and CC BY-SA attribution, prohibit
automated access to generated HTML, limit approved LiquipediaDB access to 60
requests/hour, and limit MediaWiki API traffic to one request per two seconds
(`action=parse` at most once per 30 seconds):

- API product/access: <https://liquipedia.net/api>
- API terms: <https://liquipedia.net/api-terms-of-use>

Liquipedia data is community edited and carries no live-score latency SLA. It
should not be the sole authority for an `isLive` flag or a current map score.

### Official licensed granular data

BLAST announced an exclusive official live-data partnership with Bayes Esports
for 2023-2024. That announcement proves a licensed official-data channel
existed, but it does not prove 2026 Porto availability because the stated term
ended in 2024:
<https://assets-global.website-files.com/60eee76747e116928d7aea30/63c98701b2189e3aee94f81c_BLAST%20Announcement%20%281%29.pdf>.

Abios announced in 2024 that its Bayes partnership covered official real-time
BLAST Premier data. Current event coverage, rights, and price must be confirmed
with sales before depending on it:
<https://abiosgaming.com/press/bayes-esports-official-data-partnership/>.

This is the appropriate path if contractual official live-data rights and
granular round/player data matter more than cost. It is excessive for a
once-per-minute personal e-paper card unless BLAST declines direct permission.

## Recommended SportsDashboard integration contract

The shipped PandaScore path follows this contract:

1. Read `PANDASCORE_API_KEY` only from the server-side secret store and send it
   as a Bearer header, never in a URL or saved plugin setting.
2. Request `GET /tournaments/{id}/matches` for Group A, Group B, and Playoffs,
   with a 100-record page limit, a 2 MiB hard response cap per stage, and a
   shared 45-second deadline that also bounds provider `Retry-After` waits.
3. Treat PandaScore's explicit `running`, `finished`, and `not_started` states
   as authoritative; never infer `LIVE` merely because kickoff has passed.
4. Persist a refresh window for each tracked match: wake automatically 30
   minutes before its scheduled start, poll every 180 seconds, and stop no later
   than 12 hours after the scheduled start. Cache non-urgent schedules for 15
   minutes. A render does not need to happen inside the pregame window for the
   background hook to wake.
5. Refresh Group A, Group B, and Playoffs independently. Retain each failed
   stage's last-good data, record partial coverage, and only call the displayed
   match fresh when its own stage succeeded in the current request cycle.
6. Keep stale scores visibly stale while allowing bounded network-error retries.
   Authentication failures, a missing key, and exhausted request budgets do not
   start the fast retry loop.
7. Display `PANDASCORE DATA` plus `SOURCE: PANDASCORE`; reserve the `LIVE` badge
   for PandaScore's explicit `running` lifecycle. Free Fixtures data is
   presented as series-level status/result only; map-round clocks and
   play-by-play are not claimed.
8. Do not treat an all-finished snapshot as proof that PandaScore has finished
   creating later bracket rounds. Back off non-live polling to hourly after the
   official event end, then use a fixed September 9 UTC safety cutoff to stop
   routine requests and archive any unfinished cache.
9. Refresh only the SportsDashboard image cache in the background. Do not use
   provider polling to trigger an additional e-paper write.

The following separate contract applies only if BLAST later grants permission
for its internal endpoint:

If the BLAST endpoint is approved for this use, implement it as a small,
provider-isolated adapter:

1. Configure tournament slug `open-2026-season-2`; do not scrape HTML.
2. Fetch `/brackets` once per background data refresh. Do not fetch providers
   during playlist display and do not cause an additional e-paper write.
3. During an explicitly live match, use a 30-60 second poll interval. Outside
   live play, back off to 10-30 minutes; after September 6, stop routine polling.
   Honour 429/5xx with exponential backoff even though no quota is published.
4. Normalize provider-scoped IDs, UTC timestamps, stage/round, teams, BO type,
   series score, current map name/score, explicit lifecycle, stream URL,
   `fetched_at`, source URL, and response age.
5. Lifecycle precedence: `isCompleted == true` means final;
   `isLive == true && isCompleted != true` means provider-confirmed live;
   otherwise a valid future `timeOfSeries` is upcoming. Never infer live solely
   because scheduled kickoff has passed.
6. Treat missing/TBD teams, null map times, empty maps and partially populated
   future nodes as valid states. Do not invent teams or map names.
7. Persist last-good data. On failure, show a clearly aged `BLAST STALE` card;
   never turn an old live score into a fresh `LIVE` card.
8. Keep official provider names as identity keys. Apply local display aliases
   only after normalization; do not silently rewrite `DENDELE` to another brand.
9. Add fixtures for upcoming, live before map creation, live map, between maps,
   completed 2-0/2-1, overtime score, TBD bracket, postponed/delayed match,
   upstream schema error, timeout and stale-cache recovery.
10. Keep PandaScore or an approved LiquipediaDB source behind the same
    normalized contract so the first-party endpoint can be replaced without
    renderer changes.

## Go/no-go summary

- **Data availability: GO.** First-party schedule, explicit live/final state,
  series score, map score, teams and bracket progression are programmatically
  accessible now.
- **Live granularity: GO for an e-paper scoreboard, not for play-by-play.** The
  endpoint is adequate for minute-scale status/score cards; exact latency was
  not measured because no match was live during the probe.
- **Engineering stability: CONDITIONAL.** The endpoint is undocumented and has
  no schema, quota or SLA guarantee; isolate it and retain last-good cache.
- **Usage rights: NEEDS PERMISSION OR A LICENSED PROVIDER.** Public reachability
  alone does not override BLAST's website terms.
