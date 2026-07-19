# Plugin Reliability Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for each task and superpowers:verification-before-completion before claiming success. Execute inline because this session does not permit delegated subagents.

**Goal:** Restore every currently broken plugin, prevent fresh-data fallbacks from becoming stale pages, and keep future provider/browser failures bounded so one plugin cannot starve the rotation.

**Architecture:** Keep the existing single-flight refresh scheduler and last-good caches. Repair the four failure boundaries: release completeness, Pixiv bank publication, low-memory Chromium execution, and fresh-versus-stale fallback semantics. External providers retain their existing fallbacks, augmented only where repeated optional failures consume disproportionate time.

**Tech Stack:** Python 3.13, Flask/InkyPi, Pillow, Jinja2, Chromium headless shell, pytest, PowerShell/OpenSSH, systemd release symlinks.

**Global constraints:** Preserve unrelated dirty work; test before and after each change; never expose saved secrets; no commit or push; deploy only a verified clean artifact; prove behavior on `/opt/inkypi/current` and the physical display.

---

### Task 1: Restore missing AI and orbital plugins from the verified source

**Files:**
- Add: `inkypi-weather/package/InkyPi/src/plugins/ai_ecosystem_pulse/**`
- Add: `inkypi-weather/package/InkyPi/src/plugins/orbital_signal/**`
- Add: `inkypi-weather/package/InkyPi/tests/test_ai_ecosystem_pulse.py`
- Add: `inkypi-weather/package/InkyPi/tests/test_orbital_signal.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_plugin_registry.py`

1. Add a registry test that requires both plugin IDs, configs, import targets, and required assets to be discoverable; run it and record the missing-plugin failure.
2. Restore the exact directories and tests from clean worktree commit `f16ccdab`; do not hand-rewrite them.
3. Run the restored plugin suites plus registry test and verify the source worktree remains clean.

### Task 2: Publish a viable Pixiv bank before the DATA deadline

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/pixiv_r18_ranking/pixiv_r18_ranking.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/pixiv_r18_ranking/presentation_bank.py` only if transaction support is required
- Test: `inkypi-weather/package/InkyPi/tests/test_pixiv_r18_ranking.py`

1. Add a virtual-clock test reproducing a cold bank that downloads valid records but reaches the old final cleanup hook with no durable state.
2. Reserve a bounded commit window and checkpoint a viable selection/state before optional cleanup; never commit a transaction after the hard deadline.
3. Keep cleanup best-effort after a successful checkpoint and preserve existing byte-stable rollback tests.
4. Run all Pixiv tests and the presentation scheduler tests.

### Task 3: Make Chromium safe and bounded on the Zero 2 W

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/utils/browser_renderer.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_browser_renderer.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_browser_renderer_security.py`

1. Add command-construction tests for the low-memory ARM flags required by current Chromium (`in-process-gpu`, JIT-less V8, disabled zero-copy/GPU buffers, SwiftShader, and root-only no-sandbox).
2. Add a test that a local-HTML timeout opens a short renderer-health circuit so changing HTML timestamps do not spawn a new Chromium process on every plugin run; URL-specific failures remain key-scoped.
3. Implement the flags and health circuit without changing SSRF validation, proxy routing, single-flight behavior, process-group termination, or cleanup bounds.
4. Run the full browser renderer/security suites.

### Task 4: Keep Weather and Steam Charts fresh without Chromium

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/weather/weather.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/steam_charts/steam_charts.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_weather.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_steam_charts.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_refresh_task.py`

1. Add Weather tests proving fresh provider facts produce a readable Pillow fallback when HTML rendering is unavailable and that stale provider data is still marked stale/non-cacheable.
2. Implement a compact e-paper-safe Weather fallback using existing parsed template facts and fonts.
3. Change Steam Charts tests so a PIL fallback based on freshly fetched chart data is cacheable and advances latest-success/cache bytes; provider-data fallback remains distinguishable.
4. Remove the stale-cache substitution for fresh Steam fallback output and run targeted refresh-task integration tests.

### Task 5: Bound optional provider and media failures

**Files:**
- Modify/Test only where red tests prove repeated waste:
  - `src/plugins/live_radar/live_radar.py`, `tests/test_live_radar.py`
  - `src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`, `tests/test_steam_profile_dashboard.py`
  - `src/plugins/steam_daily_art/steam_daily_art.py`, `tests/test_steam_daily_art.py`

1. Add a LiveRadar test that a confirmed batch 5xx opens a short batch-only cooldown while individual requests continue, then retries batch after expiry.
2. Add persistent bounded negative-cache tests for optional Steam media 404s so identical missing assets are not fetched on every render.
3. Do not change SportsDashboard, Tech Pulse, or box-office selection logic when their existing fallback already returns current display-safe data; verify those fallback tests instead.

### Task 6: Re-measure scheduler capacity and add only the necessary guard

**Files:**
- Modify if evidence still fails: `inkypi-weather/package/InkyPi/src/refresh_task.py`
- Modify if evidence still fails: `inkypi-weather/package/InkyPi/src/runtime/refresh_policy.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_refresh_task.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_refresh_policy.py`

1. Run a deterministic mixed-playlist test with slow failures removed and assert an hourly plugin is admitted within its bounded fairness window.
2. If that test fails, add the smallest admission/failure-cooldown guard and a failing test first.  If it passes, make no scheduler change.
3. Add/retain an explicit distinction between provider-stale output and fresh-data degraded visuals.

### Task 7: Full verification and clean release construction

**Files:**
- Verify: `tools/verify_clean_archive.py`
- Verify: `tools/epaperpod-deploy-zip.ps1`
- Verify: all changed source/tests/assets

1. Run AST/import checks, all targeted suites, then the repository InkyPi test runner.
2. Review the diff against the research findings and scan for leaked credentials, generated caches, pycache, and accidental user-change overlap.
3. Build a clean auditable release that contains both restored plugin directories and all intended pre-existing live fixes; verify archive inventory and hashes before transfer.

### Task 8: Transactional deploy, reboot, and live proof

**Files:**
- Remote: `/opt/inkypi/releases/<new-release>`
- Remote active symlink: `/opt/inkypi/current`

1. Upload to an incoming directory, verify hashes and plugin manifests, install using the existing transactional release workflow, and switch only after preflight passes.
2. Reboot once to clear Chromium-induced swap thrash, then verify service active, zero restart loop, release symlink, memory/swap recovery, and no orphan Chromium processes/jobs.
3. Force DATA/display checks for AI Ecosystem Pulse, Orbital Signal, Pixiv, Weather, Steam Charts, LiveRadar, and SportsDashboard.  Confirm current timestamps/content and physical 800x480 output rather than only HTTP status.
4. Monitor at least one bounded rotation/fairness window; compare DATA attempts, failures, latest-success ages, presentation failures, browser timeouts, and hard-resource deferrals against the pre-fix baseline.
5. Update `.learnings/LEARNINGS.md` with resolved patterns and remaining upstream-only degradation, then report exact proof and any credentials genuinely still required.
