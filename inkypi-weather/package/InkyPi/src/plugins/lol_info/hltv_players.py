"""HLTV pages the pro tracker reads for CS2 players.

The device reaches HLTV's Top 20 page, its search JSON and player pages directly
(the stats ranking pages sit behind a challenge, so they are not used). Parsers are
plain functions over the fetched text, so they can be tested without the network.
"""

from __future__ import annotations

from html import unescape
import json
import re

HLTV = "https://www.hltv.org"
USER_AGENT = "EpaperSystem/ProTracker"
PLAYER_PAGE_MAX_BYTES = 4 * 1024 * 1024
ROLE_NAMES = ("Firepower", "Entrying", "Trading", "Opening", "Closing", "Sniping", "Utility")
ROLE_LABELS = {"Firepower": "火力", "Entrying": "突破", "Trading": "补枪", "Opening": "首杀",
               "Closing": "残局", "Sniping": "狙击", "Utility": "道具"}

_TAG = re.compile(r"<[^>]+>")


def _text(value):
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", str(value or "")))).strip()


def _image_url(tag):
    match = re.search(r'src="([^"]+)"', tag)
    return unescape(match.group(1)) if match else ""


def parse_top20(page):
    """[{rank, nick, name, country, team, team_logo, bodyshot}] from a /players/top20/<year> page."""
    rows = []
    for row in re.findall(r"<tr>(.*?)</tr>", str(page or ""), flags=re.S):
        nick = re.search(r'class="top20-year-playernick">([^<]+)<', row)
        rank = re.search(r'class="top20-th-position"><b>#(\d+)</b>', row)
        if not nick or not rank:
            continue
        name = re.search(r'class="top20-year-playername text-ellipsis">(.*?)</div>', row, flags=re.S)
        country = re.search(r'/flags/30x20/([A-Z]{2})\.gif', row)
        team = re.search(r'<img alt="([^"]+)" src="([^"]+)" class="top20-year-teamlogo(?: night-only)?(?: day-only)?"', row)
        bodyshot = re.search(r'<img[^>]+class="top20-table-img"[^>]*>', row)
        rows.append({
            "rank": int(rank.group(1)),
            "nick": unescape(nick.group(1)).strip(),
            "name": _text(name.group(1)) if name else "",
            "country": country.group(1) if country else "",
            "team": unescape(team.group(1)) if team else "",
            "team_logo": unescape(team.group(2)) if team else "",
            "bodyshot": _image_url(bodyshot.group(0)) if bodyshot else "",
        })
    rows.sort(key=lambda item: item["rank"])
    return rows


def parse_search(payload, nick):
    """The search hit whose nickname is exactly ``nick``: id, page path, picture and current team."""
    try:
        groups = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    except ValueError:
        return None
    for group in groups if isinstance(groups, list) else []:
        for player in (group or {}).get("players") or []:
            if str(player.get("nickName") or "").casefold() != str(nick).casefold():
                continue
            team = player.get("team") or {}
            return {
                "id": player.get("id"),
                "location": player.get("location") or "",
                "picture": player.get("pictureUrl") or "",
                "team": team.get("name") or "",
                "team_logo_day": team.get("teamLogoDay") or "",
                "team_logo_night": team.get("teamLogoNight") or "",
            }
    return None


def parse_player_page(page):
    """Rating 3.0 with its window, the seven role scores, team, age, bodyshot and matches."""
    page = str(page or "")
    result = {"rating": None, "window": "", "maps": None, "roles": {}, "team": "", "team_logo_day": "",
              "team_logo_night": "", "age": "", "country": "", "bodyshot": "", "upcoming": [], "recent": []}
    window = re.search(r'class="stats-window">\(([^)]*)\)', page)
    if window:
        result["window"] = _text(window.group(1))
        maps = re.search(r"(\d+)\s*maps", result["window"])
        result["maps"] = int(maps.group(1)) if maps else None
    rating = re.search(r"<b>Rating 3\.0</b>\s*<span class=\"statsVal\"><p>([\d.]+)</p>", page)
    if rating:
        result["rating"] = float(rating.group(1))
    for role in ROLE_NAMES:
        score = re.search(rf"<b>{role}</b>\s*<span class=\"statsVal\"><p><b>(\d+)</b>", page)
        if score:
            result["roles"][role] = int(score.group(1))
    team = re.search(r'playerTeam".*?<img alt="([^"]+)" src="([^"]+)" class="day-only".*?'
                     r'<img alt="[^"]+" src="([^"]+)" class="night-only"', page, flags=re.S)
    if team:
        result["team"], result["team_logo_day"], result["team_logo_night"] = (
            unescape(team.group(1)), unescape(team.group(2)), unescape(team.group(3)))
    age = re.search(r'playerAge".*?<span itemprop="text">([^<]+)</span>', page, flags=re.S)
    if age:
        result["age"] = _text(age.group(1))
    real_name = page.find("playerRealname")
    country = re.search(r'/flags/30x20/([A-Z]{2})\.gif', page[real_name:real_name + 600]) if real_name > 0 else None
    if country:
        result["country"] = country.group(1)
    bodyshot = re.search(r'<img[^>]+class="player-summary-stat-box-left-bodyshot"[^>]*>', page)
    if bodyshot:
        result["bodyshot"] = _image_url(bodyshot.group(0))
    for box in re.findall(r'<a href="[^"]*" class="playerpage-matchbox a-reset([^"]*)">(.*?)</a>', page, flags=re.S):
        classes, body = box
        opponent = re.search(r'<img alt="([^"]+)" src="([^"]+)" class="team-logo"', body)
        event = re.search(r'class="playerpage-matchbox-bottom[^"]*">(.*?)</div>', body, flags=re.S)
        entry = {"opponent": unescape(opponent.group(1)) if opponent else "",
                 "opponent_logo": unescape(opponent.group(2)) if opponent else "",
                 "event": _text(event.group(1)) if event else ""}
        score = re.search(r'class="playerpage-match-result">([^<]+)<', body)
        when = re.search(r'data-unix="(\d+)"', body)
        if score:
            entry["score"] = _text(score.group(1))
            entry["won"] = "lost-matchbox" not in classes and _won(entry["score"])
            result["recent"].append(entry)
        elif when:
            entry["unix_ms"] = int(when.group(1))
            result["upcoming"].append(entry)
    return result


def _won(score):
    parts = re.findall(r"\d+", score or "")
    return len(parts) == 2 and int(parts[0]) > int(parts[1])


def fetch(session, path, timeout=(6, 30)):
    """GET an HLTV page or JSON as text (raises on HTTP errors or an oversized body)."""
    response = session.get(path if path.startswith("http") else HLTV + path, timeout=timeout,
                           headers={"User-Agent": USER_AGENT}, stream=True)
    response.raise_for_status()
    body = bytearray()
    for chunk in response.iter_content(64 * 1024):
        body.extend(chunk)
        if len(body) > PLAYER_PAGE_MAX_BYTES:
            raise ValueError(f"HLTV page too large: {path}")
    return body.decode(response.encoding or "utf-8", errors="replace")
