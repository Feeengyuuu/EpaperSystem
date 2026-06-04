# Daily Knowledge plugin

## [LRN-20260603-007] implementation

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper, daily-knowledge, api-keys

### Summary
`daily_knowledge` is a new InkyPi plugin that combines daily refreshed fact/knowledge sentences from Useless Facts and RapidAPI World Fun Facts into one e-paper page.

### User Preference
- The user wants a new page-style plugin named DailyKnowledge.
- The page should combine daily refreshed short knowledge/fact sentences from the APIs shown in screenshots.
- Do not expose screenshot-provided RapidAPI keys in code, logs, or final responses.

### Data Sources
- Useless Facts:
  - Official docs: `https://uselessfacts.jsph.pl/`
  - Uses `/api/v2/facts/today` by default, with optional `/random`.
  - Supports `language=en` and `language=de`; Chinese display falls back to English for this source.
- World Fun Facts via RapidAPI:
  - Marketplace page: `https://rapidapi.com/vintarok-vintarok-default/api/world-fun-facts-all-languages-support`
  - The plugin consumes live `.env` key `Fun_Fact` first, then standard fallbacks such as `RAPIDAPI_KEY`.
  - The exact RapidAPI endpoint path is configurable in settings and defaults to `/fact`, with several short fallback paths for first-run robustness.

### Implementation Pattern
- Plugin folder: `inkypi-weather/package/InkyPi/src/plugins/daily_knowledge/`
- Plugin class: `DailyKnowledge`
- Cache file: plugin-local `cache/daily.json`
- Cache key includes date, language, enabled sources, RapidAPI host, and RapidAPI path.
- If external APIs fail or the RapidAPI key is absent, the plugin uses local fallback facts so playlist refresh does not block.
- The plugin writes a context cache payload under `daily_knowledge` with date, language, and fact rows.

### Visual Pattern
- Full page title: `DAILY KNOWLEDGE`.
- Two stacked fact cards, each with a left accent stripe, source badge, body text, and compact source metadata.
- Uses shared day/night theme palette from `theme_utils`.
- Keep the layout sparse and readable for 800x480 e-paper.

### Validation
- Added `tests/test_daily_knowledge.py`.
- Local pytest is unavailable in the current Python environments, so validation used read-only AST parse plus a smoke render with bundled Codex Python and project dependency path ordering.
- Visual preview was inspected at `.tmp/daily_knowledge_preview.png`; `.tmp` output is ignored by git.

## [LRN-20260603-008] live deploy and display verification

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper, daily-knowledge, deployment

### Summary
`DailyKnowledge` was deployed to the live `ColoredEpaperFrame` playlist and verified through the real `/display_plugin_instance` display path.

### Deployment Pattern
- Active service entrypoint is `/usr/local/bin/inkypi run`; it uses `/usr/local/inkypi/venv_inkypi/bin/python`.
- `/usr/local/inkypi/src` is a symlink to the package source under `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/src`.
- Use the service venv for remote import checks. System `python3` can fail on shared dependencies such as `pytz` even when the service runtime is fine.
- Avoid hand-editing `device.json` while the old service process is still stopping; the running app can write its in-memory config back and overwrite manual edits. Prefer `/add_plugin` for adding playlist instances, or stop fully before direct config edits.
- `/display_plugin_instance` expects JSON keys `playlist_name`, `plugin_id`, and `plugin_instance`.
- Manual display calls can take about 70 seconds because the route waits through image generation and Waveshare display work; use a timeout of at least 120 seconds and wait for `/playlist` readiness after restart.

### Source Behavior
- Current RapidAPI World Fun Facts configuration reached the host but returned HTTP 403 at `/fact`, so the plugin fell back without blocking the display.
- Useless Facts can return awkward adult/crude random facts. `daily_knowledge` now has a light display-safety term filter and a bumped cache schema so unsuitable random facts do not persist on the screen.

### Visual Adjustment
- User asked for the two actual content lines to be larger. Body font sizing was raised from `max(18, min(30, width // 30))` to `max(22, min(34, width // 25))`.
- Final live image was pulled back from `current_image.png` and checked: larger body text fit within both cards without overlapping the source metadata.
