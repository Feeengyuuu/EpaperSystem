# Global Microsoft YaHei Base Font Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Microsoft YaHei the effective regular and bold base font across InkyPi while preserving explicit decorative and user-selected typography.

**Architecture:** Add one durable-font resolver in `utils.app_utils` that serves both PIL and HTML renderers from `${INKYPI_DATA_DIR}/fonts`, with tracked Noto Sans SC as the safe fallback. Migrate plugin-local base UI loaders to this resolver, preserve explicitly decorative loaders, and provision the user's device-owned font files outside Git and release payloads.

**Tech Stack:** Python 3.11, Pillow FreeType, Jinja/CSS `@font-face`, pytest, Ruff, Bash/systemd deployment.

## Global Constraints

- Runtime font storage is `${INKYPI_DATA_DIR}/fonts`; live path is `/var/lib/inkypi/data/fonts`.
- Font binaries must never be committed, copied into release source, or included in deployment archives.
- Brand wordmarks, icon fonts, DS-Digital, Napoli, Dogica, literary typefaces, and other explicitly decorative type remain unchanged.
- Missing, unreadable, or corrupt YaHei files must fall back to tracked Noto Sans SC without crashing a plugin.
- Regular and bold resolution must be independent and use `msyh.ttf`/`msyh.ttc` and `msyhbd.ttf`/`msyhbd.ttc` respectively.
- `msyhl.ttf`/`msyhl.ttc` are light faces and are not regular base-font candidates.
- The tracked Noto Sans SC fallback uses weight 400 for regular text and 700 for bold text; plugin loaders preserve the shared resolver instance instead of applying local 430/760/780 axes.
- Existing explicit user font selections remain valid; only empty/default settings and the five approved live legacy-default values migrate.

---

### Task 1: Shared durable base-font resolver

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/utils/app_utils.py:22-171`
- Modify: `inkypi-weather/package/InkyPi/tests/test_app_utils_fonts.py`
- Modify: `inkypi-weather/package/InkyPi/install/install.sh:110-125`
- Test: `inkypi-weather/package/InkyPi/tests/test_systemd_units.py`

**Interfaces:**
- Produces: `base_ui_font_candidates(bold: bool = False) -> tuple[str, ...]`
- Produces: `get_base_ui_font(font_size: int, bold: bool = False) -> ImageFont.FreeTypeFont`
- Produces: `resolve_base_ui_font_path(bold: bool = False) -> str`
- Produces: `font_file_uri(path: str) -> str`
- Existing `get_font`, `get_font_path`, and `get_fonts` consume the shared resolver for Microsoft YaHei aliases.

- [ ] **Step 1: Write failing resolver tests**

Add tests that copy the tracked `NotoSansSC-VF.ttf` into a temporary `${INKYPI_DATA_DIR}/fonts` as `msyh.ttf` and `msyhbd.ttf`, then assert:

```python
def test_durable_yahei_regular_and_bold_take_priority(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "src" / "static" / "fonts" / "NotoSansSC-VF.ttf"
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    shutil.copyfile(source, fonts / "msyh.ttf")
    shutil.copyfile(source, fonts / "msyhbd.ttf")
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path))

    regular = get_base_ui_font(18)
    bold = get_base_ui_font(18, bold=True)

    assert Path(regular.path) == fonts / "msyh.ttf"
    assert Path(bold.path) == fonts / "msyhbd.ttf"
    assert Path(get_font_path("microsoft-yahei")) == fonts / "msyh.ttf"
    assert Path(get_font_path("microsoft-yahei-bold")) == fonts / "msyhbd.ttf"
```

Add corrupt/missing tests that write `b"not-a-font"` and assert `get_base_ui_font` returns a FreeType font whose path is `NotoSansSC-VF.ttf`. Add a `get_fonts()` assertion that the YaHei face URL equals `Path(durable_path).resolve().as_uri()`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_app_utils_fonts.py -q
```

Expected: failures because the durable resolver and safe fallback do not exist.

- [ ] **Step 3: Implement the resolver**

Add the following structure to `app_utils.py` and make the three existing font APIs use it:

```python
YAHEI_REGULAR_FILES = ("msyh.ttf", "msyh.ttc")
YAHEI_BOLD_FILES = ("msyhbd.ttf", "msyhbd.ttc")
BASE_FALLBACK_FILES = ("NotoSansSC-VF.ttf", "LXGWWenKai-Regular.ttf")


def base_ui_font_candidates(bold=False):
    names = YAHEI_BOLD_FILES if bold else YAHEI_REGULAR_FILES
    data_dir = os.getenv("INKYPI_DATA_DIR")
    candidates = []
    if data_dir:
        candidates.extend(str(Path(data_dir) / "fonts" / name) for name in names)
    candidates.extend(resolve_path(os.path.join("static", "fonts", name)) for name in names)
    candidates.extend(resolve_path(os.path.join("static", "fonts", name)) for name in BASE_FALLBACK_FILES)
    return tuple(dict.fromkeys(candidates))


def get_base_ui_font(font_size, bold=False):
    for candidate in base_ui_font_candidates(bold=bold):
        try:
            return ImageFont.truetype(candidate, int(font_size))
        except OSError:
            continue
    return ImageFont.load_default()
```

`resolve_base_ui_font_path` must return the path actually loadable by Pillow. `font_file_uri` must call `Path(path).resolve().as_uri()`. `get_fonts()` must use the URI for CSS, while PIL APIs return filesystem paths.

- [ ] **Step 4: Create the persistent directory in the installer**

After the existing state-root ownership normalization, reassert the durable
font permissions without following symbolic links:

```bash
install -d -o root -g inkypi -m 0750 "$DATA_DIR/fonts"
find -P "$DATA_DIR/fonts" -xdev -type f \
  -exec chown --no-dereference root:inkypi {} + \
  -exec chmod 0640 {} +
```

Add a system install test asserting the exact owner/group/mode declaration and that uninstall without `--purge` does not remove `${DATA_DIR}/fonts`.

- [ ] **Step 5: Run tests, lint, and commit**

Run:

```powershell
python -m pytest tests/test_app_utils_fonts.py tests/test_systemd_units.py tests/test_uninstall_preserves_data.py -q
python -m ruff check src/utils/app_utils.py tests/test_app_utils_fonts.py
```

Expected: all focused tests pass and Ruff reports no errors.

Commit:

```powershell
git add inkypi-weather/package/InkyPi/src/utils/app_utils.py inkypi-weather/package/InkyPi/install/install.sh inkypi-weather/package/InkyPi/tests/test_app_utils_fonts.py inkypi-weather/package/InkyPi/tests/test_systemd_units.py inkypi-weather/package/InkyPi/tests/test_uninstall_preserves_data.py
git commit -m "feat: resolve base fonts from durable data"
```

---

### Task 2: Migrate independent base UI loaders

**Files:**
- Modify: `src/plugins/bambu_monitor/bambu_monitor.py`
- Modify: `src/plugins/box_office_top_movies/box_office_top_movies.py`
- Modify: `src/plugins/daily_ai_news/daily_ai_news.py`
- Modify: `src/plugins/dota_profile_dashboard/dota_profile_dashboard.py`
- Modify: `src/plugins/epaper_pet/epaper_pet.py`
- Modify: `src/plugins/flight_radar/flight_radar.py`
- Modify: `src/plugins/live_radar/live_radar.py`
- Modify: `src/plugins/lol_info/lol_info.py`
- Modify: `src/plugins/moon_phase/moon_phase.py`
- Modify: `src/plugins/pixiv_r18_ranking/pixiv_r18_ranking.py`
- Modify: `src/plugins/species_radar/species_radar.py`
- Modify: `src/plugins/sports_dashboard/common.py`
- Modify: `src/plugins/steam_charts/steam_charts.py`
- Modify: `src/plugins/steam_profile_dashboard/steam_profile_dashboard.py`
- Modify: `src/plugins/stocktracker/stocktracker.py`
- Modify: `src/plugins/telegram_digest/telegram_digest.py`
- Modify: `src/plugins/wow_profile_dashboard/wow_profile_dashboard.py`
- Modify: `src/plugins/comic/comic.py`
- Modify: `src/plugins/flow_progress/flow_progress.py`
- Modify: `src/plugins/simple_calendar/simple_calendar.py`
- Modify: `src/plugins/mini_weather/mini_weather.py`
- Modify: `src/plugins/github/github_contributions.py`
- Modify: `src/plugins/gcd_comic_covers/gcd_comic_covers.py`
- Modify: `src/plugins/magazine_covers/magazine_covers.py`
- Modify: `src/plugins/ai_text/render/ai_text.css`
- Modify: `src/plugins/calendar/render/calendar.css`
- Modify: `src/plugins/countdown/render/countdown.css`
- Modify: `src/plugins/github/render/github.css`
- Modify: `src/plugins/mini_weather/render/mini_weather.css`
- Modify: `src/plugins/rss/render/rss.css`
- Modify: `src/plugins/todo_list/render/todo_list.css`
- Modify: `src/plugins/weather/render/weather.css`
- Modify: `src/plugins/year_progress/render/year_progress.css`
- Create: `tests/test_base_ui_font_policy.py`
- Test: `tests/test_bambu_monitor.py`, `tests/test_box_office_top_movies.py`, `tests/test_daily_ai_news.py`, `tests/test_dota_profile_dashboard.py`, `tests/test_epaper_pet_context.py`, `tests/test_flight_radar.py`, `tests/test_live_radar.py`, `tests/test_lol_info.py`, `tests/test_moon_phase.py`, `tests/test_pixiv_r18_ranking.py`, `tests/test_species_radar.py`, `tests/test_sports_dashboard.py`, `tests/test_steam_charts.py`, `tests/test_steam_profile_dashboard_friend_status.py`, `tests/test_stocktracker.py`, `tests/test_telegram_digest.py`, `tests/test_wow_profile_dashboard.py`, `tests/test_flow_progress.py`, `tests/test_simple_calendar_holidays.py`, `tests/test_mini_weather_backgrounds.py`, `tests/test_gcd_comic_covers.py`, `tests/test_magazine_covers.py`

**Interfaces:**
- Consumes: `get_base_ui_font(size, bold=False)` from Task 1.
- Preserves: LoL `prefer_hangul` fallback and Pixiv/Noto Japanese fallback after YaHei.
- Preserves: `box_office_top_movies._load_font` and `steam_profile_dashboard._fonts` as structural helpers.

- [ ] **Step 1: Add failing loader-selection tests**

For each loader family, monkeypatch its imported `get_base_font` and assert ordinary UI text requests it with the correct size and bold flag. At minimum add permanent tests for SportsDashboard, LiveRadar, FlightRadar, LoL normal/Hangul, SteamCharts, TelegramDigest, and one dashboard plugin:

```python
def test_sports_dashboard_base_font_uses_shared_resolver(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        "plugins.sports_dashboard.common.get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
    )

    assert SportsDashboard._font(18, True) is sentinel
    assert calls == [(18, True)]
```

LoL's test must assert `prefer_hangul=True` can fall back to its Hangul-capable Noto font if the shared font cannot render the required glyph; Pixiv retains its Japanese Noto fallback. `test_base_ui_font_policy.py` must scan the nine CSS overrides and reject `font-family: Jost` outside the explicit Dogica/DS-Digital/Napoli allowlist, and scan the seven Python bypass files for ordinary `get_font("Jost", ...)` calls.

- [ ] **Step 2: Run focused loader tests and verify RED**

Run:

```powershell
python -m pytest tests/test_base_ui_font_policy.py tests/test_sports_dashboard.py tests/test_live_radar.py tests/test_flight_radar.py tests/test_lol_info.py tests/test_steam_charts.py tests/test_telegram_digest.py -q -k "font or yahei or cjk or hangul or base_ui_font_policy"
```

Expected: the shared-resolver and static policy assertions fail because local loaders and CSS overrides still choose Jost/LXGW/Noto directly.

- [ ] **Step 3: Replace ordinary base candidates with the shared resolver**

Each ordinary loader imports `get_base_font` and starts with:

```python
font = get_base_ui_font(int(size), bold=bool(bold))
if font is not None:
    return font
```

Remove duplicate YaHei path lists only when they serve ordinary UI copy. Change the nine base CSS overrides to `"Microsoft YaHei", "微软雅黑", Arial, sans-serif`. Keep the shared resolver's configured 400/700 variable-font instance intact rather than reloading or mutating it in plugin helpers. Keep explicit decorative assets, wordmarks, Hangul/Japanese glyph fallback branches, DS-Digital, Napoli, Dogica, and the three literary loaders intact.

- [ ] **Step 4: Run plugin suites and render smoke tests**

Run:

```powershell
python -m pytest tests/test_base_ui_font_policy.py tests/test_sports_dashboard.py tests/test_live_radar.py tests/test_flight_radar.py tests/test_lol_info.py tests/test_steam_charts.py tests/test_telegram_digest.py tests/test_bambu_monitor.py tests/test_moon_phase.py tests/test_flow_progress.py tests/test_simple_calendar_holidays.py tests/test_mini_weather_backgrounds.py tests/test_gcd_comic_covers.py tests/test_magazine_covers.py -q
python -m ruff check src/plugins/bambu_monitor src/plugins/box_office_top_movies src/plugins/daily_ai_news src/plugins/dota_profile_dashboard src/plugins/epaper_pet src/plugins/flight_radar src/plugins/live_radar src/plugins/lol_info src/plugins/moon_phase src/plugins/pixiv_r18_ranking src/plugins/species_radar src/plugins/sports_dashboard src/plugins/steam_charts src/plugins/steam_profile_dashboard src/plugins/stocktracker src/plugins/telegram_digest src/plugins/wow_profile_dashboard src/plugins/comic src/plugins/flow_progress src/plugins/simple_calendar src/plugins/mini_weather src/plugins/github src/plugins/gcd_comic_covers src/plugins/magazine_covers tests/test_base_ui_font_policy.py
```

Expected: plugin suites pass, including repeated MoonPhase renders in one clean process.

- [ ] **Step 5: Commit the independent-loader migration**

Stage only the Task 2 plugin, CSS, and test files, then commit:

```powershell
git commit -m "feat: use YaHei for plugin base UI text"
```

---

### Task 3: Migrate configurable defaults without overriding explicit choices

**Files:**
- Modify: `src/plugins/daily_art/daily_art.py`
- Modify: `src/plugins/daily_art/settings.html`
- Modify: `src/plugins/daily_knowledge/daily_knowledge.py`
- Modify: `src/plugins/daily_knowledge/settings.html`
- Modify: `src/plugins/daily_wiki_page/daily_wiki_page.py`
- Modify: `src/plugins/daily_word_poem/daily_word_poem.py`
- Modify: `src/plugins/daily_word_poem/settings.html`
- Modify: `src/plugins/tech_pulse/tech_pulse.py`
- Modify: `src/plugins/tech_pulse/settings.html`
- Test: `tests/test_daily_art.py`, `tests/test_daily_knowledge.py`, `tests/test_daily_wiki_page.py`, `tests/test_daily_word_poem.py`, `tests/test_tech_pulse.py`

**Interfaces:**
- Consumes: `DEFAULT_FONT_FAMILY` and `get_base_ui_font` from Task 1.
- Preserves: any non-empty saved `fontFamily`/`font` selection supplied by the user.

- [ ] **Step 1: Add failing default-vs-explicit tests**

For each configurable plugin assert an empty/missing font setting resolves to `Microsoft YaHei`, while an explicit `LXGW WenKai`, `Jost`, or literary family is passed unchanged to `get_font`:

```python
def test_default_font_is_yahei_but_explicit_choice_is_preserved(monkeypatch):
    calls = []
    monkeypatch.setattr(module, "get_font", lambda family, size, weight="normal": calls.append(family) or sentinel)
    plugin._font("", 18)
    plugin._font("Jost", 18)
    assert calls == ["Microsoft YaHei", "Jost"]
```

Settings tests must assert the empty/default option submitted by new instances is `Microsoft YaHei`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_daily_art.py tests/test_daily_knowledge.py tests/test_daily_wiki_page.py tests/test_daily_word_poem.py tests/test_tech_pulse.py -q -k "font or settings"
```

Expected: old Jost/LXGW defaults fail the YaHei assertions.

- [ ] **Step 3: Change only defaults and CJK fallback hooks**

Import `DEFAULT_FONT_FAMILY` for missing settings, route internal `__cjk__` sentinels through `get_base_ui_font`, and leave non-empty user selections untouched. Update the four settings templates so their initial selected value matches the Python default.

- [ ] **Step 4: Run tests, lint, and commit**

Run all five plugin suites plus Ruff on the ten changed source/template files. Commit:

```powershell
git commit -m "feat: default configurable text to YaHei"
```

---

### Task 4: Full verification, device-only font provisioning, and live acceptance

**Files:**
- No font binary is added to the worktree.
- Temporary device migration and proof scripts live under `.tmp/` only.

**Interfaces:**
- Consumes: completed Tasks 1-3 and the existing atomic release deployment flow.
- Produces: live `/var/lib/inkypi/data/fonts/msyh.ttf` and `msyhbd.ttf`, a ready release, and physical-screen proof.

- [ ] **Step 1: Run repository gates**

Run:

```powershell
python -m pytest -q
python -m ruff check src tests
python tools/verify_clean_archive.py --python G:\PersonalProjects\EpaperSystem\.tmp\inkypi-clean-verify-311\Scripts\python.exe
git diff --check
git status --short
```

Expected: all tests pass, clean-archive verification passes, and no `msyh*` binary appears in `git status` or `git archive`.

Both `install.sh` and `update.sh` call the single
`install/lib/release_archive.py` builder. Archive tests execute that real
builder through both production entry points, and `inspect_artifact()`
independently rejects nested, case-insensitive `msyh*.ttf`/`msyh*.ttc`
members.

- [ ] **Step 2: Install the device-owned fonts outside the release**

On the device, verify the source hashes first, then install without logging font bytes:

```bash
install -d -o root -g inkypi -m 0750 /var/lib/inkypi/data/fonts
install -o root -g inkypi -m 0640 /home/feeengyuuu/.local/share/fonts/microsoft/msyh.ttf /var/lib/inkypi/data/fonts/msyh.ttf
install -o root -g inkypi -m 0640 /home/feeengyuuu/.local/share/fonts/microsoft/msyhbd.ttf /var/lib/inkypi/data/fonts/msyhbd.ttf
```

Verify the service user can open both files and Pillow reports `Microsoft YaHei` regular/bold. Do not copy them into `/opt/inkypi/current`.

- [ ] **Step 3: Migrate only approved live legacy defaults**

Back up `device.json`, use the repository's transactional config path, and change only the five identified saved font fields whose values equal the old defaults (`Jost` or `LXGW WenKai`). Preserve every other explicit font selection. Re-read and diff the sanitized font fields before restarting.

- [ ] **Step 4: Deploy atomically and verify readiness**

Create a new hard-linked release from current, replace only tracked changed source/install files, run AST/preflight and `pip check`, switch `current`/`previous` atomically, restart, and require:

```text
ActiveState=active
SubState=running
Result=success
NRestarts=0
/readyz status=ready and release_id equals basename(readlink -f /opt/inkypi/current)
```

- [ ] **Step 5: Force fresh renders and inspect the physical result**

Render at least one HTML plugin and the PIL SportsDashboard through the production worker. Verify at runtime:

```python
assert ImageFont.truetype("/var/lib/inkypi/data/fonts/msyh.ttf", 18).getname()[0] == "Microsoft YaHei"
assert ImageFont.truetype("/var/lib/inkypi/data/fonts/msyhbd.ttf", 18).getname()[0] == "Microsoft YaHei"
```

Download the committed SportsDashboard image, verify its manifest reports hardware write success, and visually inspect Chinese team names, 7-10 px English metadata, bold headings, overflow, and the truthful `ESPN DATA`/EWC `ACTIVE` states.

- [ ] **Step 6: Record final evidence and cleanup**

Capture release ID, source/font hashes, service state, image ETag/commit ID, cache timestamps, and warnings. Remove only the known remote temporary deploy/proof files. Keep the previous release as rollback and do not push unless the user explicitly asks.
