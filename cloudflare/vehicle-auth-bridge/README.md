# Vehicle Auth Bridge

Private, read-only Cloudflare Worker for the EpaperSystem vehicle-status plugin.
Tesla client credentials and user refresh tokens never leave Cloudflare. The
Worker exposes only a sanitized fixed-schema summary to the e-paper device.

Tesla Partner registration and the public-key lookup both returned HTTP `200`
for this exact hostname on 2026-08-05:

- Origin: `https://epaper-vehicle-bridge.superxfy.workers.dev`
- OAuth callback: `https://epaper-vehicle-bridge.superxfy.workers.dev/oauth/callback`
- Public key: `https://epaper-vehicle-bridge.superxfy.workers.dev/.well-known/appspecific/com.tesla.3p.public-key.pem`

Do not change the hostname or public key without planning a Tesla domain and
key migration.

## Security model

- `BRIDGE_ADMIN_TOKEN` can only mint a short-lived, one-time OAuth launch URL.
  It belongs on the operator workstation and must never be copied to the Pi.
- `BRIDGE_READ_TOKEN` can only read `/api/vehicle-summary`. This is the sole
  bridge credential installed on the Pi.
- Tesla client ID/secret and the two bridge tokens are Cloudflare Secrets.
- Tesla user tokens and cached summaries are encrypted with AES-256-GCM before
  being stored in Durable Object SQLite. `TOKEN_ENCRYPTION_KEY_V1` is a
  separate 32-byte Cloudflare Secret.
- OAuth launch, state, and browser tokens are random, single-use, short-lived,
  and stored only as hashes where applicable.
- Redirects from Tesla endpoints are rejected and response bodies are bounded.
- The Worker has no OAuth scope or route for waking the vehicle or sending
  vehicle commands. The read-only `vehicle_location` scope is used only for
  schema v3 location data after explicit Tesla authorization.
- Schemas v1 and v2 do not expose location. Schema v3 exposes only a bounded
  coordinate pair and its capture age to the bearer-protected Pi endpoint. It
  never exposes VIN, Tesla vehicle IDs, raw provider responses, tokens,
  provider URLs, routes, or media.

## HTTP surface

- `GET /.well-known/appspecific/com.tesla.3p.public-key.pem` — public Tesla
  verification key.
- `POST /v1/oauth/launch` — requires the admin bearer; returns a 120-second
  one-time authorization URL.
- `GET /oauth/start?launch=...` — consumes the launch and redirects the same
  browser to Tesla consent.
- `GET /oauth/callback` — validates state and browser binding, exchanges the
  code server-side, stores rotated tokens, and removes code/state from the URL.
- `GET /oauth/result?status=connected` — generic completion page with no
  credentials.
- `GET /api/vehicle-summary` — requires the read bearer and returns the strict
  schema-v1 compatibility summary.
- `GET /api/vehicle-summary?schema_version=2` — returns the expanded fixed-key
  telemetry summary.
- `GET /api/vehicle-summary?schema_version=3` — returns schema v2 telemetry plus
  a nullable, fixed-key last-known location. An existing authorization without
  `vehicle_location` returns `tesla_reauthorization_required`; complete the
  OAuth flow again before using v3.
- Unknown, empty, or repeated schema-version parameters are rejected before any
  Tesla request.
- All other routes and methods return `404`.

Automatic invocation logs are disabled because OAuth codes and state arrive in
the callback query string. Unexpected errors return only
`{"error":"internal_error"}` and are not logged with request details.

Summary reads emit fixed-shape custom `vehicle_source_check` logs. They distinguish
cache hits, token refreshes, inventory checks, vehicle-data requests, and stale
fallbacks using only stage/outcome enums, HTTP status, allowlisted error and
connectivity values, vehicle count, and cache timing. `actual_checked_at` is null
for cache hits and fallback events; `cache_checked_at` records the cache's last
check rather than a new Tesla request. These logs never include identifiers,
provider URLs or bodies, credentials, vehicle values, locations, or exception
messages. Automatic invocation logs remain disabled.

## Summary schemas

Schema v1 remains shape-compatible with existing InkyPi releases. Schema v2 is
opt-in and adds canonical, nullable groups for energy and charging, climate
equipment, door/window details, tire pressures and warnings, vehicle
configuration, software updates, and the vehicle's preferred units. Every v2
key is present; missing, malformed, conflicting, or out-of-range provider
values are `null` rather than fabricated as zero, off, or closed.

Schema v3 keeps the exact v2 telemetry shape and adds `location`. That value is
either `null` or an object containing `captured_at`, `age_seconds`, `latitude`,
and `longitude`. Coordinates are accepted only as a valid pair and disappear
after 24 hours. V1 and v2 projections remain location-free.

The Worker requests Tesla's `location_data` vehicle-data group only with an
authorization that includes the read-only `vehicle_location` scope. The Fleet
API application must permit that scope and an older user grant must be
authorized again before v3 is available. Provider IDs, routes, media, tokens,
remote-start state, commands, and wake operations remain outside the public
contract.

## Data freshness and provider use

The summary cache TTL is 15 minutes. Cached vehicle values are never served
after 24 hours; an offline vehicle then yields an empty/unknown snapshot instead
of old lock, closure, battery, or climate values. A new authorization is
atomically installed with its cache cleared, so one Tesla account cannot inherit
another account's snapshot.

The Worker lists vehicles and requests `vehicle_data` only when a refresh is
needed and the selected vehicle reports online. It never wakes a vehicle and
does not use Fleet Telemetry. While the vehicle sleeps, schema v3 can return the
last known location until its 24-hour limit; it does not refresh that location
by waking the car. Tesla can apply Fleet API billing and rate limits; the cache
is the primary request-cost control. See Tesla's current
[Billing and Limits](https://developer.tesla.com/docs/fleet-api/billing-and-limits)
documentation before changing refresh intervals.

## Required Cloudflare secrets

```text
TESLA_CLIENT_ID
TESLA_CLIENT_SECRET
BRIDGE_ADMIN_TOKEN
BRIDGE_READ_TOKEN
TOKEN_ENCRYPTION_KEY_V1
```

Never paste these values into source, issue trackers, chat, logs, or Worker
plaintext variables. The corresponding local recovery material belongs only in
ignored, ACL-restricted `.private/` storage.

## Local verification

```powershell
npm ci
npm run check
```

`npm run check` regenerates Worker types, type-checks, runs the Miniflare/Vitest
suite, and produces a dry-run bundle. Production deployment and OAuth consent
are deliberately separate steps.
