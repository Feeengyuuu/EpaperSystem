"""The Steam console layout, composed from live data and original Steam artwork.

All geometry is authored at the physical 800 x 480 display resolution.  Game
artwork and independent icons are resolved by AppID by the provider; this module
does not contain game-specific images, names, or network requests.
"""

import colorsys
import math

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from plugins.steam_profile_dashboard.avatar_frame import AVATAR_BOX, AVATAR_SIZE, FRAME_ORIGIN
from plugins.steam_profile_dashboard.rank_ladder import TIERS, rank_border, rank_for
from plugins.steam_profile_dashboard.sidebar_assets import STROKE_COUNT, name_stroke, sidebar_asset


SIZE = (800, 480)
CANVAS = (0, 0, 0)
NAVY = (6, 21, 34)
PANEL = (9, 28, 44)
HEADER = (19, 49, 70)
BORDER = (40, 91, 118)
WHITE = (242, 248, 253)
CYAN = (129, 213, 246)
MUTED = (138, 169, 191)
GREEN = (129, 237, 105)
# Steam's presence colours for an avatar's ring: online blue, away or busy amber (in a game: GREEN).
STEAM_ONLINE = (87, 203, 222)
AWAY = (234, 176, 72)
FRIEND_GAME_ICON = 14
# The online friends: those in a game first, each a wide row on the game's artwork (at most
# FRIEND_PLAYING_ROWS), then everyone else as small tiles, FRIEND_COLUMNS to a row, filling
# FRIEND_LIST; the last tile shows a miniature mosaic of the remaining friends. A tile's avatar is FRIEND_AVATAR,
# its name FRIEND_NAME_GAP under it; a row's avatar is FRIEND_ROW_AVATAR.
FRIEND_LIST = (600, 46, 784, 201)
FRIEND_AVATAR = 38
FRIEND_ROW_AVATAR = 32
FRIEND_NAME_GAP = 5
FRIEND_PLAYING_ROWS = 2
FRIEND_PLAYING_H = 38
FRIEND_COLUMNS = 3
FRIEND_TILE = (58, 55)
FRIEND_GAP = 3
# Black ink around the hero title keeps it legible over busy game artwork.
TITLE_OUTLINE = 2
RAIL_SIZE = (181, 480)
# The bordered plate behind the persona name and its STEAM PLAYER caption; it keeps the
# left margin and the space above the name clear for the avatar frame.
NAMEPLATE_BOX = (12, 175, 172, 228)
# The hand-painted swipe under the persona name, in the same clear area.
NAME_STROKE_BOX = (12, 175, 176, 211)
# The ranked level card: the tier's promotion border round the level, and its ladder.
RANK_BORDER_ORIGIN = (6, 234)
RANK_BORDER_SIZE = 98
RANK_LADDER_Y = 340
# The profile background behind the rail: blur radius, and dimming at its top and bottom.
BACKDROP_BLUR = 7
BACKDROP_DIM = (0.78, 0.9)
# A mini profile background is a small pattern made to be seen: lighter, so its motifs show.
MINI_BACKDROP_BLUR = 1.2
MINI_BACKDROP_DIM = (0.5, 0.74)
# The navy console; a game's own hue replaces it when its artwork has one (game_palette).
DEFAULT_PALETTE = {"canvas": CANVAS, "navy": NAVY, "panel": PANEL, "header": HEADER, "border": BORDER,
                   "accent": CYAN, "muted": MUTED, "glow": None}


def game_palette(art):
    """A dark console palette in the hue of a game's artwork, or None when the art has no clear colour.

    The art is reduced to eight swatches; swatches of nearby hue pool their weight (share of
    the picture x saturation x brightness), and the heaviest family gives the hue. The
    console keeps its dark values, so white text and the in-game green read as before.
    """
    if art is None:
        return None
    small = art.convert("RGB").resize((48, 27), Image.Resampling.BILINEAR)
    quantized = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    swatches = quantized.getpalette()
    tinted = []
    for count, index in quantized.getcolors() or []:
        red, green, blue = swatches[index * 3:index * 3 + 3]
        hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if value >= 0.15 and saturation >= 0.15:
            tinted.append((hue, saturation, count * saturation * (0.4 + value)))

    def near(a, b):
        return min(abs(a - b), 1 - abs(a - b)) <= 0.06

    family = max(([swatch for swatch in tinted if near(swatch[0], centre[0])] for centre in tinted),
                 key=lambda members: sum(weight for _hue, _sat, weight in members), default=[])
    total = sum(weight for _hue, _sat, weight in family)
    # A hue needs a real share of the picture, not a few highlight pixels.
    if total < 48 * 27 * 0.05:
        return None
    x = sum(weight * math.cos(2 * math.pi * hue) for hue, _sat, weight in family)
    y = sum(weight * math.sin(2 * math.pi * hue) for hue, _sat, weight in family)
    hue = (math.atan2(y, x) / (2 * math.pi)) % 1.0
    saturation = sum(sat * weight for _hue, sat, weight in family) / total
    saturation = max(0.45, min(0.8, saturation * 1.4))

    def tone(sat, value):
        return tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(hue, sat, value))

    return {"canvas": tone(saturation, 0.05), "navy": tone(saturation, 0.12), "panel": tone(saturation * 0.85, 0.16),
            "header": tone(saturation * 0.8, 0.27), "border": tone(saturation * 0.7, 0.47),
            "accent": tone(min(0.5, saturation * 0.65), 0.98), "muted": tone(0.16, 0.76),
            "glow": tone(saturation, 0.24)}


def _focus_appid(data, recent):
    """The game the page is about: the one being played, else the most recent (as the hero shows)."""
    profile = data.get("profile") or {}
    if profile.get("gameid") or profile.get("gameextrainfo"):
        return profile.get("gameid")
    game = next(iter(recent), None) or data.get("spotlight_game") or {}
    return game.get("appid")


def _appid(value):
    return str(value or "").strip()


def _number(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _value(value):
    return "—" if value is None else str(value)


def _hours(minutes):
    if minutes is None:
        return "—"
    # Match the provider's TOP 3 and existing dashboard rounding convention.
    return f"{int(round(_number(minutes) / 60))}h"


def _game_minutes(data, appid, field):
    """A current-game row must never inherit the whole account's playtime."""
    wanted = _appid(appid)
    if not wanted:
        return None
    # Recent records are authoritative for the two-week field.  Owned records
    # can supply the lifetime field when a live game is absent from the recent API.
    for collection in ("recent_games", "owned_games"):
        for game in data.get(collection, []) or []:
            if _appid(game.get("appid")) == wanted and field in game:
                return game.get(field)
    spotlight = data.get("spotlight_game") or {}
    if _appid(spotlight.get("appid")) == wanted:
        return spotlight.get(field)
    return None


def _recent_games(plugin, data):
    """Use the existing selection policy, without repeating the live game."""
    items, seen = [], set()
    for item in plugin._recent_items(data):
        appid = _appid(item.get("appid"))
        if not item.get("name") or (appid and appid in seen):
            continue
        if appid:
            seen.add(appid)
        items.append(item)
        if len(items) == 4:
            break
    # _recent_items may count a duplicate live game toward its four-row limit.
    # Fill only with other actual recent games, then the existing owned fallback.
    candidates = list(data.get("recent_games") or [])
    candidates += sorted(
        data.get("owned_games") or [],
        key=lambda game: _number(game.get("playtime_forever")),
        reverse=True,
    )
    for game in candidates:
        if len(items) == 4:
            break
        appid = _appid(game.get("appid"))
        if not appid or appid in seen:
            continue
        name = plugin._display_game_name(data, appid, game.get("name"))
        if name:
            items.append({"appid": appid, "name": name})
            seen.add(appid)
    return items


class _Console:
    def __init__(self, plugin, data, palette=None):
        self.plugin = plugin
        self.data = data
        self.c = palette or DEFAULT_PALETTE
        self.image = Image.new("RGB", SIZE, self.c["canvas"])
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}
        self.avatar_frame = None
        if self.c.get("glow"):
            self._wash()

    def _wash(self):
        """The game's hue glowing from the top right of the page and down the profile rail."""
        glow, canvas = self.c["glow"], self.c["canvas"]
        width, height = SIZE
        page = Image.new("RGB", (64, 40))
        page.putdata([
            tuple(round(c + (g - c) * max(0.0, 1 - ((63 - x) / 63 * 0.8 + y / 39 * 0.6))) for g, c in zip(glow, canvas))
            for y in range(40) for x in range(64)
        ])
        self.image.paste(page.resize((width, height), Image.Resampling.BILINEAR))
        rail = Image.new("RGB", (1, 48))
        rail.putdata([tuple(round(g + (c - g) * min(1.0, y / 30)) for g, c in zip(glow, canvas)) for y in range(48)])
        self.image.paste(rail.resize((181, height), Image.Resampling.BILINEAR), (0, 0))

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = self.plugin._font(size, bold=bold)
        return self.fonts[key]

    def text(self, box, value, size=14, color=WHITE, bold=False,
             lines=1, min_size=None, align="left", prepared=None):
        """Center visible glyphs in their box, including mixed Latin/CJK titles.

        Cropping the actual glyph alpha bounds removes font ascender/descent
        padding.  One-line text and two-line text therefore share the same visual
        center as their neighbouring icon, regardless of font metrics.
        """
        text = " ".join(str(value or "").split())
        x, y, right, bottom = (int(v) for v in box)
        width, height = right - x, bottom - y
        if not text or width <= 0 or height <= 0:
            return
        block = prepared if prepared is not None else self._text_bitmap(
            text, width, height, size, color, bold, lines, min_size, align)
        top = y + max(0, (height - block.height) // 2)
        self.image.paste(block, (x, top), block)

    def _text_bitmap(self, text, width, height, size=14, color=WHITE,
                     bold=False, lines=1, min_size=None, align="left", outline=0):
        """Prepare a bounded, tightly cropped text block for group alignment.

        ``outline`` strokes each glyph in the canvas black, that many pixels wide.
        """
        text = " ".join(str(text or "").split())
        min_size = size if min_size is None else min_size
        # The stroke widens every line, so wrap the glyphs inside it.
        text_width = width - 2 * outline

        def glyph(line, font):
            left, top, right, bottom = self.draw.textbbox((0, 0), line, font=font, anchor="lt",
                                                          stroke_width=outline)
            bitmap = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
            ImageDraw.Draw(bitmap).text((-left, -top), line, font=font, fill=color, anchor="lt",
                                        stroke_width=outline, stroke_fill=self.c["canvas"])
            bounds = bitmap.getbbox()
            return bitmap.crop(bounds) if bounds else bitmap

        for font_size in range(size, min_size - 1, -1):
            font = self.font(font_size, bold)
            wrapped = self._wrap(text, font, text_width)
            if len(wrapped) <= lines:
                glyphs = [glyph(line, font) for line in wrapped]
                if sum(part.height for part in glyphs) + len(glyphs) - 1 <= height:
                    break
        if len(wrapped) > lines:
            wrapped = wrapped[:lines]
            last = wrapped[-1]
            while last and self.draw.textlength(last + "…", font=font) > text_width:
                last = last[:-1]
            wrapped[-1] = last.rstrip() + "…"
        glyphs = [glyph(line, font) for line in wrapped]
        block_height = min(height, sum(part.height for part in glyphs) + len(glyphs) - 1)
        tile = Image.new("RGBA", (width, max(1, block_height)))
        top = 0
        for part in glyphs:
            offset = max(0, (width - part.width) // 2) if align == "center" else 0
            if align == "right":
                offset = max(0, width - part.width)
            tile.alpha_composite(part, (offset, top))
            top += part.height + 1
        return tile

    def _wrap(self, text, font, width):
        result, line = [], ""
        # Prefer whitespace boundaries in Latin names, retain natural character
        # wrapping for Chinese and for a single very long account name.
        for character in text:
            candidate = line + character
            if line and self.draw.textlength(candidate, font=font) > width:
                split = line.rfind(" ")
                if split > len(line) // 2:
                    result.append(line[:split].rstrip())
                    line = line[split + 1:] + character
                else:
                    result.append(line.rstrip())
                    line = character.lstrip()
            else:
                line = candidate
        if line:
            result.append(line.rstrip())
        return result or [""]

    def rank_card(self, data):
        """The level as a ranked tier: its promotion border round the number, then the ladder."""
        rank = rank_for(data.get("level"), data.get("badges"))
        x0, y0 = RANK_BORDER_ORIGIN
        border, hole = rank_border(rank.key, RANK_BORDER_SIZE)
        if border is None:
            hole = (RANK_BORDER_SIZE / 2, RANK_BORDER_SIZE / 2, 24)
        cx, cy, radius = x0 + hole[0], y0 + hole[1], hole[2]
        # A dark disc fills the empty centre so the number reads over any backdrop.
        self.draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=self.c["navy"])
        if border is not None:
            self.image.paste(border, (x0, y0), border)
        else:
            self.draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                              outline=rank.color, width=3)
        self.text((cx - radius, cy - 14, cx + radius, cy + 14), _value(data.get("level")),
                  24, bold=True, min_size=14, align="center")
        x = x0 + RANK_BORDER_SIZE + 4
        self.text((x, 238, 178, 249), rank.english, 9, rank.color, bold=True, min_size=7)
        self.text((x, 250, 178, 276), rank.name, 22, rank.color, bold=True)
        if rank.division:
            self.text((x, 277, 178, 296), rank.division, 18, bold=True)
        if rank.lp is not None:
            self.draw.rounded_rectangle((x, 301, 176, 306), radius=2, fill=self.c["border"])
            if rank.lp:
                self.draw.rounded_rectangle((x, 301, x + round((176 - x) * rank.lp / 100), 306),
                                            radius=2, fill=rank.color)
            self.text((x, 309, 178, 320), f"{rank.lp} LP", 10, self.c["muted"])
        if rank.next_name:
            self.text((x, 321, 178, 332), f"距{rank.next_name} 差{rank.levels_to_next}级", 10,
                      self.c["muted"], min_size=7)
        self.rank_ladder(rank)

    def rank_ladder(self, rank):
        """Ten tier pips: climbed tiers filled in their colours, the current one large."""
        left, right, y = 18, 164, RANK_LADDER_Y
        step = (right - left) / (len(TIERS) - 1)
        current_x = left + rank.index * step
        self.draw.line((left, y, right, y), fill=self.c["border"], width=1)
        self.draw.line((left, y, current_x, y), fill=rank.color, width=2)
        for index, tier in enumerate(TIERS):
            px = left + index * step
            size = 6 if index == rank.index else 3
            diamond = ((px, y - size), (px + size, y), (px, y + size), (px - size, y))
            if index < rank.index:
                self.draw.polygon(diamond, fill=tier[4])
            elif index == rank.index:
                self.draw.polygon(diamond, fill=tier[4], outline=WHITE)
            else:
                self.draw.polygon(diamond, fill=self.c["navy"], outline=self.c["border"])

    def name_stroke(self, data):
        """Paint a brush swipe under the name in the game's hue; the paint colour, or None.

        The game being played (else the latest one) picks which of the strokes is used, so the
        swipe changes with the game as its colour does.
        """
        profile = data.get("profile") or {}
        recent = data.get("recent_games") or []
        appid = _appid(profile.get("gameid")) or (_appid(recent[0].get("appid")) if recent else 0)
        stroke = name_stroke(_number(appid) % STROKE_COUNT)
        if stroke is None:
            return None
        x, y, right, bottom = NAME_STROKE_BOX
        hue, saturation, _value = colorsys.rgb_to_hsv(*(channel / 255 for channel in self.c["accent"]))
        paint = tuple(round(channel * 255) for channel in
                      colorsys.hsv_to_rgb(hue, max(0.6, min(0.85, saturation * 2.2)), 0.9))
        swipe = Image.new("RGBA", (right - x, bottom - y), (*paint, 255))
        swipe.putalpha(stroke.resize(swipe.size, Image.Resampling.LANCZOS).getchannel("A"))
        self.image.paste(swipe, (x, y), swipe)
        return paint

    def nameplate(self, box):
        """A bordered plate under the persona name, so it stands out over any backdrop."""
        x, y, right, bottom = box
        plate = Image.new("RGBA", (right - x + 1, bottom - y + 1))
        ImageDraw.Draw(plate).rounded_rectangle((0, 0, right - x, bottom - y), radius=8,
                                                fill=(*self.c["navy"], 200),
                                                outline=self.c["accent"], width=2)
        self.image.paste(plate, (x, y), plate)

    def panel(self, box, header=0):
        self.draw.rounded_rectangle(box, radius=4, fill=self.c["panel"],
                                    outline=self.c["border"], width=1)
        if header:
            x, y, right, _ = box
            self.draw.rounded_rectangle((x, y, right, y + header), radius=4,
                                        fill=self.c["header"])
            self.draw.rectangle((x, y + header - 4, right, y + header), fill=self.c["header"])

    def art(self, box, appid, strength=145):
        """Keep original game artwork separate from the flat page canvas."""
        x, y, right, bottom = box
        size = (right - x, bottom - y)
        source = self.plugin._game_background(self.data, appid, size) if appid else None
        if source is None:
            self.draw.rectangle((x, y, right - 1, bottom - 1), fill=self.c["panel"])
            return
        fitted = ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)
        veil = Image.new("RGBA", size)
        painter = ImageDraw.Draw(veil)
        # Preserve the approved art shading regardless of the page canvas colour.
        for column in range(size[0]):
            alpha = int(strength + 45 * (1 - column / max(1, size[0] - 1)))
            painter.line((column, 0, column, size[1]), fill=(*self.c["navy"], min(225, alpha)))
        fitted.paste(veil, (0, 0), veil)
        self.image.paste(fitted, (x, y))

    def icon(self, appid, x, y, size):
        icon = self.plugin._game_square_icon(self.data, appid, size) if appid else None
        if icon is not None:
            # Game logos sit straight on their artwork, without a frame.
            self.image.paste(icon, (x, y), icon if icon.mode == "RGBA" else None)
            return
        # An explicit neutral placeholder never borrows a different game's icon.
        self.draw.rounded_rectangle((x - 1, y - 1, x + size, y + size), radius=3, fill=self.c["panel"])
        self.draw.rectangle((x + size // 4, y + size // 3,
                             x + size * 3 // 4, y + size * 2 // 3), outline=self.c["muted"])
        self.draw.line((x + size // 3, y + size // 2,
                        x + size * 2 // 3, y + size // 2), fill=self.c["muted"])

    def small_symbol(self, kind, x, y, color=None):
        draw = self.draw
        color = color or self.c["accent"]
        if kind == "clock":
            draw.ellipse((x, y, x + 15, y + 15), outline=color, width=2)
            draw.line((x + 7, y + 3, x + 7, y + 8, x + 11, y + 10), fill=color, width=2)
        elif kind == "people":
            draw.ellipse((x + 2, y, x + 7, y + 5), fill=color)
            draw.ellipse((x + 10, y + 1, x + 14, y + 5), fill=color)
            draw.rounded_rectangle((x, y + 8, x + 9, y + 14), radius=2, fill=color)
            draw.rounded_rectangle((x + 10, y + 8, x + 16, y + 14), radius=2, fill=color)
        elif kind == "chart":
            for offset, height in ((0, 7), (6, 12), (12, 17)):
                draw.rectangle((x + offset, y + 17 - height, x + offset + 3, y + 17), fill=color)
        else:
            draw.rounded_rectangle((x, y + 3, x + 18, y + 15), radius=4, fill=color)
            draw.line((x + 3, y + 9, x + 9, y + 9), fill=self.c["navy"], width=2)
            draw.line((x + 6, y + 6, x + 6, y + 12), fill=self.c["navy"], width=2)
            draw.ellipse((x + 13, y + 7, x + 15, y + 9), fill=self.c["navy"])

    def framed_avatar(self, url):
        avatar_method = getattr(self.plugin, "_profile_avatar_image", self.plugin._avatar_image)
        next_frame = getattr(self.plugin, "_next_avatar_frame", None)
        frame = next_frame() if next_frame is not None else None
        avatar = avatar_method(url, AVATAR_SIZE)
        if frame is None:
            if avatar is not None:
                self.image.paste(avatar, AVATAR_BOX[:2], avatar if avatar.mode == "RGBA" else None)
            return
        # The photo keeps its size; the frame's opening decides what shows of it.
        if avatar is not None:
            self.image.paste(avatar.convert("RGB"), AVATAR_BOX[:2], frame.mask)
        self.avatar_frame = frame

    def frame_overlay(self):
        """The avatar frame goes on last, whole, over the persona name and any panel edge it reaches."""
        if self.avatar_frame is not None:
            self.image.paste(self.avatar_frame.image, FRAME_ORIGIN, self.avatar_frame.image)

    def profile_backdrop(self):
        """The owner's equipped mini profile (else profile) background, blurred and dimmed behind the rail."""
        record = self.data.get("profile_background") or {}
        loader = getattr(self.plugin, "_profile_background_image", None)
        source = loader(record.get("url")) if loader is not None and record.get("url") else None
        if source is None:
            return False
        mini = record.get("slot") == "mini_profile_background"
        blur, (dim_top, dim_bottom) = (MINI_BACKDROP_BLUR, MINI_BACKDROP_DIM) if mini else (BACKDROP_BLUR, BACKDROP_DIM)
        backdrop = ImageOps.fit(source.convert("RGB"), RAIL_SIZE, method=Image.Resampling.LANCZOS)
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(blur))
        # Dimmed toward the page canvas, deeper down the stats, so white type reads on any picture.
        shade = Image.linear_gradient("L").resize(RAIL_SIZE).point(
            lambda value: round(255 * (dim_top + (dim_bottom - dim_top) * value / 255)))
        backdrop.paste(Image.new("RGB", RAIL_SIZE, self.c["canvas"]), (0, 0), shade)
        self.image.paste(backdrop, (0, 0))
        return True

    def rail(self):
        data, profile = self.data, self.data.get("profile") or {}
        if not self.profile_backdrop() and not self.c.get("glow"):
            self.draw.rectangle((0, 0, 181, 479), fill=self.c["canvas"])
        self.draw.line((181, 0, 181, 480), fill=self.c["border"], width=2)
        url = profile.get("avatarfull") or profile.get("avatarmedium") or profile.get("avatar")
        self.framed_avatar(url)
        if self.name_stroke(data) is None:
            self.nameplate(NAMEPLATE_BOX)
        # White with a dark outline, like the game title, so it reads on the paint.
        persona = profile.get("personaname") or "Steam User"
        name = self._text_bitmap(persona, 147, 29, 29, WHITE, True, 1, 18, outline=TITLE_OUTLINE)
        self.text((19, 178, 166, 207), persona, prepared=name)
        self.text((20, 212, 164, 225), "STEAM PLAYER", 10, self.c["muted"])
        self.draw.line((14, 230, 167, 230), fill=self.c["border"])
        self.rank_card(data)
        owned = data.get("owned_games") or []
        recent = data.get("recent_games") or []
        game_count = data.get("game_count")
        if game_count is None:
            game_count = len(owned)
        metrics = (
            ("games", "game", "游戏", _value(game_count), 20),
            ("friends", "people", "好友", f"{_value(data.get('online_friend_count'))}/{_value(data.get('friend_count'))}", 20),
            ("recent", "clock", "近2周", _hours(sum(_number(g.get("playtime_2weeks")) for g in recent)), 18),
            ("total", "clock", "总计", _hours(sum(_number(g.get("playtime_forever")) for g in owned)), 18),
        )
        # Two columns of two under the ladder: a small label over each number.
        for index, (asset, symbol, label, number, _size) in enumerate(metrics):
            x, y = 14 + (index % 2) * 80, 352 + (index // 2) * 26
            icon = sidebar_asset(asset, 16)
            if icon is not None:
                self.image.paste(icon, (x, y + 5), icon)
            else:
                self.small_symbol(symbol, x, y + 4)
            self.text((x + 20, y, x + 78, y + 10), label, 9, self.c["muted"])
            self.text((x + 20, y + 10, x + 78, y + 25), number, 15, bold=True, min_size=11)
        self.draw.line((14, 405, 167, 405), fill=self.c["border"])
        badges = data.get("badges") or {}
        badge_count = len(badges.get("badges", [])) if badges else None
        for y, asset, label, number in ((416, "badges", "徽章", badge_count), (444, "xp", "XP", badges.get("player_xp"))):
            icon = sidebar_asset(asset, 20)
            if icon is not None:
                self.image.paste(icon, (16, y + 1), icon)
            else:
                self.draw.ellipse((17, y, 34, y + 17), outline=self.c["accent"], width=2)
            self.text((43, y + 1, 86, y + 20), label, 14, self.c["muted"])
            self.text((87, y, 168, y + 23), _value(number), 20, bold=True, min_size=14, align="right")

    def hero(self, recent):
        profile = self.data.get("profile") or {}
        playing = bool(profile.get("gameid") or profile.get("gameextrainfo"))
        if playing:
            appid = profile.get("gameid")
            name = self.plugin._display_game_name(self.data, appid, profile.get("gameextrainfo"))
        else:
            game = next(iter(recent), None) or self.data.get("spotlight_game") or {}
            appid = game.get("appid")
            name = game.get("name") or "暂无公开游戏"
        recent_ids = {_appid(game.get("appid")) for game in self.data.get("recent_games") or []}
        favorite = bool(not playing and appid and _appid(appid) not in recent_ids)
        heading = "正在玩" if playing else ("游戏精选" if favorite else "最近游玩")
        english = "NOW PLAYING" if playing else ("LIBRARY FAVORITES" if favorite else "RECENT ACTIVITY")
        self.panel((194, 10, 583, 205), header=32)
        self.small_symbol("game", 204, 17, GREEN if playing else self.c["accent"])
        self.text((233, 10, 359, 42), heading,
                  22, GREEN if playing else self.c["accent"], bold=True)
        self.text((369, 10, 574, 42), english, 9, self.c["muted"])
        self.art((195, 43, 583, 205), appid, strength=80)
        if playing:
            status = "游戏中"
        else:
            status, _ = self.plugin._persona_text(profile)
        detail = f"{status} · AppID {appid}" if appid else status
        title = self._text_bitmap(name, 290, 72, 25, WHITE, True, 2, 19, outline=TITLE_OUTLINE)
        status_line = self._text_bitmap(detail, 290, 24, 15, self.c["accent"], min_size=12)
        group_height = title.height + 13 + status_line.height
        group_y = 43 + (162 - group_height) // 2
        self.icon(appid, 209, 96, 56)
        self.text((281, group_y, 571, group_y + title.height), name, prepared=title)
        status_y = group_y + title.height + 13
        self.text((281, status_y, 571, status_y + status_line.height), detail,
                  prepared=status_line)

    def friends(self):
        data = self.data
        self.panel((594, 10, 790, 205), header=32)
        self.small_symbol("people", 605, 19)
        self.text((630, 10, 710, 42), "在线好友", 18, self.c["accent"], bold=True)
        counter = f"{_value(data.get('online_friend_count'))}/{_value(data.get('friend_count'))}"
        self.text((711, 10, 783, 42), counter, 15, self.c["accent"], min_size=11, align="right")
        # Order at display time so saved data and remarks follow the presence rule.
        friends = sorted((friend for friend in data.get("friends", []) or []
                          if _number(friend.get("personastate")) > 0),
                         key=lambda friend: (self.plugin._friend_presence_rank(friend),
                                             self.plugin._friend_display_id(friend).casefold()))
        if not friends:
            message = "好友列表暂不可用" if data.get("friend_count") is None else "目前没有在线好友"
            self.text((607, 110, 780, 148), message, 15, self.c["muted"], lines=2)
            return
        playing = [friend for friend in friends if friend.get("gameid") or friend.get("gameextrainfo")]
        rows = playing[:FRIEND_PLAYING_ROWS]
        top = FRIEND_LIST[1] + len(rows) * (FRIEND_PLAYING_H + FRIEND_GAP)
        grid_rows = max(0, (FRIEND_LIST[3] - top + FRIEND_GAP) // (FRIEND_TILE[1] + FRIEND_GAP))
        rest = [friend for friend in friends if friend not in rows]
        slots = grid_rows * FRIEND_COLUMNS
        tiles = rest[:slots] if len(rest) <= slots else rest[:max(0, slots - 1)]
        hidden = rest[len(tiles):]
        # Friend avatars can be slow; resolve every shown friend's game icon
        # first so they cannot spend the shared game-media network budget.
        game_icons = {}
        for friend in rows + tiles:
            appid = _appid(friend.get("gameid"))
            if appid and appid not in game_icons:
                game_icons[appid] = self.plugin._game_square_icon(data, appid, FRIEND_GAME_ICON)
        for index, friend in enumerate(rows):
            self._playing_friend(friend, FRIEND_LIST[1] + index * (FRIEND_PLAYING_H + FRIEND_GAP), game_icons)
        shown = tiles + ([None] if hidden else [])
        lines = [shown[start:start + FRIEND_COLUMNS] for start in range(0, len(shown), FRIEND_COLUMNS)]
        # The tiles share out the room left: their rows evenly down it, each row centred across.
        width = FRIEND_LIST[2] - FRIEND_LIST[0]
        step_x = (width - FRIEND_TILE[0]) / max(1, FRIEND_COLUMNS - 1)
        room = FRIEND_LIST[3] - top
        gap_y = max(FRIEND_GAP, (room - len(lines) * FRIEND_TILE[1]) / (len(lines) + 1)) if lines else 0
        for row, line in enumerate(lines):
            y = round(top + gap_y + row * (FRIEND_TILE[1] + gap_y)) if gap_y > FRIEND_GAP \
                else top + row * (FRIEND_TILE[1] + FRIEND_GAP)
            first = FRIEND_LIST[0] + (width - FRIEND_TILE[0] - step_x * (len(line) - 1)) / 2
            for column, friend in enumerate(line):
                self._friend_tile(friend, round(first + column * step_x), y, game_icons, hidden)

    @staticmethod
    def _friend_ring_colour(friend):
        playing = bool(friend.get("gameid") or friend.get("gameextrainfo"))
        state = _number(friend.get("personastate"))
        return GREEN if playing else (STEAM_ONLINE if state in (1, 5, 6) else AWAY)

    def _friend_ring(self, friend, x, y, size=FRIEND_AVATAR):
        """The friend's avatar ``size`` across with a ring in their presence colour (Steam's:
        green in a game, blue online, amber away or busy) round it."""
        colour = self._friend_ring_colour(friend)
        self.draw.ellipse((x - 2, y - 2, x + size + 1, y + size + 1), outline=colour, width=2)
        url = friend.get("avatarfull") or friend.get("avatarmedium") or friend.get("avatar")
        avatar = self.plugin._avatar_image(url, size, decorative_outline=False)
        if avatar is not None:
            self.image.paste(avatar, (x, y), avatar if avatar.mode == "RGBA" else None)

    def _playing_friend(self, friend, y, game_icons):
        """A friend in a game as a wide row on that game's own artwork: the avatar, the name and
        the game with its icon."""
        x, right = FRIEND_LIST[0], FRIEND_LIST[2]
        appid = friend.get("gameid")
        self.art((x, y, right, y + FRIEND_PLAYING_H), appid, strength=120)
        self._friend_ring(friend, x + 4, y + 3, FRIEND_ROW_AVATAR)
        self.text((x + 43, y + 2, right - 4, y + 19), self.plugin._friend_display_id(friend), 14,
                  bold=True, min_size=11)
        name = self.plugin._display_game_name(self.data, appid, friend.get("gameextrainfo"))
        icon = game_icons.get(_appid(appid))
        text_x = x + 43
        if icon is not None:
            self.image.paste(icon, (text_x, y + 21), icon if icon.mode == "RGBA" else None)
            text_x += FRIEND_GAME_ICON + 4
        self.text((text_x, y + 20, right - 4, y + 36), name or "游戏中", 11, GREEN, bold=True)

    def _friend_tile(self, friend, x, y, game_icons, hidden):
        """A friend as a small tile on the panel itself: the ringed avatar over the name (a game's
        icon pinned to the avatar while playing); ``None`` is a nameless mosaic of those left out."""
        width, height = FRIEND_TILE
        avatar_x, avatar_y = x + (width - FRIEND_AVATAR) // 2, y + 1
        name_box = (x + 2, avatar_y + FRIEND_AVATAR + FRIEND_NAME_GAP, x + width - 2, y + height)
        if friend is None:
            # Without a name beneath it, lower the group slightly within the same tile.
            shown = self._friend_mosaic(hidden, avatar_x, avatar_y + 4)
            if not shown:
                self.draw.ellipse((avatar_x, avatar_y, avatar_x + FRIEND_AVATAR, avatar_y + FRIEND_AVATAR),
                                  outline=self.c["accent"], width=2)
                self.text((avatar_x, avatar_y, avatar_x + FRIEND_AVATAR, avatar_y + FRIEND_AVATAR), f"+{len(hidden)}", 15,
                          self.c["accent"], bold=True, align="center")
            if shown and shown < len(hidden):
                self.text(name_box, f"+{len(hidden) - shown}", 10, self.c["muted"], align="center")
            return
        self._friend_ring(friend, avatar_x, avatar_y)
        icon = game_icons.get(_appid(friend.get("gameid")))
        if icon is not None:
            badge = (avatar_x + FRIEND_AVATAR - FRIEND_GAME_ICON + 4, avatar_y + FRIEND_AVATAR - FRIEND_GAME_ICON + 2)
            self.draw.rounded_rectangle((badge[0] - 1, badge[1] - 1, badge[0] + FRIEND_GAME_ICON,
                                         badge[1] + FRIEND_GAME_ICON), radius=2, fill=self.c["navy"], outline=GREEN)
            self.image.paste(icon, badge, icon if icon.mode == "RGBA" else None)
        self.text(name_box, self.plugin._friend_display_id(friend), 11,
                  bold=True, min_size=9, align="center")

    def _friend_mosaic(self, friends, x, y):
        """Fit up to nine actual remaining avatars inside the former 38px count circle."""
        records = [(friend, friend.get("avatarfull") or friend.get("avatarmedium") or friend.get("avatar"))
                   for friend in friends]
        records = [(friend, url) for friend, url in records if url][:9]
        if not records:
            return 0
        columns = 1 if len(records) == 1 else 2 if len(records) <= 4 else 3
        gap = 2 if columns == 2 else 1
        size = (FRIEND_AVATAR - gap * (columns - 1)) // columns
        avatars = [(friend, self.plugin._avatar_image(url, size, decorative_outline=False)) for friend, url in records]
        avatars = [(friend, avatar) for friend, avatar in avatars if avatar is not None]
        rows = math.ceil(len(avatars) / columns)
        top = y + (FRIEND_AVATAR - rows * size - max(0, rows - 1) * gap) // 2
        for start in range(0, len(avatars), columns):
            line = avatars[start:start + columns]
            left = x + (FRIEND_AVATAR - len(line) * size - (len(line) - 1) * gap) // 2
            for index, (friend, avatar) in enumerate(line):
                avatar_x = left + index * (size + gap)
                self.image.paste(avatar, (avatar_x, top), avatar if avatar.mode == "RGBA" else None)
                # Draw inside the portrait's bounds so even 12px neighbours retain a gap.
                self.draw.ellipse((avatar_x, top, avatar_x + size - 1, top + size - 1),
                                  outline=self._friend_ring_colour(friend), width=1)
            top += size + gap
        return len(avatars)

    def recent(self, items):
        recent_ids = {_appid(game.get("appid")) for game in self.data.get("recent_games") or []}
        current_appid = _appid((self.data.get("profile") or {}).get("gameid"))
        has_favorites = any(_appid(item.get("appid")) not in recent_ids | {current_appid}
                            for item in items)
        self.panel((194, 215, 790, 361), header=28)
        self.small_symbol("clock", 205, 221)
        self.text((231, 215, 359, 243), "最近 / 常玩" if has_favorites else "最近游玩",
                  20, self.c["accent"], bold=True, min_size=18)
        self.text((369, 215, 568, 243), "RECENT / FAVORITES" if has_favorites else "RECENTLY PLAYED", 9, self.c["muted"])
        self.text((599, 215, 678, 243), "近2周", 14, self.c["accent"], align="center")
        self.text((701, 215, 780, 243), "总计", 14, self.c["accent"], align="center")
        if not items:
            self.text((211, 285, 773, 314), "没有公开的近期游戏数据", 17, self.c["muted"])
            return
        for index, item in enumerate(items[:4]):
            y = 244 + index * 29
            appid = item.get("appid")
            self.art((195, y, 790, y + 28), appid, strength=175)
            self.icon(appid, 206, y + 2, 24)
            playing = bool(current_appid and _appid(appid) == current_appid)
            right = 509 if playing else 588
            self.text((243, y, right, y + 28), item.get("name"), 14,
                      bold=True, lines=2, min_size=11)
            if playing:
                self.draw.rounded_rectangle((513, y + 4, 592, y + 24), radius=3,
                                            fill=self.c["navy"], outline=GREEN)
                self.text((516, y + 4, 590, y + 24), "正在游玩...", 11, GREEN,
                          bold=True, align="center")
            for field, box in (("playtime_2weeks", (599, y, 678, y + 28)),
                               ("playtime_forever", (701, y, 780, y + 28))):
                self.text(box, _hours(_game_minutes(self.data, appid, field)),
                          15, bold=True, min_size=12, align="center")
            if index < 3:
                self.draw.line((201, y + 28, 783, y + 28), fill=self.c["border"])

    def top(self):
        self.panel((194, 370, 790, 463), header=27)
        self.small_symbol("chart", 205, 375)
        self.text((231, 370, 388, 397), "常玩 TOP 3", 20, self.c["accent"], bold=True)
        self.text((399, 370, 565, 397), "TOP GAMES", 9, self.c["muted"])
        items = self.plugin._top_game_items(self.data)[:3]
        if not items:
            self.text((212, 420, 777, 444), "暂无公开的累计游玩记录", 16, self.c["muted"])
            return
        for index, item in enumerate(items):
            x = 199 + index * 197
            appid = item.get("appid")
            self.art((x, 399, x + 192, 459), appid, strength=145)
            rank_color = ((249, 202, 77), (181, 205, 223), (210, 153, 88))[index]
            self.draw.polygon(((x, 399), (x + 19, 399), (x + 12, 459), (x, 459)), fill=rank_color)
            self.text((x + 2, 400, x + 15, 459), item.get("rank", index + 1),
                      17, self.c["navy"], bold=True, align="center")
            self.icon(appid, x + 23, 412, 33)
            title = self._text_bitmap(item.get("name"), 124, 29, 13, WHITE, True, 2, 11)
            hours = item.get("suffix") or _hours(_game_minutes(self.data, appid, "playtime_forever"))
            hours_line = self._text_bitmap(hours, 124, 23, 20, WHITE, True, min_size=15)
            group_y = 399 + (60 - title.height - hours_line.height - 5) // 2
            self.text((x + 63, group_y, x + 187, group_y + title.height),
                      item.get("name"), prepared=title)
            hours_y = group_y + title.height + 5
            self.text((x + 63, hours_y, x + 187, hours_y + hours_line.height),
                      hours, prepared=hours_line)
            self.draw.rounded_rectangle((x, 399, x + 192, 459), radius=3, outline=self.c["border"])


def render_console(plugin, data, dimensions, theme_context=None):
    """Return the selected navy console at the device's requested resolution."""
    # This visual design is intentionally consistent across day/night modes; its colours
    # follow the game being played (else the most recent one) when that art has a clear hue.
    data = data or {}
    recent = _recent_games(plugin, data)
    appid = _focus_appid(data, recent)
    palette = game_palette(plugin._game_background(data, appid, (96, 54))) if appid else None
    console = _Console(plugin, data, palette)
    console.rail()
    console.hero(recent)
    console.recent(recent)
    console.top()
    # Finish the bounded game-art acquisition before optional friend avatars can
    # spend time on the network.  These panels do not overlap visually.
    console.friends()
    console.frame_overlay()
    if tuple(dimensions) != SIZE:
        return console.image.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return console.image
