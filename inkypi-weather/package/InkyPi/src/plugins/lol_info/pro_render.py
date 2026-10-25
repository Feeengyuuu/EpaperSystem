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

from .bo3_stats import MAP_NAMES
from .hltv_players import ROLE_NAMES as CS2_ROLES
from .pro_roster import ROLE_LABELS

HERO_H = 152
# The game's logo stands in the middle of the hero band, between the player and the rank.
GAME_LOGO_BOX = (318, 44, 492, 108)
# The player's photo takes the band's full height on the left.
PHOTO_BOX = (170, HERO_H)
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

    def _board_frame(self, draw, box, title, detail, colors, fonts, fill=True):
        x0, y0, x1, y1 = box
        draw.rectangle(box, fill=colors["panel"] if fill else None, outline=colors["rule"], width=1)
        self._text(draw, (x0 + 12, y0 + 7), title, fonts["section"], colors["ink"])
        self._single(draw, (x0 + 12 + self._text_width(draw, title, fonts["section"]) + 8, y0 + 12), detail,
                     fonts["tiny"], colors["muted"], x1 - x0 - 140, 8)

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
            band = ImageOps.fit(ImageOps.exif_transpose(raw).convert("RGB"), (800, HERO_H), method=Image.Resampling.LANCZOS,
                                centering=(0.62, 0.3))
            image.paste(band, (0, 0))
        self._hero_shade(image, colors)
        draw = ImageDraw.Draw(image)
        # The game's logo stands centred in the band; the pro's official lolesports photo fills its left.
        self._paste_asset_logo(image, "league-of-legends-logo.png", GAME_LOGO_BOX)
        photo = self._image_from_url(pick.get("photo"), account.get("label")) if pick.get("photo") else None
        left = 18
        if photo is not None:
            photo = ImageOps.contain(photo.convert("RGBA"), PHOTO_BOX, method=Image.Resampling.LANCZOS)
            image.paste(photo, (8, HERO_H - photo.height), photo)
            left = max(150, 8 + photo.width + 12)
        width = GAME_LOGO_BOX[0] - 8 - left
        self._single(draw, (left, 26), account.get("label") or "-", self._font(46, bold=True), colors["ink"], width, 22)
        x = left + 2
        logo = self._team_logo(account.get("team"), 30)
        team_y = 88
        if logo:
            image.paste(logo, (x, team_y + (30 - logo.height) // 2), logo)
            x += logo.width + 8
        role = account.get("role") or ""
        line = " · ".join(part for part in (account.get("team"), ROLE_LABELS.get(role, "")) if part)
        team_font = self._font(18, bold=True)
        self._single(draw, (x, team_y + 4), line, team_font, colors["ink"], left + width - x - 28, 12)
        x += min(self._text_width(draw, line, team_font), left + width - x - 28) + 8
        role_icon = self._asset_logo(f"roles/{role.lower()}.png", (24, 24)) if role else None
        if role_icon:
            image.paste(role_icon, (int(x), team_y + 3), role_icon)
        riot_id = f"{account.get('gameName', '')} #{account.get('tagLine', '')} · {str(account.get('platformRoute', '')).upper()}"
        self._single(draw, (left + 2, 126), riot_id, fonts["text"], colors["muted"], width, 8)

        emblem = self._rank_emblem_image(ranked, 92) if ranked else None
        if emblem:
            image.paste(emblem, (540, 24 + (92 - emblem.height) // 2), emblem)
        wins, losses = int(ranked.get("wins") or 0), int(ranked.get("losses") or 0)
        self._text(draw, (642, 30), self._rank_text(ranked) or "暂无排位", fonts["body"], colors["gold"])
        self._text(draw, (642, 52), f"{int(ranked.get('leaguePoints') or 0):,} LP" if ranked else "-", fonts["big"],
                   colors["ink"])
        if ranked:
            rate = wins / max(1, wins + losses) * 100
            self._text(draw, (642, 88), f"{wins}胜 {losses}负 · {rate:.1f}%", fonts["small"],
                       colors["green"] if rate >= 50 else colors["muted"])
        active = data.get("active_game")
        if active:
            draw.rounded_rectangle((690, 10, 786, 32), radius=11, fill=colors["green"])
            self._text(draw, (704, 13), "对局中", fonts["small_bold"], colors["background"])
        self._tracker_badge(draw, pick, colors, fonts)

        self._draw_lol_recent(image, draw, data, LEFT, colors, fonts)
        self._draw_lol_board(image, draw, board, RIGHT, account, colors, fonts)
        return image if tuple(dimensions) == (800, 480) else image.resize(tuple(dimensions), Image.Resampling.LANCZOS)

    def _draw_lol_recent(self, image, draw, data, box, colors, fonts):
        x0, y0, x1, y1 = box
        draw.rectangle(box, fill=colors["panel"], outline=colors["rule"], width=1)
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
        self._board_frame(draw, box, "职业天梯", f"{len(board)} 位选手 · {len(known)} 已更新 · 单双排", colors, fonts)
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
        draw.rectangle(LEFT, outline=colors["rule"], width=1)
        self._text(draw, (x0 + 12, y0 + 7), "角色能力", fonts["section"], colors["ink"])
        roles = player.get("roles") or {}
        average = self._cs2_role_average(data.get("board") or [])
        self._draw_role_tag(draw, roles, (x0 + 88, y0 + 9), colors, fonts)
        self._draw_radar(draw, (x0 + 100, y0 + 164), 70, roles, colors, fonts, average)
        self._draw_signature_role(draw, player, data.get("board") or [], average, (x0 + 12, y1 - 40, x0 + 206),
                                  colors, fonts)
        self._draw_cs2_matches(image, draw, player, (x0 + 214, y0 + 8, x1 - 10, y1 - 8), colors, fonts)
        self._frost(image, RIGHT, colors)
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
        sharp = ImageOps.fit(art, image.size, method=Image.Resampling.LANCZOS)
        base = Image.new("RGB", image.size, colors["background"])
        page = Image.blend(base, sharp.filter(ImageFilter.GaussianBlur(CS2_PAGE_BLUR)), CS2_PAGE_DIM)
        hero = Image.blend(base, sharp, CS2_HERO_DIM)
        self._hero_shade(hero, colors, height=image.height)
        start, end = CS2_HERO_FADE
        rows = Image.new("L", (1, image.height), 0)
        for y in range(image.height):
            t = min(1.0, max(0.0, (y - start) / (end - start)))
            rows.putpixel((0, y), round(255 * (1 - t * t * (3 - 2 * t))))
        image.paste(Image.composite(hero, page, rows.resize(image.size)))
        return True

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

    def _draw_radar(self, draw, centre, radius, roles, colors, fonts, average=None):
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
        self._text(draw, (x0, y0), "赛程与战绩", fonts["small_bold"], colors["muted"])
        y = y0 + 20
        # With a map pool to show, the next match is enough; without one, the next two.
        for match in (player.get("upcoming") or [])[:1 if pool else 2]:
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
        if pool and y1 - y >= 70:
            self._draw_cs2_maps(image, draw, pool, (x0, y + 8, x1, y1), colors, fonts)
        elif y1 - y >= 50:
            self._paste_asset_logo(image, "art-cs2-filler.png", (x0, y + 6, x1, y1))

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
                          f"近 3 个月 Rating · {len(rated)} 已更新", colors, fonts, fill=False)
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
