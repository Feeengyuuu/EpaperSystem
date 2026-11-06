"""Tennis panel ("two tours").

ATP on the left, WTA on the right: each tour's biggest tournament of the week under its official
mark (Chinese name where it has one, the city and the round), then three matches, live first: the
round and state, and each player's seed as a medal disc, flag icon, name and set scores in boxes
(tie-break points small beside the games), the winner's line in ink and the loser's muted.
"""

from datetime import datetime

from .common import *
from . import tennis
from .icon_marks import espn_flag_url

SportsDashboard = None

TOUR_MARKS = {"ATP": os.path.join(LOCAL_TEAM_LOGO_DIR, "atp.png"), "WTA": os.path.join(LOCAL_TEAM_LOGO_DIR, "wta.png")}
COLUMN_GAP = 10
HEAD_HEIGHT = 24
SET_W = 20


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
        mark = self._load_local_logo(TOUR_MARKS[tour], (40, 14), alpha_threshold=8)
        x = x1 + 8
        if mark:
            mark = SportsDashboard._night_inked_mark(mark)
            image.paste(mark, (x, y1 + (HEAD_HEIGHT - mark.height) // 2), mark)
            x += mark.width + 6
        name = tournament.get("name_zh") or tournament.get("name") or tour
        city = tournament.get("city") or ""
        city_font = self._font(9, False)
        city_w = self._text_width(draw, city, city_font) if city else 0
        name, name_font = self._fit_text_ellipsis(draw, name, x2 - x - city_w - 14, 12, bold=True, min_size=9)
        self._draw_text_in_box(draw, (x, y1, x2 - city_w - 10, y1 + HEAD_HEIGHT), name, name_font, COLORS["text"])
        if city:
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
        self._draw_text_in_box(draw, (x2 - 58, y1 + 2, x2 - 6, y1 + 15), state, self._font(9, live),
                               COLORS["red"] if live else COLORS["muted"], align="right")
        players = match.get("players") or []
        sets = max((len(player.get("sets") or []) for player in players), default=0)
        row_h = (y2 - y1 - 17) // 2
        for index, player in enumerate(players[:2]):
            top = y1 + 16 + index * row_h
            self._draw_tennis_player(image, draw, (x1 + 5, top, x2 - 5, top + row_h), player, match, sets)

    def _draw_tennis_player(self, image, draw, box, player, match, sets):
        x1, y1, x2, y2 = [int(value) for value in box]
        decided = match.get("state") == "post"
        lost = decided and not player.get("winner")
        seed = player.get("seed")
        disc = min(16, y2 - y1 - 2)
        if seed:
            self._draw_rank_medal(draw, (x1, y1 + (y2 - y1 - disc) // 2, x1 + disc, y1 + (y2 - y1 - disc) // 2 + disc), seed,
                                  medal=False)
        flag_x = x1 + disc + 4
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
