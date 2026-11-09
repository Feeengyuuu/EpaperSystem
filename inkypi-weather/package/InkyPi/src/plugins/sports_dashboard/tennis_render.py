"""Tennis panel ("two tours").

ATP on the left, WTA on the right: each tour's biggest tournament of the week under its official
mark (Chinese name where it has one, the city and the round), then three matches, live first: the
round and state, and each player's seed as a medal disc, flag icon, name and set scores in boxes
(tie-break points small beside the games), the winner's line in ink and the loser's muted.
"""

from datetime import datetime

from .common import *
from . import tennis, tennis_events
from .icon_marks import espn_flag_url

SportsDashboard = None

TOUR_MARKS = {"ATP": os.path.join(LOCAL_TEAM_LOGO_DIR, "atp.png"), "WTA": os.path.join(LOCAL_TEAM_LOGO_DIR, "wta.png")}
COLUMN_GAP = 10
HEAD_HEIGHT = 24
SET_W = 20
# The two headshots of a match still to be played, side by side at the block's right edge.
HEADSHOT_PAIR_W = 84
# The share of a WTA torso cut-out kept to match a headshot's framing (head and shoulders).
TORSO_BUST_SHARE = 0.5
# The share of a Wikidata photo's width, from its top middle, kept as the player's head.
WIKI_HEAD_SHARE = 0.62
# A match under way or played shows each player's face in its row, in this slot after the seed.
ROW_FACE_W = 24
# The place beside the tournament's name, by its logo (tennis.place_mark), in this box at most;
# a venue named in words takes at most this share of the header.
PLACE_LOGO = (72, 14)
VENUE_SHARE = 0.36


def match_state_label(match, now):
    if match.get("state") == "in":
        return "进行中"
    if match.get("state") == "post":
        return "已结束" if "retired" not in str(match.get("detail") or "").lower() else "对手退赛"
    moment = tennis._date(match.get("date"))
    if moment is None or "TBD" in str(match.get("detail") or ""):
        return "待定"
    local = moment.astimezone(now.tzinfo) if isinstance(now, datetime) and now.tzinfo else moment
    return f"{local:%m/%d %H:%M}"


class TennisRenderMixin:
    def _draw_tennis_panel(self, image, draw, bounds, card, source_state, now):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        accent = COLORS["blue"]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), accent, COLORS["panel"], 22, 1)
        self._draw_standalone_sport_header(image, draw, x1, y1, x2, "TENNIS", card, source_state)
        left, top, right, bottom = x1 + 12, y1 + 56, x2 - 12, y2 - 6
        tours = (card or {}).get("tours") or {}
        names = [tour for tour in tennis.TOURS if tour in tours]
        if not names:
            self._draw_centered_in_box(draw, (left, top, right, bottom), "本周暂无网球赛事", self._font(14, True), COLORS["muted"])
            return
        width = (right - left - COLUMN_GAP * (len(names) - 1)) // len(names)
        for index, tour in enumerate(names):
            column = (left + index * (width + COLUMN_GAP), top, left + index * (width + COLUMN_GAP) + width, bottom)
            self._draw_tennis_column(image, draw, column, tour, tours[tour], now, accent)

    def _draw_tennis_column(self, image, draw, box, tour, entry, now, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        tournament = entry.get("tournament") or {}
        self._draw_hub_card_shell(draw, x1, y1, x2, y1 + HEAD_HEIGHT, accent)
        # The tournament by its official mark: a Grand Slam's logo, else the event's own logo
        # (tennis_events), else the ATP's tier stamp or the WTA's tier tag; a WTA event without either
        # by the tour's wordmark and its tier as a badge.
        key = tennis.tournament_mark(tournament)
        event = tennis_events.event_for(tournament) or {}
        path = os.path.join(LOCAL_TEAM_LOGO_DIR, f"{key}.png") if key else TOUR_MARKS[tour]
        mark = self._load_local_logo(path, (66, 20) if key else (40, 14), alpha_threshold=8)
        x = x1 + 8
        if mark:
            # A logo printed on its own dark box keeps its colours by night (its letters are white).
            if not (key == event.get("mark") and event.get("ink") == "boxed"):
                mark = SportsDashboard._night_inked_mark(mark)
            image.paste(mark, (x, y1 + (HEAD_HEIGHT - mark.height) // 2), mark)
            x += mark.width + 6
        category = tennis.tournament_category(tournament)
        if not key and category in ("1000", "500", "250"):
            badge_font = self._font(9, True)
            badge_w = self._text_width(draw, category, badge_font) + 10
            draw.rounded_rectangle((x, y1 + 6, x + badge_w, y1 + HEAD_HEIGHT - 6), radius=4,
                                   fill=accent, outline=COLORS["border"], width=1)
            self._draw_centered_in_box(draw, (x, y1 + 6, x + badge_w, y1 + HEAD_HEIGHT - 6), category, badge_font,
                                       (255, 255, 255))
            x += badge_w + 6
        name = tournament.get("name_zh") or tournament.get("name") or tour
        # The place by its official logo (the host city's or the venue's), else the venue by name
        # (tennis_events), else the city ESPN gives.
        place_key = tennis.place_mark(tournament)
        place = self._load_local_logo(os.path.join(LOCAL_TEAM_LOGO_DIR, f"{place_key}.png"), PLACE_LOGO,
                                      alpha_threshold=8) if place_key else None
        city = "" if place is not None else event.get("venue") or tournament.get("city") or ""
        city_font = self._font(9, False)
        if city:
            city, city_font = self._fit_text_ellipsis(draw, city, (x2 - x1) * VENUE_SHARE, 9, min_size=8)
        city_w = place.width if place is not None else (self._text_width(draw, city, city_font) if city else 0)
        name, name_font = self._fit_text_ellipsis(draw, name, x2 - x - city_w - 14, 12, bold=True, min_size=9)
        self._draw_text_in_box(draw, (x, y1, x2 - city_w - 10, y1 + HEAD_HEIGHT), name, name_font, COLORS["text"])
        if place is not None:
            place = SportsDashboard._night_inked_mark(place)
            image.paste(place, (x2 - 8 - place.width, y1 + (HEAD_HEIGHT - place.height) // 2), place)
        elif city:
            self._draw_text_in_box(draw, (x2 - city_w - 8, y1, x2 - 6, y1 + HEAD_HEIGHT), city, city_font, COLORS["muted"],
                                   align="right")
        shown = entry.get("shown") or []
        block_top = y1 + HEAD_HEIGHT + 4
        block_h = (y2 - block_top - 4 * max(0, len(shown) - 1)) // max(1, len(shown)) if shown else 0
        block_h = min(block_h, 58)
        for index, match in enumerate(shown):
            top = block_top + index * (block_h + 4)
            self._draw_tennis_match(image, draw, (x1, top, x2, top + block_h), match, now, accent)

    def _draw_tennis_match(self, image, draw, box, match, now, accent):
        x1, y1, x2, y2 = [int(value) for value in box]
        live = match.get("state") == "in"
        draw.rounded_rectangle((x1, y1, x2, y2), radius=5, fill=COLORS["panel"],
                               outline=COLORS["red"] if live else COLORS["border"], width=2 if live else 1)
        label_font = self._font(9, True)
        round_label = match.get("round_zh") or match.get("round") or ""
        self._draw_text_in_box(draw, (x1 + 7, y1 + 2, x2 - 80, y1 + 15), round_label, label_font, accent)
        state = match_state_label(match, now)
        if live:
            draw.ellipse((x2 - 66, y1 + 5, x2 - 59, y1 + 12), fill=COLORS["red"], outline=COLORS["border"], width=1)
        if match.get("state") == "pre" and state != "待定":
            # A match to come by the page's clock icon and its local start.
            font = self._font(9, False)
            width = self._text_width(draw, state, font)
            self._draw_icon_text(draw, "CLOCK", (x2 - 6 - width - 12, y1 + 2, x2 - 4, y1 + 15), state, font, COLORS["muted"],
                                 accent=accent)
        else:
            self._draw_text_in_box(draw, (x2 - 58, y1 + 2, x2 - 6, y1 + 15), state, self._font(9, live),
                                   COLORS["red"] if live else COLORS["muted"], align="right")
        players = match.get("players") or []
        sets = max((len(player.get("sets") or []) for player in players), default=0)
        # A match still to be played has no set scores: its two players' headshots stand there instead.
        faces = ([(player.get("headshot"), player.get("photo")) for player in players[:2]]
                 if match.get("state") == "pre" and not sets else [])
        reserve = HEADSHOT_PAIR_W if any(url for pair in faces for url in pair) else 0
        row_h = (y2 - y1 - 17) // 2
        for index, player in enumerate(players[:2]):
            top = y1 + 16 + index * row_h
            self._draw_tennis_player(image, draw, (x1 + 5, top, x2 - 5 - reserve, top + row_h), player, match, sets,
                                     face=not faces)
        if reserve:
            self._draw_tennis_headshots(image, (x2 - 4 - reserve, y1 + 18, x2 - 3, y2 - 1), faces)

    def _draw_tennis_headshots(self, image, box, faces):
        """The players' headshots side by side, standing on the block's bottom edge: ESPN's, else the
        tour's own photo (a WTA torso cut-out cropped to head and shoulders)."""
        x1, y1, x2, y2 = [int(value) for value in box]
        height = y2 - y1
        slot = (x2 - x1) // 2
        for index, urls in enumerate(faces):
            face = None
            for url in urls:
                if url and face is None:
                    face = self._tennis_face(url, height)
            if face is None:
                continue
            left = x1 + index * slot + (slot - face.width) // 2
            image.paste(face, (left, y2 - face.height), face)

    def _tennis_face(self, url, height):
        size = int(height * 1.22)
        if "upload.wikimedia.org/" in url:
            # A Wikidata photo shows the player at play: its top middle (the head) is shown round.
            photo = self._load_icon_flag(url, (height * 6, height * 6))
            if photo is None or min(photo.size) < 4:
                return None
            side = max(2, round(photo.width * WIKI_HEAD_SHARE))
            left = (photo.width - side) // 2
            photo = photo.convert("RGBA").crop((left, 0, left + side, min(photo.height, side)))
            photo = ImageOps.fit(photo, (height - 2, height - 2), Image.LANCZOS, centering=(0.5, 0.3))
            mask = Image.new("L", photo.size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, photo.width - 1, photo.height - 1), fill=255)
            photo.putalpha(mask)
            return photo
        if url.lower().endswith(".jpg"):
            # A photo with its own ground (the WTA's plain headshot) is shown as a round avatar.
            photo = self._load_icon_flag(url, (height * 3, height * 3))
            if photo is None or min(photo.size) < 2:
                return None
            photo = ImageOps.fit(photo.convert("RGBA"), (height - 2, height - 2), Image.LANCZOS, centering=(0.5, 0.2))
            mask = Image.new("L", photo.size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, photo.width - 1, photo.height - 1), fill=255)
            photo.putalpha(mask)
            return photo
        if "-Torso_" not in url:
            return self._load_team_logo_for_render(url, size)
        torso = self._load_team_logo_for_render(url, size * 3)
        if torso is None:
            return None
        bust = torso.crop((0, 0, torso.width, int(torso.height * TORSO_BUST_SHARE)))
        return ImageOps.contain(bust, (size, height), Image.LANCZOS)

    def _draw_tennis_player(self, image, draw, box, player, match, sets, *, face=False):
        """A player's row: seed, (with ``face``) their headshot standing on the row's foot, flag, name,
        the winner's tick and the set scores."""
        x1, y1, x2, y2 = [int(value) for value in box]
        decided = match.get("state") == "post"
        lost = decided and not player.get("winner")
        seed = player.get("seed")
        disc = min(16, y2 - y1 - 2)
        if seed:
            self._draw_rank_medal(draw, (x1, y1 + (y2 - y1 - disc) // 2, x1 + disc, y1 + (y2 - y1 - disc) // 2 + disc), seed,
                                  medal=False)
        flag_x = x1 + disc + 4
        if face:
            # The slot stays when ESPN and the tour have no photo, so the match's flags line up.
            self._paste_tennis_row_face(image, (x1 + disc + 2, y1, x1 + disc + 2 + ROW_FACE_W, y2), player)
            flag_x += ROW_FACE_W
        self._draw_flag_icon(image, draw, player.get("flag") or espn_flag_url(player.get("country")),
                             (flag_x, y1 + 3, flag_x + 18, y2 - 3), player.get("country"))
        sets_w = sets * SET_W
        name_x = flag_x + 23
        name = player.get("name") or "TBD"
        name, name_font = self._fit_text_ellipsis(draw, name, x2 - sets_w - name_x - 18, 11,
                                                  bold=not lost, min_size=8)
        self._draw_text_in_box(draw, (name_x, y1, x2 - sets_w - 14, y2), name, name_font,
                               COLORS["muted"] if lost else COLORS["text"])
        if decided and player.get("winner"):
            check_x = x2 - sets_w - 12
            draw.line((check_x, (y1 + y2) // 2, check_x + 3, (y1 + y2) // 2 + 3), fill=COLORS["green"], width=2)
            draw.line((check_x + 3, (y1 + y2) // 2 + 3, check_x + 9, (y1 + y2) // 2 - 4), fill=COLORS["green"], width=2)
        for index, (games, tiebreak) in enumerate(player.get("sets") or []):
            cell = (x2 - sets_w + index * SET_W, y1 + 2, x2 - sets_w + (index + 1) * SET_W - 2, y2 - 2)
            won_set = self._tennis_set_won(match, index, player)
            draw.rectangle(cell, fill=COLORS["panel_gold"] if won_set else COLORS["panel"], outline=COLORS["line"], width=1)
            # The tie-break points sit small in the cell's top-right corner, the games left of them.
            mark_font = self._font(7, False)
            mark_w = self._text_width(draw, str(tiebreak), mark_font) + 1 if tiebreak is not None else 0
            self._draw_centered_in_box(draw, (cell[0] + 1, cell[1], cell[2] - mark_w, cell[3]),
                                       str(games), self._font(11, won_set), COLORS["text"] if won_set else COLORS["muted"])
            if tiebreak is not None:
                draw.text((cell[2] - mark_w, cell[1]), str(tiebreak), font=mark_font, fill=COLORS["muted"])

    def _paste_tennis_row_face(self, image, box, player):
        """The player's face (ESPN's headshot, else the tour's photo) centred in ``box``, on its foot."""
        x1, y1, x2, y2 = box
        for url in (player.get("headshot"), player.get("photo")):
            face = self._tennis_face(url, y2 - y1) if url else None
            if face is not None:
                if face.width > x2 - x1 or face.height > y2 - y1 + 2:
                    face = ImageOps.contain(face, (x2 - x1, y2 - y1 + 2), Image.LANCZOS)
                image.paste(face, (x1 + (x2 - x1 - face.width) // 2, y2 - face.height), face)
                return True
        return False

    @staticmethod
    def _tennis_set_won(match, index, player):
        players = match.get("players") or []
        other = next((item for item in players if item is not player), None)
        mine = (player.get("sets") or [])[index] if index < len(player.get("sets") or []) else None
        theirs = (other.get("sets") or [])[index] if other and index < len(other.get("sets") or []) else None
        if not mine or not theirs:
            return False
        # A set is won at six games two clear, or seven to five or six; one still in play is not.
        return (mine[0] >= 6 and mine[0] - theirs[0] >= 2) or (mine[0] == 7 and theirs[0] in (5, 6))
