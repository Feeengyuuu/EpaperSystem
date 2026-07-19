# Plugin reliability recovery research

## Scope

The live `ColoredEpaperFrame` runtime was audited from command outcomes, per-instance
latest-success timestamps, saved playlist instances, the active release tree, resource
telemetry, and a minimal Chromium smoke test.  A successful command is not sufficient
evidence of freshness because several plugins return a fallback or a cached image.

## Confirmed root causes

1. `ai_ecosystem_pulse` and `orbital_signal` still exist in the saved playlist but are
   absent from both local `main` and `/opt/inkypi/current/src/plugins`.  Every DATA
   attempt therefore fails before plugin code is loaded.  A clean historical worktree at
   commit `f16ccdaba2b5daa846c56c5c5ef2f0ad4a008ce9` contains both complete plugin trees
   and their tests.
2. Pixiv downloads media until its full 90-second DATA deadline, then attempts the only
   durable bank save from `bank.cleanup(...)`.  If the deadline expires at that final
   hook, downloaded media and state are not published; presentation remains cold and the
   next DATA run repeats the same work.
3. Chromium itself is unhealthy on the live Pi.  A one-word local HTML render through
   `BrowserRenderer` timed out after 15 seconds.  A raw `about:blank` render put Chromium
   150 processes into uninterruptible I/O while swap rose from about 286 MB to 365 MB.
   The package was upgraded from Chromium 148 to 150 on 2026-07-10.  This explains the
   common Weather and Steam Charts HTML failures and the expensive Tech Pulse preview
   failures.
4. Steam Charts currently marks a fresh-data PIL fallback with `inkypi_skip_cache`.
   `PlaylistRefresh` then deliberately reuses the older HTML cache, converting a visual
   degradation into stale information.  The current user requirement makes freshness
   higher priority than preserving the older preferred rendering.
5. Hard resource pressure suppresses renderer admission, while failed Pixiv and Chromium
   jobs occupy the one renderer for 15-90 seconds at a time.  This amplifies lateness for
   otherwise healthy plugins.  Existing retry and lane fairness code should be retained;
   it must be re-measured after the expensive failure loops are removed before another
   scheduler redesign is justified.

## Degraded but contained dependencies

- LiveRadar batch requests sometimes return HTTP 500, but individual fallback requests
  succeed.
- Tech Pulse story screenshots fail, but the fresh Hacker News payload and PIL fallback
  page succeed.
- SportsDashboard's primary CSAPI certificate is expired, but the HLTV fallback succeeds.
- Optional Steam icons/logos sometimes return 404; the primary plugin payload succeeds.
- Box-office sources can fail transiently, but later sources or later runs succeed.

These should fail fast or use cooldowns so they do not consume the renderer/network
budget repeatedly.  They must not be presented as locally repaired upstream services.

## Implementation constraints

- Preserve all unrelated dirty SportsDashboard and scheduler work in the current tree.
- Restore missing plugins from the verified clean worktree without reconstructing code.
- Use red-green tests for every behavioral change.
- Preserve last-good data only when the new data acquisition failed.  A fresh-data visual
  fallback is cacheable and may replace a stale preferred visual.
- Keep browser egress validation and bounded cleanup.  Low-memory Chromium flags and a
  renderer cooldown must not weaken URL validation.
- Build a clean release artifact, deploy transactionally, reboot once to clear swap
  thrash, and verify the active release rather than an incoming/staging directory.
- Do not commit or push unless the user asks separately.
