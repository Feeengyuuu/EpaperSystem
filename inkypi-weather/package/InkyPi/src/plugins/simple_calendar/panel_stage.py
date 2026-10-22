"""The date panel's stage: its host and speech balloon under the month line.

panel_scene decides who appears and what they say; this mixin finds the art,
fits the host into a bottom corner the background leaves free, and letters the
balloon beside it without touching the date text above. Everything is drawn
like a comic print, in solid inks a six-colour panel shows cleanly: the host is
a sticker with a white rim, an ink edge and a hard drop shadow, plain lines sit
in a round balloon with a curved tail, an exclamation gets a jagged yellow
shout box, and an occasion puts a starburst behind the host.

Optional art is found by name, so new drawings take effect when dropped in:

- panel_cast/costumes/<occasion>.png replaces the host on an occasion
  (halloween, christmas, birthday, appointment, payday, launch ...);
- panel_cast/poses/<host>_<pose>.png gives an everyday host a pose
  (point, wave, sit, sleep);
- weather_panel_backgrounds_color/clean_plates/<weather>_<daypart>[_NN].png
  are backgrounds without figures that join the default pool for their time
  of day (weather: clear, cloudy, rain, snow, fog, thunderstorm).

Drawings in panel_cast face left. occupied_corners.json lists the bottom
corners where an older background already shows a figure: on ordinary days
that figure speaks instead of a host, and occasions prefer a free stage.
"""

from __future__ import annotations

import json
import logging
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from plugins.simple_calendar import panel_scene

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parent
DATE_HERO_CUTOUT_DIR = "date_hero_cutouts"
PANEL_CAST_DIR = "panel_cast"
COLOR_BACKGROUND_DIR = "weather_panel_backgrounds_color"
CLEAN_PLATE_DIR = "clean_plates"
OCCUPIED_CORNERS_FILE = "occupied_corners.json"
HOST_CUTOUTS = {
    "sky_runner": "hero_01_sky_runner.png",
    "shield_skater": "hero_02_shield_skater.png",
    "rocket_inventor": "hero_03_rocket_inventor.png",
    "umbrella_glider": "hero_04_umbrella_glider.png",
    "clockwork_robot": "hero_05_clockwork_robot.png",
    "lightning_messenger": "hero_06_lightning_messenger.png",
    "moon_magnet": "hero_07_moon_magnet.png",
}
# Which way each original cutout faces; the host is mirrored to face its balloon.
HOST_FACING = {"sky_runner": "right", "shield_skater": "left", "moon_magnet": "right"}
HOST_NO_MIRROR = {"rocket_inventor"}  # its chest badge carries a numeral
HOST_MAX_PX = 112
HOST_MIN_PX = 64
HOST_MAX_WIDTH_SHARE = 0.40
HOST_HALO_PX = 2
DAYPART_FALLBACKS = {
    "dawn": ("dawn", "day"),
    "day": ("day",),
    "dusk": ("dusk", "day"),
    "night": ("night",),
}
BUBBLE_FONT_SIZES = (15, 14, 13, 12)
BUBBLE_PAD_X = 13
BUBBLE_PAD_Y = 7
BUBBLE_TAIL_PX = 12
COMIC_SHADOW_PX = 3
COMIC_YELLOW = (255, 221, 0)
COMIC_RED = (214, 31, 38)
SHOUT_SPIKE_PX = 7
SHOUT_STEP_PX = 11
POP_POINTS = 14
ART_ERRORS = (OSError, ValueError)


def clean_plate_family(slug):
    return "clear" if str(slug or "").startswith("clear") else str(slug or "")


@lru_cache(maxsize=4)
def _occupied_corner_manifest(manifest_path):
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("Simple Calendar occupied-corner list unavailable: %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): frozenset(corner for corner in value if corner in {"bl", "br"})
        for key, value in data.items()
        if isinstance(value, list)
    }


def _boxes_overlap(first, second):
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


class PanelStageMixin:
    """Casts and draws the date panel's host; mixed into SimpleCalendar."""

    # Backgrounds ----------------------------------------------------------------

    def _clean_plate_candidates(self, slug, daypart):
        directory = PLUGIN_DIR / COLOR_BACKGROUND_DIR / CLEAN_PLATE_DIR
        family = clean_plate_family(slug)
        if not family or not directory.is_dir():
            return []
        for part in DAYPART_FALLBACKS.get(daypart, (daypart,)):
            found = sorted(
                path
                for pattern in (f"{family}_{part}.png", f"{family}_{part}_*.png")
                for path in directory.glob(pattern)
                if path.is_file()
            )
            if found:
                return list(dict.fromkeys(found))
        return []

    def _occupied_corners(self, background_path):
        """Bottom corners ("bl", "br") of a background that already show a figure."""
        if not background_path:
            return frozenset()
        color_dir = PLUGIN_DIR / COLOR_BACKGROUND_DIR
        try:
            key = Path(background_path).resolve().relative_to(color_dir).as_posix()
        except ValueError:
            return frozenset()
        return _occupied_corner_manifest(str(color_dir / OCCUPIED_CORNERS_FILE)).get(key, frozenset())

    # Art --------------------------------------------------------------------------

    def _date_hero_cutout_paths(self):
        directory = PLUGIN_DIR / DATE_HERO_CUTOUT_DIR
        if not directory.is_dir():
            return []
        return sorted(path for path in directory.glob("*.png") if path.is_file())

    def _host_art_path(self, scene):
        """(path, facing) of the drawing for a scene: costume, pose, then the host's cutout."""
        cast = PLUGIN_DIR / PANEL_CAST_DIR
        if scene.costume:
            path = cast / "costumes" / f"{scene.costume}.png"
            if path.is_file():
                return path, "left"
        if scene.pose:
            path = cast / "poses" / f"{scene.host}_{scene.pose}.png"
            if path.is_file():
                return path, "left"
        cutout = HOST_CUTOUTS.get(scene.host)
        path = PLUGIN_DIR / DATE_HERO_CUTOUT_DIR / cutout if cutout else None
        if path is not None and path.is_file():
            return path, HOST_FACING.get(scene.host)
        return None, None

    def _load_host_art(self, scene, face):
        """The scene's drawing cropped to its figure and turned to `face`, or None."""
        path, facing = self._host_art_path(scene)
        if path is None:
            return None
        try:
            with Image.open(path) as source:
                art = source.convert("RGBA")
        except ART_ERRORS as exc:
            logger.debug("Could not load Simple Calendar host art %s: %s", path, exc)
            return None
        bounds = art.getchannel("A").getbbox()
        if not bounds:
            return None
        art = art.crop(bounds)
        original_cutout = path.parent.name == DATE_HERO_CUTOUT_DIR
        mirrorable = not (original_cutout and scene.host in HOST_NO_MIRROR)
        if facing in {"left", "right"} and facing != face and mirrorable:
            art = ImageOps.mirror(art)
        return art

    # Layout -------------------------------------------------------------------------

    def _fit_host_box(self, art, side, stage, keep_out):
        """Largest box for the host in its bottom corner that clears every keep-out box."""
        left, _top, right, bottom = stage
        max_width = (right - left) * HOST_MAX_WIDTH_SHARE
        for height in range(HOST_MAX_PX, HOST_MIN_PX - 1, -4):
            width = round(art.width * height / art.height)
            if width > max_width:
                continue
            x0 = left if side == "left" else right - width
            box = (x0, bottom - height, x0 + width, bottom)
            if not any(_boxes_overlap(box, other) for other in keep_out):
                return box
        height = HOST_MIN_PX
        width = max(1, min(round(art.width * height / art.height), int(max_width)))
        height = max(1, round(art.height * width / art.width))
        x0 = left if side == "left" else right - width
        return (x0, bottom - height, x0 + width, bottom)

    def _bubble_layout(self, draw, lines, max_width):
        """(font, fitted lines, text width, line height) for the balloon's text."""
        font = None
        fitted = list(lines)
        for size in BUBBLE_FONT_SIZES:
            font = self._get_holiday_title_font(size, bold=True)
            widths = [self._text_width(draw, line, font) for line in lines]
            if max(widths) <= max_width:
                fitted = list(lines)
                break
        else:
            fitted = [self._fit_text(draw, line, font, max_width) for line in lines]
        size = getattr(font, "size", BUBBLE_FONT_SIZES[-1])
        text_width = max(self._text_width(draw, line, font) for line in fitted)
        return font, fitted, text_width, size + 5

    # Drawing ------------------------------------------------------------------------

    def _stage_colors(self, theme_palette, surface, ink, muted):
        """Comic print colours: solid inks that hold up on a six-colour panel."""
        if theme_palette:
            return {
                "fill": surface, "ink": ink, "shadow": muted,
                "shout": theme_palette["panel"], "pop": theme_palette["accent"], "pop_edge": ink,
            }
        return {
            "fill": (255, 255, 255), "ink": (0, 0, 0), "shadow": (0, 0, 0),
            "shout": COMIC_YELLOW, "pop": COMIC_YELLOW, "pop_edge": COMIC_RED,
        }

    def _draw_panel_stage(self, img, scene, stage, keep_out, background_path, colors, side_seed=0):
        """Draw the scene's host and balloon inside `stage` (left, top, right, bottom).

        `keep_out` lists boxes the host must not cover (the date text); the
        balloon stays below stage[1]. Returns the balloon's box.
        """
        if scene is None or not scene.lines:
            return None
        left, top, right, bottom = stage
        if right - left < 120 or bottom - top < 40:
            return None
        occupied = self._occupied_corners(background_path)
        shout = scene.lines[0].endswith("！")

        if scene.costume is None and occupied:
            # An ordinary day on a background that already has a figure in a
            # bottom corner: that figure speaks, and no host is added.
            speaker_side = "right" if "br" in occupied else "left"
            return self._draw_balloon(img, scene.lines, stage, speaker_side, None, colors, shout)

        if occupied == {"br"}:
            side = "left"
        elif occupied == {"bl"}:
            side = "right"
        else:
            side = "left" if side_seed % 2 == 0 else "right"
        art = self._load_host_art(scene, "right" if side == "left" else "left")
        if art is None:
            return self._draw_balloon(img, scene.lines, stage, side, None, colors, shout)
        host_box = self._fit_host_box(art, side, stage, keep_out)
        if scene.costume and scene.occasion != "appointment":
            # Special days only; an ordinary appointment stays calm.
            self._draw_pop_burst(img, host_box, stage, keep_out, colors)
        self._paste_host(img, art, host_box, colors)
        return self._draw_balloon(img, scene.lines, stage, side, host_box, colors, shout)

    def _paste_host(self, img, art, box, colors):
        """The host as a comic sticker: white rim, ink edge and a hard drop shadow."""
        width, height = box[2] - box[0], box[3] - box[1]
        figure = art.resize((width, height), Image.LANCZOS)
        pad = HOST_HALO_PX + 2
        alpha = Image.new("L", (width + pad * 2, height + pad * 2), 0)
        alpha.paste(figure.getchannel("A"), (pad, pad))
        solid = alpha.point(lambda value: 255 if value > 40 else 0)
        rim = solid.filter(ImageFilter.MaxFilter(HOST_HALO_PX * 2 + 1))
        edge = solid.filter(ImageFilter.MaxFilter(HOST_HALO_PX * 2 + 3))
        origin = (box[0] - pad, box[1] - pad)
        img.paste(colors["shadow"], (origin[0] + COMIC_SHADOW_PX, origin[1] + COMIC_SHADOW_PX), edge)
        img.paste(colors["ink"], origin, edge)
        img.paste(colors["fill"], origin, rim)
        img.paste(figure, (box[0], box[1]), figure)

    def _draw_pop_burst(self, img, host_box, stage, keep_out, colors):
        """A starburst behind the host on an occasion, kept off the date text."""
        width, height = host_box[2] - host_box[0], host_box[3] - host_box[1]
        cx = (host_box[0] + host_box[2]) / 2
        cy = host_box[1] + height * 0.45
        radius = max(width, height) * 0.66
        for box in keep_out:
            dx = max(box[0] - cx, 0, cx - box[2])
            dy = max(box[1] - cy, 0, cy - box[3])
            radius = min(radius, math.hypot(dx, dy) - 3)
        if radius < max(width, height) * 0.52:
            return
        points = []
        for index in range(POP_POINTS * 2):
            angle = math.pi * index / POP_POINTS - math.pi / 2
            reach = radius if index % 2 == 0 else radius * 0.70
            points.append((cx + math.cos(angle) * reach, cy + math.sin(angle) * reach))
        layer = Image.new("RGB", img.size, colors["pop"])
        ImageDraw.Draw(layer).polygon(points, outline=colors["pop_edge"], width=2)
        mask = Image.new("L", img.size, 0)
        clip = ImageDraw.Draw(mask)
        clip.polygon(points, fill=255)
        clip.rectangle([0, 0, stage[0] - 1, img.height], fill=0)
        clip.rectangle([stage[2] + 1, 0, img.width, img.height], fill=0)
        clip.rectangle([0, stage[3] + 1, img.width, img.height], fill=0)
        img.paste(layer, (0, 0), mask)

    def _draw_balloon(self, img, lines, stage, speaker_side, host_box, colors, shout=False):
        """A comic balloon pointing at the host (or at the background's figure).

        Plain lines get a round speech balloon; an exclamation gets a jagged
        shout box. Both carry an ink outline and a hard drop shadow.
        """
        draw = ImageDraw.Draw(img)
        left, top, right, bottom = stage
        spike = SHOUT_SPIKE_PX if shout else 0
        if host_box is not None:
            space_left = host_box[2] + BUBBLE_TAIL_PX if speaker_side == "left" else left
            space_right = right if speaker_side == "left" else host_box[0] - BUBBLE_TAIL_PX
        else:
            # The speaker stands in the opposite corner; keep clear of its figure.
            reach = int((right - left) * 0.70)
            space_left = left if speaker_side == "right" else right - reach
            space_right = left + reach if speaker_side == "right" else right
        space_right -= COMIC_SHADOW_PX
        max_text = space_right - space_left - 2 * (BUBBLE_PAD_X + spike)
        if max_text < 40:
            return None
        font, fitted, text_width, line_height = self._bubble_layout(draw, lines, max_text)
        width = text_width + 2 * (BUBBLE_PAD_X + spike)
        height = line_height * len(fitted) - 4 + 2 * (BUBBLE_PAD_Y + spike)
        if host_box is not None:
            head_y = host_box[1] + (host_box[3] - host_box[1]) * 0.28
            y0 = min(max(top, head_y - height / 2), bottom - height - COMIC_SHADOW_PX)
            x0 = space_left if speaker_side == "left" else space_right - width
        else:
            y0 = top + 2
            x0 = space_left if speaker_side == "right" else space_right - width
        box = (int(x0), int(y0), int(x0 + width), int(y0 + height))
        body = (box[0] + spike, box[1] + spike, box[2] - spike, box[3] - spike)

        tail = self._balloon_tail(body, speaker_side, host_box, stage)
        fill = colors["shout"] if shout else colors["fill"]
        outline = self._shout_outline(body, spike) if shout else None
        radius = (body[3] - body[1]) // 2
        shift = COMIC_SHADOW_PX

        draw.polygon([(x + shift, y + shift) for x, y in tail], fill=colors["shadow"])
        if outline:
            draw.polygon([(x + shift, y + shift) for x, y in outline], fill=colors["shadow"])
        else:
            draw.rounded_rectangle([value + shift for value in body], radius=radius, fill=colors["shadow"])
        draw.polygon(tail, fill=fill, outline=colors["ink"], width=2)
        if outline:
            draw.polygon(outline, fill=fill, outline=colors["ink"], width=2)
        else:
            draw.rounded_rectangle(body, radius=radius, fill=fill, outline=colors["ink"], width=2)
        # Open the balloon's outline where the tail joins it.
        draw.polygon(_shrink(tail, 2.5), fill=fill)

        # Lettered centred, as a comic letterer would.
        text_x = (body[0] + body[2]) / 2
        for index, line in enumerate(fitted):
            line_y = body[1] + BUBBLE_PAD_Y - 2 + line_height * index + line_height / 2
            draw.text((text_x, line_y), line, font=font, fill=colors["ink"], anchor="mm")
        return box

    @staticmethod
    def _balloon_tail(body, speaker_side, host_box, stage):
        """A curved comic tail from the balloon's lower side to the speaker."""
        x0, y0, x1, y1 = body
        height = y1 - y0
        if host_box is not None:
            head_y = host_box[1] + (host_box[3] - host_box[1]) * 0.36
            tip_y = min(max(head_y, stage[1] + 4), stage[3] - 4)
        else:
            tip_y = min(y1 + 10, stage[3] - 2)
        if speaker_side == "left":
            base_a, base_b = (x0 + 10, y0 + height * 0.42), (x0 + 26, y1 - 3)
            tip_x = host_box[2] - 5 if host_box is not None else x0 - BUBBLE_TAIL_PX - 4
        else:
            base_a, base_b = (x1 - 10, y0 + height * 0.42), (x1 - 26, y1 - 3)
            tip_x = host_box[0] + 5 if host_box is not None else x1 + BUBBLE_TAIL_PX + 4
        return _curved_wedge(base_a, base_b, (tip_x, tip_y))

    @staticmethod
    def _shout_outline(body, spike):
        """The jagged edge of a shout box: sawtooth spikes all round `body`."""
        x0, y0, x1, y1 = body
        width, height = x1 - x0, y1 - y0
        perimeter = 2 * (width + height)
        count = max(8, int(perimeter // SHOUT_STEP_PX) // 2 * 2)
        points = []
        for index in range(count):
            distance = perimeter * index / count
            if distance < width:
                x, y, nx, ny = x0 + distance, y0, 0, -1
            elif distance < width + height:
                x, y, nx, ny = x1, y0 + distance - width, 1, 0
            elif distance < 2 * width + height:
                x, y, nx, ny = x1 - (distance - width - height), y1, 0, 1
            else:
                x, y, nx, ny = x0, y1 - (distance - 2 * width - height), -1, 0
            out = spike * (0.65 + 0.35 * ((index * 7) % 5) / 4) if index % 2 else -1
            points.append((x + nx * out, y + ny * out))
        return points

    # Scene ---------------------------------------------------------------------------

    @staticmethod
    def _stage_side_seed(selected_date, scene):
        return selected_date.toordinal() + panel_scene.daypart_index(scene.daypart if scene else "day")


def _curved_wedge(base_a, base_b, tip, bend=0.16, steps=5):
    """A wedge from two base points to a tip, both sides bowed the same way."""
    def side(start):
        dx, dy = tip[0] - start[0], tip[1] - start[1]
        control = ((start[0] + tip[0]) / 2 - dy * bend, (start[1] + tip[1]) / 2 + dx * bend)
        return [
            (
                (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t * t * tip[0],
                (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t * t * tip[1],
            )
            for t in (step / steps for step in range(steps + 1))
        ]
    return side(base_a) + side(base_b)[::-1][1:]


def _shrink(points, amount):
    """Move every point `amount` pixels toward the polygon's centre."""
    cx = sum(x for x, _ in points) / len(points)
    cy = sum(y for _, y in points) / len(points)
    shrunk = []
    for x, y in points:
        distance = max(math.hypot(cx - x, cy - y), 1e-6)
        step = min(amount, distance) / distance
        shrunk.append((x + (cx - x) * step, y + (cy - y) * step))
    return shrunk
