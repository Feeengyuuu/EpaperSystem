"""Competition marks for the lower-left panels, bundled locally.

Postseason and finals logos (ALCS, NLCS, World Series, WNBA Finals, NBA
Cup), PGA TOUR event logos and F1 circuit outlines live under
assets/logos/events (events_manifest.json records each source). Marks whose
artwork carries a year are looked up by edition, so a 2026 logo never lands
on a 2027 event; an event without a mark simply draws none.
"""

from .common import *

SportsDashboard = None

EVENT_MARK_DIR = os.path.join(LOCAL_TEAM_LOGO_DIR, "events")
MLB_SERIES_MARKS = {
    (2026, "ALCS"): "mlb/2026_alcs.png",
    (2026, "NLCS"): "mlb/2026_nlcs.png",
    (2026, "WORLD SERIES"): "mlb/2026_world_series.png",
}
WNBA_FINALS_MARKS = {2026: "wnba/2026_finals.png"}
NBA_CUP_MARK = "nba/nba_cup.png"
# (keyword in the ESPN event name, asset) by season; first match wins, so
# "mexico" precedes the generic "open" names.
PGA_EVENT_MARKS = {
    2026: (
        ("baycurrent", "pga/2026_baycurrent.png"),
        ("bermuda", "pga/2026_bermuda.png"),
        ("mexico", "pga/2026_mexico_open.png"),
        ("world wide technology", "pga/2026_world_wide_technology.png"),
        ("austin", "pga/2026_austin.png"),
        ("rsm", "pga/2026_rsm.png"),
        ("procore", "pga/2026_procore.png"),
        ("sanderson", "pga/2026_sanderson_farms.png"),
        ("shriners", "pga/2026_shriners.png"),
        ("isco", "pga/2026_isco.png"),
        ("masters", "pga/2026_masters.png"),
        ("pga championship", "pga/2026_pga_championship.png"),
        ("u.s. open", "pga/2026_us_open.png"),
        ("us open", "pga/2026_us_open.png"),
        ("open championship", "pga/2026_the_open.png"),
        ("the open", "pga/2026_the_open.png"),
        ("players", "pga/2026_players.png"),
    ),
}


# Jolpica's Location.country -> ISO code of the bundled host flag (F1's own
# race cards identify each Grand Prix by its host flag).
F1_COUNTRY_FLAGS = {
    "australia": "au", "china": "cn", "japan": "jp", "usa": "us", "united states": "us",
    "canada": "ca", "monaco": "mc", "spain": "es", "austria": "at", "uk": "gb",
    "united kingdom": "gb", "great britain": "gb", "belgium": "be", "hungary": "hu",
    "netherlands": "nl", "italy": "it", "azerbaijan": "az", "malaysia": "my",
    "singapore": "sg", "mexico": "mx", "brazil": "br", "qatar": "qa", "uae": "ae",
    "united arab emirates": "ae", "bahrain": "bh", "saudi arabia": "sa",
}


class EventMarksMixin:
    # Lookups -------------------------------------------------------------------

    @staticmethod
    def _event_mark_file(relative):
        if not relative:
            return None
        path = os.path.join(EVENT_MARK_DIR, relative)
        return path if os.path.exists(path) else None

    @staticmethod
    def _event_mark_year(event):
        start = (event or {}).get("start")
        return start.astimezone(timezone.utc).year if isinstance(start, datetime) else None

    @staticmethod
    def _mlb_series_mark_path(event):
        series = str((event or {}).get("series_game") or "").split(" Game")[0].strip().upper()
        return SportsDashboard._event_mark_file(MLB_SERIES_MARKS.get((SportsDashboard._event_mark_year(event), series)))

    @staticmethod
    def _wnba_series_mark_path(event):
        headline = str((event or {}).get("headline") or "").upper()
        if not re.search(r"(?<![A-Z])FINALS", headline):  # not "Semifinals"
            return None
        return SportsDashboard._event_mark_file(WNBA_FINALS_MARKS.get(SportsDashboard._event_mark_year(event)))

    @staticmethod
    def _nba_event_mark_path(event):
        headline = str((event or {}).get("headline") or "").upper()
        return SportsDashboard._event_mark_file(NBA_CUP_MARK) if "NBA CUP" in headline else None

    @staticmethod
    def _pga_event_mark_path(event):
        name = str((event or {}).get("name_en") or (event or {}).get("name") or "").lower()
        for keyword, relative in PGA_EVENT_MARKS.get(SportsDashboard._event_mark_year(event), ()):
            if keyword in name:
                return SportsDashboard._event_mark_file(relative)
        return None

    @staticmethod
    def _f1_circuit_mark_path(race):
        circuit = re.sub(r"[^a-z0-9_]", "", str((race or {}).get("circuit_id") or "").lower())
        return SportsDashboard._event_mark_file(f"f1/{circuit}.png") if circuit else None

    @staticmethod
    def _f1_country_flag_path(race):
        iso = F1_COUNTRY_FLAGS.get(str((race or {}).get("country") or "").strip().lower())
        return SportsDashboard._event_mark_file(f"f1/flags/{iso}.png") if iso else None

    # Drawing -------------------------------------------------------------------

    def _draw_event_mark(self, image, path, box, align="center", line_art=False):
        """Fit a mark into box; returns the drawn box or None.

        Line art (circuit outlines) is re-inked in the theme's ink and panel
        colours. Colour logos get a light plate on dark themes so their dark
        parts stay visible.
        """
        if not path:
            return None
        x1, y1, x2, y2 = [int(value) for value in box]
        mark = self._load_local_logo(path, (max(1, x2 - x1), max(1, y2 - y1)), alpha_threshold=8)
        if not mark:
            return None
        if line_art:
            mark = SportsDashboard._event_mark_reinked(mark)
        if align == "left":
            left = x1
        elif align == "right":
            left = x2 - mark.width
        else:
            left = x1 + (x2 - x1 - mark.width) // 2
        top = y1 + (y2 - y1 - mark.height) // 2
        if not line_art and SportsDashboard._event_mark_dark_theme():
            ImageDraw.Draw(image).rounded_rectangle(
                (left - 3, top - 3, left + mark.width + 2, top + mark.height + 2), radius=4, fill=COLORS["text"]
            )
        image.paste(mark, (left, top), mark)
        return left, top, left + mark.width, top + mark.height

    @staticmethod
    def _event_mark_reinked(mark):
        ink = Image.new("RGBA", mark.size, tuple(COLORS["text"][:3]) + (255,))
        paper = Image.new("RGBA", mark.size, tuple(COLORS["panel"][:3]) + (255,))
        dark = mark.convert("L").point(lambda value: 255 if value < 128 else 0)
        result = Image.composite(ink, paper, dark)
        result.putalpha(mark.getchannel("A").point(lambda value: 255 if value >= 128 else 0))
        return result

    @staticmethod
    def _event_mark_dark_theme():
        red, green, blue = COLORS["panel"][:3]
        return 0.299 * red + 0.587 * green + 0.114 * blue < 96
