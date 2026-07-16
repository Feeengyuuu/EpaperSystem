# World Cup Pixel Strip Gap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw the existing 248x13 World Cup pixel-pitch strip in the unused gap between `UPCOMING` and `RECENT` without moving either data section.

**Architecture:** Add a small placement helper that centers the native-size asset only when the supplied gap can contain it. Call the helper from the existing recent-section branch before rendering the bottom-anchored recent row.

**Tech Stack:** Python 3, Pillow, pytest, existing World Cup rendering mixin and local pixel asset.

## Global Constraints

- Reuse `assets/decor/worldcup_pitch_strip.png` at its native 248x13 size.
- Use nearest-neighbor pixel rendering through the existing `_draw_worldcup_pitch_strip` path.
- Do not move, resize, or reduce the existing upcoming and recent rows.
- Omit the decoration when its native size cannot fit.
- Preserve the existing no-recent tactics-strip path.
- A decorative-asset failure must not prevent match data from rendering.

---

### Task 1: Center the native strip in the data-free gap

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/worldcup_render.py:71-95,714-725`
- Test: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py:12801-12847,13032-13065`

**Interfaces:**
- Consumes: `_draw_worldcup_pitch_strip(image, draw, x1, y1, x2, y2) -> None` and the existing `upcoming_used_bottom`/`recent_y` coordinates.
- Produces: `_draw_worldcup_pitch_strip_in_gap(image, draw, x1, x2, gap_top, gap_bottom) -> bool`.

- [ ] **Step 1: Write failing placement and insufficient-space tests**

Add this focused helper test near the existing World Cup pitch-strip test:

```python
def test_worldcup_pitch_strip_gap_uses_native_size_and_omits_tight_gaps(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (556, 208), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    calls = []

    monkeypatch.setattr(
        plugin,
        "_draw_worldcup_pitch_strip",
        lambda _image, _draw, x1, y1, x2, y2: calls.append((x1, y1, x2, y2)),
    )

    assert plugin._draw_worldcup_pitch_strip_in_gap(image, draw, 282, 543, 112, 145) is True
    assert calls == [(289, 122, 536, 134)]

    assert plugin._draw_worldcup_pitch_strip_in_gap(image, draw, 282, 543, 140, 145) is False
    assert calls == [(289, 122, 536, 134)]
```

Add this integration test beside `test_worldcup_compact_panel_draws_recent_section_in_bottom_gap`:

```python
def test_worldcup_compact_panel_places_pitch_strip_between_upcoming_and_recent(monkeypatch):
    plugin = _plugin()
    now = datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc)

    def event(day, state, team_a, team_b, wins_a=None, wins_b=None):
        return {
            "start": datetime(2026, 6, day, 19, 0, tzinfo=timezone.utc),
            "state": state,
            "status": state,
            "team_a": team_a,
            "team_b": team_b,
            "team_a_tla": team_a[:3].upper(),
            "team_b_tla": team_b[:3].upper(),
            "team_a_flag": "",
            "team_b_flag": "",
            "wins_a": wins_a,
            "wins_b": wins_b,
            "block": "Group Stage",
        }

    main = event(13, "TIMED", "USA", "Mexico")
    second = event(14, "TIMED", "Brazil", "Morocco")
    third = event(15, "TIMED", "Canada", "Qatar")
    recent = event(11, "FT", "Mexico", "South Africa", 2, 1)
    selected = {
        "live": [],
        "upcoming": [main, second, third],
        "recent": [recent],
        "main": main,
        "visible_matches": 4,
    }
    upcoming_bottoms = []
    recent_tops = []
    pitch_boxes = []
    original_mini = plugin._draw_worldcup_mini_rows
    original_recent = plugin._draw_worldcup_recent_rows

    def record_mini(*args, **kwargs):
        bottom = original_mini(*args, **kwargs)
        upcoming_bottoms.append(bottom)
        return bottom

    def record_recent(image, draw, x1, x2, y, bottom, events):
        recent_tops.append(y)
        return original_recent(image, draw, x1, x2, y, bottom, events)

    monkeypatch.setattr(plugin, "_draw_worldcup_mini_rows", record_mini)
    monkeypatch.setattr(plugin, "_draw_worldcup_recent_rows", record_recent)
    monkeypatch.setattr(
        plugin,
        "_draw_worldcup_pitch_strip",
        lambda _image, _draw, x1, y1, x2, y2: pitch_boxes.append((x1, y1, x2, y2)),
    )

    plugin._render_worldcup_api_panel((556, 208), selected, "FOOTBALL LIVE", now, 4, now)

    assert recent_tops == [146]
    assert len(pitch_boxes) == 1
    x1, y1, x2, y2 = pitch_boxes[0]
    assert (x2 - x1 + 1, y2 - y1 + 1) == (248, 13)
    assert upcoming_bottoms[0] < y1 <= y2 < recent_tops[0]
```

- [ ] **Step 2: Run the new tests and verify the helper/call are absent**

Run:

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "pitch_strip_gap_uses_native_size or places_pitch_strip_between_upcoming_and_recent" -q
```

Expected: failures report that `_draw_worldcup_pitch_strip_in_gap` is missing and that no pitch box was drawn in the recent layout.

- [ ] **Step 3: Implement native-size gap placement**

Add this method immediately before `_draw_worldcup_pitch_strip`:

```python
def _draw_worldcup_pitch_strip_in_gap(self, image, draw, x1, x2, gap_top, gap_bottom):
    x1 = int(x1)
    x2 = int(x2)
    gap_top = int(gap_top)
    gap_bottom = int(gap_bottom)
    strip_width = 248
    strip_height = 13
    available_width = x2 - x1 + 1
    available_height = gap_bottom - gap_top + 1
    if available_width < strip_width or available_height < strip_height:
        return False
    strip_x1 = x1 + (available_width - strip_width) // 2
    strip_y1 = gap_top + (available_height - strip_height) // 2
    self._draw_worldcup_pitch_strip(
        image,
        draw,
        strip_x1,
        strip_y1,
        strip_x1 + strip_width - 1,
        strip_y1 + strip_height - 1,
    )
    return True
```

Call it from the existing recent branch without changing `recent_y`:

```python
if recent_rows:
    recent_y = max(upcoming_used_bottom + 1, content_bottom - 53)
    self._draw_worldcup_pitch_strip_in_gap(
        image,
        draw,
        right_x1,
        right_x2,
        upcoming_used_bottom + 1,
        recent_y - 1,
    )
    self._draw_worldcup_recent_rows(
        image,
        draw,
        right_x1,
        right_x2,
        recent_y,
        content_bottom,
        recent_rows,
    )
```

- [ ] **Step 4: Run focused and World Cup regression tests**

Run:

```powershell
tools\run_inkypi_tests.ps1 tests/test_sports_dashboard.py -k "worldcup" -q
```

Expected: all World Cup tests pass, including the existing native asset and no-lineup tactics-strip smoke test.

- [ ] **Step 5: Prepare the implementation for the root integration gate**

Review only the intended hunks:

```powershell
git diff --check -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/worldcup_render.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
git diff -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/worldcup_render.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py
```

Because the shared test file already contains approved uncommitted work, do not stage it from a subagent. The root agent will perform the integrated commit decision after all tests and live verification.
