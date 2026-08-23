# Sports Dashboard data-source research: MLS

Date: 2026-08-22

## Decision

MLS can be added to the existing club-football panel without introducing a new
provider. The exact identifiers are:

| Provider | Competition identifier | Endpoint | Authentication / access |
| --- | --- | --- | --- |
| football-data.org v4 | numeric id `2145`, code `MLS` | `https://api.football-data.org/v4/competitions/MLS/matches` and `/standings` | `X-Auth-Token` required for match data; MLS currently reports `plan: TIER_TWO`, so it is not in the free 12-competition set |
| ESPN site scoreboard | league id `770`, UID `s:600~l:770`, slug `usa.1` | `https://site.web.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard` | No API key was required by the successful read, but this is an undocumented site endpoint with no published quota or SLA |

The practical default should therefore be ESPN-first for MLS schedule and live
score data. football-data.org is a useful schedule/standings source only when
the configured token is entitled to Tier 2/Standard competitions. A free token
must not be assumed to unlock MLS merely because the anonymous competition list
contains it.

Primary sources:

- football-data.org live competition list: <https://api.football-data.org/v4/competitions>
- football-data.org competition lookup table: <https://docs.football-data.org/general/v4/lookup_tables.html>
- football-data.org coverage: <https://www.football-data.org/coverage>
- football-data.org pricing: <https://www.football-data.org/pricing>
- ESPN MLS scoreboard: <https://site.web.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard>

## 2026-08-22 probes

### football-data.org

An anonymous `GET /v4/competitions` returned HTTP 200 and this MLS entry:

- `id: 2145`
- `code: "MLS"`
- `type: "LEAGUE"`
- `plan: "TIER_TWO"`
- current season id `2467`, from `2026-02-21` through `2026-11-08`
- current matchday `21`
- seven available seasons
- competition `lastUpdated: 2026-05-14T01:17:12Z`

An anonymous request to
`/v4/competitions/MLS/matches?dateFrom=2026-08-15&dateTo=2026-08-29`
returned HTTP 403, which is consistent with the documented rule that anonymous
clients may read only area and competition-list resources. No non-empty
football-data.org key was present under the aliases supported by this local
checkout's `.env`, so a paid MLS response was not probed and its MLS-specific
standings grouping must still be fixture-tested before display work.

### ESPN

The first-party scoreboard response identified the 2026 league and exposed a
whitelisted calendar of MLS match dates. During the probe, event `761745`
(LA Galaxy at CF Montréal) changed from pre-match data to an in-play payload.
The observed live payload included:

- `STATUS_FIRST_HALF`, `state: "in"`, `period: 1`, and `displayClock: "18'"`
- a 1-0 competitor score
- a goal detail at 16 minutes, including team and scorer
- live team statistics such as possession, shots on target, shots, corners,
  and fouls
- Stade Saputo plus city/country address
- national Apple TV streaming/broadcast metadata
- team form, record, IDs, abbreviations, colours, and logo URLs

This is positive evidence that the endpoint carries live MLS updates; it is not
evidence of a guaranteed refresh latency. The `site.web.api.espn.com`
scoreboard host returned the compatible JSON schema from this workstation,
while equivalent direct requests to the legacy `site.api.espn.com` host were
rejected by ESPN/Akamai with HTTP 403 `Access Denied`, even with a browser-like
user agent.
The implementation must preserve last-good cache, bounded retries, and a clear
stale/source label.

## Useful fields

### ESPN scoreboard

The fields below are present in the current MLS response and can support the
requested richer updates:

| Display need | JSON path(s) | Notes |
| --- | --- | --- |
| League/season | `leagues[0].id`, `.slug`, `.season`, `.logos`, `.calendar`; top-level `season` and `day` | The default response is day-scoped; the calendar lists match dates across the season |
| Match identity/time | `events[].id`, `.uid`, `.date`, `.name`, `.shortName`; `competitions[0].startDate` | Keep ESPN IDs provider-scoped; merge providers by league, kickoff tolerance, and team aliases |
| Live/final state | `competitions[0].status.clock`, `.displayClock`, `.period`, `.type.name`, `.type.state`, `.type.completed`, `.type.detail` | `type.state == "in"` is stronger live evidence than kickoff-window inference |
| Score/winner | `competitions[0].competitors[].homeAway`, `.score`, `.winner` | Preserve `winner` for knockout/shootout handling; a tied regulation score can still produce a match winner |
| Venue | `competitions[0].venue.fullName`, `.venue.address.city`, `.venue.address.country` | The current renderer already consumes `fullName`; city/country are available for a richer next-match line |
| Broadcast | `competitions[0].broadcasts[].market`, `.names[]`; `geoBroadcasts[].type.shortName`, `.market.type`, `.media.shortName`, `.lang`, `.region` | Prefer `geoBroadcasts` when a region/language label matters; values are market-dependent and may be absent |
| Latest event | `competitions[0].details[].type.text`, `.clock.displayValue`, `.team.id`, `.scoreValue`, `.scoringPlay`, `.redCard`, `.yellowCard`, `.penaltyKick`, `.ownGoal`, `.shootout`, `.athletesInvolved[]` | Good candidate for one compact "last update" line: goal/card plus minute and player |
| Match stats | `competitors[].statistics[]` (`name`, `abbreviation`, `displayValue`) | Fields can appear or disappear by match state; select by `name`, never by list position |
| Form/record | `competitors[].form`, `.records[].summary` | Useful for pre-match context; treat as optional |
| Availability | `playByPlayAvailable`, `playByPlayAthletes`, links whose `rel` contains `live` or `summary` | Availability flags do not guarantee that a separate endpoint will remain publicly callable |

The current parser already consumes start time, status, score, clock, venue,
broadcast labels, logos, and moneyline odds. It does not yet retain event
details, period, match statistics, address, form, or record.

### football-data.org v4

The official Match resource documents:

- schedule/context: `id`, `utcDate`, `matchday`, `stage`, `group`,
  `lastUpdated`
- status/freshness: `status`, `minute`, `injuryTime`
- place: `venue`, `attendance`
- teams: provider IDs, names, short names, TLA codes, crests
- score: `winner`, `duration`, `fullTime`, `halfTime`
- deeper match data where the subscription/data set permits it: goals,
  bookings, substitutions, lineups/bench, team statistics, referees, and odds

See the official Match resource and status workflow:
<https://docs.football-data.org/general/v4/match.html>.

The standings resource can provide `TOTAL`, `HOME`, and `AWAY` tables with
position, played games, form, wins/draws/losses, points, goals for/against, and
goal difference. The exact conference/group shape for MLS was not observable
without a Tier 2 token, so the UI must not assume a single European-style table
until a real MLS response or captured fixture confirms it. See:
<https://docs.football-data.org/general/v4/competition.html>.

football-data.org uses `null` and empty lists as valid "unknown/unavailable"
values and folds deeper information out of list views by default. Parsing must
remain optional-field-safe. Policy reference:
<https://docs.football-data.org/general/v4/policies.html>.

## Aggregate and playoff semantics

The current regular-season ESPN MLS response contained no `aggregateScore`,
`series`, or `leg` field. More importantly, MLS Round One in 2026 is a
best-of-three series in which the first club to win two matches advances; goals
are not aggregated across the series. Wild Card, Conference Semifinal,
Conference Final, and MLS Cup rounds are single-elimination. Therefore:

- do not label a sum of match scores as an MLS aggregate score;
- for Round One, show series wins such as `SERIES 1-0` only when series state
  is supplied or can be derived reliably from the matching postseason games;
- for single-elimination matches, show match score plus extra-time/shootout
  outcome, preserving the provider's match-level `winner` flag;
- make any series/aggregate field optional because neither current provider
  contract guarantees it for every MLS event.

Official 2026 playoff rules:
<https://www.mlssoccer.com/about/competition-guidelines>.

## Authentication, limits, and freshness

### football-data.org

- Requests for match/standings resources use `X-Auth-Token`.
- Anonymous access is documented as 100 requests per 24 hours and limited to
  area and competition-list resources.
- The official policy page says 10 requests/minute for Free, 30 for Standard,
  and 60 for plans above Standard. The current pricing page instead advertises
  Standard at 60 calls/minute, Advanced at 100, and Pro at 120. Because these
  two official pages disagree, use the account's current response headers/plan
  as authority, retain the repository's conservative local budget, and back
  off on 429 rather than hard-coding the marketing number.
- The Free plan explicitly has delayed scores and schedules. Current paid plans
  advertise live scores, but MLS additionally requires a plan that includes
  Tier 2 competitions (currently Standard / EUR 49 per month or higher).

Sources:

- policies/rate limiting: <https://docs.football-data.org/general/v4/policies.html>
- current pricing/features: <https://www.football-data.org/pricing>
- registration/auth overview: <https://www.football-data.org/client/register>

### ESPN

- The observed scoreboard request carried no API key.
- No public developer contract, quota, or uptime/freshness SLA for this site
  endpoint was found. Treat the JSON shape and access policy as changeable.
- The repository's existing 720-call/day guard and 60-second live cache are
  local safety policies, not ESPN guarantees. Retain a local cap and last-good
  cache; avoid more than one scoreboard request per refresh cycle.
- A successful response is not enough to mark a match live. Use provider state,
  clock/period, and fetched age, and label stale cache explicitly.

## Integration implications for this repository

1. Add MLS as its own selectable club league using ESPN slug `usa.1`; set its
   football-data competition code to `None` so the paid Tier 2 adapter remains
   explicitly disabled. Do not overload one of `PL`, `PD`, `BL1`, `SA`, or
   `FL1`.
2. Do not make a Tier 2 football-data subscription a hidden requirement. ESPN
   must remain sufficient for the basic MLS card, with standings/deep data
   treated as optional enhancement.
3. The existing ESPN URL is called without a `dates` range. Its response is
   explicitly day-scoped (`day.date`), so an ESPN-only MLS implementation can
   otherwise show no schedule on off-days. Request a bounded recent/upcoming
   window or use the returned league calendar to choose the nearest matchday,
   while keeping one request per refresh cycle.
4. For a compact e-paper update hierarchy, prefer: confirmed live score and
   clock; latest goal/card; next kickoff plus venue/broadcast; then form/record.
   Do not crowd all optional statistics into the 800x480 panel.
5. MLS has a 34-match 2026 regular season, a May 25-July 16 World Cup pause,
   and Decision Day on November 7. Do not infer "offseason" solely from a gap
   in the calendar. Official schedule reference:
   <https://www.mlssoccer.com/news/2026-mls-schedule-most-important-dates-key-info>.
6. MLS begins a summer-to-spring calendar in 2027 after a February-May 2027
   transition season. Season selection should follow provider season metadata,
   not a permanently hard-coded February-November assumption. Official notice:
   <https://www.mlssoccer.com/news/mls-to-align-calendar-with-top-leagues-around-world>.
7. Add provider fixtures for pre-match, first half, halftime, second half,
   final, postponed, shootout, and an off-day empty scoreboard. A real paid
   football-data MLS fixture is still needed before claiming standings support.
