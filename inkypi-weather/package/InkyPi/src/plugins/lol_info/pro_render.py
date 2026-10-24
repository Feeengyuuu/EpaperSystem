"""Pages of the pro tracker: one LoL pro's account or one CS2 player, beside the board of all twenty.

Both pages share a 152 px hero band naming the player (champion splash or player photo
behind), then the player's recent games and numbers on the left and the board of the
twenty tracked players on the right, the shown one highlighted.
"""

from __future__ import annotations

import math
from datetime import datetime

from PIL import Image, ImageDraw, ImageOps

from .hltv_players import ROLE_LABELS as CS2_ROLE_LABELS
from .hltv_players import ROLE_NAMES as CS2_ROLES
from .pro_roster import ROLE_LABELS

HERO_H = 152
# The game logo heading the page takes the band's top-left corner; the photo starts below it.
TITLE_H = 42
LEFT = (14, 160, 398, 470)
RIGHT = (408, 160, 786, 470)
TIER_SHORT = {"CHALLENGER": "王者", "GRANDMASTER": "宗师", "MASTER": "大师", "DIAMOND": "钻石", "EMERALD": "翡翠",
              "PLATINUM": "铂金", "GOLD": "黄金", "SILVER": "白银", "BRONZE": "青铜", "IRON": "黑铁"}
APEX_TIERS = {"CHALLENGER", "GRANDMASTER", "MASTER"}


class ProRenderMixin:
    # -- shared pieces -------------------------------------------------------------------
    def _pro_fonts(self):
        return {"hero": self._font(38, bold=True), "big": self._font(26, bold=True), "section": self._font(16, bold=True),
                "body": self._font(14, bold=True), "text": self._font(13), "small": self._font(11),
                "small_bold": self._font(11, bold=True), "tiny": self._font(10)}

    def _hero_shade(self, image, colors):
        """Darken the band from the left so the name reads over any art."""
        width = image.width
        shade = Image.new("RGB", (width, HERO_H), colors["background"])
        mask = Image.linear_gradient("L").rotate(90, expand=True).resize((width, HERO_H))
        mask = mask.point(lambda value: int(70 + (255 - value) * 0.72))
        band = image.crop((0, 0, width, HERO_H))
        image.paste(Image.composite(shade, band, mask), (0, 0))

    def _team_logo(self, key, size):
        return self._asset_logo(f"teams/{str(key or '').lower()}.png", (size, size))

    def _board_frame(self, draw, box, title, detail, colors, fonts):
        x0, y0, x1, y1 = box
        draw.rectangle(box, fill=colors["panel"], outline=colors["rule"], width=1)
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
        draw.rounded_rectangle((cell[0] - 4, cell[1] + 1, cell[2] + 2, cell[3] - 1), radius=5,
                               fill=colors["surface"], outline=colors["gold"], width=1)

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
        # The game's logo titles the page at the top left; the pro's official lolesports photo stands below it.
        self._paste_asset_logo(image, "league-of-legends-logo.png", (12, 8, 122, 40))
        photo = self._image_from_url(pick.get("photo"), account.get("label")) if pick.get("photo") else None
        left = 18
        if photo is not None:
            photo = ImageOps.contain(photo.convert("RGBA"), (130, HERO_H - TITLE_H), method=Image.Resampling.LANCZOS)
            image.paste(photo, (12, HERO_H - photo.height), photo)
            left = max(132, 12 + photo.width + 14)
        width = 528 - left
        self._single(draw, (left, 46), account.get("label") or "-", fonts["hero"], colors["ink"], width, 20)
        x = left + 2
        logo = self._team_logo(account.get("team"), 24)
        if logo:
            image.paste(logo, (x, 99 + (24 - logo.height) // 2), logo)
            x += logo.width + 8
        role = account.get("role") or ""
        line = " · ".join(part for part in (account.get("team"), ROLE_LABELS.get(role, "")) if part)
        self._text(draw, (x, 100), line, fonts["body"], colors["ink"])
        x += self._text_width(draw, line, fonts["body"]) + 8
        role_icon = self._asset_logo(f"roles/{role.lower()}.png", (20, 20)) if role else None
        if role_icon:
            image.paste(role_icon, (x, 101), role_icon)
        riot_id = f"{account.get('gameName', '')} #{account.get('tagLine', '')} · {str(account.get('platformRoute', '')).upper()}"
        self._single(draw, (left + 2, 128), riot_id, fonts["small"], colors["muted"], width, 8)

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
        draw = ImageDraw.Draw(image)
        player = data.get("player") or {}
        draw.rectangle((0, 0, 800, HERO_H), fill=colors["panel"])
        for offset in range(-HERO_H, 800, 18):
            draw.line((offset, HERO_H, offset + HERO_H, 0), fill=colors["background_stripe"], width=6)
        # The game's logo titles the page at the top left; the player's bodyshot stands below it.
        self._paste_asset_logo(image, "cs2-logo.png", (12, 10, 140, 36), tint=colors["muted"])
        photo = self._image_from_url(player.get("bodyshot"), player.get("nick"))
        x = 20
        if photo is not None:
            photo = ImageOps.contain(photo.convert("RGBA"), (150, HERO_H - TITLE_H), method=Image.Resampling.LANCZOS)
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
        logo = self._image_from_url(player.get("team_logo_night") or player.get("team_logo"), player.get("team"))
        team_x = x
        if logo is not None and logo.width > 1:
            logo = ImageOps.contain(logo.convert("RGBA"), (22, 22), method=Image.Resampling.LANCZOS)
            image.paste(logo, (x, 123), logo)
            team_x += 28
        team_line = f"{player.get('team') or '无战队'} · HLTV {data.get('top20_year')} 第 {player.get('rank', '-')} 名"
        self._text(draw, (team_x, 125), team_line, fonts["small_bold"], colors["gold"])
        # Generated shooter props fill the band between the name block and the rating.
        text_right = max(name_right, team_x + self._text_width(draw, team_line, fonts["small_bold"]),
                         name_x + self._text_width(draw, " · ".join(part for part in (player.get("name"), age) if part),
                                                   fonts["text"]))
        if 600 - (text_right + 18) >= 90:
            self._paste_asset_logo(image, "art-cs2-hero.png", (int(text_right) + 18, 18, 600, 132))
        self._text(draw, (620, 22), "RATING 3.0", fonts["small_bold"], colors["muted"])
        rating = player.get("rating")
        self._text(draw, (618, 38), f"{rating:.2f}" if isinstance(rating, (int, float)) else "-", self._font(46, bold=True),
                   colors["gold"])
        self._single(draw, (620, 98), self._cs2_window(player), fonts["small"], colors["muted"], 166, 8)
        self._tracker_badge(draw, pick, colors, fonts)

        x0, y0, x1, y1 = LEFT
        draw.rectangle(LEFT, fill=colors["panel"], outline=colors["rule"], width=1)
        self._text(draw, (x0 + 12, y0 + 7), "角色能力", fonts["section"], colors["ink"])
        self._draw_radar(draw, (x0 + 100, y0 + 172), 70, player.get("roles") or {}, colors, fonts)
        self._draw_cs2_matches(image, draw, player, (x0 + 214, y0 + 8, x1 - 10, y1 - 8), colors, fonts)
        self._draw_cs2_board(image, draw, data.get("board") or [], RIGHT, player, data, colors, fonts)
        return image if tuple(dimensions) == (800, 480) else image.resize(tuple(dimensions), Image.Resampling.LANCZOS)

    @staticmethod
    def _cs2_window(player):
        maps = player.get("maps")
        return f"近 3 个月 · {maps} 张图" if maps else "暂无近期数据"

    def _draw_radar(self, draw, centre, radius, roles, colors, fonts):
        cx, cy = centre
        count = len(CS2_ROLES)
        points = lambda scale: [(cx + math.sin(2 * math.pi * i / count) * radius * scale,
                                 cy - math.cos(2 * math.pi * i / count) * radius * scale) for i in range(count)]
        shape = []
        if roles:
            values = [max(4, min(100, roles.get(name, 0))) / 100 for name in CS2_ROLES]
            shape = [(cx + (x - cx) * value, cy + (y - cy) * value) for (x, y), value in zip(points(1.0), values)]
            draw.polygon(shape, fill=self._blend(colors["gold"], colors["panel"], 0.28))
        # The grid goes over the fill so the rings stay readable inside the shape.
        for scale in (0.33, 0.66, 1.0):
            draw.polygon(points(scale), outline=colors["rule"])
        for x, y in points(1.0):
            draw.line((cx, cy, x, y), fill=colors["rule"], width=1)
        if shape:
            draw.line(shape + shape[:1], fill=colors["gold"], width=2)
            for x, y in shape:
                draw.ellipse((x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=colors["gold"])
        for (x, y), name in zip(points(1.24), CS2_ROLES):
            label = f"{CS2_ROLE_LABELS[name]} {roles.get(name, '-')}"
            width = self._text_width(draw, label, fonts["tiny"])
            self._text(draw, (x - width / 2, y - 6), label, fonts["tiny"],
                       colors["gold"] if roles.get(name, 0) >= 80 else colors["ink"])

    def _draw_cs2_matches(self, image, draw, player, box, colors, fonts):
        x0, y0, x1, y1 = box
        self._text(draw, (x0, y0), "赛程与战绩", fonts["small_bold"], colors["muted"])
        y = y0 + 20
        for match in (player.get("upcoming") or [])[:2]:
            when = datetime.fromtimestamp(match["unix_ms"] / 1000)
            self._text(draw, (x0, y), f"{when:%m/%d %H:%M}", fonts["tiny"], colors["cyan"])
            self._single(draw, (x0, y + 12), f"vs {match.get('opponent')}", fonts["small_bold"], colors["ink"], x1 - x0, 8)
            self._single(draw, (x0, y + 27), match.get("event"), fonts["tiny"], colors["muted"], x1 - x0, 7)
            y += 44
        for match in (player.get("recent") or [])[:4]:
            won = bool(match.get("won"))
            chip = (x0, y + 1, x0 + 34, y + 17)
            draw.rounded_rectangle(chip, radius=4, fill=colors["green"] if won else colors["red"])
            score = str(match.get("score") or "").replace(" ", "")
            self._text(draw, (chip[0] + (34 - self._text_width(draw, score, fonts["tiny"])) / 2, y + 3), score, fonts["tiny"],
                       colors["background"])
            self._single(draw, (x0 + 40, y + 1), f"vs {match.get('opponent')}", fonts["small"], colors["ink"], x1 - x0 - 40, 8)
            y += 24
            if y > y1 - 20:
                break
        if not player.get("upcoming") and not player.get("recent"):
            self._text(draw, (x0, y), "暂无比赛信息", fonts["small"], colors["muted"])
            y += 22
        if y1 - y >= 50:
            self._paste_asset_logo(image, "art-cs2-filler.png", (x0, y + 6, x1, y1))

    def _draw_cs2_board(self, image, draw, board, box, player, data, colors, fonts):
        rated = [row for row in board if isinstance(row.get("rating"), (int, float))]
        self._board_frame(draw, box, f"HLTV {data.get('top20_year')} 前 20",
                          f"近 3 个月 Rating · {len(rated)} 已更新", colors, fonts)
        for index, cell in self._board_cells(box, len(board)):
            row = board[index]
            if row["nick"] == player.get("nick"):
                self._highlight(draw, cell, colors)
            left, top, right, bottom = cell
            middle = (top + bottom) / 2
            self._text(draw, (left, middle - 7), f"{row['rank']:>2}", fonts["small_bold"], colors["muted"])
            self._single(draw, (left + 22, middle - 13), row["nick"], fonts["small_bold"], colors["ink"], 92, 8)
            self._single(draw, (left + 22, middle + 1), row.get("team") or "", fonts["tiny"], colors["muted"], 96, 7)
            rating = row.get("rating")
            text = f"{rating:.2f}" if isinstance(rating, (int, float)) else "-"
            self._text(draw, (right - self._text_width(draw, text, fonts["small_bold"]), middle - 7), text,
                       fonts["small_bold"], colors["gold"] if isinstance(rating, (int, float)) and rating >= 1.15 else colors["ink"])
