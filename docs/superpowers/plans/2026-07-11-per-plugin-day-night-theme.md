# Per-Plugin Day and Night Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every one of the 56 shipped renderable plugins its own tested daytime and deep-night presentation, selected with one canonical `auto | day | night` setting.

**Architecture:** A shared resolver normalizes legacy values and resolves `auto` once from device time and weather context. Every plugin manifest owns its day/night palette seeds and declares whether its renderer is UI-led or media-led. UI renderers consume the resolved palette directly; media renderers keep source pixels unchanged and receive a shared, plugin-colored outer chrome after their existing source/data cache returns. Resolved day/night is part of the authoritative rendered-image cache identity, while source/data caches remain theme-independent.

**Tech Stack:** Python 3.11, Pillow, Flask/Jinja, JSON plugin manifests, pytest, Ruff, Playwright, Raspberry Pi systemd release deployment.

## Global Constraints

- Canonical saved field: `themeMode`; accepted values: `auto`, `day`, `night`.
- Legacy aliases: `paper`, `light`, `comic`, and `white` resolve to `day`; `dark`, `cinema`, `streaming`, and `midnight` resolve to `night`.
- `auto` follows weather sunrise/sunset and falls back to 07:00-19:00 in the configured device timezone.
- Each plugin owns two palette definitions; plugins do not share one branded palette.
- Media source pixels are never inverted or globally recolored; only chrome, captions, borders, metadata, and fallbacks change.
- Theme-only renders reuse source/data caches and never set the plugin's network `forceRefresh` flag.
- Theme changes regenerate the visible instance immediately and other instances lazily on their next display.
- E-paper body and metadata text remains bold or semibold with strong contrast.
- All changes are based on `G:/PersonalProjects/EpaperSystem/.worktrees/main-integration` and must preserve unrelated worktree changes.

---

## File and interface map

**Shared contract and UI**

- Modify `inkypi-weather/package/InkyPi/src/utils/theme_utils.py`: expose `normalize_theme_mode()` and `resolve_plugin_theme()`.
- Modify `inkypi-weather/package/InkyPi/src/plugins/plugin_manifest.py`: parse theme capability and palette metadata without importing plugin code.
- Modify `inkypi-weather/package/InkyPi/src/plugins/plugin_registry.py`: expose `plugin_supports_day_night_theme()`.
- Modify `inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py`: provide `render_themed_image()`, palette access, and final presentation hook.
- Create `inkypi-weather/package/InkyPi/src/plugins/base_plugin/theme_presentation.py`: media chrome and high-contrast role derivation only.
- Create `inkypi-weather/package/InkyPi/src/templates/plugin_theme_settings.html`: shared `auto/day/night` selector.
- Modify `inkypi-weather/package/InkyPi/src/templates/plugin.html` and `src/blueprints/plugin.py`: render and prepopulate the shared selector.

**Scheduler and cache**

- Modify `inkypi-weather/package/InkyPi/src/refresh_task.py`: call the themed render entry point, include resolved mode in rendered cache names, and distinguish theme-only render from data force-refresh.
- Modify `inkypi-weather/package/InkyPi/src/model.py`: select only a theme-aware visible instance for immediate theme refresh.

**Plugin-owned metadata and renderers**

- Modify every `inkypi-weather/package/InkyPi/src/plugins/*/plugin-info.json` listed in Task 4.
- Modify the 17 existing two-palette renderers listed in Task 5.
- Modify the 19 UI-led fixed-color renderers listed in Tasks 6 and 7.
- The 20 media-led renderers listed in Task 8 use shared final chrome and retain their current source render path.

**Tests and acceptance**

- Modify `tests/test_theme_utils.py`, `test_plugin_manifest.py`, `test_plugin_registry.py`, `test_plugin_blueprint.py`, `test_refresh_task.py`, `test_model.py`, and `test_plugin_resource_contract.py`.
- Create `tests/test_plugin_theme_contract.py` for exhaustive 56-plugin coverage.
- Create `scripts/render_plugin_theme_matrix.py` for deterministic 800x480 day/night proofs.

---

### Task 1: Canonical plugin theme resolver

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/utils/theme_utils.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_theme_utils.py`

**Interfaces:**
- Produces: `normalize_theme_mode(value: Any, default: str | None = None) -> str | None`
- Produces: `resolve_plugin_theme(settings: Mapping[str, Any] | None, device_config: Any = None, now: datetime | None = None, palette: Mapping[str, Any] | None = None) -> dict[str, Any]`
- Contract: returned dictionary always contains canonical `mode`, `requested_mode`, `palette`, `css`, `source`, and `reason`.

- [ ] **Step 1: Write failing resolver tests**

```python
@pytest.mark.parametrize("raw, expected", [
    ("paper", "day"), ("light", "day"), ("comic", "day"),
    ("dark", "night"), ("cinema", "night"),
    ("streaming", "night"), ("midnight", "night"),
])
def test_normalize_theme_mode_accepts_legacy_aliases(raw, expected):
    assert normalize_theme_mode(raw) == expected

def test_plugin_forced_mode_overrides_device_auto(fake_device):
    result = resolve_plugin_theme({"themeMode": "night"}, fake_device, now=NOON)
    assert result["requested_mode"] == "night"
    assert result["mode"] == "night"

def test_plugin_auto_uses_shared_sunrise_sunset(fake_device, weather_context):
    result = resolve_plugin_theme({"themeMode": "auto"}, fake_device, now=NOON)
    assert result["requested_mode"] == "auto"
    assert result["mode"] == "day"

def test_missing_palette_roles_receive_readable_fallbacks(fake_device):
    result = resolve_plugin_theme(
        {"themeMode": "night"}, fake_device,
        palette={"night": {"background": "#101010", "accent": "#ff8800"}},
    )
    assert set(("background", "panel", "ink", "muted", "rule", "accent")) <= result["palette"].keys()
    assert contrast_ratio(result["palette"]["background"], result["palette"]["ink"]) >= 4.5
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest tests/test_theme_utils.py -q`

Expected: failures because the public normalizer and plugin resolver do not exist and the legacy aliases are incomplete.

- [ ] **Step 3: Implement the resolver**

```python
MODE_ALIASES = {
    "auto": "auto", "day": "day", "light": "day", "paper": "day",
    "comic": "day", "white": "day", "night": "night", "dark": "night",
    "cinema": "night", "streaming": "night", "midnight": "night",
}

def normalize_theme_mode(value, default=None):
    if value is None:
        return default
    return MODE_ALIASES.get(str(value).strip().lower(), default)

def resolve_plugin_theme(settings=None, device_config=None, now=None, palette=None):
    settings = settings or {}
    raw = next((settings.get(key) for key in
                ("themeMode", "theme_mode", "theme", "sportsDashboardTheme")
                if settings.get(key) not in (None, "")), "auto")
    requested = normalize_theme_mode(raw, "auto")
    context = get_theme_context(device_config, now=now)
    mode = context["mode"] if requested == "auto" else requested
    result = dict(context)
    result.update({"requested_mode": requested, "mode": mode})
    result["palette"] = resolve_palette_roles(palette or {}, mode)
    result["css"] = _css_palette(result["palette"])
    return result
```

- [ ] **Step 4: Run focused tests and lint**

Run: `python -m pytest tests/test_theme_utils.py -q && python -m ruff check src/utils/theme_utils.py tests/test_theme_utils.py`

Expected: all theme utility tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/src/utils/theme_utils.py inkypi-weather/package/InkyPi/tests/test_theme_utils.py
git commit -m "feat: add canonical plugin theme resolver"
```

### Task 2: Manifest capability, palette schema, and shared settings control

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/plugin_manifest.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/plugin_registry.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py`
- Modify: `inkypi-weather/package/InkyPi/src/blueprints/plugin.py`
- Modify: `inkypi-weather/package/InkyPi/src/templates/plugin.html`
- Create: `inkypi-weather/package/InkyPi/src/templates/plugin_theme_settings.html`
- Test: `inkypi-weather/package/InkyPi/tests/test_plugin_manifest.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_plugin_registry.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_plugin_blueprint.py`

**Interfaces:**
- Produces: `PluginCapabilities.supports_day_night_theme: bool`
- Produces: `PluginTheme(presentation: Literal["ui", "media"], day: Mapping, night: Mapping)`
- Produces: `plugin_supports_day_night_theme(plugin_config) -> bool`
- Template parameter: `supports_day_night_theme`.

- [ ] **Step 1: Add failing manifest and page tests**

```python
def test_v2_manifest_parses_theme_contract(tmp_path):
    path = write_manifest(tmp_path, capabilities={
        "supports_live_refresh": False,
        "supports_day_night_theme": True,
    }, theme={
        "presentation": "media",
        "day": {"background": "#f6f0e4", "accent": "#b33a2b"},
        "night": {"background": "#101318", "accent": "#ff7868"},
    })
    manifest = PluginManifest.from_path(path)
    assert manifest.capabilities.supports_day_night_theme is True
    assert manifest.theme.presentation == "media"

def test_plugin_page_has_one_shared_theme_selector(client, themed_plugin):
    html = client.get("/plugin/themed").get_data(as_text=True)
    assert html.count('name="themeMode"') == 1
    assert 'value="auto"' in html
    assert 'value="day"' in html
    assert 'value="night"' in html
```

- [ ] **Step 2: Verify the tests fail**

Run: `python -m pytest tests/test_plugin_manifest.py tests/test_plugin_registry.py tests/test_plugin_blueprint.py -q`

Expected: failures on absent capability, absent palette model, and absent shared selector.

- [ ] **Step 3: Implement strict metadata and the shared partial**

```html
{% if supports_day_night_theme %}
<div class="form-group plugin-theme-control">
  <label for="themeMode" class="form-label">Display palette</label>
  <select id="themeMode" name="themeMode" class="form-input">
    <option value="auto">Auto day/night</option>
    <option value="day">Day</option>
    <option value="night">Deep night</option>
  </select>
</div>
<script>
document.addEventListener('DOMContentLoaded', () => {
  const legacy = pluginSettings.themeMode || pluginSettings.theme_mode ||
    pluginSettings.theme || pluginSettings.sportsDashboardTheme || 'auto';
  const aliases = {paper:'day', light:'day', comic:'day', white:'day',
    dark:'night', cinema:'night', streaming:'night', midnight:'night'};
  document.getElementById('themeMode').value = aliases[legacy] || legacy || 'auto';
});
</script>
{% endif %}
```

Validate both day and night objects as dictionaries containing six-digit hex `background` and `accent`. Reject non-boolean capability values and presentation values outside `ui|media`. Pass capability through `BasePlugin.generate_settings_template()` so the partial is included once after the plugin-specific settings include.

- [ ] **Step 4: Run focused tests and lint**

Run: `python -m pytest tests/test_plugin_manifest.py tests/test_plugin_registry.py tests/test_plugin_blueprint.py -q && python -m ruff check src/plugins/plugin_manifest.py src/plugins/plugin_registry.py src/blueprints/plugin.py`

Expected: all focused tests pass.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/src/plugins/plugin_manifest.py inkypi-weather/package/InkyPi/src/plugins/plugin_registry.py inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py inkypi-weather/package/InkyPi/src/blueprints/plugin.py inkypi-weather/package/InkyPi/src/templates/plugin.html inkypi-weather/package/InkyPi/src/templates/plugin_theme_settings.html inkypi-weather/package/InkyPi/tests/test_plugin_manifest.py inkypi-weather/package/InkyPi/tests/test_plugin_registry.py inkypi-weather/package/InkyPi/tests/test_plugin_blueprint.py
git commit -m "feat: expose per-plugin theme settings"
```

### Task 3: Theme-aware render entry point and cache identity

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/plugins/base_plugin/theme_presentation.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py`
- Modify: `inkypi-weather/package/InkyPi/src/refresh_task.py`
- Modify: `inkypi-weather/package/InkyPi/src/model.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_refresh_task.py`
- Test: `inkypi-weather/package/InkyPi/tests/test_model.py`

**Interfaces:**
- Produces: `BasePlugin.render_themed_image(settings, device_config, *, theme_render_only=False)`.
- Produces: `BasePlugin.resolve_theme(settings, device_config, now=None)`.
- Produces: `apply_media_theme_chrome(image, plugin_id, theme, dimensions) -> Image.Image`.
- Cache identity suffix: `-day` or `-night` only for manifests with `supports_day_night_theme=true`.
- Theme-only media path: reuse the opposite-mode or legacy unthemed cached image, recover its unchanged inner media crop, and rebuild the bounded chrome; do not call the provider renderer.

- [ ] **Step 1: Write failing scheduler tests**

```python
def test_theme_aware_snapshot_paths_differ_by_resolved_mode(refresh_task, instance):
    refresh_task.device_config.set_config("active_theme", "day")
    day = refresh_task.cache_path_for_snapshot(instance)
    refresh_task.device_config.set_config("active_theme", "night")
    night = refresh_task.cache_path_for_snapshot(instance)
    assert day != night
    assert day.endswith("-day.png")
    assert night.endswith("-night.png")

def test_theme_only_render_does_not_set_network_force(refresh_task, plugin):
    refresh_task.render_theme_change(plugin)
    assert plugin.seen_settings["_theme_render_only"] is True
    assert plugin.seen_settings.get("forceRefresh") not in (True, "true")

def test_non_visible_theme_cache_is_generated_lazily(refresh_task, playlist):
    commands = refresh_task._select_background_commands()
    assert all(command.payload.get("theme_transition_missing") is not True for command in commands)

def test_media_theme_switch_reuses_opposite_cache(refresh_task, media_instance, provider):
    refresh_task.seed_theme_cache(media_instance, "day")
    refresh_task.render_theme_change(media_instance, "night")
    assert provider.call_count == 0
    assert refresh_task.cache_path(media_instance, "night").exists()

def test_failed_theme_render_keeps_last_good_and_enters_existing_cooldown(refresh_task, instance):
    refresh_task.seed_theme_cache(instance, "day")
    refresh_task.plugin.raise_on_render = True
    refresh_task.render_theme_change(instance, "night")
    assert refresh_task.displayed_mode(instance) == "day"
    assert refresh_task._theme_refresh_retry_delayed({"mode": "night"}, NOW) is True
```

- [ ] **Step 2: Verify failures**

Run: `python -m pytest tests/test_refresh_task.py tests/test_model.py -q`

Expected: cache paths collide and theme commands currently propagate force refresh.

- [ ] **Step 3: Implement the authoritative render wrapper**

```python
def render_themed_image(self, settings, device_config, *, theme_render_only=False):
    theme = self.resolve_theme(settings, device_config)
    render_settings = dict(settings or {})
    render_settings["_inkypi_theme"] = theme
    render_settings["_theme_render_only"] = bool(theme_render_only)
    if theme_render_only:
        render_settings.pop("forceRefresh", None)
        render_settings.pop("force_refresh", None)
    image = self.generate_image(render_settings, device_config)
    if self.manifest_theme.presentation == "media":
        image = apply_media_theme_chrome(
            image, self.get_plugin_id(), theme, self.get_dimensions(device_config)
        )
    image.info["inkypi_theme_mode"] = theme["mode"]
    return image
```

Replace all seven production `plugin.generate_image(...)` calls in `refresh_task.py` with this entry point. Add resolved mode to `_cache_identity_filename`; when only the opposite-mode cache exists, do not classify the current-mode miss as background work. For a media manifest and a theme-only command, load the opposite-mode cache (or the pre-migration legacy unthemed cache), recover the byte-identical inner media rectangle, rebuild the 8-pixel chrome, and commit it under the new mode without invoking `generate_image()`. Keep the visible instance's immediate theme switch, and set `_theme_render_only` instead of data `forceRefresh` for UI renderers.

- [ ] **Step 4: Run focused tests and lint**

Run: `python -m pytest tests/test_refresh_task.py tests/test_model.py -q && python -m ruff check src/plugins/base_plugin src/refresh_task.py src/model.py`

Expected: scheduler, model, and lint gates pass.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/src/plugins/base_plugin inkypi-weather/package/InkyPi/src/refresh_task.py inkypi-weather/package/InkyPi/src/model.py inkypi-weather/package/InkyPi/tests/test_refresh_task.py inkypi-weather/package/InkyPi/tests/test_model.py
git commit -m "feat: separate themed renders from source refreshes"
```

### Task 4: Declare all 56 plugin-owned palette seeds

**Files:**
- Modify: all 56 `inkypi-weather/package/InkyPi/src/plugins/<id>/plugin-info.json` files listed below.
- Create: `inkypi-weather/package/InkyPi/tests/test_plugin_theme_contract.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_plugin_resource_contract.py`

**Interfaces:**
- Each manifest adds `capabilities.supports_day_night_theme: true`.
- Each manifest adds `theme.presentation` plus `theme.day.background/accent` and `theme.night.background/accent`.

- [ ] **Step 1: Write the exhaustive failing contract test**

```python
def test_every_builtin_renderer_owns_two_valid_palettes():
    manifests = load_all_builtin_manifests()
    assert len(manifests) == 56
    for manifest in manifests:
        assert manifest.capabilities.supports_day_night_theme, manifest.id
        assert manifest.theme.presentation in {"ui", "media"}, manifest.id
        assert manifest.theme.day != manifest.theme.night, manifest.id
        day = resolve_palette_roles({"day": manifest.theme.day}, "day")
        night = resolve_palette_roles({"night": manifest.theme.night}, "night")
        assert contrast_ratio(day["background"], day["ink"]) >= 4.5, manifest.id
        assert contrast_ratio(night["background"], night["ink"]) >= 4.5, manifest.id
```

- [ ] **Step 2: Verify the contract fails for undeclared plugins**

Run: `python -m pytest tests/test_plugin_theme_contract.py tests/test_plugin_resource_contract.py -q`

Expected: all current manifests fail the new exhaustive capability/palette gate.

- [ ] **Step 3: Add exact plugin-owned palette seeds**

Use the following `id : presentation, day background/day accent, night background/night accent` mapping. The shared role derivation creates panel, ink, muted, and rule colors while retaining these plugin-owned seeds.

```text
ai_image: media #f4efe5/#7c3aed #0f1020/#b69cff
ai_image_multiverse: media #efeafd/#5b34c4 #0d0a1a/#a98cff
ai_text: ui #f6f1e8/#4f46a5 #11121b/#9fa8ff
apod: media #edf2f8/#2457a6 #030814/#67a7ff
backtothedate: media #f3ead8/#a0522d #17100b/#e29a66
bambu_monitor: ui #fff6d8/#007a5e #101815/#66d5aa
box_office_top_movies: ui #f5efe2/#bb1e2d #0b0d0f/#ff4d5f
calendar: ui #f3f0e8/#315c9b #11161f/#74a9ef
china_box_office_top_movies: ui #fff0df/#b63b22 #120d0b/#ff795b
chinese_literature_clock: ui #f3ead8/#8f3d2f #17100d/#d98270
clock: ui #f1f4f0/#246b55 #08130f/#75c9aa
comic: media #fff1d7/#d64d2e #17100f/#ff8062
countdown: ui #f6efe8/#b33a3a #170d0d/#ff7b7b
daily_ai_news: ui #edf5f2/#166b5c #071513/#5dc6b2
daily_art: media #f4eee5/#8a5130 #15100d/#d7a071
daily_knowledge: ui #f1ede2/#7b5b24 #15120c/#d6b56b
daily_wiki_page: ui #f4f0e6/#385f8f #10151b/#79aee6
daily_word_poem: ui #f5eadf/#944e3c #180f0c/#db8d79
dota_profile_dashboard: ui #edf1f4/#596a80 #0d1117/#9badc3
epaper_pet: ui #fff2dd/#d4672d #1a1009/#ff9c62
flight_radar: ui #edf5f7/#147995 #071418/#63c8e3
flow_progress: ui #f0f4ec/#3f7550 #0b150e/#82c895
gcd_comic_covers: media #f2eee6/#306d84 #0d1417/#6cb8d0
github: ui #f2f3f5/#4a5568 #0d1117/#9aa7b8
image_album: media #f4efe8/#9b4f70 #180f15/#e094b4
image_folder: media #eef3eb/#537b41 #0e160b/#94cb7c
image_upload: media #f3f0ea/#6c5c9b #11101a/#aa9bdd
image_url: media #eef3f5/#35718b #0b1519/#77bfd8
literature_clock: ui #f5ecdf/#7b4b2a #18110b/#c99563
live_radar: ui #eaf4ef/#00856a #061612/#57d1b1
lol_info: ui #edf3f4/#1a7080 #081416/#65bdc9
magazine_covers: media #f6eee8/#b23a55 #190d12/#f07991
mini_weather: ui #eef5fa/#2c6fa3 #07131d/#70baf0
moon_phase: ui #f0f1f5/#5d63a6 #080a17/#a2a9ff
natgeo_photo_of_the_day: media #f5f0dd/#d39a00 #171407/#ffd34e
newspaper: media #f2efe7/#343434 #111111/#d7d7d7
pixiv_r18_ranking: media #fff0f5/#df4f8f #1b0b13/#ff88b9
reddit_rule34_hot: media #f7efe9/#d7592a #1a0e09/#ff966d
rss: ui #f6f0e8/#cc5a24 #180f0a/#ff9a66
screenshot: media #edf2f5/#436e86 #0b1318/#83b8d3
simple_calendar: ui #f5f0e5/#2e6a76 #0b1518/#69b9c5
species_radar: ui #eef5ea/#3d7c3a #0b1609/#7ed67a
sports_dashboard: ui #edf4ef/#087b4e #07140d/#50c98a
steam_charts: ui #eaf1f6/#176b9b #07131a/#62b5e8
steam_daily_art: media #eaf1f5/#245f86 #08131a/#6bacd5
steam_profile_dashboard: ui #edf3f5/#2c718a #081419/#70bfd7
stocktracker: ui #f1f4ec/#3d7a45 #0b150c/#7fd58a
tech_pulse: ui #eef1f5/#455f95 #0c111c/#879fe0
telegram_digest: ui #edf5f8/#2285aa #07161b/#68c8e6
todo_list: ui #f4f1e9/#6d6250 #14120e/#b9aa8e
unsplash: media #f3efea/#66574c #12100e/#b7a99f
us_tv_hot_shows: ui #f4eef6/#7d4aa1 #140d19/#be86df
weather: ui #edf5fa/#216d9d #07131c/#69b7e8
wow_profile_dashboard: ui #f4ede3/#9a5b24 #171008/#e2a362
wpotd: media #edf4f0/#287a63 #081510/#6bcbb0
year_progress: ui #f2f0e8/#6c6a35 #14130b/#bbb86a
```

- [ ] **Step 4: Run exhaustive manifest tests and format validation**

Run: `python -m pytest tests/test_plugin_theme_contract.py tests/test_plugin_resource_contract.py tests/test_plugin_manifest.py -q && git diff --check`

Expected: 56 manifests pass schema, capability, contrast, and uniqueness checks.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/src/plugins/*/plugin-info.json inkypi-weather/package/InkyPi/tests/test_plugin_theme_contract.py inkypi-weather/package/InkyPi/tests/test_plugin_resource_contract.py
git commit -m "feat: define day and night palettes for every plugin"
```

### Task 5: Repair the 17 existing dual-palette plugins

**Files:**
- Modify renderer and settings files under: `box_office_top_movies`, `china_box_office_top_movies`, `daily_ai_news`, `daily_knowledge`, `daily_wiki_page`, `daily_word_poem`, `epaper_pet`, `live_radar`, `mini_weather`, `moon_phase`, `species_radar`, `sports_dashboard`, `steam_charts`, `steam_profile_dashboard`, `tech_pulse`, `us_tv_hot_shows`, `weather`.
- Modify their existing tests: `test_box_office_top_movies.py`, `test_china_box_office_top_movies.py`, `test_daily_ai_news.py`, `test_daily_knowledge.py`, `test_daily_wiki_page.py`, `test_daily_word_poem.py`, `test_epaper_pet_context.py`, `test_live_radar.py`, `test_mini_weather_backgrounds.py`, `test_moon_phase.py`, `test_species_radar.py`, `test_sports_dashboard.py`, `test_steam_charts.py`, `test_steam_profile_dashboard_friend_status.py`, `test_tech_pulse.py`, `test_us_tv_hot_shows.py`, and `test_theme_utils.py` for Weather integration.

**Interfaces:**
- Consumes: `settings["_inkypi_theme"]` injected by `render_themed_image()`.
- Rule: raw saved aliases never choose a palette directly; only resolved `theme["mode"]` does.

- [ ] **Step 1: Add parameterized regression tests for false-auto plugins**

```python
@pytest.mark.parametrize("plugin_id", [
    "box_office_top_movies", "china_box_office_top_movies",
    "daily_wiki_page", "tech_pulse", "us_tv_hot_shows",
])
def test_auto_changes_palette_between_noon_and_night(plugin_id, plugin_factory):
    day = render_with_fixed_time(plugin_factory(plugin_id), "auto", NOON)
    night = render_with_fixed_time(plugin_factory(plugin_id), "auto", MIDNIGHT)
    assert image_digest(day) != image_digest(night)
```

Add request-count assertions to both box-office plugins, `steam_charts`, and `steam_profile_dashboard`: rendering the opposite palette with warm source data must make zero additional provider calls.

- [ ] **Step 2: Verify the tests fail on false auto and coupled caches**

Run: `python -m pytest tests/test_box_office_top_movies.py tests/test_china_box_office_top_movies.py tests/test_daily_wiki_page.py tests/test_tech_pulse.py tests/test_us_tv_hot_shows.py tests/test_steam_charts.py tests/test_steam_profile_dashboard.py -q`

Expected: `auto` stays dark in the known false-auto plugins and one or more render caches couple data to raw theme values.

- [ ] **Step 3: Consume the resolved context and separate data from presentation**

```python
theme = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
palette = theme["palette"]
is_night = theme["mode"] == "night"
```

Use `palette` for canvas, panels, text, rules, and accents. Remove raw `themeMode/theme` from provider data cache keys. Keep only provider query inputs in source cache keys. For Dota-style rendered PNG caches in this group, include resolved `mode` only in the rendered-image layer, never the provider response cache.

- [ ] **Step 4: Run all 17 plugin suites and lint**

Run: `python -m pytest tests/test_box_office_top_movies.py tests/test_china_box_office_top_movies.py tests/test_daily_ai_news.py tests/test_daily_knowledge.py tests/test_daily_wiki_page.py tests/test_daily_word_poem.py tests/test_epaper_pet_context.py tests/test_live_radar.py tests/test_mini_weather_backgrounds.py tests/test_moon_phase.py tests/test_species_radar.py tests/test_sports_dashboard.py tests/test_steam_charts.py tests/test_steam_profile_dashboard_friend_status.py tests/test_tech_pulse.py tests/test_us_tv_hot_shows.py tests/test_theme_utils.py -q`

Expected: all plugin tests pass, day/night image digests differ, and source request counts remain unchanged on theme-only renders.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/src/plugins inkypi-weather/package/InkyPi/tests
git commit -m "fix: make existing plugin themes truly automatic"
```

### Task 6: Add palettes to HTML and compact UI renderers

**Files:**
- Modify renderer/CSS files and tests for: `ai_text`, `calendar`, `clock`, `countdown`, `flow_progress`, `rss`, `simple_calendar`, `telegram_digest`, `todo_list`, `year_progress`.
- Modify shared HTML base: `inkypi-weather/package/InkyPi/src/plugins/base_plugin/render/plugin.css`.
- Create: `inkypi-weather/package/InkyPi/tests/test_plugin_theme_renderers.py` for plugins without a dedicated test module.
- Modify: `inkypi-weather/package/InkyPi/tests/test_calendar.py`, `test_flow_progress.py`, `test_rss_fallback.py`, `test_simple_calendar_holidays.py`, and `test_telegram_digest.py`.

**Interfaces:**
- HTML templates consume CSS variables `--theme-background`, `--theme-panel`, `--theme-ink`, `--theme-muted`, `--theme-rule`, and `--theme-accent` injected by `BasePlugin.render_image()`.
- PIL renderers consume the same six RGB roles from `_inkypi_theme.palette`.

- [ ] **Step 1: Add day/night render tests for all ten plugins**

```python
@pytest.mark.parametrize("plugin_id", [
    "ai_text", "calendar", "clock", "countdown", "flow_progress",
    "rss", "simple_calendar", "telegram_digest", "todo_list", "year_progress",
])
def test_compact_ui_has_two_plugin_palettes(plugin_id, deterministic_plugin):
    day = deterministic_plugin(plugin_id).render_themed_image({"themeMode": "day"}, DEVICE)
    night = deterministic_plugin(plugin_id).render_themed_image({"themeMode": "night"}, DEVICE)
    assert day.size == night.size == (800, 480)
    assert dominant_background(day) != dominant_background(night)
    assert minimum_text_contrast(day) >= 4.5
    assert minimum_text_contrast(night) >= 4.5
```

- [ ] **Step 2: Verify fixed-color renderers fail**

Run the ten corresponding test modules with `python -m pytest ... -q`.

Expected: day and night backgrounds currently match for every fixed-color plugin.

- [ ] **Step 3: Replace hard-coded structural colors with role variables**

```css
:root {
  --theme-background: {{ theme.css.background }};
  --theme-panel: {{ theme.css.panel }};
  --theme-ink: {{ theme.css.ink }};
  --theme-muted: {{ theme.css.muted }};
  --theme-rule: {{ theme.css.rule }};
  --theme-accent: {{ theme.css.accent }};
}
body { background: var(--theme-background); color: var(--theme-ink); }
```

For PIL renderers, replace only structural background/panel/text/rule constants. Preserve semantic red/green status meaning and existing decorative fonts.

- [ ] **Step 4: Run focused tests, 800x480 snapshots, and lint**

Run: `python -m pytest tests/test_plugin_theme_renderers.py tests/test_calendar.py tests/test_flow_progress.py tests/test_rss_fallback.py tests/test_simple_calendar_holidays.py tests/test_telegram_digest.py -q`

Expected: both palette variants pass size, difference, and contrast assertions.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/src/plugins inkypi-weather/package/InkyPi/tests
git commit -m "feat: theme compact interface plugins"
```

### Task 7: Add palettes to dashboard and data-heavy UI renderers

**Files:**
- Modify renderer files and tests for: `bambu_monitor`, `chinese_literature_clock`, `dota_profile_dashboard`, `flight_radar`, `github`, `literature_clock`, `lol_info`, `stocktracker`, `wow_profile_dashboard`.
- Modify: `inkypi-weather/package/InkyPi/tests/test_bambu_monitor.py`, `test_chinese_literature_clock.py`, `test_dota_profile_dashboard.py`, `test_flight_radar.py`, `test_lol_info.py`, `test_stocktracker.py`, and `test_wow_profile_dashboard.py`.
- Extend: `inkypi-weather/package/InkyPi/tests/test_plugin_theme_renderers.py` for `github` and `literature_clock`, which have no dedicated test modules.

**Interfaces:**
- Consumes the same resolved six-role palette as Task 6.
- Bambu camera pixels remain unchanged while its canvas, cards, labels, and chart colors change.
- Dota, LoL, and WoW source response caches remain theme-independent; any rendered PNG cache includes resolved mode.

- [ ] **Step 1: Add deterministic two-palette and cache tests**

```python
@pytest.mark.parametrize("plugin_id", [
    "bambu_monitor", "chinese_literature_clock", "dota_profile_dashboard",
    "flight_radar", "github", "literature_clock", "lol_info",
    "stocktracker", "wow_profile_dashboard",
])
def test_dashboard_uses_distinct_day_and_night_palettes(plugin_id, mock_sources):
    day, night = render_both_modes(plugin_id, mock_sources)
    assert image_digest(day) != image_digest(night)
    assert mock_sources.network_calls_after_night == mock_sources.network_calls_after_day
```

Add a Bambu assertion that a fixed camera-region center pixel is identical in day and night output while the surrounding panel pixel differs.

- [ ] **Step 2: Verify the tests fail**

Run the nine corresponding test modules with `python -m pytest ... -q`.

Expected: fixed-color layouts match and Dota/LoL/WoW may return a stale rendered cache.

- [ ] **Step 3: Apply structural palette roles and split render caches**

```python
theme = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
colors = theme["palette"]
canvas = Image.new("RGB", dimensions, colors["background"])
```

Map panels to `panel`, ordinary copy to `ink`, metadata to bold `muted`, rules to `rule`, and brand emphasis to `accent`. Keep camera/photo/logo content unmodified. Add `theme["mode"]` only to locally composed PNG cache names for Dota, LoL, and WoW.

- [ ] **Step 4: Run focused suites and lint**

Run: `python -m pytest tests/test_bambu_monitor.py tests/test_chinese_literature_clock.py tests/test_dota_profile_dashboard.py tests/test_flight_radar.py tests/test_lol_info.py tests/test_stocktracker.py tests/test_wow_profile_dashboard.py tests/test_plugin_theme_renderers.py -q`

Expected: all nine plugin suites pass with no extra provider calls.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/src/plugins inkypi-weather/package/InkyPi/tests
git commit -m "feat: theme dashboard interface plugins"
```

### Task 8: Add plugin-colored chrome to all 20 media renderers

**Files:**
- Modify shared finalizer: `inkypi-weather/package/InkyPi/src/plugins/base_plugin/theme_presentation.py`
- Validate media plugins: `ai_image`, `ai_image_multiverse`, `apod`, `backtothedate`, `comic`, `daily_art`, `gcd_comic_covers`, `image_album`, `image_folder`, `image_upload`, `image_url`, `magazine_covers`, `natgeo_photo_of_the_day`, `newspaper`, `pixiv_r18_ranking`, `reddit_rule34_hot`, `screenshot`, `steam_daily_art`, `unsplash`, `wpotd`.
- Test: their existing test modules plus `tests/test_plugin_theme_contract.py`.

**Interfaces:**
- `apply_media_theme_chrome()` creates an 8-pixel outer matte, copies the source image's center rectangle into it without resampling, and uses existing non-media caption/header regions identified by the plugin manifest; every displayed media pixel is byte-for-byte from the source.
- Day/night use that plugin manifest's own background and accent seeds.

- [ ] **Step 1: Add media preservation tests**

```python
@pytest.mark.parametrize("plugin_id", MEDIA_PLUGIN_IDS)
def test_media_theme_changes_chrome_without_recoloring_source(plugin_id, source_image):
    day = render_media(plugin_id, source_image, "day")
    night = render_media(plugin_id, source_image, "night")
    assert crop_media_region(day).tobytes() == crop_media_region(night).tobytes()
    assert crop_media_region(day).tobytes() == source_image.crop((8, 8, 792, 472)).tobytes()
    assert outer_chrome(day).tobytes() != outer_chrome(night).tobytes()

def test_theme_switch_does_not_refetch_media(media_plugin, provider):
    seed_authoritative_media_cache(media_plugin, "day")
    calls = provider.call_count
    render_theme_only_from_opposite_cache(media_plugin, "night")
    assert provider.call_count == calls
```

- [ ] **Step 2: Verify the tests fail without shared chrome**

Run: `python -m pytest tests/test_plugin_theme_contract.py tests/test_daily_art.py tests/test_gcd_comic_covers.py tests/test_magazine_covers.py tests/test_newspaper_rotation.py tests/test_pixiv_r18_ranking.py tests/test_steam_daily_art.py -q`

Expected: media outputs currently have no distinct day/night chrome.

- [ ] **Step 3: Implement non-destructive plugin-colored chrome**

```python
def apply_media_theme_chrome(image, plugin_id, theme, dimensions):
    source = image.convert("RGB")
    width, height = dimensions
    inner = source.crop((8, 8, width - 8, height - 8))
    result = Image.new("RGB", dimensions, theme["palette"]["background"])
    result.paste(inner, (8, 8))
    draw = ImageDraw.Draw(result)
    colors = theme["palette"]
    draw.rectangle((6, 6, width - 7, height - 7), outline=colors["accent"], width=2)
    result.info.update(image.info)
    result.info["inkypi_theme_mode"] = theme["mode"]
    result.info["inkypi_theme_plugin"] = plugin_id
    return result
```

Do not place the post-process inside media provider/data caches. Apply it after a normal plugin return, or directly to the opposite-mode/legacy authoritative cached PNG during a theme-only transition. Preserve `inkypi_skip_cache` and all other image metadata.

- [ ] **Step 4: Run every media plugin suite and lint**

Run the 20 corresponding test modules plus `test_plugin_theme_contract.py`; expected result is all pass, with unchanged central pixels and no second provider call.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/src/plugins/base_plugin/theme_presentation.py inkypi-weather/package/InkyPi/tests
git commit -m "feat: add day and night chrome to media plugins"
```

### Task 9: Canonicalize saved instances without replacing unrelated settings

**Files:**
- Create: `inkypi-weather/package/InkyPi/install/migrate_plugin_theme_settings.py`
- Modify: `inkypi-weather/package/InkyPi/install/install.sh`
- Create: `inkypi-weather/package/InkyPi/tests/test_migrate_plugin_theme_settings.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_install_update.py`

**Interfaces:**
- CLI: `migrate_plugin_theme_settings.py --config PATH [--dry-run]`.
- Mutation: add or replace only `plugin_settings.themeMode`; remove only recognized legacy theme keys after canonicalization; preserve all other bytes semantically and preserve mode/owner through atomic replace.

- [ ] **Step 1: Write failing migration tests**

```python
def test_migration_sets_auto_and_preserves_unrelated_settings(config_file):
    before = load(config_file)
    migrate(config_file)
    after = load(config_file)
    assert all(i["plugin_settings"]["themeMode"] == "auto" for i in instances(after))
    assert without_theme(after) == without_theme(before)

def test_migration_is_atomic_and_idempotent(config_file):
    migrate(config_file)
    first = config_file.read_bytes()
    migrate(config_file)
    assert config_file.read_bytes() == first
```

- [ ] **Step 2: Verify tests fail because no migration exists**

Run: `python -m pytest tests/test_migrate_plugin_theme_settings.py tests/test_install_update.py -q`

- [ ] **Step 3: Implement atomic full-payload preservation**

Use same-directory `mkstemp`, mode `0600`, `flush`, file `fsync`, `os.replace`, and Linux directory `fsync`. Reject symlink targets. `--dry-run` prints only instance names and old/new canonical modes, never credentials or complete settings.

- [ ] **Step 4: Run migration, install, and security tests**

Run: `python -m pytest tests/test_migrate_plugin_theme_settings.py tests/test_install_update.py tests/test_secret_key.py tests/test_secret_schema.py tests/test_secret_schema_plugin_contract.py -q && bash -n install/install.sh`

Expected: migration is idempotent, metadata-safe, and secret-free.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/install/migrate_plugin_theme_settings.py inkypi-weather/package/InkyPi/install/install.sh inkypi-weather/package/InkyPi/tests/test_migrate_plugin_theme_settings.py inkypi-weather/package/InkyPi/tests/test_install_update.py
git commit -m "feat: migrate plugin instances to automatic themes"
```

### Task 10: Deterministic visual matrix and complete regression

**Files:**
- Create: `inkypi-weather/package/InkyPi/scripts/render_plugin_theme_matrix.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_plugin_theme_contract.py`
- Create outputs under ignored `output/acceptance/themes/<plugin_id>-day.png` and `-night.png`.

**Interfaces:**
- CLI: `python scripts/render_plugin_theme_matrix.py --output PATH --width 800 --height 480`.
- Summary JSON records plugin ID, both hashes, dimensions, dominant backgrounds, and contrast result; it contains no settings or secrets.

- [ ] **Step 1: Add a failing smoke test for the matrix runner**

```python
def test_theme_matrix_covers_every_manifest(tmp_path):
    summary = run_matrix(tmp_path, deterministic=True)
    assert len(summary["plugins"]) == 56
    assert all(item["day_sha256"] != item["night_sha256"] for item in summary["plugins"])
    assert all(item["size"] == [800, 480] for item in summary["plugins"])
```

- [ ] **Step 2: Implement deterministic fixture routing**

Use each plugin's existing demo/mock/offline fixture. Do not contact providers in the matrix run. Fail with the plugin ID if a fixture cannot produce both images.

- [ ] **Step 3: Run focused and complete gates**

Run:

```powershell
python scripts/render_plugin_theme_matrix.py --output ..\..\..\..\output\acceptance\themes --width 800 --height 480
python -m pytest -q
python -m ruff check src tests install scripts
git diff --check
```

Expected: 56 day images and 56 night images, all image pairs differ, complete tests pass, Ruff passes, and diff check is clean.

- [ ] **Step 4: Review the 21 live-instance image pairs manually**

Inspect at original resolution: `live_radar`, `newspaper`, `daily_ai_news`, `simple_calendar`, `stocktracker`, `steam_daily_art`, `bambu_monitor`, `backtothedate`, `magazine_covers`, `daily_word_poem`, `steam_charts`, `box_office_top_movies`, `weather`, `sports_dashboard`, `gcd_comic_covers`, `daily_art`, `china_box_office_top_movies`, `pixiv_r18_ranking`, `daily_wiki_page`, `species_radar`, and `tech_pulse`.

Expected: no clipping, no thin gray metadata, original media remains natural, and every pair is visibly distinct.

- [ ] **Step 5: Commit**

```powershell
git add inkypi-weather/package/InkyPi/scripts/render_plugin_theme_matrix.py inkypi-weather/package/InkyPi/tests/test_plugin_theme_contract.py
git commit -m "test: verify every plugin day and night theme"
```

### Task 11: Deploy, migrate, and prove the physical device

**Files:**
- No tracked source changes expected.
- Save acceptance evidence under ignored `G:/PersonalProjects/EpaperSystem/output/acceptance/<release-id>/themes/`.

**Interfaces:**
- Release ID format: UTC timestamp plus current short commit.
- Live config remains `/var/lib/inkypi/config/device.json`; backup is stored under its migration directory before mutation.

- [ ] **Step 1: Build and validate a clean release archive**

Run the repository release builder, inspect the archive for forbidden secrets/fonts, and verify the SHA-256 before upload.

- [ ] **Step 2: Deploy through `/usr/local/bin/inkypi update`**

Expected: preflight, dependency check, install, migration dry run, atomic switch, readiness, and rollback guard all pass. Do not manually overwrite `/opt/inkypi/current`.

- [ ] **Step 3: Migrate the 21 live instances to `themeMode=auto`**

Back up `device.json` and both LKG files. Run the migration as root, verify only theme keys changed semantically, then restart once. Expected service state: `active`, `/ready` healthy, `NRestarts=0` after the controlled restart.

- [ ] **Step 4: Force a bounded day/night acceptance rotation**

For each of the 21 instances, render `day` and `night` through the same production themed render entry point, save the display manifest and PNG, and restore `themeMode=auto`. Never bulk-render all plugins concurrently.

- [ ] **Step 5: Verify scheduler behavior and finish**

Confirm current visible instance changes immediately at a forced theme boundary; non-visible instances do not bulk-render; the next selected instance uses the correct themed cache; provider request counts do not spike; service remains ready with zero unexpected restarts. Record the current release ID, image hashes, and redacted journal evidence.
