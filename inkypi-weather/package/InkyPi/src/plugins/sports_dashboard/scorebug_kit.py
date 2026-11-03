"""Shared building blocks for the lower-left sports panels.

Every lower-left panel (NFL, NCAA, NBA, WNBA, MLB, PGA, F1) uses the same
layout: the sport header, a featured card on the left and a list column on
the right. The featured card for team sports is a broadcast-style scorebug
(status pill, two team rows with a line score, then sport details); the
right column lists the other games with their own clocks.

Sport specifics come from a :class:`ScorebugSpec`. Text on the six-ink panel
stays legible only in pure inks, so secondary text is black and the
hierarchy is carried by weight and size; the muted tone and solid red behind
small text dither into specks.
"""

from PIL import ImageFilter

from .common import *
from .card_backdrops import SPORT_BACKDROP_KINDS, card_backdrop_box, paint_card_backdrop
from .tv_networks import tv_logo_width_ratio, tv_network_parts

SportsDashboard = None


class _VenueName:
    """A venue without an official logo, set as its sport's venue icon then its name: laid out like a
    logo (by its width), drawn straight onto the page so the name keeps the page's own type."""

    def __init__(self, icon, name, font, fill, text_width):
        self.icon, self.name, self.font, self.fill = icon, name, font, fill
        self.width, self.height = icon.width + KIT_VENUE_ICON_GAP + text_width, icon.height

    def draw(self, kit, image, draw, x, y1, y2):
        image.paste(self.icon, (x, y1 + (y2 - y1 - self.icon.height) // 2), self.icon)
        kit._draw_text_in_box(draw, (x + self.icon.width + KIT_VENUE_ICON_GAP, y1, x + self.width, y2), self.name,
                              self.font, self.fill)

KIT_FEATURED_CARD_MAX_WIDTH = 318
KIT_SCOREBUG_ROW_HEIGHT = 33
KIT_SCOREBUG_SCORE_WIDTH = 44
KIT_SCOREBUG_PERIOD_WIDTH = 17
KIT_SCOREBOARD_ROW_HEIGHT = 26
KIT_SCOREBOARD_STATE_WIDTH = 44
# Under a row's kickoff today, its networks' logos stand this tall; beside a live card's pill, this.
KIT_SCOREBOARD_TV_HEIGHT, KIT_STATUS_TV_HEIGHT = 9, 13
# A venue without an official logo reads as its sport's venue icon (assets/decor/venue_icons: a football
# stadium, an indoor arena, a ballpark, a soccer stadium) then its name; the icon at most this tall and
# KIT_VENUE_ICON_WIDE times as wide, KIT_VENUE_ICON_GAP before the name.
VENUE_ICON_KINDS = ("football", "arena", "ballpark", "soccer")
KIT_VENUE_ICON_HEIGHT, KIT_VENUE_ICON_WIDE, KIT_VENUE_ICON_GAP = 13, 1.6, 3
# A game yet to start shows its stadium's logo at the row's right, at most this wide, when its team
# names leave at least the minimum (past this gap after the longer name).
KIT_ROW_VENUE_MAX_W, KIT_ROW_VENUE_MIN_W, KIT_ROW_VENUE_GAP = 72, 24, 8
# On the live score wall a game under way shows its venue the same way, in the room between its
# longer team name and the score column; a ground without a logo reads as its sport's venue icon.
KIT_SPORT_VENUE_KINDS = {"NFL": "football", "NCAA": "football", "NBA": "arena", "WNBA": "arena", "NHL": "arena",
                         "MLB": "ballpark"}
# A live row's leading score sits in a solid ink box this far round its digits (the wall's
# broadcast-scorebug mark); every row's scores keep the room, so all digits stay in one column.
KIT_LEAD_PAD = 3
# A decided game's numbers read in the result's colours, the winner's emerald and the loser's
# crimson (day, then the brighter night tone), so a result reads at a glance without a box.
KIT_RESULT_INKS = {"win": ((22, 150, 96), (74, 222, 148)), "loss": ((214, 62, 72), (255, 112, 118))}
# A one-line "2-0" score parts into its two numbers and the mark between them.
KIT_RESULT_SCORE_RE = re.compile(r"\s*(\d+)\s*([-:–])\s*(\d+)\s*")
# How much wider a decided score draws than its plain text (a pixel either side of the mark), so a
# caller can fit the text to its room less this.
KIT_RESULT_EXTRA = 2
KIT_TILE_HEIGHT = 36
# A finished game's one-line recap (ESPN's headline) after a cobalt "战报" chip (day, then night).
KIT_RECAP_FILLS = ((37, 84, 199), (104, 146, 246))
# The space either side of the rule between a TV tile's networks and its venue logo.
KIT_TILE_VENUE_GAP = 7
# Raised frosted-glass panels over a card's stadium art: the art beneath blurred this much (so its
# detail does not dither into specks behind the type), how much of the fill covers it, and the ink
# shadow's offset down and to the right.
KIT_GLASS_BLUR = 2
KIT_GLASS_ALPHA = 0.6
KIT_GLASS_SHADOW = 2
KIT_TICKER_ROW_HEIGHT = 17
KIT_TICKER_LOGO_SIZE = 12
KIT_TICKER_LOGO_GAP = 3
# Pure panel inks a team colour may map to; white is left out so every team
# stripe stays visible on paper.
KIT_TEAM_INK_KEYS = ("red", "blue", "green", "amber", "text")
# A logo pixel fainter than this against its tile takes the theme's ink.
KIT_LOGO_MIN_CONTRAST = 2.0
# Day timeline in the clock column: a fixed-width day tab with a rail below
# its left edge, so the tabs, rails and times line up from row to row.
KIT_DAY_TAB_WIDTH = 26
KIT_DAY_RAIL_OFFSET = 4
# Leader categories by icon (assets/decor/<set>) instead of a tag: football's pass, run and catch,
# basketball's basket, rebound and pass, hockey's goal, assist and point. A sport without its own
# set reads football's (its PASS / RUSH / REC are no other sport's labels).
FOOTBALL_STAT_ICONS = ("nfl_stat_icons", {"PASS": "passing", "RUSH": "rushing", "REC": "receiving"})
BASKETBALL_STAT_ICONS = ("basketball_stat_icons", {"PTS": "points", "REB": "rebounds", "AST": "assists"})
STAT_ICON_SETS = {"NBA": BASKETBALL_STAT_ICONS, "WNBA": BASKETBALL_STAT_ICONS,
                  "NHL": ("nhl_stat_icons", {"G": "goals", "A": "assists", "PTS": "points"})}
# A leader row's category icon fits this box; a large row's is wider, for basketball's pass.
KIT_LEADER_ICON_SIZE, KIT_LEADER_ICON_BOX_LARGE = 16, (30, 22)
# A large leader row's value says what it counts (basketball's "19 分", "11 篮板").
STAT_UNITS = {sport: {"PTS": "分", "REB": "篮板", "AST": "助攻"} for sport in ("NBA", "WNBA")}
# A leader's stat line ('1290 YDS, 8 TD, 8 INT', '16/18, 172 YDS, 1 TD', '28') parts into figures and
# their labels: the figures in the sport's Bold Italic, the yards (the line's headline) a size up,
# each label after its figure as its icon where the sport has one (UNIT_ICON_SETS), else small and
# light. A line that does not part this way is set as it is.
KIT_STAT_PART_RE = re.compile(r"^([+-]?\d[\d.,/:%]*)(?:\s+([A-Za-z][A-Za-z/%]*))?$")
KIT_STAT_HEADLINE_UNITS = frozenset({"YDS"})
# (figure, headline figure, label) sizes, largest first; parts KIT_STAT_GAP apart, a figure
# KIT_STAT_LABEL_GAP before its label. A label's icon is KIT_STAT_ICON_RISE taller than the label's
# type and at most KIT_STAT_ICON_WIDE times as wide as tall (the yardstick is long).
KIT_STAT_SIZES = ((11, 13, 8), (10, 12, 8), (9, 10, 7))
KIT_STAT_GAP, KIT_STAT_LABEL_GAP = 6, 3
KIT_STAT_ICON_RISE, KIT_STAT_ICON_WIDE = 4, 1.8
# Football's stat labels by icon (assets/decor/football_unit_icons): the yards' yardstick, the
# touchdown's goalpost, the interception's crossed-out ball, the carry's tuck, the catch, and a
# passer's completions ('24/35', which ESPN gives without a label) as a ball with a check.
FOOTBALL_UNIT_ICONS = ("football_unit_icons", {"YDS": "yards", "TD": "touchdowns", "INT": "interceptions",
                                               "CAR": "carries", "REC": "receptions", "CMP": "completions"})
UNIT_ICON_SETS = {"NFL": FOOTBALL_UNIT_ICONS, "NCAA": FOOTBALL_UNIT_ICONS}


def stat_parts(value):
    """[(figure, label)] of a stat line, or [] when any part is not a figure with a label."""
    parts = []
    for part in re.split(r",\s+", str(value or "").strip()):
        match = KIT_STAT_PART_RE.match(part.strip())
        if not match:
            return []
        parts.append((match.group(1), (match.group(2) or "").upper()))
    return parts


def unit_icon_path(sport, label):
    """The icon standing for ``sport``'s stat label ``label``, or None (it is then type)."""
    folder, icons = UNIT_ICON_SETS.get(sport, ("", {}))
    return os.path.join(LOCAL_DECOR_DIR, folder, f"{icons[label]}.png") if label in icons else None


def stat_icon_path(sport, label):
    """The icon standing for ``sport``'s leader category ``label``, or None (it is then a tag)."""
    folder, icons = STAT_ICON_SETS.get(sport, FOOTBALL_STAT_ICONS)
    return os.path.join(LOCAL_DECOR_DIR, folder, f"{icons[label]}.png") if label in icons else None
# Mascot art fills the room under a short list once it is at least this tall.
KIT_SPARE_FILLER_MIN_HEIGHT = 50
KIT_SPARE_STRIP_MIN_HEIGHT = 28
KIT_SPARE_FILLER_MAX_HEIGHT = 130


def _kit_result_ink(outcome):
    """The colour of a decided game's "win" or "loss" number on the current page."""
    day, night = KIT_RESULT_INKS[outcome]
    return night if _kit_luminance(COLORS["panel"]) < 0.2 else day


def _kit_luminance(rgb):
    channels = [value / 255 for value in rgb[:3]]
    channels = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _kit_ink_on(fill):
    """The theme ink or paper text, whichever reads better on ``fill``."""
    ground = _kit_luminance(fill)
    return max((COLORS["text"], COLORS["paper_text"]), key=lambda ink: abs(_kit_luminance(ink) - ground))


def _kit_inked_where_faint(logo, background):
    """``logo`` with the pixels that vanish on ``background`` (black type on a
    dark tile) in the theme's ink; the brand colours that read stay as they are."""
    ground = _kit_luminance(background)
    logo = logo.copy()
    pixels = logo.load()
    for y in range(logo.height):
        for x in range(logo.width):
            pixel = pixels[x, y]
            if pixel[3] < 8:
                continue
            lighter, darker = sorted((_kit_luminance(pixel), ground), reverse=True)
            if (lighter + 0.05) / (darker + 0.05) < KIT_LOGO_MIN_CONTRAST:
                pixels[x, y] = (*COLORS["text"], pixel[3])
    return logo


class ScorebugSpec:
    """Per-sport hooks for the shared scorebug and scoreboard."""

    def __init__(self, sport, accent, live, tag, tint, **hooks):
        self.sport = sport
        self.accent = accent
        self.live = live
        self.tag = tag
        self.tint = tint
        self.hooks = hooks

    def hook(self, name, *args, default=None):
        function = self.hooks.get(name)
        return function(*args) if function else default


class ScorebugKitMixin:
    # Frame ---------------------------------------------------------------------

    def _draw_kit_panel_frame(self, image, draw, bounds, spec, card, source_state, header=None):
        """Panel background and header; returns (featured card box, list column box)."""
        x1, y1, x2, y2 = [int(value) for value in bounds]
        draw.rectangle((x1, y1, x2, y2), fill=COLORS["panel"])
        self._draw_halftone(draw, (x1, y1, x2, y2), COLORS[spec.accent], COLORS["panel"], 22, 1)
        if header is None:
            self._draw_standalone_sport_header(image, draw, x1, y1, x2, spec.sport, card, source_state)
        else:
            header(image, draw, (x1, y1, x2, y2))
        content_y = y1 + 57
        bottom = y2 - 7
        card_w = max(240, min(KIT_FEATURED_CARD_MAX_WIDTH, int((x2 - x1 + 1) * 0.575)))
        split_x = x1 + 12 + card_w + 8
        self._draw_vertical_split(draw, split_x, content_y - 5, bottom)
        return (x1 + 12, content_y, split_x - 9, bottom), (split_x + 6, content_y - 2, x2 - 12, bottom)

    def _draw_kit_card(self, draw, bounds, spec, state, image=None):
        """Card shell, over the sport's stadium art when ``image`` is given;
        returns the inner (left, top, right, bottom) content box."""
        x1, y1, x2, y2 = [int(value) for value in bounds]
        self._draw_hub_card_shell(draw, x1, y1, x2, y2, COLORS[spec.live] if state == "live" else COLORS[spec.accent])
        if image is not None:
            paint_card_backdrop(image, card_backdrop_box(x1, y1, x2, y2), SPORT_BACKDROP_KINDS.get(spec.sport), COLORS["panel"])
        return x1 + 16, y1, x2 - 10, y2

    def _draw_kit_empty_card(self, draw, bounds, spec, text):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        self._draw_kit_card(draw, bounds, spec, "scheduled")
        text, font = self._fit_text(draw, text, x2 - x1 - 40, 14, bold=True, min_size=10)
        self._draw_centered_in_box(draw, (x1 + 12, y1, x2, y2), text, font, COLORS["muted"])

    # Status row ---------------------------------------------------------------

    def _draw_kit_status_row(self, draw, x1, y, x2, spec, state, pill, context="", right="", right_icon=None,
                             image=None, broadcast=""):
        """Pill, context and a right-aligned note; ``right_icon(x, y)`` draws a
        14 px mark just left of the note. A ``broadcast`` context (a live game's networks) reads as
        its networks' logos on ``image``, the label in type only when none of them has one."""
        # Live keeps the dashboard's LIVE-pill idiom (light pill, red block,
        # black text): solid red behind small text dithers into specks.
        marker = 9 if state == "live" else 0
        if state == "final":
            fill, text_fill = COLORS["text"], COLORS["paper_text"]
        else:
            fill, text_fill = (COLORS["panel"] if state == "live" else COLORS[spec.tag]), COLORS["text"]
        pill, pill_font = self._fit_text(draw, pill, 132, 12, bold=True, min_size=9)
        pill_w = self._text_width(draw, pill, pill_font) + 16 + marker
        draw.rounded_rectangle((x1, y, x1 + pill_w, y + 18), radius=4, fill=fill, outline=COLORS["border"], width=1)
        if marker:
            draw.rectangle((x1 + 5, y + 4, x1 + 10, y + 14), fill=COLORS[spec.live], outline=COLORS["border"], width=1)
        self._draw_centered_in_box(draw, (x1 + marker, y, x1 + pill_w, y + 18), pill, pill_font, text_fill)
        right_w = 0
        if right:
            right, right_font = self._fit_text(draw, right, 110, 11, bold=False, min_size=8)
            right_w = self._text_width(draw, right, right_font)
            self._draw_text_in_box(draw, (x2 - right_w, y, x2, y + 18), right, right_font, COLORS["text"], align="right")
            if right_icon:
                right_w += 18
                right_icon(x2 - right_w, y + 2)
        budget = x2 - right_w - 10 - (x1 + pill_w + 8)
        if broadcast and image is not None and budget > 16 and self._draw_kit_small_broadcast(
                image, (x1 + pill_w + 8, y, x1 + pill_w + 8 + budget, y + 18), broadcast, KIT_STATUS_TV_HEIGHT):
            return
        context = context or broadcast
        if context and budget > 16:
            context, context_font = self._fit_text_ellipsis(draw, context, budget, 11, bold=True, min_size=8)
            self._draw_text_in_box(draw, (x1 + pill_w + 8, y, x1 + pill_w + 8 + budget, y + 18), context, context_font, COLORS["text"])

    # Scorebug -----------------------------------------------------------------

    def _draw_kit_scorebug(self, image, draw, x1, y, x2, event, state, spec):
        """Two team rows with a line score; returns the y below the rows."""
        periods = spec.hook("period_count", event, default=0) if state != "scheduled" else 0
        lines_x1 = x2 - KIT_SCOREBUG_SCORE_WIDTH - 4 - periods * KIT_SCOREBUG_PERIOD_WIDTH
        top = y
        if periods:
            current = spec.hook("current_period", event) if state == "live" else None
            for index in range(periods):
                left = lines_x1 + index * KIT_SCOREBUG_PERIOD_WIDTH
                font = self._font(8, current == index + 1)
                label = spec.hook("period_label", index + 1, default=str(index + 1))
                self._draw_centered_in_box(draw, (left, y, left + KIT_SCOREBUG_PERIOD_WIDTH, y + 9), label, font, COLORS["text"])
            top = y + 10
        for index, side in enumerate(("a", "b")):
            row_y = top + index * KIT_SCOREBUG_ROW_HEIGHT
            if index:
                draw.line((x1, row_y - 1, x2, row_y - 1), fill=COLORS["line"], width=1)
            self._draw_kit_scorebug_row(image, draw, (x1, row_y, x2, row_y + KIT_SCOREBUG_ROW_HEIGHT - 2), event, side, state, lines_x1, periods, spec)
        return top + 2 * KIT_SCOREBUG_ROW_HEIGHT

    def _draw_kit_scorebug_row(self, image, draw, box, event, side, state, lines_x1, periods, spec):
        x1, y1, x2, y2 = box
        prefix = f"team_{side}"
        winner = spec.hook("winner_side", event, default="") if state == "final" else ""
        dimmed = bool(winner) and winner != side
        draw.rectangle((x1, y1 + 3, x1 + 3, y2 - 3), fill=COLORS[self._kit_team_ink_key(event.get(f"{prefix}_color"), spec.accent)])
        logo_size = 24
        self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), x1 + 8, y1 + (y2 - y1 - logo_size) // 2, logo_size, spec.hook("logo_fallback", event, side, default=event.get(f"{prefix}_code")))
        name_x = x1 + 38
        has_ball = state == "live" and spec.hook("possession_side", event, default="") == side
        name_budget = lines_x1 - 8 - name_x - (15 if has_ball else 0)
        name = spec.hook("team_name", event, side, True, default=event.get(prefix) or "TBD")
        name, name_font = self._fit_text(draw, name, name_budget, 16, bold=not dimmed, min_size=11)
        self._draw_text_in_box(draw, (name_x, y1, name_x + name_budget, y1 + 19), name, name_font, COLORS["text"])
        if has_ball:
            spec.hook("possession_icon", self, draw, name_x + self._text_width(draw, name, name_font) + 5, y1 + 6)
        sub_x = name_x
        if state == "live":
            sub_x = spec.hook("row_widget", self, draw, sub_x, y1 + 23, event, side, default=sub_x)
        sub = spec.hook("row_note", event, side, state, default=str(event.get(f"record_{side}") or "").strip())
        if sub and lines_x1 - 8 - sub_x > 16:
            sub, sub_font = self._fit_text_ellipsis(draw, sub, lines_x1 - 8 - sub_x, 9, bold=False, min_size=7)
            self._draw_text_in_box(draw, (sub_x, y1 + 20, lines_x1 - 8, y1 + 30), sub, sub_font, COLORS["text"])
        scores = list(spec.hook("linescore", event, side, default=[]) or [])
        current = spec.hook("current_period", event) if state == "live" else None
        for index in range(periods):
            value = scores[index] if index < len(scores) else None
            if value is None:
                continue
            left = lines_x1 + index * KIT_SCOREBUG_PERIOD_WIDTH
            period_font = self._font(10, current == index + 1)
            if spec.sport in {"NBA", "WNBA"}:
                _value, period_font = self._fit_score(draw, str(value), KIT_SCOREBUG_PERIOD_WIDTH - 2, 10,
                                                      bold=current == index + 1, sport=spec.sport, max_height=y2 - y1 - 2)
            self._draw_centered_in_box(draw, (left, y1, left + KIT_SCOREBUG_PERIOD_WIDTH, y2), str(value), period_font, COLORS["text"])
        score_right = x2 - 9 if winner else x2
        score_box = (score_right - KIT_SCOREBUG_SCORE_WIDTH + 6, y1, score_right, y2)
        if state == "scheduled":
            line = spec.hook("pregame_value", event, side, default="")
            if line:
                line, line_font = self._fit_text(draw, line, KIT_SCOREBUG_SCORE_WIDTH, 13, bold=True, min_size=9)
                self._draw_text_in_box(draw, score_box, line, line_font, COLORS["text"], align="right")
            return
        score = event.get(f"wins_{side}")
        score_text, score_font = self._fit_score(draw, "-" if score is None else str(score), score_box[2] - score_box[0], 26,
                                                bold=not dimmed, min_size=16, sport=spec.sport, max_height=y2 - y1)
        # A final's winning score reads emerald with its arrow, the losing one crimson.
        ink = _kit_result_ink("win" if winner == side else "loss") if winner else COLORS["text"]
        self._draw_text_in_box(draw, score_box, score_text, score_font, ink, align="right")
        if winner == side:
            middle = (y1 + y2) // 2
            draw.polygon([(x2, middle - 5), (x2, middle + 5), (x2 - 6, middle)], fill=ink)

    def _draw_kit_pips(self, draw, x, y, filled, total, fill_key="amber"):
        """Timeout / foul style pips; returns the x after the last pip."""
        filled = max(0, min(total, int(filled)))
        for index in range(total):
            left = x + index * 10
            if index < filled:
                draw.rectangle((left, y, left + 7, y + 3), fill=COLORS[fill_key], outline=COLORS["border"], width=1)
            else:
                draw.rectangle((left, y, left + 7, y + 3), outline=COLORS["border"], width=1)
        return x + total * 10 - 3

    # Details ------------------------------------------------------------------

    def _draw_kit_glass(self, image, box, fill, radius=4, alpha=KIT_GLASS_ALPHA):
        """A raised frosted-glass panel at ``box``: a hard ink shadow down and to the right
        (only where the panel does not cover it), the art beneath blurred and showing through
        ``fill``, a light sheen along its top edge and an ink outline."""
        x1, y1, x2, y2 = [int(value) for value in box]
        offset = KIT_GLASS_SHADOW
        size = (x2 - x1 + 1 + offset, y2 - y1 + 1 + offset)
        panel = Image.new("L", size, 0)
        ImageDraw.Draw(panel).rounded_rectangle((0, 0, x2 - x1, y2 - y1), radius=radius, fill=255)
        shadow = Image.new("L", size, 0)
        ImageDraw.Draw(shadow).rounded_rectangle((offset, offset, size[0] - 1, size[1] - 1), radius=radius, fill=255)
        image.paste(COLORS["border"], (x1, y1, x1 + size[0], y1 + size[1]), ImageChops.subtract(shadow, panel))
        frost = image.crop((x1, y1, x1 + size[0], y1 + size[1])).filter(ImageFilter.GaussianBlur(KIT_GLASS_BLUR))
        image.paste(frost, (x1, y1), panel)
        image.paste(fill, (x1, y1, x1 + size[0], y1 + size[1]), panel.point(lambda value: round(value * alpha)))
        draw = ImageDraw.Draw(image)
        sheen = tuple(round(channel + (255 - channel) * 0.6) for channel in fill)
        draw.line((x1 + radius, y1 + 1, x2 - radius, y1 + 1), fill=sheen, width=1)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, outline=COLORS["border"], width=1)

    def _draw_kit_tiles(self, draw, box, tiles, spec, tile_h=KIT_TILE_HEIGHT, image=None, glass=False, venue=None):
        """Label-over-value tiles; with ``image`` a TV tile shows the networks'
        logos (and, given a ``venue`` with a logo, that logo after a rule at its right),
        and ``glass`` raises each tile as translucent glass over the card's art."""
        x1, y1, x2, _y2 = [int(value) for value in box]
        tiles = [tile for tile in tiles if tile]
        if not tiles:
            return y1
        gap = 6
        tile_w = (x2 - x1 - gap * (len(tiles) - 1)) // len(tiles)
        label_font = self._font(8, False)
        for index, (label, value) in enumerate(tiles):
            left = x1 + index * (tile_w + gap)
            right = left + tile_w
            if glass and image is not None:
                self._draw_kit_glass(image, (left, y1, right, y1 + tile_h), COLORS[spec.tint])
            else:
                draw.rounded_rectangle((left, y1, right, y1 + tile_h), radius=4, fill=COLORS[spec.tint], outline=COLORS["border"], width=1)
            self._draw_text_in_box(draw, (left + 7, y1 + 3, right - 4, y1 + 13), label, label_font, COLORS["text"])
            value_box = (left + 7, y1 + 14, right - 4, y1 + tile_h - 3)
            if image is not None and label == "TV" and venue:
                value_box = self._draw_kit_tile_venue(image, draw, value_box, venue, tile_h)
            if image is not None and label == "TV" and self._draw_kit_tv_logos(image, draw, value_box, value, COLORS[spec.tint]):
                continue
            if SportsDashboard._kit_countdown_parts(value):
                self._draw_kit_countdown(draw, value_box, value, spec)
                continue
            if label == "O/U" and re.fullmatch(r"\d+(?:\.\d+)?", str(value or "")):
                self._draw_kit_total(draw, value_box, str(value))
                continue
            value, value_font = self._fit_text_ellipsis(draw, str(value or "--"), tile_w - 12, 15, bold=True, min_size=9)
            self._draw_text_in_box(draw, value_box, value, value_font, COLORS["text"])
        return y1 + tile_h

    def _draw_kit_tile_venue(self, image, draw, box, venue, tile_h):
        """The venue's logo at the right of a TV tile's value row (as tall as the tile allows,
        at most half its width) after a short rule; the room left for the networks."""
        x1, y1, x2, y2 = [int(value) for value in box]
        height = min(tile_h - 8, y2 - y1 + 6)
        logo = self._kit_venue_logo(venue, ((x2 - x1) // 2, height))
        if logo is None:
            return box
        middle = (y1 + y2) // 2
        logo_x = x2 - logo.width
        image.paste(logo, (logo_x, middle - logo.height // 2), logo)
        rule_x = logo_x - KIT_TILE_VENUE_GAP
        draw.line((rule_x, y1 - 9, rule_x, y2), fill=COLORS["border"], width=1)
        return (x1, y1, rule_x - KIT_TILE_VENUE_GAP, y2)

    def _draw_kit_countdown(self, draw, box, value, spec):
        """'IN 1H 40M' as a stopwatch and flip-clock cards: each number on a
        dark card with hinge notches on its sides, its unit beside it."""
        x1, y1, x2, y2 = [int(value) for value in box]
        middle = (y1 + y2) // 2
        self._draw_kit_stopwatch(draw, x1, middle - 6, spec)
        number_font, unit_font = self._font(min(14, y2 - y1 - 3), True), self._font(9, True)
        card, digits, hinge = COLORS["text"], COLORS["paper_text"], COLORS[spec.tint]
        x = x1 + 15
        for number, unit in SportsDashboard._kit_countdown_parts(value):
            card_w = self._text_width(draw, number, number_font) + 6
            unit_w = self._text_width(draw, unit, unit_font)
            if x + card_w + 1 + unit_w > x2:
                break
            draw.rounded_rectangle((x, y1, x + card_w, y2), radius=3, fill=card)
            self._draw_centered_in_box(draw, (x, y1, x + card_w, y2), number, number_font, digits)
            draw.rectangle((x, middle, x + 1, middle + 1), fill=hinge)
            draw.rectangle((x + card_w - 1, middle, x + card_w, middle + 1), fill=hinge)
            x += card_w + 1
            self._draw_text_in_box(draw, (x, middle - 2, x + unit_w, y2), unit, unit_font, COLORS["text"])
            x += unit_w + 4

    def _draw_kit_total(self, draw, box, total):
        """The over/under line: an over (green, up) and under (red, down)
        arrow pair, the total, then 'PTS'."""
        x1, y1, x2, y2 = [int(value) for value in box]
        middle = (y1 + y2) // 2
        draw.polygon([(x1, middle - 1), (x1 + 5, middle - 7), (x1 + 10, middle - 1)], fill=COLORS["green"], outline=COLORS["border"])
        draw.polygon([(x1, middle + 2), (x1 + 5, middle + 8), (x1 + 10, middle + 2)], fill=COLORS["red"], outline=COLORS["border"])
        unit_font = self._font(8, True)
        unit_w = self._text_width(draw, "PTS", unit_font)
        total, total_font = self._fit_text(draw, total, x2 - x1 - 15 - unit_w - 3, 15, bold=True, min_size=9)
        self._draw_text_in_box(draw, (x1 + 15, y1, x2, y2), total, total_font, COLORS["text"])
        unit_x = x1 + 15 + self._text_width(draw, total, total_font) + 3
        self._draw_text_in_box(draw, (unit_x, middle - 1, unit_x + unit_w, y2), "PTS", unit_font, COLORS["text"])

    def _draw_kit_stopwatch(self, draw, x, y, spec):
        """An 11x13 stopwatch: crown, side button, face and two hands."""
        ink = COLORS["text"]
        draw.rectangle((x + 4, y, x + 6, y + 1), fill=ink)
        draw.point((x + 10, y + 2), fill=ink)
        draw.ellipse((x, y + 2, x + 11, y + 13), fill=COLORS["panel"], outline=ink, width=2)
        center_x, center_y = x + 5, y + 8
        draw.line((center_x, center_y, center_x, center_y - 3), fill=ink, width=1)
        draw.line((center_x, center_y, center_x + 2, center_y + 2), fill=COLORS[spec.live], width=1)

    def _draw_kit_tv_logos(self, image, draw, box, label, background):
        """Each network of a broadcast label as its logo, left to right, with
        the name in text for a network without one. False when no logo applies."""
        x1, y1, x2, y2 = [int(value) for value in box]
        parts = [(name, os.path.join(LOCAL_TV_LOGO_DIR, f"{key}.png") if key else None, tv_logo_width_ratio(key))
                 for name, key in tv_network_parts(label)]
        if not any(path and os.path.exists(path) for _name, path, _ratio in parts):
            return False
        height, x = y2 - y1, x1
        for name, path, ratio in parts:
            if x2 - x < 14:
                break
            logo = self._load_local_logo(path, (min(x2 - x, height * ratio), height), alpha_threshold=8) if path else None
            if logo:
                logo = _kit_inked_where_faint(logo, background)
                image.paste(logo, (x, y1 + (height - logo.height) // 2), logo)
                x += logo.width + 7
            else:
                text, font = self._fit_text_ellipsis(draw, name, x2 - x, 13, bold=True, min_size=9)
                self._draw_text_in_box(draw, (x, y1, x2, y2), text, font, COLORS["text"])
                x += self._text_width(draw, text, font) + 7
        return True

    def _draw_series_row_broadcast(self, image, draw, box, event):
        """A series row's networks as small logos, right-aligned; the label in text without one."""
        x1, y1, x2, y2 = [int(value) for value in box]
        label = str(event.get("broadcast") or "").strip()
        if not label or self._draw_kit_small_broadcast(image, box, label, max(9, y2 - y1 - 3), align="right"):
            return
        text, font = self._fit_text_ellipsis(draw, label, x2 - x1, 10, bold=False, min_size=8)
        self._draw_text_in_box(draw, (x1, y1, x2, y2), text, font, COLORS["text"], align="right")

    def _draw_kit_small_broadcast(self, image, box, label, height, align="left"):
        """A broadcast's networks as logos ``height`` tall in a row's ``box``, at its ``align`` side
        and centred on it; False (nothing drawn) when none of them has a logo."""
        x1, y1, x2, y2 = [int(value) for value in box]
        strip = Image.new("RGBA", (max(1, x2 - x1), height), (0, 0, 0, 0))
        if not self._draw_kit_tv_logos(strip, ImageDraw.Draw(strip), (0, 0, strip.width, height), label, COLORS["panel"]):
            return False
        bounds = strip.getchannel("A").getbbox()
        if not bounds:
            return False
        used = strip.crop((0, 0, bounds[2], height))
        image.paste(used, (x2 - used.width if align == "right" else x1, y1 + (y2 - y1 - height) // 2), used)
        return True

    def _draw_kit_broadcast_logos(self, image, draw, box, label, height):
        """A broadcast's networks as logos ``height`` tall, centred in ``box``; the label as a
        footer line when none of them has a logo."""
        if not label:
            return
        x1, y1, x2, y2 = [int(value) for value in box]
        height = min(height, y2 - y1)
        if height >= 9 and x2 > x1:
            strip = Image.new("RGBA", (x2 - x1, height), (0, 0, 0, 0))
            if self._draw_kit_tv_logos(strip, ImageDraw.Draw(strip), (0, 0, strip.width, height), label, COLORS["panel"]):
                bounds = strip.getchannel("A").getbbox()
                if bounds:
                    used = strip.crop((bounds[0], 0, bounds[2], height))
                    image.paste(used, (x1 + (x2 - x1 - used.width) // 2, y1 + (y2 - y1 - height) // 2), used)
                    return
        self._draw_kit_footer(draw, (x1, y2 - 12, x2, y2), label)

    def _kit_title_wordmark(self, wordmark):
        """A league's title wordmark for the theme's panel: by night its type drawn dark for a light
        page (MLB's, the WNBA's, the PGA TOUR's) takes the ink; its brand colours stay."""
        if wordmark is None or _kit_luminance(COLORS["panel"]) >= 0.2:
            return wordmark
        return _kit_inked_where_faint(wordmark, COLORS["panel"])

    def _kit_venue_logo(self, venue, size):
        """The venue's official logo fitted to ``size`` and inked for the theme's card, else None."""
        key = VENUE_LOGO_KEYS.get(re.sub(r"[^a-z0-9]", "", str(venue or "").lower()))
        return self._kit_print_logo(os.path.join(LOCAL_VENUE_LOGO_DIR, f"{key}.png") if key else None, size)

    def _kit_print_logo(self, path, size):
        """A logo printed for a white ground (a venue's, a Grand Prix's) fitted to ``size`` and inked
        for the theme's card, else None."""
        logo = self._load_local_logo(path, size, alpha_threshold=8) if path else None
        if not logo:
            return None
        # The logos are print art for a white ground, so a light card shows them as
        # printed (a ballpark's yellow roofline stays yellow).
        if _kit_luminance(COLORS["panel"]) < 0.2:
            # On a dark card the lettering is ink throughout, as on the arenas' own
            # dark-ground lockups; the brand colours stay.
            logo = _kit_inked_where_faint(logo, COLORS["panel"])
            pixels = logo.load()
            for y in range(logo.height):
                for x in range(logo.width):
                    red, green, blue, alpha = pixels[x, y]
                    if alpha >= 8 and max(red, green, blue) - min(red, green, blue) < 28:
                        pixels[x, y] = (*COLORS["text"], alpha)
        return logo

    def _kit_city_logo(self, city, size):
        """The home city's official emblem fitted to ``size`` and inked for the theme's card, else None."""
        key = CITY_LOGO_KEYS.get(re.sub(r"[^a-z0-9]", "", str(city or "").lower()))
        logo = self._load_local_logo(os.path.join(LOCAL_CITY_LOGO_DIR, f"{key}.png"), size, alpha_threshold=8) if key else None
        if logo and _kit_luminance(COLORS["panel"]) < 0.2:
            # A seal's line art is printed dark for a white ground: on a dark card it takes the ink.
            logo = _kit_inked_where_faint(logo, COLORS["panel"])
        return logo or None

    def _kit_venue_icon(self, kind, height):
        """``kind``'s venue icon (VENUE_ICON_KINDS) ``height`` tall, else None."""
        if kind not in VENUE_ICON_KINDS:
            return None
        return self._load_local_logo(os.path.join(LOCAL_DECOR_DIR, "venue_icons", f"{kind}.png"),
                                     (round(height * KIT_VENUE_ICON_WIDE), height), alpha_threshold=8)

    def _kit_venue_name(self, draw, venue, kind, height, max_width, size, bold, fill):
        """A venue without an official logo as its ``kind``'s icon and its name within ``max_width``
        (a _VenueName), else None."""
        icon = self._kit_venue_icon(kind, min(height, KIT_VENUE_ICON_HEIGHT)) if venue else None
        room = max_width - (icon.width + KIT_VENUE_ICON_GAP if icon else 0)
        if icon is None or room < 24:
            return None
        name, font = self._fit_text_ellipsis(draw, venue, room, size, bold=bold, min_size=7)
        return _VenueName(icon, name, font, fill, self._text_width(draw, name, font))

    def _draw_kit_venue(self, image, draw, box, venue, kind=None):
        """The venue as its arena's official logo centred in ``box``, else as ``kind``'s venue icon and
        its name, else its name as a footer line."""
        if not venue:
            return
        x1, y1, x2, y2 = [int(value) for value in box]
        logo = self._kit_venue_logo(venue, (x2 - x1, y2 - y1))
        if logo:
            image.paste(logo, (x1 + (x2 - x1 - logo.width) // 2, y1 + (y2 - y1 - logo.height) // 2), logo)
            return
        mark = self._kit_venue_name(draw, venue, kind, y2 - y1, x2 - x1, 9, False, COLORS["text"]) if kind else None
        if mark is not None:
            mark.draw(self, image, draw, x1 + (x2 - x1 - mark.width) // 2, y1, y2)
            return
        self._draw_kit_footer(draw, box, venue)

    def _draw_kit_venue_line(self, image, draw, box, venue, before="", after="", *, size=9, bold=False,
                             align="center", separator="·", logo_height=None, fill=None, kind=None):
        """``before``, the venue's official logo in place of its name, and ``after`` on one line in
        ``box`` (centred, or from its left), parted by ``separator``. A venue without a logo reads as
        ``kind``'s venue icon and its name; False, with nothing drawn, when it has neither, so the
        caller keeps its text line."""
        x1, y1, x2, y2 = [int(value) for value in box]
        before, after = str(before or "").strip(), str(after or "").strip()
        height = int(logo_height or (y2 - y1))
        fill = COLORS["text"] if fill is None else fill
        logo = self._kit_venue_logo(venue, ((x2 - x1) // 2 if before or after else x2 - x1, height)) if venue else None
        if logo is None and kind:
            logo = self._kit_venue_name(draw, venue, kind, height, (x2 - x1) * 2 // 3 if before or after else x2 - x1,
                                        size, bold, fill)
        if logo is None:
            return False
        join_font = self._font(size, bold)
        join_w = self._text_width(draw, separator, join_font) + 12
        # The home city after the venue, as its official emblem when it has one.
        city = self._kit_city_logo(after, (height * 3, height + 2)) if after else None
        if city is not None:
            after = ""
        room = x2 - x1 - logo.width - (join_w + city.width if city is not None else 0)
        # (text, font, width) for the text before the logo and the text after it.
        pieces = []
        for text in (before, after):
            if not text or room - join_w < 16:
                pieces.append(None)
                continue
            text, font = self._fit_text_ellipsis(draw, text, room - join_w, size, bold=bold, min_size=7)
            width = self._text_width(draw, text, font)
            pieces.append((text, font, width))
            room -= width + join_w
        total = logo.width + sum(piece[2] + join_w for piece in pieces if piece) + (join_w + city.width if city is not None else 0)
        x = x1 if align == "left" else x1 + (x2 - x1 - total) // 2
        for index, piece in enumerate(pieces):
            if index == 1:
                if isinstance(logo, _VenueName):
                    logo.draw(self, image, draw, x, y1, y2)
                else:
                    image.paste(logo, (x, y1 + (y2 - y1 - logo.height) // 2), logo)
                x += logo.width
            if not piece:
                continue
            text, font, width = piece
            if index == 1:
                self._draw_centered_in_box(draw, (x, y1, x + join_w, y2), separator, join_font, fill)
                x += join_w
            self._draw_text_in_box(draw, (x, y1, x + width, y2), text, font, fill)
            x += width
            if index == 0:
                self._draw_centered_in_box(draw, (x, y1, x + join_w, y2), separator, join_font, fill)
                x += join_w
        if city is not None:
            self._draw_centered_in_box(draw, (x, y1, x + join_w, y2), separator, join_font, fill)
            x += join_w
            image.paste(city, (x, y1 + (y2 - y1 - city.height) // 2), city)
        return True

    def _draw_series_row_winner_logo(self, image, draw, event, side, x, y1, y2, spec):
        size = min(13, y2 - y1)
        fallback = spec.hook("logo_fallback", event, side, default=event.get(f"team_{side}_code"))
        self._draw_team_logo(image, draw, event.get(f"team_{side}_logo"), x, y1 + (y2 - y1 - size) // 2, size, fallback)

    def _draw_kit_leader_rows(self, image, draw, x1, y, x2, y_limit, event, leaders, spec, max_rows=3, large=False):
        """The leaders, a row each: the category's icon (else its tag), the team's logo, the name and
        the value. ``large`` rows (for a card with room) set them bigger, the value bold with its unit."""
        label_font = self._font(8, True)
        row_h, pitch, icon_box, logo_size, name_size = (24, 28, KIT_LEADER_ICON_BOX_LARGE, 16, 12) if large else \
            (14, 17, (KIT_LEADER_ICON_SIZE, KIT_LEADER_ICON_SIZE), 12, 10)
        units = STAT_UNITS.get(spec.sport, {}) if large else {}
        for leader in list(leaders or [])[:max_rows]:
            if y + row_h > y_limit:
                break
            label = leader.get("label") or ""
            middle = y + row_h // 2
            path = stat_icon_path(spec.sport, label)
            icon = self._load_local_logo(path, icon_box, alpha_threshold=8) if path else None
            if icon:
                # A category reads as its icon: the pass, the run, the catch; the basket, the rebound.
                image.paste(icon, (x1 + (icon_box[0] - icon.width) // 2, middle - icon.height // 2), icon)
                name_x = x1 + icon_box[0] + 5
            else:
                draw.rounded_rectangle((x1, middle - 6, x1 + 30, middle + 6), radius=3, fill=COLORS[spec.tag], outline=COLORS["border"], width=1)
                self._draw_centered_in_box(draw, (x1, middle - 6, x1 + 30, middle + 6), label, label_font, COLORS["text"])
                name_x = x1 + 36
            side = leader.get("side")
            if side in {"a", "b"}:
                prefix = f"team_{side}"
                self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), name_x, middle - logo_size // 2, logo_size,
                                     spec.hook("logo_fallback", event, side, default=event.get(f"{prefix}_code")))
                name_x += logo_size + 4
            value_right = x2
            unit = units.get(label)
            if unit:
                unit_font = self._font(9, False)
                unit_w = self._text_width(draw, unit, unit_font)
                self._draw_text_in_box(draw, (x2 - unit_w, middle - 3, x2, middle + 8), unit, unit_font, COLORS["text"],
                                       align="right")
                value_right = x2 - unit_w - 2
            text = str(leader.get("value") or "")
            parts = stat_parts(text)
            if label == "PASS" and parts and "/" in parts[0][0] and not parts[0][1]:
                # A passer's line opens on completions of attempts ('24/35').
                parts[0] = (parts[0][0], "CMP")
            value_w = 0
            if parts:
                sizes = ((16, 16, 9),) if large else KIT_STAT_SIZES
                value_w = self._draw_kit_stat_line(image, draw, value_right, middle, parts, spec.sport, sizes,
                                                   (x2 - x1) // 2)
            if not value_w:
                value, value_font = self._fit_text_ellipsis(draw, text, (x2 - x1) // 2, 15 if large else 9, bold=large,
                                                            min_size=8)
                value_w = self._text_width(draw, value, value_font)
                self._draw_text_in_box(draw, (value_right - value_w, y, value_right, y + row_h), value, value_font,
                                       COLORS["text"], align="right")
            name, name_font = self._fit_text_ellipsis(draw, leader.get("name") or "", value_right - value_w - 8 - name_x,
                                                      name_size, bold=True, min_size=8)
            self._draw_text_in_box(draw, (name_x, y, value_right - value_w - 8, y + row_h), name, name_font, COLORS["text"])
            y += pitch
        return y

    def _draw_kit_stat_line(self, image, draw, right, middle, parts, sport, sizes, max_width):
        """``parts`` (stat_parts) ending at ``right``, the figures' digits centred on ``middle``, at
        the first of ``sizes`` that fits ``max_width``: each label as its icon (unit_icon_path),
        centred on the row, else as type on the figures' baseline. Returns the width drawn, 0 when
        none fits."""
        for figure_size, headline_size, label_size in sizes:
            label_font = self._font(label_size, False)
            icon_h = label_size + KIT_STAT_ICON_RISE
            figures, runs = [], []
            for index, (figure, label) in enumerate(parts):
                size = headline_size if label in KIT_STAT_HEADLINE_UNITS else figure_size
                if index:
                    runs.append(("gap", KIT_STAT_GAP))
                figures.append(self._stat_font(sport, size))
                runs.append(("text", figure, figures[-1]))
                if not label:
                    continue
                path = unit_icon_path(sport, label)
                icon = self._load_local_logo(path, (round(icon_h * KIT_STAT_ICON_WIDE), icon_h), alpha_threshold=8) \
                    if path else None
                runs.append(("gap", KIT_STAT_LABEL_GAP))
                runs.append(("icon", icon) if icon is not None else ("text", label, label_font))
            widths = [run[1] if run[0] == "gap" else run[1].width if run[0] == "icon"
                      else draw.textlength(run[1], font=run[2]) for run in runs]
            width = sum(widths)
            if width > max_width:
                continue
            # One baseline under every run: the figures' digits centred on the row.
            baseline = int(middle - min(font.getbbox("8", anchor="ls")[1] for font in figures) / 2)
            x = right - width
            for run, run_w in zip(runs, widths):
                if run[0] == "text":
                    draw.text((x, baseline), run[1], font=run[2], fill=COLORS["text"], anchor="ls")
                elif run[0] == "icon":
                    image.paste(run[1], (round(x), round(middle - run[1].height / 2)), run[1])
                x += run_w
            return round(width)
        return 0

    def _draw_kit_labeled_line(self, draw, box, label, text, bold=True):
        """A "LABEL  value" line in pure black, value truncated with an ellipsis."""
        x1, y1, x2, y2 = [int(value) for value in box]
        label_w = 0
        if label:
            label_font = self._font(8, False)
            label_w = self._text_width(draw, label, label_font) + 6
            self._draw_text_in_box(draw, (x1, y1, x1 + label_w, y2), label, label_font, COLORS["text"])
        text, font = self._fit_text_ellipsis(draw, text, x2 - x1 - label_w, 10, bold=bold, min_size=8)
        self._draw_text_in_box(draw, (x1 + label_w, y1, x2, y2), text, font, COLORS["text"])

    def _draw_kit_recap_line(self, draw, box, recap):
        """A cobalt '战报' chip, then a finished game's recap headline on one line."""
        x1, y1, x2, y2 = [int(value) for value in box]
        chip_font = self._font(9, True)
        chip_w = self._text_width(draw, "战报", chip_font) + 8
        fill = KIT_RECAP_FILLS[1] if _kit_luminance(COLORS["panel"]) < 0.2 else KIT_RECAP_FILLS[0]
        draw.rounded_rectangle((x1, y1, x1 + chip_w, y2), radius=3, fill=fill)
        self._draw_centered_in_box(draw, (x1, y1, x1 + chip_w, y2), "战报", chip_font, _kit_ink_on(fill))
        text, font = self._fit_text_ellipsis(draw, recap, x2 - x1 - chip_w - 6, 9, bold=False, min_size=8)
        self._draw_text_in_box(draw, (x1 + chip_w + 6, y1, x2, y2), text, font, COLORS["text"])

    def _draw_kit_footer(self, draw, box, text):
        if not text:
            return
        x1, y1, x2, y2 = box
        text, font = self._fit_text_ellipsis(draw, text, x2 - x1, 9, bold=False, min_size=8)
        self._draw_centered_in_box(draw, box, text, font, COLORS["text"])

    def _draw_kit_share_bar(self, draw, box, left_label, right_label, left_share, left_ink, right_ink, label="WIN",
                            image=None, logos=(None, None)):
        """Two-team share bar (win probability); labels in pure black, each team's logo on the outside of its label."""
        x1, y1, x2, y2 = [int(value) for value in box]
        middle = (y1 + y2) // 2
        left_bold = left_share >= 0.5
        left_font = self._font(10, left_bold)
        right_font = self._font(10, not left_bold)
        label_w = 0
        if label:
            label_w = 28
            self._draw_text_in_box(draw, (x1, y1, x1 + label_w, y2), label, self._font(8, False), COLORS["text"])
        left_logo, right_logo = logos if image is not None else (None, None)
        left_x, right_x = x1 + label_w, x2
        if left_logo is not None:
            image.paste(left_logo, (left_x, middle - left_logo.height // 2), left_logo)
            left_x += left_logo.width + 3
        if right_logo is not None:
            right_x -= right_logo.width
            image.paste(right_logo, (right_x, middle - right_logo.height // 2), right_logo)
            right_x -= 3
        left_w = self._text_width(draw, left_label, left_font)
        right_w = self._text_width(draw, right_label, right_font)
        self._draw_text_in_box(draw, (left_x, y1, left_x + left_w, y2), left_label, left_font, COLORS["text"])
        self._draw_text_in_box(draw, (right_x - right_w, y1, right_x, y2), right_label, right_font, COLORS["text"], align="right")
        bar_x1 = left_x + left_w + 6
        bar_x2 = right_x - right_w - 6
        if bar_x2 - bar_x1 < 20:
            return
        split = int(round(bar_x1 + (bar_x2 - bar_x1) * max(0.0, min(1.0, left_share))))
        draw.rectangle((bar_x1, middle - 3, bar_x2, middle + 3), fill=COLORS["panel"], outline=COLORS["border"], width=1)
        if split > bar_x1:
            draw.rectangle((bar_x1, middle - 3, split, middle + 3), fill=COLORS[left_ink], outline=COLORS["border"], width=1)
        if bar_x2 > split and right_ink != left_ink:
            draw.rectangle((split, middle - 3, bar_x2, middle + 3), fill=COLORS[right_ink], outline=COLORS["border"], width=1)

    # Final scores ---------------------------------------------------------------

    def _kit_result_layout(self, draw, text, font, winner=None):
        """(parts, slot widths, winning part index) for a decided one-line score ("2-0"), the
        winner taken from the numbers unless ``winner`` ("a" / "b", "" for none) says; None for a
        draw, an undecided game or a score that is not two numbers."""
        match = KIT_RESULT_SCORE_RE.fullmatch(str(text or ""))
        if not match:
            return None
        parts = match.groups()
        if winner is None:
            first, second = int(parts[0]), int(parts[2])
            winner = "a" if first > second else "b" if second > first else ""
        if winner not in ("a", "b"):
            return None
        widths = [self._text_width(draw, part, font) for part in parts]
        return parts, (widths[0], widths[1] + KIT_RESULT_EXTRA, widths[2]), 0 if winner == "a" else 2

    def _kit_result_width(self, draw, text, font, winner=None):
        """How wide ``_draw_kit_result_score`` draws ``text``."""
        layout = self._kit_result_layout(draw, text, font, winner)
        return sum(layout[1]) if layout else self._text_width(draw, text, font)

    def _draw_kit_result_score(self, draw, box, text, font, align="center", winner=None, draw_text=None):
        """A one-line final score in ``box``, the winner's number emerald and the loser's crimson, the
        mark between them in ink; anything else (a draw, "vs", a live score) is drawn as it is.
        ``draw_text`` (default ``_draw_text_in_box``) draws each piece."""
        x1, y1, x2, y2 = box
        draw_text = draw_text or self._draw_text_in_box
        layout = self._kit_result_layout(draw, text, font, winner)
        if layout is None:
            draw_text(draw, box, text, font, COLORS["text"], align=align)
            return
        parts, slots, won = layout
        total = sum(slots)
        x = x2 - total if align == "right" else x1 if align == "left" else x1 + (x2 - x1 - total) / 2
        for index, (part, slot) in enumerate(zip(parts, slots)):
            ink = COLORS["text"] if index == 1 else _kit_result_ink("win" if index == won else "loss")
            draw_text(draw, (x, y1, x + slot, y2), part, font, ink, align="center")
            x += slot

    # Scoreboard column --------------------------------------------------------

    def _draw_kit_column_header(self, draw, x1, y1, x2, spec, title, counter=""):
        draw.rectangle((x1, y1 + 2, x1 + 8, y1 + 17), fill=COLORS[spec.accent], outline=COLORS["border"], width=1)
        counter_font = self._font(10, True)
        counter_w = self._text_width(draw, counter, counter_font) if counter else 0
        title, title_font = self._fit_text(draw, title, x2 - x1 - 22 - counter_w, 13, bold=True, min_size=9)
        self._draw_text_in_box(draw, (x1 + 13, y1, x2 - counter_w - 4, y1 + 18), title, title_font, COLORS["text"])
        if counter:
            self._draw_text_in_box(draw, (x2 - counter_w, y1 + 1, x2, y1 + 17), counter, counter_font, COLORS[spec.live], align="right")
        draw.line((x1, y1 + 19, x2, y1 + 19), fill=COLORS["border"], width=1)
        return y1 + 23

    def _draw_kit_scoreboard_column(self, image, draw, bounds, games, now, spec, title="SCOREBOARD", empty="No other games"):
        x1, y1, x2, y2 = [int(value) for value in bounds]
        live_count = sum(1 for event in games if SportsDashboard._hub_event_state(event) == "live")
        row_y = self._draw_kit_column_header(draw, x1, y1, x2, spec, title, f"{live_count} LIVE" if live_count else "")
        if not games:
            self._draw_text_in_box(draw, (x1 + 10, row_y + 2, x2, row_y + 18), empty, self._font(10, True), COLORS["muted"])
            self._draw_kit_spare_filler(image, (x1, row_y + 22, x2, y2), spec)
            return
        capacity = max(0, (y2 - row_y + 1) // KIT_SCOREBOARD_ROW_HEIGHT)
        shown = games[:capacity]
        self._draw_kit_spare_filler(image, (x1, row_y + len(shown) * KIT_SCOREBOARD_ROW_HEIGHT + 4, x2, y2), spec)
        slots = [SportsDashboard._kit_day_slot(event, now) for event in shown]
        for index, event in enumerate(shown):
            top = row_y + index * KIT_SCOREBOARD_ROW_HEIGHT
            previous = slots[index - 1] if index else None
            following = slots[index + 1] if index + 1 < len(slots) else None
            self._draw_kit_scoreboard_row(image, draw, (x1, top, x2, top + KIT_SCOREBOARD_ROW_HEIGHT - 1), event, now, spec,
                                          last=index == len(shown) - 1, slot=(previous, slots[index], following))

    def _draw_kit_scoreboard_row(self, image, draw, box, event, now, spec, last=False, slot=None, venue=False):
        x1, y1, x2, y2 = [int(value) for value in box]
        state = SportsDashboard._hub_event_state(event)
        state_x2 = x1 + KIT_SCOREBOARD_STATE_WIDTH
        if slot and slot[1]:
            self._draw_kit_day_slot(draw, (x1, y1, state_x2, y2), *slot, spec)
        else:
            top_label, bottom_label, top_key = spec.hook("state_labels", event, now, default=("", "", "text"))
            top_label, top_font = self._fit_text(draw, top_label, KIT_SCOREBOARD_STATE_WIDTH - 4, 10, bold=True, min_size=8)
            self._draw_text_in_box(draw, (x1 + 2, y1 + 1, state_x2, y1 + 12), top_label, top_font, COLORS[top_key])
            # Under today's kickoff its networks stand as their logos (CBS, FOX...), else in text.
            broadcast = str(event.get("broadcast") or "").strip()
            if bottom_label and not (bottom_label == broadcast and self._draw_kit_small_broadcast(
                    image, (x1 + 2, y1 + 13, state_x2, y1 + 24), broadcast, KIT_SCOREBOARD_TV_HEIGHT)):
                bottom_label, bottom_font = self._fit_text(draw, bottom_label, KIT_SCOREBOARD_STATE_WIDTH - 4, 9, bold=False, min_size=7)
                self._draw_text_in_box(draw, (x1 + 2, y1 + 13, state_x2, y1 + 24), bottom_label, bottom_font, COLORS["text"])
        if state == "live" and spec.hook("row_flag", event, default=False):
            draw.rectangle((state_x2 - 1, y1 + 3, state_x2 + 1, y2 - 3), fill=COLORS[spec.live])
        winner = spec.hook("winner_side", event, default="") if state == "final" else ""
        offense = spec.hook("possession_side", event, default="") if state == "live" else ""
        leader = SportsDashboard._wall_leader(event) if state == "live" else None
        score_right = x2 - 2 - KIT_LEAD_PAD
        team_x = state_x2 + 6
        if state == "scheduled":
            self._draw_kit_row_venue(image, draw, (team_x + 15, y1, score_right, y2), event, spec)
        elif venue:
            self._draw_kit_row_live_venue(draw, image, (team_x + 15, y1, score_right, y2), event, spec, leader, offense)
        for index, side in enumerate(("a", "b")):
            line_y1 = y1 + 1 + index * 12
            line_y2 = line_y1 + 12
            prefix = f"team_{side}"
            dimmed = bool(winner) and winner != side
            self._draw_team_logo(image, draw, event.get(f"{prefix}_logo"), team_x, line_y1, 11, spec.hook("logo_fallback", event, side, default=event.get(f"{prefix}_code")))
            score = spec.hook("row_score", event, side, default=event.get(f"wins_{side}")) if state != "scheduled" else None
            score_text = "" if score is None else str(score)
            score_text, score_font = self._fit_score(draw, score_text, 34, 11, bold=not dimmed,
                                                    sport=spec.sport, max_height=line_y2 - line_y1)
            score_w = self._text_width(draw, score_text, score_font) if score_text else 0
            name_x = team_x + 15
            name_budget = score_right - score_w - (KIT_LEAD_PAD if leader else 0) - 6 - name_x                 - (9 if offense == side else 0)
            name, font = self._kit_team_name(draw, spec, event, side, name_budget, 11, not dimmed)
            self._draw_text_in_box(draw, (name_x, line_y1, name_x + name_budget, line_y2), name, font, COLORS["text"])
            if offense == side:
                dot_x = name_x + self._text_width(draw, name, font) + 4
                draw.ellipse((dot_x, line_y1 + 4, dot_x + 5, line_y1 + 9), fill=COLORS["amber"], outline=COLORS["border"], width=1)
            if score_text:
                # A live game's leader sits in solid ink; a final's winner reads emerald, its loser crimson.
                ink = _kit_result_ink("win" if winner == side else "loss") if winner else COLORS["text"]
                if leader == side:
                    draw.rounded_rectangle((score_right - score_w - KIT_LEAD_PAD, line_y1 + 1,
                                            score_right + KIT_LEAD_PAD, line_y2 - 1), radius=3, fill=COLORS["text"])
                    ink = COLORS["paper_text"]
                self._draw_text_in_box(draw, (score_right - score_w, line_y1, score_right, line_y2), score_text,
                                       score_font, ink, align="right")
        if not last:
            draw.line((x1, y2, x2, y2), fill=COLORS["line"], width=1)

    def _draw_kit_row_venue(self, image, draw, box, event, spec):
        """A game yet to start shows its stadium's own logo at the row's right, in the room its
        scores will take, as long as both team names still fit there at full size."""
        names_x, y1, right, y2 = [int(value) for value in box]
        widest = max(self._text_width(draw, *self._kit_team_name(draw, spec, event, side, right - names_x, 11, True))
                     for side in ("a", "b"))
        room = right - (names_x + widest + KIT_ROW_VENUE_GAP)
        if room < KIT_ROW_VENUE_MIN_W:
            return
        logo = self._kit_venue_logo(event.get("venue"), (min(room, KIT_ROW_VENUE_MAX_W), y2 - y1 - 4))
        if logo is not None:
            image.paste(logo, (right - logo.width, y1 + (y2 - y1 - logo.height) // 2), logo)

    def _draw_kit_row_live_venue(self, draw, image, box, event, spec, leader, offense):
        """A game under way shows its venue's logo (else its sport's venue icon) centred in the room
        left between its longer team name, as the row sets it, and its score column; nothing when
        that room is narrower than KIT_ROW_VENUE_MIN_W, so no name is shortened for it."""
        names_x, y1, score_right, y2 = [int(value) for value in box]
        winner = spec.hook("winner_side", event, default="") if SportsDashboard._hub_event_state(event) == "final" else ""
        score_w = names_w = 0
        for side in ("a", "b"):
            # Each line as the row sets it: its own score's width, then its name in what is left.
            bold = not (winner and winner != side)
            score = spec.hook("row_score", event, side, default=event.get(f"wins_{side}"))
            text, font = self._fit_score(draw, "" if score is None else str(score), 34, 11, bold=bold, sport=spec.sport,
                                         max_height=12)
            width = self._text_width(draw, text, font) if text else 0
            score_w = max(score_w, width)
            budget = score_right - width - (KIT_LEAD_PAD if leader else 0) - 6 - names_x - (9 if offense == side else 0)
            name, font = self._kit_team_name(draw, spec, event, side, budget, 11, bold)
            names_w = max(names_w, self._text_width(draw, name, font) + (9 if offense == side else 0))
        left = names_x + names_w + KIT_ROW_VENUE_GAP
        right = score_right - score_w - (KIT_LEAD_PAD if leader else 0) - KIT_ROW_VENUE_GAP
        if right - left < KIT_ROW_VENUE_MIN_W:
            return None
        height = y2 - y1 - 4
        venue = str(event.get("venue") or "").strip()
        logo = self._kit_venue_logo(venue, (min(right - left, KIT_ROW_VENUE_MAX_W), height)) if venue else None
        if logo is None and venue:
            logo = self._kit_venue_icon(KIT_SPORT_VENUE_KINDS.get(spec.sport), min(height, KIT_VENUE_ICON_HEIGHT + 4))
        if logo is None or logo.width > right - left:
            return None
        x = left + (right - left - logo.width) // 2
        image.paste(logo, (x, y1 + (y2 - y1 - logo.height) // 2), logo)
        return x, logo.width

    def _draw_kit_day_slot(self, draw, box, previous, slot, following, spec):
        """An upcoming game on a later day as a timeline stop. The day's first
        game carries the day tab and its kickoff; later games that day hang off
        a rail with a dot and show their time only when it changes."""
        x1, y1, _x2, y2 = box
        day, time = slot
        accent = COLORS[spec.accent]
        rail_x, middle = x1 + KIT_DAY_RAIL_OFFSET, (y1 + y2) // 2
        continues = bool(following) and following[0] == day
        if previous and previous[0] == day:
            draw.line((rail_x, y1 - 1, rail_x, y2 if continues else middle), fill=accent, width=2)
            draw.ellipse((rail_x - 3, middle - 3, rail_x + 3, middle + 3), fill=accent, outline=COLORS["border"], width=1)
            if previous[1] != time:
                self._draw_kit_slot_time(draw, (rail_x + 6, middle - 6, x1 + KIT_SCOREBOARD_STATE_WIDTH, middle + 6), time)
            return
        self._draw_kit_day_tab(draw, x1, y1 + 1, day, accent)
        if continues:
            draw.line((rail_x, y1 + 12, rail_x, y2), fill=accent, width=2)
        self._draw_kit_slot_time(draw, (rail_x + 6, y1 + 13, x1 + KIT_SCOREBOARD_STATE_WIDTH, y1 + 24), time)

    def _draw_kit_ticker_day(self, draw, x, top, previous, slot, following, spec):
        """The one-line ticker's version of the day timeline: the day tab on the
        day's first row, a rail and dot on the rows after it, the time beside."""
        day, time = slot
        accent = COLORS[spec.accent]
        rail_x, bottom = x + KIT_DAY_RAIL_OFFSET, top + KIT_TICKER_ROW_HEIGHT - 1
        continues = bool(following) and following[0] == day
        if previous and previous[0] == day:
            draw.line((rail_x, top - 1, rail_x, bottom if continues else top + 7), fill=accent, width=2)
            draw.ellipse((rail_x - 3, top + 4, rail_x + 3, top + 10), fill=accent, outline=COLORS["border"], width=1)
        else:
            self._draw_kit_day_tab(draw, x, top + 2, day, accent)
            if continues:
                draw.line((rail_x, top + 13, rail_x, bottom), fill=accent, width=2)
        self._draw_kit_slot_time(draw, (x + KIT_DAY_TAB_WIDTH + 3, top, x + 58, top + 15), time)

    def _draw_kit_spare_filler(self, image, box, spec):
        """The sport's mascot spot art, centred in the room a short ticker or
        scoreboard leaves below its rows."""
        x1, y1, x2, y2 = [int(value) for value in box]
        sport = str(spec.sport or "").upper()
        path = LOCAL_SPARE_FILLER_PATHS.get(sport)
        if y2 - y1 < KIT_SPARE_FILLER_MIN_HEIGHT:
            # A short, wide band still takes the league's wide scene.
            path = LOCAL_WALL_SLOT_FILLER_PATHS.get(sport) if y2 - y1 >= KIT_SPARE_STRIP_MIN_HEIGHT else None
        if not path or not os.path.exists(path) or x2 - x1 < 80:
            return
        art = self._load_local_logo(path, (x2 - x1, min(y2 - y1, KIT_SPARE_FILLER_MAX_HEIGHT)), alpha_threshold=8)
        if art:
            image.paste(art, (x1 + (x2 - x1 - art.width) // 2, y1 + (y2 - y1 - art.height) // 2), art)

    def _draw_kit_day_tab(self, draw, x, y, day, fill):
        draw.rounded_rectangle((x, y, x + KIT_DAY_TAB_WIDTH, y + 10), radius=3, fill=fill, outline=COLORS["border"], width=1)
        self._draw_centered_in_box(draw, (x, y, x + KIT_DAY_TAB_WIDTH, y + 10), day, self._font(8, True), _kit_ink_on(fill))

    def _draw_kit_slot_time(self, draw, box, time):
        text, font = self._fit_text(draw, time, box[2] - box[0], 10, bold=True, min_size=8)
        self._draw_text_in_box(draw, box, text, font, COLORS["text"])

    def _kit_team_name(self, draw, spec, event, side, budget, size, bold, min_size=None):
        """The team's full name (新英格兰爱国者) when it fits ``budget`` at ``size`` or one
        point smaller, else its short name (爱国者) shrunk or clipped to fit.

        Both teams of a game switch together: a full name is used only when the
        opponent's full name fits the same room, so a matchup never mixes the two."""
        short = spec.hook("team_name", event, side, False, default=None) or event.get(f"team_{side}") or "TBD"
        full = spec.hook("team_name", event, side, True, default=None)
        other = spec.hook("team_name", event, "b" if side == "a" else "a", True, default=None)
        if full and full != short:
            for candidate in (size, size - 1):
                font = self._font(candidate, bold)
                if all(self._text_width(draw, name, font) <= budget for name in (full, other) if name):
                    return full, font
        return self._fit_text_ellipsis(draw, short, budget, size, bold=bold, min_size=min_size or max(7, size - 3))

    def _kit_team_pair_text(self, draw, spec, event, middle, budget, size, bold):
        """"A 3-1 B" with full names when the whole line fits, else with short names."""
        for full in (True, False):
            names = [spec.hook("team_name", event, side, full, default=None) or event.get(f"team_{side}") or "TBD"
                     for side in ("a", "b")]
            text = f"{names[0]} {middle} {names[1]}"
            if full and self._text_width(draw, text, self._font(size, bold)) > budget:
                continue
            return self._fit_text_ellipsis(draw, text, budget, size, bold=bold, min_size=max(7, size - 2))

    def _draw_kit_ticker(self, image, draw, box, games, now, spec, title=None):
        """Compact one-line rows for the other games: clock, teams, score."""
        x1, y1, x2, y2 = [int(value) for value in box]
        live_count = sum(1 for event in games if SportsDashboard._hub_event_state(event) == "live")
        if title is None:
            title = "TONIGHT" if any(SportsDashboard._hub_event_state(event) != "final" for event in games[:4]) else "RESULTS"
        y = self._draw_kit_column_header(draw, x1, y1, x2, spec, title, f"{live_count} LIVE" if live_count else "")
        if not games:
            self._draw_text_in_box(draw, (x1 + 4, y, x2, y + 14), f"No other {spec.sport} games", self._font(9, True), COLORS["muted"])
            self._draw_kit_spare_filler(image, (x1, y + 18, x2, y2), spec)
            return
        capacity = max(0, (y2 - y + 1) // KIT_TICKER_ROW_HEIGHT)
        slots = [SportsDashboard._kit_day_slot(event, now) for event in games[:capacity]]
        self._draw_kit_spare_filler(image, (x1, y + len(slots) * KIT_TICKER_ROW_HEIGHT + 4, x2, y2), spec)
        for index, event in enumerate(games[:capacity]):
            top = y + index * KIT_TICKER_ROW_HEIGHT
            state = SportsDashboard._hub_event_state(event)
            if slots[index]:
                following = slots[index + 1] if index + 1 < len(slots) else None
                self._draw_kit_ticker_day(draw, x1, top, slots[index - 1] if index else None, slots[index], following, spec)
            else:
                clock, key = SportsDashboard._kit_ticker_clock(event, now, spec)
                clock, clock_font = self._fit_text(draw, clock, 52, 9, bold=True, min_size=7)
                self._draw_text_in_box(draw, (x1, top, x1 + 54, top + 15), clock, clock_font, COLORS[key])
            if state == "live" and spec.hook("row_flag", event, default=False):
                draw.rectangle((x1 + 55, top + 3, x1 + 56, top + 12), fill=COLORS[spec.live])
            winner = spec.hook("winner_side", event, default="") if state == "final" else ""
            if state == "scheduled":
                middle_text = "vs"
            else:
                middle_text = f"{event.get('wins_a') if event.get('wins_a') is not None else '-'}-{event.get('wins_b') if event.get('wins_b') is not None else '-'}"
            middle_font = self._font(10, True)
            if state != "scheduled":
                middle_text, middle_font = self._fit_score(draw, middle_text, min(68, (x2 - x1) / 3) - KIT_RESULT_EXTRA, 10,
                                                          bold=True, sport=spec.sport, max_height=15)
            middle_w = self._kit_result_width(draw, middle_text, middle_font, winner)
            team_x1 = x1 + 60
            center = (team_x1 + x2) // 2
            self._draw_kit_result_score(draw, (center - middle_w // 2 - 1, top, center + middle_w // 2 + 2, top + 15),
                                        middle_text, middle_font, winner=winner)
            # Each team's logo sits on the outer side of its name.
            logo_room = KIT_TICKER_LOGO_SIZE + KIT_TICKER_LOGO_GAP
            for side, box_side, align in (
                ("a", (team_x1 + logo_room, top, center - middle_w // 2 - 5, top + 15), "right"),
                ("b", (center + middle_w // 2 + 5, top, x2 - logo_room, top + 15), "left"),
            ):
                text, font = self._kit_team_name(draw, spec, event, side, box_side[2] - box_side[0], 10,
                                                 winner in ("", side), min_size=8)
                self._draw_text_in_box(draw, box_side, text, font, COLORS["text"], align=align)
                text_w = self._text_width(draw, text, font)
                if side == "a":
                    logo_x = box_side[2] - text_w - KIT_TICKER_LOGO_GAP - KIT_TICKER_LOGO_SIZE
                else:
                    logo_x = box_side[0] + text_w + KIT_TICKER_LOGO_GAP
                fallback = spec.hook("logo_fallback", event, side, default=event.get(f"team_{side}_code"))
                self._draw_team_logo(image, draw, event.get(f"team_{side}_logo"), logo_x, top + 2, KIT_TICKER_LOGO_SIZE, fallback)
            if index < min(capacity, len(games)) - 1:
                draw.line((x1, top + KIT_TICKER_ROW_HEIGHT - 1, x2, top + KIT_TICKER_ROW_HEIGHT - 1), fill=COLORS["line"], width=1)

    # Shared labels ------------------------------------------------------------

    @staticmethod
    def _kit_day_slot(event, now):
        """('SUN', '10:00') for a game still to come on a later day, else None. Schedule columns
        give every start on the 24-hour clock, so the times line up as one column."""
        start = (event or {}).get("start")
        if SportsDashboard._hub_event_state(event) != "scheduled" or not isinstance(start, datetime) or start.date() == now.date():
            return None
        return start.strftime("%a").upper(), SportsDashboard._format_time_24h(start)

    @staticmethod
    def _kit_ticker_clock(event, now, spec):
        """One-line clock: 'Q4 2:31', 'FINAL', '16:00' today, 'TUE 18:30' later."""
        state = SportsDashboard._hub_event_state(event)
        top, bottom, key = spec.hook("state_labels", event, now)
        if state == "live":
            return f"{top} {bottom}".strip(), key
        if state == "final":
            return ("FINAL/OT" if bottom == "OT" else "FINAL"), key
        start = (event or {}).get("start")
        if not isinstance(start, datetime):
            return "TBD", key
        time_text = SportsDashboard._format_time_24h(start)
        if start.date() == now.date():
            return time_text, key
        return f"{start.strftime('%a').upper()} {time_text}", key


    @staticmethod
    def _kit_scoreboard_order(events, main=None):
        """Other games: live first, then upcoming by start, then finals newest first."""
        main = main or {}
        main_id = str(main.get("event_id") or "")
        events = [
            event for event in events or []
            if event is not main and not (main_id and str(event.get("event_id") or "") == main_id)
        ]

        def start_key(event):
            start = event.get("start")
            return start.timestamp() if isinstance(start, datetime) else float("inf")

        by_state = {"live": [], "scheduled": [], "final": []}
        for event in events:
            by_state.setdefault(SportsDashboard._hub_event_state(event), []).append(event)
        live = sorted(by_state["live"], key=lambda event: (start_key(event), str(event.get("event_id") or "")))
        upcoming = sorted(by_state["scheduled"], key=start_key)
        finals = sorted(by_state["final"], key=start_key, reverse=True)
        return live + upcoming + finals

    @staticmethod
    def _kit_start_label(start, now):
        """'TODAY 1:05 PM', 'MON 5:15 PM' within a week, else '10/27 11:25 AM'."""
        if not isinstance(start, datetime):
            return "NEXT"
        time_text = SportsDashboard._format_time(start)
        days = (start.date() - now.date()).days
        if days == 0:
            return f"TODAY {time_text}"
        if 0 < days < 7:
            return f"{start.strftime('%a').upper()} {time_text}"
        return f"{start.strftime('%m/%d')} {time_text}"

    @staticmethod
    def _kit_countdown_parts(value):
        """[('1', 'H'), ('40', 'M')] for a countdown label like 'IN 1H 40M', else []."""
        value = str(value or "")
        if not re.fullmatch(r"IN(?: \d+[DHM])+", value):
            return []
        return re.findall(r"(\d+)([DHM])", value)

    @staticmethod
    def _kit_countdown_label(start, now):
        if not isinstance(start, datetime):
            return "TBD"
        minutes = int((start - now).total_seconds() // 60)
        if minutes <= 0:
            return "SOON"
        if minutes < 60:
            return f"IN {minutes}M"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"IN {hours}H {minutes:02d}M"
        days, hours = divmod(hours, 24)
        return f"IN {days}D {hours}H"

    @staticmethod
    def _kit_schedule_state_labels(event, now, live_labels, live_key, final_bottom=None):
        """(top, bottom, colour key) for a scoreboard row's clock column."""
        event = event or {}
        state = SportsDashboard._hub_event_state(event)
        start = event.get("start")
        if state == "live":
            top, bottom = live_labels(event)
            return top, bottom, live_key
        if state == "final":
            bottom = final_bottom(event) if final_bottom else ""
            if not bottom and isinstance(start, datetime):
                bottom = start.strftime("%m/%d")
            return "FINAL", bottom, "text"
        if not isinstance(start, datetime):
            return "TBD", "", "text"
        time_text = SportsDashboard._format_time(start)
        if start.date() == now.date():
            return time_text, str(event.get("broadcast") or "").strip(), "text"
        return start.strftime("%a").upper(), time_text, "text"

    @staticmethod
    def _kit_clock_label(event, period_label):
        """Live clock for timed periods: 'Q2 2:00', 'HALFTIME', 'END Q1', 'OT 3:12'."""
        event = event or {}
        detail = str(event.get("status_detail") or "").strip().upper()
        period = SportsDashboard._lpl_int_value(event.get("period"))
        clock = str(event.get("clock") or "").strip()
        if "HALF" in detail:
            return "HALFTIME"
        if detail.startswith("END"):
            return f"END {period_label(period)}" if period else detail
        if period:
            label = period_label(period)
            return f"{label} {clock}" if clock else label
        return str(event.get("status_text") or "LIVE").strip().upper() or "LIVE"

    @staticmethod
    def _kit_quarter_label(period):
        period = int(period or 0)
        if period > 4:
            return "OT" if period == 5 else f"{period - 4}OT"
        return f"Q{period}" if period > 0 else ""

    @staticmethod
    def _kit_period_count(*lines, regulation=4, maximum=6):
        played = max((len(line or []) for line in lines), default=0)
        if not played:
            return 0
        return max(regulation, min(maximum, played))

    @staticmethod
    def _kit_spread_for_side(event, side):
        """The point spread from this team's side, e.g. "-12.5" / "+12.5"."""
        event = event or {}
        match = re.fullmatch(r"\s*([A-Za-z]{2,4})\s+([+-]?\d+(?:\.\d+)?)\s*", str(event.get("spread") or ""))
        if not match:
            return ""
        code = match.group(1).upper()
        value = float(match.group(2))
        codes = {
            "a": str(event.get("team_a_code") or "").upper(),
            "b": str(event.get("team_b_code") or "").upper(),
        }
        if code not in codes.values():
            return ""
        own = value if codes[side] == code else -value
        text = f"{own:+.1f}".replace(".0", "")
        return "PK" if own == 0 else text

    @staticmethod
    def _kit_venue_label(event, include_tv=True):
        event = event or {}
        parts = []
        venue = str(event.get("venue") or "").strip()
        city = str(event.get("city") or "").strip()
        if venue:
            parts.append(venue)
        if city and city not in venue:
            parts.append(city)
        broadcast = str(event.get("broadcast") or "").strip()
        if include_tv and broadcast:
            parts.append(f"TV {broadcast}")
        return "  ·  ".join(parts)

    @staticmethod
    def _kit_team_ink_key(color, fallback):
        color = str(color or "").strip().lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", color):
            return fallback
        rgb = tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))
        return min(
            KIT_TEAM_INK_KEYS,
            key=lambda key: sum((channel - ink) ** 2 for channel, ink in zip(rgb, DAY_COLORS[key])),
        )

    @staticmethod
    def _kit_ink_text_key(ink_key):
        """Text colour readable on a solid ink block (judged on the day palette)."""
        red, green, blue = DAY_COLORS.get(ink_key, DAY_COLORS["text"])[:3]
        return "text" if 0.299 * red + 0.587 * green + 0.114 * blue > 150 else "paper_text"
