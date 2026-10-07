# Steam Gilded Avatar Frame Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the Steam profile avatar in the console rail with the approved gilded frame (design `docs/superpowers/specs/2026-10-07-steam-gilded-avatar-frame-design.md`).

**Architecture:** A focused module draws the frame once per process at 4× supersampling and returns a cached RGBA overlay with a transparent avatar window. The console rail pastes the avatar, then the overlay, replacing the old navy outline. The dashboard style version moves to v40 with v39 kept compatible.

**Tech Stack:** Python 3.11+, Pillow 12 (`ImageDraw`, LANCZOS resize with automatic premultiplied alpha), pytest.

## Global Constraints

- Package root: `inkypi-weather/package/InkyPi` (all paths below are relative to it).
- Tests run with `G:\PersonalProjects\EpaperSystem\.tmp\venvs\inkypi-release-311-secure-20260904\Scripts\python.exe -m pytest -p no:cacheprovider`.
- Avatar stays 146×146 at (16, 16); the window `(16, 16, 162, 162)` must be fully transparent in the overlay.
- Overlay size `(180, 178)` placed at the rail origin; opaque pixels must stay within x < 180 and y < 178 (rail divider at x 180–182; persona name glyphs start near y 180).
- Strokes at least 2 px at final size; gems use pure red family so Spectra 6 maps them to red.
- Spectra 6 palette (from `display/waveshare_epd/epd7in3e.py`): `(0,0,0, 255,255,255, 255,255,0, 255,0,0, 0,0,0, 0,0,255, 0,255,0)`.
- No new binary assets; no network.

---

### Task 1: Gilded frame overlay module

**Files:**
- Create: `src/plugins/steam_profile_dashboard/avatar_frame.py`
- Test: `tests/test_steam_profile_avatar_frame.py`

**Interfaces:**
- Produces: `gilded_avatar_frame() -> PIL.Image.Image` (cached RGBA, size `FRAME_SIZE`); constants `FRAME_SIZE = (180, 178)`, `AVATAR_WINDOW = (16, 16, 162, 162)`.

- [ ] **Step 1: Write the failing tests**

```python
"""The rail avatar frame is a cached, e-paper-legible RGBA overlay."""

from PIL import Image

from plugins.steam_profile_dashboard.avatar_frame import (
    AVATAR_WINDOW,
    FRAME_SIZE,
    gilded_avatar_frame,
)

SPECTRA6 = (0, 0, 0, 255, 255, 255, 255, 255, 0, 255, 0, 0, 0, 0, 0, 0, 0, 255, 0, 255, 0)
CORNER_GEMS = ((10, 10), (168, 10), (10, 168), (168, 168))


def _is_gold(pixel):
    red, green, blue = pixel[:3]
    return red >= 150 and red >= green and green > blue + 40


def test_frame_is_a_cached_rgba_overlay_with_a_clear_avatar_window():
    frame = gilded_avatar_frame()

    assert frame.mode == "RGBA"
    assert frame.size == FRAME_SIZE
    assert gilded_avatar_frame() is frame
    assert frame.getchannel("A").crop(AVATAR_WINDOW).getextrema() == (0, 0)


def test_frame_stays_inside_the_rail_above_the_persona_name():
    left, top, right, bottom = gilded_avatar_frame().getchannel("A").getbbox()

    assert (left, top) >= (0, 0)
    assert right <= 180
    assert bottom <= 178


def test_frame_band_is_gold_with_ruby_corners_and_a_centred_crown():
    frame = gilded_avatar_frame()
    alpha = frame.getchannel("A")

    for xy in ((10, 60), (168, 120), (60, 168), (40, 10)):
        assert frame.getpixel(xy)[3] == 255
        assert _is_gold(frame.getpixel(xy))
    for xy in CORNER_GEMS:
        red, green, blue, opacity = frame.getpixel(xy)
        assert opacity == 255 and red > 150 and green < 90 and blue < 90
    assert alpha.crop((74, 0, 105, 4)).getbbox() is not None
    assert alpha.crop((25, 0, 60, 4)).getbbox() is None


def test_frame_survives_spectra6_dithering_as_yellow_gold_and_red_gems():
    canvas = Image.new("RGB", FRAME_SIZE, (0, 0, 0))
    frame = gilded_avatar_frame()
    canvas.paste(frame, (0, 0), frame)
    palette = Image.new("P", (1, 1))
    palette.putpalette(SPECTRA6 + (0, 0, 0) * 249)

    paper = canvas.quantize(palette=palette).convert("RGB")

    band_colors = paper.crop((6, 30, 15, 150)).getcolors()
    assert max(band_colors)[1] == (255, 255, 0)
    assert [paper.getpixel(xy) for xy in CORNER_GEMS] == [(255, 0, 0)] * 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -p no:cacheprovider tests/test_steam_profile_avatar_frame.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'plugins.steam_profile_dashboard.avatar_frame'`.

- [ ] **Step 3: Write the module**

```python
"""Gilded frame around the console rail avatar.

Drawn procedurally at 4x and downsampled once per process. The avatar window
stays fully transparent so the photo is never covered. Strokes are at least
2 px at final size, the gold is yellow-dominant and the gems are pure red so
the frame survives Spectra 6 dithering on the epd7in3e panel.
"""

from functools import lru_cache

from PIL import Image, ImageDraw


SCALE = 4
FRAME_SIZE = (180, 178)
AVATAR_WINDOW = (16, 16, 162, 162)
BAND_OUTER = (5, 5, 173, 173)
BAND_WIDTH = 10
CORNERS = ((10, 10), (168, 10), (10, 168), (168, 168))
STUDS = ((10, 89), (168, 89), (89, 168))
CROWN_CENTER_X = 89

GOLD_HIGHLIGHT = (255, 240, 170)
GOLD = (236, 190, 70)
GOLD_MID = (214, 160, 46)
GOLD_DEEP = (150, 100, 22)
BRONZE = (70, 42, 8)
RUBY = (214, 24, 36)
RUBY_HIGHLIGHT = (255, 130, 130)

# Colour across the band from its outer edge to its inner edge.
BAND_PROFILE = (
    (0.0, GOLD_DEEP),
    (0.2, GOLD_HIGHLIGHT),
    (0.5, GOLD),
    (0.78, GOLD_MID),
    (1.0, GOLD_DEEP),
)
# Light falls from the top-left, so the bottom and right sides sit in shade.
SIDE_SHADE = {"top": 1.0, "left": 0.94, "right": 0.8, "bottom": 0.72}


def _px(value):
    return round(value * SCALE)


def _mix(stops, position):
    for (start, low), (end, high) in zip(stops, stops[1:]):
        if position <= end:
            weight = 0.0 if end == start else (position - start) / (end - start)
            return tuple(round(a + (b - a) * weight) for a, b in zip(low, high))
    return stops[-1][1]


def _shade(color, factor):
    return tuple(round(channel * factor) for channel in color)


def _draw_band(draw):
    x0, y0, x1, y1 = (_px(value) for value in BAND_OUTER)
    width = _px(BAND_WIDTH)
    for step in range(width):
        color = _mix(BAND_PROFILE, step / (width - 1))
        # Insetting both ends of every line by its depth mitres the corners.
        draw.line((x0 + step, y0 + step, x1 - step, y0 + step), fill=_shade(color, SIDE_SHADE["top"]))
        draw.line((x0 + step, y1 - step, x1 - step, y1 - step), fill=_shade(color, SIDE_SHADE["bottom"]))
        draw.line((x0 + step, y0 + step + 1, x0 + step, y1 - step - 1), fill=_shade(color, SIDE_SHADE["left"]))
        draw.line((x1 - step, y0 + step + 1, x1 - step, y1 - step - 1), fill=_shade(color, SIDE_SHADE["right"]))
    draw.rectangle((x0, y0, x1, y1), outline=BRONZE, width=_px(1.5))
    draw.rectangle(tuple(_px(value) for value in (14.5, 14.5, 163.5, 163.5)), outline=BRONZE, width=_px(1.5))


def _diamond(draw, center_x, center_y, radius, fill):
    draw.polygon(
        (
            (center_x, center_y - radius),
            (center_x + radius, center_y),
            (center_x, center_y + radius),
            (center_x - radius, center_y),
        ),
        fill=fill,
    )


def _gem(draw, center_x, center_y, radius):
    ring = radius + _px(1.2)
    draw.ellipse((center_x - ring, center_y - ring, center_x + ring, center_y + ring), fill=BRONZE)
    draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill=RUBY)
    glint = radius * 0.38
    glint_x, glint_y = center_x - radius * 0.42, center_y - radius * 0.42
    draw.ellipse((glint_x - glint, glint_y - glint, glint_x + glint, glint_y + glint), fill=RUBY_HIGHLIGHT)


def _draw_corners(draw):
    for corner_x, corner_y in CORNERS:
        center_x, center_y = _px(corner_x), _px(corner_y)
        _diamond(draw, center_x, center_y, _px(9), BRONZE)
        _diamond(draw, center_x, center_y, _px(7.6), GOLD)
        _diamond(draw, center_x, center_y, _px(5.2), GOLD_HIGHLIGHT)
        _gem(draw, center_x, center_y, _px(3.2))


def _draw_studs(draw):
    for stud_x, stud_y in STUDS:
        center_x, center_y = _px(stud_x), _px(stud_y)
        for radius, fill in ((_px(3), BRONZE), (_px(2.1), GOLD_HIGHLIGHT)):
            draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill=fill)


def _draw_crown(draw):
    cx = CROWN_CENTER_X
    outline = (
        (cx - 15, 14.5), (cx - 14, 4.5), (cx - 10.5, 10), (cx - 7, 6.5), (cx - 3.5, 9.5),
        (cx, 1.5), (cx + 3.5, 9.5), (cx + 7, 6.5), (cx + 10.5, 10), (cx + 14, 4.5), (cx + 15, 14.5),
    )
    draw.polygon([(_px(x), _px(y)) for x, y in outline], fill=GOLD, outline=BRONZE, width=_px(1.2))
    draw.rectangle(tuple(_px(value) for value in (cx - 15.5, 11, cx + 15.5, 15)),
                   fill=GOLD_DEEP, outline=BRONZE, width=_px(1))
    for tip_x, tip_y in ((cx - 14, 4.5), (cx - 7, 6.5), (cx, 1.5), (cx + 7, 6.5), (cx + 14, 4.5)):
        center_x, center_y = _px(tip_x), _px(tip_y)
        for radius, fill in ((_px(1.9), BRONZE), (_px(1.2), GOLD_HIGHLIGHT)):
            draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill=fill)
    for dot_x in (cx - 9, cx + 9):
        center_x, center_y = _px(dot_x), _px(13)
        radius = _px(1.1)
        draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill=GOLD_HIGHLIGHT)
    _gem(draw, _px(cx), _px(12.6), _px(1.8))


@lru_cache(maxsize=1)
def gilded_avatar_frame():
    """Return the shared RGBA overlay; callers must paste it, never mutate it."""
    width, height = FRAME_SIZE
    canvas = Image.new("RGBA", (width * SCALE, height * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    _draw_band(draw)
    _draw_corners(draw)
    _draw_studs(draw)
    _draw_crown(draw)
    frame = canvas.resize(FRAME_SIZE, Image.Resampling.LANCZOS)
    canvas.close()
    # Resampling can spill a faint fringe inward; the photo window stays clear.
    frame.paste((0, 0, 0, 0), AVATAR_WINDOW)
    return frame
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest -p no:cacheprovider tests/test_steam_profile_avatar_frame.py -q`
Expected: `4 passed`. If the Spectra 6 band test is not yellow-dominant, adjust `BAND_PROFILE` stops toward `GOLD` (never relax the assertion).

- [ ] **Step 5: Commit**

```bash
git add src/plugins/steam_profile_dashboard/avatar_frame.py tests/test_steam_profile_avatar_frame.py
git commit -m "feat(steam): draw a gilded avatar frame overlay"
```

### Task 2: Use the frame in the console rail

**Files:**
- Modify: `src/plugins/steam_profile_dashboard/console_renderer.py` (imports; `_Console.rail()` avatar block around line 252–257)
- Modify: `src/plugins/steam_profile_dashboard/steam_profile_dashboard.py:39-44` (style version)
- Test: `tests/test_steam_profile_console_renderer.py`

**Interfaces:**
- Consumes: `gilded_avatar_frame()` from Task 1.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_steam_profile_console_renderer.py`)

```python
def test_rail_avatar_sits_inside_the_gilded_frame(plugin, data):
    from plugins.steam_profile_dashboard.avatar_frame import gilded_avatar_frame

    image = render_console(plugin, data, (800, 480))
    frame = gilded_avatar_frame()

    for xy in ((16, 16), (89, 89), (161, 161)):
        assert image.getpixel(xy) == (105, 120, 90)
    for xy in ((10, 60), (168, 120), (10, 168)):
        assert image.getpixel(xy) == frame.getpixel(xy)[:3]
    assert image.getpixel((176, 120)) == CANVAS


def test_style_version_moves_on_while_previous_cache_stays_compatible():
    from plugins.steam_profile_dashboard.steam_profile_dashboard import (
        STEAM_CACHED_DISPLAY_COMPATIBLE_STYLES,
        STEAM_DASHBOARD_STYLE_VERSION,
    )

    assert STEAM_DASHBOARD_STYLE_VERSION == "midnight-console-gilded-avatar-v40"
    assert STEAM_CACHED_DISPLAY_COMPATIBLE_STYLES[0] == "midnight-console-borderless-friends-v39"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -p no:cacheprovider tests/test_steam_profile_console_renderer.py -q -k "gilded_frame or style_version"`
Expected: 2 failures — band pixels are canvas/navy instead of gold, and the style version is still v39.

- [ ] **Step 3: Implement**

In `console_renderer.py` add the import next to `sidebar_asset`:

```python
from plugins.steam_profile_dashboard.avatar_frame import gilded_avatar_frame
```

Replace in `_Console.rail()`:

```python
        if avatar is not None:
            self.image.paste(avatar, (16, 16), avatar if avatar.mode == "RGBA" else None)
        self.draw.rounded_rectangle((13, 13, 164, 164), radius=4, outline=BORDER, width=2)
```

with:

```python
        if avatar is not None:
            self.image.paste(avatar, (16, 16), avatar if avatar.mode == "RGBA" else None)
        frame = gilded_avatar_frame()
        self.image.paste(frame, (0, 0), frame)
```

In `steam_profile_dashboard.py`:

```python
STEAM_DASHBOARD_STYLE_VERSION = "midnight-console-gilded-avatar-v40"
STEAM_CACHED_DISPLAY_COMPATIBLE_STYLES = (
    "midnight-console-borderless-friends-v39",
    "midnight-console-sidebar-generated-icons-v38",
    "midnight-console-black-base-aligned-v37",
    "midnight-console-official-assets-v36",
)
```

- [ ] **Step 4: Run the Steam test files**

Run: `python -m pytest -p no:cacheprovider -q tests/test_steam_profile_*.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/plugins/steam_profile_dashboard/console_renderer.py src/plugins/steam_profile_dashboard/steam_profile_dashboard.py tests/test_steam_profile_console_renderer.py
git commit -m "feat(steam): frame the profile avatar in gilded gold"
```

### Task 3: Visual and full verification

- [ ] Render the real dashboard image (`outputs/steam-avatar-frame-20261007/steam-current.png` avatar) through the new rail and a Spectra 6 simulation; inspect both at 2× before deployment.
- [ ] Run the full suite with `tools/run_inkypi_tests.ps1` and `ruff check` on changed files.
