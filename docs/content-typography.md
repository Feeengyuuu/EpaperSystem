# Plugin typography: content roles and coverage

This audit covers all 74 plugins other than `sports_dashboard` and `live_radar`,
at source baseline `23662e14` (2026-11-14). Sports has its own league/game roles;
LiveRadar restores its original Microsoft YaHei regular/bold UI chain at the
owner's request, retaining the existing fallback when private fonts are absent. This document
records deliberate retention as well as changes: content matching does not
require a different family for every plugin.

The product is an 800 × 480 e-paper page, read at a glance. Readable Chinese,
clear figures, stable line wrapping and strong contrast take precedence over
decorative branding. Font selection must not change fetched data, refresh
policy, saved settings, layout dimensions or image/logo lettering.

## A finite set of roles

| Role | Chinese | Latin / figures | Use |
| --- | --- | --- | --- |
| News and dense lists | Noto Sans SC 500; 600 for bold headings at 14 px or more | Inter SemiBold / Bold | Headlines, summaries, sources and rankings |
| Research and technology | Same Noto weights | Public Sans Regular / SemiBold | Paper titles, original English, abstracts and service information |
| Instruments | Same Noto labels | Inter labels; Space Grotesk 600 / 700 for readings at 22 px or more | Printer progress, temperatures, battery and range |
| Steam | Same Noto fallback | Installed private Motiva Sans, otherwise Inter | Steam game names, prices and player counts |
| Existing editorial / literary | Existing Noto Serif, WenKai, Kai or quote-specific face | Existing Spectral, Lora, Bodoni or Jost roles | Art captions, poetry, literature, news display headings |
| Existing specialist displays | Existing safe CJK fallback | Existing transport, space, game, comic and clock families | Preserve the page's established subject and readable body |

Keep two or three families per page. Do not put paragraphs in a condensed,
monospaced, handwriting or display face. Monospace is useful for aligned
telemetry; it is not a requirement for every number. Instrument values are
measured in their existing boxes, and Latin capital/figure ink height is
matched to the old UI face using `8H`; nominal font size alone is misleading.

## Implemented opt-in pages

`src/utils/content_fonts.py` owns the new roles. It does not change
`get_base_ui_font`, `PageCanvas`, or the default UI across the application.

| Plugin | Change | Preserved boundaries |
| --- | --- | --- |
| `cn_hot_search` | Restored the existing Microsoft YaHei chain through `PageCanvas` at the owner's request | Source logos, rank geometry, story selection and explicit nondefault `fontFamily` |
| `daily_digest` | News pair through `ContentCanvas` | Content cards, image area, poem text and explicit nondefault `fontFamily` |
| `ai_papers` | Restored the existing Microsoft YaHei regular/bold chain through `PageCanvas` at the owner's request | Original English, Chinese translation, abstract, figures, image ranks and explicit nondefault `fontFamily` |
| `tech_play` | Restored the existing Microsoft YaHei chain through `PageCanvas` at the owner's request; body and small labels retain bold | Service state, game/paper blocks, image ranks and explicit nondefault `fontFamily` |
| `telegram_digest` | News pair at the existing `_font` boundary | Curation, source/age fields, lead/list wrapping and picture placement |
| `bambu_monitor` | News labels plus instrument face for large readings | Status semantics, fitted percentage, job filename, camera and AMS cards |
| `vehicle_status` | News labels plus instrument face for large readings | Vehicle art, existing model badge, battery/range geometry and units |
| `steam_charts` | Steam pair in native overview/fallback; equivalent local faces in HTML | Installed Motiva priority, original logos, game data, tables and old UI fallback |

The remaining `ContentCanvas` page, `daily_digest`, treats the normal `Microsoft YaHei` family as
its default role. A different explicitly selected family still uses the
original `PageCanvas.font` resolver. These changes do not rewrite a saved
selection. The other four plugins did not expose a font-family selector at
their changed rendering boundary.

Latin and Chinese are paired with the existing `EsportsFont` run renderer;
characters absent from Latin use the Chinese face on the same baseline.
Missing or corrupt files degrade through available bundled faces to the shared
UI font. Steam private bytes remain outside source and public release archives.

Font pairs are held in a bounded weak lookup. A page canvas owns its active
sizes only until that page is released. There is no added persistent strong
cache of 17 MB Noto instances. The shared glyph loader still owns its existing
per-file probes and raster caches; this is not a claim that all native font
objects are destroyed at the end of a render. Cache identity includes the
original requested bold flag even when small labels use the same heading
weight, because the fallback and reference ink height may differ.
Reference ink heights and matched Latin sizes use bounded scalar-only caches
keyed by font file identity, size and weight. They avoid reopening probe faces
for each new pair and do not retain native font objects.

## Existing designs to retain

| Plugins | Existing typography and decision |
| --- | --- |
| `apod`, `orbital_signal`, `planet_now` | `utils/space_typography.py` already separates Inter headings, Public Sans body and DM Mono telemetry with CJK fallback. Keep. |
| `bay_commute` | Space Grotesk/Inter readings, Hind transit labels and RG2014 road-sign roles already match different transport modes. Keep. |
| `bay_traffic_cams` | RG2014 OSD/road lettering already matches the camera display. Keep. |
| `flight_radar` | Inter airport codes and clear shared Chinese city labels. Keep. |
| `nature_nearby` | Noto Serif SC with Lora Italic gives the existing field-guide identity. Keep. |
| `daily_art` | `cover_type.py` gallery role uses Spectral with Noto Serif fallback; small reversed chips retain readable UI text. Keep. |
| `gcd_comic_covers` | Bangers/Boogaloo with ZCOOL KuaiLe supports comic-cover captions. Keep. |
| `magazine_covers` | Bodoni display title and Jost body form the existing newsstand role. Keep. |
| `daily_ai_news` | Noto Serif Black headline subset plus Noto Sans 500 story text is already tuned for news. Keep. |
| `daily_wiki_page` | Swiss renderer already uses Noto Sans with body size and sentence-aware truncation. Keep. |
| `backtothedate` | Zhi Mang Xing quotation subset is intentionally limited to quotations. Keep body metadata separate. |
| `chinese_literature_clock` | Existing Fandol Kai / I.Ming / WenKai choices are intentional. The UI's “方正新楷近似” is an approximation, not an official Fangzheng font claim. |
| `literature_clock` | Configurable literary face, default WenKai. Keep. |
| `lol_info` | Existing Beaufort/Cinzel, Barlow and Rubik roles are game-specific; preserve Korean and other missing-glyph fallback. |
| `game_deals`, `steam_profile_dashboard` | Existing optional Motiva Sans and price-tag roles already match Steam. Keep. |
| `stocktracker` | Existing Jost body and large Bodoni financial figure; preserve the contrast rather than putting small prices in display serif. |
| `weather` | Existing Jost primary temperature and ordinary UI labels provide a useful instrument hierarchy. Keep. |
| `clock` | Existing clock-specific digital/display options are part of the chosen clock design. Keep. |

## Audited candidates retained in this pass

These pages can reuse a role later, but the first pass has no demonstrated need
to change their typography. A frozen full-page comparison is required before
opting them in.

| Plugins | Candidate and reason to keep the present page now |
| --- | --- |
| `ai_ecosystem_pulse`, `tech_pulse` | Research pair could help dense Latin/Chinese summaries; both have independent font/settings paths that should be respected. |
| `bay_alerts`, `daily_pop` | News pair is a possible refinement, but avoid changing every shared canvas solely because it exists. |
| `money_radar`, `world_odds` | Use a financial role only after checking long prices/probabilities and signed changes; Money Radar news rows already use Public Sans. |
| `morning_read`, `newspaper` | Editorial headings or quotations may benefit from serif; definitions, excerpts and dense small body text should stay clear sans. |
| `print_inspiration`, `time_almanac` | Information and image hierarchy currently matters more than a new face; retain existing configurable canvas. |
| `dota_profile_dashboard`, `wow_profile_dashboard` | Optional game-specific headings are candidates; avoid claiming Cinzel or another stylistic substitute is an official game font. Keep readable statistics. |

## General-purpose and configurable pages retained

| Plugins | Decision |
| --- | --- |
| `ai_text`, `daily_knowledge`, `daily_word_poem`, `rss` | User/configured content and body type remain intact. A poetry role should be scoped to quotation text, not silently applied to all definitions. |
| `calendar`, `simple_calendar`, `countdown`, `flow_progress`, `year_progress` | Existing calendar/progress numerals and shared UI labels remain; typography alone is not a reason to change date density. |
| `github`, `mini_weather`, `ticketmaster_events`, `todo_list` | Existing shared UI and template behavior remain. Only introduce subject headings after a layout-specific preview. |

## Image-led and externally rendered pages retained

| Plugins | Decision |
| --- | --- |
| `ai_image`, `ai_image_multiverse`, `image_album`, `image_folder`, `image_upload`, `image_url`, `screenshot`, `unsplash`, `wpotd`, `natgeo_photo_of_the_day` | The image or externally rendered page is the primary content. Do not substitute embedded lettering or add typography over it. Existing UI/captions remain. |
| `box_office_top_movies`, `china_box_office_top_movies`, `us_tv_hot_shows`, `steam_daily_art` | Poster/game art leads; retain source lettering and existing readable captions. A later caption role must preserve poster area. |
| `comic`, `epaper_pet`, `moon_phase`, `species_radar` | Keep the existing illustration/pixel/astronomy/species presentation and UI labels. No blanket body-font replacement. |
| `pixiv_r18_ranking`, `reddit_rule34_hot` | Image-led pages retain existing image and caption behavior. |

The five tables cover exactly 74 unique plugin IDs: 8 changed, 21 established
designs retained, 12 later candidates, 13 general-purpose pages and 20 image-led
pages. `base_plugin` is infrastructure, not a selectable plugin in that count.

## Existing reusable assets and licences

No new font download or conversion is needed for the implemented roles.

| Family | Existing asset / licence evidence | Scope |
| --- | --- | --- |
| Noto Sans SC | `src/static/fonts/NotoSansSC-VF.ttf`, `NotoSansSC-OFL.txt` | General Chinese body and headings; 500/600 in the new roles |
| Inter | `src/plugins/flight_radar/assets/fonts/Inter-{SemiBold,Bold}.ttf`, `LICENSE.txt`, `sources.json` | Clear Latin and numerals; existing SIL OFL assets |
| Public Sans / DM Mono | `src/utils/assets/space_fonts`, per-family OFL and `sources.json` | Research body / explicitly aligned telemetry |
| Space Grotesk | `src/static/fonts/SpaceGrotesk-VF.ttf`, `SpaceGrotesk-OFL.txt` | Large instrument figures; not dense Chinese/English paragraphs |
| Noto Serif SC / Lora | `src/static/fonts`, OFL and `NatureFonts.source.json` | Existing editorial/nature roles; serif subsets do not imply full language coverage |
| Spectral / Bangers / Boogaloo / ZCOOL KuaiLe | `src/plugins/base_plugin/assets/cover_fonts`, OFL and `cover_fonts.source.json` | Existing gallery/comic captions |
| Bodoni Moda | `src/plugins/stocktracker/assets/fonts`, OFL/source record | Large finance/newsstand display text |
| Hind | `src/plugins/bay_commute/assets/fonts`, OFL/source record | Existing transit labels |
| Rubik / Cinzel | `src/plugins/lol_info/assets/fonts`, per-family OFL | Existing game headings and public fallbacks |
| Barlow condensed families | `src/plugins/sports_dashboard/assets/fonts`, per-family OFL/source record | Existing dense sports rows, owned by the sports typography layer |
| LXGW WenKai / Zhi Mang Xing | Existing static/quotation assets and OFL records | Literature / the existing quotation subset respectively |

Private YaHei, Motiva and game/league faces are not a bundled OFL font pool.
RG2014 has its own licence record and should not be relabelled OFL. Jost,
Napoli, digital/pixel fonts and the existing Kai/Ming assets keep their present
scope; this audit did not infer general reuse permission from a filename.

## Preview and regression requirements

The local evidence round is `outputs/typography-audit-20261114` (not tracked).
`preview_content.py` imports baseline and worktree source in place, blocks
network calls and draws identical frozen synthetic regression payloads. They
are typography examples, not current news, current vehicle state or a live
device screenshot. The baseline uses the same installed YaHei files as the
earlier local previews; new faces use the bundled assets.

Files follow `<plugin>-<before|after>-<day|night>.png`; the JSON manifests record
source checkout/HEAD, dimensions, data origin and render durations. All eight
changed plugins have full-page day/night coverage. News, paper and job-name
samples deliberately contain long Chinese and mixed Latin. Device number
tests exercise the largest percentages/readings against the existing boxes.
Separate device edge pages show a 100% print beside `LAYER 9999/9999`, and a
100% vehicle battery beside a 999-mile range, in both themes.

`test_content_fonts.py` verifies real glyph fallback, corrupt/missing files,
custom family preservation, private Steam priority, visible instrument height,
width limits and weak pair/native-face lifetime. Existing page tests retain
their content and layout assertions; draw recorders count the final RGB/RGBA
page, not the font renderer's internal grayscale mask. HTML Steam tables keep
local public fallback faces when the private face is missing or unreadable.
The targeted validation round passed 683 tests with 6 environment-dependent
skips; Ruff passed and the architecture gate reported no violations. Local
pixel comparisons confirmed the scalar-cache optimization did not change any
of the 16 regular after images.

The font pairing adds rendering work. The evidence records five draws per
plugin/theme and retains timing samples, rather than claiming unchanged speed.
These Windows desktop timings were collected while an independent full test
run was active; they are not a Raspberry Pi benchmark. The changes add no
network fetch, native-font strong cache or additional display refresh.

A successful local render/test does not establish physical e-paper appearance.
Physical acceptance and deployment belong to a separately authorized release.
