"""The Steam console layout, composed from live data and original Steam artwork.

All geometry is authored at the physical 800 x 480 display resolution.  Game
artwork and independent icons are resolved by AppID by the provider; this module
does not contain game-specific images, names, or network requests.
"""

from PIL import Image, ImageDraw, ImageOps

from plugins.steam_profile_dashboard.sidebar_assets import sidebar_asset


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
FRIEND_GAME_ICON = 14


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
    def __init__(self, plugin, data):
        self.plugin = plugin
        self.data = data
        self.image = Image.new("RGB", SIZE, CANVAS)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}

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
                     bold=False, lines=1, min_size=None, align="left"):
        """Prepare a bounded, tightly cropped text block for group alignment."""
        text = " ".join(str(text or "").split())
        min_size = size if min_size is None else min_size

        def glyph(line, font):
            left, top, right, bottom = self.draw.textbbox((0, 0), line, font=font, anchor="lt")
            bitmap = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
            ImageDraw.Draw(bitmap).text((-left, -top), line, font=font,
                                        fill=color, anchor="lt")
            bounds = bitmap.getbbox()
            return bitmap.crop(bounds) if bounds else bitmap

        for font_size in range(size, min_size - 1, -1):
            font = self.font(font_size, bold)
            wrapped = self._wrap(text, font, width)
            if len(wrapped) <= lines:
                glyphs = [glyph(line, font) for line in wrapped]
                if sum(part.height for part in glyphs) + len(glyphs) - 1 <= height:
                    break
        if len(wrapped) > lines:
            wrapped = wrapped[:lines]
            last = wrapped[-1]
            while last and self.draw.textlength(last + "…", font=font) > width:
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

    def panel(self, box, header=0):
        self.draw.rounded_rectangle(box, radius=4, fill=PANEL,
                                    outline=BORDER, width=1)
        if header:
            x, y, right, _ = box
            self.draw.rounded_rectangle((x, y, right, y + header), radius=4,
                                        fill=HEADER)
            self.draw.rectangle((x, y + header - 4, right, y + header), fill=HEADER)

    def art(self, box, appid, strength=145):
        """Keep original game artwork separate from the flat page canvas."""
        x, y, right, bottom = box
        size = (right - x, bottom - y)
        source = self.plugin._game_background(self.data, appid, size) if appid else None
        if source is None:
            self.draw.rectangle((x, y, right - 1, bottom - 1), fill=PANEL)
            return
        fitted = ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)
        veil = Image.new("RGBA", size)
        painter = ImageDraw.Draw(veil)
        # Preserve the approved art shading regardless of the page canvas colour.
        for column in range(size[0]):
            alpha = int(strength + 45 * (1 - column / max(1, size[0] - 1)))
            painter.line((column, 0, column, size[1]), fill=(*NAVY, min(225, alpha)))
        fitted.paste(veil, (0, 0), veil)
        self.image.paste(fitted, (x, y))

    def icon(self, appid, x, y, size):
        icon = self.plugin._game_square_icon(self.data, appid, size) if appid else None
        self.draw.rounded_rectangle((x - 1, y - 1, x + size, y + size), radius=3,
                                    fill=PANEL, outline=CYAN)
        if icon is not None:
            self.image.paste(icon, (x, y), icon if icon.mode == "RGBA" else None)
        else:
            # An explicit neutral placeholder never borrows a different game's icon.
            self.draw.rectangle((x + size // 4, y + size // 3,
                                 x + size * 3 // 4, y + size * 2 // 3), outline=MUTED)
            self.draw.line((x + size // 3, y + size // 2,
                            x + size * 2 // 3, y + size // 2), fill=MUTED)

    def small_symbol(self, kind, x, y, color=CYAN):
        draw = self.draw
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
            draw.line((x + 3, y + 9, x + 9, y + 9), fill=NAVY, width=2)
            draw.line((x + 6, y + 6, x + 6, y + 12), fill=NAVY, width=2)
            draw.ellipse((x + 13, y + 7, x + 15, y + 9), fill=NAVY)

    def rail(self):
        data, profile = self.data, self.data.get("profile") or {}
        self.draw.rectangle((0, 0, 181, 479), fill=CANVAS)
        self.draw.line((181, 0, 181, 480), fill=BORDER, width=2)
        url = profile.get("avatarfull") or profile.get("avatarmedium") or profile.get("avatar")
        avatar_method = getattr(self.plugin, "_profile_avatar_image", self.plugin._avatar_image)
        avatar = avatar_method(url, 146)
        if avatar is not None:
            self.image.paste(avatar, (16, 16), avatar if avatar.mode == "RGBA" else None)
        self.draw.rounded_rectangle((13, 13, 164, 164), radius=4, outline=BORDER, width=2)
        self.text((14, 175, 169, 207), profile.get("personaname") or "Steam User",
                  29, bold=True, min_size=18)
        self.text((15, 209, 166, 222), "STEAM PLAYER", 10, MUTED)
        self.draw.line((14, 230, 167, 230), fill=BORDER)
        level_frame = sidebar_asset("level_frame", 54)
        if level_frame is not None:
            self.image.paste(level_frame, (9, 237), level_frame)
        else:
            self.draw.polygon(((36, 240), (57, 252), (57, 276), (36, 288), (15, 276), (15, 252)),
                              outline=CYAN, width=2)
        self.text((17, 254, 56, 277), _value(data.get("level")), 23,
                  bold=True, min_size=17, align="center")
        self.text((67, 248, 167, 265), "等级", 14, MUTED)
        self.text((67, 268, 167, 289), _value(data.get("level")), 21, bold=True)
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
        for index, (asset, symbol, label, number, size) in enumerate(metrics):
            y = 299 + index * 25
            icon = sidebar_asset(asset, size)
            if icon is not None:
                self.image.paste(icon, (16 + (20 - size) // 2, y + 1 + (20 - size) // 2), icon)
            else:
                self.small_symbol(symbol, 17, y)
            self.text((43, y + 1, 90, y + 19), label, 13, MUTED)
            self.text((91, y, 168, y + 22), number, 18, bold=True, min_size=13, align="right")
        self.draw.line((14, 405, 167, 405), fill=BORDER)
        badges = data.get("badges") or {}
        badge_count = len(badges.get("badges", [])) if badges else None
        for y, asset, label, number in ((416, "badges", "徽章", badge_count), (444, "xp", "XP", badges.get("player_xp"))):
            icon = sidebar_asset(asset, 20)
            if icon is not None:
                self.image.paste(icon, (16, y + 1), icon)
            else:
                self.draw.ellipse((17, y, 34, y + 17), outline=CYAN, width=2)
            self.text((43, y + 1, 86, y + 20), label, 14, MUTED)
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
        self.small_symbol("game", 204, 17, GREEN if playing else CYAN)
        self.text((233, 10, 359, 42), heading,
                  22, GREEN if playing else CYAN, bold=True)
        self.text((369, 10, 574, 42), english, 9, MUTED)
        self.art((195, 43, 583, 205), appid, strength=80)
        if playing:
            status = "游戏中"
        else:
            status, _ = self.plugin._persona_text(profile)
        detail = f"{status} · AppID {appid}" if appid else status
        title = self._text_bitmap(name, 290, 72, 25, WHITE, True, 2, 19)
        status_line = self._text_bitmap(detail, 290, 24, 15, CYAN, min_size=12)
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
        self.text((630, 10, 710, 42), "在线好友", 18, CYAN, bold=True)
        counter = f"{_value(data.get('online_friend_count'))}/{_value(data.get('friend_count'))}"
        self.text((711, 10, 783, 42), counter, 15, CYAN, min_size=11, align="right")
        friends = [friend for friend in data.get("friends", []) or []
                   if _number(friend.get("personastate")) > 0]
        if not friends:
            message = "好友列表暂不可用" if data.get("friend_count") is None else "目前没有在线好友"
            self.text((607, 110, 780, 148), message, 15, MUTED, lines=2)
            return
        visible = friends[:4]
        # Friend avatars can be slow; resolve every visible friend's game icon
        # first so they cannot spend the shared game-media network budget.
        game_icons = {}
        for friend in visible:
            appid = _appid(friend.get("gameid"))
            if appid and appid not in game_icons:
                game_icons[appid] = self.plugin._game_square_icon(data, appid, FRIEND_GAME_ICON)
        for index, friend in enumerate(visible):
            y = 48 + index * 39
            url = friend.get("avatarfull") or friend.get("avatarmedium") or friend.get("avatar")
            avatar = self.plugin._avatar_image(url, 32, decorative_outline=False)
            if avatar is not None:
                self.image.paste(avatar, (605, y), avatar if avatar.mode == "RGBA" else None)
            self.text((646, y + 1, 783, y + 19), self.plugin._friend_display_id(friend),
                      14, bold=True, min_size=11)
            state, color = self.plugin._persona_text(friend)
            has_game = bool(friend.get("gameid") or friend.get("gameextrainfo"))
            if not has_game:
                self.draw.ellipse((646, y + 23, 652, y + 29), fill=color)
                self.text((658, y + 21, 783, y + 35), state, 12, color)
                continue
            name = self.plugin._display_game_name(data, friend.get("gameid"), friend.get("gameextrainfo"))
            icon = game_icons.get(_appid(friend.get("gameid")))
            if icon is None:
                # Without an official icon keep the in-game status dot; never
                # draw a placeholder that could read as a different game.
                self.draw.ellipse((646, y + 23, 652, y + 29), fill=GREEN)
                text_x = 658
            else:
                # The green frame keeps the in-game status cue the dot carried.
                self.draw.rounded_rectangle((645, y + 20, 660, y + 35), radius=2, outline=GREEN)
                self.image.paste(icon, (646, y + 21), icon if icon.mode == "RGBA" else None)
                text_x = 665
            self.text((text_x, y + 21, 783, y + 35), name or "游戏中", 11, CYAN)

    def recent(self, items):
        recent_ids = {_appid(game.get("appid")) for game in self.data.get("recent_games") or []}
        current_appid = _appid((self.data.get("profile") or {}).get("gameid"))
        has_favorites = any(_appid(item.get("appid")) not in recent_ids | {current_appid}
                            for item in items)
        self.panel((194, 215, 790, 361), header=28)
        self.small_symbol("clock", 205, 221)
        self.text((231, 215, 359, 243), "最近 / 常玩" if has_favorites else "最近游玩",
                  20, CYAN, bold=True, min_size=18)
        self.text((369, 215, 568, 243), "RECENT / FAVORITES" if has_favorites else "RECENTLY PLAYED", 9, MUTED)
        self.text((599, 215, 678, 243), "近2周", 14, CYAN, align="center")
        self.text((701, 215, 780, 243), "总计", 14, CYAN, align="center")
        if not items:
            self.text((211, 285, 773, 314), "没有公开的近期游戏数据", 17, MUTED)
            return
        for index, item in enumerate(items[:4]):
            y = 244 + index * 29
            appid = item.get("appid")
            self.art((195, y, 790, y + 28), appid, strength=175)
            self.icon(appid, 206, y + 2, 24)
            playing = bool(current_appid and _appid(appid) == current_appid)
            right = 537 if playing else 588
            self.text((243, y, right, y + 28), item.get("name"), 14,
                      bold=True, lines=2, min_size=11)
            if playing:
                self.draw.rounded_rectangle((541, y + 4, 586, y + 24), radius=3,
                                            fill=NAVY, outline=GREEN)
                self.text((545, y + 4, 584, y + 24), "正在玩", 11, GREEN, bold=True)
            for field, box in (("playtime_2weeks", (599, y, 678, y + 28)),
                               ("playtime_forever", (701, y, 780, y + 28))):
                self.text(box, _hours(_game_minutes(self.data, appid, field)),
                          15, bold=True, min_size=12, align="center")
            if index < 3:
                self.draw.line((201, y + 28, 783, y + 28), fill=BORDER)

    def top(self):
        self.panel((194, 370, 790, 463), header=27)
        self.small_symbol("chart", 205, 375)
        self.text((231, 370, 388, 397), "常玩 TOP 3", 20, CYAN, bold=True)
        self.text((399, 370, 565, 397), "TOP GAMES", 9, MUTED)
        items = self.plugin._top_game_items(self.data)[:3]
        if not items:
            self.text((212, 420, 777, 444), "暂无公开的累计游玩记录", 16, MUTED)
            return
        for index, item in enumerate(items):
            x = 199 + index * 197
            appid = item.get("appid")
            self.art((x, 399, x + 192, 459), appid, strength=145)
            rank_color = ((249, 202, 77), (181, 205, 223), (210, 153, 88))[index]
            self.draw.polygon(((x, 399), (x + 19, 399), (x + 12, 459), (x, 459)), fill=rank_color)
            self.text((x + 2, 400, x + 15, 459), item.get("rank", index + 1),
                      17, NAVY, bold=True, align="center")
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
            self.draw.rounded_rectangle((x, 399, x + 192, 459), radius=3, outline=BORDER)


def render_console(plugin, data, dimensions, theme_context=None):
    """Return the selected navy console at the device's requested resolution."""
    # This visual design is intentionally consistent across day/night modes.
    console = _Console(plugin, data or {})
    recent = _recent_games(plugin, console.data)
    console.rail()
    console.hero(recent)
    console.recent(recent)
    console.top()
    # Finish the bounded game-art acquisition before optional friend avatars can
    # spend time on the network.  These panels do not overlap visually.
    console.friends()
    if tuple(dimensions) != SIZE:
        return console.image.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return console.image
