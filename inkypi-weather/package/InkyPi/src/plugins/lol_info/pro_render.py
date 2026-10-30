"""Pages of the pro tracker: one LoL pro's account or one CS2 player, beside the board of all twenty.

Both pages share a 152 px hero band naming the player (champion splash or player photo
behind), then the player's recent games and numbers on the left and the board of the
twenty tracked players on the right, the shown one highlighted. The CS2 page stands on
the player's best map (official loading art) and shows the guns of their recent loadout.
"""

from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from datetime import datetime
from functools import lru_cache, partial

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .hltv_players import ROLE_LABELS as CS2_ROLE_LABELS
from plugins.sports_dashboard import cs2_branding
from plugins.sports_dashboard.common import (
    LOCAL_CS2_TEAM_LOGO_DIR,
    LOCAL_MSI_LOGO_PATH,
    LOCAL_WORLDS_LOGO_PATH,
    LOCAL_WORLDS_WORDMARK_PATHS,
)
from plugins.sports_dashboard.esports_font_runs import EsportsFont
from plugins.sports_dashboard.esports_fonts import active_font, type_scope
from plugins.sports_dashboard.score_fonts import FONT_DIR as SCORE_FONT_DIR
from utils.app_utils import get_base_ui_font
from utils.safe_image import safe_open_image
from utils.fonts import load_font

from . import lolesports_pro
from .bo3_stats import MAP_NAMES, WIN_DAYS
from .hltv_players import ROLE_NAMES as CS2_ROLES
from .pro_roster import ROLE_LABELS, SERVER_FLAGS, TEAM_LEAGUES
from .pro_tracker import LOL_FORM_AXES

HERO_H = 152
# The LoL band: the pro's photo (its transparent margins trimmed) at the left at nearly the
# band's height, the ID and the filed details beside it, then the game's logo in its own
# column centred on the photo's height, then the rank.
PHOTO_BOX = (190, HERO_H - 6)
GAME_LOGO_COLUMN = (406, 548)
INFO_LABEL_W = 34
# The team's league logo and the account server's flag fit these boxes in the identity rows.
LEAGUE_MARK, SERVER_FLAG = (40, 18), (22, 15)
# Leaguepedia's face for a player's ID (Rubik, SIL OFL).
ID_FONT = "fonts/Rubik-Bold.ttf"
# The LoL page's Latin and figures in Cinzel (SIL OFL), an open inscriptional face in the manner of
# the client's own Beaufort; Chinese stays in the page's face. Weights by role.
LOL_FACE = "fonts/Cinzel-VF.ttf"
LOL_FACE_WEIGHTS = {"hero": 900, "big": 700, "section": 700, "body": 700, "small_bold": 700, "text": 600,
                    "small": 600, "tiny": 600}
LOL_ID_WEIGHT = 900
# The client's own Beaufort for LOL by weight, when the owner installed it in the device's data
# fonts (install/font_dlc.json lol_pro_*; never packaged); Cinzel stands in without it.
LOL_BRAND_FILES = {900: "lol-beaufort-heavy.ttf", 700: "lol-beaufort-bold.ttf", 600: "lol-beaufort-medium.ttf"}
# The splash keeps the band's framing and runs on down the page under the boards, dimmed,
# fading into the page over the last LOL_SPLASH_TAIL rows it covers.
LOL_PAGE_DIM, LOL_SPLASH_TAIL = 0.42, 96
# The cards' backdrops follow what they show: the tracked team's logo behind the board, the
# latest game's champion behind the recent games, and on the CS2 page a radar sweep turned
# onto the player's best role, lit only inside the player's own role shape.
CARD_MARK_ALPHA, CARD_ART_ALPHA = 0.15, 0.36
CHAMPION_LOADING_URL = "https://ddragon.leagueoflegends.com/cdn/img/champion/loading/{key}_0.jpg"
RADAR_TRAIL, RADAR_SWEEP_ALPHA = 70, 0.34
# The CS2 role card is a tactical screen: a dotted grid every GRID_STEP px, gold brackets of
# HUD_ARM px on its corners, and the role chart (radius RADAR_RADIUS, centred RADAR_TOP below
# the card's top) on a scope of SCOPE_RADIUS, drawn RADAR_SCALE times larger and scaled down;
# the role labels sit RADAR_LABEL radii out.
GRID_STEP, HUD_ARM = 14, 11
RADAR_SCALE, RADAR_RADIUS, SCOPE_RADIUS, RADAR_TOP, RADAR_LABEL = 3, 56, 94, 132, 1.3
# The slot under the results: the team's latest win over a photo of the player, else the photo,
# cut round where the picture's detail gathers and zoomed in by PHOTO_ZOOM.
PHOTO_ZOOM = 1.18
# The win under its photo: the tag row, the opponent line, one row per map (its art chip
# WIN_MAP_CHIP), and the rows over which the photo fades into the page above the text.
WIN_TAG_H, WIN_HEADER_H, WIN_ROW_H, WIN_MAP_CHIP, WIN_FADE = 26, 20, 16, (24, 13), 26
# The LoL card: the ranked form chart's axes and the tag each one's strength earns; the pro
# corner's champion splash and dragon colours; pro results older than LOL_STAGE_DAYS (and a
# next series further off) count as out of season.
LOL_FORM_LABELS = {"damage": "输出", "farm": "发育", "teamfight": "参团", "vision": "视野", "survival": "生存",
                   "kills": "击杀"}
LOL_ARCHETYPES = {"damage": "输出核心", "farm": "发育机器", "teamfight": "团战核心", "vision": "视野大师",
                  "survival": "稳健运营", "kills": "收割者"}
CHAMPION_CENTERED_URL = "https://ddragon.leagueoflegends.com/cdn/img/champion/centered/{key}_0.jpg"
DRAGON_COLOURS = {"infernal": (226, 88, 52), "mountain": (176, 132, 86), "ocean": (62, 152, 214),
                  "cloud": (196, 214, 228), "hextech": (88, 196, 214), "chemtech": (118, 196, 78), "elder": (236, 198, 112)}
# The event's official mark heading the pro's schedule stands this tall at most.
EVENT_MARK_HEIGHT = 16
LOL_STAGE_DAYS = 45
# A match's event logo fits this box before an upcoming match's event name, and the narrower
# one at a result's end, which leaves the opponent's name its room.
CS2_EVENT_LOGO, CS2_RESULT_EVENT_LOGO = (30, 15), (24, 15)
# The CS2 page's matches in one scoreboard family (the sports dashboard's open Barlow faces):
# Condensed for scores, times and figures, Semi Condensed for team names and labels.
MATCH_FACES = {"score": "BarlowCondensed-Bold.ttf", "figure": "BarlowCondensed-SemiBold.ttf",
               "name": "BarlowSemiCondensed-Bold.ttf", "label": "BarlowSemiCondensed-SemiBold.ttf"}
# The rating's digits: their height and the baseline and left edge they stand on.
CS2_RATING_DIGITS, CS2_RATING_AT = 35, (620, 83)
# HLTV's own mark leads the Top 20 board's title.
HLTV_LOGO, BOARD_LOGO_SIZE = "hltv-logo.png", 22
# The CS2 page's middle carries the loadout, so its game logo keeps the band's top-left corner
# and the bodyshot starts below it.
TITLE_H = 42
LEFT = (14, 160, 398, 470)
RIGHT = (408, 160, 786, 470)
TIER_SHORT = {"CHALLENGER": "王者", "GRANDMASTER": "宗师", "MASTER": "大师", "DIAMOND": "钻石", "EMERALD": "翡翠",
              "PLATINUM": "铂金", "GOLD": "黄金", "SILVER": "白银", "BRONZE": "青铜", "IRON": "黑铁"}
APEX_TIERS = {"CHALLENGER", "GRANDMASTER", "MASTER"}
# The CS2 page lies on the player's best map: the page dimmed and softened, the hero band
# brighter, the panels frosted over it so their text keeps its contrast.
CS2_PAGE_DIM, CS2_HERO_DIM, CS2_PAGE_BLUR = 0.36, 0.62, 1.6
CS2_PANEL_ALPHA = 0.86
# The bright band dissolves into the dimmed page over these rows, and the bodyshot's foot
# fades out over its last CS2_PHOTO_FADE rows, so neither ends on a hard line.
CS2_HERO_FADE = (HERO_H - 40, HERO_H + 16)
CS2_PHOTO_FADE = 22
# From this Sniping score the AWP leads the loadout; otherwise the T rifle does.
SNIPER_ROLE = 40
# A team without any logo gets its initials in white on one of these grounds (chosen by its name),
# each clear of the CS2 page's dark panels.
TEAM_BADGE_FILLS = ((37, 99, 235), (190, 40, 52), (15, 118, 92), (118, 70, 196), (196, 110, 18))
CS2_RIFLES = {"t": ("AK-47", "Galil AR", "SG 553"), "ct": ("M4A1-S", "M4A4", "FAMAS", "AUG")}
CS2_PISTOLS = ("Desert Eagle", "USP-S", "Glock-18", "P250", "Five-SeveN", "Tec-9", "CZ75-Auto", "R8 Revolver")
# A player's role tag: the AWP from this Sniping score, else their strongest other role.
CS2_ARCHETYPES = {"Sniping": "主狙手", "Firepower": "火力核心", "Entrying": "突破手", "Trading": "补枪手",
                  "Opening": "首杀手", "Closing": "残局专家", "Utility": "道具手"}
# A role score this far off the Top 20 average reads as a strength (gold) or a gap (muted).
ROLE_MARGIN = 5
MEDALS = ((255, 205, 54), (206, 214, 226), (214, 134, 62))
# The board's rating bars run over this span of Rating 3.0.
RATING_SPAN = (0.85, 1.40)
TEAM_NAME_NOISE = frozenset({"team", "esports", "esport", "gaming", "club", "gg"})
TEAM_ALIASES = {"navi": "natusvincere"}


def _team_key(name):
    plain = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    key = "".join(word for word in re.findall(r"[a-z0-9]+", plain.lower()) if word not in TEAM_NAME_NOISE)
    return TEAM_ALIASES.get(key, key)


@lru_cache(maxsize=64)
def _team_badge(name, size):
    """A team with no logo anywhere: its initials (or its short name) in white on a rounded square
    of a colour its name picks; None for an opponent still to be decided."""
    name = str(name or "").strip()
    words = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii"))
    if not words or name.upper() in {"TBD", "TBA"}:
        return None
    joined = "".join(words)
    label = (joined if len(joined) <= 3 else "".join(word[0] for word in words)[:3] if len(words) > 1
             else joined[:2]).upper()
    fill = TEAM_BADGE_FILLS[sum((index + 1) * ord(char) for index, char in enumerate(name)) % len(TEAM_BADGE_FILLS)]
    scale = 4
    side = size * scale
    badge = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.rounded_rectangle((0, 0, side - 1, side - 1), radius=side // 4, fill=(*fill, 255))
    for points in range(round(side * 0.62), 6, -2):
        font = get_base_ui_font(points, bold=True)
        if draw.textlength(label, font=font) <= side * 0.84:
            break
    draw.text((side / 2, side / 2), label, font=font, fill=(255, 255, 255, 255), anchor="mm")
    return badge.resize((size, size), Image.Resampling.LANCZOS)


@lru_cache(maxsize=16)
def _load_id_font(path, size):
    return load_font(path, size)


@lru_cache(maxsize=1)
def _bundled_team_logos():
    """{team key: path} of the CS2 logos the sports dashboard ships (HLTV serves some as SVG)."""
    try:
        with open(os.path.join(LOCAL_CS2_TEAM_LOGO_DIR, "manifest.json"), encoding="utf-8") as handle:
            teams = json.load(handle).get("teams", {})
    except (OSError, ValueError, AttributeError):
        return {}
    index = {}
    for team in teams.values() if isinstance(teams, dict) else []:
        filename = str((team or {}).get("slug_file") or "")
        if re.fullmatch(r"[a-z0-9_-]+\.png", filename):
            for value in (team.get("name"), filename[:-4]):
                index.setdefault(_team_key(value), os.path.join(LOCAL_CS2_TEAM_LOGO_DIR, filename))
    return index


@lru_cache(maxsize=32)
def _lol_face(path, size, weight):
    """Cinzel at ``size`` and ``weight``."""
    font = load_font(path, size)
    font.set_variation_by_axes([weight])
    return font


@lru_cache(maxsize=32)
def _brand_face(path, size):
    return load_font(path, size)


def _lol_type(plugin_dir, size, weight):
    """The LoL page's face at ``size`` and ``weight``: the owner's Beaufort for LOL file for that
    weight (LOL_BRAND_FILES) when installed, else the bundled Cinzel."""
    data = os.environ.get("INKYPI_DATA_DIR", "").strip()
    brand = os.path.join(data, "fonts", LOL_BRAND_FILES[weight]) if data else ""
    if brand and os.path.isfile(brand):
        try:
            return _brand_face(brand, size)
        except OSError:
            pass
    return _lol_face(os.path.join(plugin_dir, "assets", LOL_FACE), size, weight)


@lru_cache(maxsize=48)
def _match_face(kind, size):
    """``kind``'s Barlow face at ``size``, the page's own face standing in for what it lacks
    (Chinese)."""
    fallback = get_base_ui_font(size, bold=kind in ("score", "name"))
    return EsportsFont(load_font(str(SCORE_FONT_DIR / MATCH_FACES[kind]), size), fallback)


class ProRenderMixin:
    # -- shared pieces -------------------------------------------------------------------
    def _match_text(self, draw, xy, text, kind, size, fill, *, width=None, min_size=None, anchor="lm"):
        """``text`` in the matches' scoreboard type (MATCH_FACES), a point smaller at a time down
        to ``min_size`` while wider than ``width``; returns the width it took."""
        text = str(text or "")
        font = _match_face(kind, size)
        while width is not None and size > (min_size or size) and font.getlength(text) > width:
            size -= 1
            font = _match_face(kind, size)
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)
        return font.getlength(text)

    def _pro_fonts(self):
        return {"hero": self._font(38, bold=True), "big": self._font(26, bold=True), "section": self._font(16, bold=True),
                "body": self._font(14, bold=True), "text": self._font(13), "small": self._font(11),
                "small_bold": self._font(11, bold=True), "tiny": self._font(10)}

    def _hero_shade(self, image, colors, height=HERO_H):
        """Darken the band (its top ``height`` rows) from the left so the name reads over any art."""
        width = image.width
        shade = Image.new("RGB", (width, height), colors["background"])
        mask = Image.linear_gradient("L").rotate(90, expand=True).resize((width, height))
        mask = mask.point(lambda value: int(70 + (255 - value) * 0.72))
        band = image.crop((0, 0, width, height))
        image.paste(Image.composite(shade, band, mask), (0, 0))

    def _team_logo(self, key, size):
        return self._asset_logo(f"teams/{str(key or '').lower()}.png", (size, size))

    def _layered_backdrop(self, image, canvas, colors, hero_dim, page_dim, cover=None):
        """Lay ``canvas`` (the page-sized art) under the page: bright and sharp in the hero band,
        easing across CS2_HERO_FADE into a dimmed, softened page; ``cover`` (L) fades it into the
        page colour where the art runs out."""
        base = Image.new("RGB", image.size, colors["background"])
        page = Image.blend(base, canvas.filter(ImageFilter.GaussianBlur(CS2_PAGE_BLUR)), page_dim)
        hero = Image.blend(base, canvas, hero_dim)
        self._hero_shade(hero, colors, height=image.height)
        start, end = CS2_HERO_FADE
        rows = Image.new("L", (1, image.height), 0)
        for y in range(image.height):
            t = min(1.0, max(0.0, (y - start) / (end - start)))
            rows.putpixel((0, y), round(255 * (1 - t * t * (3 - 2 * t))))
        layered = Image.composite(hero, page, rows.resize(image.size))
        image.paste(layered if cover is None else Image.composite(layered, base, cover))

    def _lol_backdrop(self, image, art, colors):
        """The splash framed as the band always showed it, carried on down the page at that scale."""
        scale = max(image.width / art.width, HERO_H / art.height)
        scaled = art.resize((round(art.width * scale), round(art.height * scale)), Image.Resampling.LANCZOS)
        # The band's crop of the splash (centred like ImageOps.fit at (0.62, 0.3)).
        x, y = round((image.width - scaled.width) * 0.62), round((HERO_H - scaled.height) * 0.3)
        canvas = Image.new("RGB", image.size, colors["background"])
        canvas.paste(scaled, (x, y))
        bottom = min(image.height, y + scaled.height)
        ramp = Image.new("L", (1, image.height), 0)
        for row in range(max(0, y), bottom):
            ramp.putpixel((0, row), round(255 * min(1.0, (bottom - row) / LOL_SPLASH_TAIL)))
        self._layered_backdrop(image, canvas, colors, 1.0, LOL_PAGE_DIM, ramp.resize(image.size))

    @staticmethod
    def _wash(image, art, origin, alpha, box):
        """Lay ``art`` (RGBA) faintly at ``origin``, its alpha scaled by ``alpha``, clipped to ``box``."""
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        layer.paste(art, origin)
        clip = Image.new("L", image.size, 0)
        ImageDraw.Draw(clip).rectangle((box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1), fill=round(255 * alpha))
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), clip))
        base = image.convert("RGBA")
        base.alpha_composite(layer)
        image.paste(base.convert("RGB"))

    def _team_mark(self, image, logo, box):
        """The tracked player's team logo, large and faint, standing in the card's lower right."""
        if logo is None:
            return
        logo = logo.convert("RGBA")
        self._wash(image, logo, (box[2] - 16 - logo.width, box[3] - 12 - logo.height), CARD_MARK_ALPHA, box)

    def _champion_wash(self, image, key, box):
        """The latest game's champion (official loading art) along the card's right, fading leftward."""
        art = self._image_from_url(CHAMPION_LOADING_URL.format(key=key), key) if key else None
        if art is None:
            return
        art = ImageOps.contain(art.convert("RGBA"), (box[2] - box[0], box[3] - box[1] - 2), method=Image.Resampling.LANCZOS)
        fade = Image.linear_gradient("L").rotate(90, expand=True)
        art.putalpha(ImageChops.multiply(art.getchannel("A"), fade.resize(art.size)))
        self._wash(image, art, (box[2] - 1 - art.width, box[1] + 1), CARD_ART_ALPHA, box)

    def _id_font(self, size):
        """The pro's ID in the LoL page's face at its heaviest, else Leaguepedia's Rubik."""
        try:
            return _lol_type(self.get_plugin_dir(), int(size), LOL_ID_WEIGHT)
        except OSError:
            pass
        path = os.path.join(self.get_plugin_dir(), "assets", ID_FONT)
        try:
            return _load_id_font(path, int(size))
        except OSError:
            return self._font(size, bold=True)

    def _draw_id(self, draw, position, text, fill, max_width, size, min_size):
        """The player's ID in Leaguepedia's face, shrunk until it fits ``max_width``."""
        text = str(text or "-")
        for current in range(size, min_size - 1, -1):
            font = self._id_font(current)
            if self._text_width(draw, text, font) <= max_width:
                break
        draw.text(position, text, font=font, fill=fill)
        return font

    def _board_frame(self, draw, box, title, detail, colors, fonts, fill=True, image=None, logo=None):
        """The board's frame and title line; with ``logo`` (an asset) the source's mark leads the title."""
        x0, y0, x1, y1 = box
        draw.rectangle(box, fill=colors["panel"] if fill else None, outline=colors["rule"], width=1)
        title_x = x0 + 12
        mark = self._asset_logo(logo, (BOARD_LOGO_SIZE, BOARD_LOGO_SIZE)) if image is not None and logo else None
        if mark is not None:
            image.paste(mark, (title_x, y0 + 7 + (BOARD_LOGO_SIZE - mark.height) // 2), mark)
            title_x += mark.width + 6
        self._text(draw, (title_x, y0 + 7), title, fonts["section"], colors["ink"])
        self._single(draw, (title_x + self._text_width(draw, title, fonts["section"]) + 8, y0 + 12), detail,
                     fonts["tiny"], colors["muted"], x1 - title_x - 128, 8)

    def _board_cells(self, box, count=20, rows=10):
        x0, y0, x1, y1 = box
        top, column_w = y0 + 34, (x1 - x0 - 24) / 2
        row_h = (y1 - 8 - top) / rows
        for index in range(min(count, rows * 2)):
            column, row = divmod(index, rows)
            left = x0 + 12 + column * column_w
            yield index, (left, top + row * row_h, left + column_w - 6, top + (row + 1) * row_h)

    def _highlight(self, draw, cell, colors):
        """The tracked player's row: a solid deep-gold bar with a bright gold edge on its left."""
        box = (cell[0] - 6, cell[1] + 2, cell[2] + 3, cell[3] - 2)
        fill = tuple(round(gold * 0.32 + panel * 0.68) for gold, panel in zip(colors["gold"], colors["panel"]))
        draw.rounded_rectangle(box, radius=6, fill=fill)
        draw.rounded_rectangle((box[0], box[1], box[0] + 4, box[3]), radius=2, fill=colors["gold"])

    def _tracker_badge(self, draw, pick, colors, fonts, right=786, y=134):
        text = f"随机追踪 {pick.get('position', 1)}/{pick.get('size', 1)} · {datetime.now():%H:%M}"
        self._text(draw, (right - self._text_width(draw, text, fonts["tiny"]), y), text, fonts["tiny"], colors["muted"])

    # -- LoL ----------------------------------------------------------------------------
    def _lol_fonts(self):
        """The page's fonts with their Latin and figures in the client's Beaufort (Cinzel without it,
        see _lol_type), each paired with the page's own face for Chinese; the plain fonts when
        neither file is there."""
        fonts = self._pro_fonts()
        try:
            return {key: EsportsFont(_lol_type(self.get_plugin_dir(), font.size, LOL_FACE_WEIGHTS.get(key, 600)), font)
                    for key, font in fonts.items()}
        except OSError:
            return fonts

    def _render_lol_pro(self, data, dimensions, theme_context, pick, board):
        colors = self._render_colors(theme_context)
        fonts = self._lol_fonts()
        image = Image.new("RGB", (800, 480), colors["background"])
        draw = ImageDraw.Draw(image)
        account = (pick or {}).get("account") or {}
        ranked = data.get("ranked") or {}

        art = self._choose_skin_art(data)
        raw = self._image_from_url((art or {}).get("splash_url"), (art or {}).get("skin_name"))
        if raw is not None:
            # The splash runs on down the page, under the frosted cards.
            self._lol_backdrop(image, ImageOps.exif_transpose(raw).convert("RGB"), colors)
        else:
            self._hero_shade(image, colors)
        draw = ImageDraw.Draw(image)
        # The pro's official lolesports photo, its transparent margins trimmed, fills the band's left.
        photo = self._image_from_url(pick.get("photo"), account.get("label")) if pick.get("photo") else None
        left, top = 18, 10
        if photo is not None:
            photo = ImageOps.contain(self._trim_alpha(photo.convert("RGBA")), PHOTO_BOX, method=Image.Resampling.LANCZOS)
            photo = self._fade_foot(photo)
            top = HERO_H - photo.height
            image.paste(photo, (8, top), photo)
            left = max(150, 8 + photo.width + 14)
        column_left, column_right = GAME_LOGO_COLUMN
        self._draw_lol_identity(image, draw, account, (left, top, column_left - 14 - left), colors, fonts)
        # The game's logo in its own column, centred on the photo's height.
        logo = self._asset_logo("league-of-legends-logo.png", (column_right - column_left - 12, HERO_H - top - 24))
        if logo:
            middle = (top + HERO_H) // 2
            image.paste(logo, ((column_left + column_right - logo.width) // 2, middle - logo.height // 2), logo)

        emblem = self._rank_emblem_image(ranked, 88) if ranked else None
        if emblem:
            image.paste(emblem, (558, 26 + (88 - emblem.height) // 2), emblem)
        wins, losses = int(ranked.get("wins") or 0), int(ranked.get("losses") or 0)
        self._text(draw, (652, 30), self._rank_text(ranked) or "暂无排位", fonts["body"], colors["gold"])
        self._text(draw, (652, 52), f"{int(ranked.get('leaguePoints') or 0):,} LP" if ranked else "-", fonts["big"],
                   colors["ink"])
        if ranked:
            rate = wins / max(1, wins + losses) * 100
            self._text(draw, (652, 88), f"{wins}胜 {losses}负 · {rate:.1f}%", fonts["small"],
                       colors["green"] if rate >= 50 else colors["muted"])
        active = data.get("active_game")
        if active:
            draw.rounded_rectangle((690, 10, 786, 32), radius=11, fill=colors["green"])
            self._text(draw, (704, 13), "对局中", fonts["small_bold"], colors["background"])
        self._tracker_badge(draw, pick, colors, fonts)

        self._draw_lol_card(image, draw, data, account, colors, fonts)
        self._draw_lol_board(image, draw, board, RIGHT, account, colors, fonts)
        return image if tuple(dimensions) == (800, 480) else image.resize(tuple(dimensions), Image.Resampling.LANCZOS)

    def _draw_lol_identity(self, image, draw, account, frame, colors, fonts):
        """The pro's ID in Leaguepedia's face, then their details filed by kind: team, role, account."""
        left, top, width = frame
        self._draw_id(draw, (left - 2, top + 8), account.get("label") or "-", colors["ink"], width, 48, 24)
        value_x = left + INFO_LABEL_W
        right = left + width
        y = top + 70

        def label(text):
            self._text(draw, (left, y + 2), text, fonts["small"], colors["muted"])

        team = str(account.get("team") or "")
        if team:
            label("战队")
            x = value_x
            logo = self._team_logo(team, 20)
            if logo:
                image.paste(logo, (x, y + 8 - logo.height // 2), logo)
                x += logo.width + 6
            self._single(draw, (x, y), team, fonts["body"], colors["ink"], right - x, 9)
            # The team's league logo follows its name.
            league = TEAM_LEAGUES.get(team.upper())
            mark = self._asset_logo(f"leagues/{league}.png", LEAGUE_MARK) if league else None
            if mark:
                image.paste(mark, (x + self._text_width(draw, team, fonts["body"]) + 8, y + 9 - mark.height // 2), mark)
            y += 24
        role = str(account.get("role") or "")
        if ROLE_LABELS.get(role):
            label("位置")
            x = value_x
            icon = self._asset_logo(f"roles/{role.lower()}.png", (20, 20))
            if icon:
                image.paste(icon, (x, y - 2), icon)
                x += icon.width + 6
            self._text(draw, (x, y), ROLE_LABELS[role], fonts["body"], colors["ink"])
            y += 24
        label("账号")
        # The server the account plays on: its flag and Chinese name after the Riot ID.
        server = str(account.get("platformRoute") or "").lower()
        flag_code, server_name = SERVER_FLAGS.get(server, ("", server.upper()))
        flag = self._image_from_url(f"https://flagcdn.com/w40/{flag_code}.png", flag_code) if flag_code else None
        if flag is not None:
            flag = ImageOps.contain(flag.convert("RGBA"), SERVER_FLAG, method=Image.Resampling.LANCZOS)
        tail_w = (flag.width + 4 if flag is not None else 0) + (self._text_width(draw, server_name, fonts["small"])
                                                                if server_name else 0)
        riot_id = f"{account.get('gameName', '')} #{account.get('tagLine', '')}"
        fitted = self._fit_font(draw, riot_id, fonts["text"], right - value_x - tail_w - 8, 8)
        draw.text((value_x, y + 1), riot_id, font=fitted, fill=colors["ink"])
        x = value_x + self._text_width(draw, riot_id, fitted) + 8
        if flag is not None:
            image.paste(flag, (x, y + 9 - flag.height // 2), flag)
            draw.rectangle((x, y + 9 - flag.height // 2, x + flag.width - 1, y + 8 + flag.height - flag.height // 2),
                           outline=self._blend(colors["rule"], colors["panel"], 0.5))
            x += flag.width + 4
        if server_name:
            self._text(draw, (x, y + 2), server_name, fonts["small"], colors["muted"])

    def _draw_tactical_frame(self, image, draw, colors):
        """The left card's tactical screen: the frosted panel, a dotted grid behind the chart column,
        a dotted rule before the matches column and gold brackets on the corners."""
        x0, y0, x1, y1 = LEFT
        self._frost(image, LEFT, colors)
        grid = self._blend(colors["rule"], colors["panel"], 0.22)
        for y in range(y0 + 14, y1 - 6, GRID_STEP):
            for x in range(x0 + 10, x0 + 206, GRID_STEP):
                draw.point((x, y), fill=grid)
        for y in range(y0 + 12, y1 - 10, 4):
            draw.point((x0 + 208, y), fill=self._blend(colors["rule"], colors["panel"], 0.4))
        self._draw_hud_frame(draw, LEFT, colors)

    def _draw_lol_card(self, image, draw, data, account, colors, fonts):
        """The left card as the CS2 page's tactical screen: the pro's ranked form on a radar against
        the other pros (their role's mark until enough are known), their top champions and four ranked
        numbers; beside it their team's pro schedule and results, and the page's dynamic corner."""
        x0, y0, x1, y1 = LEFT
        self._draw_tactical_frame(image, draw, colors)
        form = data.get("form") or {}
        scores = form.get("scores") or {}
        self._text(draw, (x0 + 12, y0 + 7), "近期状态", fonts["section"], colors["ink"])
        if scores:
            self._draw_tag(draw, LOL_ARCHETYPES[max(LOL_FORM_AXES, key=lambda axis: scores.get(axis, 0))],
                           (x0 + 88, y0 + 9), colors, fonts)
        average = form.get("average") or ({axis: 50 for axis in LOL_FORM_AXES} if scores else {})
        self._draw_radar(image, draw, (x0 + 103, y0 + RADAR_TOP), RADAR_RADIUS, scores, colors, fonts, average,
                         axes=LOL_FORM_AXES, labels=LOL_FORM_LABELS)
        self._draw_lol_mastery(image, draw, data.get("mastery"), (x0 + 12, y0 + 233, x0 + 204), colors, fonts)
        caption_y = y0 + 254
        if scores:
            draw.line((x0 + 12, caption_y + 6, x0 + 24, caption_y + 6), fill=colors["gold"], width=2)
            self._text(draw, (x0 + 27, caption_y), "本人", fonts["tiny"], colors["muted"])
            draw.line((x0 + 56, caption_y + 6, x0 + 68, caption_y + 6), fill=colors["cyan"], width=2)
            self._text(draw, (x0 + 71, caption_y), "前 20 平均" if form.get("average") else "位置基准", fonts["tiny"],
                       colors["muted"])
        summary = data.get("summary") or {}
        note = f"排位近 {summary.get('games') or 0} 场"
        self._text(draw, (x0 + 204 - self._text_width(draw, note, fonts["tiny"]), caption_y), note, fonts["tiny"],
                   colors["muted"])
        rate = float(summary.get("winrate") or 0)
        self._draw_stat_chips(draw, (("KDA", f"{float(summary.get('kda') or 0):.2f}", colors["ink"], "kda"),
                                     ("胜率", f"{rate:.0f}%", colors["green"] if rate >= 50 else colors["red"], "winrate"),
                                     ("参团", f"{float(summary.get('kp') or 0):.0f}%", colors["ink"], "kill_participation"),
                                     ("补刀/分", f"{float(summary.get('cs_per_min') or 0):.1f}", colors["ink"],
                                      "cs_per_minute")),
                              (x0 + 8, caption_y + 15, x0 + 204, y1 - 8), colors, fonts, image)
        self._draw_lol_stage(image, draw, data, account, (x0 + 214, y0 + 8, x1 - 10, y1 - 8), colors, fonts)

    def _draw_lol_mastery(self, image, draw, mastery, line, colors, fonts):
        """The pro's three champions of most mastery, each icon with its name."""
        x0, y, x1 = line
        mastery = (mastery or [])[:3]
        if not mastery:
            return
        self._text(draw, (x0, y + 3), "常用", fonts["tiny"], colors["muted"])
        slot = (x1 - x0 - 26) / 3
        for index, item in enumerate(mastery):
            left = int(x0 + 26 + index * slot)
            icon = self._icon_from_url(item.get("champion_icon"), 18, item.get("champion_name"))
            image.paste(icon, (left, y), icon)
            self._single(draw, (left + 21, y + 2), item.get("champion_name"), fonts["tiny"], colors["ink"], slot - 24, 7)

    def _lol_team_logo(self, url, label, size):
        """A team's bundled logo when the page ships one, else lolesports' (None when neither loads)."""
        logo = self._team_logo(label, size)
        if logo is None:
            raw = self._image_from_url(url, label)
            logo = (ImageOps.contain(self._trim_alpha(raw.convert("RGBA")), (size, size), method=Image.Resampling.LANCZOS)
                    if raw is not None and raw.width > 1 else None)
        return logo

    def _draw_lol_stage(self, image, draw, data, account, box, colors, fonts):
        """The team's pro schedule: the next (or live) series, the last results and, below them, the
        dynamic corner: the newest pro game the pro played (live while it runs) over their champion's
        splash. Out of season the corner and the rows fall back to the pro's ranked games."""
        x0, y0, x1, y1 = box
        esports = data.get("esports") or {}
        now = datetime.now().timestamp()
        results = [row for row in esports.get("results") or []
                   if now - float(row.get("start") or 0) <= LOL_STAGE_DAYS * 86400]
        series = esports.get("series") if (esports.get("series") or {}).get("games") else None
        upcoming = esports.get("next")
        if upcoming and upcoming.get("state") != "inProgress" and float(upcoming.get("start") or 0) - now > LOL_STAGE_DAYS * 86400:
            upcoming = None
        matches = data.get("matches") or []
        pro = bool(results or upcoming)
        record = ((series or (results[0] if results else {}) or {}).get("ours") or {}).get("record")
        stage = lolesports_pro.STAGE_NAMES.get((series or (results[0] if results else {}) or {}).get("stage") or "", "")
        note_left = x1
        if pro and record and stage:
            note = f"{stage} {record[0]}胜{record[1]}负"
            note_left = x1 - self._text_width(draw, note, fonts["tiny"])
            self._text(draw, (note_left, y0 + 2), note, fonts["tiny"], colors["muted"])
        # The event the pro is playing in heads the section by its official mark (the column is too
        # narrow for the title beside it); without one the title stands.
        current = series or (results[0] if results else None) or upcoming or {}
        mark = self._event_mark(current.get("league"), current.get("start"), (note_left - 8 - x0, EVENT_MARK_HEIGHT),
                                colors) if pro else None
        if mark is not None:
            image.paste(mark, (x0, y0 + 8 - mark.height // 2), mark)
        else:
            self._text(draw, (x0, y0), "赛程与战绩" if pro else "最近排位", fonts["small_bold"], colors["muted"])
        y = y0 + 20
        if upcoming:
            self._draw_lol_next(image, draw, upcoming, (x0, y, x1), colors, fonts)
            y += 44
        if pro:
            own = self._team_logo(account.get("team"), 16)
            for row in results[:2 if series else 3]:
                match = {"opponent": row["theirs"]["code"], "opponent_logo": row["theirs"].get("image"),
                         "score": f"{row['ours'].get('wins') or 0} : {row['theirs'].get('wins') or 0}", "won": row.get("won")}
                self._draw_cs2_result(image, draw, match, own, (x0, y, x1), colors, fonts, team_logo=self._lol_team_logo)
                y += 24
        else:
            for match in matches[:3]:
                self._draw_lol_ranked_row(image, draw, match, (x0, y, x1), colors, fonts)
                y += 24
        room = (x0, y + 6, x1, y1)
        champions = data.get("champions") or {}
        if series and self._draw_lol_series_card(image, draw, series, champions, room, colors, fonts):
            return
        if matches and self._draw_lol_ranked_card(image, draw, matches[0], champions, room, colors, fonts):
            return
        if y1 - y >= 50:
            self._paste_asset_logo(image, "art-lol-filler.png", (x0, y + 6, x1, y1))

    def _event_mark(self, league, start, max_size, colors):
        """The event's official mark within ``max_size``: the Worlds edition's wordmark (its emblem
        for an edition without one) and MSI's emblem in the page's ink, being one-colour marks; a
        league's own logo as it is. None for anything else or a missing file."""
        league = str(league or "").lower()
        if league in ("lck", "lpl", "lec"):
            return self._asset_logo(f"leagues/{league}.png", max_size)
        if league == "worlds":
            year = datetime.fromtimestamp(float(start)).year if start else datetime.now().year
            path = LOCAL_WORLDS_WORDMARK_PATHS.get(year) or LOCAL_WORLDS_LOGO_PATH
        elif league == "msi":
            path = LOCAL_MSI_LOGO_PATH
        else:
            return None
        if not os.path.isfile(path) or max_size[0] < 12 or max_size[1] < 8:
            return None
        with safe_open_image(path) as source:
            mark = self._trim_alpha(source.convert("RGBA"))
        mark.thumbnail(max_size, Image.Resampling.LANCZOS)
        return self._tint_opaque_pixels(mark, colors["ink"])

    def _draw_lol_next(self, image, draw, upcoming, line, colors, fonts):
        """The next series (a red 直播中 tag while it runs): when, "vs [logo] team", league and stage."""
        x0, y, x1 = line
        if upcoming.get("state") == "inProgress":
            draw.rounded_rectangle((x0, y, x0 + 40, y + 13), radius=6, fill=colors["red"])
            self._text(draw, (x0 + 5, y), "直播中", fonts["tiny"], colors["background"])
        else:
            when = datetime.fromtimestamp(float(upcoming.get("start") or 0))
            self._text(draw, (x0, y), f"{when:%m/%d} 周{'一二三四五六日'[when.weekday()]} {when:%H:%M}", fonts["tiny"],
                       colors["cyan"])
        theirs = upcoming.get("theirs") or {}
        self._text(draw, (x0, y + 12), "vs", fonts["small_bold"], colors["ink"])
        name_x = x0 + self._text_width(draw, "vs", fonts["small_bold"]) + 5
        logo = self._lol_team_logo(theirs.get("image"), theirs.get("code"), 16)
        if logo is not None:
            image.paste(logo, (name_x + (16 - logo.width) // 2, y + 13 + (16 - logo.height) // 2), logo)
            name_x += 20
        self._single(draw, (name_x, y + 12), self._fitted_name(draw, theirs.get("name") or theirs.get("code") or "-",
                                                               fonts["small_bold"], x1 - name_x),
                     fonts["small_bold"], colors["ink"], x1 - name_x, 8)
        league = lolesports_pro.LEAGUE_NAMES.get(upcoming.get("league"), str(upcoming.get("league") or "").upper())
        parts = [league, lolesports_pro.STAGE_NAMES.get(upcoming.get("stage") or "", upcoming.get("stage") or ""),
                 f"BO{upcoming.get('best_of') or 1}"]
        self._single(draw, (x0, y + 28), " · ".join(part for part in parts if part), fonts["tiny"], colors["muted"],
                     x1 - x0, 7)

    def _draw_lol_ranked_row(self, image, draw, match, line, colors, fonts):
        """One ranked game: a 胜/负 tag, the champion, the KDA and how long ago."""
        x0, y, x1 = line
        won = bool(match.get("win"))
        draw.rounded_rectangle((x0, y + 2, x0 + 18, y + 20), radius=4, fill=colors["green"] if won else colors["red"])
        label = "胜" if won else "负"
        self._text(draw, (x0 + (18 - self._text_width(draw, label, fonts["small_bold"])) / 2, y + 4), label,
                   fonts["small_bold"], colors["background"])
        icon = self._icon_from_url(match.get("champion_icon"), 18, match.get("champion_name"))
        image.paste(icon, (x0 + 24, y + 2), icon)
        kda = f"{match.get('kills', 0)}/{match.get('deaths', 0)}/{match.get('assists', 0)}"
        self._text(draw, (x0 + 48, y + 2), kda, fonts["body"], colors["ink"] if won else colors["muted"])
        when = self._relative(match.get("timestamp"))
        self._text(draw, (x1 - self._text_width(draw, when, fonts["tiny"]), y + 6), when, fonts["tiny"], colors["muted"])

    def _champion_splash(self, key):
        return self._image_from_url(CHAMPION_CENTERED_URL.format(key=key), key) if key else None

    def _draw_card_tag(self, draw, box, tag, fill, corner, colors, fonts):
        """A card's tag on its picture's top left, and a dark chip with ``corner`` on the top right."""
        x0, y0, x1, _y1 = box
        tag_w = self._text_width(draw, tag, fonts["small_bold"]) + 12
        draw.rounded_rectangle((x0 + 5, y0 + 5, x0 + 5 + tag_w, y0 + 21), radius=8, fill=fill)
        self._text(draw, (x0 + 11, y0 + 7), tag, fonts["small_bold"], colors["background"])
        if corner:
            corner_w = self._text_width(draw, corner, fonts["tiny"]) + 8
            draw.rectangle((x1 - 5 - corner_w, y0 + 6, x1 - 5, y0 + 20), fill=colors["background"])
            self._text(draw, (x1 - 1 - corner_w, y0 + 7), corner, fonts["tiny"], colors["ink"])

    def _draw_lol_series_card(self, image, draw, series, champions, box, colors, fonts):
        """The pro's newest pro game over their champion's splash: a tag (直播中 / 最近胜利 / 最近一局)
        and the date, then the opponent and the series score, the champion with the pro's KDA, their
        farm and share of the fights, both teams' towers, barons and dragons, and the series' games.
        False when there is no room."""
        x0, y0, x1, y1 = [int(value) for value in box]
        width, height = x1 - x0, y1 - y0
        games = series.get("games") or []
        game = games[-1]
        rows = [20, 20, 14, 15] + ([16] if len(games) > 1 else [])
        while rows and height - 6 - sum(rows) < WIN_TAG_H + 10:
            rows.pop()
        if len(rows) < 3:
            return False
        text_top = height - 6 - sum(rows)
        image.paste(self._top_picture(self._champion_splash(game.get("champion")), (width, height), text_top, colors),
                    (x0, y0))
        draw.rectangle((x0, y0, x1, y1), outline=colors["gold"], width=1)
        live = game.get("state") != "finished"
        tag, fill = (("直播中", colors["red"]) if live else ("最近胜利", colors["gold"]) if game.get("won")
                     else ("最近一局", colors["cyan"]))
        start = series.get("start")
        self._draw_card_tag(draw, (x0, y0, x1, y1), tag, fill,
                            f"{datetime.fromtimestamp(float(start)):%m/%d}" if start else "", colors, fonts)
        y = y0 + text_top
        theirs = series.get("theirs") or {}
        self._text(draw, (x0 + 6, y), "vs", fonts["small_bold"], colors["ink"])
        name_x = x0 + 11 + self._text_width(draw, "vs", fonts["small_bold"])
        logo = self._lol_team_logo(theirs.get("image"), theirs.get("code"), 16)
        if logo is not None:
            image.paste(logo, (name_x + (16 - logo.width) // 2, y + 1 + (16 - logo.height) // 2), logo)
            name_x += 20
        ours_team, theirs_team = game.get("ours") or {}, game.get("theirs") or {}
        score = (f"{ours_team.get('kills', 0)} : {theirs_team.get('kills', 0)}" if live
                 else f"{(series.get('ours') or {}).get('wins') or 0} : {theirs.get('wins') or 0}")
        score_w = self._text_width(draw, score, fonts["body"])
        self._single(draw, (name_x, y), theirs.get("code") or "-", fonts["small_bold"], colors["ink"],
                     x1 - 12 - score_w - name_x, 8)
        self._text(draw, (x1 - 6 - score_w, y - 2), score, fonts["body"], colors["gold"])
        y += rows[0]
        champion = (champions.get("by_id") or {}).get(str(game.get("champion") or "").lower()) or {}
        icon = self._icon_from_url(champion.get("icon_url"), 18, champion.get("name") or game.get("champion"))
        image.paste(icon, (x0 + 6, y), icon)
        kda = f"{game.get('kills', 0)}/{game.get('deaths', 0)}/{game.get('assists', 0)}"
        kda_w = self._text_width(draw, kda, fonts["body"])
        self._single(draw, (x0 + 28, y + 1), champion.get("name") or game.get("champion") or "-", fonts["small_bold"],
                     colors["ink"], x1 - 12 - kda_w - (x0 + 28), 8)
        self._text(draw, (x1 - 6 - kda_w, y), kda, fonts["body"], colors["ink"])
        y += rows[1]
        facts = [f"补刀 {game.get('cs', 0)}"]
        if game.get("kp") is not None and "damage_share" in game:
            facts += [f"参团 {float(game['kp']) * 100:.0f}%", f"伤害 {float(game['damage_share']) * 100:.0f}%"]
        else:
            facts += [f"经济 {float(game.get('gold') or 0) / 1000:.1f}k", f"{game.get('level', 0)} 级"]
        self._single(draw, (x0 + 6, y), " · ".join(facts), fonts["tiny"], colors["ink"], width - 12, 7)
        y += rows[2]
        if len(rows) > 3:
            self._draw_lol_objectives(draw, ours_team, theirs_team, (x0 + 6, y, x1 - 6), colors, fonts)
            y += rows[3]
        if len(rows) > 4:
            self._draw_lol_series_strip(image, draw, games, champions, (x0 + 6, y, x1 - 6), colors, fonts)
        return True

    def _draw_lol_objectives(self, draw, ours, theirs, line, colors, fonts):
        """Both teams' towers and barons (ours first), then a dot per dragon the pro's team took."""
        x0, y, x1 = line
        text = f"塔 {ours.get('towers', 0)}:{theirs.get('towers', 0)} · 大龙 {ours.get('barons', 0)}:{theirs.get('barons', 0)} · 龙"
        self._text(draw, (x0, y), text, fonts["tiny"], colors["muted"])
        x = x0 + self._text_width(draw, text, fonts["tiny"]) + 4
        for name in (ours.get("dragons") or [])[:6]:
            if x + 8 > x1:
                break
            draw.ellipse((x, y + 3, x + 7, y + 10), fill=DRAGON_COLOURS.get(name, colors["gold"]))
            x += 9

    def _draw_lol_series_strip(self, image, draw, games, champions, line, colors, fonts):
        """Each game of the series: its number, the pro's champion and a green (won) or red (lost) edge."""
        x0, y, x1 = line
        slot = min(30, (x1 - x0) / max(1, len(games)))
        for index, game in enumerate(games):
            left = int(x0 + index * slot)
            champion = (champions.get("by_id") or {}).get(str(game.get("champion") or "").lower()) or {}
            self._text(draw, (left, y + 1), f"G{game.get('number') or index + 1}", fonts["tiny"], colors["muted"])
            icon = self._icon_from_url(champion.get("icon_url"), 13, champion.get("name") or game.get("champion"))
            image.paste(icon, (left + 14, y), icon)
            if game.get("won") is not None:
                draw.rectangle((left + 13, y - 1, left + 28, y + 14),
                               outline=colors["green"] if game["won"] else colors["red"])

    def _draw_lol_ranked_card(self, image, draw, match, champions, box, colors, fonts):
        """The pro's latest ranked game over their champion's splash: a tag and how long ago, the
        champion with the KDA, the damage, farm and vision, and the kill share and length. False when
        there is no room."""
        x0, y0, x1, y1 = [int(value) for value in box]
        width, height = x1 - x0, y1 - y0
        rows = [20, 15, 15]
        text_top = height - 6 - sum(rows)
        if text_top < WIN_TAG_H + 10:
            return False
        image.paste(self._top_picture(self._champion_splash(match.get("champion_key")), (width, height), text_top,
                                      colors), (x0, y0))
        draw.rectangle((x0, y0, x1, y1), outline=colors["gold"], width=1)
        won = bool(match.get("win"))
        self._draw_card_tag(draw, (x0, y0, x1, y1), "排位" + ("胜利" if won else "失利"),
                            colors["gold"] if won else colors["cyan"], self._relative(match.get("timestamp")), colors, fonts)
        y = y0 + text_top
        icon = self._icon_from_url(match.get("champion_icon"), 18, match.get("champion_name"))
        image.paste(icon, (x0 + 6, y), icon)
        kda = f"{match.get('kills', 0)}/{match.get('deaths', 0)}/{match.get('assists', 0)}"
        kda_w = self._text_width(draw, kda, fonts["body"])
        self._single(draw, (x0 + 28, y + 1), match.get("champion_name") or "-", fonts["small_bold"], colors["ink"],
                     x1 - 12 - kda_w - (x0 + 28), 8)
        self._text(draw, (x1 - 6 - kda_w, y), kda, fonts["body"], colors["ink"])
        y += rows[0]
        facts = [f"伤害 {float(match.get('damage') or 0) / 1000:.1f}k", f"补刀 {match.get('cs', 0)}"]
        if match.get("vision") is not None:
            facts.append(f"视野 {match['vision']}")
        self._single(draw, (x0 + 6, y), " · ".join(facts), fonts["tiny"], colors["ink"], width - 12, 7)
        y += rows[1]
        lane = self._lane_label(match.get("lane"))
        facts = [f"参团 {float(match.get('kp') or 0):.0f}%", f"{int(match.get('duration') or 0) // 60} 分钟"]
        self._single(draw, (x0 + 6, y), " · ".join(([lane] if lane else []) + facts), fonts["tiny"], colors["muted"],
                     width - 12, 7)
        return True

    def _draw_lol_board(self, image, draw, board, box, account, colors, fonts):
        known = [row for row in board if row.get("tier")]
        self._frost(image, box, colors)
        self._team_mark(image, self._team_logo(account.get("team"), 150), box)
        self._board_frame(draw, box, "职业天梯", f"{len(board)} 位选手 · {len(known)} 已更新 · 单双排", colors, fonts,
                          fill=False)
        for index, cell in self._board_cells(box, len(board)):
            row = board[index]
            if row["id"] == account.get("player_id", account.get("id")):
                self._highlight(draw, cell, colors)
            left, top, right, bottom = cell
            middle = (top + bottom) / 2
            self._text(draw, (left, middle - 7), f"{index + 1:>2}", fonts["small_bold"], colors["muted"])
            logo = self._team_logo(row.get("team"), 16)
            if logo:
                image.paste(logo, (int(left + 20), int(middle - 8)), logo)
            self._single(draw, (left + 40, middle - 13), row["player"], fonts["small_bold"], colors["ink"], 80, 8)
            self._text(draw, (left + 40, middle + 1), ROLE_LABELS.get(row.get("role"), "") + (
                "  ●对局中" if row.get("in_game") else ""), fonts["tiny"], colors["green"] if row.get("in_game") else colors["muted"])
            tier = str(row.get("tier") or "").upper()
            if tier:
                label = TIER_SHORT.get(tier, tier) + ("" if tier in APEX_TIERS else str(row.get("rank") or ""))
                lp = f"{int(row.get('lp') or 0):,}"
                self._text(draw, (right - self._text_width(draw, lp, fonts["small_bold"]), middle - 13), lp,
                           fonts["small_bold"], colors["gold"])
                self._text(draw, (right - self._text_width(draw, label, fonts["tiny"]), middle + 1), label, fonts["tiny"],
                           colors["muted"])
            else:
                note = "未找到" if row.get("missing") else "-"
                self._text(draw, (right - self._text_width(draw, note, fonts["tiny"]), middle - 6), note, fonts["tiny"],
                           colors["red"] if row.get("missing") else colors["muted"])

    # -- CS2 ----------------------------------------------------------------------------
    def _render_cs2_pro(self, data, dimensions, theme_context, pick):
        colors = self._render_colors(theme_context)
        fonts = self._pro_fonts()
        image = Image.new("RGB", (800, 480), colors["background"])
        player = data.get("player") or {}
        pool = player.get("map_pool") or []
        if not self._cs2_backdrop(image, pool[0] if pool else None, colors):
            stripes = ImageDraw.Draw(image)
            stripes.rectangle((0, 0, 800, HERO_H), fill=colors["panel"])
            for offset in range(-HERO_H, 800, 18):
                stripes.line((offset, HERO_H, offset + HERO_H, 0), fill=colors["background_stripe"], width=6)
        draw = ImageDraw.Draw(image)
        # The game's logo titles the page at the top left; the player's bodyshot stands below it.
        self._paste_asset_logo(image, "cs2-logo.png", (12, 10, 140, 36), tint=colors["muted"])
        photo = self._image_from_url(player.get("bodyshot"), player.get("nick"))
        x = 20
        if photo is not None:
            photo = ImageOps.contain(photo.convert("RGBA"), (150, HERO_H - TITLE_H), method=Image.Resampling.LANCZOS)
            photo = self._fade_foot(photo)
            image.paste(photo, (12, HERO_H - photo.height), photo)
            x = max(150, 12 + photo.width + 14)
        self._single(draw, (x, 44), player.get("nick") or "-", fonts["hero"], colors["ink"], 330, 20)
        name_right = x + self._text_width(draw, player.get("nick") or "-", fonts["hero"])
        country = str(player.get("country") or "").lower()
        flag = self._image_from_url(f"https://flagcdn.com/w40/{country}.png" if country else "", country)
        name_x = x
        if flag is not None:
            flag = ImageOps.contain(flag.convert("RGBA"), (22, 15), method=Image.Resampling.LANCZOS)
            image.paste(flag, (x, 103), flag)
            name_x += flag.width + 6
        age = str(player.get("age") or "").replace(" years", " 岁")
        self._single(draw, (name_x, 99), " · ".join(part for part in (player.get("name"), age) if part), fonts["text"],
                     colors["ink"], 300, 9)
        team_logo = self._cs2_team_logo(player.get("team_logo_night") or player.get("team_logo"), player.get("team"), 22)
        team_x = x
        if team_logo is not None:
            image.paste(team_logo, (x, 123), team_logo)
            team_x += 28
        team_line = f"{player.get('team') or '无战队'} · HLTV {data.get('top20_year')} 第 {player.get('rank', '-')} 名"
        self._text(draw, (team_x, 125), team_line, fonts["small_bold"], colors["gold"])
        # The guns of the recent loadout fill the band between the name block and the rating
        # (the generated shooter props when HLTV saw none).
        text_right = max(name_right, team_x + self._text_width(draw, team_line, fonts["small_bold"]),
                         name_x + self._text_width(draw, " · ".join(part for part in (player.get("name"), age) if part),
                                                   fonts["text"]))
        guns_box = (int(text_right) + 20, 12, 600, 146)
        if guns_box[2] - guns_box[0] >= 90 and not self._draw_cs2_loadout(image, draw, self._cs2_guns(player),
                                                                          guns_box, colors, fonts):
            self._paste_asset_logo(image, "art-cs2-hero.png", (guns_box[0], 18, 600, 132))
        self._text(draw, (620, 22), "RATING 3.0", fonts["small_bold"], colors["muted"])
        rating = player.get("rating")
        draw.text(CS2_RATING_AT, f"{rating:.2f}" if isinstance(rating, (int, float)) else "-",
                  font=self._cs2_rating_face(CS2_RATING_DIGITS), fill=colors["gold"], anchor="ls")
        self._single(draw, (620, 98), self._cs2_window(player), fonts["small"], colors["muted"], 166, 8)
        self._tracker_badge(draw, pick, colors, fonts)

        x0, y0, x1, y1 = LEFT
        self._draw_cs2_role_card(image, draw, player, data.get("board") or [], colors, fonts)
        self._draw_cs2_matches(image, draw, player, (x0 + 214, y0 + 8, x1 - 10, y1 - 8), colors, fonts)
        self._frost(image, RIGHT, colors)
        self._team_mark(image, self._cs2_team_logo(player.get("team_logo_night") or player.get("team_logo"),
                                                   player.get("team"), 150), RIGHT)
        self._draw_cs2_board(image, draw, data.get("board") or [], RIGHT, player, data, colors, fonts)
        return image if tuple(dimensions) == (800, 480) else image.resize(tuple(dimensions), Image.Resampling.LANCZOS)

    @staticmethod
    def _cs2_rating_face(digits):
        """CS2's own display face for the rating, its digits ``digits`` px tall: Stratum2 when the
        owner has installed it on the device (sports-esports-cs2-score in the data fonts, see
        sports_dashboard/assets/fonts/ESPORTS.md), else the matches' Barlow Condensed."""
        with type_scope("cs2"):
            private = active_font(digits, True)
        face = (private.font_variant if isinstance(private, EsportsFont) else partial(_match_face, "score"))
        probe = face(size=digits).getbbox("8")
        return face(size=max(8, round(digits * digits / max(1, probe[3] - probe[1]))))

    def _map_art(self, map_key):
        """The official loading art of a competitive map (assets/maps/<map>.jpg), or None."""
        if map_key not in MAP_NAMES:
            return None
        art = self._asset_logo(f"maps/{map_key}.jpg", (800, 450))
        return art.convert("RGB") if art else None

    def _cs2_backdrop(self, image, best, colors):
        """Lay the best map under the whole page (False when there is none to lay).

        One crop of the map runs down the page: bright and sharp in the hero band, then
        easing into the dimmed, softened page across CS2_HERO_FADE."""
        art = self._map_art((best or {}).get("map"))
        if art is None:
            return False
        self._layered_backdrop(image, ImageOps.fit(art, image.size, method=Image.Resampling.LANCZOS), colors,
                               CS2_HERO_DIM, CS2_PAGE_DIM)
        return True

    @staticmethod
    def _draw_radar_sweep(layer, centre, reach, roles, shape, colors, scale=1, axes=CS2_ROLES):
        """A gold beam swept onto the player's best role with its trail fading out behind it,
        composited onto ``layer``. The beam lights only the player's own shape, so no gold
        reaches past the scores it stands for."""
        best = max(axes, key=lambda name: roles.get(name) or 0)
        heading = -90 + 360 * axes.index(best) / len(axes)
        cx, cy = centre
        scope = (cx - reach, cy - reach, cx + reach, cy + reach)
        beam = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        pen = ImageDraw.Draw(beam)
        steps = 28
        for step in range(steps):
            alpha = round(255 * RADAR_SWEEP_ALPHA * (1 - step / steps) ** 1.8)
            pen.pieslice(scope, heading - RADAR_TRAIL * (step + 1) / steps, heading - RADAR_TRAIL * step / steps,
                         fill=(*colors["gold"][:3], alpha))
        angle = math.radians(heading)
        pen.line((cx, cy, cx + math.cos(angle) * reach, cy + math.sin(angle) * reach),
                 fill=(*colors["gold"][:3], 210), width=2 * scale)
        inside = Image.new("L", layer.size, 0)
        ImageDraw.Draw(inside).polygon(shape, fill=255)
        beam.putalpha(ImageChops.multiply(beam.getchannel("A"), inside))
        layer.alpha_composite(beam)

    @staticmethod
    def _fade_foot(photo, rows=CS2_PHOTO_FADE):
        """The cut-out photo with its last ``rows`` fading to transparent."""
        rows = min(rows, photo.height)
        ramp = Image.new("L", (1, photo.height), 255)
        for y in range(photo.height - rows, photo.height):
            ramp.putpixel((0, y), round(255 * (photo.height - y) / (rows + 1)))
        faded = photo.copy()
        faded.putalpha(ImageChops.multiply(photo.getchannel("A"), ramp.resize(photo.size)))
        return faded

    @staticmethod
    def _frost(image, box, colors, alpha=CS2_PANEL_ALPHA):
        """Frost the page under a panel with the panel colour, so the map shows through faintly."""
        x0, y0, x1, y1 = box
        region = image.crop((x0, y0, x1 + 1, y1 + 1))
        image.paste(Image.blend(region, Image.new("RGB", region.size, colors["panel"]), alpha), (x0, y0))

    def _cs2_team_logo(self, url, label, size, badge=False):
        """The team's bundled logo when the sports dashboard has one, else HLTV's (an SVG drawn);
        with ``badge``, a team with neither still gets a mark before its name."""
        path = _bundled_team_logos().get(_team_key(label))
        logo = None
        if path and os.path.isfile(path):
            try:
                with Image.open(path) as source:
                    logo = self._trim_alpha(source.convert("RGBA"))
            except OSError:
                logo = None
        if logo is None:
            logo = self._image_from_url(url, label)
        if logo is None or logo.width <= 1:
            return _team_badge(label, size) if badge else None
        return ImageOps.contain(logo.convert("RGBA"), (size, size), method=Image.Resampling.LANCZOS)

    @staticmethod
    def _cs2_guns(player):
        """Up to three guns of the recent loadout: the AWP first for a sniper, else the T rifle; then the CT rifle and a pistol."""
        loadout = player.get("loadout") or {}

        def find(sides, names):
            for name in names:
                for side in sides:
                    slot = next((item for item in loadout.get(side) or [] if item.get("weapon") == name), None)
                    if slot:
                        return slot
            return None

        awp = find(("t", "ct"), ("AWP",))
        t_rifle, ct_rifle = find(("t",), CS2_RIFLES["t"]), find(("ct",), CS2_RIFLES["ct"])
        pistol = find(("t", "ct"), CS2_PISTOLS)
        sniper = (player.get("roles") or {}).get("Sniping", 0) >= SNIPER_ROLE
        order = [awp, t_rifle, ct_rifle, pistol] if sniper else [t_rifle, ct_rifle, pistol, awp]
        guns = []
        for slot in order:
            if slot and slot.get("image") and all(slot["weapon"] != gun["weapon"] for gun in guns):
                guns.append(slot)
        return guns[:3]

    def _gun_image(self, slot, max_size):
        raw = self._image_from_url(slot.get("image"), slot.get("weapon"))
        if raw is None:
            return None
        return ImageOps.contain(self._trim_alpha(raw.convert("RGBA")), max_size, method=Image.Resampling.LANCZOS)

    def _draw_cs2_loadout(self, image, draw, guns, box, colors, fonts):
        """The main gun large with up to two more beneath, each named with its skin (False when none loads)."""
        x0, y0, x1, y1 = box
        width = x1 - x0
        main = self._gun_image(guns[0], (width, 60)) if guns else None
        if main is None:
            return False
        self._text(draw, (x0, y0), "近期常用枪械", fonts["tiny"], colors["muted"])
        image.paste(main, (x0 + (width - main.width) // 2, y0 + 14 + (60 - main.height) // 2), main)
        self._gun_caption(draw, guns[0], (x0, y0 + 76, x1), fonts["small_bold"], fonts["tiny"], colors)
        rest = guns[1:3] if width >= 170 else guns[1:2]
        cell = width / max(1, len(rest))
        for index, slot in enumerate(rest):
            left = int(x0 + index * cell)
            gun = self._gun_image(slot, (int(cell) - 8, 28))
            if gun is None:
                continue
            image.paste(gun, (left + (int(cell) - gun.width) // 2, y0 + 94 + (28 - gun.height) // 2), gun)
            self._gun_caption(draw, slot, (left, y0 + 123, int(left + cell) - 4), fonts["tiny"], fonts["tiny"], colors)
        return True

    def _gun_caption(self, draw, slot, line, weapon_font, skin_font, colors):
        """"AK-47 Wild Lotus" centred on the gun: the gun in gold, its skin after it muted, cut to fit."""
        left, y, right = line
        weapon = str(slot.get("weapon") or "")
        skin = str(slot.get("skin") or "")
        weapon_w = self._text_width(draw, weapon, weapon_font)
        skin_w = min(self._text_width(draw, skin, skin_font), max(0, right - left - weapon_w - 4))
        if skin_w < 24:
            skin_w = 0
        x = left + max(0, (right - left - weapon_w - (skin_w + 4 if skin_w else 0)) / 2)
        self._single(draw, (x, y), weapon, weapon_font, colors["gold"], right - left, 7)
        if skin_w:
            self._single(draw, (x + weapon_w + 4, y + (weapon_font.size - skin_font.size)), skin, skin_font,
                         colors["muted"], skin_w, 7)

    @staticmethod
    def _cs2_window(player):
        maps = player.get("maps")
        return f"近 3 个月 · {maps} 张图" if maps else "暂无近期数据"

    @staticmethod
    def _cs2_role_average(board):
        """The Top 20's mean score per role, from the players whose pages have been read."""
        scored = [row["roles"] for row in board if len(row.get("roles") or {}) >= len(CS2_ROLES) - 1]
        if len(scored) < 3:
            return {}
        return {name: sum(roles.get(name, 0) for roles in scored) / len(scored) for name in CS2_ROLES}

    @staticmethod
    def _cs2_archetype(roles):
        if not roles:
            return ""
        if roles.get("Sniping", 0) >= SNIPER_ROLE:
            return CS2_ARCHETYPES["Sniping"]
        best = max((name for name in CS2_ROLES if name != "Sniping"), key=lambda name: roles.get(name, 0))
        return CS2_ARCHETYPES[best]

    def _draw_role_tag(self, draw, roles, origin, colors, fonts):
        """A gold tag after the section title naming the player's role, e.g. 火力核心."""
        label = self._cs2_archetype(roles)
        if label:
            self._draw_tag(draw, label, origin, colors, fonts)

    def _draw_tag(self, draw, label, origin, colors, fonts):
        """A gold tag after a section title."""
        x, y = origin
        width = self._text_width(draw, label, fonts["small_bold"]) + 14
        draw.rounded_rectangle((x, y, x + width, y + 18), radius=9, fill=colors["gold"])
        self._text(draw, (x + 7, y + 2), label, fonts["small_bold"], colors["background"])

    def _draw_cs2_role_card(self, image, draw, player, board, colors, fonts):
        """The left card as a tactical screen: gold corner brackets and a dotted grid, the role
        chart on a radar scope, the signature role, and the player's bo3 career in four chips."""
        x0, y0, x1, y1 = LEFT
        self._draw_tactical_frame(image, draw, colors)
        roles = player.get("roles") or {}
        self._text(draw, (x0 + 12, y0 + 7), "角色能力", fonts["section"], colors["ink"])
        self._draw_role_tag(draw, roles, (x0 + 88, y0 + 9), colors, fonts)
        average = self._cs2_role_average(board)
        self._draw_radar(image, draw, (x0 + 103, y0 + RADAR_TOP), RADAR_RADIUS, roles, colors, fonts, average)
        self._draw_signature_role(draw, player, board, (x0 + 12, y0 + 234, x0 + 204), colors, fonts)
        caption_y = y0 + 254
        if average:
            draw.line((x0 + 12, caption_y + 6, x0 + 24, caption_y + 6), fill=colors["gold"], width=2)
            self._text(draw, (x0 + 27, caption_y), "本人", fonts["tiny"], colors["muted"])
            draw.line((x0 + 56, caption_y + 6, x0 + 68, caption_y + 6), fill=colors["cyan"], width=2)
            self._text(draw, (x0 + 71, caption_y), "前 20 平均", fonts["tiny"], colors["muted"])
        career = player.get("career") or {}
        if career.get("maps"):
            note = f"生涯 {career['maps']} 图"
            self._text(draw, (x0 + 204 - self._text_width(draw, note, fonts["tiny"]), caption_y), note, fonts["tiny"],
                       colors["muted"])
            self._draw_career_chips(draw, career, (x0 + 8, caption_y + 15, x0 + 204, y1 - 8), colors, fonts)

    def _draw_hud_frame(self, draw, box, colors, arm=HUD_ARM):
        """A hairline frame with gold brackets on its corners, like a scope's targeting box."""
        x0, y0, x1, y1 = box
        draw.rectangle(box, outline=self._blend(colors["rule"], colors["panel"], 0.45), width=1)
        for x, y, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            draw.line((x, y, x + dx * arm, y), fill=colors["gold"], width=2)
            draw.line((x, y, x, y + dy * arm), fill=colors["gold"], width=2)

    def _draw_radar(self, image, draw, centre, radius, roles, colors, fonts, average=None, axes=CS2_ROLES,
                    labels=CS2_ROLE_LABELS):
        """The role chart on a radar scope: a ticked bezel round banded rings, the Top 20 average
        dashed in cyan, the player's shape in gold with a soft glow and the sweep onto their best
        role, whose point carries a ring. Drawn RADAR_SCALE times larger and scaled down, so its
        lines and curves come out smooth; the labels go on afterwards at the page's own size."""
        k = RADAR_SCALE
        cx, cy = centre
        half = SCOPE_RADIUS + 3
        middle, reach, scope = half * k, radius * k, SCOPE_RADIUS * k
        count = len(axes)

        def point(index, scale, origin=middle, length=reach):
            angle = 2 * math.pi * index / count
            return origin + math.sin(angle) * length * scale, origin - math.cos(angle) * length * scale

        def ring(scale):
            return [point(index, scale) for index in range(count)]

        def shape_of(scores):
            return [point(index, max(4, min(100, scores.get(name, 0))) / 100) for index, name in enumerate(axes)]

        disc = self._blend(colors["background"], colors["panel"], 0.55)
        band = self._blend(colors["rule"], disc, 0.08)
        layer = Image.new("RGBA", (2 * middle, 2 * middle), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        pen.ellipse((middle - scope, middle - scope, middle + scope, middle + scope), fill=(*disc, 215),
                    outline=(*colors["rule"], 110), width=k)
        inner = scope - 8 * k
        pen.ellipse((middle - inner, middle - inner, middle + inner, middle + inner), outline=(*colors["rule"], 45),
                    width=k)
        for degree in range(0, 360, 5):
            length = (5 if degree % 30 == 0 else 2.5) * k
            angle = math.radians(degree)
            pen.line((middle + math.sin(angle) * scope, middle - math.cos(angle) * scope,
                      middle + math.sin(angle) * (scope - length), middle - math.cos(angle) * (scope - length)),
                     fill=(*colors["rule"], 150 if degree % 30 == 0 else 80), width=k)
        # A gold notch on the bezel marks each role's axis.
        for index in range(count):
            tip, left, right = (point(index, 1, length=scope - 6 * k), point(index - 0.08, 1, length=scope),
                                point(index + 0.08, 1, length=scope))
            pen.polygon((tip, left, right), fill=(*colors["gold"], 200))
        # Banded rings (each band repaints its inner part), then the rings' and spokes' hairlines.
        for scale, fill in ((1.0, band), (0.75, disc), (0.5, band), (0.25, disc)):
            pen.polygon(ring(scale), fill=(*fill, 215))
        for scale in (0.25, 0.5, 0.75):
            pen.line(ring(scale) + ring(scale)[:1], fill=(*colors["rule"], 55), width=k)
        pen.line(ring(1.0) + ring(1.0)[:1], fill=(*colors["rule"], 150), width=k)
        for x, y in ring(1.0):
            pen.line((middle, middle, x, y), fill=(*colors["rule"], 55), width=k)
        if average:
            self._dashed_loop(pen, shape_of(average), (*colors["cyan"], 235), 2 * k, 5 * k, 3 * k)
        if roles:
            shape = shape_of(roles)
            glow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            ImageDraw.Draw(glow).line(shape + shape[:1], fill=(*colors["gold"], 170), width=6 * k, joint="curve")
            layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(4 * k)))
            fill = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            ImageDraw.Draw(fill).polygon(shape, fill=(*colors["gold"], 72))
            layer.alpha_composite(fill)
            self._draw_radar_sweep(layer, (middle, middle), reach * 1.12, roles, shape, colors, k, axes)
            pen.line(shape + shape[:1], fill=(*colors["gold"], 255), width=round(2.4 * k), joint="curve")
            best = max(range(count), key=lambda index: roles.get(axes[index]) or 0)
            for index, (x, y) in enumerate(shape):
                dot = (4.2 if index == best else 3) * k
                if index == best:
                    pulse = 8 * k
                    pen.ellipse((x - pulse, y - pulse, x + pulse, y + pulse), outline=(*colors["gold"], 170), width=k)
                pen.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(*colors["gold"], 255),
                            outline=(*colors["background"], 255), width=k)
        small = layer.resize((2 * half, 2 * half), Image.Resampling.LANCZOS)
        box = (cx - half, cy - half, cx + half, cy + half)
        base = image.crop(box).convert("RGBA")
        base.alpha_composite(small)
        image.paste(base.convert("RGB"), box[:2])
        self._draw_radar_labels(draw, centre, radius, roles, colors, fonts, average, axes, labels)

    @staticmethod
    def _dashed_loop(pen, points, fill, width, dash, gap):
        """A closed dashed line through ``points``, its dashes running on round the corners."""
        path = list(points) + list(points[:1])
        period, travelled = dash + gap, 0.0
        for (ax, ay), (bx, by) in zip(path, path[1:]):
            length = math.hypot(bx - ax, by - ay)
            at = 0.0
            while at < length:
                phase = (travelled + at) % period
                step = min(dash - phase if phase < dash else period - phase, length - at)
                if phase < dash:
                    pen.line((ax + (bx - ax) * at / length, ay + (by - ay) * at / length,
                              ax + (bx - ax) * (at + step) / length, ay + (by - ay) * (at + step) / length),
                             fill=fill, width=width)
                at += max(step, 0.5)
            travelled += length

    def _draw_radar_labels(self, draw, centre, radius, roles, colors, fonts, average, axes=CS2_ROLES,
                           labels=CS2_ROLE_LABELS):
        """Each role's name and score outside the chart: the score gold where it beats the Top 20
        average, muted where it trails it; the player's best role sits on a gold tag."""
        cx, cy = centre
        best = max(axes, key=lambda name: roles.get(name) or 0) if roles else None
        for index, name in enumerate(axes):
            angle = 2 * math.pi * index / len(axes)
            x, y = cx + math.sin(angle) * radius * RADAR_LABEL, cy - math.cos(angle) * radius * RADAR_LABEL
            score = roles.get(name)
            label, value = labels[name], str(score if score is not None else "-")
            label_w = self._text_width(draw, label, fonts["tiny"])
            width = label_w + 3 + self._text_width(draw, value, fonts["small_bold"])
            left = x - width / 2
            if name == best and score:
                draw.rounded_rectangle((left - 5, y - 8, left + width + 5, y + 8), radius=8, fill=colors["gold"])
                self._text(draw, (left, y - 6), label, fonts["tiny"], colors["background"])
                self._text(draw, (left + label_w + 3, y - 7), value, fonts["small_bold"], colors["background"])
                continue
            colour = colors["ink"]
            if isinstance(score, (int, float)) and average:
                colour = (colors["gold"] if score >= average[name] + ROLE_MARGIN else
                          colors["muted"] if score <= average[name] - ROLE_MARGIN else colors["ink"])
            self._text(draw, (left, y - 6), label, fonts["tiny"], colors["muted"])
            self._text(draw, (left + label_w + 3, y - 7), value, fonts["small_bold"], colour)

    def _draw_signature_role(self, draw, player, board, line, colors, fonts):
        """The player's strongest role with its place among the Top 20."""
        x0, y, x1 = line
        roles = player.get("roles") or {}
        if not roles:
            return
        best = max(CS2_ROLES, key=lambda name: roles.get(name, 0))
        scores = sorted((row["roles"].get(best, 0) for row in board if row.get("roles")), reverse=True)
        place = 1 + sum(1 for score in scores if score > roles.get(best, 0))
        self._text(draw, (x0, y + 2), "招牌", fonts["tiny"], colors["muted"])
        text = f"{CS2_ROLE_LABELS[best]} {roles.get(best)}"
        self._text(draw, (x0 + 26, y), text, fonts["body"], colors["gold"])
        if len(scores) >= 3:
            self._text(draw, (x0 + 30 + self._text_width(draw, text, fonts["body"]), y + 2), f"前 20 第 {place}",
                       fonts["tiny"], colors["ink"])

    def _draw_career_chips(self, draw, career, box, colors, fonts):
        """The player's bo3 career in four chips: K/D, damage per round, the share of maps won and titles."""
        kd, adr, rate = career.get("kd"), career.get("adr"), career.get("win_rate")
        self._draw_stat_chips(draw, (("K/D", f"{kd:.2f}" if isinstance(kd, (int, float)) else "-"),
                                     ("ADR", f"{adr:.0f}" if isinstance(adr, (int, float)) else "-"),
                                     ("地图胜率", f"{rate * 100:.0f}%" if isinstance(rate, (int, float)) else "-"),
                                     ("冠军", str(career.get("titles") if career.get("titles") is not None else "-"))),
                              box, colors, fonts)

    def _draw_stat_chips(self, draw, chips, box, colors, fonts, image=None):
        """(label, value[, colour[, icon]]) chips side by side under a gold top edge, the value large;
        an icon (assets/icons/<name>.png) leads its label."""
        x0, y0, x1, y1 = box
        gap = 4
        width = (x1 - x0 - gap * (len(chips) - 1)) / len(chips)
        fill = self._blend(colors["background"], colors["panel"], 0.45)
        edge = self._blend(colors["rule"], colors["panel"], 0.3)
        for index, chip in enumerate(chips):
            label, value = chip[0], chip[1]
            colour = chip[2] if len(chip) > 2 else colors["ink"]
            icon = self._asset_logo(f"icons/{chip[3]}.png", (12, 12)) if image is not None and len(chip) > 3 else None
            left = round(x0 + index * (width + gap))
            right = round(left + width)
            draw.rounded_rectangle((left, y0, right, y1), radius=4, fill=fill, outline=edge)
            draw.line((left + 3, y0, right - 3, y0), fill=colors["gold"], width=2)
            label_w = self._text_width(draw, label, fonts["tiny"]) + (icon.width + 2 if icon else 0)
            label_x = left + (right - left - label_w) / 2
            if icon:
                image.paste(icon, (int(label_x), y0 + 4), icon)
                label_x += icon.width + 2
            self._text(draw, (label_x, y0 + 3), label, fonts["tiny"], colors["muted"])
            self._single(draw, (left + (right - left - self._text_width(draw, value, fonts["body"])) / 2, y0 + 16), value,
                         fonts["body"], colour, right - left - 4, 9)

    def _draw_cs2_matches(self, image, draw, player, box, colors, fonts):
        x0, y0, x1, y1 = box
        pool = player.get("map_pool") or []
        win = self._cs2_recent_win(player)
        slot = bool(win or player.get("photos"))
        # Every match names its event, each led (or a result ended) by that event's logo.
        links = player.get("event_logos") or {}
        self._text(draw, (x0, y0), "赛程与战绩", fonts["small_bold"], colors["muted"])
        y = y0 + 20
        # With the slot below to fill, the next match is enough; without it, the next two.
        for match in (player.get("upcoming") or [])[:1 if pool or slot else 2]:
            when = datetime.fromtimestamp(match["unix_ms"] / 1000)
            self._match_text(draw, (x0, y + 6), f"{when:%m/%d %H:%M}", "figure", 13, colors["cyan"])
            # "vs", then the opponent's logo right before its name; the event's own logo leads its title.
            name_x = x0 + round(self._match_text(draw, (x0, y + 21), "vs", "label", 12, colors["muted"])) + 5
            team = self._cs2_team_logo(match.get("opponent_logo"), match.get("opponent"), 16, badge=True)
            if team is not None:
                image.paste(team, (name_x + (16 - team.width) // 2, y + 13 + (16 - team.height) // 2), team)
                name_x += 20
            self._match_text(draw, (name_x, y + 21), match.get("opponent"), "name", 15, colors["ink"],
                             width=x1 - name_x, min_size=10)
            event_x = x0
            mark = self._cs2_event_logo(match.get("event"), when, links)
            if mark is not None:
                image.paste(mark, (x0, y + 27 + (CS2_EVENT_LOGO[1] - mark.height) // 2), mark)
                event_x += mark.width + 4
            self._match_text(draw, (event_x, y + 27 + CS2_EVENT_LOGO[1] // 2), match.get("event"), "label", 12,
                             colors["muted"], width=x1 - event_x, min_size=9)
            y += 44
        own_logo = self._cs2_team_logo(player.get("team_logo_night") or player.get("team_logo"), player.get("team"), 16,
                                       badge=True)
        # The latest win takes the slot's room, so one result fewer is listed above it.
        for match in (player.get("recent") or [])[:3 if win else 4]:
            self._draw_cs2_result(image, draw, match, own_logo, (x0, y, x1), colors, fonts,
                                  event_logo=self._cs2_event_logo(match.get("event"), None, links, CS2_RESULT_EVENT_LOGO))
            y += 24
            if y > y1 - 20:
                break
        if not player.get("upcoming") and not player.get("recent"):
            self._text(draw, (x0, y), "暂无比赛信息", fonts["small"], colors["muted"])
            y += 22
        # The room under the results is the page's dynamic corner: the team's latest win with its
        # maps over a picture of the player; else one of HLTV's newest pictures of the player, a new
        # one each render; else the map pool, else the art (the best map still lies behind the page).
        room = (x0, y + 6, x1, y1)
        if win and self._draw_cs2_win(image, draw, player, win, room, colors, fonts):
            return
        if player.get("photos") and y1 - y >= 50 and self._draw_cs2_photo(image, draw, player, room, colors, fonts):
            return
        if pool and y1 - y >= 70:
            self._draw_cs2_maps(image, draw, pool, (x0, y + 8, x1, y1), colors, fonts)
        elif y1 - y >= 50:
            self._paste_asset_logo(image, "art-cs2-filler.png", (x0, y + 6, x1, y1))

    @staticmethod
    def _cs2_recent_win(player):
        """The team's latest win while it is at most WIN_DAYS old and lists its maps (else None)."""
        win = player.get("last_win") or {}
        start = win.get("start")
        if not win.get("maps") or not isinstance(start, (int, float)):
            return None
        return win if datetime.now().timestamp() - start <= WIN_DAYS * 86400 else None

    def _cs2_photo(self, player):
        """(picture, its place in HLTV's newest) for this render's random pick, or the next newest
        that loads when it does not; (None, 0) when none does."""
        photos = player.get("photos") or []
        pick = player.get("photo_pick")
        order = ([pick] if pick in photos else []) + [url for url in photos if url != pick]
        for url in order[:3]:
            photo = self._image_from_url(url, player.get("nick"))
            if photo is not None:
                return photo, photos.index(url) + 1
        return None, 0

    @staticmethod
    def _focus_crop(photo, size, zoom=PHOTO_ZOOM):
        """``photo`` cut to ``size`` round where its detail gathers (the players rather than the dark
        stage wall above them), zoomed in a little; halfway to the middle, so one bright sign cannot
        pull the cut off the players."""
        source = photo.convert("RGB")
        width, height = size
        edges = source.convert("L").resize((64, max(3, round(64 * source.height / source.width))),
                                           Image.Resampling.BILINEAR).filter(ImageFilter.FIND_EDGES)
        pixels = edges.load()
        total = sum_x = sum_y = 0
        for y in range(1, edges.height - 1):
            for x in range(1, edges.width - 1):
                value = pixels[x, y]
                total, sum_x, sum_y = total + value, sum_x + value * x, sum_y + value * y
        focus_x = (sum_x / total / edges.width + 0.5) / 2 if total else 0.5
        focus_y = (sum_y / total / edges.height + 0.45) / 2 if total else 0.45
        crop_w = min(source.width, source.height * width / height) / zoom
        crop_h = crop_w * height / width
        left = min(max(0.0, focus_x * source.width - crop_w / 2), source.width - crop_w)
        top = min(max(0.0, focus_y * source.height - crop_h / 2), source.height - crop_h)
        return source.resize(size, Image.Resampling.LANCZOS, box=(left, top, left + crop_w, top + crop_h))

    def _draw_cs2_photo(self, image, draw, player, box, colors, fonts):
        """This render's pick of HLTV's newest pictures of the player, cut round them, with its credit
        and its place among the pictures; False when none loads, so the room keeps its art."""
        x0, y0, x1, y1 = [int(value) for value in box]
        photo, place = self._cs2_photo(player)
        if photo is None:
            return False
        image.paste(self._focus_crop(photo, (x1 - x0, y1 - y0)), (x0, y0))
        draw.rectangle((x0, y0, x1, y1), outline=colors["gold"], width=1)
        credit = f"HLTV 最新照片 {place}/{len(player.get('photos') or [])}"
        credit_w = self._text_width(draw, credit, fonts["tiny"]) + 10
        draw.rectangle((x0 + 1, y1 - 15, x0 + 1 + credit_w, y1 - 1), fill=colors["background"])
        self._text(draw, (x0 + 6, y1 - 14), credit, fonts["tiny"], colors["ink"])
        return True

    def _draw_cs2_win(self, image, draw, player, win, box, colors, fonts):
        """The team's latest win under a picture of the player (the first map's art without one): a
        最近胜利 tag and the date on the picture, which fades into the page above the opponent and the
        series score, each map with its art, the player's K-D on it and its score, and the player's
        line over the series. False when not even one map fits."""
        x0, y0, x1, y1 = [int(value) for value in box]
        width, height = x1 - x0, y1 - y0
        maps = win.get("maps") or []
        line_h = 14 if win.get("kills") is not None else 0
        rows = maps[:max(0, (height - WIN_TAG_H - WIN_HEADER_H - line_h - 6) // WIN_ROW_H)]
        if not rows:
            return False
        text_top = height - 6 - line_h - WIN_ROW_H * len(rows) - WIN_HEADER_H
        # The picture is cut for the room above the text, so the player stays in view there.
        photo, _place = self._cs2_photo(player)
        art = photo if photo is not None else self._map_art(maps[0].get("map"))
        image.paste(self._top_picture(art, (width, height), text_top, colors), (x0, y0))
        draw.rectangle((x0, y0, x1, y1), outline=colors["gold"], width=1)
        tag = "最近胜利"
        tag_w = self._text_width(draw, tag, fonts["small_bold"]) + 12
        draw.rounded_rectangle((x0 + 5, y0 + 5, x0 + 5 + tag_w, y0 + 21), radius=8, fill=colors["gold"])
        self._text(draw, (x0 + 11, y0 + 7), tag, fonts["small_bold"], colors["background"])
        if isinstance(win.get("start"), (int, float)):
            day = f"{datetime.fromtimestamp(win['start']):%m/%d}"
            day_w = self._text_width(draw, day, fonts["tiny"]) + 8
            draw.rectangle((x1 - 5 - day_w, y0 + 6, x1 - 5, y0 + 20), fill=colors["background"])
            self._text(draw, (x1 - 1 - day_w, y0 + 7), day, fonts["tiny"], colors["ink"])
        y = y0 + text_top
        middle = y + 9
        name_x = x0 + 11 + round(self._match_text(draw, (x0 + 6, middle), "vs", "label", 12, colors["muted"]))
        logo = self._cs2_team_logo(win.get("opponent_logo"), win.get("opponent"), 16, badge=True)
        if logo is not None:
            image.paste(logo, (name_x + (16 - logo.width) // 2, y + 1 + (16 - logo.height) // 2), logo)
            name_x += 20
        score = " : ".join(part.strip() for part in str(win.get("score") or "").split(":"))
        score_w = self._match_text(draw, (x1 - 6, middle), score, "score", 20, colors["gold"], anchor="rm")
        self._match_text(draw, (name_x, middle), self._fitted_name(draw, win.get("opponent") or "-", fonts["small_bold"],
                                                                  x1 - 12 - score_w - name_x),
                         "name", 15, colors["ink"], width=x1 - 12 - score_w - name_x, min_size=10)
        y += WIN_HEADER_H
        for row in rows:
            self._draw_win_map(image, draw, row, (x0 + 6, y, x1 - 6), colors, fonts)
            y += WIN_ROW_H
        if line_h:
            line = (f"{player.get('nick') or ''} 本场 {win['kills']}-{win['deaths']}"
                    + (f" · ADR {win['adr']:.0f}" if isinstance(win.get("adr"), (int, float)) else ""))
            self._match_text(draw, (x0 + 6, y + 7), line.strip(), "label", 12, colors["gold"], width=width - 12, min_size=9)
        return True

    def _top_picture(self, art, size, text_top, colors):
        """``art`` cut for the room above ``text_top`` and fading into the page colour over WIN_FADE
        rows, the page colour below it (all page colour without art)."""
        width, height = size
        canvas = Image.new("RGB", size, colors["background"])
        if art is None:
            return canvas
        shown = max(1, min(height, text_top + WIN_FADE // 2))
        canvas.paste(self._focus_crop(art, (width, shown)), (0, 0))
        ramp = Image.new("L", (1, height), 0)
        for y in range(max(0, shown - WIN_FADE), height):
            ramp.putpixel((0, y), round(255 * min(1.0, (y - shown + WIN_FADE) / WIN_FADE)))
        return Image.composite(Image.new("RGB", size, colors["background"]), canvas, ramp.resize(size))

    def _draw_win_map(self, image, draw, row, line, colors, fonts):
        """One map of the win: a chip of its art edged green (won) or red (lost), its name, the
        player's K-D on it, and the map's score."""
        x0, y, x1 = line
        won = bool(row.get("won"))
        chip_w, chip_h = WIN_MAP_CHIP
        art = self._map_art(row.get("map"))
        if art is not None:
            image.paste(ImageOps.fit(art, WIN_MAP_CHIP, method=Image.Resampling.LANCZOS, centering=(0.5, 0.45)),
                        (x0, y + 1))
        else:
            draw.rectangle((x0, y + 1, x0 + chip_w, y + 1 + chip_h), fill=colors["panel"])
        draw.rectangle((x0, y + 1, x0 + chip_w, y + 1 + chip_h), outline=colors["green"] if won else colors["red"])
        middle = y + 1 + chip_h // 2
        self._match_text(draw, (x1, middle), f"{row.get('ours')}:{row.get('theirs')}", "score", 15,
                         colors["green"] if won else colors["red"], anchor="rm")
        kd_right = x1 - 33
        if row.get("kills") is not None:
            self._match_text(draw, (kd_right, middle), f"{row['kills']}-{row.get('deaths', 0)}", "figure", 13,
                             colors["muted"], anchor="rm")
        name_x = x0 + chip_w + 5
        self._single(draw, (name_x, y), row.get("name") or row.get("english") or "", fonts["small_bold"],
                     colors["ink"] if won else colors["muted"], kd_right - 30 - name_x, 8)

    @staticmethod
    @lru_cache(maxsize=32)
    def _cs2_event_logo_file(name, day):
        """The bundled logo of the event (its dark-page copy first) the sports dashboard keeps, or "".
        A match listed without its date (a result) is matched on HLTV's exact name alone."""
        if not day:
            paths = cs2_branding.local_named_branding(name)
        else:
            card = {"event_name": name, "main": {"event_name": name, "start": f"{day}T12:00:00+00:00"}}
            paths = cs2_branding.local_event_branding(card) or cs2_branding.local_family_branding(card)
        return str(paths.get("event_logo_path_dark") or paths.get("event_logo_path") or "")

    def _cs2_event_logo(self, name, when, links=None, box=CS2_EVENT_LOGO):
        """The event's logo contained in ``box``: the bundled one, else the one HLTV's event lists gave
        for that name (``links``); None when neither has one."""
        if not name:
            return None
        logo = None
        path = self._cs2_event_logo_file(str(name), f"{when:%Y-%m-%d}" if when else "")
        if path and os.path.isfile(path):
            try:
                with Image.open(path) as source:
                    logo = source.convert("RGBA")
            except OSError:
                logo = None
        if logo is None and (links or {}).get(name):
            logo = self._image_from_url(links[name], name)
        if logo is None:
            return None
        logo = self._trim_alpha(logo.convert("RGBA"))
        return ImageOps.contain(logo, box, method=Image.Resampling.LANCZOS) if logo.width > 1 else None

    def _draw_cs2_result(self, image, draw, match, own_logo, line, colors, fonts, team_logo=None, event_logo=None):
        """One result as HLTV lists them: a 胜/负 tag, then both logos around the score, and the event's
        logo at the row's end; a loss is greyed out."""
        x0, y, x1 = line
        if event_logo is not None:
            image.paste(event_logo, (x1 - event_logo.width, y + 3 + (16 - event_logo.height) // 2), event_logo)
            x1 -= event_logo.width + 4
        won = bool(match.get("won"))
        draw.rounded_rectangle((x0, y + 2, x0 + 18, y + 20), radius=4, fill=colors["green"] if won else colors["red"])
        label = "胜" if won else "负"
        self._text(draw, (x0 + (18 - self._text_width(draw, label, fonts["small_bold"])) / 2, y + 4), label,
                   fonts["small_bold"], colors["background"])
        x = x0 + 24
        if own_logo is not None:
            image.paste(own_logo, (x + (16 - own_logo.width) // 2, y + 3 + (16 - own_logo.height) // 2), own_logo)
        x += 20
        ours, theirs = ([part.strip() for part in str(match.get("score") or "").split(":")] + ["-", "-"])[:2]
        # The score in a fixed slot, its colon centred, so every row's score lines up.
        colon_x = x + 22
        self._match_text(draw, (colon_x, y + 11), ":", "score", 18, colors["muted"], anchor="mm")
        self._match_text(draw, (colon_x - 5, y + 11), ours, "score", 18, colors["ink"] if won else colors["muted"],
                         anchor="rm")
        self._match_text(draw, (colon_x + 5, y + 11), theirs, "score", 18, colors["muted"] if won else colors["ink"],
                         anchor="lm")
        x = colon_x + 27
        # The opponent's logo keeps its slot when it does not load, so the names stay in a column.
        logo = (team_logo or partial(self._cs2_team_logo, badge=True))(match.get("opponent_logo"), match.get("opponent"), 16)
        if logo is not None:
            image.paste(logo, (int(x) + (16 - logo.width) // 2, y + 3 + (16 - logo.height) // 2), logo)
        x += 21
        self._match_text(draw, (x, y + 11), self._fitted_name(draw, match.get("opponent") or "-", fonts["small_bold"], x1 - x),
                         "name", 15, colors["ink"] if won else colors["muted"], width=x1 - x, min_size=10)

    def _fitted_name(self, draw, name, font, max_width, min_size=8):
        """``name`` when it fits ``max_width`` at ``min_size`` or larger; else a long team name's
        initials (Ninjas in Pyjamas -> NIP), or the name cut short with an ellipsis."""
        name = str(name or "")
        if self._text_width(draw, name, self._font(min_size, bold=self._is_bold_font(font))) <= max_width:
            return name
        words = re.findall(r"[A-Za-z0-9]+", name)
        if len(words) >= 2:
            return "".join(word[0] for word in words).upper()
        small = self._font(min_size, bold=self._is_bold_font(font))
        while len(name) > 1 and self._text_width(draw, name + "…", small) > max_width:
            name = name[:-1]
        return name + "…"

    def _draw_cs2_maps(self, image, draw, pool, box, colors, fonts):
        """The best map's art with its numbers, then the next maps of the pool by score."""
        x0, y0, x1, y1 = box
        best = pool[0]
        self._text(draw, (x0, y0), "擅长地图", fonts["small_bold"], colors["muted"])
        note = "近 6 个月 · bo3.gg"
        self._text(draw, (x1 - self._text_width(draw, note, fonts["tiny"]), y0 + 2), note, fonts["tiny"], colors["muted"])
        thumb_h = 54 if y1 - y0 >= 110 else 40
        top = y0 + 18
        art = self._map_art(best["map"])
        if art is not None:
            thumb = ImageOps.fit(art, (x1 - x0, thumb_h), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
            # Darken the art from the left so the map's name reads over it.
            shade = Image.linear_gradient("L").rotate(90, expand=True).resize(thumb.size)
            thumb = Image.composite(Image.new("RGB", thumb.size, colors["background"]), thumb,
                                    shade.point(lambda value: int((255 - value) * 0.78)))
            image.paste(thumb, (x0, top))
        draw.rectangle((x0, top, x1, top + thumb_h), outline=colors["gold"], width=1)
        self._text(draw, (x0 + 6, top + 4), best["name"], fonts["body"], colors["ink"])
        self._text(draw, (x0 + 6, top + 22), best["english"], fonts["tiny"], colors["muted"])
        score = f"{best['rating']:.1f}"
        score_font = self._font(20, bold=True)
        self._text(draw, (x1 - 6 - self._text_width(draw, score, score_font), top + thumb_h - 28), score, score_font,
                   colors["gold"])
        y = top + thumb_h + 3
        facts = f"{best['maps']} 张图 · 场均击杀 {best['kpr']:.2f} · ADR {best['adr']:.0f}"
        self._single(draw, (x0, y), facts, fonts["tiny"], colors["ink"], x1 - x0, 7)
        y += 15
        for row in pool[1:3]:
            if y + 13 > y1:
                break
            self._single(draw, (x0, y), row["name"], fonts["tiny"], colors["ink"], 66, 7)
            bar = (x0 + 70, y + 4, x1 - 54, y + 10)
            draw.rectangle(bar, fill=colors["bar"])
            fill = bar[0] + (bar[2] - bar[0]) * max(0.05, min(1.0, row["rating"] / 10))
            draw.rectangle((bar[0], bar[1], int(fill), bar[3]), fill=colors["gold"])
            text = f"{row['rating']:.1f} · {row['maps']} 图"
            self._text(draw, (x1 - self._text_width(draw, text, fonts["tiny"]), y), text, fonts["tiny"], colors["muted"])
            y += 15

    def _draw_cs2_board(self, image, draw, board, box, player, data, colors, fonts):
        """The Top 20: medals for the first three, team logos, a Rating bar and how it moved."""
        rated = [row for row in board if isinstance(row.get("rating"), (int, float))]
        self._board_frame(draw, box, f"HLTV {data.get('top20_year')} 前 20",
                          f"近 3 个月 Rating · {len(rated)} 已更新", colors, fonts, fill=False, image=image,
                          logo=HLTV_LOGO)
        best = max((row["rating"] for row in rated), default=None)
        low, high = RATING_SPAN
        for index, cell in self._board_cells(box, len(board)):
            row = board[index]
            if row["nick"] == player.get("nick"):
                self._highlight(draw, cell, colors)
            left, top, right, bottom = cell
            middle = (top + bottom) / 2
            rank = int(row.get("rank") or index + 1)
            if rank <= len(MEDALS):
                draw.ellipse((left - 1, middle - 9, left + 17, middle + 9), fill=MEDALS[rank - 1])
                self._text(draw, (left + 8 - self._text_width(draw, str(rank), fonts["small_bold"]) / 2, middle - 7),
                           rank, fonts["small_bold"], colors["background"])
            else:
                self._text(draw, (left, middle - 7), f"{rank:>2}", fonts["small_bold"], colors["muted"])
            logo = self._cs2_team_logo(row.get("team_logo"), row.get("team"), 16, badge=True)
            if logo is not None:
                image.paste(logo, (int(left + 21 + (16 - logo.width) / 2), int(middle - logo.height / 2)), logo)
            self._single(draw, (left + 41, middle - 13), row["nick"], fonts["small_bold"], colors["ink"], 76, 8)
            self._single(draw, (left + 41, middle + 1), row.get("team") or "", fonts["tiny"], colors["muted"], 78, 7)
            rating = row.get("rating")
            if not isinstance(rating, (int, float)):
                self._text(draw, (right - self._text_width(draw, "-", fonts["small_bold"]), middle - 7), "-",
                           fonts["small_bold"], colors["muted"])
                continue
            text = f"{rating:.2f}"
            colour = colors["gold"] if rating == best or rating >= 1.15 else colors["ink"]
            text_x = right - self._text_width(draw, text, fonts["small_bold"])
            self._text(draw, (text_x, middle - 13), text, fonts["small_bold"], colour)
            previous = row.get("rating_prev")
            if isinstance(previous, (int, float)) and previous != rating:
                up = rating > previous
                ax, ay = text_x - 9, middle - 6
                arrow = [(ax, ay + 5), (ax + 7, ay + 5), (ax + 3.5, ay - 1)] if up else [(ax, ay - 1), (ax + 7, ay - 1),
                                                                                        (ax + 3.5, ay + 5)]
                draw.polygon(arrow, fill=colors["green"] if up else colors["red"])
            bar = (right - 40, middle + 4, right, middle + 8)
            draw.rectangle(bar, fill=colors["bar"])
            share = max(0.06, min(1.0, (rating - low) / (high - low)))
            draw.rectangle((bar[0], bar[1], int(bar[0] + (bar[2] - bar[0]) * share), bar[3]), fill=colour)
