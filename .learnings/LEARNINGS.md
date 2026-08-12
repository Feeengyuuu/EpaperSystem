# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260808-GBW] best_practice

**Logged**: 2026-08-08T19:18:10-07:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Generate e-paper header wordmarks against the measured runtime slot, then theme them from alpha with a text fallback.

### Details
The first img-2 direction was stylish but had an extremely wide visible aspect ratio, so fitting it into the 250x40 header slot made the lettering too short to read. A more compact source was generated and post-processed into a bounded 736x172 alpha-mask asset. At runtime the mask is recolored for day and night themes, applies only to the matching vehicle nickname, and safely falls back to dynamic text if the asset is missing or corrupt. The enlarged vehicle art and wordmark were both validated through the public 800x480 `generate_image()` path rather than private drawing helpers.

### Suggested Action
Measure the target slot, required visible bounding box, and safe gaps before prompting for an image asset. Reject sources whose visible alpha ratio makes the fitted result too small; then use bounded loading, theme tinting, a dynamic fallback, final-pixel tests, and representative day/night previews.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/vehicle_status/vehicle_status.py, inkypi-weather/package/InkyPi/src/plugins/vehicle_status/grey_bullet_wordmark.png, inkypi-weather/package/InkyPi/tests/test_vehicle_status.py
- Tags: vehicle-status, img2, wordmark, e-paper, alpha-mask, theme, fallback
- Pattern-Key: ui.epaper_measured_wordmark_asset
- Recurrence-Count: 1
- First-Seen: 2026-08-08
- Last-Seen: 2026-08-08

### Resolution
- **Resolved**: 2026-08-08T19:18:10-07:00
- **Commit/PR**: local branch
- **Notes**: Four 800x480 previews and public `generate_image()` tests passed independent code and visual review.

---

## [LRN-20260727-003] best_practice

**Logged**: 2026-07-27T19:09:36-07:00
**Priority**: high
**Status**: resolved
**Area**: release-engineering

### Summary
Build Linux deployment archives on Windows with `core.autocrlf=false`, then inspect the ZIP contents for carriage returns before uploading.

### Details
The committed `update_vendors.sh` blob and Windows working-tree file both contained LF line endings, and `.gitattributes` declared `*.sh text eol=lf`. Even so, `git archive` under the repository's Windows `core.autocrlf=true` configuration produced a ZIP entry with CRLF endings. The Pi updater safely rejected the release before switching `current` because Bash parsed `pipefail\r` as an invalid option. Rebuilding the same commit with `git -c core.autocrlf=false archive` restored LF endings. Exact file-list comparison alone would not have detected this content transformation.

### Suggested Action
For every Pi release, run `git -c core.autocrlf=false archive`, compare all ZIP file names with `git ls-tree`, reject runtime or cache paths, and scan every Linux text entry plus extensionless install entry point for carriage returns. After any failed preflight, verify that `current`, PID, restart count, health endpoints, update journal, and uploaded temporary ZIP all remain safe before retrying.

### Metadata
- Source: error
- Related Files: .gitattributes, tools/epaperpod-deploy-zip.ps1, inkypi-weather/package/InkyPi/install/update_vendors.sh
- Tags: windows, git-archive, autocrlf, linux, deployment, preflight, line-endings
- See Also: LRN-20260726-002
- Pattern-Key: release_engineering.force_lf_archive_and_scan_zip_contents
- Recurrence-Count: 1
- First-Seen: 2026-07-27
- Last-Seen: 2026-07-27

### Resolution
- **Resolved**: 2026-07-27T19:09:36-07:00
- **Commit/PR**: local-worktree
- **Notes**: Verified 1,241 Git files, excluded runtime state, scanned 568 Linux text entries with zero carriage returns, and successfully deployed release `sports-ewc-active-detail-a7d3c0e9-lf1`.

---

## [LRN-20260727-002] best_practice

**Logged**: 2026-07-27T18:30:00-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
On a small-memory Pi with an SD card, recurring images should be cached at render size while memory hotness and disk-access timestamps are managed separately.

### Details
Plugin refreshes run in short-lived spawned workers, so a process-local `lru_cache` does not survive the next refresh and cannot prevent repeated full-size decoding. Fixed transparent assets should be resized offline to roughly three to five times their display size, validated from image headers before RGBA conversion, and shipped with the plugin. Repeated network images can use the existing bounded persistent cache, but only as content-addressed render-size derivatives; random or real-time sources must remain opt-in. The live 16 GB card was already about 85 percent full, so increasing every namespace independently would create storage pressure. After removing verified stale deployment artifacts, a 256 MiB global cap balances offline fallback depth with free-space safety and avoids the cold-start churn of a more aggressive 128 MiB cut. Cache hits should always update in-memory LRU order, while durable access timestamps are coalesced to `min(24 hours, TTL / 2)` to reduce SD metadata writes without disabling expiry.

### Suggested Action
When adding a recurring image, first decide whether it is fixed, repeated network content, or intentionally live. Package fixed assets at bounded render size; opt repeated network images into the shared managed cache with a strict namespace and object budget; do not cache live/random images by default. Keep at least 1.5 GiB or 15 percent of the SD card free, and validate both first-decode memory and repeated-hit disk writes.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py, inkypi-weather/package/InkyPi/src/plugins/steam_charts/steam_charts.py, inkypi-weather/package/InkyPi/src/utils/cache_manager.py
- Tags: raspberry-pi, sd-card, image-cache, render-size, spawned-worker, write-amplification, cache-budget
- See Also: LRN-20260727-001
- Pattern-Key: caching.render_size_derivatives_and_coalesced_disk_touches
- Recurrence-Count: 1
- First-Seen: 2026-07-27
- Last-Seen: 2026-07-27

### Resolution
- **Resolved**: 2026-07-27T18:30:00-07:00
- **Commit/PR**: local-worktree
- **Notes**: Bounded the high-risk LoL and Steam static assets, reduced the shared managed-cache ceiling to 256 MiB, and coalesced cache-hit timestamp writes. A common opt-in derivative cache for still-uncached network media remains future work.

---

## [LRN-20260726-004] correction

**Logged**: 2026-07-26T15:39:51-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Keep playlist display cache-only by default, but let Telegram Digest fetch fresh provider data before every one of its own displays.

### Details
The global `display_triggered_refresh_enabled=false` stability policy correctly prevented provider calls during playlist display, but it also suppressed Telegram Digest even though its persisted `refreshOnDisplay` setting and manifest default were already true. The user explicitly wants Telegram to refresh on every rotation encounter while retaining the cache-only policy for other plugins. Telegram's existing presentation transaction is the correct boundary: prepare fresh data off-display, atomically display only a successful prepared image, and reconcile displayed-message state only after the physical commit.

### Suggested Action
Use an explicit manifest capability for the narrow provider-refresh exception. Keep `DISPLAY_CACHE` provider-free, run Telegram through `PRESENTATION_REFRESH`, retain the previous successful image when the provider result is stale or unavailable, and test two consecutive rotation encounters plus the capability-only prepared-display retry path.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/plugins/plugin_manifest.py, inkypi-weather/package/InkyPi/src/plugins/telegram_digest/plugin-info.json, inkypi-weather/package/InkyPi/tests/test_refresh_task.py
- Tags: telegram-digest, cache-only, refresh-on-display, presentation-refresh, provider-opt-in
- Pattern-Key: refresh_scheduler.per_plugin_display_provider_opt_in
- Recurrence-Count: 1
- First-Seen: 2026-07-26
- Last-Seen: 2026-07-26

### Resolution
- **Resolved**: 2026-07-26T16:32:00-07:00
- **Commit/PR**: `66e343a4d41747e910f28a817dc88cd9fdb2d73a`
- **Notes**: Added a Telegram-only manifest opt-in that uses the existing provider-backed presentation transaction before every automatic display while leaving `DISPLAY_CACHE` provider-free for all plugins. Consecutive-rotation coverage proves exactly one provider call per encounter, successful physical commit ordering, old-cache retention on failure, and fair backoff. The focused suite passed 597 tests, the clean archive gate passed 4,781 tests with 46 skips, and release `deploy-20260726T225719Z-tgrefresh-66e343a4` is active with zero restarts. A forced live queue mutation was rejected during safety review, so the production queue was left untouched.

---

## [LRN-20260726-003] best_practice

**Logged**: 2026-07-26T13:28:40-07:00
**Priority**: critical
**Status**: resolved
**Area**: runtime

### Summary
Low-memory services need execution-time admission control and an unconditional supervisor recovery path for clean memory-pressure termination.

### Details
SportsDashboard held the single render worker for several minutes while resident memory climbed until earlyoom sent `SIGTERM`. The service remained inactive because systemd classified that signal as a clean exit and `Restart=on-failure` did not restart it. A scheduler-only resource check would still race with queued work, and a cooperative restart request could still hang behind a non-daemon render worker.

### Suggested Action
Apply resource gates immediately before every renderer intent, persist lane-local deferral attempts and retry times so a blocked plugin cannot starve peers, and keep cached display provider-free. On constrained devices, quarantine unbounded heavyweight renderers behind a margin the hardware cannot accidentally satisfy. Configure the supervisor to restart clean terminations and force the process to a supervised exit only after bounded cleanup when a worker cannot join.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/runtime/runtime_state.py, inkypi-weather/package/InkyPi/src/inkypi.py, inkypi-weather/package/InkyPi/install/inkypi.service
- Tags: low-memory, earlyoom, execution-gate, scheduler-fairness, systemd, supervised-restart
- Pattern-Key: runtime.execution_admission_and_unconditional_supervisor_recovery
- Recurrence-Count: 1
- First-Seen: 2026-07-26
- Last-Seen: 2026-07-26

### Resolution
- **Resolved**: 2026-07-26T13:50:00-07:00
- **Commit/PR**: crash-recovery-hardening
- **Notes**: Added execution-time heavyweight admission, persisted fair deferrals without hiding last-good cache, bounded supervised worker shutdown, and unconditional systemd recovery; 4768 tests passed.

---

## [LRN-20260726-002] best_practice

**Logged**: 2026-07-26T02:00:00-07:00
**Priority**: critical
**Status**: resolved
**Area**: release-engineering

### Summary
An overall device deployment must prove that its source baseline contains the active release before packaging, and the archive must exclude runtime caches and the mutable current display image.

### Details
An overall repair archive was built from `d98efe8`, even though the approved live integration was based on descendant `78c0daab` plus reviewed changes. The deployment was healthy but silently restored older LiveRadar code-drawn icons and removed other already-live integrations. The same packaging path also admitted ignored plugin caches and `src/static/images/current_image.png`, which made stale runtime state capable of crossing release boundaries.

### Suggested Action
Before every overall deployment, record the active release, compare its source commit with the candidate using the commit graph, and stop if the candidate is an ancestor or lacks approved production files. Build from a clean integration branch, audit source-to-archive hashes, and reject plugin `cache` directories, hidden `*_cache` directories, and `current_image.png`.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/install/lib/release_archive.py, inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: deployment, release-baseline, regression, archive-hygiene, runtime-cache, current-image
- Pattern-Key: release_engineering.verify_baseline_and_exclude_runtime_state
- Recurrence-Count: 1
- First-Seen: 2026-07-26
- Last-Seen: 2026-07-26

### Resolution
- **Resolved**: 2026-07-26T02:00:00-07:00
- **Commit/PR**: pending
- **Notes**: Reassembled the final release from `78c0daab`, merged all current fixes, and added archive exclusions and regression tests before redeployment.

---

## [LRN-20260726-001] correction

**Logged**: 2026-07-26T01:45:00-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
A deployed visual fix can regress when the next release is assembled from a different worktree that never received the prior source and binary assets.

### Details
LiveRadar's official transparent platform icons, generated transparent status icons, and Twitch Helix `/users` avatar lookup existed in the worktree used for the earlier device release, but not in the main workspace later used for the overall release archive. The overall deployment therefore replaced the device's working icon/avatar implementation with older code-drawn badges and dropped the local assets even though the prior live release was correct.

### Suggested Action
Before every overall release, compare the chosen source root against the active device release for plugin-local assets and recently repaired code paths, require regression tests that fail when required alpha assets or provider enrichments disappear, and archive only after confirming the release source is the intended integrated worktree.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py, inkypi-weather/package/InkyPi/src/plugins/live_radar/platform_icons, inkypi-weather/package/InkyPi/src/plugins/live_radar/status_icons, inkypi-weather/package/InkyPi/tests/test_live_radar.py
- Tags: worktree, release-source, visual-regression, binary-assets, twitch
- Pattern-Key: release.verify_integrated_worktree_and_plugin_assets
- Recurrence-Count: 1
- First-Seen: 2026-07-26
- Last-Seen: 2026-07-26

### Resolution
- **Resolved**: 2026-07-26T01:45:00-07:00
- **Commit/PR**: operational
- **Notes**: Restored the previous release's exact transparent assets and selective icon/avatar hunks into the main workspace, added red-green regressions, and passed the complete LiveRadar suite plus Ruff before rebuilding the overall release.

---

## [LRN-20260725-002] best_practice

**Logged**: 2026-07-25T16:10:00-07:00
**Priority**: high
**Status**: resolved
**Area**: tests

### Summary
Windows pytest fixture paths must reserve headroom for the deepest nested artifact, not merely resolve `--basetemp` to an absolute path.

### Details
The custom `tmp_path` fixture produced a 270-character nested venv interpreter path inside a long Git worktree. Python 3.14 `ensurepip` failed deterministically there, while the same interpreter created a venv successfully at a 62-character path. Making a relative `--basetemp` absolute fixed pytest semantics but did not control the fixture leaf length.

### Suggested Action
Bound custom pytest fixture slugs, retain a random uniqueness suffix, and add a Windows contract that measures a representative deepest nested path. Keep that path below a documented safety budget before relying on installer or archive tests.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/tests/conftest.py, inkypi-weather/package/InkyPi/tests/test_tmp_path_contract.py, inkypi-weather/package/InkyPi/tests/test_install_update.py
- Tags: windows, pytest, tmp-path, venv, ensurepip, path-length, worktree
- See Also: LRN-20260716-010
- Pattern-Key: windows.pytest_tmp_path_reserves_nested_artifact_headroom
- Recurrence-Count: 1
- First-Seen: 2026-07-25
- Last-Seen: 2026-07-25

### Resolution
- **Resolved**: 2026-07-25T16:10:00-07:00
- **Commit/PR**: local-worktree
- **Notes**: Capped the fixture slug at 32 characters, added a 240-character nested-venv headroom contract, restored all installer tests, and passed the full 4714-test suite.

---

## [LRN-20260725-003] best_practice

**Logged**: 2026-07-25T16:10:00-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Provider-confirmed live state must use one source-freshness rule across routing, event selection, and refresh scheduling.

### Details
A fixed three-hour match window incorrectly dropped a provider-confirmed long-running match, but blindly rolling the deadline let a day-old stale `2H` last-good cache masquerade as live and outrank current fixtures. Applying freshness only in the route was also insufficient because the stale event could still win the main-card live bucket.

### Suggested Action
Keep a bounded normal match window that tolerates short provider outages. Extend it only for a recent `LIVE` or fresh-cache observation, pass the same source state and fetch time into route summaries and render selection, and make stale live events leave both the live bucket and live-refresh lane. Test fresh long-running live, normal-window stale recovery, stale-only fallback, and stale-live plus a future fixture.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/csl.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py, inkypi-weather/package/InkyPi/tests/test_sports_dashboard_csl_data.py, inkypi-weather/package/InkyPi/tests/test_sports_dashboard_csl_route.py
- Tags: sports-dashboard, csl, live-state, source-freshness, last-good, routing, refresh
- See Also: LRN-20260710-006
- Pattern-Key: sports_dashboard.live_source_freshness_shared_across_route_render_refresh
- Recurrence-Count: 1
- First-Seen: 2026-07-25
- Last-Seen: 2026-07-25

### Resolution
- **Resolved**: 2026-07-25T16:10:00-07:00
- **Commit/PR**: local-worktree
- **Notes**: Added source-aware CSL event bucketing and bounded live renewal, closed all review findings, and passed the full 4714-test suite.

---

## [LRN-20260722-003] correction

**Logged**: 2026-07-22T21:54:58-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
Cross-provider club names need league-scoped exact aliases and provider-specific coverage checks at the display boundary.

### Details
ESPN events can localize through ESPN team IDs, but football-data.org uses a different ID domain and returns formal names such as `Como 1907` and `Deportivo Alavés`. Reusing IDs would map the wrong domain, while broad removal of prefixes, founding years, or club suffixes could merge unrelated teams and weaken event reconciliation. A current 96-team five-league comparison found 45 missing formal-name aliases; two additional recent-team aliases were kept for rotation compatibility.

### Suggested Action
Enumerate current provider names per league, add explicit display-only aliases, and validate every provider payload rather than only ESPN. Keep the original provider name and normalized event key for merging, preserve unknown named teams verbatim, and reserve `待定球队` for genuine placeholders.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football_localization.py, inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py, tools/check_club_football_sources.py
- Tags: sports-dashboard, football-data, localization, aliases, provider-id, event-key
- Pattern-Key: sports_dashboard.club_localization_requires_provider_exact_aliases
- Recurrence-Count: 1
- First-Seen: 2026-07-22
- Last-Seen: 2026-07-22

### Resolution
- **Resolved**: 2026-07-22T21:54:58-07:00
- **Commit/PR**: operational
- **Notes**: Added 47 exact aliases, expanded football-data name coverage checks, passed 672 source-tree tests and 668 packaged tests with 4 expected repo-tool skips, deployed `deploy-20260723T043754Z-00fa7fee1086`, and verified `阿拉维斯` plus `科莫` on the exact current display image.

---

## [LRN-20260722-002] best_practice

**Logged**: 2026-07-22T21:54:58-07:00
**Priority**: high
**Status**: resolved
**Area**: release

### Summary
Release line-ending normalization must preserve the exact raw bytes of dependency lock inputs used by the safe-clone decision.

### Details
Normalizing `install/requirements.txt` from CRLF to LF changed its raw SHA-256 even though the dependency text was logically identical. The updater therefore rejected the safe virtual-environment clone, performed a full Raspberry Pi dependency installation, and reached its timeout. Unix entrypoints still require LF, but the dependency lock must remain byte-identical to the active release.

### Suggested Action
Build from a clean known-good archive, normalize only Unix entrypoints and explicitly selected source files, preserve the requirements file raw hash, and audit the final ZIP against the deployed baseline before transfer.

### Metadata
- Source: task_observation
- Related Files: inkypi-weather/package/InkyPi/install/requirements.txt, inkypi-weather/package/InkyPi/install/lib/update_engine.py, inkypi-weather/package/InkyPi/install/lib/release_archive.py
- Tags: release, requirements, crlf, sha256, safe-clone, updater
- Pattern-Key: release.preserve_dependency_lock_raw_bytes_for_safe_clone
- Recurrence-Count: 1
- First-Seen: 2026-07-22
- Last-Seen: 2026-07-22

### Resolution
- **Resolved**: 2026-07-22T21:54:58-07:00
- **Commit/PR**: operational
- **Notes**: Preserved requirements SHA-256 `5da8fbf520d8c7c95d312c8bc8fad7dc30cae9bf916f29511a7e744670db8f00`; both the icon and localization releases used the safe-clone path and committed successfully.

---

## [LRN-20260722-001] best_practice

**Logged**: 2026-07-22T21:54:58-07:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Remote league-logo availability does not guarantee current branding or legibility at e-paper rail size.

### Details
Provider events may omit league logos, cross-provider event merges may fail on team-name variants, and otherwise valid remote logo URLs can expose stale branding. At the 17x15 rail size, an asset can also decode successfully yet lose its recognizable silhouette.

### Suggested Action
Bundle audited local league assets as the primary render source, keep remote logos only as fallback, and test alpha bounds, distinct hashes, theme contrast, and tiny-size opaque-pixel coverage.

### Metadata
- Source: task_observation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football_render.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/assets/logos/club_leagues, inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
- Tags: sports-dashboard, league-logo, local-asset, e-paper, tiny-size, contrast
- Pattern-Key: sports_dashboard.pin_league_icons_and_test_physical_scale
- Recurrence-Count: 1
- First-Seen: 2026-07-22
- Last-Seen: 2026-07-22

### Resolution
- **Resolved**: 2026-07-22T21:54:58-07:00
- **Commit/PR**: operational
- **Notes**: Bundled five distinct 128x128 league icons, added local-first and physical-size regressions, deployed the assets, and reverified all five icons on the final localization display.

---

## [LRN-20260721-006] correction

**Logged**: 2026-07-22T00:20:30-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
Stable provider movie IDs must survive chart parsing and drive same-provider media enrichment; fuzzy cross-provider search must never overwrite authoritative metadata.

### Details
The Maoyan chart identified the 2026 film `八仙！` as movie `1525868`, but the parser discarded that ID and accepted TMDb `results[0]`, which was the 1993 film `笑八仙`. Preserving the ID enabled exact Maoyan detail lookup with ID and normalized-title validation. A second live-proof pass exposed that some authoritative Pipi originals exceeded the safe decoder pixel limit, so same-provider media URLs also need a bounded source-side rendition before download.

### Suggested Action
Carry stable source IDs into cached models, prefer exact same-source detail endpoints, validate returned ID and normalized title, and treat fuzzy cross-source search as a strict fallback with title/year checks. Never overwrite an existing authoritative poster. Normalize trusted image-CDN URLs to a bounded rendition, bump cache state when matching or media semantics change, and reject old-version caches even on refresh-failure fallback.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/box_office_top_movies/box_office_top_movies.py, inkypi-weather/package/InkyPi/src/plugins/china_box_office_top_movies/china_box_office_top_movies.py, inkypi-weather/package/InkyPi/tests/test_box_office_top_movies.py
- Tags: box-office, maoyan, provider-id, tmdb, poster, cache-version, safe-image
- Pattern-Key: provider_enrichment.authoritative_id_and_bounded_media_before_fuzzy_fallback
- Recurrence-Count: 1
- First-Seen: 2026-07-21
- Last-Seen: 2026-07-22

### Resolution
- **Resolved**: 2026-07-22T00:20:30-07:00
- **Commit/PR**: operational
- **Notes**: Passed 663 tests, deployed `deploy-20260722T070613-f790ce612434`, displayed the exact corrected instance on the physical panel, restored all five poster thumbnails, and observed no post-refresh poster warning or application error.

---

## [LRN-20260721-005] correction

**Logged**: 2026-07-21T22:56:00-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
SportsDashboard must reserve “待定球队” for genuine provider placeholders, not for named clubs that miss localization.

### Details
football-data.org supplied resolved club names and crests, but an incomplete Chinese alias table made `_club_team_zh_name` replace `FC Bayern München` and later `Como 1907` with “待定球队”. That label falsely implied the matchup itself was undecided even though the provider had already identified the teams.

### Suggested Action
Translate known aliases when available. For an unmapped but non-placeholder provider name, preserve the provider name; only empty names and explicit TBD/TBA/TBC/unknown values should render as “待定球队”. Keep regression coverage for a known alias, an unmapped named team, and real placeholder values.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football_localization.py, inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
- Tags: sports-dashboard, club-football, localization, placeholder, provider-name
- Pattern-Key: sports_dashboard.pending_team_requires_provider_placeholder
- Recurrence-Count: 1
- First-Seen: 2026-07-21
- Last-Seen: 2026-07-21

### Resolution
- **Resolved**: 2026-07-21T22:56:00-07:00
- **Commit/PR**: operational
- **Notes**: Added the Bayern provider alias plus safe named-team fallback, passed all 610 SportsDashboard tests, deployed `deploy-20260722T053851-54901f8b994d`, and verified the refreshed instance exactly matched the physical display image.

---

## [LRN-20260721-004] correction

**Logged**: 2026-07-21T22:08:01-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
Steam Charts should center only English-only title blocks on the cover's horizontal midline.

### Details
The requested alignment is vertical placement relative to the screenshot, not centered text. In combined Steam Charts rows, an English-only title stays left-aligned to the right of the cover while its one-line or wrapped two-line block is vertically centered against the cover. Chinese-only and Chinese-plus-English bilingual titles remain top-aligned so their hierarchy and readability do not regress. The rule applies only when a real cover image is present and must match in both HTML and PIL fallback rendering.

### Suggested Action
Keep the English-only classification explicit in prepared game data, gate the HTML modifier by that flag and `.has-image`, and reuse the same flag in PIL title geometry. Preserve tests for English one-line/two-line centering plus Chinese and bilingual top alignment.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_charts/steam_charts.py, inkypi-weather/package/InkyPi/src/plugins/steam_charts/render/steam_charts.html, inkypi-weather/package/InkyPi/src/plugins/steam_charts/render/steam_charts.css, inkypi-weather/package/InkyPi/tests/test_steam_charts.py
- Tags: steam-charts, title, cover, alignment, bilingual
- Pattern-Key: steam_charts.english_only_title_cover_midline
- Recurrence-Count: 1
- First-Seen: 2026-07-21
- Last-Seen: 2026-07-21

### Resolution
- **Resolved**: 2026-07-21T22:08:01-07:00
- **Commit/PR**: operational
- **Notes**: Implemented matching HTML and PIL behavior, passed all 47 Steam Charts tests, deployed `deploy-20260722T044653-c1ed6359ac7c`, and verified the refreshed instance image exactly matched the physical display image.

---

## [LRN-20260719-007] correction

**Logged**: 2026-07-19T22:20:00-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
At the start of a new club season, SportsDashboard must default directly to the five-league panel instead of returning to the World Cup panel.

### Details
The five-league implementation had already been live-verified, but a later main-branch reliability recovery omitted its three source modules and title assets. The surviving renderer therefore had no club route and fell back to the historical World Cup page. The user explicitly changed the seasonal product policy: Premier League, La Liga, Bundesliga, Serie A, and Ligue 1 are now the default top-left module. `auto` and `worldcup` remain deliberate manual choices, but missing or invalid settings must resolve to `club`, and the default club route must not probe the World Cup schedule first.

### Suggested Action
When integrating or recovering SportsDashboard, verify that all club-football modules and five wordmark assets are present. Keep tests that assert missing and invalid `footballPanelMode` values select `club`, and that the default route calls only the club renderer. Before live proof, inspect the persisted instance mode as well as source defaults, then require a fresh data job, cache display with forced hardware write, and the actual `/api/current_image` PNG.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/settings.html, inkypi-weather/package/InkyPi/tests/test_sports_dashboard_new_season.py
- Tags: sports-dashboard, club-football, new-season, default-route, world-cup, regression, physical-display
- Pattern-Key: sports_dashboard.new_season_defaults_to_five_leagues
- Recurrence-Count: 1
- First-Seen: 2026-07-19
- Last-Seen: 2026-07-19

### Resolution
- **Resolved**: 2026-07-19T22:20:00-07:00
- **Commit/PR**: operational
- **Notes**: Restored the five-league implementation, added direct-club routing tests, deployed `deploy-20260719-five-leagues-new-season-d1f8d4c900c7`, completed fresh data and physical display jobs, and verified the live 800x480 five-league image.

---

## [LRN-20260719-008] correction

**Logged**: 2026-07-19T22:25:00-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
The five-league SportsDashboard panel must not be anisotropically resized from its approved 536x240 design canvas.

### Details
The live instance retained `worldCupLeftWidth=552` and no explicit top height, so the shared World Cup slot resolved to 552x208. The club renderer always draws at 536x240 and then resizes to the requested slot, expanding width by about 3 percent while compressing height by 13.3 percent. The resulting relative aspect distortion is visually obvious on the physical display even though bounds and size tests pass.

### Suggested Action
Make the club route use an approved native geometry or render responsively at the actual slot without non-uniform image scaling. Add a composed-dashboard regression using missing persisted height and the production left-width override, and assert that club content is not resized to a different aspect ratio. Prove the correction on the physical 800x480 panel before marking this learning resolved.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/worldcup.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/club_football_render.py
- Tags: sports-dashboard, club-football, aspect-ratio, persisted-settings, physical-display
- Pattern-Key: sports_dashboard.club_panel_no_anisotropic_resize
- Recurrence-Count: 1
- First-Seen: 2026-07-19
- Last-Seen: 2026-07-19

### Resolution
- **Resolved**: 2026-07-19T22:44:00-07:00
- **Commit/PR**: operational
- **Notes**: Replaced the fixed 536x240 render-then-resize path with native runtime layout, added production-slot no-resize and five-row-fit regressions, deployed `deploy-20260719-club-native-layout-332141235709`, and verified the 552x208 image through a completed Waveshare hardware write.

---

## [LRN-20260719-006] correction

**Logged**: 2026-07-19T14:36:00-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
LiveRadar may legitimately spend several minutes refreshing because the device reaches mainland China platforms from the United States; freshness is preferred over a short generic timeout.

### Details
The production room list contains more than 30 entries. When the shared batch endpoint returned HTTP 500, the plugin's circuit breaker switched the remaining chunks to individual room requests and completed in about 149 seconds. A proposed eight-second large-batch cutoff would have reduced queue occupancy but replayed saved status instead of attempting fresh cross-border data, which the user explicitly rejected.

### Suggested Action
Keep LiveRadar's plugin-specific long latency budget and individual recovery path. Use the batch circuit breaker to avoid repeated failing batch calls, but do not apply generic short provider deadlines or prefer stale cache solely because this cross-border refresh takes minutes. Verify that the command eventually completes and the scheduler advances.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py, inkypi-weather/package/InkyPi/tests/test_live_radar.py
- Tags: live-radar, cross-border, latency-budget, freshness, circuit-breaker
- Pattern-Key: live_radar.cross_border_long_refresh_budget
- Recurrence-Count: 1
- First-Seen: 2026-07-19
- Last-Seen: 2026-07-19

### Resolution
- **Resolved**: 2026-07-19T14:36:00-07:00
- **Release**: deploy-20260719-plugin-reliability-v5-86f0458e4753
- **Notes**: Reverted the unpublished short-timeout experiment. The deployed refresh ran from 14:32:42 to 14:35:11, then the queue advanced without a command failure.

---

## [LRN-20260719-005] best_practice

**Logged**: 2026-07-19T14:32:00-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
Windows release archives must disable checkout-style CRLF conversion and audit the bytes of Unix entry points before deployment.

### Details
An alternate Git index produced the intended source tree, but `git archive` under the local Windows configuration emitted CRLF bytes for eight `install/*.sh` and extensionless Unix launchers. Source tests and tree hashes did not expose the packaging defect. Re-archiving with `git -c core.autocrlf=false -c core.eol=lf archive` preserved LF bytes; the final archive audit also checked required plugins, excluded temporary/private files, and verified the exact artifact hash.

### Suggested Action
Build Linux release zips with checkout conversion disabled, then inspect archive bytes for carriage returns in every shell script and extensionless launcher. Run affected tests from a fresh extraction of the same hashed archive before upload.

### Metadata
- Source: production_release
- Related Files: tools/epaperpod-deploy-zip.ps1, inkypi-weather/package/InkyPi/install
- Tags: windows, git-archive, crlf, release-audit, exact-artifact
- Pattern-Key: release_archive.windows_git_archive_crlf_conversion
- Recurrence-Count: 1
- First-Seen: 2026-07-19
- Last-Seen: 2026-07-19

### Resolution
- **Resolved**: 2026-07-19T14:29:54-07:00
- **Release**: deploy-20260719-plugin-reliability-v5-86f0458e4753
- **Notes**: Final archive `86f0458e47531687814528dadf90887bc7a6429f78a7936af1c501265fdbaf30` had zero Unix CR bytes and its extracted affected suite passed with 1757 tests.

---

## [LRN-20260719-004] best_practice

**Logged**: 2026-07-19T14:14:00-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
A prepared-bank presentation request that arrives before the first data refresh must be a safe no-change, not an exception or a stale-cache fallback.

### Details
Pixiv's publication-time reserve fixed data jobs that ran close to their hard deadline, but it could not help when the presentation lane ran before the instance received any data slot. The live scheduler therefore logged repeated `Pixiv presentation bank is cold for this instance` errors. Treating only the typed cold-bank condition as `PresentationPreparation(image=None, changed=False)` leaves the current screen unchanged and lets the background data lane hydrate the bank; corruption and other unexpected bank errors still fail visibly.

### Suggested Action
For every prepared-bank plugin, test the ordering where presentation runs before initial data. Return no-change only for an explicit cold/uninitialized state, keep provider access out of presentation, and never substitute an old rendered page.

### Metadata
- Source: production_debug
- Related Files: inkypi-weather/package/InkyPi/src/plugins/pixiv_r18_ranking/pixiv_r18_ranking.py, inkypi-weather/package/InkyPi/src/plugins/pixiv_r18_ranking/presentation_bank.py, inkypi-weather/package/InkyPi/tests/test_pixiv_r18_ranking.py
- Tags: pixiv, presentation-bank, cold-start, no-change, stale-cache
- Pattern-Key: prepared_bank.presentation_before_initial_data
- Recurrence-Count: 1
- First-Seen: 2026-07-19
- Last-Seen: 2026-07-19

### Resolution
- **Resolved**: 2026-07-19T14:29:54-07:00
- **Release**: deploy-20260719-plugin-reliability-v5-86f0458e4753
- **Notes**: Added a typed cold-bank exception and red-green regression; Pixiv plus project-wide suites passed before transactional deployment. The deployed release returned `changed=False` and `image=None` for an injected cold-bank request instead of raising.

---

## [LRN-20260719-003] best_practice

**Logged**: 2026-07-19T12:40:00-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
Plugin reliability audits must combine command outcomes, latest-success staleness, and active-release plugin presence; failure-rate tables alone miss silent missing-plugin regressions.

### Details
The 24-hour refresh log correctly exposed concentrated DATA failures for Pixiv, Weather, and Orbital Signal, but the new-release failure table omitted AI Ecosystem Pulse because it had no post-switch attempt. The saved playlist instance was still visible and its last success was more than 21 hours old, while both AI Ecosystem Pulse and Orbital Signal were absent from the active release plugin tree. Earlier attempts failed with `Plugin config not found`. A command-only dashboard would therefore undercount a completely broken instance as healthy or inactive. The same audit also showed that zero command failures do not imply cadence health: Steam Charts used its fallback renderer successfully but remained many hours beyond its configured interval under sustained queue and resource pressure.

### Suggested Action
For every saved plugin instance, report four independent signals: active artifact presence, latest-success age versus configured cadence, DATA attempts/failures, and degraded fallback warnings. Flag missing-code instances and overdue instances even when attempt count is zero. Add this cross-check to any future runtime reliability report or monitoring endpoint.

### Resolution
Restored the missing AI Ecosystem Pulse and Orbital Signal source trees, bounded repeated provider/media/browser failures, added fresh-data Pillow fallbacks, and strengthened scheduler freshness handling. The full project suite passed and the restored plugins completed real DATA and Waveshare display cycles before the final follow-up release.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/plugins
- Tags: plugin-reliability, active-release, missing-plugin, stale-data, silent-failure, monitoring
- See Also: LRN-20260719-002
- Pattern-Key: runtime_audit.cross_check_attempts_staleness_and_artifact_presence
- Recurrence-Count: 1
- First-Seen: 2026-07-19
- Last-Seen: 2026-07-19

---

## [LRN-20260719-002] best_practice

**Logged**: 2026-07-19T01:12:00-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Reserved rotation preflight can starve an overdue ordinary DATA refresh even after the generic candidate queue has fairness ordering.

### Details
Steam Charts had a saved 3600-second interval and its DATA lane last succeeded at 22:59, so it was due again around 23:59. At 01:10 the service was ready and Steam had received only `DISPLAY_CACHE` commands, while missing-cache or failing rotation members repeatedly received DATA or PRESENTATION work. The reserved-presentation fast path in `_select_independent_refresh_command` returns before `choose_refresh_candidate`, so the fairness policy added for attempted `BOOTSTRAP_MISSING` candidates cannot protect an ordinary interval candidate from repeated rotation-critical admissions. Soft memory pressure and the single renderer make the drift larger, but the priority bypass is the scheduler boundary that explains the starvation.

### Suggested Action
Treat the reserved-member fast path as part of the same global fairness contract. Bound consecutive or repeated reservation-driven provider attempts, honor retry backoff before re-reserving broken members, and guarantee an overdue ordinary DATA candidate a service slot without breaking the five-minute display deadline. Add a regression scenario with an hourly cache-only plugin plus several failing reserved members and prove both bounded Steam staleness and continued rotation progress.

### Resolution
Implemented a scheduler-wide freshness and fairness boundary: due cache-backed instances refresh before display, failed DATA/PRESENTATION work enters retry backoff and cannot immediately reuse stale cache, candidate ordering favors the least recently attempted current due generation, and reservation-driven work grants a bounded concession to ordinary DATA candidates that have been overdue for at least five minutes. The hard-memory-pressure path may still display a valid cache rather than start unsafe work. Focused refresh regressions passed (`402 passed`), and live release `deploy-20260719-refresh-fairness-edd55acfb39d` automatically refreshed Steam Charts from `2026-07-18T22:59:47-07:00` to `2026-07-19T01:59:34-07:00` while a failing `species_radar` presentation repeatedly entered backoff. The refreshed Steam cache was then written successfully to the Waveshare display; the service remained ready with zero restarts.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/runtime/refresh_policy.py, inkypi-weather/package/InkyPi/tests/test_refresh_task.py, inkypi-weather/package/InkyPi/tests/test_refresh_policy.py
- Tags: steam-charts, stale-cache, interval-refresh, rotation-reservation, scheduler-fairness, soft-memory-pressure
- See Also: LRN-20260718-005
- Pattern-Key: refresh_scheduler.reserved_rotation_bypasses_data_fairness
- Recurrence-Count: 1
- First-Seen: 2026-07-19
- Last-Seen: 2026-07-19

---

## [LRN-20260718-005] best_practice

**Logged**: 2026-07-18T23:21:00-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Retrying bootstrap candidates must compete fairly with overdue cadence refreshes.

### Details
`BOOTSTRAP_MISSING` had unconditional priority over interval candidates. Several broken no-cache plugins therefore rotated through their retry backoffs and collectively starved Steam Charts for more than five hours, even though its saved interval remained one hour. Preserve first priority only for a bootstrap candidate that has never been attempted; after an attempt, order it by `last_attempt_at` against ordinary candidates' `due_since`.

### Suggested Action
When changing refresh admission, test a retrying missing-cache candidate against an older interval candidate. Use live per-plugin attempt counts and cache timestamps to distinguish scheduler starvation from provider or renderer failure.

### Metadata
- Source: production_debug
- Related Files: inkypi-weather/package/InkyPi/src/runtime/refresh_policy.py, inkypi-weather/package/InkyPi/tests/test_refresh_policy.py
- Tags: refresh-scheduler, bootstrap, starvation, fairness, steam-charts
- Pattern-Key: refresh_scheduler.retrying_bootstrap_fairness
- Recurrence-Count: 1
- First-Seen: 2026-07-18
- Last-Seen: 2026-07-18

### Resolution
- **Resolved**: 2026-07-18T23:21:00-07:00
- **Release**: deploy-20260719-steam-fairness-4f0cacdbfbdf
- **Notes**: Added a regression test, changed candidate ordering, deployed the fix, and verified the refreshed Steam cache was written to the physical display.

---

## [LRN-20260718-004] best_practice

**Logged**: 2026-07-18T22:02:00-07:00
**Priority**: high
**Status**: resolved
**Area**: tests

### Summary
Mixed dirty files require exact index patches and tests against the staged tree, not the broader working tree.

### Details
The LiveRadar fix touched `refresh_task.py` and `test_refresh_task.py`, but both files already contained unrelated uncommitted scheduler and SportsDashboard work. Staging whole files would have silently published those changes, while testing only the working tree could hide a dependency on them. Applying a narrow patch with `git apply --cached`, materializing the index with `git write-tree` plus `git archive`, and running the full target test file from that snapshot proved the commit independently while preserving the user's worktree.

### Suggested Action
When intended and unrelated changes share files, build and review an explicit cached patch, require `git diff --cached --check`, then run relevant tests from an archive of `git write-tree`. Commit only after the staged snapshot passes; finally verify the remote branch hash after push.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/tests/test_refresh_task.py
- Tags: git, partial-staging, dirty-worktree, staged-tree, verification, push
- Pattern-Key: git.mixed_worktree_verify_staged_tree
- Recurrence-Count: 1
- First-Seen: 2026-07-18
- Last-Seen: 2026-07-18

### Resolution
- **Resolved**: 2026-07-18T22:02:00-07:00
- **Commit/PR**: a1666093
- **Notes**: The staged-tree snapshot passed 352 refresh-task tests, only two intended files were committed, and remote `main` matched the local commit hash after push.

---

## [LRN-20260718-003] best_practice

**Logged**: 2026-07-18T21:45:00-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Reserved rotation presentations must give the same due instance one data attempt before a `NO_CHANGE` presentation can mark it ready.

### Details
LiveRadar displayed a 05:58 cache at 20:55 even though its two-minute data cadence was overdue. The reserved-member fast path bypassed the generic data-before-presentation policy and ran `PRESENTATION_REFRESH` first. Because LiveRadar intentionally uses `PresentationMode.NO_CHANGE` to hold one snapshot for the full five-minute dwell, that presentation completed without fetching data, and the scheduler later displayed the old cache. This was a scheduler contract bug, not a device crash or upstream API failure; a manual data refresh completed successfully and produced a current image.

### Suggested Action
For a reserved presentation candidate, look up a due DATA candidate for the exact instance. Outside hard resource pressure, admit one high-priority data attempt when no attempt occurred after the presentation request, then let presentation preempt ordinary background work. Test both halves so short data intervals cannot starve presentation and no-change presentations cannot bless stale caches.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/runtime/refresh_policy.py, inkypi-weather/package/InkyPi/tests/test_refresh_task.py
- Tags: liveradar, refresh-scheduler, presentation, no-change, stale-cache, rotation
- See Also: LRN-20260716-003
- Pattern-Key: refresh_scheduler.reserved_data_before_no_change_presentation
- Recurrence-Count: 1
- First-Seen: 2026-07-18
- Last-Seen: 2026-07-18

### Resolution
- **Resolved**: 2026-07-18T21:45:00-07:00
- **Commit/PR**: operational
- **Notes**: Added a red-green regression, passed all 358 refresh-task tests, deployed release `deploy-20260719-liveradar-data-first-f930fd64c131`, refreshed LiveRadar, and matched the displayed image hash to the current cache.

---

## [LRN-20260718-001] insight

**Logged**: 2026-07-18T17:54:00-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
A timed-out rotation preflight can become a tight scheduler loop when deferring the reserved item does not produce a different eligible member.

### Details
On the live device, the same `backtothedate` presentation request was timed out and immediately selected again roughly every three seconds for more than nine minutes. The service and HTTP endpoints stayed responsive, but one Python thread consumed about 35 percent CPU and no new display commit occurred. Two eligibility boundaries mattered: moving a reservation to the queue tail is not progress unless another currently displayable member exists, and a presentation that fails after admission must release its reservation and leave the display candidate set until its current request's retry deadline. Without both rules, the same broken plugin can regain the critical path while healthy plugins remain pending.

### Suggested Action
Base deferral on the current eligible cache set, retain the reservation when no alternative can make progress, and prioritize its presentation preparation. If that preparation fails, release the automatic-rotation reservation and exclude the failed request during its retry backoff so another cache or background data refresh can advance. Test a last-member queue, a multi-member queue with only one eligible cache, and failure-to-backoff recovery. On the live device, verify the release log, absence of repeated preflight warnings during backoff, a different plugin's hardware display commit, a changed current-image ETag, and restored readiness.

### Metadata
- Source: live_device
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/model.py
- Tags: rotation, presentation-preflight, scheduler-loop, readiness, high-cpu, live-device
- Pattern-Key: runtime.rotation_preflight_no_progress_loop
- Recurrence-Count: 2
- First-Seen: 2026-07-18
- Last-Seen: 2026-07-18

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
- Related Files: inkypi-weather/package/InkyPi/src/blueprints/plugin.py, inkypi-weather/package/InkyPi/src/security, tools/live_all_instances_acceptance.py, inkypi-weather/package/InkyPi/tests/test_live_all_instances_acceptance.py
- Tags: acceptance, public-api, csrf, credentials, ownership, redaction
- Pattern-Key: acceptance.public_api_status_vocabulary
- Recurrence-Count: 2
- First-Seen: 2026-07-12
- Last-Seen: 2026-07-22

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

## [LRN-20260716-005] best_practice

**Logged**: 2026-07-16T13:10:43-07:00
**Priority**: high
**Status**: resolved
**Area**: plugin

### Summary
A completed manual refresh job is not proof that the rendered image was promoted to the plugin cache.

### Details
Sports Dashboard treated every forced composite containing FRESH_CACHE as STALE_CACHE, even when all panels were trusted LIVE or fresh-cache sources. The job therefore completed successfully while inkypi_skip_cache silently prevented promotion, and the following display job wrote an old LPL image. Forced rendering must accept both LIVE and FRESH_CACHE while continuing to reject stale, local-fallback, or untrusted sources.

### Suggested Action
After manual refresh, verify both the job result and the plugin-instance cache Last-Modified/hash before displaying it. Cover mixed LIVE plus FRESH_CACHE provenance with a regression test and keep stale/local fallback tests fail-closed.

### Metadata
- Source: production_debug
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py, inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
- Tags: manual-refresh, provenance, fresh-cache, cache-promotion, live-proof

---

## [LRN-20260716-006] best_practice

**Logged**: 2026-07-16T13:47:45-07:00
**Priority**: high
**Status**: resolved
**Area**: plugin

### Summary
A sports source-state label is a cache-provenance contract, not merely diagnostic text.

### Details
The EWC detail loader returned fresh remote matches as EWC DETAIL, but the provenance classifier only trusts explicit LIVE or CACHE states. A successful manual render was therefore marked LOCAL_FALLBACK and never promoted. Manual force was also blocked by soft daily budgets on key-free ESPN and hub providers even though automatic refreshes still need those budgets.

### Suggested Action
Label successful remote loads with LIVE, and cover every new source-state string with provenance tests. Explicit force may bypass internal soft budgets only for key-free providers; never bypass keyed or vendor-enforced quotas.

### Metadata
- Source: production_debug
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/esports.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/worldcup.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/nba.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/offseason_hub.py
- Tags: ewc, provenance, live, manual-refresh, soft-budget

---

## [LRN-20260716-007] best_practice

**Logged**: 2026-07-16T14:33:16-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
Full application preflight needs a slow-device timeout distinct from post-switch health timeout.

### Details
The transactional updater safely rejected a candidate before activation because the Raspberry Pi exceeded the hard-coded 120-second app preflight limit after intensive dependency hashing. The CLI health timeout controls only post-switch readyz and cannot fix this stage. The failed update correctly removed staging while leaving current, the service, and the uploaded artifact intact.

### Suggested Action
Keep the full no-hardware app probe, but allow 600 seconds for cold-cache Raspberry Pi storage. Verify pre-switch failure leaves current untouched, and still require post-switch readyz plus a plugin-specific manual refresh before accepting the release.

### Metadata
- Source: production_debug
- Related Files: inkypi-weather/package/InkyPi/install/lib/update_engine.py, inkypi-weather/package/InkyPi/tests/test_install_update.py
- Tags: updater, preflight, timeout, raspberry-pi, rollback, live-proof

---

## [LRN-20260716-008] correction

**Logged**: 2026-07-16T18:02:24-07:00
**Priority**: high
**Status**: pending
**Area**: frontend

### Summary
The pit-telemetry F1 SportsDashboard redesign was rejected and must remain paused.

### Details
The implemented 556x268 F1 panel, including the generated header strip and dense two-column telemetry layout, did not meet the user's visual expectations. It was not merged, deployed, or pushed. Treat commit `12d8e3bf19c1` on local branch `codex/paused-f1-dashboard-redesign` as a recoverable experiment, not an approved design baseline.

### Suggested Action
When F1 work resumes, restart with small visual-direction mockups and explicit approval before rebuilding or reconnecting runtime rotation. Do not reuse this layout by default.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/f1_render.py
- Tags: sports-dashboard, f1, ui, img-2, paused, rejected-direction
- Pattern-Key: sports_dashboard.f1_rejected_pit_telemetry_direction
- Recurrence-Count: 1
- First-Seen: 2026-07-16
- Last-Seen: 2026-07-16

---

## [LRN-20260716-009] best_practice

**Logged**: 2026-07-16T18:37:56-07:00
**Priority**: high
**Status**: resolved
**Area**: config

### Summary
SecretSchema changes must regenerate both compatibility artifacts before the full suite can pass.

### Details
Adding a new secret alias only to `install/api_key_registry.json` does not affect the runtime schema, while changing only `src/config/secret_schema.json` leaves the generated registry and `.env.example` stale. The repository contract compares both artifacts byte-for-byte with `SecretSchema` output.

### Suggested Action
Update `src/config/secret_schema.json`, then run `install/configure_api_keys.py --generate-artifacts`. Verify `tests/test_secret_schema.py` and `tests/test_secret_schema_plugin_contract.py` before the full suite.

### Metadata
- Source: error
- Related Files: inkypi-weather/package/InkyPi/src/config/secret_schema.json, inkypi-weather/package/InkyPi/install/configure_api_keys.py, inkypi-weather/package/InkyPi/install/api_key_registry.json, inkypi-weather/package/InkyPi/.env.example
- Tags: secret-schema, generated-artifacts, api-key-registry, env-example, tests
- Pattern-Key: config.secret_schema_regenerate_artifacts
- Recurrence-Count: 1
- First-Seen: 2026-07-16
- Last-Seen: 2026-07-16

### Resolution
- **Resolved**: 2026-07-16T18:37:56-07:00
- **Commit/PR**: 9cc2e326
- **Notes**: Regenerated both artifacts and verified the full suite with 3971 passing tests.

---

## [LRN-20260716-010] best_practice

**Logged**: 2026-07-16T18:37:56-07:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
Windows pytest fixture paths can outgrow normal worktree deletion limits.

### Details
`git worktree remove --force` unregistered the temporary worktree but stopped with `Filename too long`, leaving an unregistered directory containing paths up to 357 characters. The safe recovery was to confirm the target remained under `.worktrees`, confirm it no longer appeared in `git worktree list`, confirm no `.git` metadata remained, and then delete only that residual directory through the Windows extended-length path prefix.

### Suggested Action
Keep pytest temp roots short for temporary worktrees. If cleanup still fails, verify provenance and Git registration before using `\\?\` long-path removal on the exact orphaned directory.

### Metadata
- Source: error
- Related Files: tools/run_inkypi_tests.ps1
- Tags: windows, git-worktree, pytest, long-path, cleanup
- Pattern-Key: windows.worktree_remove_long_path_residual
- Recurrence-Count: 1
- First-Seen: 2026-07-16
- Last-Seen: 2026-07-16

### Resolution
- **Resolved**: 2026-07-16T18:37:56-07:00
- **Commit/PR**: operational
- **Notes**: Removed only the verified unregistered main-validation residual and confirmed the target no longer existed.

---

## [LRN-20260716-011] correction

**Logged**: 2026-07-16T19:10:54-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
SportsDashboard visual reviews should default to a directly shared 800x480 PNG, not an interactive HTML companion.

### Details
The user rejected the browser HTML comparison flow and asked to see a PNG screenshot instead. For this project, the useful review artifact is the complete e-paper canvas with the proposed panel composited into its real slot, so unchanged neighboring panels and physical-scale text density remain visible.

### Suggested Action
For future SportsDashboard UI exploration, render or composite the candidate locally at 800x480, preserve unchanged dashboard regions, validate image dimensions and external logo loading, and attach the PNG directly in chat. Use HTML only as an internal rendering implementation detail when needed.

### Metadata
- Source: user_feedback
- Related Files: output/playwright/club-football-b-preview.png
- Tags: sports-dashboard, ui-review, png, 800x480, visual-preview
- See Also: 2026-06-21-sports-dashboard-pc-render-direct-display-preview.md
- Pattern-Key: sports_dashboard.ui_review_png_first
- Recurrence-Count: 1
- First-Seen: 2026-07-16
- Last-Seen: 2026-07-16

### Resolution
- **Resolved**: 2026-07-16T19:10:54-07:00
- **Commit/PR**: operational
- **Notes**: Replaced the HTML review flow with a verified 800x480 PNG using the current SportsDashboard image as the unchanged base.

---

## [LRN-20260718-002] best_practice

**Logged**: 2026-07-18T20:06:00-07:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
SportsDashboard empty schedule slots should use context-specific transparent artwork instead of generic empty-state text.

### Details
EWC has a manifest-defined game set, so one generic filler cannot preserve the identity of the currently selected game. The reliable implementation is one transparent RGBA placeholder per official game slug, rendered only into unoccupied UPCOMING or RECENT row slots. When the World Cup has no remaining UPCOMING event, the same visual gap should transition to a five-major-leagues preview rather than displaying `No more World Cup schedule`. Generated artwork must be normalized to the exact row dimensions, keep transparent corners, and be proven on the physical 800x480 display.

### Suggested Action
Keep placeholder coverage tied to the EWC game manifest, validate every asset's slug, dimensions, RGBA mode, and transparent corners, and retain a rendering test for both the missing EWC row and empty World Cup UPCOMING branch. After deployment, force a data refresh and hardware display, then require the plugin preview and `/api/current_image` hashes to match.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/esports_render.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/worldcup_render.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/assets/decor/ewc_game_placeholders
- Tags: sports-dashboard, ewc, world-cup, transparent-placeholder, img-2, live-proof
- Pattern-Key: sports_dashboard.context_specific_empty_slot_art
- Recurrence-Count: 1
- First-Seen: 2026-07-18
- Last-Seen: 2026-07-18

### Resolution
- **Resolved**: 2026-07-18T20:06:00-07:00
- **Commit/PR**: operational
- **Notes**: Added 25 game-specific EWC assets plus the five-league World Cup preview, passed 539 SportsDashboard tests, deployed release `deploy-20260719-sports-placeholders-b3814c3f90b3`, and verified the live hardware image.

---

## [LRN-20260719-001] best_practice

**Logged**: 2026-07-19T00:27:28-07:00
**Priority**: high
**Status**: resolved
**Area**: config

### Summary
A current SportsDashboard release can still replay an old page when persisted per-source live switches are all false.

### Details
The device source hashes and active release matched the workspace, but the displayed SportsDashboard image was the exact 20:02 cache at 23:42. A manual data refresh produced current World Cup, PGA, and EWC content, proving providers, fallbacks, and rendering were healthy. The saved instance contained the retired `liveRefreshEnabled=false` master plus all seven per-source live switches explicitly false, so `get_live_refresh_state` produced no displayed-instance live candidate after the temporary World Cup window expired. The safe repair was a one-time startup migration that matches only this full legacy signature, enables the seven per-source switches, and persists a completion marker; individual opt-outs and later deliberate all-off settings remain respected.

### Suggested Action
When SportsDashboard appears old, compare current and plugin-cache hashes, check the cache timestamp, verify deployed source hashes, then inspect saved instance settings before changing renderer or scheduler code. Prove the repair with a fresh data cache, a cache-only hardware write, an automatic `source: live / intent: live_refresh` event, the exact follow-up display, and matching current/cache hashes after the physical panel sleeps.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/config.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py, inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/tests/test_config_env_key_aliases.py
- Tags: sports-dashboard, stale-cache, persisted-settings, live-refresh, one-time-migration, physical-display
- Pattern-Key: sports_dashboard.legacy_all_live_switches_disabled
- Recurrence-Count: 1
- First-Seen: 2026-07-19
- Last-Seen: 2026-07-19

### Resolution
- **Resolved**: 2026-07-19T00:27:28-07:00
- **Commit/PR**: operational
- **Notes**: Deployed `deploy-20260719-sports-stale-repair-1e6e1d7133a2`; migration repaired one instance, seven live switches read true, automatic live refresh and follow-up display ran, and final current/cache hashes matched.

---

## [LRN-20260719-002] correction

**Logged**: 2026-07-19T23:56:24-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
LiveRadar status freshness and live screenshot freshness require separate clocks, and provider fallback work must fit inside the status interval.

### Details
The user requires both the plugin's internal status refresh time and continued screenshot updates while a streamer remains live. Reusing `cacheSeconds` as the default media TTL couples unrelated behavior, while sequential room fallbacks can take longer than the next status interval and make an otherwise correct cache policy look stale. The reliable contract is: status cache is fresh only while age is strictly less than `cacheSeconds`; visible live media has an independent 60-second default TTL; and one provider attempt has a deadline before the next status interval so it cannot keep issuing individual retries indefinitely.

### Suggested Action
For live dashboards, test the exact TTL boundary rather than only fresh/stale examples. Keep status and media caches independent, cap provider work at `cacheSeconds` minus a scheduler margin, preserve last-good data on failure, and prove both clocks separately on the deployed device.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py, inkypi-weather/package/InkyPi/tests/test_live_radar.py
- Tags: liveradar, status-cache, live-screenshot, ttl, refresh-deadline, stale-display
- Pattern-Key: live_dashboard.independent_status_media_refresh_clocks
- Recurrence-Count: 1
- First-Seen: 2026-07-19
- Last-Seen: 2026-07-19

### Resolution
- **Resolved**: 2026-07-19T23:56:24-07:00
- **Commit/PR**: operational
- **Notes**: Added exact-boundary tests for status and screenshot TTLs plus a per-refresh network budget; full InkyPi suite passed with 4177 tests.

---

## [LRN-20260720-001] correction

**Logged**: 2026-07-20T01:12:56-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
A short cache TTL does not refresh a displayed plugin unless the scheduler can discover an active live-refresh contract.

### Details
LiveRadar correctly re-fetched a live screenshot once rendering was invoked after its 60-second TTL, but its manifest still declared `supports_live_refresh=false` and it inherited the inactive base scheduler hook. As a result, the TTL was only enforced on a later manual or playlist render and could not keep screenshots moving while LiveRadar remained displayed. The complete contract requires both `supports_live_refresh=true` and a side-effect-free `get_live_refresh_state` that reads the matching warm status cache, becomes active only while a successful room is live, and returns the shorter of the status and media TTLs.

### Suggested Action
For every plugin freshness fix, verify two separate layers: the cache decides whether a render should fetch, and the scheduler decides whether another render happens at all. Add manifest, hook, offline/error deactivation, and no-side-effect tests; then prove a real automatic live-lane success, changed media hashes, and a new physical display commit without submitting a manual refresh.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py, inkypi-weather/package/InkyPi/src/plugins/live_radar/plugin-info.json, inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/tests/test_live_radar.py
- Tags: liveradar, live-refresh, scheduler, cache-ttl, manifest-capability, physical-display
- Pattern-Key: live_dashboard.ttl_requires_scheduler_activation
- Recurrence-Count: 1
- First-Seen: 2026-07-20
- Last-Seen: 2026-07-20

### Resolution
- **Resolved**: 2026-07-20T01:12:56-07:00
- **Commit/PR**: 99652719
- **Notes**: Enabled displayed-instance live refresh, passed 4179 tests, deployed `deploy-20260720-liveradar-live-6e7a4219716a`, and observed an automatic live-lane success with six changed screenshot hashes and a new hardware display commit.

---

## [LRN-20260720-002] best_practice

**Logged**: 2026-07-20T19:06:53-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
Weather latest-success timestamps can represent a fresh-data Pillow fallback rather than a successful normal HTML render.

### Details
On `ColoredEpaperFrame`, Weather continued to fetch valid OpenWeather data and update its instance timestamp, but Chromium 150 repeatedly timed out while converting the Weather HTML page to PNG. The active release then deliberately cached the fresh-data `PIL SAFE MODE` fallback, so `/playlist` reported a recent successful refresh even though the user-visible layout was degraded. On 2026-07-20 there were 29 matching Chromium timeouts and 29 Weather fallback renders; the latest attempt spent the full 60-second browser timeout before falling back. The 416 MB device also had substantial swap use, and Chromium itself warned that devices below 1 GB are unsupported for this workload.

### Suggested Action
When Weather looks abnormal, check the instance PNG and pair latest-success age with `Chromium render timed out` plus `Weather HTML render failed` warnings. Treat provider freshness and visual-render health as separate signals. Preserve the approved HTML/CSS/Chart.js UI: first repair the browser lifecycle and prove the original screenshot live, then cache only successful original screenshots as the failure fallback. Do not silently replace the product UI with a hand-drawn approximation or merely increase the timeout.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/weather/weather.py, inkypi-weather/package/InkyPi/src/plugins/weather/render/weather.html, inkypi-weather/package/InkyPi/src/utils/browser_renderer.py
- Tags: weather, chromium-150, pillow-fallback, latest-success, visual-degradation, raspberry-pi, swap-pressure
- See Also: LRN-20260710-009, LRN-20260719-003
- Pattern-Key: weather.refresh_success_can_be_visual_fallback
- Recurrence-Count: 1
- First-Seen: 2026-07-20
- Last-Seen: 2026-07-20

### Resolution
- **Resolved**: 2026-07-20T21:55:53-07:00
- **Commit/PR**: operational
- **Notes**: Reduced Chromium virtual-time budget from the outer 60-second deadline to 2 seconds, restored the original Weather template as the only primary renderer, added last-good original screenshot fallback, deployed `deploy-20260721-final-fresh-ui-3f98caa77a38`, and proved the original 800x480 live layout with visible forecast borders.

---

## [LRN-20260720-003] correction

**Logged**: 2026-07-20T21:58:00-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
An approved plugin screenshot is a UI contract, and visual acceptance must use current live media rather than fixtures or fallback layouts.

### Details
Weather's native redraw and Steam Charts' low-memory Pillow-first path were technically functional but changed layouts the user had already approved. A Steam fixture preview also hid real covers and an old instance image still showed a 04:41 refresh time. The correct acceptance chain is original renderer first, current provider data, real covers/screenshots, in-image timestamp verification, and live-device proof. Fallback renderers may preserve availability but must not silently become the normal layout solely because memory is constrained.

### Suggested Action
Before changing a renderer, compare against the last approved live screenshot and preserve its hierarchy. For image-forward plugins, reject placeholder-only mocks as final evidence. Verify the visible timestamp, real media, full text, and alignment together; a successful refresh task does not prove the displayed content is current.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/weather/weather.py, inkypi-weather/package/InkyPi/src/plugins/weather/render/weather.html, inkypi-weather/package/InkyPi/src/plugins/steam_charts/steam_charts.py, inkypi-weather/package/InkyPi/src/plugins/steam_charts/render/steam_charts.css
- Tags: approved-ui, live-proof, real-media, visible-timestamp, weather, steam-charts
- Pattern-Key: plugin_ui.approved_renderer_and_live_media_are_acceptance_contract
- Recurrence-Count: 1
- First-Seen: 2026-07-20
- Last-Seen: 2026-07-20

### Resolution
- **Resolved**: 2026-07-20T21:55:53-07:00
- **Commit/PR**: operational
- **Notes**: Restored both original HTML renderers, proved current Steam covers and timestamps, aligned cover/title tops, wrapped complete titles, and retained native renderers only as explicit or failure fallbacks.

---

## [LRN-20260720-004] best_practice

**Logged**: 2026-07-20T21:58:00-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
Plugin refresh deadlines cover provider I/O and rendering together, so independent fetches and repeated layout measurements must both be bounded.

### Details
Daily AI News initially spent about 46 seconds on sequential market fallbacks and then evaluated 21 complete font layouts per news column. The task hit its 120-second deadline even though each phase worked in isolation. Fetching the six independent quotes concurrently while preserving output order, and using ten representative font sizes with content-preservation tests, reduced the live refresh to 97.11 seconds. Sports composite regions follow the same independence rule: football, lower sports, and esports must fetch, cache, and fail closed separately rather than promoting or blocking the whole image together.

### Suggested Action
Budget the complete refresh path, parallelize only independent I/O, preserve deterministic presentation order, and cap expensive fit searches with representative candidates plus no-truncation tests. For composite dashboards, keep per-region provenance, cache keys, and exception boundaries independent.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py
- Tags: refresh-deadline, bounded-layout, concurrent-fetch, deterministic-order, independent-regions
- Pattern-Key: plugin_refresh.total_deadline_requires_bounded_independent_phases
- Recurrence-Count: 1
- First-Seen: 2026-07-20
- Last-Seen: 2026-07-20

### Resolution
- **Resolved**: 2026-07-20T21:55:53-07:00
- **Commit/PR**: operational
- **Notes**: Daily AI News completed live in 97.11 seconds; SportsDashboard rendered independently sourced football, main-sports, and esports regions on the deployed device.

---

## [LRN-20260720-005] best_practice

**Logged**: 2026-07-20T21:58:00-07:00
**Priority**: high
**Status**: resolved
**Area**: release

### Summary
Windows release staging must normalize CRLF bytes without invoking a mismatched GNU `sed`, and live log evidence must redact query-string secrets.

### Details
A direct Windows call to GNU `sed -i 's/\r$//'` corrupted final `r` characters in Python identifiers, such as changing `URLError`; that archive was correctly rejected before deployment. The safe workflow is byte-wise CRLF-to-LF normalization, normalized-content comparison against the source tree, archive-entry byte comparison, then transactional deployment. When collecting failure evidence, redact key, appid, token, api_key, access_token, and bearer values before logs leave the device.

### Suggested Action
Use the repository release builder on a clean staging tree, normalize only line endings with encoding-safe byte or .NET operations, require zero normalized-content mismatches, and inspect key archive entries before upload. Never use cross-platform `sed -i` for release normalization on this Windows workspace; always apply the standard secret-redaction filter to diagnostic logs.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/install/lib/release_archive.py, tools/epaperpod-deploy-zip.ps1
- Tags: windows, crlf, release-archive, byte-verification, secret-redaction
- Pattern-Key: release.windows_lf_normalization_requires_byte_verification
- Recurrence-Count: 1
- First-Seen: 2026-07-20
- Last-Seen: 2026-07-20

### Resolution
- **Resolved**: 2026-07-20T21:55:53-07:00
- **Commit/PR**: operational
- **Notes**: Deployed the verified LF-only archive with SHA-256 `3f98caa77a38393fca5faac7f0176ebb6084249365bb76221bf6ea9a90daace8`; service remained active with zero restarts.

---

## [LRN-20260721-001] best_practice

**Logged**: 2026-07-21T17:21:28-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
HTML renderer circuit breakers must be scoped to a stable failure domain instead of one process-wide switch.

### Details
A Weather Chromium timeout opened a single global HTML circuit for five minutes, so unrelated plugins such as Steam Charts immediately received a render failure without getting their own Chromium attempt. This made one expensive or malformed document look like a system-wide renderer outage and caused several plugins to fall back together.

### Suggested Action
Pass a stable `plugin_id:template` failure domain through the shared renderer. Cool down repeated failures for the same domain, but allow unrelated templates to attempt rendering. Keep a regression test proving same-domain suppression and cross-domain isolation.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/utils/browser_renderer.py, inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py, inkypi-weather/package/InkyPi/tests/test_browser_renderer.py
- Tags: chromium, circuit-breaker, failure-domain, plugin-isolation
- Pattern-Key: plugin_renderer.circuit_breakers_are_failure_domain_scoped
- Recurrence-Count: 1
- First-Seen: 2026-07-21
- Last-Seen: 2026-07-21

### Resolution
- **Resolved**: 2026-07-21T17:54:24-07:00
- **Commit/PR**: operational
- **Notes**: Scoped circuits and negative-cache keys by stable renderer domain, bounded the domain table, passed all 4203 tests in four isolated shards, deployed `deploy-20260722T003652-45cf72e5ce7c`, and observed no post-deploy Steam/Weather timeout or fallback; one unrelated Tech Pulse URL screenshot timeout remained isolated.

---

## [LRN-20260721-002] best_practice

**Logged**: 2026-07-21T17:21:28-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
Retry fairness must compare service age across due generations; current-generation precedence can starve a failed plugin indefinitely.

### Details
Weather's backoff had expired after five minutes, but its due generation was marked already attempted. Continuously arriving fresh-due candidates therefore always sorted ahead of it, leaving Weather without another data attempt for roughly seventeen hours. Backoff correctness alone did not guarantee eventual service.

### Suggested Action
Keep true bootstrap first attempts at highest priority, then sort ordinary due candidates by the older of their current due time or last attempt as appropriate. Test an old failed retry against a newer first attempt from another plugin and prove that a successful choice updates fairness for the next pass.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/runtime/refresh_policy.py, inkypi-weather/package/InkyPi/tests/test_refresh_policy.py
- Tags: scheduler, fairness, starvation, retry, backoff
- See Also: LRN-20260629-005, LRN-20260629-006
- Pattern-Key: refresh_scheduler.retry_fairness_across_due_generations
- Recurrence-Count: 1
- First-Seen: 2026-07-21
- Last-Seen: 2026-07-21

### Resolution
- **Resolved**: 2026-07-21T17:54:24-07:00
- **Commit/PR**: operational
- **Notes**: Removed current-generation precedence from ordinary due ordering while preserving bootstrap priority; the regression suite passed and the deployed scheduler automatically selected the previously starved Weather instance for a background data refresh at 17:48.

---

## [LRN-20260721-003] correction

**Logged**: 2026-07-21T17:21:28-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
An unversioned plugin-internal screenshot cache must not masquerade as a successful current render.

### Details
Weather reused `original-html-{size}-{theme}.png` after a fresh data fetch and HTML failure. The filename did not encode instance, location, settings, data, template version, or age, so an old safe-mode image could be returned indefinitely with a fresh visible refresh time. The worker then exposed a completed job even when the generated result was deliberately not promoted.

### Suggested Action
Keep last-good screenshots only in the outer instance/version/theme-scoped cache. If the product renderer fails, raise or mark the result non-cacheable, preserve the previous approved image, record failure/backoff, and finish the job as failed rather than completed. Test both cache preservation and the externally visible job status.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/weather/weather.py, inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/tests/test_weather_theme_context.py, inkypi-weather/package/InkyPi/tests/test_refresh_task.py
- Tags: weather, screenshot-cache, stale-image, provenance, job-status
- See Also: LRN-20260720-002, LRN-20260630-003
- Pattern-Key: plugin_cache.last_good_must_be_outer_scoped_and_fail_honestly
- Recurrence-Count: 1
- First-Seen: 2026-07-21
- Last-Seen: 2026-07-21

### Resolution
- **Resolved**: 2026-07-21T17:54:24-07:00
- **Commit/PR**: operational
- **Notes**: Removed Weather's internal screenshot cache, made degraded DATA jobs fail honestly, protected Steam HTML caches from emergency fallback replacement, and proved current 17:49 Steam and 17:51 Weather HTML images on the physical display with matching instance/current hashes.

---

## [LRN-20260725-001] correction

**Logged**: 2026-07-25T14:35:40-07:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
Platform-identification icons must come from current official online assets, never from a crop of the user's reference screenshot.

### Details
The LiveRadar reference screenshot showed the desired site-tab icon style, but it was a visual direction reference rather than an asset source. The correct implementation retrieves each current official favicon or press asset, selects the highest-resolution official layer available, verifies real alpha transparency, and normalizes it mechanically onto a transparent canvas. The user also explicitly rejected added badge borders and background fills, so the final icons must be pasted directly while retaining their own colors.

### Suggested Action
For future platform-icon work, inspect the current official site or brand asset page, record the source URL and retrieval date, verify RGBA alpha extrema and transparent corners, and inspect the final icon on both light and dark production backgrounds. Do not crop, trace, or reconstruct an icon from a user screenshot unless the user explicitly requests that method. Do not add a badge shell when the requested visual is a transparent standalone mark.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/platform_icons, inkypi-weather/package/InkyPi/src/plugins/live_radar/status_icons, inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py, inkypi-weather/package/InkyPi/tests/test_live_radar.py
- Tags: live-radar, official-assets, favicon, transparent-png, screenshot-reference, no-border
- Pattern-Key: visual_assets.platform_icons_use_current_official_transparent_sources
- Recurrence-Count: 1
- First-Seen: 2026-07-25
- Last-Seen: 2026-07-25

### Resolution
- **Resolved**: 2026-07-25T14:35:40-07:00
- **Commit/PR**: local-worktree
- **Notes**: Removed screenshot-derived attempts, bundled current official Douyu, Bilibili, and Twitch assets with source records, verified true alpha, removed badge borders and fills, and checked 800x480 day/night previews.

---

## [LRN-20260725-004] best_practice

**Logged**: 2026-07-25T16:19:14-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Current third-party API repairs must combine official web documentation, a live response probe, and local data-path tracing.

### Details
LiveRadar's Twitch Helix path reported healthy stream status while every Twitch avatar remained empty. Local tracing showed that `/helix/streams` never supplied an avatar, but the decisive external check was Twitch's current API reference: `profile_image_url` belongs to `/helix/users`. A live probe also proved the CDN and decoder worked, exposed an aggregator value of `User not found: ...` masquerading as an avatar URL, and caught the real CDN hostname `jtvnw.net` while the request-header matcher only covered `ttvnw.net`. Any one evidence source alone would have left part of the defect unresolved.

### Suggested Action
When a plugin depends on a changing external service, search the current official documentation and probe a safe real response before finalizing the diagnosis. Keep optional enrichment failures from discarding fresh primary status, validate external media values as HTTP(S) URLs, and test live, offline, disabled-enrichment, and upstream-failure branches.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py, inkypi-weather/package/InkyPi/tests/test_live_radar.py
- Tags: live-radar, twitch, helix, internet-research, official-docs, live-probe, avatar, input-validation
- See Also: LRN-20260725-001
- Pattern-Key: external_integrations.official_docs_live_probe_and_local_trace
- Recurrence-Count: 1
- First-Seen: 2026-07-25
- Last-Seen: 2026-07-25

### Resolution
- **Resolved**: 2026-07-25T16:19:14-07:00
- **Commit/PR**: local-worktree
- **Notes**: Added Twitch `/helix/users` avatar enrichment with graceful degradation, rejected non-URL avatar values, corrected the Twitch CDN Referer match, passed 107 LiveRadar tests and the full 4719-test suite, and rendered a live-network 800x480 avatar preview.

---

## [LRN-20260725-005] best_practice

**Logged**: 2026-07-25T16:56:11-07:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
SportsDashboard main-card badge enlargement should use independent vertical offsets for the team name, meta row, and odds row.

### Details
Increasing the CSL main badge scale from 1.2 to 1.4 naturally moved the team-name anchor down by 6px. Moving every lower line by the same amount would waste the card's bottom safety area and preserve the overly loose rhythm. Keeping separate `main_team_points_offset` and `main_team_odds_offset` values let the name move with the badge while the lower rows moved only 4px and 3px. The final card retained 30px below the odds box and preserved 4px between synthetic meta and odds glyphs.

### Suggested Action
For future SportsDashboard identity-card scaling, parameterize each vertical relationship, verify final pixel bounding boxes in live/upcoming/recent states, and keep default offsets unchanged for unrelated competitions.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/csl.py, inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/worldcup_render.py, inkypi-weather/package/InkyPi/tests/test_sports_dashboard_csl_render.py
- Tags: sports-dashboard, csl, team-badge, vertical-spacing, pixel-geometry
- Pattern-Key: sports_dashboard.main_card_independent_vertical_offsets
- Recurrence-Count: 1
- First-Seen: 2026-07-25
- Last-Seen: 2026-07-25

### Resolution
- **Resolved**: 2026-07-25T16:56:11-07:00
- **Commit/PR**: local-worktree
- **Notes**: Implemented 1.4 badge scale with independent 11px and 10px lower-row offsets; 44 CSL tests and the full 4725-test suite passed.

---

## [LRN-20260727-001] correction

**Logged**: 2026-07-27T16:10:00-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
Low-memory protection must preserve the plugin's required detail contract, not silently redefine an overview card as success.

### Details
The EWC sidebar remained renderable after its original detail-page parser was bounded, but it showed only competition cards. That output protected the 416 MiB device while dropping the user-visible match contract: teams or participants, score, stage, status, and scheduled time. The correct repair used the official RSC response, scanned only to the bounded `initialStructures` value, stopped at the matching array close, and kept the existing match mapping. Live validation exposed a second boundary: even when the heaviest region ran first, its EWC parser peak could overlap LoL, Valve, and image allocations and trip the 70 MiB guard. The stable shape is a dedicated short-lived EWC prefetch worker followed by an esports renderer that reads only the atomically committed EWC cache. Because that cache is a cross-process hand-off boundary, a successful network parse is insufficient: the prefetch worker must write a unique publication token, read it back from disk, and verify that the exact cache-only selection is reproducible; otherwise it must report an explicit degraded hand-off to the last durable cache. A protected live canary then exposed a third boundary: the esports renderer still fetched Valve's HLTV/OpenDota data even when a timed LoL or EWC candidate made Valve mathematically unable to win. Lazy elimination based on the existing candidate sort contract removed needless provider work without changing the selected card, but the next canary proved it was not the remaining peak. The actual 70+ MiB jump came after selection, when local LCK/LPL logos as large as 5000x3513 were converted to full-size RGBA and copied into a separate alpha plane before being reduced to roughly 40-100 display pixels. The durable fix is to package bounded official thumbnails, reject oversized local assets from image headers before decode, avoid alpha-plane copies, and cache only the small render-ready result. A final live image exposed a separate selection regression: replacing an ongoing EWC competition overview with its future detailed match changed the card from phase 0 to phase 1, so an earlier LCK match displaced it. Competition activity and detailed-match timing are separate facts: promote a fresh upcoming detail only when the active competition source is trusted, demote it to an ordinary upcoming match when that overview is stale, and never let an overview-only card outrank another provider's real match.

### Suggested Action
Define the minimum user-visible contract before introducing a stability fallback. For EWC, competition metadata alone is not detailed-match success. Preserve competition activity, focused match phase, and each source's provenance separately, then prefer bounded streaming, early termination, provider-specific prefetch workers, write/read cache attestation, selection-dominance lazy loading, cache-only composition, packaged render-size image assets, bounded small-image caches, and between-region parent memory reclamation; keep resource guards intact and verify the final plugin image and physical display.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/esports.py, inkypi-weather/package/InkyPi/src/runtime/sports_isolated_renderer.py, inkypi-weather/package/InkyPi/tests/test_sports_ewc_bounded_memory_contract.py, inkypi-weather/package/InkyPi/tests/test_sports_isolated_renderer.py
- Tags: sports-dashboard, ewc, low-memory, detail-contract, selection-phase, streaming-json, isolated-worker, cache-attestation, lazy-provider-loading, image-cache, logo-decode, malloc-trim
- See Also: LRN-20260720-003, LRN-20260716-006, LRN-20260727-002
- Pattern-Key: stability_fallbacks.preserve_required_detail_contract
- Recurrence-Count: 1
- First-Seen: 2026-07-27
- Last-Seen: 2026-07-27

### Resolution
- **Resolved**: 2026-07-27T16:10:00-07:00
- **Commit/PR**: local-worktree
- **Notes**: Restored bounded EWC match parsing, retained the 70 MiB worker guard, separated EWC network parsing from the multi-provider esports composition worker, verified the durable cache hand-off before enabling cache-only composition, skipped Valve live fetches whenever the original selector proves Valve cannot win, bounded recurring logo assets before RGBA decode, and separated real EWC match priority from overview-only scheduling. The earlier suggestion to promote a future EWC detail from an active competition to phase 0 was superseded by LRN-20260727-002 after physical-display validation.

---

## [LRN-20260727-002] correction

**Logged**: 2026-07-27T22:27:00-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
Esports detail completeness and competition provenance must not override the customized right-sidebar selection contract.

### Details
The EWC detail repair correctly restored teams, stage, status, score, and scheduled time, but an active overall competition flag was then allowed to promote a future detailed match into live phase 0. Removing that promotion exposed a second regression: upcoming candidates were sorted by absolute time before configured provider priority, so an earlier LCK match still displaced LPL. The intended contract is independent of data richness: compare actual match phase first; within the same phase preserve LPL, LCK, EWC, then Valve; use scheduled time only inside the same provider priority. An overall competition being active is context and provenance, not proof that its focused future match is live.

### Suggested Action
Keep match phase, provider priority, schedule, detail completeness, and competition provenance as separate fields. Select by phase, then configured provider priority, then time. Accept a live deployment only after the actual instance image and physical display show the requested provider; disappearance of the previously wrong provider is not sufficient proof.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/esports.py, inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
- Tags: sports-dashboard, lpl, lck, ewc, sidebar, provider-priority, selection-phase, physical-display
- See Also: LRN-20260727-001, LRN-20260716-006
- Pattern-Key: selection_contracts.keep_phase_priority_and_provenance_orthogonal
- Recurrence-Count: 1
- First-Seen: 2026-07-27
- Last-Seen: 2026-07-27

### Resolution
- **Resolved**: 2026-07-27T22:27:00-07:00
- **Commit/PR**: local-worktree
- **Notes**: Removed future-detail phase promotion, restored configured provider priority ahead of time within the same phase, added LPL-over-EWC and LPL-over-LCK regression tests, and required live instance plus physical-display proof.

---

## [LRN-20260728-001] best_practice

**Logged**: 2026-07-28T14:16:11-07:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Retained background jobs must leave at least one queue running slot for urgent display work.

### Details
Moving deferred work from QUEUED to RUNNING can bypass pending-queue reserved-slot accounting. If retained jobs are allowed to reach the queue capacity, later MANUAL or DISPLAY work cannot be taken even when submission reserved capacity was configured.

### Suggested Action
Bound retained background ownership to at most `queue.capacity - 1`, reject or defer excess intake immediately, and cover the capacity-two case with a regression test that proves a manual display can still run.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/runtime/refresh_queue.py
- Tags: scheduler, queue, starvation, display
- Pattern-Key: harden.retained_queue_capacity
- Recurrence-Count: 1
- First-Seen: 2026-07-28
- Last-Seen: 2026-07-28

### Resolution
- **Resolved**: 2026-07-28T14:16:11-07:00
- **Commit/PR**: local branch
- **Notes**: Ian retained ownership is capped below queue capacity and the capacity-two urgent-display regression passes.

---

## [LRN-20260728-002] best_practice

**Logged**: 2026-07-28T15:06:25-07:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
Install GitHub CLI without administrator access or WinGet from the verified official portable release.

### Details
On a non-admin Windows machine where WinGet is unavailable, use GitHub's official amd64 ZIP and checksum release assets. Require the published SHA-256 to match and the executable to have a valid GitHub, Inc. Authenticode signature before installing under `%LOCALAPPDATA%\Programs` and adding its `bin` directory to the user PATH. Keep CLI authentication separate: `gh auth status` may remain logged out while existing Git credentials still permit a direct push. When command policy rejects a monolithic PowerShell installer, split download, verification, extraction, installation, PATH update, and cleanup into bounded auditable steps.

### Suggested Action
Reuse the verified portable-install sequence and leave `gh auth login` as an explicit interactive follow-up.

### Metadata
- Source: error
- Related Files: .learnings/LEARNINGS.md
- Tags: windows, github-cli, portable-install, checksum, authenticode, path
- Pattern-Key: tooling.verified_portable_github_cli_install
- Recurrence-Count: 1
- First-Seen: 2026-07-28
- Last-Seen: 2026-07-28

### Resolution
- **Resolved**: 2026-07-28T15:06:25-07:00
- **Commit/PR**: local-worktree
- **Notes**: Installed GitHub CLI 2.96.0 from the official portable release, verified its checksum and signer, added it to the user PATH, and removed the installer staging directory.

---

## [LRN-20260811-001] best_practice

**Logged**: 2026-08-11T20:12:42-07:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
A target release preflight must require an explicit capability handshake from the already-installed updater before requesting a new host migration.

### Details
The transactional updater executes the candidate release's preflight code. That candidate can understand a new migration even when the currently installed updater does not. Without a handshake, the old updater could accept and activate the candidate while silently ignoring the requested host mutation. Require the installed updater to pass a fixed capability token, persist a release-bound expectation, and revalidate the request in the activation coordinator before switching pointers.

### Suggested Action
For every future host migration, add a two-stage capability release, an explicit installed-updater handshake, a strict allowlisted request, and coordinator-side fail-closed validation.

### Metadata
- Source: conversation
- Related Files: inkypi-weather/package/InkyPi/install/preflight.py, inkypi-weather/package/InkyPi/install/lib/update_engine.py
- Tags: updater, migration, preflight, compatibility, fail-closed
- Pattern-Key: updater.host_migration_capability_handshake
- Recurrence-Count: 1
- First-Seen: 2026-08-11
- Last-Seen: 2026-08-11

### Resolution
- **Resolved**: 2026-08-11T20:12:42-07:00
- **Commit/PR**: local branch
- **Notes**: Implemented and covered by headless_mode_v1 handshake, activation, failure, and power-loss tests.

---

## [LRN-20260811-002] correction

**Logged**: 2026-08-11T23:24:00-07:00
**Priority**: high
**Status**: resolved
**Area**: runtime

### Summary
Prepared-presentation acceptance must distinguish preparing a successor from committing that prepared image on the next display.

### Details
A first manual display can successfully write the current canonical image and enqueue preparation of the next bank selection without advancing `presentation_receipt`. The receipt is committed only when the exact prepared request is consumed by a later display. An acceptance loop that waits for a receipt immediately after the initial display can therefore time out even though production rotation is healthy. The display endpoint also defaults `request_presentation` to false, so a verification flag must send the value explicitly rather than omit the payload.

### Suggested Action
Before counting display rounds, require a prepared `presentation_request`. For each counted round, capture its request ID, explicitly submit `request_presentation=true`, require the resulting receipt to contain that exact ID, then wait for a distinct prepared successor. Verify displayed and canonical hashes together and keep warm-up displays separate from counted receipt advances.

### Metadata
- Source: error
- Related Files: tools/live_all_instances_acceptance.py, inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/tests/test_live_all_instances_acceptance.py
- Tags: presentation-refresh, receipt, display, acceptance, prepared-bank
- Pattern-Key: acceptance.prepared_presentation_requires_next_display_commit
- Recurrence-Count: 1
- First-Seen: 2026-08-11
- Last-Seen: 2026-08-11

### Resolution
- **Resolved**: 2026-08-11T23:24:00-07:00
- **Commit/PR**: local branch
- **Notes**: The tracked acceptance tool now sends the explicit presentation request flag, and the live Magazine sequence verifies exact request-to-receipt linkage across counted displays.

---
