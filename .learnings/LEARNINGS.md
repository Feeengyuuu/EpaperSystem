# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---
## [LRN-20260604-008] best_practice

**Logged**: 2026-06-04
**Priority**: high
**Status**: active
**Area**: epaper, comic-covers, source-fallback

### Summary
ComicCovers should default to `mixed` and recycle Comic Vine recent covers before falling back to GCD metadata.

### Details
On ColoredEpaperFrame, the `ComicCovers` instance had no persisted `sourceMode`, so the previous default `gcd` skipped the available Comic Vine key. GCD then returned many issues with no cover URL plus 403/429 image/API failures, causing the plugin to render a text-only metadata card. After switching the default and instance setting to `mixed`, Comic Vine returned 24 candidates with real image URLs. A second issue appeared when the day's Comic Vine priority candidates were already marked seen: the ordering code dropped to GCD candidates while lower-quality GCD candidates were still unexhausted. Recycling the Comic Vine priority pool before GCD fallback restored real cover images.

### Suggested Action
For `gcd_comic_covers`, keep `DEFAULT_SOURCE_MODE = "mixed"` and make the settings page default to mixed. When `match_quality == "comicvine_recent"` candidates exist but are all seen for the day, recycle that priority pool while avoiding the immediate last issue when possible. When updating an existing instance through `/update_plugin_instance/<name>`, include `plugin_id=gcd_comic_covers`; the persisted config field is `plugin_settings`.

### Metadata
- Source: production_debug
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`, `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/settings.html`, `inkypi-weather/package/InkyPi/tests/test_gcd_comic_covers.py`, `.learnings/gcd_comic_covers_comic_vine_priority_recycle.md`
- Tags: gcd-comic-covers, comic-vine, gdc, 429, 403, plugin-settings, random-playlist

---
## [LRN-20260604-007] best_practice

**Logged**: 2026-06-04
**Priority**: high
**Status**: active
**Area**: epaper, deployment, device-targeting

### Summary
Do not deploy to the first reachable InkyPi device; verify the target hostname is `ColoredEpaperFrame`.

### Details
During LoLInfo random-playlist deployment, `192.168.1.188` briefly disappeared and `192.168.1.187` was reachable. That reachable device was `EpaperPodBeta`, not the intended target. The correct live target came back at `192.168.1.188`, and SSH `hostname` returned `ColoredEpaperFrame`.

### Suggested Action
Before any live deployment for the main frame, verify both HTTP reachability and SSH identity with `hostname`. If a scan finds `.187` or another reachable InkyPi-like device, treat it as non-target unless it identifies as `ColoredEpaperFrame`. If a wrong target is modified during recovery, remove any added playlist instance before finishing.

### Metadata
- Source: deployment_correction
- Related Files: `tools/add_lol_info_to_random_list.py`, `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `.learnings/colored_epaper_frame_hostname_guard.md`
- Tags: epaperpod, colored-epaper-frame, hostname, deployment, device-identity, random-playlist

---
## [LRN-20260604-006] best_practice

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, playlist

### Summary
LoLInfo must carry `refreshOnDisplay=true` when it is added to the random playlist.

### Details
The LoLInfo skin-art panel intentionally rotates a Data Dragon splash from the player's commonly used and high-mastery champions. The standard playlist display path uses the cached instance image first, so the instance needs `refreshOnDisplay=true` in its persisted settings; otherwise LoLInfo can be in the random playlist but still show the same cached skin art until its normal refresh interval expires.

### Suggested Action
When adding or updating `LoLInfo` in `DailyDoseOfDay`, include `refreshOnDisplay=true` with the Riot ID settings. Use `tools/add_lol_info_to_random_list.py` as an idempotent helper once the live device is reachable.

### Metadata
- Source: production_playlist_setup
- Related Files: `tools/add_lol_info_to_random_list.py`, `.tmp/update_lol_info_instance.py`, `inkypi-weather/package/InkyPi/src/plugins/lol_info/settings.html`, `inkypi-weather/package/InkyPi/src/refresh_task.py`, `.learnings/lol_info_random_playlist_refresh_on_display.md`
- Tags: lol-info, random-playlist, refresh-on-display, skin-art, cache

---
## [LRN-20260604-005] best_practice

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, layout, visual

### Summary
LoLInfo skin splash art should render as an unobstructed image, with the Riot logo in the lower middle gap.

### Details
After adding the right-side skin-art pool, the first pass placed champion and skin-name labels over the bottom of the splash image and kept the Riot logo near the image's upper-left edge. The user corrected this: the skin artwork should fill its framed area without text overlays, and the Riot logo should sit in the lower empty space between the overview metrics and the skin image.

### Suggested Action
For future LoLInfo visual changes, do not overlay champion names, skin names, or source labels on top of the skin splash frame. Keep `_overview_layout()` responsible for positioning the large right-side art frame and the lower-middle Riot logo, and verify on the 800x480 render before deploying.

### Metadata
- Source: production_visual_refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `inkypi-weather/package/InkyPi/tests/test_lol_info.py`, `.learnings/lol_info_skin_art_full_image_layout.md`
- Tags: lol-info, riot-logo, skin-art, no-overlay, layout, 800x480

---
## [LRN-20260604-004] insight

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, riot-api, assets

### Summary
LoLInfo can use Riot Data Dragon for champion splash art, loading art, and skin art.

### Details
Riot's authenticated player APIs return account, match, ranked, mastery, and champion identifiers, but champion artwork is provided through Data Dragon static assets. Individual champion JSON files contain a `skins` array with each skin's `num`; splash art uses `/cdn/img/champion/splash/{ChampionKey}_{num}.jpg`, and loading art uses `/cdn/img/champion/loading/{ChampionKey}_{num}.jpg`. Some skin entries are chromas and may not have separate splash images; entries with `parentSkin` should be treated as chromas.

### Suggested Action
For future LoLInfo visual expansions, use Match-V5 or mastery data to choose champion keys, then use Data Dragon champion detail JSON to list skin `num` values. Cache splash/loading images locally and fall back to champion square icons if an art URL is missing or blocked. Do not imply the match API exposes the exact cosmetic skin used by the player unless verified separately.

### Metadata
- Source: official_riot_docs
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `.learnings/lol_info_riot_champion_skin_art_assets.md`
- Tags: lol-info, riot-api, data-dragon, splash-art, skin-art, champion-assets

---
## [LRN-20260604-003] best_practice

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, layout

### Summary
LoLInfo bottom overview should reserve the right side for future refresh content.

### Details
The user requested that the lower overview area be squeezed left so the right side remains open for a planned future refresh module. The implemented layout keeps the bottom panel full width, but constrains the Riot logo and six overview metrics to the left portion by reserving roughly a quarter of the panel width on the right.

### Suggested Action
For future LoLInfo bottom-panel edits, preserve the right reserved area unless the user explicitly fills it. Keep the existing left-packed metric layout and bump `STYLE_VERSION` after coordinate changes so cached images do not hide the update.

### Metadata
- Source: production_visual_refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `.learnings/lol_info_bottom_right_reserved_region.md`
- Tags: lol-info, bottom-panel, reserved-region, layout, 800x480

---
## [LRN-20260604-002] best_practice

**Logged**: 2026-06-04
**Priority**: high
**Status**: active
**Area**: epaper, lol-info, color-ui

### Summary
LoLInfo should follow the vintage comic process-color rule instead of a blue/cyan dashboard palette.

### Details
The live LoLInfo page initially used a dark blue panel system with saturated cyan borders, which made the screen read as a generic digital dashboard rather than the EpaperSystem formal color rule. The correction keeps night readability but maps the UI to `docs/color-ui-guidelines.md`: process-black background, warm-paper text and linework, and limited flat accent colors for cyan, amber, red, and green.

### Suggested Action
For future LoLInfo color changes, avoid cyan as the dominant border or background color. Keep panel borders warm-paper, use black/dark grounds for night mode, and reserve cyan for small informational labels. Bump `STYLE_VERSION` after color-token changes so cached images do not mask the update.

### Metadata
- Source: production_visual_refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `docs/color-ui-guidelines.md`, `.learnings/lol_info_comic_palette_compliance.md`
- Tags: lol-info, color-ui, comic-palette, process-black, warm-paper

---
## [LRN-20260604-001] best_practice

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, layout

### Summary
LoLInfo needs compact fitted route labels and explicit bottom padding in the mastery panel.

### Details
On the live `LoLInfo` page for `NA1 / AMERICAS`, the route label looked slightly misaligned when rendered as a larger fixed-position string, and the third mastery row sat too close to the right panel's bottom border. The fix was to render the route with `_single()` using the tiny font and a slash separator, move the mastery list upward, reduce icon size and row step, and use a larger right padding for mastery bars.

### Suggested Action
For future LoLInfo layout edits, keep route labels width-fitted because `AMERICAS` is much longer than `ASIA`. Maintain visible bottom and right padding around the third mastery row on the 800x480 layout.

### Metadata
- Source: production_visual_refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `.learnings/lol_info_spacing_for_long_routes_and_mastery.md`
- Tags: lol-info, spacing, route-label, mastery-panel, 800x480

---
## [LRN-20260603-021] insight

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, riot-api

### Summary
LoLInfo needs the exact Riot ID tagLine for account lookup.

### Details
Account-V1 could not find the North America account with guessed tags `NA1`, `NA`, `US1`, or `US`, and Summoner-V4 by-name returned 403 on the live key. After the user supplied tagLine `pog`, Account-V1 returned a PUUID and the live LoLInfo instance refreshed correctly.

### Suggested Action
For future LoLInfo account changes, ask for `gameName#tagLine` first. Use `platformRoute=na1` and `regionalRoute=americas` for North America after the exact tag is known.

### Metadata
- Source: production_probe
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `.learnings/lol_info_riot_id_tagline_lookup.md`
- Tags: lol-info, riot-id, tag-line, na1, account-lookup

---
## [LRN-20260603-020] deployment

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, deployment

### Summary
`systemctl restart inkypi` can stay in `stop-sigterm` until the 4 minute timeout.

### Details
During the LoLInfo deploy, the old service stayed `ActiveState=deactivating`, `SubState=stop-sigterm`, `ExecMainPID=2154` until systemd reached `TimeoutStopUSec=4min`, killed the old Python processes, and started the new service. Follow-up non-interactive `sudo kill` did not work because sudo required a terminal/password.

### Suggested Action
After restarting `inkypi`, poll systemd state and wait for `Started inkypi.service`, `waitress - Serving on http://0.0.0.0:80`, and `/playlist` HTTP 200 before triggering manual display refresh.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `.learnings/colored_epaper_frame_systemctl_restart_timeout.md`
- Tags: colored-epaper-frame, epaperpod, deploy, systemd, waitress, restart-timeout

---
## [LRN-20260603-019] insight

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, riot-api

### Summary
LoLInfo can use PUUID-based Riot endpoints and needs logo background cleanup.

### Details
The live `Riot_KEY` returned useful account, summoner, league-by-puuid, champion mastery, match, challenge, rotation, and platform status data. The older encrypted summoner id is not needed for ranked data because `/lol/league/v4/entries/by-puuid/{puuid}` works. The provided Riot logo asset has an opaque light/checkerboard background, so it must be cleaned and tinted before rendering on the dark e-paper dashboard.

### Suggested Action
For future LoLInfo work, keep Riot ID, platform route, and regional route as settings. Use Data Dragon `zh_CN` for champion labels/icons, treat Spectator 404 as "未在对局中", and keep brand logo preprocessing in the plugin rather than pre-editing the source files.

### Metadata
- Source: local_probe
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `tools/probe_riot_api.py`, `.learnings/lol_info_riot_api_and_brand_assets.md`
- Tags: lol-info, riot-api, puuid, data-dragon, logo-assets

---
## [LRN-20260603-018] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, dota-profile-dashboard, localization

### Summary
Dota hero names should be localized from the official `language=schinese` hero list.

### Details
OpenDota returned English `heroStats.localized_name` on the live Dota profile page. The working fix was to fetch `https://www.dota2.com/datafeed/herolist?language=schinese`, cache `name_loc` by hero id, and overwrite hero display names before rendering. The official list returned `獸` for Primal Beast, so normalize that value to Simplified Chinese `兽`.

### Suggested Action
For future Dota profile localization work, key localized names by numeric hero id, not English names. Bump `STYLE_VERSION` after localization changes so the plugin does not reuse a stale rendered cache.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/dota_profile_dashboard/dota_profile_dashboard.py`, `.learnings/dota_profile_schinese_hero_names.md`
- Tags: dota-profile-dashboard, schinese, hero-names, opendota, dota2-datafeed

---
## [LRN-20260603-017] workflow

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, dota-profile-dashboard, deployment

### Summary
Dota Profile live deploy uses the project SSH key, not default Windows identities.

### Details
On `ColoredEpaperFrame` at `192.168.1.188`, default SSH failed because `C:\Users\super\.ssh` contained only known-host files. The working deploy path used `.ssh\epaperpod_codex_20260525` with `IdentitiesOnly=yes`, uploaded the new plugin to `/usr/local/inkypi/src/plugins/dota_profile_dashboard`, validated it in `/usr/local/inkypi/venv_inkypi`, restarted `inkypi`, added `DotaProfile` to `DailyDoseOfDay`, and verified the instance image matched `/api/current_image`.

### Suggested Action
For future new-plugin deploys on this device, start with the project key path and absolute Windows OpenSSH binaries. After a manual display, compare `/plugin_instance_image/<playlist>/<plugin>/<instance>` with `/api/current_image` by SHA256 before claiming the screen is showing the plugin.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/dota_profile_dashboard/dota_profile_dashboard.py`, `.learnings/dota_profile_live_deploy_key_and_data_limits.md`
- Tags: dota-profile-dashboard, epaperpod, ssh-key, deploy, opendota, current-image

---
## [LRN-20260603-016] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, dota-profile-dashboard, icon-rendering

### Summary
Dota hero icons need internal white pixel normalization, not only a black container.

### Details
OpenDota `heroStats.icon` images served through the Steam CDN can contain opaque white or near-white pixels inside the 32x32 source image. A black square container alone still leaves a visible white frame and white interior patches. The dashboard should remove the separate outline and normalize near-white source pixels to black before and after resizing.

### Suggested Action
For compact Dota hero rows, avoid drawing icon outlines and apply near-white to black cleanup in `_square_icon()`. Use local preview plus a small pixel smoke test before deploying to the e-paper device.

### Metadata
- Source: local_preview
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/dota_profile_dashboard/dota_profile_dashboard.py`, `inkypi-weather/package/InkyPi/tests/test_dota_profile_dashboard.py`, `.learnings/dota_profile_hero_icon_black_normalization.md`
- Tags: dota-profile-dashboard, hero-icons, white-border, black-background, epaper

---
## [LRN-20260603-015] insight

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, dota-profile-dashboard, opendota

### Summary
Dota hero portraits should use OpenDota `heroStats` metadata with Steam CDN image URLs.

### Details
`Austinparisi42/Dota2HeroDatabase` does not contain a standalone hero image archive. Its JavaScript calls `https://api.opendota.com/api/heroStats` and reads the `icon`/`img` fields. The old page concatenates those paths with `https://api.opendota.com`, but that image URL returned 404 during the local probe. The same relative path works through `https://cdn.cloudflare.steamstatic.com`.

### Suggested Action
For Dota profile UI work, keep `heroStats` as the metadata source, generate hero image URLs from `icon`/`img`, try Steam CDN first, and retain the OpenDota asset base only as a fallback.

### Metadata
- Source: local_probe
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/dota_profile_dashboard/dota_profile_dashboard.py`, `tools/preview_dota_profile_dashboard.py`, `.learnings/dota_hero_icon_source_opendota_cdn.md`
- Tags: dota-profile-dashboard, opendota, hero-icons, steam-cdn

---
## [LRN-20260603-014] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, dota-profile-dashboard, windows

### Summary
Local plugin preview cache writes may need a direct-write fallback on Windows.

### Details
While building the Dota Profile Dashboard local preview, the rendered image completed but JSON cache commit failed at `Path.replace()` in `.tmp` with `[WinError 5] Access is denied`. The plugin now keeps the normal temp-file path, catches `PermissionError` around the replace step, writes the final JSON directly, and best-effort removes the temp file.

### Suggested Action
For new UI-first plugins tested locally on this Windows machine, prefer mock data and include a cache write fallback when using temp-file replacement. This keeps preview scripts usable even when pytest or network dependencies are unavailable.

### Metadata
- Source: local_preview
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/dota_profile_dashboard/dota_profile_dashboard.py`, `tools/preview_dota_profile_dashboard.py`
- Tags: dota-profile-dashboard, preview, windows, cache, permissionerror, mock-data

---
## [LRN-20260603-013] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, steam-profile-dashboard

### Summary
Steam dashboard real game names should all show left-side app logos sized from their row font.

### Details
The Steam page now treats displayed game names as structured rows with `appid`, `name`, and optional prefix/suffix. Current game, recent/realtime rows, commonly played TOP games, and friend activity can all render a Steam app logo immediately left of the game name. Icon size should come from the row font/line height, and rows that do not fit must skip the whole row rather than leaving an orphan icon.

### Suggested Action
For future Steam dashboard changes, do not flatten game rows into plain strings until after icon placement is decided. Use `img_icon_url` first and Steam app capsule fallback for missing hashes. Bump `STEAM_DASHBOARD_STYLE_VERSION` after changing icon coverage so live verification renders a fresh image.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Tags: steam-profile-dashboard, game-icons, appid, typography, cache-key, epaperpod

---
## [LRN-20260603-012] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, steam-profile-dashboard

### Summary
Steam profile dashboard lower `最近 / 实时` game rows should use small square Steam app icons.

### Details
After adding a square app icon to the top current-game title, the lower-left `最近 / 实时` list also needed visual game icons. The safer pattern is to build structured recent items with `text` and optional `appid`, then render rows through `_draw_recent_item()` so game rows can reuse `_game_square_icon()` and non-game rows keep the original green bullet fallback. Lower recent rows may still wrap because they include Chinese names and playtime details.

### Suggested Action
For future Steam dashboard list changes, preserve app icons for game rows and keep bullet fallback only for metadata or missing-icon rows. Bump `STEAM_DASHBOARD_STYLE_VERSION` after changing lower-list visuals so live verification is not served stale cache.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Tags: steam-profile-dashboard, recent-games, app-icons, list-rendering, cache-key, epaperpod

---
## [LRN-20260603-011] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, steam-profile-dashboard

### Summary
Steam profile dashboard current-game title should stay single-line and shrink rather than wrap.

### Details
After adding the square Steam game icon before the current game name, `Farthest Frontier` wrapped to a second line in the top profile panel. The preferred behavior is to keep the "正在玩：" label, icon, and game title on one row, using `_draw_single_line_text(..., min_size=10)` to shrink the title when width is tight. The row helper should return at least the icon height so the stats line below does not collide with the icon.

### Suggested Action
For future Steam dashboard title/layout changes, avoid wrapped current-game titles in the top panel. Bump `STEAM_DASHBOARD_STYLE_VERSION` after this kind of visual change so the live Pi does not reuse a stale cached image.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Tags: steam-profile-dashboard, current-game, typography, single-line, cache-key, epaperpod

---
## [LRN-20260603-010] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, steam-profile-dashboard

### Summary
Steam profile dashboard Chinese text can reuse the already deployed Microsoft YaHei fonts from `sports_dashboard`.

### Details
The Steam dashboard previously listed `C:/Windows/Fonts/msyh.ttc` after LXGW/Noto fallbacks, which did not help on the live Pi. The live device already had `msyh.ttc`, `msyhbd.ttc`, and `msyhl.ttc` under `src/plugins/sports_dashboard/fonts/`. Updating `steam_profile_dashboard._font()` to prefer `../sports_dashboard/fonts/msyh*.ttc` changed Chinese rendering to YaHei without copying new font files. Bumping `STEAM_DASHBOARD_STYLE_VERSION` forced a new cache key so the existing cached image did not keep the old font.

### Suggested Action
For future plugin font changes, first inspect existing deployed plugin font folders before adding font assets. When changing a rendered dashboard font, include the font/style version in the cache key or bump the existing style version so live verification renders the new font immediately.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`, `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/fonts/msyh.ttc`
- Tags: steam-profile-dashboard, yahei, fonts, cache-key, epaperpod, coloredepaperframe

---
## [LRN-20260603-009] best_practice

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
When a plugin gains new default settings, existing playlist instances keep their old settings until explicitly updated.

### Details
During the multi-plugin deploy, `ChineseClock` had the new `settings.html` default of `quote_selection=source_random` and Open Library enrichment enabled, but the live `ChineseClock` instance still had the old persisted setting `quote_selection=shortest`. Because `update_plugin_instance` replaces the entire settings payload, the migration needed to read the current non-secret settings, preserve visual values such as font, background, and highlight style, then update only the desired behavior fields.

### Suggested Action
After deploying settings-bearing plugin changes, inspect the live `device.json` for key instances. Do not assume UI defaults affect existing instances. Use the app API to update the existing instance with a complete form payload that preserves prior visual settings and adds the new behavior settings.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/settings.html`, `inkypi-weather/package/InkyPi/src/blueprints/plugin.py`
- Tags: inkypi, playlist, settings, migration, chinese-clock, deploy

---
## [LRN-20260603-004] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
DailyArt should avoid showing a single narrow portrait artwork centered on the landscape e-paper screen.

### Details
On `ColoredEpaperFrame`, portrait museum scans look sparse when rendered one at a time with contain fit. The DailyArt plugin now supports `layoutMode=auto_gallery`, which keeps landscape artworks as a single large image only when no portrait candidates are available, but collects portrait candidates into a 3-item horizontal gallery by default. The cache payload records both `artwork` and `artworks`, and the context cache writes a `museum_artwork_gallery` item so downstream source display can cite all visible artworks.

### Suggested Action
For portrait-heavy visual plugins, prefer a 3-up gallery on the 800x480 panel. Include layout settings in the cache key, increase candidate/attempt limits enough to find multiple portrait images, and store multi-item source metadata rather than only the first image.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/daily_art/daily_art.py`, `inkypi-weather/package/InkyPi/src/plugins/daily_art/settings.html`, `inkypi-weather/package/InkyPi/tests/test_daily_art.py`
- Tags: inkypi, daily-art, museum-api, gallery, portrait, layout, context-cache

---
## [LRN-20260530-130] best_practice

**Logged**: 2026-05-30T02:40:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
GCD Comic Covers must avoid shared-session retries and render metadata fallbacks.

### Details
On EpaperPod, `www.comics.org` API requests can return `429 Too Many Requests`, and `files1.comics.org` cover images can return Cloudflare `403` challenges. The shared InkyPi HTTP session uses urllib3 retries and may sleep on 429, causing GCD refreshes to occupy the background cache worker for a long time. Direct cover images may also be unavailable even with browser-like headers.

### Suggested Action
For `gcd_comic_covers`, use plugin-local `requests.get` calls with short connect/read timeouts and no shared-session retry behavior. Cap cover attempts per refresh and, when metadata is available but source images are blocked, render a metadata cover card instead of repeatedly trying bad image URLs.

### Metadata
- Source: production_debug
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`, `inkypi-weather/package/InkyPi/tests/test_gcd_comic_covers.py`
- Tags: epaperpod, gcd, comics, cloudflare, retries, timeout, fallback

---
## [LRN-20260530-129] best_practice

**Logged**: 2026-05-30T01:49:21-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Diagnose EpaperPod's current playlist screen from successful display updates, not only the next selected plugin.

### Details
The `/playlist` page marks the current `refresh_info` plugin with `Displayed Now`, while `/api/current_image` headers show the last saved current image timestamp. Logs can contain a later `Determined next plugin` line for a slow or stuck plugin before any successful `Updating display`; in that case the visible screen remains the previous plugin. On 2026-05-30, `NatGeoDaily` stayed displayed after `GCD Comic Covers` was selected because the latter was still failing/fetching cover candidates without a completed display update.

### Suggested Action
For future "what is currently displayed / when did it switch" checks, combine `/playlist`, `/download-logs?hours=...`, and `/api/current_image` headers. Treat the latest successful `Updating display` plus matching `Displayed Now` as authoritative; use `Determined next plugin` only as an attempted transition.

### Metadata
- Source: production_debug
- Related Files: `inkypi-weather/package/InkyPi/src/refresh_task.py`, `inkypi-weather/package/InkyPi/src/templates/playlist.html`
- Tags: epaperpod, playlist, rotation, current-screen, logs, diagnostics

---
## [LRN-20260529-128] insight

**Logged**: 2026-05-29T23:49:09-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
`WikiDaily` is the `wpotd` playlist instance, not the Daily Word Wikiquote plugin.

### Details
In the EpaperPod playlist configuration, the instance named `WikiDaily` maps to plugin id `wpotd`, which renders Wikipedia Picture of the Day. The separate `daily_word_poem` plugin is named `DailyWord` in the playlist and now uses Wikiquote for the golden sentence. Future checks for "wiki daily" should inspect `src/plugins/wpotd/` first, then inspect `daily_word_poem` only if the user specifically mentions Daily Word, Wikiquote, or golden sentence.

### Suggested Action
When diagnosing `WikiDaily`, parse the active playlist config and check the `wpotd` refresh state, settings, and generated plugin image path before reviewing `daily_word_poem`.

### Metadata
- Source: conversation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/wpotd/wpotd.py`, `inkypi-weather/package/InkyPi/src/plugins/daily_word_poem/daily_word_poem.py`
- Tags: epaperpod, wikidaily, wpotd, daily-word, wikiquote

---

## [LRN-20260529-126] best_practice

**Logged**: 2026-05-29T19:59:53-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For fixed-height PIL status labels on the Bambu Monitor page, position large text from its actual `textbbox` instead of a fixed y offset.

### Details
The deployed color Bambu page rendered `PRINTING` with the bottom of the large white state label visually clipped by the green status band. The cause was a fixed `top + 42` draw position with the Jost font's bbox extending below the intended rectangle. Computing `draw.textbbox((0, 0), state, font=state_font)` and placing the glyph with a small top padding kept the whole word inside the band.

### Suggested Action
When adding large all-caps labels to 800x480 e-paper panels, calculate glyph placement using `textbbox` and explicit padding. Do not assume a font-size-based y offset fits inside a fixed rectangle, especially with Jost/SemiBold.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py`
- Tags: inkypi, bambu-monitor, pillow, typography, clipping, epaper

---
## [LRN-20260528-105] knowledge_gap

**Logged**: 2026-05-28T16:24:47-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Waveshare 13.3inch e-Paper HAT+ (E) is hardware-compatible with Raspberry Pi 40-pin hosts, but current EpaperPod software is not plug-and-play for it.

### Details
The 13.3inch Spectra 6/E6 HAT+ product uses SPI and a standard Raspberry Pi 40-pin GPIO header, so the hardware can be used with Raspberry Pi-series hosts. However, the local EpaperPod package currently only contains `display/waveshare_epd/epd7in5_V2.py`. Waveshare's 13.3 E manual points to a separate `13.3inch_e-Paper_E` demo package with `epd_13in3E_test.py`; third-party notes indicate the Python import is `epd13in3E` and may require WiringPi plus bundled `.so` libraries. The current InkyPi installer fetches drivers from the standard `RaspberryPi_JetsonNano/python/lib/waveshare_epd/<WS_TYPE>.py` path, which may not cover this separate-program driver.

### Suggested Action
Before buying or migrating to this screen, verify on a spare Pi by running Waveshare's official `epd_13in3E_test.py`. For EpaperPod support, plan a driver integration task: vendor the 13.3 E Python driver and `.so` files, install WiringPi if required, adapt `WaveshareDisplay` if the driver API differs, set `display_type=epd13in3E`, and set resolution/orientation around `1600x1200`.

### Metadata
- Source: compatibility_research
- Related Files: `inkypi-weather/package/InkyPi/src/display/waveshare_display.py`, `inkypi-weather/package/InkyPi/install/install.sh`, `docs/new-board-migration-baseline.md`
- Tags: inkypi, epaper, waveshare, spectra6, epd13in3E, compatibility

---
## [LRN-20260528-104] insight

**Logged**: 2026-05-28T16:17:45-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For the Moon Phase e-paper plugin, the user wants a SpaceX-adjacent aerospace telemetry style.

### Details
While implementing the `moon_phase` plugin, the user clarified that the full page should be very close to SpaceX's design language. The applied interpretation was high-contrast black/white, all-caps technical labels, thin rules, corner registration marks, telemetry-style data rows, and no brand logos or copied marks.

### Suggested Action
For future moon/space-themed e-paper layouts in this project, default to restrained aerospace telemetry styling: black field, white typography, fine grid/rule details, large mission-style numeric metrics, and real astronomical data.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/moon_phase/moon_phase.py`
- Tags: inkypi, epaper, moon-phase, visual-design, spacex-style, telemetry

---
## [LRN-20260528-103] correction

**Logged**: 2026-05-28T16:06:42-07:00
**Priority**: low
**Status**: active
**Area**: epaper

### Summary
When discussing "other existing plugins" for this e-paper setup, interpret it as external ecosystem plugins, not plugins already present in the local package.

### Details
The user clarified that by "其他的，与我们拥有的不同的" they meant plugins or plugin ideas that already exist elsewhere and are different from the locally installed/custom InkyPi plugins.

### Suggested Action
For future plugin recommendation questions, first separate local installed/custom plugins from external InkyPi/PaperPi/InkyCal ecosystem options, and explicitly label whether a candidate is directly installable, likely portable, or only useful as inspiration.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/`, `docs/new-board-migration-baseline.md`
- Tags: inkypi, epaper, recommendations, external-plugins

---
## [LRN-20260528-102] best_practice

**Logged**: 2026-05-28T15:17:47-07:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
For content plugins, make quote/text provenance explicit when adding curated local lists.

### Details
After replacing the Daily Word plugin's poem panel with golden sentences, the user asked where the sentences came from. The implementation used a hardcoded `LOCAL_QUOTES` list rather than a network API or verified source dataset, so the UI/data should not imply externally sourced or fully source-verified quotes.

### Suggested Action
When adding curated text such as quotes, expose whether it is local curated content, user-provided content, or an external API result. For stricter attribution, add source fields per item and render/cache them instead of only author/topic.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/daily_word_poem/daily_word_poem.py`
- Tags: inkypi, content, quotes, provenance, attribution

---
## [LRN-20260528-101] best_practice

**Logged**: 2026-05-28T15:12:49-07:00
**Priority**: medium
**Status**: pending
**Area**: tests

### Summary
Avoid pytest `tmp_path` for narrow InkyPi plugin checks in this sandbox; Python-created `mode=0o700` temp dirs can become unreadable.

### Details
While validating the Daily Word & Quote change, pytest collected the test file successfully but failed before test code ran because temporary directories created with Python/pytest permissions became inaccessible on Windows (`PermissionError: [WinError 5]`). Even `--basetemp` under `.tmp` and `C:\tmp` failed. Directly invoking the test functions with normal PowerShell-created directories worked, and the syntax check passed with `PYTHONDONTWRITEBYTECODE=1`.

### Suggested Action
For small InkyPi plugin validations, load dependencies with `PYTHONPATH=.../InkyPi/.pc-packages`, use a Python 3.12 project venv, set `PYTHONDONTWRITEBYTECODE=1`, and either run focused pytest outside the sandbox or call narrow test functions with fresh per-run temp directories. Reusing cache test directories can cause false `from_cache` assertions. Clean up any pytest probe directories created during diagnosis.

### Metadata
- Source: error
- Related Files: `inkypi-weather/package/InkyPi/tests/test_daily_word_poem.py`, `inkypi-weather/package/InkyPi/.pc-packages/`
- Tags: inkypi, pytest, windows, sandbox, tmp_path, permissions
- See Also: LRN-20260527-054

---

## [LRN-20260527-052] best_practice

**Logged**: 2026-05-27T18:57:22-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Avoid pytest `tmp_path` for small InkyPi plugin cache tests on this Windows setup.

### Details
While adding the `live_radar` plugin, logic tests passed but render/cache tests using pytest's `tmp_path` failed because `C:\Users\super\AppData\Local\Temp\pytest-of-LocalTest` could not be scanned. A project-local `--basetemp` also failed during pytest cleanup. Replacing filesystem temp usage with monkeypatched in-memory `_read_cache` / `_write_cache` fakes made the tests deterministic and avoided unrelated ACL problems.

### Suggested Action
For future narrow plugin unit tests, fake plugin cache reads/writes in memory when the cache persistence itself is not under test. Keep real filesystem checks for focused integration tests only, and use source `compile(...)` rather than `py_compile` when `__pycache__` permissions are dirty.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/tests/test_live_radar.py
- Tags: inkypi, epaper, pytest, tmp_path, windows-acl, cache-tests

---

## [LRN-20260527-051] best_practice

**Logged**: 2026-05-27T18:47:41-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Build a future LiveRadar e-paper plugin around the batch status API, not full-page browser screenshots.

### Details
The public LiveRadar app exposes `/api/status` and `/api/status/batch` through Cloudflare Pages Functions, and a live elevated network smoke confirmed `/api/status/batch` returns normalized streamer status for multiple rooms. For the current 7.5-inch e-paper device, the practical plugin shape is to poll that normalized API, cache results, and render a static Pillow/Jinja dashboard. Recreating the whole browser app would waste the Pi Zero W budget on DOM, animation, audio, localStorage, and interactive controls that the no-button e-paper setup cannot use.

### Suggested Action
For a future `live_radar` plugin, default to a settings-managed room list plus optional LiveRadar export/import JSON, call `https://liveradar.pages.dev/api/status/batch` for batches of up to 10 rooms, and render live/replay/offline sections into an 800x480 static layout. Keep a standalone Python port of the platform fetchers as a fallback only if relying on the public Pages API becomes undesirable.

### Metadata
- Source: implementation
- Related Files: G:\PersonalProjects\LiveRader\LR_online\functions\_shared\platform-status.js, inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py
- Tags: inkypi, epaper, liveradar, streamer-status, cloudflare-pages, batch-api

---

## [LRN-20260527-050] correction

**Logged**: 2026-05-27T17:21:24-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Person-image smart crop should prioritize the face over generic visual saliency.

### Details
The user corrected the Wikipedia Picture of the Day smart-crop behavior: generic edge/detail scoring can lock onto clothes, props, text, or background detail instead of the intended person. For character or portrait-style images, the crop target should first try to locate a face-like region and use that as the crop anchor, then fall back to generic saliency only when no face candidate is found.

### Suggested Action
For future e-paper image fit/crop changes, keep the face-first branch in `AdaptiveImageLoader` when `focus_crop=True`. Validate with off-center face examples where high-detail non-face regions would otherwise win.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/utils/image_loader.py, inkypi-weather/package/InkyPi/tests/test_wpotd_smart_crop.py
- Tags: inkypi, epaper, wpotd, smart-crop, face-priority

---

## [LRN-20260527-049] best_practice

**Logged**: 2026-05-27T15:31:09-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
After Pi plugin hot-deploys, verify through the local Web API and allow several minutes for Waveshare display init.

### Details
During the Daily Comic deploy, direct `scp` of `comic.py` and `settings.html` followed by `sudo -n systemctl restart inkypi` worked, but `python3 -m py_compile` as the normal user failed on the Pi because `InkyPi/src/plugins/comic/__pycache__` was not writable. The service also remained active but did not serve HTTP until Waveshare `epd7in5_V2` initialization completed about 3 minutes after restart. Once Waitress was serving, calling `POST /display_plugin_instance` from the Windows host with PowerShell JSON was more reliable than constructing JSON inside an SSH command; it generated the DailyComic image and wrote the plugin's `comic_rotation_*` state into `device.json`.

### Suggested Action
For future plugin installs, upload changed files with `scp`, restart InkyPi, then poll `http://127.0.0.1/playlist` on the Pi until HTTP 200 before triggering a plugin. Avoid normal-user `py_compile` in plugin directories with stale/root-owned `__pycache__`; use local tests plus service/log verification instead. Prefer local `Invoke-RestMethod` or `curl.exe` for JSON API calls over SSH-embedded JSON.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/comic/comic.py, inkypi-weather/package/InkyPi/src/plugins/comic/settings.html
- Tags: inkypi, epaperpod, deployment, scp, pycache, waveshare, display-plugin-instance

---

## [LRN-20260527-048] best_practice

**Logged**: 2026-05-27T15:21:29-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Preload system Pillow before adding `.pc-packages` when running InkyPi pytest on Python 3.14.

### Details
The project-local `.pc-packages` directory contains `pytest`, but its bundled `PIL` package can fail on the current system Python 3.14 with `ImportError: cannot import name '_imaging' from 'PIL'`. A reliable test command is to import `PIL.Image` first from the system environment, then prepend `.pc-packages` to `sys.path` and invoke `pytest.main(...)`. Use `PYTHONDONTWRITEBYTECODE=1` and disable pytest's cache provider when permission errors appear around `__pycache__` or `.pytest_cache`.

### Suggested Action
For future InkyPi test runs on this workstation, prefer:
`$env:PYTHONDONTWRITEBYTECODE='1'; python -c "import pathlib, sys; import PIL.Image; sys.path.insert(0, str(pathlib.Path('.pc-packages').resolve())); import pytest; raise SystemExit(pytest.main(['-p','no:cacheprovider','tests']))"`

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/tests/test_comic_rotation.py
- Tags: inkypi, epaper, pytest, pillow, pc-packages, windows, validation

---

## [LRN-20260526-047] best_practice

**Logged**: 2026-05-26T20:20:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
BacktotheDate should use a multi-poster wall for portrait images on the horizontal 7.5-inch device.

### Details
The user found a single portrait poster centered on the horizontal e-paper screen visually uncomfortable. The preferred behavior is: landscape posters display as one full-screen image, while portrait posters fetch multiple portrait images and render them as a horizontal poster wall. The active instance uses `fitMode=mosaic` and `posterColumns=3`.

### Suggested Action
For future BacktotheDate layout changes, preserve the auto orientation behavior: one full-screen landscape image, three portrait posters across the horizontal canvas by default, with 2/3/4 columns available as settings. Verify on the actual `/api/current_image` after a real display refresh.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/backtothedate/backtothedate.py, inkypi-weather/package/InkyPi/src/plugins/backtothedate/settings.html
- Tags: inkypi, epaper, backtothedate, layout, portrait, mosaic

---

## [LRN-20260526-046] best_practice

**Logged**: 2026-05-26T20:05:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Use `scp -r` or Linux-created zips for Pi plugin deployment; PowerShell `Compress-Archive` can preserve Windows backslashes as literal remote filenames.

### Details
During the `backtothedate` deployment, a Windows-created zip extracted on the Pi into files named like `InkyPi\src\plugins\backtothedate\plugin-info.json` at the package root instead of real nested directories. InkyPi then loaded the playlist instance but could not find the plugin config. Direct `scp -r` of the plugin directory and direct `scp` of `refresh_task.py` to the real `InkyPi/src/...` paths fixed the install.

### Suggested Action
For future Pi plugin hot-deploys from Windows, prefer direct `scp -r` to the target directory, or create/extract deployment archives on Linux with forward-slash paths. After deploy, verify both the real path and plugin registry behavior before triggering display.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/backtothedate/backtothedate.py, inkypi-weather/package/InkyPi/src/refresh_task.py
- Tags: inkypi, epaper, deployment, powershell, zip, scp

---

## [LRN-20260526-045] best_practice

**Logged**: 2026-05-26T19:50:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Run external InkyPi plugin smoke tests outside the sandbox when shell HTTPS is routed to the dead local proxy.

### Details
While adding `backtothedate`, offline compile/import and local image-render smoke tests passed, but the first live `requests` smoke against `chineseposters.net` failed because sandboxed shell networking used proxy `127.0.0.1:9` and returned `ProxyError` / connection refused. Re-running the same focused live smoke with approved elevated network access succeeded and generated an 800x480 preview.

### Suggested Action
For future internet-backed InkyPi plugins, run offline parser/render tests first. If the live shell smoke fails with `127.0.0.1:9` proxy errors, request escalation for the exact network smoke instead of changing plugin code or treating the target site as unreachable.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/backtothedate/backtothedate.py
- Tags: inkypi, epaper, network-smoke, sandbox, proxy, validation

---

## [LRN-20260526-044] best_practice

**Logged**: 2026-05-26T18:15:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Bambu Monitor should use a branded waiting image after repeated camera frame failures.

### Details
The user wanted the Bambu Monitor Live View panel to avoid a blank or plain error state when the A1 camera cannot return an image for a while. The production behavior is now: show the fresh camera frame on success, keep a recent good frame for short transient failures, and fall back to bundled `camera_waiting.png` after repeated camera failures or when no good frame exists.

### Suggested Action
For future Bambu Monitor camera changes, preserve the fallback order: fresh frame, short-lived stale frame, then waiting image. Keep `camera_waiting.png` packaged with the plugin zip and deployed alongside `bambu_monitor.py`.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py, inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/camera_waiting.png, docs/new-board-migration-baseline.md
- Tags: inkypi, bambu-monitor, camera, fallback, waiting-image, epaper

---

## [LRN-20260526-043] correction

**Logged**: 2026-05-26T18:03:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Keep Bambu Monitor's production UI in English by default.

### Details
After testing a full Simplified Chinese localization of `bambu_monitor`, the user rejected the Chinese presentation and asked to restore English. The Chinese pass also showed that this Pi's active package did not have a readable CJK font in `src/static/fonts`, so plugin-local fonts were needed for Chinese rendering, but that extra font should not remain in the default Bambu Monitor package after reverting to English.

### Suggested Action
For future Bambu Monitor changes, keep screen labels, plugin display name, and settings copy in English unless the user explicitly asks to re-localize it again. Do not bundle `LXGWWenKai-Regular.ttf` with this plugin for the default English build.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py, inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/settings.html, docs/new-board-migration-baseline.md
- Tags: inkypi, bambu-monitor, localization, english-default, epaper

---

## [LRN-20260526-042] best_practice

**Logged**: 2026-05-26T17:34:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Bambu A1 camera frames require an explicit 80-byte TLS auth packet before reading the JPEG stream.

### Details
The Bambu A1 at `192.168.1.137` accepted local camera connections on port `6000`, but the first implementation used native `struct.pack("IIL", 0x40, 0x3000, 0)` and produced a 76-byte auth packet on the Pi. That authenticated badly enough that the socket stayed open but returned no frame data. A probe confirmed that explicit little-endian 80-byte auth (`<IIII`, followed by 32-byte username `bblp` and 32-byte access code) returned a JPEG frame in about 2 seconds. The stream begins with a 16-byte little-endian frame header before the JPEG payload, though scanning for JPEG markers is still a useful fallback.

### Suggested Action
For future Bambu camera work, avoid native struct packing and keep the camera code read-only: TLS to `host:6000`, send the explicit 80-byte auth packet, read the 16-byte frame header plus JPEG payload, cache the last good frame, and render it inside a bounded black/white e-paper panel. Keep `BAMBU_ACCESS_CODE` in `.env` and do not log it.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py, docs/new-board-migration-baseline.md
- Tags: inkypi, bambu, a1, camera, tls, epaper, access-code

---

## [LRN-20260526-041] best_practice

**Logged**: 2026-05-26T15:56:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Prioritize the selected InkyPi display plugin before background cache refreshes.

### Details
Refreshing every due plugin before display can make slow internet APIs delay the visible 5-minute rotation. The optimized flow is: select the next playlist item, synchronously refresh it if due or missing so the displayed result is fresh, update the display, then refresh other due plugin caches in one non-overlapping background pass. If a previous cache pass is still running, skip the next background pass instead of stacking API requests.

### Suggested Action
For future refresh-flow work, preserve the display-first/background-cache-second order in `refresh_task.py`. Validate with a 20-second smoke and confirm logs show `Determined next plugin` and `Updating display` before background `Refreshing due plugin instance cache`, plus `Due plugin cache refresh already running` when a slow pass overlaps the next tick.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/tests/test_refresh_task.py, docs/new-board-migration-baseline.md
- Tags: inkypi, scheduler, display-priority, background-refresh, api-rate-limit

---

## [LRN-20260528-002] correction

**Logged**: 2026-05-28T00:28:31-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Steam dashboard avatar decoration should be integrated into the avatar frame, not scattered around the profile area.

### Details
The user rejected a first pass that placed many controller-button decorations around the avatar because it made the page look messy. The intended direction is to make the avatar border itself more interesting, with compact details such as segmented rings, ticks, and small gamepad nodes attached to the circular frame.

### Suggested Action
For future Steam dashboard visual polish, keep decorative controls inside or directly attached to the avatar frame. Avoid filling the left-side whitespace with free-floating symbols unless the user explicitly asks for that layout.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py
- Tags: inkypi, epaper, steam-dashboard, avatar-frame, visual-design

---

## [LRN-20260528-001] best_practice

**Logged**: 2026-05-28T00:01:39-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
For user-managed DailyImage batches, store source images in a user-owned folder and point `image_upload` at those paths.

### Details
Batch uploading several large JPEGs through the InkyPi Web API can make the Pi slow or drop the HTTP connection because the request path performs image processing. The `static/images/saved` tree may also have permission issues that make direct SCP and path validation unreliable. A safer pattern is to copy images to a user-owned folder such as `/home/feeengyuuu/epaper_images/DailyImage/`, then update `imageFiles[]` to those absolute paths through the lightweight plugin settings API.

### Suggested Action
When adding personal image batches to `DailyImage`, copy files via SCP to `/home/feeengyuuu/epaper_images/DailyImage/`, set `displayMode=no_repeat_random`, and update the complete `imageFiles[]` list with absolute paths. Verify with the InkyPi venv that every image opens before reporting success.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/image_upload/image_upload.py
- Tags: inkypi, epaper, image-upload, dailyimage, permissions, upload

---

## [LRN-20260526-038] best_practice

**Logged**: 2026-05-26T15:29:24-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Batch-uploaded local image rotations should use a persisted no-repeat random bag.

### Details
The user wanted a plugin where they upload many images and each refresh picks a random image without repeats until all uploaded images have appeared. The existing `image_upload` plugin is the right base because it already supports multi-file upload and e-paper fit/pad options, but ordinary `random.choice` is not acceptable for this workflow.

### Suggested Action
Use `image_upload` with `displayMode=no_repeat_random` for personal photo/art batches. Persist `image_no_repeat_queue`, `image_no_repeat_pool`, and `image_no_repeat_last` in plugin settings, and keep the plugin instance refresh interval aligned with how often a new uploaded image should appear.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/image_upload/image_upload.py, inkypi-weather/package/InkyPi/src/plugins/image_upload/settings.html
- Tags: inkypi, epaper, image-upload, no-repeat, random-bag

---

## [LRN-20260526-037] best_practice

**Logged**: 2026-05-26T15:18:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
InkyPi playlist-level plugin rotation should use a persisted shuffled no-repeat queue.

### Details
The user wanted the large plugin rotation mechanism to match the no-repeat feel used for Steam Daily Art. Avoid only immediate repeats is not enough, because the same plugin can still recur before the rest of the playlist has appeared. The baseline behavior should shuffle the active plugin list, show each plugin once, then start a new shuffled round while avoiding the just-displayed plugin as the first item of the new round.

### Suggested Action
Keep playlist-level rotation state in `device.json` using `plugin_rotation_queue` and `plugin_rotation_pool`. Preserve each plugin instance's own content refresh cadence separately; do not flatten data freshness intervals just to make display rotation consistent.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/model.py, docs/new-board-migration-baseline.md
- Tags: inkypi, epaper, playlist, rotation, no-repeat

---

## [LRN-20260526-036] best_practice

**Logged**: 2026-05-26T15:07:22-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Steam Daily Art should refresh the live Steam frontpage list on schedule while preserving no-repeat history.

### Details
The user corrected the Steam Daily Art behavior: selection should follow Steam's current ranked/frontpage order, not invent a separate random priority, but it must skip previously displayed items until the current pool is exhausted. Saved plugin instances need their active `rotationCadence` updated too, because changing defaults does not affect already configured InkyPi instances.

### Suggested Action
Keep `sourceCategory=fresh_frontpage`, `selectionMode=daily_rotation`, and `rotationCadence=hourly` as the active baseline for Steam Daily Art. Do not include the exact returned app list in the selection state key, otherwise normal Steam frontpage updates will reset the no-repeat history too aggressively.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_daily_art/steam_daily_art.py, inkypi-weather/package/InkyPi/src/plugins/steam_daily_art/settings.html
- Tags: inkypi, epaper, steam-daily-art, no-repeat, live-refresh

---

## [LRN-20260526-039] best_practice

**Logged**: 2026-05-26T16:12:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Bambu Monitor can join the production playlist after a live cloud-connected MQTT read succeeds.

### Details
The Bambu printer at `192.168.1.137` accepted a read-only local MQTT/TLS status read while preserving the user's current cloud-connected workflow. The live check returned real printer status (`PREPARE`, heated bed, and four AMS trays) using `BAMBU_ACCESS_CODE` from `.env`; no LAN Only switch was required. The production playlist instance should be named `Bambu`, use `accessCodeEnv=BAMBU_ACCESS_CODE`, keep `accessCode` blank in `device.json`, and refresh every 5 minutes.

### Suggested Action
For future board migration, include `bambu_monitor` in `DailyDoseOfDay` only with verified host, serial, and env-key configuration. Keep it read-only, cache for 60 seconds, and avoid storing or logging the Access Code.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py, docs/new-board-migration-baseline.md
- Tags: inkypi, bambu, playlist, cloud-connected, mqtt, access-code

---

## [LRN-20260526-038] constraint

**Logged**: 2026-05-26T15:55:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Prefer Bambu monitoring paths that preserve Bambu Cloud and mobile-app access.

### Details
The user asked whether Bambu printer data can be read like Bambu Studio while keeping the printer online. For this project, do not assume LAN Only mode is acceptable just because it enables local MQTT. First try a cloud-preserving read-only local test against the existing printer IP and MQTT/TLS port, and only recommend LAN Only / Developer Mode if the user explicitly accepts losing Bambu Cloud / Bambu Handy behavior.

### Suggested Action
For Bambu Monitor setup, keep the printer in its current cloud-connected mode first. With serial number and access code, run a read-only MQTT status test against `192.168.1.137:8883`. If it succeeds, use that mode for InkyPi. If it fails, present the trade-off before enabling LAN Only.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py
- Tags: inkypi, bambu, cloud-mode, lan-only, user-preference, printer-monitor

---

## [LRN-20260526-037] insight

**Logged**: 2026-05-26T15:45:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
The local Bambu printer currently appears as `192.168.1.137 / unknownb43a45aa835c`.

### Details
In the router client list, `192.168.1.137 / unknownb43a45aa835c` was initially ambiguous, while `192.168.1.61 / esp32s3-CEDB8C` also looked plausible. Local TCP checks showed `192.168.1.137` accepts connections on Bambu-relevant ports `8883` and `990`, making it the likely Bambu Lab printer endpoint. `192.168.1.65 / EPSON42C451` is the Epson printer, not the 3D printer.

### Suggested Action
Use `192.168.1.137` as the Bambu Monitor host unless the router lease changes. Before troubleshooting credentials, re-check that ports `8883` and `990` are still reachable from the same network.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py
- Tags: inkypi, bambu, mqtt, local-network, printer-ip

---

## [LRN-20260526-036] best_practice

**Logged**: 2026-05-26T15:35:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Keep Bambu printer monitoring read-only, cached, and staged until LAN credentials are verified.

### Details
The Bambu Monitor plugin uses local MQTT/TLS on port 8883 with username `bblp`, the printer serial in the `device/{serial}/report` topic, and the LAN access code as the password. For this InkyPi setup, it should only subscribe to reports and optionally send Bambu's full-status refresh request; it must not expose pause, stop, heat, or G-code control paths. The e-paper view should be pure black/white PIL rendering with a short cache, because printer data can update faster than the 7.5-inch panel should refresh.

### Suggested Action
Store the LAN access code in `.env` as `BAMBU_ACCESS_CODE`, configure host and serial through the plugin settings, smoke-test demo/setup render first, then test live MQTT. Do not add Bambu Monitor to the production random playlist until a real live read succeeds and the physical screen is visually checked.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py, inkypi-weather/dist/bambu-monitor-20260526.zip
- Tags: inkypi, bambu, mqtt, zero-2-wh, epaper, printer-monitor

---

## [LRN-20260526-040] best_practice

**Logged**: 2026-05-26T15:36:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Back up both local and active Pi `.env` files because their secret sets can differ.

### Details
The local EpaperSystem package and dist `.env` files were not identical, and the active Raspberry Pi package `.env` was larger than the local copies. For this project, a reliable API-key backup should include `inkypi-weather/package/InkyPi/.env`, dist package `.env` copies, and the active Pi path `/home/feeengyuuu/inkypi-weather-pi-package-20260524-3/InkyPi/.env`.

### Suggested Action
When the user asks for an API-key backup, create a local ignored `.secrets-backup/` bundle, include a manifest with hashes only, do not print secret values in chat, and make sure `.secrets-backup/` plus nested `.env` files are ignored.

### Metadata
- Source: implementation
- Related Files: .gitignore
- Tags: inkypi, env, api-keys, backup, secrets

---

## [LRN-20260526-039] best_practice

**Logged**: 2026-05-26T15:30:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
InkyPi playlist display rotation and plugin content refresh must be separate scheduling concerns.

### Details
The previous playlist scheduler refreshed a plugin only when that plugin was randomly selected for display. That made per-plugin timers lazy: a 30-minute weather refresh or 15:00 newspaper refresh could be delayed until random selection picked that instance. The updated scheduler refreshes all due active-playlist plugin image caches before choosing the random display item, so internet-backed content follows its own cadence while the displayed plugin order remains random.

### Suggested Action
For future scheduler changes, preserve the two-step tick: first refresh due/missing plugin instance caches, then choose the display item. Validate with a temporary 20-second global cycle and look for `Refreshing due plugin instance cache` before `Determined next plugin`; restore `plugin_cycle_interval_seconds=300` immediately after the smoke.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/tests/test_refresh_task.py, docs/new-board-migration-baseline.md
- Tags: inkypi, scheduler, playlist, content-freshness, internet-refresh

---

## [LRN-20260526-034] best_practice

**Logged**: 2026-05-26T03:25:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Use the live Pi `device.json` as the source of truth when freezing the beta playlist baseline.

### Details
The playlist screenshot is useful for human confirmation, but it does not expose plugin IDs, refresh JSON, plugin settings, or whether an instance has `latest_refresh_time`. The latest beta capture showed 16 active entries in `DailyDoseOfDay`; the decisive data came from the remote `device.json`, including configured `Money` / `stocktracker`, `SteamDailyArt`, and `Anal` settings.

### Suggested Action
When the user says the current screen is the latest beta state, SSH to the Pi and read `InkyPi/src/config/device.json` before updating migration docs. Preserve instance names, plugin IDs, refresh rules, and important plugin settings in `docs/new-board-migration-baseline.md`.

### Metadata
- Source: user_feedback
- Related Files: docs/new-board-migration-baseline.md
- Tags: inkypi, beta-baseline, device-json, playlist, migration

---

## [LRN-20260526-035] best_practice

**Logged**: 2026-05-26T03:30:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Choose future Zero 2 WH InkyPi plugins by e-paper rendering cost, not novelty.

### Details
For the 7.5-inch 800x480 black/white Waveshare setup on a Raspberry Pi Zero 2 WH, the best long-running plugins are cached, mostly static, and rendered with native Pillow using pure black/white contrast. Calendar, weather, progress, literary clock, local status, Steam summary, and simple finance dashboards fit this model. Browser screenshot, Chromium, heavy Matplotlib, continuous gray gradients, and frequently refreshed API-heavy plugins should be treated as experimental unless they are cached and converted to a native e-paper layout.

### Suggested Action
Before installing new third-party plugins, classify them as: safe rotation, staged/manual, or experimental. Prefer pure PIL or simple image plugins for production playlists; keep web-rendered or API-heavy plugins out of random rotation until smoke-tested on the Pi and visually checked on the physical panel.

### Metadata
- Source: implementation_research
- Related Files: inkypi-weather/package/InkyPi/src/plugins, docs/new-board-migration-baseline.md
- Tags: inkypi, zero-2-wh, plugins, epaper, selection-criteria, performance

---

## [LRN-20260526-032] best_practice

**Logged**: 2026-05-26T03:12:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Keep unconfigured StockTracker placeholders out of the active InkyPi random playlist.

### Details
The beta playlist briefly selected the old `Stock` / `stocktracker` placeholder during random rotation, but its playlist settings had empty `tickers`, `shares`, and `period`, causing `RuntimeError: Please provide both tickers and shares`. The latest beta baseline reintroduces StockTracker as the configured `Money` instance with non-empty holdings settings. The rule is to avoid unconfigured placeholders, not to ban StockTracker entirely.

### Suggested Action
Before adding or renaming a `stocktracker` instance in production rotation, set non-empty tickers, shares, and period values, run a direct render smoke, then add it through the playlist UI/API. Do not include placeholder finance plugins in a random cycle because they can be selected automatically.

### Metadata
- Source: implementation
- Related Files: docs/new-board-migration-baseline.md, inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py
- Tags: inkypi, playlist, stocktracker, random-rotation, migration-baseline

---

## [LRN-20260526-031] best_practice

**Logged**: 2026-05-26T03:05:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Treat the current EpaperSystem beta playlist/device state as the production-board migration baseline.

### Details
The user confirmed the current beta state is the desired end state for future new-mainboard installation. The migration target is documented in `docs/new-board-migration-baseline.md`: InkyPi on Waveshare `epd7in5_V2`, 800x480 horizontal, `DailyDoseOfDay`, 5-minute random rotation, and per-plugin refresh cadence. New production boards should be brought to that baseline directly instead of rediscovering plugin setup from scratch.

### Suggested Action
When a new board arrives, start from `docs/new-board-migration-baseline.md`, copy the current package, recreate private `.env` keys, restore `device.json` playlist settings, run HTTP readiness checks, use a temporary 20-second scheduler smoke, then restore `plugin_cycle_interval_seconds=300`.

### Metadata
- Source: user_feedback
- Related Files: docs/new-board-migration-baseline.md, inkypi-weather/package/InkyPi/src/config/device_dev.json
- Tags: inkypi, migration, new-board, beta-baseline, production-handoff

---

## [LRN-20260526-033] correction

**Logged**: 2026-05-26T03:05:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
StockTracker must render as pure black and white on the 7.5-inch e-paper display.

### Details
The first StockTracker screen used Matplotlib dark gray panels, grid lines, and translucent fills. On the black/white e-paper panel those gray values became a full-screen dotted dither pattern, and the original Matplotlib font sizes overlapped at 800x480. The corrected renderer uses a PIL layout with pure black background, pure white borders/text/lines, no grid or fill areas, fitted fonts, a table-style holdings section, and final 1-bit thresholding before returning RGB. On the Pi, `src/static/fonts` was not readable by the deploy user, so the plugin should prefer absolute system font paths such as DejaVu/Liberation with a safe fallback.

### Suggested Action
For future financial/dashboard-style e-paper plugins, do not use gray dashboards or anti-aliased chart fills. Build a native 800x480 black/white layout and threshold the final image to prevent background dot noise.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py
- Tags: inkypi, stocktracker, e-paper, dithering, typography, black-white-render

---

## [LRN-20260526-030] best_practice

**Logged**: 2026-05-26T02:48:49-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Validate InkyPi scheduler changes with a temporary short cycle before restoring the real e-paper cadence.

### Details
For playlist/refresh changes, waiting for the production 5-minute cycle slows feedback and can obscure whether the scheduler is working. Add temporary seconds-level support or otherwise shorten the global cycle, verify one or two automatic playlist selections in `journalctl`, then restore the production interval. For this device, the useful proof was `plugin_cycle_interval_seconds=20` producing automatic `NASAPics -> SteamDaily` playlist updates before restoring `300`.

### Suggested Action
When testing InkyPi rotation behavior, use a short-cycle smoke first and capture `Determined next plugin` plus `Updating display` logs. Restore `plugin_cycle_interval_seconds=300` immediately after the smoke passes.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/model.py
- Tags: inkypi, playlist, scheduler, smoke-test, e-paper-cadence

---

## [LRN-20260526-028] correction

**Logged**: 2026-05-26T02:07:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
E-paper pet screen UI should use a panelized information hierarchy, not a debug-style text layout.

### Details
The first `epaper_pet` screen layout was too rough: large expression text, loosely aligned status bars, and weak text hierarchy made the page feel like a debug screen. The accepted direction is a 4-part 800x480 layout: header identity/status, face panel, vitals panel, and log panel. Use fixed readable e-paper typography, strong borders, aligned values, and no overlapping auxiliary decorations.

### Suggested Action
Preserve the v3 renderer structure in `epaper_pet.py`: header box, FACE panel, VITALS panel with five clean bars, and bottom LOG panel. Avoid adding decorative signal strips or small text that competes with health/status rows.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py, inkypi-weather/dist/epaper-pet-ui-v3-20260526.zip
- Tags: inkypi, epaper-pet, ui-layout, typography, no-overlap

---

## [LRN-20260526-029] correction

**Logged**: 2026-05-26T02:34:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Chinese Literature Clock must render with per-character font fallback, not a single CJK font.

### Details
The user reported boxed missing-glyph symbols in the Simplified Chinese literature clock. The default Fangzheng-Xinkai-near font is backed by `FandolKai-Regular.otf`, which does not cover every public-domain quote character in the dataset. A scan found 83 dataset characters missing from FandolKai, while the bundled `LXGWWenKai-Regular.ttf` and `I.Ming-8.10.ttf` covered them. Drawing whole lines with one font lets missing glyph boxes appear.

### Suggested Action
Preserve the font cascade in `chinese_literature_clock.py`: keep FandolKai as the primary visual style, but measure and draw text per character through `_load_font_cascade(...)`, `_font_for_char(...)`, and `_draw_text(...)`. When validating future data/font changes, scan the dataset for `still_missing 0` before deploying.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/chinese_literature_clock.py
- Tags: inkypi, chinese-literature-clock, fonts, missing-glyph, fallback

---

## [LRN-20260526-027] best_practice

**Logged**: 2026-05-26T02:50:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Heavy InkyPi plugins should lazy-load market/rendering dependencies and vendor only missing Pi packages.

### Details
`MEANGAIN/InkyPi-StockTracker` required `yfinance`, `matplotlib`, `pandas`, and related dependencies. Installing with `pip --target ... yfinance matplotlib` tried to copy or build large dependencies such as `numpy` and initially filled the small `/tmp`; direct venv installation failed because sudo requires a password. The working path was to install only missing wheels into the plugin-local `_vendor` directory with `--no-deps`, set `TMPDIR=$HOME/tmp-pip`, reuse the InkyPi venv's existing `numpy/Pillow/requests`, and lazy-load `yfinance/matplotlib/numpy` only during `generate_image` so InkyPi startup and settings pages remain responsive. Because another plugin had already loaded a `google` package, `stocktracker` also needed to append its `_vendor/google` path to `google.__path__` before importing yfinance.

### Suggested Action
For future third-party InkyPi plugins with large dependency trees, avoid importing heavy libraries at module import time. Validate three paths separately: registry registration, settings page, and actual image generation. On Pi, use plugin-local `_vendor` installs with explicit missing packages instead of sudo-writing the system venv.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py, inkypi-weather/dist/stocktracker-20260526.tar.gz
- Tags: inkypi, stocktracker, yfinance, matplotlib, vendor-dependencies, lazy-import

---

## [LRN-20260526-026] best_practice

**Logged**: 2026-05-26T01:45:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For InkyPi remote UI smoke checks over PowerShell SSH, grep plugin ids instead of display names.

### Details
After installing `saulob/InkyPi-Simple-Calendar`, the remote smoke check `grep -F "Simple Calendar"` was split by the shell invocation and produced a false timeout even though the service later came up normally. Checking the stable plugin id `simple_calendar` avoided quoting problems and confirmed the home page contained `/plugin/simple_calendar`.

### Suggested Action
When validating InkyPi plugin installs remotely, prefer `curl -fsS http://127.0.0.1/ | grep -F <plugin_id>` and only inspect human display names manually if needed.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py
- Tags: inkypi, ssh, smoke-test, simple-calendar, quoting

---

## [LRN-20260526-025] insight

**Logged**: 2026-05-26T01:35:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Avoid PowerShell `Compress-Archive` for Pi-deployed InkyPi plugin zips when Linux `unzip` exit status is part of the deploy chain.

### Details
Deploying `flow-progress-20260526.zip` worked, but the zip made by PowerShell used backslashes as path separators. Linux `unzip` extracted the files correctly while emitting a warning and returning a non-zero status, which interrupted the chained remote deploy command before service restart. SSH command strings with regex pipes also need conservative quoting because `|` inside a pattern can be eaten as a shell pipeline.

### Suggested Action
For future InkyPi plugin update zips, either create archives with Unix-style paths or make the remote deploy script tolerate `unzip` warning exit code only after verifying the expected plugin files exist. Prefer simple remote checks such as `grep -F 'Plugin Name'` over regex alternation in ad hoc SSH commands.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/dist/flow-progress-20260526.zip, tools/epaperpod-deploy-zip.ps1
- Tags: inkypi, deploy, zip, powershell, ssh-quoting

---

## [LRN-20260526-027] correction

**Logged**: 2026-05-26T01:55:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Mini Weather should keep a clean white layout while preserving weather icon detail.

### Details
The user reported that the Mini Weather background looked covered in dots, but the later pure black-and-white fix overcorrected and made the weather icons too boring. The accepted direction is a pure white page with no gray card fills, strong black text/rules, restored contrast-boosted grayscale icon details, and red/blue high-low temperatures for information hierarchy. Avoid whole-image thresholding unless the user explicitly asks for a strictly 1-bit look.

### Suggested Action
Preserve the PIL fallback's direct `return img`, the grayscale `ImageOps.autocontrast(...)` icon path, and the current temperature group layout that prevents the `degC` label from overlapping digits. Keep backgrounds flat white and do not reintroduce broad gray fills.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/mini_weather/mini_weather.py, inkypi-weather/dist/mini-weather-clean-icon-detail-20260526.zip
- Tags: inkypi, mini-weather, epaper, clean-background, icon-detail, pil-fallback

---

## [LRN-20260526-024] best_practice

**Logged**: 2026-05-26T01:20:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Install small third-party InkyPi plugins by copying the plugin folder into the project-local InkyPi package and validating direct rendering before deployment.

### Details
`saulob/InkyPi-Flow-Progress` installed cleanly as `src/plugins/flow_progress` with the expected `flow_progress.py`, `plugin-info.json`, `settings.html`, and `icon.png` files. The local root is not a reliable git clone target, so download or stage third-party plugin source under `.tmp`, copy only the plugin folder into `inkypi-weather/package/InkyPi/src/plugins/`, and add the plugin id to `src/config/device_dev.json` only for stable local UI ordering.

### Suggested Action
For Pi deployment, package plugin-only updates with an `InkyPi/src/plugins/<plugin_id>/...` zip layout so the existing deploy script can unzip from the package root. Validate with a short Python render smoke that returns `(800, 480) RGB` before starting any long-running dev server.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/flow_progress/flow_progress.py, inkypi-weather/dist/flow-progress-20260526.zip
- Tags: inkypi, plugin-install, flow-progress, packaging, smoke-test

---

## [LRN-20260526-023] best_practice

**Logged**: 2026-05-26T00:45:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
Use short smoke checks for InkyPi integration before starting long-running dev servers.

### Details
Starting the InkyPi PC dev server from Codex can hold the tool session open even when the app itself is running as a child process. For plugin validation, prefer short checks first: syntax compile, direct render smoke, Config plugin discovery, and plugin registry instantiation. Only start the dev server when UI/manual browser validation is required, and use a launcher that fully detaches stdout/stderr.

### Suggested Action
Keep `tools/smoke_epaper_pet.py` as the first validation path for the e-paper pet plugin. If a long-running server is needed later, create or reuse a dedicated background launcher script instead of invoking `run_pc_dev.ps1` directly from an interactive tool call.

### Metadata
- Source: implementation
- Related Files: tools/smoke_epaper_pet.py, inkypi-weather/package/run_pc_dev.ps1
- Tags: inkypi, dev-server, smoke-test, windows, long-running-process

---

## [LRN-20260526-022] correction

**Logged**: 2026-05-26T00:44:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Do not assume hardware buttons or touch input for the e-paper pet.

### Details
The user clarified that the e-paper pet cannot depend on interactive buttons because the device has no buttons. The pet should be autonomous by default: heartbeat updates should handle self-care, naps, ambient expression changes, status drift, and visible mood changes without requiring feed/play/clean controls.

### Suggested Action
Keep e-paper pet UI settings limited to configuration. Avoid user-facing action buttons as the core loop. Hidden HTTP/debug actions are acceptable only as optional maintenance hooks; the visible product should feel alive through autonomous state and low-frequency expression refresh.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py, inkypi-weather/package/InkyPi/src/plugins/epaper_pet/settings.html
- Tags: epaper, virtual-pet, no-buttons, autonomous-care, low-refresh

---

## [LRN-20260526-021] best_practice

## [LRN-20260526-021] best_practice

**Logged**: 2026-05-26T00:00:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Design e-paper pets around low-refresh expressions, not continuous animation.

### Details
The user likes the OpenClawGotchi-style architecture for e-paper pets because it treats the display as a mood/expression surface instead of a high-frame-rate animation screen. For future pet work, prefer state-driven faces, emoji, kaomoji, small icon changes, and event-based refreshes over frequent sprite animation. The "alive" feeling should come from lifecycle state, heartbeat decisions, personality, and contextual expressions rather than constant screen updates.

### Suggested Action
Implement e-paper pets with a persistent pet state, a heartbeat/tick loop, a face/mood renderer, and dirty-region or event-based display refresh. Use full or partial refresh only when mood, status, message, or important lifecycle state changes. Avoid marquee text, rapid animation loops, and unnecessary redraws.

### Metadata
- Source: user_feedback
- Related Files: TBD
- Tags: epaper, virtual-pet, openclawgotchi, low-refresh, expressions, kaomoji, emoji

---

## [LRN-20260526-022] correction

**Logged**: 2026-05-26T01:25:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
Mini Weather current temperature unit must be measured as part of the temperature group.

### Details
The user reported that the `°C` label overlapped the large current temperature number on the e-paper screen. The PIL fallback renderer was positioning the unit independently at the right edge of the current card, so when the temperature digits shifted right to avoid the weather icon, the unit could overlap the number.

### Suggested Action
Preserve `_draw_current_conditions(...)` in `mini_weather.py`. It measures icon width, temperature text width, unit width, and gaps as one group, then shrinks the temperature/font/icon size until the whole group fits. Avoid returning to fixed right-edge unit placement for the current temperature card.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/mini_weather/mini_weather.py, inkypi-weather/dist/mini-weather-temp-unit-layout-clean-20260526.zip
- Tags: inkypi, mini-weather, epaper, temperature, layout, pil-fallback

---

## [LRN-20260526-021] correction

**Logged**: 2026-05-26T00:58:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Third-party InkyPi plugins may need manual zip install and PIL rendering on the armv6 Pi.

### Details
`inkypi plugin install mini_weather https://github.com/saulob/InkyPi-Mini-Weather` failed because the Pi did not have `git` installed. The package also runs on `armv6l`, where `/usr/bin/chromium-headless-shell` exits with `Illegal instruction`, so HTML screenshot rendering fails with return code 132 even for a minimal HTML page. Mini Weather was made usable by installing the GitHub zip manually and adding a Pillow fallback renderer that runs when the original HTML renderer returns `None`.

### Suggested Action
For future third-party InkyPi plugin installs on this device, first check `command -v git` and `chromium-headless-shell --version`. If `git` is absent, download the repo zip locally and deploy the plugin directory by scp/unzip to both the active package and `/usr/local/inkypi`. If a plugin depends on `BasePlugin.render_image`, smoke-test rendering and add a PIL fallback or avoid the plugin until Chromium is replaced with an armv6-compatible renderer.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/mini_weather/mini_weather.py, inkypi-weather/dist/mini-weather-plugin-pil-fallback-fontfix-20260526.zip
- Tags: inkypi, third-party-plugin, mini-weather, armv6, chromium, pil-fallback

---

## [LRN-20260526-020] best_practice

**Logged**: 2026-05-26T00:20:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
Use dark, thresholded world-map watermarks for Daily AI News backgrounds.

### Details
The user approved making the Daily AI News page more interesting with the provided world-news background image, but prior feedback still requires avoiding white speckles, gray haze, and reduced text readability on e-paper. The accepted approach is to ship the source image as a plugin asset and render only its darker contours as a low-brightness blue-gray watermark over the black page. Do not use the original bright/gradient image directly.

### Suggested Action
Preserve `BACKGROUND_IMAGE = "background_world_news.png"` and `_base_background(...)` in `daily_ai_news.py`. Keep alpha thresholding conservative enough that large page areas remain pure black, with the world map and lower arcs visible only as subtle near-black structure behind the news layout.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py, inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/background_world_news.png, inkypi-weather/dist/daily-ai-news-dark-world-bg-v2-20260526.zip
- Tags: inkypi, daily-ai-news, epaper, background, world-map, no-dither

---

## [LRN-20260525-019] correction

**Logged**: 2026-05-25T23:58:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Daily AI News must preserve all visible headline slots and keep top news fresh, specific, and RSS-grounded.

### Details
The user rejected layouts that silently drop the left top #2/#3 stories or leave the right news column mostly empty. For `daily_ai_news`, the left top block should force the first three stories into view with adaptive typography, and the right supplement column should fit #4-#6, producing a balanced left-three/right-three layout rather than a crowded four-item right rail. The top-news generation path should rank recent hard-news RSS items, demote evergreen/broad items, dedupe repeated events, and keep market snapshots out of top-news generation so stock data cannot be transformed into invented headlines.

### Suggested Action
Preserve `SUMMARY_SCHEMA_VERSION = "fresh-hard-news-rss-only-dedupe-v7"`, the RSS-only top-news prompt rules, `_rank_news_items(...)`, `_dedupe_top_items(...)`, and the forced/adaptive `_draw_news_items_fit(...)` rendering for the top and side news columns. Keep the visible top-news layout at left #1-#3 and right #4-#6. Treat stale instance title `二狗新闻` as `整点新闻` during render unless the user sets a different explicit title.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py, inkypi-weather/dist/daily-ai-news-title-right-top7-20260525.zip
- Tags: inkypi, daily-ai-news, epaper, rss-only, fresh-news, adaptive-layout

---

## [LRN-20260525-018] best_practice

**Logged**: 2026-05-25T23:46:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
Use the top-right Steam profile panel area for up to four real online friend avatars.

### Details
The user marked the empty top-right area of the Steam dashboard profile panel as a 2x2 online-friends avatar slot. The plugin should use Steam `GetPlayerSummaries` avatar URLs (`avatarfull`, then `avatarmedium`, then `avatar`) from currently online friends and draw the first four after the plugin's friend sorting. The area should only reserve width when online avatars are present.

### Suggested Action
Preserve `STEAM_DASHBOARD_STYLE_VERSION = "solid-dark-wrap-friend-avatars-v1"`, `_online_friends_for_avatars(...)`, and `_draw_online_friend_avatars(...)`. Keep real avatar caching through `_avatar_image(...)`, and validate the top panel text still fits beside the 2x2 avatar grid.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py, inkypi-weather/dist/steam-profile-dashboard-solid-dark-friend-avatars-20260526.zip
- Tags: inkypi, steam, friends, avatars, epaper

---

## [LRN-20260525-017] correction

**Logged**: 2026-05-25T23:38:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Use Steam Store `schinese` names verbatim; do not clean English affixes.

### Details
The user reversed the earlier request to strip English suffixes/prefixes from Steam `schinese` game names. The desired behavior is now to display the full Steam Store `schinese` name exactly as returned. Examples: Timberborn AppID `1062090` should display `海狸浮生记 Timberborn`; Wallpaper Engine AppID `431960` should display `Wallpaper Engine：壁纸引擎`. English remains a fallback only when no `schinese` name is available.

### Suggested Action
Keep `STEAM_NAME_DISPLAY_VERSION = "zh-store-full-single-fetch-v1"` and avoid reintroducing `_clean_primary_game_name`, CJK suffix stripping, or prefix stripping. Keep the single-AppID Store fetch behavior because comma-separated Store `appdetails` requests return 400 for multiple AppIDs on the Pi.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py, inkypi-weather/dist/steam-profile-dashboard-solid-dark-full-schinese-20260526.zip
- Tags: inkypi, steam, localization, schinese-verbatim, epaper

---

## [LRN-20260525-016] correction

**Logged**: 2026-05-25T23:16:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Use pure black large-area fills for the Steam dashboard to avoid e-paper dithering dots.

### Details
The user reported that the dark background had too many dots. The likely cause is e-paper conversion dithering large dark-gray fills. The Steam dashboard should use pure black for the page background and panel fills, and pure white for large text, borders, and separators. Small status dots can remain colored, but avoid large gray fills in the background.

### Suggested Action
Preserve `STEAM_DASHBOARD_STYLE_VERSION = "solid-dark-wrap-v1"` and keep `bg = (0, 0, 0)`, `panel = (0, 0, 0)`, and pure-white panel borders. Validate the preview's dominant color is pure black before deploying.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py, inkypi-weather/dist/steam-profile-dashboard-solid-dark-20260526.zip
- Tags: inkypi, steam, epaper, pure-black, no-dither

---

## [LRN-20260525-015] correction

**Logged**: 2026-05-25T23:10:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Strip trailing English suffixes from Steam `schinese` game names when they already include Chinese.

### Details
Timberborn AppID `1062090` returns `海狸浮生记 Timberborn` from Steam Store `appdetails` with `l=schinese`, while the desired dashboard display is `海狸浮生记`. The plugin should treat this as a Chinese title with an appended English suffix, not as an English fallback case.

### Suggested Action
Keep `_clean_primary_game_name(...)`: if the primary `schinese` name contains CJK characters and ends with the exact English name, remove the trailing English suffix. Keep `STEAM_NAME_DISPLAY_VERSION = "zh-clean-fallback-en-v1"` in the cache key so old localized-name caches are invalidated.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py
- Tags: inkypi, steam, localization, chinese-title-cleanup, timberborn

---

## [LRN-20260525-014] correction

**Logged**: 2026-05-25T22:59:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
For Steam dashboard localization, English is a fallback, not a second displayed name.

### Details
The user clarified that "English second" means priority order: display the Simplified Chinese Steam Store name when available; only display English when no Simplified Chinese name exists. Do not render `简中 / English` pairs by default. The user also rejected refresh-driven scrolling for static e-paper text. Long sentences should wrap into complete lines with no `...`; if a lower-priority row no longer fits after wrapping, skip the row rather than drawing an incomplete sentence.

### Suggested Action
Keep `STEAM_NAME_DISPLAY_VERSION = "zh-fallback-en-v1"` and `STEAM_DASHBOARD_STYLE_VERSION = "dark-wrap-v1"` for future Steam plugin work. Validate previews for no ellipsis, no marquee/scroll dependency, Chinese-only names where Chinese exists, English fallback where Chinese does not, and no overlapping text.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py, inkypi-weather/dist/steam-profile-dashboard-dark-wrap-zh-fallback-20260526.zip
- Tags: inkypi, steam, localization, wrapping, no-ellipsis, epaper

---

## [LRN-20260525-016] best_practice

**Logged**: 2026-05-25T23:29:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
Update InkyPi playlist instance settings through the app API when `device.json` is not writable by the deployment user.

### Details
On the Raspberry Pi package, `src/config/device.json` can be readable but not writable for the SSH deployment user. Direct JSON edits can fail with `PermissionError`, while the running InkyPi app can persist instance settings through `PUT /update_plugin_instance/<instance>`. For `daily_ai_news`, this was the reliable way to change `brief_title` from `二狗新闻` to `整点新闻` without resetting other settings.

### Suggested Action
When changing an existing InkyPi plugin instance setting, read the current settings, POST or PUT the full settings payload through the app endpoint, restart only if code/defaults changed, then refresh the playlist instance and verify `/api/current_image`.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py, inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/settings.html
- Tags: inkypi, config, playlist-instance, deployment, raspberry-pi

---

## [LRN-20260525-015] correction

**Logged**: 2026-05-25T23:19:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
Daily AI News bottom row should be a two-column A-share and US-stock market brief, not generic risk/data/watch bullets.

### Details
The user found the old bottom news summary boring. For `daily_ai_news`, fetch major index snapshots and render the bottom row as two wide modules: `A股今日` and `美股今日`. Programmatically format the first line of market percentage moves from raw quote data to avoid model typos in index names or percentages. Let the second line be a short deterministic market tone so it always fits.

### Suggested Action
Keep market data in `market_snapshot`, render summary lines as compact index moves such as `上证-1.16% 深成+.87% 创业板-.64%`, and verify the final `/api/current_image` after playlist display refresh.

### Metadata
- Source: user_correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py
- Tags: inkypi, daily-news, market-brief, a-share, us-stocks, epaper

---

## [LRN-20260525-014] correction

**Logged**: 2026-05-25T23:04:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For dark InkyPi news screens, use exact palette black and validate the final playlist current image, not only plugin render output.

### Details
Near-black RGB backgrounds such as `(7, 8, 13)` can become visible speckling after e-paper processing. Use exact `(0, 0, 0)` for the background and header when the user asks for a pure dark news page. Also, `/update_now` manual preview can overwrite `current_image.png` with form/default settings; after deploying `daily_ai_news`, verify `/display_plugin_instance` for the playlist instance and fetch `/api/current_image` to confirm the actual displayed image.

### Suggested Action
For future `daily_ai_news` layout updates, keep background colors palette-safe, keep detail text large enough for 800x480 e-paper, then verify in this order: local render, Pi cached render, playlist display update, `/api/current_image` fetch.

### Metadata
- Source: user_correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py
- Tags: inkypi, daily-news, epaper, pure-black, playlist-display

---

## [LRN-20260525-013] best_practice

**Logged**: 2026-05-25T22:45:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Use the Steam Profile Dashboard dark layout as the default visual standard.

### Details
The user explicitly selected a dark style as the future default for the InkyPi Steam dashboard. The preferred look is a dark neutral background, filled dark panels, high-contrast light text, Steam blue and online green accents, and pixel-width clipping for long bilingual game names. The lower-right common-games area should be an independent `常玩 TOP 3` ranked list instead of consecutive bullet rows.

### Suggested Action
For future `steam_profile_dashboard` updates, preserve `STEAM_DASHBOARD_STYLE_VERSION = "dark-v1"`, keep the style version in the cache key, and validate with a local 800x480 preview before deploying to the Pi. Avoid reverting to the old white background or bullet-only common-game list.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py, inkypi-weather/dist/steam-profile-dashboard-dark-default-20260526-v2.zip
- Tags: inkypi, steam, dark-style, top3, epaper, raspberry-pi

---

## [LRN-20260525-013] correction

**Logged**: 2026-05-25T22:50:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For Daily AI News, do not hide sentence endings with ellipses; use available right-side space before dropping content.

### Details
The 800x480 news layout should render complete Chinese news sentences and avoid `...` truncation. If a dense news view needs more capacity, split the upper news area into a main column plus a right-side quick-news column, enlarge detail text enough for e-paper readability, and reduce decorative gray divider lines. For model outputs, normalize module items from either strings or dictionaries so fields such as `risk`, `signal`, and `watch` do not render as Python dict text.

### Suggested Action
When updating `daily_ai_news`, wrap full items and omit only whole overflowing items. Keep the right column wide enough for quick-news details, keep detail text at least 14px on 800x480, and verify with a Pi-side cached render plus actual display log before reporting success.

### Metadata
- Source: user_correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py
- Tags: inkypi, daily-news, epaper-layout, typography, no-ellipsis

---

## [LRN-20260525-012] best_practice

**Logged**: 2026-05-25T22:18:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For Steam Profile Dashboard game names, render visible AppIDs as Simplified Chinese first and English second using Steam Store appdetails.

### Details
Steam Web API profile, recent-game, owned-game, and friend presence fields can expose English `name` / `gameextrainfo` values. To make all visible game names Chinese-first, collect only the AppIDs that the dashboard can render, fetch Steam Store appdetails in `schinese` and `english`, and cache a `localized_game_names` display value such as `赛博朋克 2077 / Cyberpunk 2077`. The Pi-side smoke test can call `_fetch_store_appdetails_map(['1091500'], 'schinese', 'basic')` and `_fetch_store_appdetails_map(['1091500'], 'english', 'basic')` without triggering an e-paper refresh.

### Suggested Action
When updating `steam_profile_dashboard`, keep all rendered game names behind `_display_game_name(...)`, include a name-display version in the cache key, and verify both deployment (`/plugin/steam_profile_dashboard`) and a no-render Store API sample before reporting success.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py, tools/epaperpod-deploy-zip.ps1
- Tags: inkypi, steam, localization, game-names, raspberry-pi

---

## [LRN-20260525-011] best_practice

**Logged**: 2026-05-25T22:05:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For InkyPi AI Image Multiverse model additions, verify the actual dropdown option after deployment, not just the hidden pipeline config.

### Details
The AI Image Multiverse settings page embeds the full `MODEL_PIPELINE` JSON in a hidden `data-config` block, so a simple grep for a model id can pass even if the model is not present in the rendered `<select>`. After adding `gpt-image-2`, the reliable smoke check was fetching `/plugin/ai_image_multiverse` on the Pi and confirming the actual `<option value="gpt-image-2">Image 2 (OpenAI)</option>` entry. The Pi may report `inkypi` as `active` before port 80 is ready; wait for the plugin URL to return before concluding the deployment is good.

### Suggested Action
When adding or updating AI Image Multiverse models, patch `MODEL_PIPELINE`, run an AST parse check locally, deploy the plugin zip, then verify both `systemctl is-active inkypi` and the rendered plugin page option. On this Windows environment, avoid relying on `.NET` `System.IO.Path.GetRelativePath` for packaging and use unique zip names if a failed archive remains locked.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/ai_image_multiverse/ai_image_multiverse.py, inkypi-weather/package/InkyPi/src/plugins/ai_image_multiverse/settings.html, tools/epaperpod-deploy-zip.ps1
- Tags: inkypi, ai-image-multiverse, gpt-image-2, deployment, raspberry-pi

---

## [LRN-20260525-012] correction

**Logged**: 2026-05-25T22:28:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Use the Responses API with minimal reasoning for GPT-5-nano daily news summaries.

### Details
The first Daily AI News smoke test used Chat Completions with `gpt-5-nano` and a small `max_completion_tokens` cap. The request reached OpenAI but returned empty visible output because GPT-5 reasoning tokens can consume the output budget. Switching to `client.responses.create(...)` with `reasoning={"effort":"minimal"}`, `text={"verbosity":"low"}`, and a larger `max_output_tokens` produced a valid JSON summary on the Raspberry Pi.

### Suggested Action
For unattended InkyPi text-summary plugins that default to GPT-5-nano, call the Responses API first and keep Chat Completions only as a fallback. Cache successful daily output before display rotation, and keep `daily_api_limit` at 1 by default so manual displays reuse cache instead of spending additional API calls.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py
- Tags: inkypi, openai, gpt-5-nano, responses-api, daily-news, cache

---

## [LRN-20260525-011] correction

**Logged**: 2026-05-25T22:10:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Do not assume ChatGPT subscription image-generation quota can fund InkyPi plugin API calls.

### Details
OpenAI documents ChatGPT subscriptions and API billing as separate systems. InkyPi plugins running unattended on the Raspberry Pi must use an API key for OpenAI image generation, and those calls are billed on the API platform. A ChatGPT Plus/Pro membership may include image generation inside ChatGPT, but it is not an API credit pool for local plugins.

### Suggested Action
For future InkyPi image-generation plugins, offer one of three explicit modes: OpenAI API with a model/quality/budget cap, deterministic local posterization with no API cost, or manual upload of images generated in ChatGPT. Avoid browser-cookie automation or unofficial ChatGPT scraping as a workaround.

### Metadata
- Source: official_docs_check
- Related Files: inkypi-weather/package/InkyPi/src/plugins
- Tags: inkypi, openai, api-billing, image-generation, chatgpt

---

## [LRN-20260525-010] best_practice

**Logged**: 2026-05-25T22:02:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
Use FandolKai as the redistributable Fangzheng-Xinkai-like fallback for Chinese e-paper typography.

### Details
Founder 方正新楷 is not safe to fetch and bundle without an explicit licensed font file. For this InkyPi package, CTAN's FandolKai-Regular.otf is a practical bundled substitute: it is a Chinese Kai-style OpenType font, available from the CTAN fandol package under GPL, and renders correctly through PIL on both PC and the Raspberry Pi. Keep the UI label honest, such as `方正新楷近似`, rather than naming it as the proprietary Founder font.

### Suggested Action
When the user asks for 方正新楷-like typography, register `方正新楷近似` to `plugins/chinese_literature_clock/fonts/FandolKai-Regular.otf`, include `Fandol-COPYING.txt`, and smoke-test both `get_font("方正新楷近似", size)` and a full 800x480 plugin render on the Pi. If an old saved setting such as `LXGW WenKai` or `康熙字典体` should be migrated, handle it in plugin rendering because Pi `device.json` may be root-owned.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/utils/app_utils.py, inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/chinese_literature_clock.py, inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/fonts/FandolKai-Regular.otf
- Tags: inkypi, typography, chinese, kai, fandol, font-license

---

## [LRN-20260525-009] best_practice

**Logged**: 2026-05-25T21:45:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
When mining Project Gutenberg Chinese texts for literature-clock seeds, remove hard line wrapping before sentence extraction.

### Details
Project Gutenberg Chinese ebook text can contain line breaks in the middle of sentences. Splitting by physical lines truncates quotes and produces weaker seed data. Strip Gutenberg boilerplate first, collapse whitespace, then split on Chinese sentence punctuation before matching precise clock expressions and period expressions. Keep exact HH:MM matches in a separate quota from shichen/geng/daypart fallback rows so the dataset grows without burying precise time quotes.

### Suggested Action
For future `chinese_literature_clock` seed expansions, download public-domain UTF-8 text into `.tmp`, normalize by removing whitespace, extract exact clock rows first, then cap period fallback rows by key/source. Record Project Gutenberg ebook IDs in `data/SOURCES.md` and validate with CSV field counts, exact-time coverage, local render smoke, and Pi render smoke.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/data/chinese_litclock.csv, inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/data/SOURCES.md, .tmp/build_chinese_litclock_expanded.py
- Tags: inkypi, literature-clock, chinese, gutenberg, dataset

---

## [LRN-20260525-008] best_practice

**Logged**: 2026-05-25T21:35:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For Kangxi-style Chinese e-paper typography, prefer I.Ming over unclear reposted Kangxi font files.

### Details
Several online "康熙字典体" TTF downloads are reposted or unclear about redistribution rights. I.Ming is an open-licensed Ming typeface under IPA Font License v1.0, documents its Kangxi/old-glyph design basis, includes Kangxi Dictionary headwords, and can be packaged plugin-locally for InkyPi. When bundling it, include the IPA license file and register a user-facing alias such as `康熙字典体` in `app_utils.FONT_FAMILIES`.

### Suggested Action
For future Chinese literature/e-paper plugins that need Kangxi-style type, download `I.Ming-8.10.ttf` from the official `ichitenfont/I.Ming` release/tree, store it under the plugin `fonts/` directory, include `IPA_Font_License_Agreement_v1.0_chi.md`, and smoke-test `get_font("康熙字典体", size)` plus direct image rendering on the Pi venv.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/fonts/I.Ming-8.10.ttf, inkypi-weather/package/InkyPi/src/utils/app_utils.py
- Tags: inkypi, typography, kangxi, iming, chinese, font-license

---

## [LRN-20260525-010] best_practice

**Logged**: 2026-05-25T21:50:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
For third-party InkyPi plugins with missing Python dependencies, use a plugin-local `_vendor` directory before asking for broader sudo.

### Details
`InkyPi-Image-Multiverse` imports `google.genai` at module load time, so the plugin will not appear in InkyPi until `google-genai` is importable. Installing into `/usr/local/inkypi/venv_inkypi` as the deployment user failed with permission denied, but `pip install --target InkyPi/src/plugins/ai_image_multiverse/_vendor google-genai` worked. Add a small `sys.path` hook near the top of the plugin module before third-party imports so the vendored packages are available during plugin discovery.

The Pi can take more than a minute to finish Waveshare display init and plugin imports before port 80 serves pages. Do not run parallel import/page checks during restart; wait for `http://127.0.0.1/` to return 200, then check the plugin page and registry. If a local deployment HTTP server times out, inspect and close any leftover listener on port 8766.

### Suggested Action
For future dependency-heavy InkyPi plugins, package the plugin with Unix-style zip paths, vendor missing packages into the plugin directory on the Pi, restart InkyPi once dependencies are present, then verify `/plugin/<id>` returns 200 and the plugin registry can instantiate the plugin under a Flask app context.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/ai_image_multiverse/ai_image_multiverse.py, inkypi-weather/dist/ai-image-multiverse-20260525.zip
- Tags: inkypi, plugin, dependencies, vendor, raspberry-pi, deployment

---

## [LRN-20260525-007] best_practice

**Logged**: 2026-05-25T21:25:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For Chinese literature clock plugins, separate exact clock matches from traditional Chinese time periods and use character wrapping.

### Details
Public-domain Chinese novels contain fewer exact HH:MM phrases than English literature-clock datasets. Late Qing texts may include modern clock phrases such as `九點一刻` or `十二點半`, while classical novels more often use `子時`, `三更`, `五更`, `黃昏`, or `掌燈`. A practical matcher should try exact minute and near-minute rows first, then nearby quarter/half/hour rows, and only then fall back to shichen/geng/daypart buckets. Chinese quote rendering also cannot rely on whitespace word wrapping; direct Pillow renderers need character-based wrapping so long Chinese sentences fit the 800x480 canvas.

### Suggested Action
When adding or expanding Chinese literary time datasets, store exact `HH:MM` rows whenever the source has clock time and store broader rows as `period:*`. Keep the fallback order explicit, and test the plugin with direct 800x480 rendering on both Windows and the Pi venv before deployment.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/quote_picker.py, inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/chinese_literature_clock.py
- Tags: inkypi, literature-clock, chinese, pillow, time-matching

---

## [LRN-20260525-006] best_practice

**Logged**: 2026-05-25T21:20:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For InkyPi plugin localization on Windows, validate UTF-8 and syntax without relying on PowerShell display or `py_compile`.

### Details
PowerShell can display UTF-8 Simplified Chinese plugin files as mojibake even when the file is correct. Confirm localized HTML/JSON/Python content with Python `read_text(encoding="utf-8")` or `unicode_escape` output before treating text as corrupted. `python -m py_compile` can fail in this workspace because `__pycache__` bytecode rename operations hit `Access is denied`; use `python -B -c` with `ast.parse(...)` for syntax checks when bytecode output is not needed.

For direct Pillow render smoke tests, the global Python environment may lack InkyPi runtime dependencies such as `requests`, while `.pc-packages` can contain binary wheels incompatible with the current Python. A narrow isolated render test can load the plugin module with simple stubs for `BasePlugin` and `utils.http_client` when the test only exercises pure rendering paths.

### Suggested Action
When localizing InkyPi plugins, verify strings through UTF-8 reads, run AST syntax checks instead of bytecode compilation, and smoke-render a sample 800x480 image before packaging the plugin zip.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py, inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/settings.html
- Tags: inkypi, localization, utf-8, py_compile, smoke-test, windows

---

## [LRN-20260525-005] best_practice

**Logged**: 2026-05-25T21:05:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For InkyPi third-party plugin fonts, prefer plugin-local font files when the deployment user cannot write `src/static/fonts`.

### Details
The Pi deployment user can unzip files into `src/plugins/literature_clock`, but cannot create or write `src/static/fonts`, causing a font install package to fail before service restart. Keeping `LXGWWenKai-Regular.ttf` under `src/plugins/literature_clock/fonts/` and teaching `app_utils.resolve_font_path()` to resolve `plugins/...` font paths allowed the font to appear in global InkyPi font lists without broadening sudo privileges.

### Suggested Action
When adding fonts for third-party InkyPi plugins, first check whether `src/static/fonts` is writable. If not, ship the font under the plugin directory, register the font family with a `plugins/<plugin_id>/fonts/<file>.ttf` path, and validate with the Pi venv using `get_font()` plus a direct image render smoke test.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/utils/app_utils.py, inkypi-weather/package/InkyPi/src/plugins/literature_clock/fonts/LXGWWenKai-Regular.ttf
- Tags: inkypi, font, lxgw-wenkai, plugin, raspberry-pi

---

## [LRN-20260525-004] best_practice

**Logged**: 2026-05-25T20:58:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For direct-Pillow InkyPi plugins using `style_settings`, set both render-time defaults and settings-page defaults.

### Details
InkyPi's shared plugin style panel defaults `backgroundColor` to `#ffffff` and `textColor` to `#000000`. If a direct-Pillow plugin only changes its Python fallback colors, a newly created plugin instance can still submit white/black values from the form and override the intended visual default. The Literature Clock black-background update needed both Python defaults and a `DOMContentLoaded` settings-page override for new instances.

### Suggested Action
When changing default colors for a Pillow-rendered plugin, update the Python renderer to handle missing settings and update the plugin `settings.html` to set the shared `backgroundColor` / `textColor` inputs for non-edit creation.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/literature_clock/literature_clock.py, inkypi-weather/package/InkyPi/src/plugins/literature_clock/settings.html
- Tags: inkypi, plugin, style-settings, pillow, defaults

---

## [LRN-20260525-003] best_practice

**Logged**: 2026-05-25T20:50:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
For third-party InkyPi plugin deployment, package zip entries with Unix-style paths and verify the plugin can generate an image on the Pi, not just appear in the web UI.

### Details
PowerShell `Compress-Archive` produced zip entries with backslash path separators; Raspberry Pi `unzip` extracted them but returned a warning/non-zero status, which stopped the automatic deployment chain before service restart. A .NET `ZipArchive` package with forward-slash entry names avoided that failure. Also, a plugin can appear in the InkyPi UI from `plugin-info.json` even if its real image path fails. The Literature Clock plugin initially used InkyPi `render_image`, but Chromium returned code 132 on the Pi, so simple e-paper plugins should prefer direct Pillow rendering when possible. Manual SSH tests run as the Pi user may not be able to read `/usr/local/inkypi/src/static/fonts`, while the service runs as root; font loading should still have a Pillow default fallback.

### Suggested Action
When installing more third-party plugins, build deploy zips with normalized `/` paths, run `tools/epaperpod-deploy-zip.ps1`, wait for InkyPi to finish its slow display init, confirm the web UI lists the plugin, and run a direct `generate_image` smoke test inside `/usr/local/inkypi/venv_inkypi` before calling the install done.

### Metadata
- Source: implementation
- Related Files: tools/epaperpod-deploy-zip.ps1, inkypi-weather/package/InkyPi/src/plugins/literature_clock/literature_clock.py
- Tags: inkypi, plugin, deployment, zip, pillow, raspberry-pi

---

## [LRN-20260525-003] best_practice

**Logged**: 2026-05-25T21:05:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For Steam profile dashboards on Pi Zero WH, split live status refresh from heavier Steam profile data.

### Details
Steam online status and currently-playing game can be refreshed with `ISteamUser/GetPlayerSummaries` as a single lightweight request, while friends, owned games, recent games, badges, bans, and store app details should use a longer cache. This keeps "now playing" responsive without making every e-paper update scan the whole profile. Cache real Steam avatars by URL so the dashboard still uses the true `avatarfull` image without redownloading it on every render.

PowerShell `Compress-Archive` creates zip entries that can trigger Linux `unzip` warnings about backslash path separators and non-zero exit codes. For Pi deployment zips, prefer Python `zipfile` with explicit forward-slash archive names.

### Suggested Action
Keep `steam_profile_dashboard` on a two-tier cache: `statusCacheSeconds` for live status and `fullCacheMinutes` for heavy profile data. Package plugin zips with forward-slash paths before using automated deploy scripts.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py, inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/settings.html
- Tags: inkypi, steam, cache, raspberry-pi, deployment

---

## [LRN-20260525-002] best_practice

**Logged**: 2026-05-25T20:10:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
For future direct Pi execution from Codex, use a project-local SSH key plus limited passwordless sudo for the InkyPi service.

### Details
Password-based SSH blocks unattended `ssh` and `scp` tool execution, even when the Pi is reachable. The practical setup is to keep a dedicated private key under the project `.ssh/` directory, install its public key into the Pi user's `~/.ssh/authorized_keys`, and add a narrow sudoers rule that only permits `systemctl start/stop/restart/status inkypi` without a password. This allows plugin zips to be fetched/unzipped under the user's home directory and then restart only the InkyPi service.

On this Windows workspace, `ssh-keygen` created usable private keys but failed to save `.pub` files on the `G:` drive with `Bad file descriptor`; derive the public key with `ssh-keygen -y` and store it in a non-`.pub` text file when needed.

### Suggested Action
Before promising unattended Pi deployment, verify `tools/epaperpod-test-key.ps1` succeeds with `BatchMode=yes`. Use `tools/epaperpod-deploy-zip.ps1` for zip deployment only after the bootstrap has installed the key and limited sudoers rule.

### Metadata
- Source: implementation
- Related Files: tools/epaperpod-test-key.ps1, tools/epaperpod-deploy-zip.ps1, inkypi-weather/dist/epaperpod_codex_bootstrap.sh
- Tags: inkypi, ssh, raspberry-pi, deployment, sudo

---

## [LRN-20260524-006] insight

**Logged**: 2026-05-24T17:00:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For beginner Raspberry Pi e-paper setups, prefer a board with a pre-soldered 40-pin header.

### Details
The user is not comfortable with patient GPIO soldering, and partial soldering made hardware debugging ambiguous. A Raspberry Pi Zero WH or Zero 2 W/WH-style board with a factory/pre-soldered 40-pin header removes the most likely contact issue. However, Zero WH is the older Pi Zero W class and requires a 32-bit Raspberry Pi OS image, while Zero 2 W supports the 64-bit image already used in this setup.

### Suggested Action
When replacing hardware, recommend a pre-soldered header board. Prefer Zero 2 W/WH when available for compatibility with the existing 64-bit setup and better performance; use 32-bit Raspberry Pi OS Lite if the replacement is a Zero WH.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/install_on_pi.sh, inkypi-weather/package/README_PACKAGE.md
- Tags: raspberry-pi, epaper, gpio, soldering, hardware-debug

---

## [LRN-20260524-005] insight

**Logged**: 2026-05-24T16:45:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
Do not continue software-level e-paper debugging while the Raspberry Pi Zero GPIO header is only partially soldered.

### Details
On the Zero 2 W + Waveshare e-Paper Driver HAT setup, the OS can show `/dev/spidev0.0` and `/dev/spidev0.1` even if the physical 40-pin header is not reliably soldered. Partial or friction-only header contact can still allow SSH and system boot, but any missing/unstable control or SPI pin can make `epd7in5_V2.EPD().init()` hang with no panel flash. The key pins include BCM GPIO17/RST, GPIO25/DC, GPIO8/CS0, GPIO24/BUSY, GPIO18/PWR, GPIO10/MOSI, GPIO11/SCLK, plus 3.3V/5V/GND.

### Suggested Action
Before judging the panel, HAT, driver, or InkyPi service, power off and fully solder/inspect all 40 GPIO pins or at least verify continuity for the required pins. Treat partial soldering as a hardware blocker for `ReadBusy()` or `SPI.writebytes()` hangs.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/display/waveshare_epd/epdconfig.py, inkypi-weather/package/InkyPi/src/display/waveshare_epd/epd7in5_V2.py
- Tags: inkypi, epaper, gpio, soldering, hardware-debug

---

## [LRN-20260524-004] insight

**Logged**: 2026-05-24T15:50:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
InkyPi can appear installed but not serve the web UI if Waveshare display initialization blocks before Flask starts.

### Details
On the Raspberry Pi Zero 2 W setup, `inkypi.service` was `active (running)` but port 80 did not listen because startup blocked while loading `epd7in5_V2`; logs stopped at `Loading EPD display for epd7in5_V2 display` and the traceback showed `ReadBusy()`. This means hardware initialization can block the web UI before users can configure plugins. Use `display_type=mock` to separate web/app validation from HAT/ribbon/power/driver troubleshooting.

### Suggested Action
When port 80 is closed but `inkypi.service` is active, check `journalctl -u inkypi` and `ss -ltnp`. If logs stop at Waveshare `ReadBusy()`, switch temporarily to mock display, verify the web UI, then inspect HAT seating, ribbon lock, power supply, and the exact PCB/FPC model before changing drivers.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/display/waveshare_display.py, inkypi-weather/package/InkyPi/src/display/waveshare_epd/epd7in5_V2.py
- Tags: inkypi, waveshare, epaper, readbusy, hardware-debug

---

## [LRN-20260525-001] correction

**Logged**: 2026-05-25T18:20:00-07:00
**Priority**: medium
**Status**: pending
**Area**: epaper

### Summary
For Steam game art on the e-paper display, prefer Steam Library Hero images over capsule/header images.

### Details
The user clarified that the goal is not a maintained list of game IDs or generic Steam capsules. The desired source is Steam's current daily/store promotional items, using each game's horizontal library display artwork where possible. For Steam app CDN assets this means `library_hero.jpg` should be the primary image, with capsule/header URLs only as fallback. Do not rotate portrait/profile images; if a fallback image is not suitably horizontal, preserve its orientation and place it on the horizontal 800x480 canvas.

The logo overlay should not use a fixed position by default. Steam library hero art varies, so place `logo.png` in a low-detail/blank region of the final 800x480 crop, with Golden Left only as a manual fallback.

### Suggested Action
For `steam_daily_art`, default `imageMode` to `library_hero`, default `logoPosition` to automatic empty-space placement, fetch current Steam featured/daily items from the internet, and keep the processing path pure PIL without Chromium.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_daily_art/steam_daily_art.py, inkypi-weather/package/InkyPi/src/plugins/steam_daily_art/settings.html
- Tags: inkypi, steam, game-art, epaper, raspberry-pi

---

## [LRN-20260524-003] correction

**Logged**: 2026-05-24T01:40:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
When using OpenWeather One Call 3.0 for this InkyPi package, keep usage below the free tier and do not rely on OpenWeather's default call limit.

### Details
The user enabled One Call 3.0 but explicitly does not want paid overage. OpenWeather's free One Call allowance is 1,000 calls/day, while the official FAQ says the default post-subscription daily limit is 2,000 calls/day. The project should therefore combine account-side daily limit configuration with code-side caching and a local daily safety limit. Local tests also showed that Windows can deny `os.replace()` on JSON state files, so cache writes need a direct-write fallback.

### Suggested Action
Keep `OPENWEATHER_ONECALL_DAILY_LIMIT` at 900 by default, clamp it to 1,000 in code, cache One Call responses for at least 30 minutes, and make validation scripts avoid live requests unless explicitly requested.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/weather/weather.py, inkypi-weather/package/README_PACKAGE.md, inkypi-weather/package/tools/check_openweather.py
- Tags: inkypi, openweather, cost-control, cache, raspberry-pi

---

## [LRN-20260524-002] insight

**Logged**: 2026-05-24T02:05:00-07:00
**Priority**: high
**Status**: pending
**Area**: epaper

### Summary
For InkyPi weather packaging, keep PC development dependencies inside the project and verify OpenWeather One Call access before relying on the OpenWeather provider.

### Details
The user's PC could not create a normal Python venv because `ensurepip` hit an AppData permission error. Installing requirements into `package/InkyPi/.pc-packages` with `pip --target` keeps the setup inside the project folder and avoids global changes. InkyPi's Weather plugin reads `OPEN_WEATHER_MAP_SECRET` from `InkyPi/.env`; the file should be UTF-8 without BOM. The OpenWeatherMap provider uses the One Call 3.0 API, which returns 401 unless the key has One Call by Call access. Open-Meteo remains the practical provider until that subscription/access is enabled.

### Suggested Action
Run PC preview through `inkypi-weather/package/run_pc_dev.ps1`, keep the Pi install bundle separate from `.pc-packages`, and use `check_openweather.ps1` before switching the Weather plugin from Open-Meteo to OpenWeatherMap.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/run_pc_dev.ps1, inkypi-weather/package/check_openweather.ps1, inkypi-weather/package/InkyPi/.env
- Tags: inkypi, epaper, openweather, pc-preview, raspberry-pi

---

## [LRN-20260526-006] best_practice

**Logged**: 2026-05-26T14:45:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Steam Daily Art should use frontpage promo categories plus a shorter rotation key instead of a single daily deal.

### Details
The user felt Steam Daily Art updated too slowly. The plugin already used Steam Store `featuredcategories`, but the saved instance was pinned to `sourceCategory: daily_deals`, which only yields one item, and the cache key was date-based. Better defaults are `fresh_frontpage` and `six_hours`, using categories such as spotlights, daily deals, specials, top sellers, and new releases. Existing plugin instances must be updated through InkyPi's `update_plugin_instance` HTTP endpoint because defaults do not override saved settings, and direct writes to `device.json` may require sudo.

### Suggested Action
When changing plugin defaults, check the active `device.json` instance settings and update saved instances through the local app API if needed. For Daily Art, prefer `fresh_frontpage` with `six_hours` or `hourly`; use `every_refresh` only when the user wants maximum churn.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_daily_art/steam_daily_art.py, inkypi-weather/package/InkyPi/src/plugins/steam_daily_art/settings.html
- Tags: inkypi, epaper, steam-daily-art, steam-store, cache, settings

---

## [LRN-20260526-005] constraint

**Logged**: 2026-05-26T00:55:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Steam friend rich-presence detail lines are not available through the current Web API based InkyPi plugin.

### Details
The user asked to add detailed friend activity such as `在房间中：竞技模式` under the game name. That information is Steam Rich Presence. Steamworks exposes it through client APIs such as `GetFriendRichPresence` after `RequestFriendRichPresence`, but the InkyPi plugin currently uses Steam Web API endpoints such as `GetPlayerSummaries`, which provide game ID and game name but not Rich Presence detail strings.

### Suggested Action
Do not promise real detailed friend activity unless a Steam client/Steamworks bridge or another authenticated data source is added. Keep the existing friend widget to avatar, friend ID, game name, and online status from Web API data.

### Metadata
- Source: implementation_research
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py
- Tags: inkypi, epaper, steam-dashboard, steam-api, rich-presence

---

## [LRN-20260526-004] best_practice

**Logged**: 2026-05-26T00:43:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Steam dashboard friend live rows should show friend ID above the live status line.

### Details
The user refined the top-right friend area so each online friend block carries more context: avatar on the left, friend ID/nickname on the first text line, and status dot plus `正在游玩：...` or `在线` on the second line. The two text lines should be treated as one group and vertically centered against the avatar and panel.

### Suggested Action
Keep the friend activity widget as a two-line text group next to each avatar, and calculate row height from both avatar size and text-group height before centering the full list.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py
- Tags: inkypi, epaper, steam-dashboard, friends, layout

---

## [LRN-20260526-003] best_practice

**Logged**: 2026-05-26T00:30:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Center fixed friend-status row groups by total row height inside the Steam profile panel.

### Details
The user corrected the top-right friend-status rows because a fixed top offset made four avatar/text groups sit too low and overlap the panel border. The robust layout is to calculate total group height from row count, avatar size, and row gap, then center that group within the profile panel.

### Suggested Action
For compact repeated widgets in the Steam dashboard, compute group bounds first and center within the containing panel instead of relying on fixed offsets.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py
- Tags: inkypi, epaper, steam-dashboard, layout, alignment

---

## [LRN-20260526-002] best_practice

**Logged**: 2026-05-26T00:20:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Steam dashboard online-friend avatars should include compact live status text when space allows.

### Details
The user requested the top-right friend area to show each online friend's current game to the right of the avatar, or `在线` if not in game, with a live dot before the text. Friend game status comes from `GetPlayerSummaries` fields such as `gameid` and `gameextrainfo`, so cached friend summaries should be refreshed during live status refreshes instead of waiting only for the full cache interval.

### Suggested Action
Keep this area as a compact row list: avatar, status dot, single-line fitted status/game text. Reuse localized Steam game-name resolution for friend `gameid` values.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py
- Tags: inkypi, epaper, steam-dashboard, friends, live-status

---

## [LRN-20260526-001] best_practice

**Logged**: 2026-05-26T00:10:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Steam dashboard ranked rows should render as single-line table rows with font fitting, not wrapped prose.

### Details
The user corrected the `常玩 TOP 3` area because wrapping a long ranked game row made the hours land on a second line. This area reads as a compact table, so each row should stay on one line and reduce font size within a floor before allowing horizontal overflow. This is separate from prose-like areas such as `最近 / 实时`, where complete wrapped text remains acceptable.

### Suggested Action
For future Steam dashboard table/list refinements, use a single-line fitting helper for ranked or metric rows, and reserve wrapping for sentence-like content.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py
- Tags: inkypi, epaper, steam-dashboard, typography, layout

---

## [LRN-20260525-002] best_practice

**Logged**: 2026-05-25T23:58:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For InkyPi Steam dashboard backgrounds, preprocess decorative images into near-black, low-level silhouette assets and keep content panels solid black.

### Details
Directly using a dark wallpaper with continuous gradients can reintroduce visible dithering on the 7.5-inch e-paper display. A safer approach is to isolate only the decorative motif, flatten it into a very small dark range, use pure black as the base, and draw text-heavy panels with opaque black fills.

### Suggested Action
When adding future dashboard backgrounds, generate a dedicated 800x480 asset from the source image, verify it locally, and bump `STEAM_DASHBOARD_STYLE_VERSION` so the device cache invalidates.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/background.png, inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py
- Tags: inkypi, epaper, steam-dashboard, background, dithering

---

## [LRN-20260524-001] correction

**Logged**: 2026-05-24T01:15:15-07:00
**Priority**: high
**Status**: pending
**Area**: frontend

### Summary
For the 7.5-inch e-paper dashboard, do not adapt the 10.85-inch layout by non-uniform compression.

### Details
The first pass used a 1360x480 to 800x480 squash mode as a temporary fit, but the user corrected that this is not acceptable. The correct approach is a native 800x480 layout that preserves icon proportions, circular widgets, and element aspect ratios. PC preview must also be one-to-one so layout can be inspected without repeatedly moving the SD card.

### Suggested Action
Default to `layout` mode, keep any squash/fit/crop modes only as diagnostics, and use the live 1:1 preview server while iterating on UI.

### Metadata
- Source: user_feedback
- Related Files: dashboard-7in5/epaper-dashboard-7in5/render_7in5_layout.py, dashboard-7in5/epaper-dashboard-7in5/preview_server.py
- Tags: epaper, layout, preview, raspberry-pi

---

## [LRN-20260526-002] best_practice

**Logged**: 2026-05-26T16:45:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For InkyPi plugin localization, render-test CJK text with a bundled CJK font and pull proof images over HTTP when static directories are not shell-readable.

### Details
Adding Simplified Chinese to `epaper_pet` required more than translating strings: the renderer needed a CJK-capable font fallback (`LXGW WenKai`) and character-based wrapping because Chinese text has no spaces. On the Pi, shell/scp access to `InkyPi/src/static/images` may fail with permission or "no such file" even when the web app serves the image successfully.

### Suggested Action
For future localized e-paper plugins, add a language setting, localize rendered labels/messages at draw time, use bundled CJK fonts, test an actual 800x480 preview, then verify Pi output through `/plugin_instance_image/...` or `/static/images/current_image.png` rather than direct filesystem copy.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py, inkypi-weather/package/InkyPi/src/plugins/epaper_pet/settings.html
- Tags: inkypi, epaper, localization, chinese, fonts, qa

---

## [LRN-20260526-003] best_practice

**Logged**: 2026-05-26T16:58:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
For InkyPi pet AI dialogue, default to free-provider routing and never auto-consume OpenAI credits.

### Details
The user explicitly corrected AI usage toward cost control and asked to stay within free usage as much as possible. The pet plugin should default to `ai_provider=free_auto`, prefer a configured `GROQ_API_KEY`, and avoid calling `OPEN_AI_SECRET` unless the user explicitly selects OpenAI. If no free provider key exists, keep the pet alive with local fallback dialogue and mark status such as `missing_free_provider`.

### Suggested Action
For future AI-enabled e-paper features, expose provider and daily-limit controls, keep paid providers opt-in, and verify state/log evidence that paid OpenAI was not called when free provider keys are missing.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py, inkypi-weather/package/InkyPi/src/plugins/epaper_pet/settings.html
- Tags: inkypi, epaper, ai, cost-control, openai, groq

---
---
### Summary
InkyPi news/frontpage plugins on the current Raspberry Pi cannot depend on Chromium screenshots alone; `chromium-headless-shell` may be installed but crash immediately with `Illegal instruction`.

### Details
During the DailyDoseOfDay newspaper rotation work on 2026-05-26, the deployed Pi at `192.168.1.183` had `/usr/bin/chromium-headless-shell`, but running `chromium-headless-shell --version` failed with `Illegal instruction`. The newspaper plugin therefore needed a non-browser fallback: fetch homepage HTML with `requests`, extract likely headline text, and render a black/white PIL page. The fallback was verified through `/display_plugin_instance`, rotating from BBC News to CNN and updating the Waveshare display successfully. Service restarts can also take roughly 2m20s before port 80 responds because display initialization is slow, so avoid rapid restart loops while verifying.

### Suggested Action
For future InkyPi plugins that show live web/news content, keep a lightweight HTML/PIL fallback path and verify via the actual `/display_plugin_instance` route plus logs/current display output. Treat Chromium screenshot support on this Pi as optional, not guaranteed.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/newspaper/newspaper.py`, `inkypi-weather/package/InkyPi/src/refresh_task.py`
- Tags: inkypi, epaper, newspaper, chromium, fallback, raspberry-pi

---
### Summary
When an InkyPi news/frontpage source cannot be captured as a browser screenshot and falls back to extracted text, the fallback must support simplified Chinese output.

### Details
The DailyDoseOfDay newspaper rotation includes Chinese sources such as CCTV News and Xinhua. On the current Pi, Chromium screenshots fail, so those sources commonly render through the HTML headline fallback. That fallback needs to decode Chinese pages robustly, repair common UTF-8 mojibake when possible, convert traditional Chinese text to simplified Chinese where supported, and render with project-bundled CJK fonts so simplified Chinese does not appear as tofu boxes. This was implemented and verified with unit tests plus a real `/display_plugin_instance` refresh that selected `CCTV News` after screenshot failure.

### Suggested Action
For future e-paper news/text fallbacks, treat simplified Chinese support as part of the acceptance criteria: correct decoding, simplified normalization, CJK-capable font selection, and a real-device refresh/log verification for at least one mainland Chinese source.

### Metadata
- Source: user correction and implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/newspaper/newspaper.py`, `inkypi-weather/package/InkyPi/tests/test_newspaper_rotation.py`
- Tags: inkypi, epaper, newspaper, simplified-chinese, fallback, cjk-fonts

---
### Summary
When the InkyPi web app is busy with a display refresh, `/update_plugin_instance/<name>` may time out; direct `device.json` edits plus service restart can be the reliable fallback.

### Details
While configuring the `DailyDoseOfDay` `ChinaDaily` newspaper instance on 2026-05-26, the HTTP update route timed out and did not persist the new media rotation settings. Logs showed the service was occupied with another manual display update, and the web UI was temporarily unresponsive. A structured JSON edit to `InkyPi/src/config/device.json`, preserving existing plugin fields and backing up the file first, followed by `sudo -n systemctl restart inkypi`, successfully loaded the settings. The service still needed roughly 2-3 minutes before port 80 responded.

### Suggested Action
For future live Pi configuration changes, prefer the app route when responsive; if it times out during display work, inspect logs/config, back up `device.json`, make a minimal structured JSON edit, restart InkyPi, wait for HTTP readiness, then verify with `/display_plugin_instance` and journal logs.

### Metadata
- Source: implementation
- Related Files: `InkyPi/src/config/device.json`, `inkypi-weather/package/InkyPi/src/blueprints/plugin.py`
- Tags: inkypi, epaper, configuration, device-json, timeout, restart

---
### Summary
For the InkyPi newspaper rotation, prefer verified Freedom Forum `newspaper` sources over URL sources when the user wants the original China Daily-style front page look.

### Details
The HTML headline fallback worked technically, but the user found it visually worse than the original China Daily front page. Because this Pi cannot run Chromium screenshots reliably, URL sources such as BBC, CNN, CCTV, and Xinhua should be removed when the goal is actual newspaper/front-page imagery. On 2026-05-26, the `DailyDoseOfDay` `ChinaDaily` instance was reconfigured to use only verified Freedom Forum covers: China Daily, People's Daily, NYT, WSJ, Washington Post, USA Today, Los Angeles Times, San Francisco Chronicle, Chicago Tribune, Boston Globe, and Seattle Times. Daily Mail was tested and excluded because the current cover URL returned 404.

### Suggested Action
Before adding sources to this newspaper rotation, test each Freedom Forum slug from the Pi and include only sources that return a real JPEG. Put China Daily first when restoring the preferred visual baseline.

### Metadata
- Source: user correction and implementation
- Related Files: `InkyPi/src/config/device.json`, `inkypi-weather/package/InkyPi/src/plugins/newspaper/constants.py`
- Tags: inkypi, epaper, newspaper, frontpage, freedom-forum, source-selection

---
### Summary
The current deployed InkyPi board is Raspberry Pi Zero W Rev 1.1, not Zero 2; migration expectations should account for armv6 and 512MB constraints.

### Details
On 2026-05-27, the active Pi at `192.168.1.183` reported `Raspberry Pi Zero W Rev 1.1`, `armv6l`, 32-bit userspace, and roughly 427MiB RAM visible to Linux. This explains failures such as Chromium `Illegal instruction` and limited headroom for browser-based screenshot plugins. A future Zero 2 W/WH migration should improve CPU compatibility and multitasking, but RAM remains 512MB-class and e-paper refresh speed is unchanged.

### Suggested Action
When planning Zero 2 migration features, separate CPU/architecture wins from memory/display limits. Retest Chromium/web screenshots on the new board before adding URL screenshot sources back into production rotation, and keep verified Freedom Forum newspaper covers as the stable baseline until then.

### Metadata
- Source: implementation_research
- Related Files: `docs/new-board-migration-baseline.md`, `inkypi-weather/package/InkyPi/src/plugins/newspaper/newspaper.py`
- Tags: inkypi, raspberry-pi-zero-w, zero-2, migration, chromium, hardware

---
### Summary
Bambu Monitor can read A1 AMS Lite slot material type and color from the local MQTT report; remaining percentage may be invalid.

### Details
On the deployed InkyPi at `192.168.1.183`, the Bambu Monitor cache for printer `192.168.1.137` showed four A1 AMS Lite slots with normalized fields: slot id, active flag, material type, RGBA hex color, and `remain`. The current A1 report provided valid material types and colors for slots `0-0` through `0-3`, but `remain` was `-1` for every slot, so remaining percentage should be treated as unavailable rather than rendered as a real value.

### Suggested Action
For future Bambu Monitor UI work, display AMS Lite material type and color swatches by slot, highlight the active slot, and hide or label remaining percentage as unknown when `remain` is negative. If brand/sub-brand detail is needed, capture the raw MQTT `ams` payload rather than relying only on the normalized cache.

### Metadata
- Source: implementation_research
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py`
- Tags: inkypi, bambu, ams-lite, mqtt, material, epaper

---
### Summary
For the user's Bambu A1 camera in Bambu Monitor, top-align the live camera crop instead of centering it.

### Details
The A1 camera on the user's setup points downward enough that centered cropping wastes important top-frame content. The user explicitly requested the live view to start from the top and allow the bottom to be cropped. `ImageOps.fit` should therefore use a vertical centering value of `0.0` for real camera frames. Waiting/fallback artwork can remain centered because it is a designed placeholder rather than a camera feed.

### Suggested Action
When adjusting Bambu Monitor camera layout, keep `CAMERA_FRAME_CENTERING = (0.5, 0.0)` for live frames unless the physical camera position changes. Verify using `/api/current_image` after a real `display_plugin_instance` refresh because small crop changes are only meaningful on the rendered e-paper layout.

### Metadata
- Source: user correction and implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py`
- Tags: inkypi, bambu, a1, camera, crop, epaper

---
### Summary
In Bambu Monitor AMS cards, 1-bit filament color chip textures must be clipped inside the chip bounds.

### Details
The first AMS color-chip implementation used diagonal hatch lines that extended beyond the 11x11 chip rectangle, visibly spilling into the RED label area on the e-paper preview. Replacing the hatch with short horizontal lines drawn from `box[0] + 2` to `box[2] - 2` and within `box[1] + 2` to `box[3] - 2` fixed the overflow while preserving a monochrome texture cue.

### Suggested Action
For future tiny 1-bit UI marks on the 800x480 InkyPi layout, avoid unconstrained diagonal strokes and verify at actual rendered resolution. Keep decorative chip/pattern pixels at least 2 px inside the outline unless a clipped mask is used.

### Metadata
- Source: user correction and implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py`
- Tags: inkypi, bambu, ams, ui, epaper, texture

---
### Summary
For Epaper Pet ambient awareness, use a shared context cache written by producer plugins instead of scraping private plugin caches directly.

### Details
On 2026-05-26, Epaper Pet was extended to read current weather/news/Steam context. The stable pattern is `plugins/context_cache.py` with small per-plugin JSON summaries under `.context_cache`, while `weather`, `mini_weather`, `daily_ai_news`, `steam_daily_art`, and `steam_profile_dashboard` write the cache when they successfully generate. The `/plugin_instance_image/...` route only serves an existing image and does not regenerate plugin output, so it will not update producer context by itself. A Windows smoke test also showed `os.replace(tmp, target)` can fail with `WinError 5`; cache writers need a direct-write fallback. Avoid naming new helpers `_clip_text` inside `epaper_pet.py` because the renderer already owns that method name.

### Suggested Action
When adding more live context sources for the pet, hook the source plugin's successful `generate_image` path and write a compact public context payload with a TTL. Use public context files for AI prompts and keep private caches as implementation details. To prime Pi context without refreshing the e-paper display, read existing private caches once and write them through `write_context`.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/context_cache.py`, `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `inkypi-weather/package/InkyPi/src/plugins/weather/weather.py`, `inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py`, `inkypi-weather/package/InkyPi/src/plugins/steam_daily_art/steam_daily_art.py`, `inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Tags: inkypi, epaper-pet, context-cache, ai, plugin-cache, windows

---
### Summary
Epaper Pet must use OpenAI only as a paid fallback after the free Groq path hits quota or rate limits.

### Details
The user explicitly allowed OpenAI usage only after free AI is used up so they can monitor billing. In `free_auto`, keep Groq as the first provider. Do not use OpenAI just because Groq has a bad key or a generic transient failure. Only fall back when Groq returns quota/rate-limit style failure such as HTTP 429, rate limit, quota, exceeded, tokens per day, or requests per day. Record the actual provider in pet state, including `ai_message_provider`, `ai_message_fallback_from`, `ai_message_fallback_reason`, and provider usage counts when available.

### Suggested Action
When changing Epaper Pet AI routing, preserve this order: Groq first, OpenAI only after free quota/rate limit, explicit `openai` provider only when the user chooses paid mode. Run `tools/smoke_epaper_pet.py`; it includes a fake Groq 429 case that must call OpenAI exactly once.

### Metadata
- Source: user correction and implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/settings.html`, `tools/smoke_epaper_pet.py`
- Tags: inkypi, epaper-pet, ai-cost, groq, openai, fallback

---
### Summary
Epaper Pet activity labels should describe pet behavior, not system mechanics.

### Details
The user rejected labels like "刷新状态"/"刷新报告" because they make the pet feel mechanical. Activity labels should read as what the pet is doing in-world, such as "偷听世界", "微型跳舞", "像素巡逻", or similar compact behavior phrases. Technical causes like refresh, cache, render, status, API, or report should stay internal unless they are translated into a pet-like action.

### Suggested Action
When adding or renaming Epaper Pet events, review the visible `activity` string from the user's perspective. Prefer lively, small, e-paper-friendly behaviors over implementation words. Keep backward-compatible mappings for old activity ids so existing saved pet state displays naturally after deploy.

### Metadata
- Source: user correction and implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`
- Tags: inkypi, epaper-pet, activity-labels, localization, ux

---
### Summary
In EpaperSystem on Windows, use fallback search and no-bytecode validation when standard tools hit local permission issues.

### Details
During Simple Calendar cleanup, `rg.exe` failed with `Access is denied` even for basic searches, while PowerShell `Select-String` and `Get-ChildItem` worked. Direct `python -m py_compile` also failed with `WinError 5` when replacing a file under a plugin `__pycache__` directory, but `python -B -c "compile(open(...).read(), path, 'exec')"` validated syntax without writing bytecode, and direct plugin render smoke tests still worked.

### Suggested Action
For future EpaperSystem work, try `rg` first as usual, then immediately fall back to PowerShell search if it is blocked. For Python validation inside `inkypi-weather/package/InkyPi/src/plugins`, prefer no-bytecode compile/import/render checks before touching `__pycache__` or treating pyc write failures as source errors.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`
- Tags: inkypi, windows, tooling, validation, pycache

---
### Summary
When the user reports repeated InkyPi pages, distinguish playlist instance rotation from plugin-internal content rotation.

### Details
The main playlist no-repeat queue in `model.py` operates on plugin instance keys, not on the visual content produced inside a selected plugin. Automatic playlist rotation should show every active plugin instance once before repeating, but manual `/update_now` and `/display_plugin_instance` calls bypass the automatic `get_next_plugin()` selection. Rotating plugins such as `newspaper`, `steam_daily_art`, and `image_upload` have their own internal source/image selection state, so a repeated-looking screen can come from plugin-internal fallback or cache behavior even when the main plugin instance queue is working.

### Suggested Action
For duplicate-page reports, inspect logs for `Determined next plugin` first. If the plugin instance names repeat before the queue is exhausted, debug `plugin_rotation_queue` in `device.json` and deployment freshness. If instance names differ or the same rotating plugin was manually refreshed, inspect that plugin's internal rotation state and failures.

### Metadata
- Source: implementation_analysis
- Related Files: `inkypi-weather/package/InkyPi/src/model.py`, `inkypi-weather/package/InkyPi/src/refresh_task.py`, `inkypi-weather/package/InkyPi/tests/test_model.py`
- Tags: inkypi, playlist, no-repeat, rotation, diagnostics

---
### Summary
Epaper Pet should show compact AI provider and daily refresh telemetry on the rendered screen.

### Details
The user wants to track which AI engine refreshed the pet line and how many AI refreshes have been used. Keep this as a small bottom-right on-screen footer such as `AI Groq 9/24` or `AI OpenAI 1/24 <- Groq`. Use existing pet state fields (`ai_usage`, `ai_message_provider`, and fallback metadata) instead of adding another counter.

### Suggested Action
When changing Epaper Pet AI or render layout, preserve the footer and keep it short enough to avoid colliding with the journal line. Validate with `tools/smoke_epaper_pet.py` so Groq and OpenAI fallback telemetry remain visible and accurate.

### Metadata
- Source: user request and implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `tools/smoke_epaper_pet.py`
- Tags: inkypi, epaper-pet, ai-telemetry, groq, openai, ux

---
### Summary
Epaper Pet should feel alive through local survival systems first, with AI acting as a narrator.

### Details
The user wants the pet to behave like a self-sustaining Tamagotchi, not just a random quote generator. The stable pattern is to keep life mechanics deterministic and local: daily plans, time-band routines, survival instincts, level-based hunting, prey-size unlocks, and food reserve. AI dialogue should read these state facts and phrase them naturally, while Groq/OpenAI usage remains controlled by the existing budget rules.

### Suggested Action
When extending Epaper Pet, add mechanics that change state before adding AI prompt text. Level progression should affect real survival outcomes such as huntable prey size and reserve capacity. Keep expressive faces non-sexual and pet-like; use belly-up or zoomies framing for active states.

### Metadata
- Source: user direction and implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `tools/smoke_epaper_pet.py`
- Tags: inkypi, epaper-pet, tamagotchi, survival-loop, ai-narration, level-system

---
### Summary
On this Windows EpaperSystem shell, `curl.exe` may be blocked by proxy env vars while PowerShell HTTP works.

### Details
During live Pi verification, `curl.exe` returned HTTP `000` because it honored `http_proxy=http://127.0.0.1:9`, even though `Invoke-WebRequest -UseBasicParsing`, `Test-NetConnection`, and SSH-side `curl http://127.0.0.1/playlist` confirmed the Pi service was reachable. Do not treat local curl `000` as device failure until proxy/env behavior is checked.

### Suggested Action
For EpaperPodBeta live checks from Windows, prefer `Invoke-WebRequest -UseBasicParsing` for HTTP status, or run `curl` on the Pi over SSH. If using local `curl.exe`, clear/override proxy env vars first.

### Metadata
- Source: implementation
- Related Files: none
- Tags: inkypi, windows, curl, proxy, live-verification

---
### Summary
BacktotheDate should rotate portrait posters instead of tiling multiple portrait images.

### Details
The user tested the three-column portrait mosaic on a landscape e-paper device and found the effect uncomfortable. The preferred behavior is: landscape posters display as one full poster with same-image blurred background fill; portrait posters rotate 90 degrees counterclockwise first, then display as one full image with the same blurred background treatment.

### Suggested Action
For future BacktotheDate layout changes, keep `fitMode=rotate_portrait` as the default/preferred mode. Do not return to multi-poster portrait tiling unless the user explicitly asks for it again. Preserve full clear-image containment; only the background layer should be cropped/blurred.

### Metadata
- Source: user correction and implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/backtothedate/backtothedate.py`, `inkypi-weather/package/InkyPi/src/plugins/backtothedate/settings.html`
- Tags: inkypi, backtothedate, e-paper, layout, portrait-rotation, blur-background

---
### Summary
For EpaperSystem GitHub backup, avoid `gh auth login` keyring persistence on this Windows machine.

### Details
`gh auth login` successfully completed browser/device authorization but then failed while moving the active token into the Windows keyring with "Not enough memory resources are available to process this command." A direct GitHub OAuth device flow using the GitHub CLI OAuth client, keeping the token only in the current PowerShell process memory, worked for creating the private repository and pushing the backup. The local project also had a broken partial `.git` directory with only `config.lock`, `hooks`, `info`, and `description`; backing it up and moving the valid staging `.git` back to the project root restored normal Git status.

### Suggested Action
For future private backups from this checkout, first verify whether `gh auth status` actually succeeds. If it fails at keyring persistence, use a temporary direct device-flow publish script that does not write the token to disk, push with a one-time `http.extraheader`, then delete the script/log. Keep the remote URL token-free and verify with the GitHub connector after push.

### Metadata
- Source: implementation
- Related Files: `.gitignore`, `.gitattributes`
- Tags: github, backup, gh-cli, keyring, device-flow, windows, git

---
### Summary
EpaperPod can be online with `inkypi` active while the local Web server is hung; use a user cron watchdog when root install is unavailable.

### Details
After moving the device closer to the router, the Pi returned to `192.168.1.183` and SSH worked, but both remote `http://192.168.1.183/settings` and Pi-local `http://127.0.0.1/settings` timed out even though `inkypi.service` was `active` and port 80 was listening. Restarting `inkypi` restored the page. The user account did not have passwordless sudo for writing `/etc` or restarting NetworkManager, but sudoers allowed exact commands like `/usr/bin/systemctl restart inkypi`, so a user crontab watchdog was installed at `/home/feeengyuuu/bin/epaperpod-user-watchdog.sh` to check local HTTP every 2 minutes after a 5-minute startup grace and restart InkyPi after repeated failures.

### Suggested Action
When this device is reachable by SSH but the settings page hangs, test `curl -I --max-time 8 http://127.0.0.1/settings` on the Pi. If local curl hangs while `systemctl is-active inkypi` is active, restart InkyPi and inspect `/home/feeengyuuu/.cache/epaperpod-watchdog/watchdog.log`. For stronger Wi-Fi recovery, root access or a sudoers rule is still needed to install a system timer, disable NetworkManager Wi-Fi powersave, or restart NetworkManager.

### Metadata
- Source: implementation
- Related Files: `.tmp/epaperpod-user-watchdog.sh`
- Tags: inkypi, epaperpod, watchdog, cron, wifi, systemd, raspberry-pi

---
### Summary
EpaperPod in the user's room can fail to rejoin the LAN even after a power reboot.

### Details
After the user rebooted the device in the weak-coverage room, local checks from `192.168.1.90` still could not reach the previous Pi address `192.168.1.183`; `EpaperPodBeta.local` and `EpaperPodBeta.attlocal.net` did not resolve. A full local `192.168.1.0/24` check for ports `22` and `80` found only the Windows host, router, printer, and AT&T/AirTies extender-style web endpoints; no SSH endpoint for the Pi appeared. This supports the diagnosis that the Pi Zero W is not joining the accessible LAN from that location, rather than merely having InkyPi's web app hung.

### Suggested Action
When this happens, first move the Pi or extender to improve 2.4GHz signal and confirm the device appears in the AT&T client list before attempting SSH/deploy. Once reachable, install stronger Wi-Fi recovery such as disabling power save and adding a network watchdog; until then code deployment cannot proceed from Codex.

### Metadata
- Source: live_network_check
- Related Files: `tools/epaperpod-test-key.ps1`
- Tags: inkypi, epaperpod, wifi, extender, raspberry-pi-zero-w, deployment

---
### Summary
For EpaperPod plugin hotfixes, prefer direct SCP upload plus remote unzip over a temporary local HTTP download server.

### Details
During the Simple Calendar clean-background deploy, starting a local Python HTTP server from PowerShell failed once with a duplicate `Path`/`PATH` environment-key error and then hung without opening port `8766`. Direct `scp` to `/home/feeengyuuu/inkypi-weather-pi-package-20260524-3`, followed by `unzip -o` and `sudo -n systemctl restart inkypi`, completed cleanly. The Web UI may still take multiple minutes to become ready after service restart because display initialization is slow, so verify both `systemctl is-active inkypi` and a local/remote HTTP endpoint before declaring the deployment done.

### Suggested Action
Use `tools/epaperpod-test-key.ps1` to confirm SSH, then upload deploy zips with `C:\Windows\System32\OpenSSH\scp.exe` and apply them over SSH. Trigger a plugin refresh through `/display_plugin_instance` with real JSON serialization rather than hand-escaped command-line JSON, then fetch `/api/current_image` to verify the actual rendered screen.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/dist/simple-calendar-clean-20260527.zip`, `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`
- Tags: inkypi, epaperpod, deployment, scp, simple-calendar, windows-powershell

---
### Summary
When running SSH commands from PowerShell, avoid unescaped `$()` in double-quoted remote commands.

### Details
PowerShell expands `$(...)` locally before launching `ssh.exe`, so a remote command like `echo HOST=$(hostname)` inside a double-quoted command string prints the Windows host name instead of the Pi host name. This can make remote diagnostics look inconsistent even though the rest of the SSH command runs on the Pi.

### Suggested Action
Use single-quoted PowerShell strings for remote shell snippets when possible, or escape PowerShell `$` characters before sending commands through `ssh.exe`. For important diagnostics, prefer explicit remote checks that do not rely on shell substitutions in the outer PowerShell string.

### Metadata
- Source: implementation
- Related Files: `tools/epaperpod-test-key.ps1`
- Tags: windows-powershell, ssh, quoting, epaperpod, diagnostics

---
### Summary
EpaperPod can persist Wi-Fi powersave off through the root-run InkyPi startup path when normal sudo is not passwordless.

### Details
The `feeengyuuu` account can list full sudo privileges but requires a password for arbitrary commands; only selected `systemctl inkypi` commands are passwordless. Since `/etc/systemd/system/inkypi.service` runs `/usr/local/bin/inkypi run` as root, adding a production startup hook in `src/inkypi.py` to call `/usr/sbin/iw dev wlan0 set power_save off` successfully turned `wlan0` from `Power save: on` to `Power save: off` after `sudo -n systemctl restart inkypi`. The hook must run before `DisplayManager` initialization because Waveshare display startup can take about 2 minutes; placing it later delays the Wi-Fi fix and makes verification misleading.

### Suggested Action
For this device, keep Wi-Fi powersave handling in an early, non-fatal startup helper such as `src/utils/network_utils.py`. Verify with `/usr/sbin/iw dev wlan0 get power_save`, then wait for `/playlist` to return HTTP 200 because service active status appears before Waitress is ready.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/inkypi.py`, `inkypi-weather/package/InkyPi/src/utils/network_utils.py`
- Tags: inkypi, epaperpod, wifi, powersave, systemd, raspberry-pi, deployment

---
### Summary
EpaperPod Wi-Fi recovery should be failure-triggered, not continuous scanning, to protect normal display operation.

### Details
For the user's "keep searching and reconnect after disconnect" requirement, the safer implementation is a root-run InkyPi background watchdog that checks `wlan0`, the default gateway, and NetworkManager state on an interval. It should keep powersave off, but only run `nmcli radio wifi on`, `nmcli device wifi rescan ifname wlan0`, `nmcli device connect wlan0`, and known connection activation after consecutive connectivity failures. This avoids constant scans while the display is already connected, and keeps the e-paper refresh path independent from network recovery.

### Suggested Action
Keep the watchdog lightweight and non-fatal: no reboot, no display operations, no forced disconnect while healthy. Verify deployment with `journalctl -u inkypi` for `Starting Wi-Fi reconnect watchdog on wlan0`, `/usr/sbin/iw dev wlan0 get power_save`, `nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status`, gateway ping, and `/playlist` HTTP 200.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/utils/network_utils.py`, `inkypi-weather/package/InkyPi/src/inkypi.py`
- Tags: inkypi, epaperpod, wifi, reconnect, watchdog, networkmanager

---
### Summary
Use `python -m ast ... >/dev/null` for Pi-side syntax checks when `__pycache__` permissions are root-owned.

### Details
On EpaperPod, running `python -m py_compile` as `feeengyuuu` failed after direct source deploy because earlier root-run service imports had created `__pycache__` files that the user account could not overwrite. The source was valid; the failure was only bytecode write permission. `python -m ast /path/to/file.py >/dev/null` parsed the files without writing `.pyc` and worked for deployed runtime files before restarting/validating the service.

### Suggested Action
For Pi hot deploy validation, use local tests first, then remote `python -m ast <file> >/dev/null` on each changed Python file. Treat `py_compile` permission failures under `src/**/__pycache__` as an environment artifact unless the AST parse also fails.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/utils/theme_utils.py`, `inkypi-weather/package/InkyPi/src/refresh_task.py`
- Tags: inkypi, epaperpod, deployment, python, permissions, pycache, validation

---
### Summary
Run focused pytest checks outside the sandbox when this Windows workspace denies pytest temp directory access.

### Details
During the Daily Word & Poem plugin work, the project-local pytest package existed under `.pc-packages`, but ordinary sandboxed pytest runs failed while creating or scanning temporary directories such as `C:\Users\super\AppData\Local\Temp\pytest-of-LocalTest`, `tmp\pytest-dwp-*`, and `C:\tmp\epaper-pytest-*`. The tests themselves were valid and passed once the same focused pytest command was run with approval outside the sandbox and with `PYTHONPATH=.pc-packages`.

### Suggested Action
For this checkout, use `.venv-test\Scripts\python.exe -m pytest ...` with `PYTHONPATH` pointed at `.pc-packages`, `PYTHONDONTWRITEBYTECODE=1`, and a scoped `--basetemp` path. If sandboxed runs fail with `PermissionError: [WinError 5]` before test code executes, rerun the exact focused test with escalation rather than debugging the tests.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/tests/test_daily_word_poem.py`, `.pc-packages`
- Tags: inkypi, pytest, windows, sandbox, tempdir, validation

---
### Summary
Verify deployed InkyPi current screen images through HTTP when static image directories are not readable over SSH.

### Details
On EpaperPod, `display.display_manager` can log that it saved `src/static/images/current_image.png`, while the `feeengyuuu` SSH account still gets `Permission denied` or `No such file` when trying to list or scp that static directory directly. The same image is available through the running Flask/Waitress static route after `/playlist` returns HTTP 200.

### Suggested Action
After a `display_plugin_instance` smoke test, fetch `http://127.0.0.1/static/images/current_image.png` on the Pi into `/tmp`, then scp the `/tmp` copy back for visual QA. For LiveRadar, the current site logo is exposed by the site HTML as `apple-touch-icon.png`; package a downscaled local copy with the plugin instead of relying on runtime web access for the title logo.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py`, `inkypi-weather/package/InkyPi/src/plugins/live_radar/liveradar_logo.png`
- Tags: inkypi, epaperpod, liveradar, deployment, static-images, visual-qa
## [LRN-20260527-054] best_practice

**Logged**: 2026-05-27T20:45:00-07:00
**Priority**: medium
**Status**: active
**Area**: tests

### Summary
For InkyPi pytest runs on this Windows workspace, load project dependencies from `.pc-packages` and rerun outside the sandbox if pytest temp directories hit `Access denied`.

### Details
The local virtual environments may not have `pytest` or Flask installed, while `.pc-packages` contains the bundled project dependencies. Running pytest inside the sandbox can fail during `tmp_path` setup or cleanup with Windows `PermissionError` on pytest temporary directories even when `--basetemp` points inside the workspace.

### Suggested Action
Run targeted tests with `PYTHONPATH=.../InkyPi/.pc-packages` and set `INKYPI_CONTEXT_CACHE_DIR` to a project-local temp folder. If pytest fails only on temp-directory permission errors, rerun the same command outside the sandbox rather than treating it as a code failure.

### Metadata
- Source: verification
- Related Files: `inkypi-weather/package/InkyPi/pytest.ini`, `inkypi-weather/package/InkyPi/.pc-packages/`
- See Also: LRN-20260527-052
- Tags: inkypi, pytest, windows, sandbox, pc-packages

---
## [LRN-20260527-055] best_practice

**Logged**: 2026-05-27T22:05:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper_pet

### Summary
For `epaper_pet` behavior changes, verify autonomous state transitions from the pet state cache, not only from `current_image.png`.

### Details
After triggering `Robot`, the pet correctly entered `activity=hunting`, restored food, and persisted `last_hunt`, but a subsequent playlist refresh quickly overwrote `static/images/current_image.png` with another plugin. The durable proof of the behavior was the pet cache file plus `refresh_info` and journal logs, while the current screen image only proves the last completed display update.

### Suggested Action
When validating pet autonomy, inspect `plugins/epaper_pet/cache/pets/<pet_id>.json` for `activity`, `message`, `stats`, and any new behavior-specific fields. Use `display_plugin_instance` for live display proof, but expect the active playlist to rotate afterward and capture the robot image immediately if visual evidence matters.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`
- Tags: inkypi, epaperpod, epaper_pet, deployment, validation, playlist-rotation

---
## [LRN-20260528-001] correction

**Logged**: 2026-05-28T01:03:45-07:00
**Priority**: high
**Status**: pending
**Area**: docs

### Summary
Commercial EpaperSystem promotional images must use img-2 Taobao-style bases plus real 800x480 horizontal project screenshots for the device screen.

### Details
The user corrected an image-generation workflow that first allowed img-2 to invent screen UI, then overcorrected into local-only mockups. For sale-facing product imagery, the desired workflow is still img-2 generated promotional imagery, but the screen content must be actual project output, not generated widgets or fictional layouts. The requested visual language is mainland China Taobao ecommerce style. The correct workflow is to generate Taobao-style scene/device/packaging bases with a blank green screen placeholder, then composite a validated real 800x480 landscape screenshot into the display and add exact Chinese sales copy through code.

### Suggested Action
For future product images in this repo, keep actual screen captures in `marketing_assets/source_screens/`, validate dimensions, use img-2 for Taobao-style base imagery, and use deterministic compositing for the device screen and Chinese copy. Prompt img-2 for a blank chroma-green screen only and never rely on it to render the UI or final text.

### Metadata
- Source: user_feedback
- Related Files: `marketing_assets/README.md`, `tools/create_marketing_assets.py`, `tools/composite_taobao_img2_assets.py`
- Tags: epaper, marketing, imagegen, img-2, screenshots, commercial, taobao

---
## [LRN-20260528-002] best_practice

**Logged**: 2026-05-28T15:50:00-07:00
**Priority**: medium
**Status**: active
**Area**: deployment

### Summary
Verify zip entry paths before deploying individual InkyPi plugin files from Windows.

### Details
PowerShell `Compress-Archive` with multiple explicit file paths can flatten entries to bare filenames. On EpaperPod this extracted `daily_word_poem.py`, `settings.html`, and `plugin-info.json` into the package root instead of overwriting `InkyPi/src/plugins/daily_word_poem/`, so the restart would not have picked up the intended plugin changes.

### Suggested Action
For single-plugin deployments, stage files under a temporary `InkyPi/src/plugins/<plugin_id>/` directory, compress the staging directory contents, and inspect zip entries before SCP. Treat `unzip` path-separator warnings on Linux as a reason to run a separate post-extract confirmation before restarting.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/daily_word_poem/daily_word_poem.py`
- Tags: inkypi, epaperpod, deployment, powershell, zip

---
## [LRN-20260528-003] best_practice

**Logged**: 2026-05-28T15:50:00-07:00
**Priority**: high
**Status**: active
**Area**: daily_word_poem

### Summary
Use Wikiquote official date pages as a fallback when the Toolforge Quote of the Day API returns 404.

### Details
The Toolforge `wq-quote-of-the-day-parser` endpoint is documented and useful, but it returned `404` for `2026-05-28` and for `/api/quote_of_the_day` during deployment. Wikiquote's official `Wikiquote:Quote_of_the_day/May_28` raw page was reachable and contained the QOTD text and author, allowing the Daily Word plugin to keep using Wikiquote instead of dropping to local golden sentences.

### Suggested Action
For daily quote fetches, try the Toolforge date endpoint, then the Toolforge today endpoint, then `https://en.wikiquote.org/w/index.php?title=Wikiquote:Quote_of_the_day/<Month>_<day>&action=raw`. Bump the plugin cache schema when changing fetch fallback behavior so same-day failed-cache payloads are refreshed immediately.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/daily_word_poem/daily_word_poem.py`, `inkypi-weather/package/InkyPi/tests/test_daily_word_poem.py`
- Tags: inkypi, daily_word_poem, wikiquote, toolforge, cache

---
## [LRN-20260528-106] correction

**Logged**: 2026-05-28T19:01:10-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Keep Bambu Monitor live camera frames unprocessed in the rendered preview.

### Details
The user reported that the Bambu Monitor real-time camera screenshot looked too high contrast and asked to restore the original image directly instead of processing it. The live camera frame should be cached as the original JPEG bytes and rendered with only bounded crop/resize for layout. Avoid plugin-local grayscale conversion, autocontrast, manual thresholding, and whole-plugin thresholding when that would alter the camera image.

### Suggested Action
For future Bambu Monitor camera work, preserve the raw camera frame through cache and panel rendering. If a display-specific black/white conversion is needed, leave it to the display pipeline rather than applying camera-specific contrast or threshold transforms inside `bambu_monitor.py`.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py`, `inkypi-weather/package/InkyPi/tests/test_bambu_monitor.py`
- Tags: inkypi, bambu-monitor, camera, original-media, contrast, epaper

---
## [LRN-20260528-109] best_practice

**Logged**: 2026-05-28T20:49:00-07:00
**Priority**: medium
**Status**: active
**Area**: deployment

### Summary
Package EpaperPod plugin zips with explicit entry names instead of wildcard compression.

### Details
PowerShell `Compress-Archive -Path InkyPi\src\plugins\epaper_pet\*` produced a bad deployment zip: it included `__pycache__` artifacts and lost the required `InkyPi/src/...` prefix. A previously opened zip reader can also leave a package temporarily undeletable in the same workflow. Creating a fresh package with explicit `System.IO.Compression` entries and disposing both writer and reader produced the expected five runtime files with Unix-style paths.

### Suggested Action
For future EpaperPod plugin deployments, use a new package filename or ensure readers are disposed before overwrite, and add each runtime file explicitly as `InkyPi/src/plugins/<plugin_id>/...`. Verify archive entries before upload.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`
- Tags: inkypi, epaperpod, deployment, zip, powershell

---
## [LRN-20260528-108] best_practice

**Logged**: 2026-05-28T20:38:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper_pet

### Summary
Use ecology-based prey metadata for the e-paper pet hunting chain.

### Details
When expanding the Tamagotchi-style pet's hunting system, model prey as a reality-inspired food web rather than fantasy items. Each prey entry should carry unlock level, prey size tier, broad biological group, approximate mass, localized display name, reserve gain, XP gain, and hunt cost. High-level hunting should focus on the current and immediately previous prey size tiers so progression feels like the animal is moving up the food chain instead of randomly returning to mostly tiny prey.

### Suggested Action
For future `epaper_pet` survival work, keep hunted-food data structured and expose the most recent prey metadata to AI context so generated one-liners can reference the animal's current ecology and progression.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `tools/smoke_epaper_pet.py`
- Tags: epaper, pet, survival, hunting, progression, localization

---
## [LRN-20260528-107] best_practice

**Logged**: 2026-05-28T19:22:00-07:00
**Priority**: medium
**Status**: active
**Area**: deployment

### Summary
EpaperPod plugin deployment can update writable plugin files over SSH, but service reload may need the InkyPi reboot endpoint.

### Details
During Bambu Monitor deployment, the SSH user could overwrite files in both `/usr/local/inkypi/src/plugins/bambu_monitor/` and `/home/feeengyuuu/inkypi-weather-pi-package-20260524-3/InkyPi/src/plugins/bambu_monitor/`, but `sudo -n true` failed with `sudo: a password is required`, so direct `systemctl restart inkypi` was unavailable over SSH. Calling the app's `/shutdown` endpoint with `{"reboot": true}` rebooted the device and reloaded plugin code. The Zero W took several minutes to initialize the Waveshare display before Waitress began serving HTTP again.

### Suggested Action
For future EpaperPod single-plugin deployments, deploy a forward-slash zip to both active and source package paths, run remote `py_compile` with `PYTHONPYCACHEPREFIX=/tmp/...`, then use the InkyPi reboot endpoint if `sudo -n systemctl restart inkypi` is unavailable. Poll both `systemctl is-active inkypi` and local `curl http://127.0.0.1/` because systemd can be active before HTTP is ready.

### Metadata
- Source: implementation
- Related Files: `tools/epaperpod-deploy-zip.ps1`, `inkypi-weather/package/InkyPi/src/plugins/bambu_monitor/bambu_monitor.py`
- Tags: inkypi, epaperpod, deployment, reboot, sudo, readiness

---
## [LRN-20260528-110] correction

**Logged**: 2026-05-28T21:35:00-07:00
**Priority**: high
**Status**: active
**Area**: backend

### Summary
Use random-bag playlist rotation for EpaperPod plugin pages.

### Details
The user clarified that automatic whole-plugin rotation should remove each displayed plugin instance from the random selection pool until every plugin in the active playlist has displayed. Only after the pool is exhausted should the pool be rebuilt and shuffled again. This is different from a sliding recent-history cooldown: the desired mental model is "display one, exclude one" for the current round, then restore the full pool for the next round, with only immediate back-to-back repeats avoided at the round boundary.

### Suggested Action
For future playlist work in `Playlist.get_next_plugin()`, preserve the persisted `plugin_rotation_queue` as the current round's remaining random bag. Do not replace it with probabilistic recent-history blocking unless the user explicitly asks for a longer cross-round cooldown.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/model.py`, `inkypi-weather/package/InkyPi/tests/test_model.py`
- Tags: inkypi, epaperpod, playlist, random, no-repeat, rotation

---
## [LRN-20260529-111] correction

**Logged**: 2026-05-29T15:38:59-07:00
**Priority**: high
**Status**: active
**Area**: hardware

### Summary
In EpaperSystem, "new screen" usually means the Raspberry Pi e-paper panel, not the Windows desktop monitor.

### Details
The user corrected an attempted Windows display enumeration. For this workspace, screen/hardware questions should first be interpreted in the EpaperPod/Raspberry Pi context: current baseline device `EpaperPodBeta`, InkyPi service, Waveshare e-paper HATs, SPI, `display_type`, and Pi connectivity. Only inspect Windows monitors if the user explicitly says PC monitor, HDMI desktop screen, or Windows display.

### Suggested Action
For future EpaperSystem hardware checks, start with project docs, Pi SSH/HTTP reachability, InkyPi `device.json`, Waveshare driver modules, and product model compatibility before querying Windows display APIs.

### Metadata
- Source: user_feedback
- Related Files: `docs/new-board-migration-baseline.md`, `inkypi-weather/dist/inkypi-weather-pi-package/InkyPi/src/display/display_manager.py`
- Tags: epaper, raspberry-pi, waveshare, hardware, correction

---
## [LRN-20260529-112] best_practice

**Logged**: 2026-05-29T15:43:24-07:00
**Priority**: medium
**Status**: active
**Area**: hardware

### Summary
For EpaperSystem Waveshare screen swaps, update both the install wrapper and the JSON config defaults.

### Details
InkyPi's lower-level installer already accepts `-W <waveshare_model>` and can fetch the matching Waveshare driver, but the project wrapper `install_on_pi.sh`, package README, migration baseline, and `install/config_base/device.json` can still pin the previous screen. For an existing board, `install.sh` should update `device.json` with structured JSON logic rather than text-only `sed` replacement so `display_type`, `resolution`, orientation, and image settings remain consistent.

### Suggested Action
When changing e-paper hardware, update the wrapper `-W` argument, vendor the official driver into `src/display/waveshare_epd/`, update fresh-install config defaults, and make install-time config migration explicit for existing `device.json` files.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/install_on_pi.sh`, `inkypi-weather/package/InkyPi/install/install.sh`, `inkypi-weather/package/InkyPi/install/config_base/device.json`
- Tags: inkypi, waveshare, display, installer, migration

---
## [LRN-20260529-113] best_practice

**Logged**: 2026-05-29T16:18:29-07:00
**Priority**: high
**Status**: active
**Area**: hardware

### Summary
For Waveshare `epd7in3e`, a BUSY pin stuck low points first to panel connection issues.

### Details
During the 7.3 inch Spectra 6 / E6 deployment, `epd7in3e.EPD().init()` timed out with `busy_initial=0` and `busy_final=0`. After the user reconnected the host/screen, BUSY reads became `[1, 1, 1, 1, 1]` and `epd.init()` completed in about 0.3 seconds. InkyPi then loaded `epd7in3e`, Waitress became ready after about 2 minutes 20 seconds, and forced display refreshes completed successfully. The practical diagnostic is: if BUSY remains `0`, stop software retries and power off before checking FPC orientation, connector latch, HAT seating, and power.

### Suggested Action
Keep the `epd7in3e` driver BUSY timeout guard and include the hardware connection checklist in deployment docs. For E6 panels, expect slow startup and display updates; avoid stopping the service mid-refresh unless necessary.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/display/waveshare_epd/epd7in3e.py`, `docs/new-board-migration-baseline.md`
- Tags: inkypi, waveshare, epd7in3e, busy-pin, fpc, deployment

---
## [LRN-20260529-114] best_practice

**Logged**: 2026-05-29T16:53:41-07:00
**Priority**: medium
**Status**: active
**Area**: tests

### Summary
For InkyPi plugin tests in this workspace, use the Codex Python 3.12 runtime with project `.pc-packages` on `PYTHONPATH`.

### Details
The local `.venv*` environments did not include pytest/Pillow, system Python 3.14 could not import compiled Pillow from `.pc-packages`, and direct bytecode compilation hit existing `__pycache__` permission errors. The working path was `C:\Users\super\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` with `PYTHONPATH` set to `inkypi-weather/package/InkyPi/.pc-packages` and `PYTHONDONTWRITEBYTECODE=1`.

### Suggested Action
For future targeted InkyPi Python tests, run from `inkypi-weather/package/InkyPi` with the Codex runtime, set `.pc-packages` on `PYTHONPATH`, and disable bytecode/cache writes when possible.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/tests/test_flow_progress.py`
- Tags: inkypi, tests, python, pytest, pc-packages, codex-runtime

---
## [LRN-20260529-115] correction

**Logged**: 2026-05-29T16:56:04-07:00
**Priority**: medium
**Status**: active
**Area**: frontend

### Summary
Dots / `flow_progress` should title the page as `PROGRESSION of <current year>`, not `DOTS`.

### Details
The user corrected the first color comic day design: the title should not be the plugin nickname `DOTS`; it should read as an annual progress page, using the current render year dynamically, for example `PROGRESSION of 2026`.

### Suggested Action
For future `flow_progress` visual iterations, including a possible night version, preserve the dynamic `PROGRESSION of {now.year}` title unless the user explicitly asks for a different naming direction.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/flow_progress/flow_progress.py`
- Tags: inkypi, flow-progress, dots, title, design

---
## [LRN-20260529-116] best_practice

**Logged**: 2026-05-29T17:08:22-07:00
**Priority**: low
**Status**: active
**Area**: infra

### Summary
Avoid complex nested Python one-liners inside PowerShell SSH commands for EpaperPod deployment checks.

### Details
During a Dots hot deploy, remote `python3 -c ...` commands that embedded JSON quotes were mangled by the Windows PowerShell to SSH quoting layers, producing unterminated string syntax errors. The deploy itself succeeded, and simpler SSH commands plus local `Invoke-RestMethod` / `Invoke-WebRequest` API checks were reliable.

### Suggested Action
For future Pi deployment verification, prefer simple SSH commands, local Web API calls, or small checked-in helper scripts over quote-heavy remote Python one-liners. If structured remote JSON parsing is necessary, copy a temporary script or use a minimal command with no nested JSON/string quoting.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/blueprints/plugin.py`, `inkypi-weather/package/InkyPi/src/blueprints/main.py`
- Tags: epaperpod, ssh, powershell, deployment, quoting

---
## [LRN-20260529-117] correction

**Logged**: 2026-05-29T17:14:46-07:00
**Priority**: medium
**Status**: active
**Area**: frontend

### Summary
The Dots / `flow_progress` title banner needs explicit left/right padding around `PROGRESSION of <year>`.

### Details
The user corrected the deployed comic day view because the `PROGRESSION of 2026` title was visually squeezed against the slanted title border. The fix expanded the banner width and added a dedicated `title_text_pad` inside the text box so the long title has breathing room.

### Suggested Action
For future `flow_progress` title changes, keep explicit horizontal padding inside the slanted banner and validate on the actual 800x480 render, not only by text centering math.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/flow_progress/flow_progress.py`
- Tags: inkypi, flow-progress, dots, title, layout, padding

---
## [LRN-20260529-118] correction

**Logged**: 2026-05-29T17:24:48-07:00
**Priority**: high
**Status**: active
**Area**: frontend

### Summary
For major color e-paper visual redesigns, generate an img-2 reference before hand-coding the page.

### Details
While redesigning the stock page toward a Shanghai Animation Studio-era Chinese animation craft direction, the first direct hand-coded pass looked poor. The user explicitly corrected the workflow: first use img-2 to generate a visual reference, then use that as the implementation target.

### Suggested Action
For future substantial e-paper art-direction changes, especially historical or culturally specific themes, create a generated visual reference first, review its composition, then translate only the successful layout/color principles into code. Avoid copying protected characters or film scenes; preserve functional semantics such as US stock green-up/red-down colors when requested.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`
- Tags: epaper, color-ui, visual-design, img2, stocktracker, workflow

---
## [LRN-20260529-116] correction

**Logged**: 2026-05-29T17:10:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
When the user approves generated Mini Weather backgrounds, install that exact set and make the rendered background fill the whole 800x480 plugin viewport.

### Details
The user correction was that the latest generated mythic comic backgrounds were already visually good and should not be regenerated. The remaining requirement was placement: the image must fill the background dimensions. For Mini Weather this means keeping the approved images, storing them as exact `800x480` plugin assets, selecting them by live weather slug, and rendering the background layer with viewport-cover behavior so the base plugin body padding/margins do not leave a plain edge.

### Suggested Action
For future Mini Weather background sets, do not treat "background must fill" as a prompt change if the art is already approved. Treat it as integration/layout work: resize/crop assets to `800x480`, use `background-size: cover`, avoid `background-repeat`, and preview the actual plugin frame to check for outer whitespace.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/mini_weather/render/mini_weather.css`, `inkypi-weather/package/InkyPi/src/plugins/mini_weather/backgrounds_color/`
- Tags: inkypi, mini-weather, weather-backgrounds, imagegen, layout

---
## [LRN-20260529-117] correction

**Logged**: 2026-05-29T17:35:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
The active EpaperPod device URL is `http://192.168.1.186/`, and Mini Weather icon previews should use the approved old Chinese animation-inspired style.

### Details
The user corrected the device target from `.183` to `.186`. The `.186` host responded with HTTP 200 and SSH hostname `EpaperPodBeta`. For Mini Weather, the requested visual direction is not to copy specific Shanghai Animation Film Studio frames or characters, but to make original weather icons that evoke old Chinese animation craft: ink line, cut-paper shapes, water/ink wash, mineral colors, auspicious cloud forms, and paper texture.

### Suggested Action
For future EpaperPod deploys in this workspace, default to `feeengyuuu@192.168.1.186` unless the user gives a newer address. For Mini Weather icon work, store candidate icons under `icons_color/<style>/`, keep the original `icons/` assets as fallback, and preview against the real Mini Weather layout before finalizing.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/mini_weather/icons_color/shanghai_animation/`, `inkypi-weather/package/InkyPi/src/plugins/mini_weather/mini_weather.py`
- Tags: inkypi, epaperpod, host, mini-weather, icons, shanghai-animation

---
## [LRN-20260529-118] best_practice

**Logged**: 2026-05-29T18:28:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Mini Weather visual changes must be applied to both HTML/CSS and PIL fallback on the Pi.

### Details
On the active EpaperPod, Mini Weather logged `HTML render failed; using PIL fallback renderer`, so CSS-only changes to card transparency did not affect the actual device image. The fallback renderer draws cards, labels, and background blending directly in Pillow. User-facing changes such as replacing `NOW` with the current weekday and making card backgrounds transparent must be reflected in `mini_weather.py` as well as `render/mini_weather.css`.

### Suggested Action
Before judging Mini Weather visual changes on hardware, inspect `journalctl -u inkypi` for HTML render fallback warnings. Keep a local PIL fallback preview script for card/background work, and deploy/restart Python changes when the fallback renderer is involved.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/mini_weather/mini_weather.py`, `inkypi-weather/package/InkyPi/src/plugins/mini_weather/render/mini_weather.css`
- Tags: inkypi, mini-weather, pil-fallback, css, visual-qa

---
## [LRN-20260529-119] correction

**Logged**: 2026-05-29T17:31:19-07:00
**Priority**: high
**Status**: active
**Area**: frontend

### Summary
For the Stock Tracker color-screen update, preserve the existing dashboard layout and only add restrained color.

### Details
The user rejected the themed Shanghai Animation Studio-inspired stock page direction as visually unpleasant and redirected the work to keep the current stock page structure. The right approach is the existing title, portfolio summary, trend chart, and holdings table layout, with color used for panel accents, chart line/fill, row alternation, and US-style green-up/red-down changes.

### Suggested Action
For future stock page visual updates, avoid full art-direction redesign unless explicitly approved from a reference. Start from the current functional layout and add color/layering incrementally so the page feels alive without changing scan patterns.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`
- Tags: stocktracker, color-ui, layout, epaper

---
## [LRN-20260529-120] correction

**Logged**: 2026-05-29T17:31:19-07:00
**Priority**: medium
**Status**: active
**Area**: frontend

### Summary
Stock Tracker holding rows need a clear gutter between the left status color bar and ticker text.

### Details
After adding color to the existing Stock Tracker layout, the user noted the left color indicator in the holdings list visually overlapped the ticker symbols. The fix was to keep the status bar near the row edge and move the `SYMBOL` column text to the right.

### Suggested Action
When adding row status indicators to compact e-paper tables, reserve explicit horizontal gutter space before text. Validate with bold ticker symbols because their strokes make tight spacing look like overlap on the rendered screen.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`
- Tags: stocktracker, table-layout, color-ui, epaper

---
## [LRN-20260529-121] correction

**Logged**: 2026-05-29T17:32:47-07:00
**Priority**: medium
**Status**: active
**Area**: frontend

### Summary
Stock Tracker should place the Yahoo Finance source label in the top header row.

### Details
The user asked to move `Yahoo Finance data` out of the portfolio summary card and place it on the same top row as `COLOR E-PAPER MODE`. This keeps the card focused on portfolio value while preserving the source label as page-level metadata.

### Suggested Action
For future Stock Tracker header/layout changes, keep data source and display mode labels grouped in the top-right header rather than inside content panels.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`
- Tags: stocktracker, header, metadata, layout

---
## [LRN-20260529-122] correction

**Logged**: 2026-05-29T17:33:54-07:00
**Priority**: medium
**Status**: active
**Area**: frontend

### Summary
Stock Tracker should show the refresh timestamp in the portfolio summary card footer.

### Details
After moving `Yahoo Finance data` to the page header, the user asked to reuse its former footer position in the portfolio card for the refresh time. This makes source/mode page-level metadata while keeping update recency close to the portfolio value.

### Suggested Action
For future Stock Tracker layout changes, keep `Yahoo Finance data | COLOR E-PAPER MODE` in the top-right header and place a compact `Updated HH:MM` timestamp in the summary card footer.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`
- Tags: stocktracker, refresh-time, metadata, layout

---
## [LRN-20260529-123] correction

**Logged**: 2026-05-29T17:52:49-07:00
**Priority**: high
**Status**: active
**Area**: frontend

### Summary
Color-screen UI should obey the old comic palette law, including Stock Tracker.

### Details
The user clarified that the governing color rule is old comic process/Pantone palette compliance. The Stock Tracker page should use the same color discipline as the Dots comic page: warm paper ground, process-black outlines/text, and limited yellow, blue, orange, red, and green accents, while preserving semantic requirements such as US stock green-up/red-down behavior.

### Suggested Action
For future color-screen plugin updates, do not use generic muted dashboard colors by default. Start from `docs/color-ui-guidelines.md` and trace UI colors to vintage comic process palette tokens unless the user explicitly requests a different palette.

### Metadata
- Source: user_feedback
- Related Files: `docs/color-ui-guidelines.md`, `.learnings/color_ui_palette_rule.md`, `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`
- Tags: color-ui, comic-palette, stocktracker, epaper

---
## [LRN-20260529-124] correction

**Logged**: 2026-05-29T18:06:24-07:00
**Priority**: medium
**Status**: active
**Area**: frontend

### Summary
Stock Tracker trend chart markers should be dark green, not yellow.

### Details
The user pointed out that the yellow chart markers on the blue trend line were hard to read on the e-paper preview. The fix uses a dark green marker token with black outline for the chart points, while leaving US stock gain/loss colors unchanged.

### Suggested Action
For future Stock Tracker chart tweaks, keep trend markers high-contrast against both the blue line and pale blue chart fill. Prefer the dark green comic-palette marker with black outline over yellow dots in the trend panel.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`
- Tags: stocktracker, chart, markers, color-ui, readability

---
## [LRN-20260529-125] correction

**Logged**: 2026-05-29T19:39:44-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Robinhood account activity CSV is not necessarily a current holdings snapshot.

### Details
The user provided a Robinhood CSV with `Activity Date`, `Instrument`, `Trans Code`, and `Quantity`. It was sufficient to identify Robinhood activity-report format, but not sufficient by itself to guarantee current holdings because it may omit opening positions before the report range. In this case, a dividend row referenced existing AAPL shares that were not represented by buy rows in the CSV.

### Suggested Action
For Stock Tracker, treat `symbol,shares` current-position CSV or a screenshot/manual current holdings list as the accurate source of truth. Use Robinhood activity CSV only if the export covers full account history, or if implementing a stateful workflow that applies weekly activity deltas to a verified baseline.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`, `inkypi-weather/package/InkyPi/tests/test_stocktracker.py`
- Tags: stocktracker, robinhood, csv, holdings, accuracy

---
## [LRN-20260529-126] best_practice

**Logged**: 2026-05-29T20:08:06-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Simple Calendar comic character overlays should target the focus-panel date number.

### Details
For the Simple Calendar comic layout, small character cutouts are most effective when they are composited over the large left/right focus-panel day number. Placing character art across every month-grid date would compete with readability and event markers. The better pattern is a date-stable rotating cutout on the focus number, with the calendar grid kept clean.

### Suggested Action
For future Simple Calendar interaction art, add or tune cutouts through `date_hero_cutouts` and `_draw_focus_date_hero`, keeping overlays on the large focus date unless the user explicitly asks for month-grid stickers.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`, `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/date_hero_cutouts/`
- Tags: simple-calendar, comic, cutouts, epaper, readability

---
## [LRN-20260529-127] best_practice

**Logged**: 2026-05-29T20:26:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
EpaperPod deploy checks should tolerate existing SFTP dirs and redirect pycache writes.

### Details
When deploying new plugin asset directories to EpaperPod, `sftp -b` stops on `mkdir` failure if the directory already exists. Prefix directory creation commands with `-mkdir` so existing remote directories do not abort the batch. Also, `python3 -m py_compile` against `/usr/local/inkypi` can fail with `Permission denied` when it tries to write `__pycache__`; use `PYTHONPYCACHEPREFIX=/tmp/inkypi-pycache` for remote syntax checks.

### Suggested Action
For future EpaperPod deployments, generate SFTP batches with `-mkdir` for directory setup and validate Python files with `PYTHONPYCACHEPREFIX=/tmp/inkypi-pycache python3 -m py_compile <file>`.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`, `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/date_hero_cutouts/`
- Tags: epaperpod, deploy, sftp, py_compile, permissions

---
## [LRN-20260530-128] best_practice

**Logged**: 2026-05-30T00:10:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Wikipedia POTD can be video media; WikiDaily must use MediaWiki thumbnails.

### Details
On 2026-05-29, Wikipedia Picture of the Day resolved to `A_Trip_Down_Market_Street_(1906).webm`. Downloading the original `.webm` as if it were an image caused request timeouts and temporary no-space errors, so `WikiDaily` never advanced `latest_refresh_time`.

### Suggested Action
For `wpotd`, request `imageinfo` with `iiurlwidth` and prefer `thumburl`. Reject direct `.webm`, `.ogv`, `.mp4`, `.mov`, and `.svg` original URLs unless MediaWiki provides a raster thumbnail.

### Metadata
- Source: production_debug
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/wpotd/wpotd.py`, `inkypi-weather/package/InkyPi/tests/test_wpotd_smart_crop.py`
- Tags: epaperpod, wpotd, wikipedia, mediawiki, video, thumbnails

---
## [LRN-20260531-129] best_practice

**Logged**: 2026-05-31T17:40:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Simple Calendar style-only fixes can be hot-applied through the plugin instance API.

### Details
On ColoredEpaperFrame, the running `inkypi` process was root-owned and `sudo -n systemctl restart inkypi` was unavailable. The live `Date` Simple Calendar instance had no `weatherPanelBackgroundStyle`, so it used the old default. Because the already-running code supported explicit comic styles, updating the instance via `PUT /update_plugin_instance/Date` with the existing settings plus `weatherPanelBackgroundStyle=img2_original_heroes_mixed`, then calling `POST /display_plugin_instance`, changed the live render without rebooting the device.

### Suggested Action
For future Simple Calendar visual default changes, prefer a code deploy plus an API instance-setting hotfix when restart access is unavailable. Preserve all existing form fields when using `PUT /update_plugin_instance/<instance>` because the endpoint replaces plugin settings with the submitted form data.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`, `inkypi-weather/package/InkyPi/src/blueprints/plugin.py`
- Tags: epaperpod, simple-calendar, deploy, hotfix, display-plugin-instance

---
