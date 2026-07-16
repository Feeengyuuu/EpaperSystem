# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260715-006] correction

**Logged**: 2026-07-15T17:25:00-07:00
**Priority**: high
**Status**: resolved
**Area**: plugin

### Summary
A prepared bank can contain several valid records while the rendered page still appears to have only one if the bank-to-view adapter drops related records.

### Details
Species Radar's older renderer expected a selected hero followed by other observations for its recent list and thumbnail strip. It also advanced through a shuffled observation pool on each normal display without repeats until the round was exhausted, while theme-only redraws did not advance. The prepared-bank adapter initially passed only one ready record, so both the gallery and the rotation appeared frozen. Increasing full-size media downloads attacked the wrong layer and exceeded the Pi data deadline. Related records must merge the existing local Chinese-name cache, use bounded medium-image prefetching, and promote those cached images into the ready presentation bank so both the gallery and display rotation survive cache-only display. A second failure mode appeared in the shared presentation lane: an older request that repeatedly failed could regain eligibility after its retry cooldown and win again solely because its original due time was oldest. If other work occupied the lane throughout the cooldown, a newer never-attempted Species Radar request could remain pending indefinitely. Runtime inspection then exposed a subtler request-generation bug: the lane's last attempt and retry deadline can belong to a previous presentation request. Treating that historical attempt as an attempt of the newly generated request incorrectly demotes or delays the new work even after fair candidate ordering is added.

### Suggested Action
When a banked plugin loses multi-item composition, inspect the persisted bank, ready-record pool, final render payload, and shared presentation scheduler separately. Preserve the proven full-size provider workload, persist a bounded related-metadata pool, merge local enrichment caches during rendering, prefetch display-sized media inside a separate soft deadline with a hard save reserve, and promote that cached media into ready records. Within the presentation lane, prefer never-attempted requests and then the least-recently attempted request while preserving the established ordering slots of other auxiliary lanes. Count an attempt or its retry deadline only when that attempt occurred at or after the current request's `requested_at`; an older recorded attempt belongs to the previous request generation. Test payload cardinality, names, thumbnails, no-repeat full-round rotation, theme-only non-advancement, deadline exhaustion, persistence, provider-free display access, cross-instance failure fairness, and request-generation isolation.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/species_radar/species_radar.py, inkypi-weather/package/InkyPi/src/runtime/refresh_policy.py, inkypi-weather/package/InkyPi/tests/test_species_radar.py, inkypi-weather/package/InkyPi/tests/test_refresh_policy.py
- Tags: prepared-bank, view-adapter, gallery, cached-media, cached-names, soft-deadline, shuffle-rotation, presentation-fairness, head-of-line-blocking, species-radar
- Pattern-Key: plugin.prepared_bank_view_cardinality
- Recurrence-Count: 2
- First-Seen: 2026-07-15
- Last-Seen: 2026-07-15

---

## [LRN-20260715-004] best_practice

**Logged**: 2026-07-15T01:10:00-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
When authoritative and saved time series are combined, filter by source precedence and time window before drawing, and align markers to the actual supplemental segment.

### Details
Money's official Robinhood history must own every overlapping date. Durable local history may only extend the curve before the first official date and inside the selected period. Sampling local-history markers across the completed curve falsely implied that official points came from local records.

### Suggested Action
Merge dated points before extracting values, keep the authoritative source on overlap, return the exact supplemental prefix length with the curve, and place markers only on those prefix coordinates.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py, inkypi-weather/package/InkyPi/tests/test_stocktracker.py
- Tags: time-series, source-precedence, chart-markers, robinhood, stocktracker
- Pattern-Key: chart.authoritative_series_precedence
- Recurrence-Count: 1
- First-Seen: 2026-07-15
- Last-Seen: 2026-07-15

---

## [LRN-20260715-005] best_practice

**Logged**: 2026-07-15T01:12:00-07:00
**Priority**: medium
**Status**: resolved
**Area**: tests

### Summary
Do not treat a single image-viewer rendering anomaly as a generated-image regression.

### Details
The original-detail viewer temporarily omitted visible text from a live Money screenshot even though the PNG SHA and region pixel frequencies matched the known-good image. Reopening the same exact file in high-detail mode showed the complete page.

### Suggested Action
Before changing rendering code, verify the same file hash, compare representative pixel regions, and reopen the identical image through a second viewer detail mode.

### Metadata
- Source: error
- Related Files: tools/live_all_instances_acceptance.py
- Tags: image-viewer, screenshot, sha256, pixel-verification, false-positive
- Pattern-Key: verify.image_viewer_anomaly
- Recurrence-Count: 1
- First-Seen: 2026-07-15
- Last-Seen: 2026-07-15

---

## [LRN-20260715-001] correction

**Logged**: 2026-07-15T00:05:00-07:00
**Priority**: critical
**Status**: resolved
**Area**: plugin

### Summary
When a live plugin may be failing because its user login, cookie, OAuth grant, or account selection is missing, stop and ask the user before changing providers or adding a fallback.

### Details
Several InkyPi providers worked in older releases with authenticated user context. Replacing an unavailable authenticated source with sample data, stale CSV input, or an unrelated public source can make the plugin appear healthy while violating the user's live-data requirement.

### Suggested Action
First identify whether the failure boundary is authentication or account metadata. If it is, report exactly what user action or non-secret identifier is needed and wait. Resume code changes only after the user authorizes that provider and completes the required login.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins, tools/live_all_instances_acceptance.py
- Tags: authentication, oauth, live-data, fail-closed, user-input

---

## [LRN-20260715-002] best_practice

**Logged**: 2026-07-15T00:10:00-07:00
**Priority**: high
**Status**: resolved
**Area**: plugin

### Summary
Multi-symbol portfolio charts must align every holding onto shared historical keys and one shared live key before summing values.

### Details
Robinhood returns current quote timestamps that differ by a few seconds between symbols. Summing against the first symbol's keys silently counted only symbols with identical timestamps, creating a false low point and a dramatic straight-line jump to the overridden account total.

### Suggested Action
Use only dates present for every held symbol, append all current prices under one snapshot-level live key, and retain each symbol's original quote timestamp separately for provenance. Test with deliberately different quote timestamps and missing historical dates.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py, inkypi-weather/package/InkyPi/tests/test_stocktracker.py
- Tags: stocktracker, robinhood-mcp, time-series, alignment, chart

---

## [LRN-20260714-003] correction

**Logged**: 2026-07-14T21:25:00-07:00
**Priority**: high
**Status**: resolved
**Area**: plugin

### Summary
When a live InkyPi plugin regresses after scheduler or presentation-bank work, compare the last physically successful implementation before reducing provider functionality.

### Details
Species Radar had previously refreshed successfully by downloading media only for the single observation being displayed. The newer presentation bank multiplied one refresh into several observation, photo, map, and optional-name requests, then repeatedly missed the Pi data deadline. StockTracker also already had a working inline holdings path, but stale CSV settings prevented that path from being reached after the old CSV disappeared.

### Suggested Action
Use the last known-good tag or commit as the workload and fallback reference. Preserve the old per-display provider workload inside the new incremental bank, and allow a configured real-data fallback when persisted file paths become stale. Do not interpret a previously solved live-device behavior as a greenfield tuning problem.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/species_radar/species_radar.py, inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py
- Tags: regression, known-good, live-device, incremental-bank, stale-path, fallback

---

## [LRN-20260710-009] best_practice

**Logged**: 2026-07-10T23:20:00-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
Chromium headless cannot combine an enabled Linux sandbox with `--no-zygote`.

### Details
Chromium 150 exited before rendering with `Zygote cannot be disabled if sandbox is enabled`. The renderer suppressed stderr, so both Weather and Steam initially appeared to have unrelated screenshot failures. Adding `--no-sandbox` made a diagnostic succeed but would have weakened isolation; removing `--no-zygote` preserved the sandbox and restored real HTML rendering on the 416 MB device.

### Suggested Action
Keep regression assertions that reject both `--no-sandbox` and `--no-zygote`, and require a live HTML render plus kernel OOM check after Chromium package updates.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/utils/browser_renderer.py, inkypi-weather/package/InkyPi/tests/test_browser_renderer.py
- Tags: chromium, sandbox, zygote, raspberry-pi, html-rendering

---

## [LRN-20260710-010] best_practice

**Logged**: 2026-07-10T23:20:00-07:00
**Priority**: high
**Status**: resolved
**Area**: operations

### Summary
A ready service can still rotate stale, sample, or impossible plugin instances.

### Details
The control plane remained healthy while a removed Ticketmaster plugin, missing Riot/NASA/Steam keys, and Telegram sample fallback repeatedly consumed playlist work. A brief `readyz` 503 also occurred during legitimate long renders and returned to 200 when the task finished.

### Suggested Action
For live acceptance, inspect instance-level logs and committed display manifests across multiple refresh cycles. Reversibly remove only proven-unrunnable instances with ConfigStore versioning and root-only backups; treat bounded render-time 503 as transient only when health stays alive and readiness returns to 200.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/health.py, inkypi-weather/package/InkyPi/src/config_store.py
- Tags: playlist, readiness, api-keys, sample-data, config-migration, live-acceptance

---

## [LRN-20260710-011] best_practice

**Logged**: 2026-07-10T23:20:00-07:00
**Priority**: medium
**Status**: resolved
**Area**: release

### Summary
Release preflight requires the resolved release directory, not the `/opt/inkypi/current` symlink.

### Details
A transactional config migration committed successfully but preflight rejected the symlink path as not being a regular release directory. The rollback restored all config files and restarted the service; retrying with `readlink -f /opt/inkypi/current` passed.

### Suggested Action
Resolve and verify the current release once at the start of every maintenance wrapper, then pass that immutable path to preflight and all release-local tools.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/install/preflight.py
- Tags: preflight, symlink, release, rollback, maintenance

---

## [LRN-20260710-001] best_practice

**Logged**: 2026-07-10T16:53:14-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
Treat a low-memory Raspberry Pi release update as a resource-bounded transaction.

### Details
On a 512 MB Pi, a reliable update required an offline wheelhouse, `TMPDIR` on the root filesystem instead of `/tmp` tmpfs, quieting desktop/background services, preserving the previous release, and temporarily widening the hardware watchdog only during the update. Check disk headroom after cache growth and never race zram's systemd unit with a manual immediate `swapoff`/`swapon` cycle.

### Suggested Action
Keep the updater's disk/memory preflight and rollback boundary; clean only reproducible caches when necessary and restore the watchdog/services after live verification.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/install/install.sh, inkypi-weather/package/InkyPi/install/lib/update_engine.py
- Tags: raspberry-pi, low-memory, watchdog, tmpfs, zram, rollback

---

## [LRN-20260710-002] best_practice

**Logged**: 2026-07-10T16:53:14-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
Relocated virtual environments must be launched through their final absolute interpreter path.

### Details
`activate` embeds the staging venv path, so sourcing it after moving a release can silently fall back to the system interpreter. Pi GPIO packages also belong in the Pi runtime lock, and `lgpio` needs a writable runtime working directory for its FIFO.

### Suggested Action
Launch `/opt/inkypi/current/venv_inkypi/bin/python` directly, keep GPIO dependencies hash-locked, and set both `WorkingDirectory` and `LG_WD` to `/run/inkypi`.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/install/inkypi, inkypi-weather/package/InkyPi/install/inkypi.service, inkypi-weather/package/InkyPi/install/requirements-pi.in
- Tags: venv, relocation, systemd, gpio, lgpio

---

## [LRN-20260710-003] best_practice

**Logged**: 2026-07-10T16:53:14-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
Build deploy archives from the repository root so nested line-ending attributes survive.

### Details
A subtree-only archive omitted parent `.gitattributes`, converting Linux service/socket and APT list files to CRLF. Bash optional probes under `set -e` must also return success on their expected no-op path.

### Suggested Action
Verify Linux control files are LF in the final tracked archive and keep expected optional branches explicitly successful.

### Metadata
- Source: error
- Related Files: .gitattributes, inkypi-weather/package/InkyPi/install/install.sh
- Tags: git-archive, crlf, bash, systemd, apt

---

## [LRN-20260710-004] best_practice

**Logged**: 2026-07-10T16:53:14-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Keep legacy cache migration as a controlled operational step until it is transaction-safe.

### Details
An automatic installer migration introduced service ABA, private-home access, marker/LKG, and power-loss boundaries. The live data could be copied safely while the service was explicitly stopped, but the generic automation could not prove those invariants.

### Suggested Action
Do not ship automatic migration until config identity, both LKG snapshots, service ownership, and crash recovery are verified before publishing a one-time marker.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/install/install.sh
- Tags: migration, config, lkg, crash-safety, aba

---

## [LRN-20260710-005] best_practice

**Logged**: 2026-07-10T17:01:30-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Persist user-referenced plugin files outside legacy home-backed source trees.

### Details
The hardened service correctly uses `ProtectHome=true`, so a legacy URI under `/usr/local/inkypi/src` became unreadable when that symlink traversed a mode-0700 user home. The file itself was world-readable, but every parent directory must also be traversable.

### Suggested Action
Store plugin-owned files under `INKYPI_DATA_DIR`, migrate saved URIs through ConfigStore, and keep any compatibility resolver restricted to a known legacy directory plus one filename. Never weaken `ProtectHome` or home permissions to preserve a stale path.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py
- Tags: protecthome, persistent-data, file-uri, config-migration, path-traversal

---

## [LRN-20260710-006] insight

**Logged**: 2026-07-10T21:32:21-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
Fresh provider data and valid provenance do not prove that a rendered live-state label is truthful.

### Details
SportsDashboard fetched current ESPN, EWC, PGA, and MLB payloads, yet broad date windows, empty competition results, provider post states, and MLB warmup codes were still rendered as LIVE. Data freshness is a transport property; acceptance must separately validate the provider's semantic state and the final human-facing label.

### Suggested Action
Keep provider-specific semantic tests for scheduled, warmup, active, post, completed, and empty-result states, then inspect a freshly generated production image rather than accepting cache timestamps or source badges alone.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard
- Tags: sports, provenance, freshness, semantics, live-state, acceptance

---

## [LRN-20260710-007] best_practice

**Logged**: 2026-07-10T21:32:21-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
Stopping a systemd service removes its RuntimeDirectory, and a hardened service user cannot use a private user home as runtime storage.

### Details
One-off maintenance that stops `inkypi.service` also removes `/run/inkypi`. Subsequent commands run as `inkypi` fail unless the root wrapper recreates the directory with the service's expected ownership and mode. `ProtectHome=true` and mode-0700 home parents also make otherwise readable files unreachable.

### Suggested Action
Have privileged maintenance wrappers recreate `/run/inkypi` before invoking the service interpreter, keep durable assets under `/var/lib/inkypi`, and never weaken home or service sandbox permissions as a shortcut.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/install/inkypi.service
- Tags: systemd, runtimedirectory, protecthome, service-user, maintenance

---

## [LRN-20260710-008] best_practice

**Logged**: 2026-07-10T21:32:21-07:00
**Priority**: high
**Status**: resolved
**Area**: release

### Summary
Git ignore rules do not protect proprietary runtime assets from a release builder that walks the filesystem directly.

### Details
The installer builds archives with recursive filesystem traversal rather than `git archive`, so an ignored `msyh.ttf` or `msyhbd.ttf` placed anywhere under the source tree could still enter a release. Repository cleanliness alone is therefore insufficient evidence for font licensing and artifact hygiene.

### Suggested Action
Keep Microsoft YaHei files only in the device-owned data directory, explicitly exclude case-insensitive `msyh*.ttf` and `msyh*.ttc` basenames in the archive builder, and test the real archive contents with nested and uppercase fixtures.

### Metadata
- Source: code_review
- Related Files: inkypi-weather/package/InkyPi/install/install.sh, inkypi-weather/package/InkyPi/tests/test_systemd_units.py
- Tags: release, archive, gitignore, proprietary-font, licensing, artifact

---

## [LRN-20260710-009] best_practice

**Logged**: 2026-07-10T23:45:00-07:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Global base-font migrations must preserve intentional display typography through selector-level exceptions.

### Details
Weather's primary temperature and unit intentionally used Jost as large numeric display type. Replacing every font declaration with the shared Microsoft YaHei stack erased that visual role even though the rest of the interface correctly adopted the new base font.

### Suggested Action
Before a global font migration, inventory explicit data-display and decorative selectors. Encode approved exceptions at selector scope, assert the surrounding component still uses the shared base stack, and verify the rendered production image rather than relying only on stylesheet scans.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/weather/render/weather.css, inkypi-weather/package/InkyPi/tests/test_base_ui_font_policy.py
- Tags: typography, font-migration, weather, selector-exception, visual-regression

---

## [LRN-20260711-001] user_feedback

**Logged**: 2026-07-11T14:30:00-07:00
**Priority**: critical
**Status**: unresolved
**Area**: architecture

### Summary
Cache-only playlist display must not erase instance-owned refresh-on-display and plugin-internal rotation contracts.

### Details
The independent-refresh integration correctly separated random cache display from provider work, but the production scheduler stopped consuming effective `refreshOnDisplay`. Twelve live instances explicitly saved the rule, and Newspaper dynamically enabled it through `mediaRotationMode=rotate`. Several affected plugins use the render call to advance a warm local rotation queue, so preserving interval/scheduled DATA cadence alone does not preserve their visible behavior.

### Suggested Action
Keep `DISPLAY_CACHE` strictly provider-free, then enqueue a separate single-worker `PRESENTATION_REFRESH` lane after successful display. Resolve instance override before manifest/default, apply resource gates and lane-local cooldown, preserve DATA/LIVE/THEME clocks and the playlist anchor, and prove real rotation plugins with warm-cache HTTP sentinels before deployment.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/plugins/plugin_settings.py, .superpowers/sdd/plugin-refresh-interaction-matrix.md
- Tags: refresh-on-display, cache-only, rotation, scheduler, presentation-lane, single-worker

---

## [LRN-20260712-001] best_practice

**Logged**: 2026-07-12T22:00:00-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Theme-aware render caches must be resolved through the shared cache catalog everywhere they are exposed.

### Details
The display worker correctly wrote day/night-suffixed cache files, while the plugin-instance preview route still derived the old unsuffixed path. A render job could therefore complete successfully and update the panel while the settings preview returned 404.

### Suggested Action
Route preview, display, and fallback lookup through the same resolved theme context and CacheCatalog candidate selection. Keep a regression test for current-theme and last-known-good candidates instead of reconstructing cache filenames in HTTP routes.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/blueprints/plugin.py
- Tags: theme, cache-catalog, preview, last-known-good, dual-theme

---

## [LRN-20260712-002] best_practice

**Logged**: 2026-07-12T22:00:00-07:00
**Priority**: critical
**Status**: resolved
**Area**: release

### Summary
Low-memory device updates need a tiny trusted bootstrap and must reuse the already verified environment.

### Details
Building or compiling a second full environment while the display service is live can exhaust a small device. Reliable updates used the pinned SSH identity and host alias, verified the artifact hash, stopped the service with a recovery trap, reused the exact validated virtual environment, forced binary-only offline wheels, and checked the activated release rather than relying on command exit alone.

### Suggested Action
Keep deployment transport pinned, compile-test generated probe code, preserve rollback on every signal or timeout, and require release ID, ready endpoint, config ownership, image integrity, and residue checks before declaring an update healthy.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/install/inkypi-update, inkypi-weather/package/InkyPi/install/lib/update_engine.py
- Tags: low-memory, updater, rollback, offline-wheelhouse, host-key, verification

---

## [LRN-20260712-003] best_practice

**Logged**: 2026-07-12T22:00:00-07:00
**Priority**: high
**Status**: resolved
**Area**: operations

### Summary
Live acceptance helpers must use public API contracts and preserve service-owned security/config files.

### Details
Internal render states are exposed publicly as compatibility values such as completed and timed_out, and the public ready response intentionally omits internal diagnostic fields. Root-run maintenance can also silently change configuration ownership, while direct service-user reads of the one-time admin bootstrap token are correctly denied.

### Suggested Action
Test helpers against the public status vocabulary, authenticate through the HTTP flow with CSRF, perform bootstrap setup through the running service, preserve service ownership on every atomic config replacement and backup, and emit only redacted gate results.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/blueprints/plugin.py, inkypi-weather/package/InkyPi/src/security
- Tags: acceptance, public-api, csrf, credentials, ownership, redaction

---

## [LRN-20260712-004] user_feedback

**Logged**: 2026-07-12T22:00:00-07:00
**Priority**: high
**Status**: resolved
**Area**: plugin

### Summary
The LoL plugin's theme-triggered account and skin rotation is intentional product behavior.

### Details
Day/night switching is allowed to select the paired LoL account and visual skin. This is not an accidental credential mutation and must survive scheduler, cache, and theme refactors; unrelated Telegram behavior may be repaired independently.

### Suggested Action
Keep a focused contract test for theme-only LoL account/skin selection, avoid rewriting its provider/cache contract during global theme work, and verify both modes without logging account identifiers.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/lol_info
- Tags: lol, theme, account-rotation, skin, regression

---

## [LRN-20260713-001] best_practice

**Logged**: 2026-07-13T06:36:07-07:00
**Priority**: high
**Status**: resolved
**Area**: plugin

### Summary
Runtime cache namespaces must use path components accepted by the shared CacheManager.

### Details
Ticketmaster could reach its provider and receive events, but its production cache leaf began with a dot. The shared CacheManager correctly rejected that unsafe namespace before poster downloads started, so the plugin caught the resulting error and rendered an empty-events fallback instead of the available event data.

### Suggested Action
Keep legacy hidden cache directories only in local development mode. Under `INKYPI_CACHE_DIR`, use safe namespace components and test the plugin with an initialized global CacheManager so state and image namespaces are exercised exactly as they are on the device.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/plugins/ticketmaster_events/ticketmaster_events.py, inkypi-weather/package/InkyPi/tests/test_ticketmaster_events.py
- Tags: ticketmaster, cache-manager, runtime-path, poster-cache, live-acceptance

---

## [LRN-20260713-002] best_practice

**Logged**: 2026-07-13T06:45:00-07:00
**Priority**: critical
**Status**: resolved
**Area**: operations

### Summary
Do not transport exact credentials through a Windows PowerShell string pipeline to SSH.

### Details
The pipeline prepended a UTF-8 BOM to the first line. A recovery helper and its subsequent HTTP acceptance test both consumed the same altered value, so they agreed with each other while the intended password still failed an independent byte-clean verification.

### Suggested Action
Send secret stdin through a byte-oriented subprocess with explicit UTF-8 encoding and no BOM. Verify the resulting credential through a second process, and keep all proof output limited to booleans, modes, ownership checks, and token absence.

### Metadata
- Source: error
- Related Files: .tmp/ssh_secret_pipe.py, .tmp/live_credential_audit.py
- Tags: powershell, ssh, utf-8-bom, credentials, independent-verification

---

## [LRN-20260713-003] best_practice

**Logged**: 2026-07-13T07:15:00-07:00
**Priority**: critical
**Status**: resolved
**Area**: release

### Summary
Windows subtree release archives must disable automatic line-ending conversion.

### Details
The committed Unix scripts and checked-out files used LF, but `git archive HEAD:<subtree>` inherited Windows `core.autocrlf` after leaving the repository-level attributes outside the archived tree. The resulting ZIP converted every Unix entrypoint to CRLF, so the device rejected `set -o pipefail` before the candidate release could switch.

### Suggested Action
Build subtree artifacts with `git -c core.autocrlf=false archive`, then scan every shell script and extensionless Unix launcher in the final ZIP for carriage returns before upload. Keep the transactional updater rollback check in the deployment gate.

### Metadata
- Source: error
- Related Files: .gitattributes, tools/epaperpod-deploy-zip.ps1, inkypi-weather/package/InkyPi/install/update_vendors.sh
- Tags: git-archive, windows, autocrlf, release-artifact, rollback

---

## [LRN-20260714-001] best_practice

**Logged**: 2026-07-14T19:00:00-07:00
**Priority**: high
**Status**: resolved
**Area**: operations

### Summary
Live InkyPi deployment and acceptance must test the exact privileged command, use the pinned IPv4 SSH identity, and follow the current pre-refresh display contract.

### Details
`sudo -n true` incorrectly suggested passwordless deployment was unavailable because sudoers intentionally permits only specific update, service, and acceptance commands. The device hostname could also select an unreachable IPv6 address after a DHCP change even though the pinned key and host record were valid. Finally, an older acceptance helper waited for a post-display presentation receipt after the scheduler had changed to prepare fresh content before display, producing a false failure after a successful internet refresh and hardware write.

### Suggested Action
Inspect `sudo -n -l` and execute the exact whitelisted command when testing privilege. Resolve the device's current IPv4 address and keep `HostKeyAlias`, `IdentitiesOnly`, the repository key, and pinned known-hosts file. For explicit per-plugin acceptance, force one DATA refresh, suppress redundant post-display presentation, request one cache display, and require fresh-data evidence plus a committed hardware-write image.

### Metadata
- Source: conversation
- Related Files: tools/epaperpod-deploy-zip.ps1, tools/live_all_instances_acceptance.py, inkypi-weather/package/InkyPi/src/refresh_task.py
- Tags: sudoers, ssh, ipv4, host-key-alias, acceptance, pre-refresh, hardware-write

---

## [LRN-20260714-002] best_practice

**Logged**: 2026-07-14T19:45:00-07:00
**Priority**: high
**Status**: resolved
**Area**: plugin

### Summary
Cache-only display can freeze a plugin's time-bucket panel rotation even when its selector logic remains intact.

### Details
Sports Dashboard still contained its right-side league priorities and bottom-panel sport rotation, but the scheduler reused the last rendered image on every display. Because no presentation refresh was declared, current time buckets were never evaluated again and the internal panels appeared permanently stuck.

### Suggested Action
For plugins whose composition changes independently of provider freshness, declare presentation refresh and re-render from the current provider caches immediately before display. Do not force every upstream provider during presentation preparation; keep provider refresh policy and display-time composition as separate contracts.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard, inkypi-weather/package/InkyPi/src/refresh_task.py
- Tags: sports-dashboard, presentation-refresh, panel-rotation, cache, scheduler

---

## [LRN-20260714-004] correction

**Logged**: 2026-07-14T21:40:00-07:00
**Priority**: critical
**Status**: resolved
**Area**: scheduler

### Summary
Fresh-before-display must fail closed; a timed-out presentation refresh must never write the last-good cache as if it were current.

### Details
The pre-refresh scheduler initially waited for fresh presentation data but deliberately fell back to the last-good cache after 180 seconds or under hard resource pressure. On physical e-paper this made an old Steam Charts page appear again after a failed provider request, violating the requirement that the first visible page already be fresh.

### Suggested Action
Defer the failed reservation to the tail of the current shuffle round without acknowledging it, persist the queue, and immediately allow another healthy plugin to be selected. Keep the failed member in the pool for a later retry, but never consume or display it until fresh preparation succeeds.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/model.py, inkypi-weather/package/InkyPi/src/refresh_task.py
- Tags: stale-cache, fail-closed, shuffle-bag, e-paper, pre-refresh

---

## [LRN-20260715-003] correction

**Logged**: 2026-07-15T00:20:00-07:00
**Priority**: medium
**Status**: resolved
**Area**: plugin

### Summary
When restoring an InkyPi plugin's original appearance, recover its plugin-specific font from git history instead of applying the current global base font policy.

### Details
Money/StockTracker originally used bundled Jost and Jost SemiBold files. A later global YaHei migration changed it to NotoSansSC at runtime, so restoring only colors or layout could not reproduce the original visual character.

### Suggested Action
Trace the plugin through git history, identify the exact bundled font files and weight mapping, add a test against the loaded font filenames, and scope the exception to that plugin.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py, inkypi-weather/package/InkyPi/src/static/fonts/Jost.ttf, inkypi-weather/package/InkyPi/src/static/fonts/Jost-SemiBold.ttf
- Tags: stocktracker, typography, original-design, jost, git-history

---

## [LRN-20260715-004] best_practice

**Logged**: 2026-07-15T22:45:00-07:00
**Priority**: critical
**Status**: resolved
**Area**: scheduler

### Summary
A five-minute e-paper rotation target needs one end-to-end deadline budget, not only a shorter scheduler sleep.

### Details
The live frame still stalled after polling was tightened because ordinary data renders occupied the single worker, the shuffle bag waited `3 * interval` (15 minutes) before conceding an ineligible remainder, and a day/night transition required an exact-theme cache. The working policy wakes precisely near 300 seconds, reserves and prepares the next distinct member immediately after an automatic display, gives a failed theme/cache refresh 30 seconds before using the authoritative last-good cache, caps production shuffle starvation at 60 seconds, and reserves 60 seconds for the physical Waveshare write. Ordinary background refreshes remain enabled outside the final guarded window. Live proof measured two committed automatic displays 303.294 seconds apart.

### Suggested Action
Treat 300 seconds as the target and 420 seconds as the total operational budget. Test scheduler polling, worker admission, presentation prefetch, shuffle starvation, theme-cache fallback, and physical commit timing together. Prove the result from consecutive persisted `refresh_time` values plus hardware logs, not from configuration constants alone.

### Metadata
- Source: production_debug
- Related Files: inkypi-weather/package/InkyPi/src/model.py, inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/tests/test_model.py, inkypi-weather/package/InkyPi/tests/test_refresh_task.py
- Tags: five-minute-rotation, seven-minute-deadline, prefetch, shuffle-starvation, theme-cache, hardware-write

---

## [LRN-20260715-005] correction

**Logged**: 2026-07-15T22:45:00-07:00
**Priority**: high
**Status**: resolved
**Area**: release

### Summary
Do not build a clean InkyPi release by copying `/opt/inkypi/current`; it includes the installed virtual environment.

### Details
Copying the committed live release directory into `/var/tmp` produced a 500 MB staging tree and a 353 MB ZIP because `venv_inkypi` is installed after extraction. The normal source artifact was only about 147 MB. Reusing the previously verified source ZIP as the device-side baseline, extracting it, overlaying the tested files, and zipping that directory preserved the expected release shape.

### Suggested Action
For device-side micro-releases, always extract a known clean source artifact before overlaying changes. Inspect uncompressed size and archive contents before updater execution, and reject artifacts containing `venv_inkypi` or other installed runtime state.

### Metadata
- Source: error
- Related Files: install/inkypi_update.py, tools/epaperpod-deploy-zip.ps1
- Tags: release-artifact, virtualenv, device-side-packaging, zip, preflight

---
## [LRN-20260716-001] best_practice

**Logged**: 2026-07-16T12:35:41-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend, tests

### Summary
World Cup panel decorations must use only the real gap between UPCOMING and RECENT and must never move either section.

### Details
One additional UPCOMING row leaves enough vertical room for the native 248x13 pitch strip, while two additional rows consume the gap completely. The correct contract is to measure the rendered UPCOMING bottom and RECENT top, center the strip at native size only when both dimensions fit, and omit it otherwise. A fixture with two extra rows initially encoded an impossible expectation and was corrected to one extra row.

### Suggested Action
Keep geometry tests for both a fitting gap and an insufficient gap, and assert that RECENT retains its baseline coordinate in the integration render.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/worldcup_render.py, inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
- Tags: world-cup, pixel-strip, gap-aware-layout, native-size

---

## [LRN-20260716-002] best_practice

**Logged**: 2026-07-16T12:35:41-07:00
**Priority**: medium
**Status**: resolved
**Area**: tests

### Summary
Invoke the InkyPi test wrapper with paths relative to the InkyPi root and give pytest enough parent-process time to exit cleanly.

### Details
`tools/run_inkypi_tests.ps1` changes its working directory to `inkypi-weather/package/InkyPi`, so test paths must be `tests/...`; repository-prefixed paths do not resolve. On this Windows host, a one-second shell timeout can terminate the PowerShell parent after roughly fifteen seconds while leaving the completed pytest child behind, so apparent hangs can be timeout artifacts rather than test failures. Supply the verified Python 3.11 interpreter through `INKYPI_PYTHON311`.

### Suggested Action
Use InkyPi-root-relative test paths, set `INKYPI_PYTHON311` explicitly, and use a timeout comfortably above the observed suite duration.

### Metadata
- Source: error
- Related Files: tools/run_inkypi_tests.ps1
- Tags: pytest, powershell, timeout, python-311, working-directory

---

## [LRN-20260716-003] best_practice

**Logged**: 2026-07-16T12:35:41-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
A secondary worktree may not contain deployment identity files, and an older main-worktree deploy wrapper may target a stale package layout.

### Details
The worktree deploy wrapper safely stopped when its local `.ssh` directory was absent. Copying credentials into the worktree would weaken secret handling, and the main-worktree wrapper attempted an obsolete package directory. The safe recovery was to keep the clean verified source artifact, use direct pinned OpenSSH commands that reference the repository-owned key and known-hosts files, and run the transactional updater without changing secrets or source layout.

### Suggested Action
Before deploying from a worktree, validate both the wrapper's package-root assumption and credential-path resolution. Never copy private keys into a worktree; fall back only to strict-host-key, identity-pinned transport and retain the artifact/updater verification gates.

### Metadata
- Source: error
- Related Files: tools/epaperpod-deploy-zip.ps1, install/inkypi_update.py
- Tags: worktree, deployment, ssh, pinned-host-key, release-artifact

---

## [LRN-20260716-004] best_practice

**Logged**: 2026-07-16T12:35:41-07:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
When the managed Windows patch helper cannot enforce split writable roots, use a narrow, reviewable Git patch fallback.

### Details
The patch helper rejected writes because its restricted-token sandbox could not enforce split writable roots, and its packaged wrapper returned access denied. For already-authorized workspace edits, the reliable fallback was an exact-anchor edit on a temporary copy, automatic diff generation, `git apply --check`, then `git apply`, followed by line-ending and BOM verification. This preserves reviewability and avoids broad script-based rewrites.

### Suggested Action
Retry the normal patch helper once; if the same sandbox failure recurs, keep the fallback scoped to named files and exact anchors, preflight with `git apply --check`, and verify encoding plus the final diff.

### Metadata
- Source: error
- Related Files: .learnings/LEARNINGS.md
- Tags: apply-patch, windows-sandbox, git-apply, encoding

---
