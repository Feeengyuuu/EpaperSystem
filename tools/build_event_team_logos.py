"""Fetch the official logos of the teams and touring shows Local Events names in its titles.

    python tools/build_event_team_logos.py [league ...]

- The teams: each league's list from ESPN's site API (the NBA, WNBA, NHL, NFL, MLB, MLS, NWSL, the
  USL Championship, and every Division I school from the men's college basketball list: a school's
  logo is the same in every sport), with its logo and, where ESPN draws a different one for dark
  backgrounds, that one too for the night page.
- The shows and teams ESPN does not list (ORGS): the logo Wikidata names on Commons, else the one the
  organisation's own site serves.

Each logo is cropped to its ink and kept at most 48 px on its longer side in 256 colours, in
src/plugins/ticketmaster_events/assets/teams/<league>/<slug>.png (<slug>-dark.png for the night one);
teams.source.json lists each one's names as Ticketmaster writes them, its short name, its source and
its hash. Naming a league rebuilds only that league's logos.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
TEAMS = ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "ticketmaster_events" / "assets" / "teams"
MANIFEST = TEAMS / "teams.source.json"
HEADERS = {"User-Agent": "Mozilla/5.0 InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/teams?limit=1000"
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width=400"
SIDE = 48
# league key: (ESPN path, sports it plays, in Ticketmaster's genre names).
LEAGUES = {
    "nba": ("basketball/nba", ("basketball",)),
    "wnba": ("basketball/wnba", ("basketball",)),
    "nhl": ("hockey/nhl", ("hockey",)),
    "nfl": ("football/nfl", ("football",)),
    "mlb": ("baseball/mlb", ("baseball",)),
    "mls": ("soccer/usa.1", ("soccer",)),
    "nwsl": ("soccer/usa.nwsl", ("soccer",)),
    "usl": ("soccer/usa.usl.1", ("soccer",)),
    "ncaa": ("basketball/mens-college-basketball", ()),
}
# Names Ticketmaster gives a team beside ESPN's (the schools by their short names).
EXTRA_NAMES = {"ncaa/california-golden-bears": ("Cal", "Cal Bears", "Cal Golden Bears"),
               "ncaa/san-jose-state-spartans": ("SJSU", "San Jose State"),
               "ncaa/stanford-cardinal": ("Stanford",)}
# The touring shows, leagues and teams ESPN does not list: key -> (league, names, short, sports, source).
ORGS = {
    "harlem-globetrotters": ("show", ("Harlem Globetrotters",), "Globetrotters", ("basketball",),
                             ("commons", "Harlem Globetrotters logo.png")),
    "wwe": ("show", ("WWE", "WWE Monday Night RAW", "WWE Friday Night SmackDown"), "WWE", ("wrestling",),
            ("commons", "WWE black logo.svg")),
    "aew": ("show", ("AEW", "All Elite Wrestling"), "AEW", ("wrestling",), ("commons", "All Elite Wrestling logo 2023.png")),
    "ufc": ("show", ("UFC",), "UFC", ("mixed martial arts", "martial arts"), ("commons", "UFC Logo.svg")),
    "pbr": ("show", ("PBR", "Professional Bull Riders"), "PBR", ("rodeo",),
            ("site", "https://www.pbr.com/media/wqpe010m/pbrlogo.png")),
    "blue-man-group": ("show", ("Blue Man Group",), "Blue Man Group", (), ("commons", "Blue Man Group.svg")),
    "disney-on-ice": ("show", ("Disney On Ice",), "Disney On Ice", (),
                      ("site", "https://www.disneyonice.com/wp-content/uploads/2024/01/DOI-logo.png")),
    "san-jose-barracuda": ("ahl", ("San Jose Barracuda",), "Barracuda", ("hockey",),
                           ("site", "https://d7ejvexv8z1z7.cloudfront.net/imager/generalimages/General-Website-Assets/57/"
                                    "SJ-Barracuda-Logo_0a62e460fdac3c2d7790c3ac8d36d6e3.png")),
    "oakland-ballers": ("pioneer", ("Oakland Ballers",), "Ballers", ("baseball",), ("commons", "Oakland Ballers insignia.svg")),
}


def _fetch(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.content


def _plain(text):
    """The name as written, its accents dropped (San José -> San Jose)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _fit(content):
    """The logo cropped to its ink, at most SIDE px on its longer side."""
    with Image.open(io.BytesIO(content)) as source:
        art = source.convert("RGBA")
    box = art.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox()
    art = art.crop(box) if box else art
    art.thumbnail((SIDE, SIDE), Image.Resampling.LANCZOS)
    return art


def _write(folder, name, art):
    """The logo saved in 256 colours with their transparency (half the size, alike at this size)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.png"
    art.quantize(256, method=Image.Quantize.FASTOCTREE).save(target, optimize=True)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _differs(day, night):
    if day.size != night.size:
        return True
    return ImageChops.difference(day, night).getbbox() is not None


def _logo(team, rel):
    for logo in team.get("logos") or ():
        rels = logo.get("rel") or ()
        if rel in rels and "scoreboard" not in rels:
            return logo.get("href") or ""
    return ""


def _espn_league(league, records):
    path, sports = LEAGUES[league]
    data = json.loads(_fetch(ESPN.format(path=path)))
    folder = TEAMS / league
    for stale in folder.glob("*.png"):
        stale.unlink()
    for row in data["sports"][0]["leagues"][0]["teams"]:
        team = row["team"]
        slug = team["slug"]
        day_url, night_url = _logo(team, "default"), _logo(team, "dark")
        if not day_url:
            continue
        day = _fit(_fetch(day_url))
        entry = {"league": league, "sports": list(sports),
                 "names": sorted({_plain(team["displayName"]), *EXTRA_NAMES.get(f"{league}/{slug}", ())}),
                 "short": _plain(team.get("shortDisplayName") or team["name"]),
                 # A school is also written by its place ('vs. Oregon State'), a pro team never alone.
                 "places": [_plain(team["location"])] if league == "ncaa" else [],
                 "source": day_url, "size": list(day.size), "sha256": _write(folder, slug, day)}
        if night_url:
            night = _fit(_fetch(night_url))
            if _differs(day, night):
                entry["dark"] = {"source": night_url, "sha256": _write(folder, f"{slug}-dark", night)}
        records[f"{league}/{slug}"] = entry
        print(league, slug, day.size, "dark" if "dark" in entry else "")


def _orgs(records):
    for slug, (league, names, short, sports, (kind, source)) in ORGS.items():
        url = COMMONS.format(name=quote(source)) if kind == "commons" else source
        art = _fit(_fetch(url))
        records[f"{league}/{slug}"] = {"league": league, "sports": list(sports), "names": list(names), "short": short,
                                       "places": [], "source": url, "size": list(art.size),
                                       "sha256": _write(TEAMS / league, slug, art)}
        print(league, slug, art.size)


def main(leagues=()):
    leagues = [league for league in (leagues or [*LEAGUES, "orgs"])]
    try:
        records = json.loads(MANIFEST.read_text(encoding="utf-8"))["teams"]
    except (OSError, ValueError, KeyError):
        records = {}
    for league in leagues:
        if league == "orgs":
            records = {key: entry for key, entry in records.items() if key.split("/", 1)[1] not in ORGS}
            _orgs(records)
        elif league in LEAGUES:
            records = {key: entry for key, entry in records.items() if entry["league"] != league}
            _espn_league(league, records)
        else:
            raise SystemExit(f"unknown league {league!r}: one of {', '.join([*LEAGUES, 'orgs'])}")
    manifest = {"source": "ESPN's site API team lists (site.api.espn.com) and their a.espncdn.com logos; ORGS from "
                          "Wikimedia Commons (the logo Wikidata names) or the organisation's own site",
                "licence": "each team's or show's own logo, shown to name it",
                "processing": f"cropped to its ink, at most {SIDE} px on its longer side, 256 colours; -dark is ESPN's logo for dark "
                              "backgrounds, kept where it differs",
                "teams": dict(sorted(records.items()))}
    text = json.dumps(manifest, ensure_ascii=False, indent=1) + "\n"
    MANIFEST.write_bytes(re.sub(r"\r\n", "\n", text).encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1:])
