"""The teams and touring shows Local Events' titles name, and their official logos.

The logos and the names Ticketmaster writes for each are in assets/teams
(tools/build_event_team_logos.py). A title is read as a game ('Golden State Warriors vs. Phoenix
Suns', 'Stanford Cardinal Womens Volleyball vs. Cal') or as a show one of them leads ('Harlem
Globetrotters 2026 World Tour'): each side's longest known name at its start, among the leagues of
the event's sport. A school is also known by its place alone ('vs. Oregon State') in a college game,
where the place names one school. The sport after a school ('Mens Basketball') is said once, in
Chinese (男篮). A title naming no team is left as it is.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image

from utils.safe_image import safe_open_image
from utils.theme_utils import contrast_ratio, relative_luminance

TEAM_DIR = Path(__file__).with_name("assets") / "teams"
# The leagues a game of each Ticketmaster sub-genre is played in; the shows go with any.
LEAGUE_HINTS = {"nba": "nba", "wnba": "wnba", "nhl": "nhl", "nfl": "nfl", "mlb": "mlb", "mls": "mls",
                "nwsl": "nwsl", "usl": "usl", "ahl": "ahl", "college": "ncaa", "ncaa": "ncaa"}
# Where one name is a team in two leagues, the professional one first.
LEAGUE_ORDER = ("nba", "wnba", "nhl", "nfl", "mlb", "mls", "nwsl", "usl", "ahl", "pioneer", "ncaa", "show")
GAME = re.compile(r"\s+(?:vs\.?|versus|v\.)\s+|\s+(?:at|@)\s+", re.IGNORECASE)
TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
# A team's sport, written after it: 'Mens Basketball', "Women's Volleyball", 'Baseball'. A side whose
# team has no logo (a Division II school) still has it cut off its end ('Adams State Grizzlies Mens
# Basketball').
SPORT_NAMES = (r"(?:(men|women)(?:'?s)?\s+)?(beach volleyball|basketball|volleyball|soccer|baseball|softball|"
               r"football|water polo|wrestling|gymnastics|lacrosse|field hockey|hockey|tennis)")
SPORT = re.compile(rf"^{SPORT_NAMES}\b\s*", re.IGNORECASE)
TRAILING_SPORT = re.compile(rf"\s+(?={SPORT_NAMES}$)", re.IGNORECASE)
SPORT_ZH = {"basketball": ("篮球", "篮"), "volleyball": ("排球", "排"), "soccer": ("足球", "足"),
            "beach volleyball": ("沙滩排球", ""), "baseball": ("棒球", ""), "softball": ("垒球", ""),
            "football": ("橄榄球", ""), "water polo": ("水球", ""), "wrestling": ("摔跤", ""),
            "gymnastics": ("体操", ""), "lacrosse": ("长曲棍球", ""), "field hockey": ("曲棍球", ""),
            "hockey": ("冰球", ""), "tennis": ("网球", "")}
# A logo's dark parts that would vanish on the night page take its ink, as the stickers' outlines do.
NIGHT_OUTLINE_LUMINANCE, NIGHT_MIN_CONTRAST = 0.18, 2.0


@dataclass(frozen=True)
class Team:
    key: str
    league: str
    short: str
    place: str
    path: Path
    dark_path: Path | None


@dataclass(frozen=True)
class Side:
    """One side of a game (or the show's lead): the team as the title writes it, then the rest."""
    team: Team | None
    text: str
    rest: str = ""


@dataclass(frozen=True)
class TitleTeams:
    """A title read for its teams: what comes before them, the sides (two for a game), the word
    between them, what follows, and the sport in Chinese ("" when the title does not say it)."""
    prefix: str
    sides: tuple
    joiner: str = ""
    trailing: str = ""
    sport: str = ""

    @property
    def is_game(self):
        return len(self.sides) == 2


def _tokens(text):
    """Each word's plain form (lower case, no apostrophes) and where it ends in the text, which is
    returned without its accents (Montréal -> Montreal)."""
    folded = "".join(char for char in unicodedata.normalize("NFKD", str(text or "").replace("’", "'"))
                     if not unicodedata.combining(char))
    words = []
    for match in TOKEN.finditer(folded):
        plain = match.group().encode("ascii", "ignore").decode().replace("'", "").lower()
        if plain:
            words.append((plain, match.end()))
    return words, folded


def _key(text):
    return " ".join(word for word, _end in _tokens(text)[0])


@lru_cache(maxsize=1)
def _index():
    """{league: ({name: team}, {place: team or None when two share it}, sports)}."""
    try:
        teams = json.loads((TEAM_DIR / "teams.source.json").read_text(encoding="utf-8"))["teams"]
    except (OSError, ValueError, KeyError):
        teams = {}
    index = {}
    for key, entry in teams.items():
        league = entry.get("league") or key.split("/", 1)[0]
        folder, slug = TEAM_DIR / league, key.split("/", 1)[1]
        dark = folder / f"{slug}-dark.png"
        team = Team(key, league, entry.get("short") or "", next(iter(entry.get("places") or ()), ""),
                    folder / f"{slug}.png", dark if entry.get("dark") else None)
        names, places, sports = index.setdefault(league, ({}, {}, set()))
        sports.update(entry.get("sports") or ())
        for name in entry.get("names") or ():
            names.setdefault(_key(name), team)
        for place in entry.get("places") or ():
            place = _key(place)
            places[place] = None if place in places and places[place] != team else team
    return index


def _leagues(segment, genre, subgenre):
    """The leagues an event's teams may come from, most likely first."""
    index = _index()
    hint = LEAGUE_HINTS.get((subgenre or "").strip().casefold())
    if (segment or "").strip().casefold() != "sports":
        return ["show"]
    if hint:
        return [hint, "show"]
    genre = (genre or "").strip().casefold()
    named = [league for league in LEAGUE_ORDER if league in index and genre in index[league][2]]
    # A school plays every sport, and a sport no league names may be any league's.
    return named + [league for league in LEAGUE_ORDER if league in index and league not in named
                    and (not index[league][2] or not named)]


def _match(text, leagues, places=False, starts=1):
    """(team, prefix, team text, rest) for the longest team name opening ``text`` (or after at most
    ``starts`` - 1 leading words), by name, or in a college game by place; None for no team."""
    words, folded = _tokens(text)
    index = _index()
    for start in range(min(starts, len(words))):
        begin = 0 if start == 0 else words[start - 1][1]
        for end in range(len(words), start, -1):
            candidate = " ".join(word for word, _end in words[start:end])
            for league in leagues:
                names, by_place, _sports = index.get(league, ({}, {}, set()))
                team = names.get(candidate) or (by_place.get(candidate) if places and league == "ncaa" else None)
                if team is not None:
                    stop = words[end - 1][1]
                    prefix = folded[:begin].strip(" -–—:|")
                    return team, prefix, folded[begin:stop].strip(" -–—:|"), folded[stop:].strip()
    return None


def _unknown_side(text):
    """(name, sport) of a side naming no known team: 'Adams State Grizzlies', 'Mens Basketball'."""
    text = text.strip()
    match = TRAILING_SPORT.search(text)
    return (text[:match.start()].strip(), text[match.end():]) if match else (text, "")


def _sport(rest):
    """(Chinese sport, what follows it) from a side's rest ('Mens Basketball' -> 男篮)."""
    match = SPORT.match(rest)
    if not match:
        return "", rest
    gender, sport = (match.group(1) or "").lower(), match.group(2).lower()
    whole, short = SPORT_ZH[sport]
    label = (("男" if gender == "men" else "女") + (short or ("子" + whole))) if gender else whole
    return label, rest[match.end():].strip(" -–—:|")


@lru_cache(maxsize=256)
def read_title(title, segment="", genre="", subgenre=""):
    """The title's teams (TitleTeams), or None when it names none."""
    title = str(title or "").strip()
    leagues = _leagues(segment, genre, subgenre)
    college = leagues[0] == "ncaa"
    for separator in GAME.finditer(title):
        home, away = title[:separator.start()], title[separator.end():]
        # A few words may lead the game ('Preseason: ...'); the other side is in the same league,
        # and in a college game either school may be written by its place.
        first = _match(home, leagues, places=college, starts=4)
        second = _match(away, [first[0].league] if first else leagues,
                        places=college or bool(first and first[0].league == "ncaa"))
        if first is None and second is not None and second[0].league == "ncaa":
            first = _match(home, ["ncaa"], places=True, starts=4)
        if first is None and second is None:
            continue
        first = first or (None, "", *_unknown_side(home))
        second = second or (None, "", *_unknown_side(away))
        sport_a, rest_a = _sport(first[3])
        sport_b, rest_b = _sport(second[3])
        sides = (Side(first[0], first[2], rest_a), Side(second[0], second[2]))
        return TitleTeams(first[1], sides, separator.group().strip(), rest_b, sport_a or sport_b)
    found = _match(title, leagues)
    if found is None:
        return None
    team, _prefix, text, rest = found
    sport, rest = _sport(rest)
    return TitleTeams("", (Side(team, text, rest),), sport=sport)


@lru_cache(maxsize=128)
def _art(path):
    with safe_open_image(path) as source:
        return source.convert("RGBA")


@lru_cache(maxsize=128)
def team_logo(team, box, night, ground, ink):
    """The team's logo contained in ``box`` (width, height): its dark-background version on the
    night page where ESPN draws one, there its dark parts that would vanish on ``ground`` in
    ``ink``; None without the file."""
    path = team.dark_path if night and team.dark_path is not None and team.dark_path.is_file() else team.path
    try:
        art = _art(path)
    except (OSError, ValueError):
        return None
    scale = min(box[0] / art.width, box[1] / art.height)
    logo = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.Resampling.LANCZOS)
    if night:
        pixels = logo.load()
        for y in range(logo.height):
            for x in range(logo.width):
                red, green, blue, alpha = pixels[x, y]
                colour = (red, green, blue)
                if (alpha >= 8 and relative_luminance(colour) < NIGHT_OUTLINE_LUMINANCE
                        and contrast_ratio(colour, ground) < NIGHT_MIN_CONTRAST):
                    pixels[x, y] = (*ink, alpha)
    return logo
