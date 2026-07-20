# LiveRadar Source Health Design

**Date:** 2026-07-19
**Status:** Approved direction; written review pending
**Scope:** `live_radar` status acquisition, live media refresh, source health, and Twitch credentials

## Problem

The production `LiveRadar` instance monitors 65 rooms across Bilibili, Douyu,
and Twitch on a Raspberry Pi in the United States. Today all rooms first pass
through `https://liveradar.pages.dev/api/status/batch`. A failed multi-room
batch falls back to individual requests, and Bilibili failures then trigger a
second direct repair path. This preserves freshness, but a real refresh can
take 83-149 seconds and can occupy most of the 120-second refresh cadence.

The design must reduce status request count and isolate provider failures
without weakening two existing user-facing guarantees:

1. fresh status is preferred over a short generic timeout; and
2. while a streamer is live, the displayed live screenshot continues to
   refresh instead of becoming a permanent cached image.

## Goals

- Query Twitch live state through Twitch's official Helix API in one batch.
- Query Bilibili live state through the existing direct APIs while avoiding
  repeated room-ID-to-UID lookups.
- Keep Douyu working without requiring a new approved Douyu developer project.
- Prevent one platform's outage from forcing all three platforms into the same
  slow fallback path.
- Keep live screenshots fresh on the configured screenshot TTL.
- Preserve the last valid status and media independently when a source fails.
- Record enough per-platform health data to diagnose partial degradation.
- Store Twitch credentials only in the device secret environment.

## Non-goals

- Do not silently delete or rewrite saved room entries.
- Do not introduce an Asia-hosted proxy or a new always-on service.
- Do not scrape Twitch when official credentials are available.
- Do not require Douyu Aid/Secret, an IP whitelist, or project approval in this
  change.
- Do not redesign the existing LiveRadar card layout or room ordering.

## Evaluated Approaches

### 1. Tune the existing aggregate request path

Keep all rooms on `liveradar.pages.dev`, increase timeouts, and extend caches.
This is low risk but does not remove the central dependency or the 65-room
individual fallback. It also risks making screenshots stale.

### 2. Platform-isolated hybrid acquisition (selected)

Use Twitch Helix for Twitch, cached direct Bilibili batch lookup for Bilibili,
and the current aggregate service for Douyu. Each platform has its own fallback
and health state. This removes most redundant requests without adding another
service or requiring Douyu approval.

### 3. Self-host a cross-platform proxy near mainland China

This could reduce China-platform latency, but it adds deployment, monitoring,
credentials, and provider-compliance work. It is disproportionate for one
display and remains an optional future step if direct operation is still too
slow after approach 2.

## Architecture

### Room partitioning

The existing parser remains authoritative. After parsing and de-duplicating the
saved room list, acquisition partitions it into `twitch`, `bilibili`, `douyu`,
and unknown-platform groups. Results are merged back in configured room order
before existing card sorting is applied.

One platform failure must never discard successful results from another
platform.

### Twitch provider

The Twitch provider uses an app access token obtained with the OAuth client
credentials grant. The token is held in memory until shortly before expiry and
is never written to the plugin cache.

- `GET https://api.twitch.tv/helix/streams` receives all configured
  `user_login` values in one request.
- A missing stream in the response is an offline state, not an error.
- Live rows use the official response fields for title, category, viewer count,
  start time, and `thumbnail_url`.
- Profile/avatar data may use the official users endpoint and a one-day media
  cache; it must not delay status completion.
- Missing or rejected credentials fall back to the existing aggregate provider
  only for Twitch rooms.

Twitch's documented maximum page size is 100, so the current 13 Twitch rooms
fit in one request.

### Bilibili provider

The existing direct Bilibili repair path becomes the preferred Bilibili path.

1. Resolve `room_id` to `uid` through `room/v1/Room/get_info` only when the
   mapping is absent or expired.
2. Persist successful mappings for seven days.
3. Negative-cache definitive missing-room responses for six hours; preserve
   the room entry and report it in health state.
4. Query all resolved UIDs with one
   `room/v1/Room/get_status_info_by_uids` request (chunk limit 50).
5. If the direct batch is unavailable, fall back to the aggregate provider for
   Bilibili rooms, then to the last trusted per-room status.

The mapping cache is data-only and contains no credentials.

### Douyu provider

Douyu remains on `liveradar.pages.dev` because the official open platform
requires developer registration, Aid/Secret, IP allow-listing, and per-project
approval.

- Send Douyu rooms in platform-only chunks of at most ten.
- Retain the five-minute multi-room circuit breaker.
- On a batch failure, retry only the affected Douyu rooms individually.
- Preserve last trusted per-room results when the provider remains unavailable.

### Unknown platforms

Unknown platform entries retain the existing aggregate behavior and may not
affect the health result of known platforms.

## Live Screenshot and Media Contract

Status and media are separate phases with separate failure semantics.

- Status acquisition completes and is cacheable before optional media work.
- When `showSnapshots=true` and a room is live, every displayed live card that
  needs a screenshot checks the configured `snapshotCacheSeconds` value.
- At the current default of 60 seconds, a live screenshot older than 60 seconds
  is fetched again on the next eligible render, even when the streamer and
  stream title have not changed.
- LiveRadar advertises live-refresh support. Its side-effect-free scheduler hook
  reads only the matching warm status cache; while that cache contains at least
  one successful live room, the displayed instance refreshes at the shorter of
  `cacheSeconds` and `snapshotCacheSeconds`. The hook becomes inactive again
  when the refreshed cache contains no successful live room.
- Only screenshots needed by the current large-live and mini-live cards are
  refreshed. Offline-room images are not refreshed on the fast status path.
- A failed screenshot request reuses the last valid screenshot or the existing
  placeholder. It does not turn a live room offline, fail the platform status,
  or overwrite a valid media file.
- Avatar refresh keeps its longer cache and cannot block live status success.
- Theme-only redraws remain network-free and may use stale media.

This contract makes the media TTL actionable while the dashboard is displayed,
preserves continuously updating live imagery, and removes unnecessary media
requests for rooms that are not currently visible or live.

## Cache and Provenance

The plugin keeps three independent classes of state:

1. room status, keyed by platform and room identifier;
2. Bilibili room-to-UID mappings and negative entries; and
3. media files with their own avatar and screenshot TTLs.

A refresh merges only successful room updates. A failed room does not erase its
last trusted status. Every merged row retains a provenance value such as
`TWITCH_HELIX`, `BILIBILI_DIRECT`, `LIVERADAR_BATCH`,
`LIVERADAR_INDIVIDUAL`, or `FRESH_CACHE`.

An all-error refresh remains non-cacheable and preserves the last good rendered
page. A mixed fresh-plus-cached result is cacheable only when every displayed
live status is either fresh or within the existing accepted cache age.

## Health State and Logging

After each data refresh, write a bounded plugin-local health document and one
structured summary log. Per platform it records:

- selected source and fallback source;
- request count and elapsed milliseconds;
- configured, fresh, cached, and failed room counts;
- last full success and last partial success timestamps;
- failed room identifiers and normalized error categories; and
- media refresh attempts, successes, and failures.

Health output must never include client secrets, OAuth tokens, cookies, or raw
authorization headers. Repeated identical warnings are summarized instead of
emitted once per room.

## Credentials

Add `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` to the canonical secret
schema and regenerate `.env.example` and the installer API-key registry.

Production values are installed in `/etc/inkypi/inkypi.env` through the
existing protected credential path. Tests use placeholders. No credential,
token, or live value is committed to Git or stored in image metadata.

## Failure Handling

- HTTP requests use the shared bounded session and task context.
- Authentication failure invalidates the in-memory Twitch token once, obtains a
  new token, and retries the Helix request once.
- Rate-limit responses honor provider reset/backoff information and fall back
  without a retry storm.
- The existing long LiveRadar task budget remains available for cross-border
  recovery; this design reduces work rather than imposing a short global
  cutoff.
- A definitive invalid Bilibili room is quarantined temporarily, never deleted.
- Scheduler completion and queue advancement remain mandatory after both full
  and partial success.

## Tests

Implementation follows test-driven development. Required regressions include:

- one Twitch token request is reused until expiry;
- all configured Twitch logins are queried in one Helix request;
- missing Twitch credentials and authorization errors fall back only Twitch;
- Bilibili UID mappings are reused and definitive misses are negative-cached;
- one platform failure preserves other platforms' fresh results;
- Douyu batch failure enters the existing circuit breaker and affects only
  Douyu rooms;
- a live screenshot is fetched again after `snapshotCacheSeconds`;
- a screenshot failure preserves the previous image and live status;
- theme-only rendering performs no provider or media requests;
- health output contains counts and normalized errors but no secrets;
- secret schema compatibility artifacts remain synchronized.

Run focused LiveRadar and secret-schema tests, then the complete InkyPi suite.

## Live Acceptance

On `ColoredEpaperFrame`:

1. the saved 65-room list is preserved;
2. Twitch uses one Helix stream-status request;
3. warm Bilibili refresh avoids repeated room-to-UID lookups and uses batched
   UID status requests;
4. Douyu failures cannot force Twitch or Bilibili into individual aggregate
   retries;
5. a real data job reaches terminal success and the refresh queue advances;
6. a live streamer screenshot is re-requested after its 60-second TTL and the
   refreshed valid media is used on a later display;
7. media failure demonstrably preserves the previous screenshot and live state;
8. no credential appears in logs, repository diffs, cache JSON, or rendered
   metadata; and
9. the service remains ready with zero unexpected restarts.

The expected normal-path data duration is below the current 120-second cadence.
If cross-border fallback exceeds that cadence, it may continue within the
existing task deadline, but it must coalesce duplicate work and release the
single worker when complete.

## External References

- Twitch Get Streams API: <https://dev.twitch.tv/docs/api/reference#get-streams>
- Twitch client credentials flow:
  <https://dev.twitch.tv/docs/authentication/getting-tokens-oauth#client-credentials-grant-flow>
- Twitch API rate limits: <https://dev.twitch.tv/docs/api/guide#twitch-rate-limits>
- Douyu developer onboarding: <https://open.douyu.com/source/>
- Bilibili open live documentation: <https://open-live.bilibili.com/>
