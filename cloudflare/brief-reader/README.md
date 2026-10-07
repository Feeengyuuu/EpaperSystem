# Model Y Cloud Reader

Production reader: <https://e.superxfy.workers.dev/> (Worker name `e`)

Layout in this repository:

- `src/` — the Worker and its publication-ledger Durable Object; `static/` holds
  the reader stylesheet and script the Worker bundles as text.
- `tools/` — the Raspberry Pi publisher sidecar (`sync_device_portal.py`,
  `push_portal.py`, `watch_device_portal.sh`) and its unit tests.

The sidecar reads the device's own `/playlist` page. When that page changes
(as with the 2026-10 Now Playing redesign), update `_PlaylistParser` so it
reads both layouts, test it against a saved copy of the live page, and only
then switch the device's `current` publisher release. A parser mismatch never
damages the cloud edition, but every publication stops with `capture_invalid`
until the sidecar is fixed.

This Cloudflare Worker serves the original 800×480 plugin PNG files from one
private, immutable release. Browser reads are authenticated and can only read
the current catalog and its content-addressed assets. They cannot call an
InkyPi provider, renderer, refresh queue, admin route, or physical display.

Successful login creates a host-only, `HttpOnly`, `Secure`, `SameSite=Strict`
session with Chromium's 400-day maximum lifetime. Every authenticated visit to
the reader root renews that lifetime, so a regularly used vehicle browser stays
signed in. Clearing site data, using a different browser profile, or going more
than 400 days without opening the reader still requires one new login.

Release schema v2 carries only the active playlist's sanitized shortest
positive plugin refresh interval. The reader derives its catalog check cadence
as one quarter of that interval, bounded to 15–60 seconds; the current 120-second
LiveRadar interval therefore produces a 30-second browser check. Hidden pages
still pause checks and check immediately when visible again. Legacy v1 releases
remain readable and use the same 30-second fallback.

When a new edition arrives, the reader resumes the same instance and pause
state, including the remaining time before its next slide. Reordering a
playlist therefore does not restart playback, and frequent updates cannot
starve its later items. A missing lazy-loaded image triggers an immediate
catalog check so an open page can recover after old assets are retired. The
empty reader also polls until its first complete edition is published.

Web publication currently excludes the `pixiv_r18_ranking` plugin before any
thumbnail is fetched. All other active items keep their physical-playlist
order, and the browser check cadence is derived only from those published
items. This is a publisher-only exclusion: the InkyPi playlist, scheduler,
cache, and physical display are unchanged. To re-enable Pixiv on the web,
remove its ID from `_EXCLUDED_WEB_PLUGIN_IDS` in `sync_device_portal.py` and run
the publisher tests before releasing the sidecar.

For protocol upgrades, deploy and verify the v1/v2-compatible Worker first,
then atomically switch the device publisher. Rollback uses the reverse order:
publisher first, Worker second. Reversing the upgrade order does not damage the
last-good edition, but the old Worker will reject v2 and pause new publication.

The SQLite Durable Object switches `current` only after every declared asset
has been accepted. A failed upload therefore leaves the last-good release
visible. Old release data is removed after the atomic switch to keep Free-plan
storage bounded. Replay nonces are retained only beyond the valid signature
window and indexed by timestamp so routine cleanup does not scan the full
ledger on every publication request.

## Validation

```powershell
npm test
npm run typecheck
npm run deploy:dry
py -3.11 -m unittest discover -s tools -p "test_*.py"
py -3.14 -m unittest discover -s tools -p "test_*.py"
```

`npm test` runs the Worker integration tests and executes the shipped
`static/portal.js` under a deterministic browser adapter, covering publication
changes during playback, image failures, session state, and first publication.

`push_portal.py` accepts only an exact `manifest.json + assets/<sha256>.png`
bundle and reads its signing key from `EPAPER_PUBLISH_KEY`. The key is not a
CLI argument and must never be committed or logged.

`sync_device_portal.py` is the Raspberry Pi sidecar. It accepts only the
loopback `/playlist` page and same-origin `/plugin_instance_image/...` GET
routes, validates a complete active playlist, reserves a monotonic generation,
and publishes only after all PNG files pass the full 800×480 validation.

## Installed device layout

- Code: `~/.local/lib/epaper-publisher/releases/<release-id>`
- Active code: `~/.local/lib/epaper-publisher/current`
- Private key: `~/.config/epaper-publisher/publish.key` (`0600`)
- State: `~/.local/state/epaper-publisher/state.json` (`0600`)
- Display signal: `/run/inkypi/display_revision` (atomic 32-hex commit marker)
- Trigger: `inotifywait` wakes `watch_device_portal.sh` after each successful
  Epaper display commit; Python exists only for the bounded publication run
- Supervision: the tagged `EPAPER_PUBLISHER` user crontab block starts the
  watcher at boot and replaces it within one minute if it exits

The display transaction only writes a tiny local marker and never calls the
network. The watcher and cloud upload remain independent of
`inkypi.service`: cloud failure cannot fail or delay a physical display
commit. The pre-install crontab is saved at
`~/.local/state/epaper-publisher/crontab.before`. To disable the publisher,
restore that file with `crontab` and leave the current cloud edition in place.
Code rollback is an atomic `current` symlink switch to a verified prior
publisher release.
