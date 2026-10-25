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
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .hltv_players import ROLE_LABELS as CS2_ROLE_LABELS
from plugins.sports_dashboard.common import LOCAL_CS2_TEAM_LOGO_DIR
from utils.fonts import load_font

from .bo3_stats import MAP_NAMES
from .hltv_players import ROLE_NAMES as CS2_ROLES
from .pro_roster import ROLE_LABELS

HERO_H = 152
# The LoL band: the pro's photo (its transparent margins trimmed) at the left at nearly the
# band's height, the ID and the filed details beside it, then the game's logo in its own
# column centred on the photo's height, then the rank.
PHOTO_BOX = (190, HERO_H - 6)
GAME_LOGO_COLUMN = (406, 548)
INFO_LABEL_W = 34
# Leaguepedia's face for a player's ID (Rubik, SIL OFL).
ID_FONT = "fonts/Rubik-Bold.ttf"
# The splash keeps the band's framing and runs on down the page under the boards, dimmed,
# fading into the page over the last LOL_SPLASH_TAIL rows it covers.
LOL_PAGE_DIM, LOL_SPLASH_TAIL = 0.42, 96
# The cards' backdrops follow what they show: the tracked team's logo behind the board, the
# latest game's champion behind the recent games, and on the CS2 page a radar sweep turned
# onto the player's best role, lit only inside the player's own role shape.
CARD_MARK_ALPHA, CARD_ART_ALPHA = 0.15, 0.36
CHAMPION_LOADING_URL = "https://ddragon.leagueoflegends.com/cdn/img/champion/loading/{key}_0.jpg"
RADAR_TRAIL, RADAR_SWEEP_ALPHA = 70, 0.34
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


class ProRenderMixin:
    # -- shared pieces -------------------------------------------------------------------
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
    def _render_lol_pro(self, data, dimensions, theme_context, pick, board):
        colors = self._render_colors(theme_context)
        fonts = self._pro_fonts()
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

        self._draw_lol_recent(image, draw, data, LEFT, colors, fonts)
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
        server = str(account.get("platformRoute") or "").upper()
        chip_w = self._text_width(draw, server, fonts["tiny"]) + 10 if server else 0
        riot_id = f"{account.get('gameName', '')} #{account.get('tagLine', '')}"
        fitted = self._fit_font(draw, riot_id, fonts["text"], right - value_x - chip_w - 6, 8)
        draw.text((value_x, y + 1), riot_id, font=fitted, fill=colors["ink"])
        if server:
            x = value_x + self._text_width(draw, riot_id, fitted) + 6
            draw.rounded_rectangle((x, y + 1, x + chip_w, y + 16), radius=4, outline=colors["rule"])
            self._text(draw, (x + 5, y + 3), server, fonts["tiny"], colors["muted"])

    def _draw_lol_recent(self, image, draw, data, box, colors, fonts):
        x0, y0, x1, y1 = box
        self._frost(image, box, colors)
        self._champion_wash(image, ((data.get("matches") or [{}])[0] or {}).get("champion_key"), box)
        draw.rectangle(box, outline=colors["rule"], width=1)
        self._text(draw, (x0 + 12, y0 + 7), "最近比赛", fonts["section"], colors["ink"])
        latest = (data.get("matches") or [{}])[0].get("timestamp")
        if latest:
            # Pros go quiet in solo queue around tournaments: say when the newest game was.
            note = f"最后一局 {self._relative(latest)}"
            self._text(draw, (x1 - 12 - self._text_width(draw, note, fonts["tiny"]), y0 + 12), note, fonts["tiny"],
                       colors["muted"])
        champions = data.get("champions") or {}
        y = y0 + 36
        for match in (data.get("matches") or [])[:5]:
            icon = self._icon_from_url(match.get("champion_icon"), 26, match.get("champion_name"))
            image.paste(icon, (x0 + 12, y), icon)
            self._single(draw, (x0 + 46, y + 4), self._champion_full_name_from_match(match, champions), fonts["text"],
                         colors["ink"], 132, 9)
            self._text(draw, (x0 + 186, y + 4), f"{match['kills']}/{match['deaths']}/{match['assists']}", fonts["body"],
                       colors["ink"])
            won = bool(match.get("win"))
            badge = self._asset_logo(f"icons/{'victory' if won else 'defeat'}.png", (26, 26))
            if badge:
                image.paste(badge, (x0 + 261, y), badge)
            else:
                chip = (x0 + 262, y + 3, x0 + 286, y + 23)
                draw.rounded_rectangle(chip, radius=4, fill=colors["green"] if won else colors["red"])
                self._text(draw, (chip[0] + 5, y + 5), "胜" if won else "负", fonts["small_bold"], colors["background"])
            self._text(draw, (x0 + 296, y + 1), self._lane_label(match.get("lane")), fonts["tiny"], colors["muted"])
            self._text(draw, (x0 + 296, y + 14), self._relative(match.get("timestamp")), fonts["tiny"], colors["muted"])
            y += 32
        if not data.get("matches"):
            self._text(draw, (x0 + 14, y0 + 60), "暂无近期比赛", fonts["text"], colors["muted"])
        # The strip between the games and the numbers: a generated arena vignette, then the top champions.
        mastery = (data.get("mastery") or [])[:3]
        art_right = x0 + 112 if mastery else x1 - 12
        self._paste_asset_logo(image, "art-lol-filler.png", (x0 + 8, y1 - 112, art_right, y1 - 62))
        if mastery:
            self._text(draw, (x0 + 120, y1 - 112), "常用英雄", fonts["small_bold"], colors["cyan"])
            slot = (x1 - x0 - 132) / 3
            for index, item in enumerate(mastery):
                left = int(x0 + 120 + index * slot)
                icon = self._icon_from_url(item.get("champion_icon"), 22, item.get("champion_name"))
                image.paste(icon, (left, y1 - 92), icon)
                self._single(draw, (left + 26, y1 - 94), item.get("champion_name"), fonts["tiny"], colors["ink"],
                             slot - 30, 7)
                self._text(draw, (left + 26, y1 - 81), self._compact(item.get("points")), fonts["tiny"],
                           colors["muted"])
        summary = data.get("summary") or {}
        tiles = (("kda", "KDA", f"{summary.get('kda', 0):.2f}", colors["gold"]),
                 ("winrate", "胜率", f"{summary.get('winrate', 0):.0f}%",
                  colors["green"] if summary.get("winrate", 0) >= 50 else colors["red"]),
                 ("kill_participation", "参团", f"{summary.get('kp', 0):.0f}%", colors["cyan"]),
                 ("cs_per_minute", "补刀/分", f"{summary.get('cs_per_min', 0):.1f}", colors["gold"]))
        tile_w = (x1 - x0 - 24) / 4
        for index, (icon_name, label, value, colour) in enumerate(tiles):
            left = x0 + 12 + index * tile_w
            draw.line((left, y1 - 56, left + tile_w - 8, y1 - 56), fill=colors["rule"], width=1)
            icon = self._asset_logo(f"icons/{icon_name}.png", (17, 17))
            label_x = left
            if icon:
                image.paste(icon, (int(left), y1 - 53), icon)
                label_x = left + 20
            self._text(draw, (label_x, y1 - 50), label, fonts["tiny"], colors["muted"])
            self._single(draw, (left, y1 - 36), value, fonts["big"], colour, tile_w - 10, 12)

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
        self._text(draw, (618, 38), f"{rating:.2f}" if isinstance(rating, (int, float)) else "-", self._font(46, bold=True),
                   colors["gold"])
        self._single(draw, (620, 98), self._cs2_window(player), fonts["small"], colors["muted"], 166, 8)
        self._tracker_badge(draw, pick, colors, fonts)

        x0, y0, x1, y1 = LEFT
        self._frost(image, LEFT, colors)
        roles = player.get("roles") or {}
        draw.rectangle(LEFT, outline=colors["rule"], width=1)
        self._text(draw, (x0 + 12, y0 + 7), "角色能力", fonts["section"], colors["ink"])
        average = self._cs2_role_average(data.get("board") or [])
        self._draw_role_tag(draw, roles, (x0 + 88, y0 + 9), colors, fonts)
        self._draw_radar(image, draw, (x0 + 100, y0 + 164), 70, roles, colors, fonts, average)
        self._draw_signature_role(draw, player, data.get("board") or [], average, (x0 + 12, y1 - 40, x0 + 206),
                                  colors, fonts)
        self._draw_cs2_matches(image, draw, player, (x0 + 214, y0 + 8, x1 - 10, y1 - 8), colors, fonts)
        self._frost(image, RIGHT, colors)
        self._team_mark(image, self._cs2_team_logo(player.get("team_logo_night") or player.get("team_logo"),
                                                   player.get("team"), 150), RIGHT)
        self._draw_cs2_board(image, draw, data.get("board") or [], RIGHT, player, data, colors, fonts)
        return image if tuple(dimensions) == (800, 480) else image.resize(tuple(dimensions), Image.Resampling.LANCZOS)

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
    def _draw_radar_sweep(image, centre, radius, roles, shape, colors):
        """A radar screen around the role chart: a scope ring, and a gold beam swept onto the
        player's best role with its trail fading out behind it. The beam lights only the
        player's own shape, so no gold reaches past the scores it stands for."""
        best = max(CS2_ROLES, key=lambda name: roles.get(name) or 0)
        heading = -90 + 360 * CS2_ROLES.index(best) / len(CS2_ROLES)
        cx, cy = centre
        reach = radius * 1.12
        scope = (cx - reach, cy - reach, cx + reach, cy + reach)
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        steps = 28
        for step in range(steps):
            alpha = round(255 * RADAR_SWEEP_ALPHA * (1 - step / steps) ** 1.8)
            pen.pieslice(scope, heading - RADAR_TRAIL * (step + 1) / steps, heading - RADAR_TRAIL * step / steps,
                         fill=(*colors["gold"][:3], alpha))
        angle = math.radians(heading)
        pen.line((cx, cy, cx + math.cos(angle) * reach, cy + math.sin(angle) * reach),
                 fill=(*colors["gold"][:3], 210), width=2)
        inside = Image.new("L", image.size, 0)
        ImageDraw.Draw(inside).polygon(shape, fill=255)
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), inside))
        ImageDraw.Draw(layer).ellipse(scope, outline=(*colors["rule"][:3], 255), width=1)
        base = image.convert("RGBA")
        base.alpha_composite(layer)
        image.paste(base.convert("RGB"))

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

    def _cs2_team_logo(self, url, label, size):
        """The team's bundled logo when the sports dashboard has one, else HLTV's (None for an SVG)."""
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
            return None
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
        if not label:
            return
        x, y = origin
        width = self._text_width(draw, label, fonts["small_bold"]) + 14
        draw.rounded_rectangle((x, y, x + width, y + 18), radius=9, fill=colors["gold"])
        self._text(draw, (x + 7, y + 2), label, fonts["small_bold"], colors["background"])

    def _draw_radar(self, image, draw, centre, radius, roles, colors, fonts, average=None):
        cx, cy = centre
        count = len(CS2_ROLES)
        points = lambda scale: [(cx + math.sin(2 * math.pi * i / count) * radius * scale,
                                 cy - math.cos(2 * math.pi * i / count) * radius * scale) for i in range(count)]

        def shape_of(scores):
            values = [max(4, min(100, scores.get(name, 0))) / 100 for name in CS2_ROLES]
            return [(cx + (x - cx) * value, cy + (y - cy) * value) for (x, y), value in zip(points(1.0), values)]

        shape = shape_of(roles) if roles else []
        if shape:
            draw.polygon(shape, fill=self._blend(colors["gold"], colors["panel"], 0.28))
            self._draw_radar_sweep(image, centre, radius, roles, shape, colors)
        # The grid goes over the fill so the rings stay readable inside the shape.
        for scale in (0.33, 0.66, 1.0):
            draw.polygon(points(scale), outline=colors["rule"])
        for x, y in points(1.0):
            draw.line((cx, cy, x, y), fill=colors["rule"], width=1)
        if average:
            # The Top 20 average as a cyan outline, so every role reads as above or below it.
            outline = shape_of(average)
            draw.line(outline + outline[:1], fill=colors["cyan"], width=2)
        if shape:
            draw.line(shape + shape[:1], fill=colors["gold"], width=2)
            for x, y in shape:
                draw.ellipse((x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=colors["gold"])
        for (x, y), name in zip(points(1.24), CS2_ROLES):
            score = roles.get(name)
            label = f"{CS2_ROLE_LABELS[name]} {score if score is not None else '-'}"
            width = self._text_width(draw, label, fonts["tiny"])
            colour = colors["ink"]
            if isinstance(score, (int, float)) and average:
                colour = (colors["gold"] if score >= average[name] + ROLE_MARGIN else
                          colors["muted"] if score <= average[name] - ROLE_MARGIN else colors["ink"])
            elif isinstance(score, (int, float)) and score >= 80:
                colour = colors["gold"]
            self._text(draw, (x - width / 2, y - 6), label, fonts["tiny"], colour)

    def _draw_signature_role(self, draw, player, board, average, line, colors, fonts):
        """The player's strongest role with its place among the Top 20, and the radar's legend."""
        x0, y, x1 = line
        roles = player.get("roles") or {}
        if not roles:
            return
        best = max(CS2_ROLES, key=lambda name: roles.get(name, 0))
        scores = sorted((row["roles"].get(best, 0) for row in board if row.get("roles")), reverse=True)
        place = 1 + sum(1 for score in scores if score > roles.get(best, 0))
        self._text(draw, (x0, y), "招牌", fonts["tiny"], colors["muted"])
        text = f"{CS2_ROLE_LABELS[best]} {roles.get(best)}"
        self._text(draw, (x0 + 26, y - 2), text, fonts["body"], colors["gold"])
        if len(scores) >= 3:
            self._text(draw, (x0 + 30 + self._text_width(draw, text, fonts["body"]), y), f"前 20 第 {place}", fonts["tiny"],
                       colors["ink"])
        if average:
            legend_y = y + 18
            draw.line((x0, legend_y + 6, x0 + 14, legend_y + 6), fill=colors["gold"], width=2)
            self._text(draw, (x0 + 18, legend_y), "本人", fonts["tiny"], colors["muted"])
            draw.line((x0 + 50, legend_y + 6, x0 + 64, legend_y + 6), fill=colors["cyan"], width=2)
            self._text(draw, (x0 + 68, legend_y), "前 20 平均", fonts["tiny"], colors["muted"])

    def _draw_cs2_matches(self, image, draw, player, box, colors, fonts):
        x0, y0, x1, y1 = box
        pool = player.get("map_pool") or []
        photos = player.get("photos") or []
        self._text(draw, (x0, y0), "赛程与战绩", fonts["small_bold"], colors["muted"])
        y = y0 + 20
        # With a photo or a map pool to show, the next match is enough; without either, the next two.
        for match in (player.get("upcoming") or [])[:1 if pool or photos else 2]:
            when = datetime.fromtimestamp(match["unix_ms"] / 1000)
            self._text(draw, (x0, y), f"{when:%m/%d %H:%M}", fonts["tiny"], colors["cyan"])
            self._single(draw, (x0, y + 12), f"vs {match.get('opponent')}", fonts["small_bold"], colors["ink"], x1 - x0, 8)
            self._single(draw, (x0, y + 27), match.get("event"), fonts["tiny"], colors["muted"], x1 - x0, 7)
            y += 44
        own_logo = self._cs2_team_logo(player.get("team_logo_night") or player.get("team_logo"), player.get("team"), 16)
        for match in (player.get("recent") or [])[:4]:
            self._draw_cs2_result(image, draw, match, own_logo, (x0, y, x1), colors, fonts)
            y += 24
            if y > y1 - 20:
                break
        if not player.get("upcoming") and not player.get("recent"):
            self._text(draw, (x0, y), "暂无比赛信息", fonts["small"], colors["muted"])
            y += 22
        # The room under the results holds the newest HLTV picture of the player (the best map still
        # lies behind the page); without one, the map pool, else the art.
        if photos and y1 - y >= 50 and self._draw_cs2_photo(image, draw, player, (x0, y + 8, x1, y1), colors, fonts):
            return
        if pool and y1 - y >= 70:
            self._draw_cs2_maps(image, draw, pool, (x0, y + 8, x1, y1), colors, fonts)
        elif y1 - y >= 50:
            self._paste_asset_logo(image, "art-cs2-filler.png", (x0, y + 6, x1, y1))

    def _draw_cs2_photo(self, image, draw, player, box, colors, fonts):
        """The newest picture HLTV has of the player, framed like the map art with its credit; False
        when none loads, so the room keeps its art."""
        x0, y0, x1, y1 = [int(value) for value in box]
        photo = next((image_ for image_ in (self._image_from_url(url, player.get("nick"))
                                             for url in (player.get("photos") or [])[:3]) if image_ is not None), None)
        if photo is None:
            return False
        fitted = ImageOps.fit(photo.convert("RGB"), (x1 - x0, y1 - y0), method=Image.Resampling.LANCZOS,
                              centering=(0.5, 0.35))
        image.paste(fitted, (x0, y0))
        draw.rectangle((x0, y0, x1, y1), outline=colors["gold"], width=1)
        credit = "HLTV 最新照片"
        credit_w = self._text_width(draw, credit, fonts["tiny"]) + 10
        draw.rectangle((x0 + 1, y1 - 15, x0 + 1 + credit_w, y1 - 1), fill=colors["background"])
        self._text(draw, (x0 + 6, y1 - 14), credit, fonts["tiny"], colors["ink"])
        return True

    def _draw_cs2_result(self, image, draw, match, own_logo, line, colors, fonts):
        """One result as HLTV lists them: a 胜/负 tag, then both logos around the score; a loss is greyed out."""
        x0, y, x1 = line
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
        for text, bright in ((ours, won), (":", False), (theirs, not won)):
            self._text(draw, (x, y + 2), text, fonts["body"], colors["ink"] if bright else colors["muted"])
            x += self._text_width(draw, text, fonts["body"]) + 3
        x += 2
        # The opponent's logo keeps its slot when it does not load, so the names stay in a column.
        logo = self._cs2_team_logo(match.get("opponent_logo"), match.get("opponent"), 16)
        if logo is not None:
            image.paste(logo, (int(x) + (16 - logo.width) // 2, y + 3 + (16 - logo.height) // 2), logo)
        x += 20
        self._single(draw, (x, y + 3), match.get("opponent") or "-", fonts["small_bold"],
                     colors["ink"] if won else colors["muted"], x1 - x, 8)

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
            logo = self._cs2_team_logo(row.get("team_logo"), row.get("team"), 16)
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
