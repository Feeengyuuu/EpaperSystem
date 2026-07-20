# LiveRadar Source Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute this plan, superpowers:test-driven-development for every behavior change, and superpowers:verification-before-completion before claiming success. Execute inline because this session does not permit delegated subagents.

**Goal:** Replace LiveRadar's fragile mixed-platform request path with platform-specific sources, keep all 65 configured rooms, and guarantee that visible live screenshots are re-requested after a 60-second TTL even when the streamer and cover URL have not changed.

**Architecture:** Partition rooms before network I/O and merge results back into original order. Twitch uses one official Helix streams request per refresh with an in-memory app-token cache; Bilibili resolves room IDs once into a persistent UID map and then uses the existing bulk UID endpoint; Douyu alone uses the existing aggregator with a platform-scoped circuit breaker and individual fallback. Status caches and media caches remain independent, and every source records a credential-free health summary.

**Tech Stack:** Python 3.13, requests-compatible shared HTTP session, Pillow, pytest, canonical InkyPi SecretSchema, PowerShell/OpenSSH, systemd release symlinks.

**Global constraints:** Never write or print the supplied Twitch secret in repository files, tests, command output, logs, or artifacts. Preserve all 65 default rooms and current UI layout. Keep theme-only redraw network-free. Use stale status/screenshot caches only as last-good fallbacks, never as proof of a fresh fetch. Do not push unless the user separately asks; deploy only a verified clean archive.

---

### Task 1: Register Twitch credentials without storing values

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/config/secret_schema.json`
- Regenerate: `inkypi-weather/package/InkyPi/install/api_key_registry.json`
- Regenerate: `inkypi-weather/package/InkyPi/.env.example`
- Modify: `inkypi-weather/package/InkyPi/tests/test_secret_schema.py`

1. Add a failing schema test requiring `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET`, both owned by the `LiveRadar` feature.
2. Run `python -m pytest tests/test_secret_schema.py tests/test_secret_schema_plugin_contract.py -q` from the package root and confirm the new assertion fails.
3. Add the two canonical schema entries with official Twitch developer-console help URLs and no real values.
4. Run `python install/configure_api_keys.py --generate-artifacts`, then rerun both schema suites and verify generated artifacts match byte-for-byte.

### Task 2: Add the official Twitch Helix source

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_live_radar.py`

1. Add failing tests proving that 13 Twitch logins cause one `GET /helix/streams` call, offline rooms are synthesized in input order, Twitch thumbnail templates become concrete 640x360 URLs, and the token endpoint is not called again before the cached token expires.
2. Add a failing test proving that neither credential value appears in result dictionaries, source-health output, or warning text.
3. Load the two credentials through `device_config.load_env_key`, falling back to process environment only for isolated tests/runtime compatibility.
4. Implement client-credentials token acquisition with an expiry safety margin and a single Helix request using repeated `user_login` parameters.
5. If credentials or Helix are unavailable, record a bounded failure and fall back to a Twitch-only aggregator request; never mix Twitch with Douyu/Bilibili in that request.
6. Run the Twitch-focused tests after each red/green cycle.

### Task 3: Make Bilibili direct bulk status the primary path

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_live_radar.py`

1. Add failing tests for a cold room-ID-to-UID lookup followed by one bulk UID status call, then a warm refresh that skips every room-info request.
2. Add a failing test that a missing/invalid room is negative-cached for six hours while valid rooms continue to return normally.
3. Persist only room ID, UID, safe room metadata, and timestamps in `bilibili_room_map.json`; expire successful mappings after seven days and negative mappings after six hours.
4. Promote direct Bilibili fetching ahead of the aggregator. Only unresolved/direct-failed Bilibili rooms may use a Bilibili-only aggregator fallback.
5. Keep cover, avatar, live/replay, heat, and start-time conversion compatible with the existing card renderer.

### Task 4: Isolate Douyu and merge platform results deterministically

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_live_radar.py`

1. Replace the mixed-platform batch tests with failing routing tests: Douyu batches contain Douyu only, Twitch/Bilibili never enter them when their primary source succeeds, and final results match the original 65-room order.
2. Scope the batch-failure cooldown by platform, retain ten-room chunks, and retain individual retry only for aggregator-backed rooms.
3. Merge by `(platform, id)` and synthesize an explicit error result for any missing source response; duplicate configured keys may reuse the same fetched status without shifting neighboring cards.
4. Preserve the last-good whole-status cache behavior in `generate_image`.

### Task 5: Add source health without leaking secrets

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_live_radar.py`

1. Add a failing test for a health document containing per-platform source, duration, requested/success/error counts, and update time.
2. Write the latest document atomically to the LiveRadar cache namespace and emit one compact summary log per platform.
3. Sanitize errors to exception class plus bounded public reason; never serialize request headers, token responses, client IDs, or client secrets.
4. A failed health write must not fail the dashboard render.

### Task 6: Enforce live screenshot refresh independently of status

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_live_radar.py`

1. Add a failing virtual-time media test: render the same live card and same cover URL at T+0, T+59, and T+60; assert network GET counts are 1, 1, and 2.
2. Change the default `snapshotCacheSeconds` to 60 independent of `cacheSeconds`, while preserving the allowed 30-1800 second override.
3. Keep live screenshot retrieval active for every visible live snapshot path, including compact live cards. Preserve the cached image on refresh failure and keep live status unchanged.
4. Retain the existing theme-only contract: a theme redraw can read warm media but performs zero network I/O.

### Task 7: Targeted and full local verification

**Files:**
- Verify: all changed source, tests, schema, and generated artifacts
- Review: `.learnings/LEARNINGS.md`

1. Run focused LiveRadar and SecretSchema tests, then the repository InkyPi test runner.
2. Parse changed Python with AST/import checks and scan the diff for credentials, cache files, pycache, encoding damage, and unrelated edits.
3. Confirm `DEFAULT_ROOMS_TEXT` still parses to 65 rooms with platform counts 31 Bilibili, 21 Douyu, and 13 Twitch.
4. Record a concise resolved lesson only if the implementation yields a new reusable platform-isolation or media-TTL pattern.

### Task 8: Protected credential install, transactional deploy, and live proof

**Files:**
- Remote secret file: `/etc/inkypi/inkypi.env` (values never printed)
- Remote release: `/opt/inkypi/releases/<new-release>`
- Remote active symlink: `/opt/inkypi/current`

1. Install the supplied Twitch values into the protected environment file using a no-echo transfer/update path; verify only key presence and file permissions.
2. Build and inspect a clean LF-safe release archive, deploy transactionally, and verify the active symlink and `inkypi.service` readiness with zero restart loop.
3. Trigger a real 65-room LiveRadar DATA render. Prove one Twitch Helix streams request, warm Bilibili mapping behavior, Douyu-only aggregator chunks, ordered 65 results, and credential-free source-health JSON/logs.
4. While at least one streamer is live, render the same visible card twice more than 60 seconds apart and prove its cover cache mtime/content/request evidence advances while live status remains live. If no configured streamer is live, exercise the exact production media path with a temporary non-persisted live fixture and report that limitation explicitly.
5. Inspect the physical 800x480 output and keep monitoring through at least one subsequent refresh window before declaring completion.
