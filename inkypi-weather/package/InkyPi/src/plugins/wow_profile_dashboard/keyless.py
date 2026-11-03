"""Keyless WoW sources for the dashboard: Raider.IO's character profile (Mythic+ score, best runs, raid
progression and gear, standing in when Battle.net keys are missing or fail), this week's Mythic+
affixes from Raider.IO, and the WoW Token's gold price from data.wowtoken.app.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

RAIDERIO_API = "https://raider.io/api/v1"
# The fields a fallback page needs (about 23 KB); with Battle.net only the score and raids (about 2 KB).
PROFILE_FIELDS = "mythic_plus_scores_by_season:current,raid_progression,mythic_plus_best_runs,gear"
SUMMARY_FIELDS = "mythic_plus_scores_by_season:current,raid_progression"
WOWTOKEN_URL = "https://data.wowtoken.app/v2/current/retail.json"
REQUEST_TIMEOUT = (4, 15)
USER_AGENT = "InkyPi WowProfileDashboard/1.0"
RAIDERIO_REGIONS = {"us", "eu", "kr", "tw"}
# Raider.IO's gear slots as Battle.net names them, so the paper doll places them alike.
GEAR_SLOTS = {
    "head": "Head", "neck": "Neck", "shoulder": "Shoulder", "back": "Back", "chest": "Chest", "waist": "Waist",
    "wrist": "Wrist", "hands": "Hands", "legs": "Legs", "feet": "Feet", "finger1": "Finger", "finger2": "Finger",
    "trinket1": "Trinket", "trinket2": "Trinket", "mainhand": "Main Hand", "offhand": "Off Hand",
}
QUALITIES = {0: "POOR", 1: "COMMON", 2: "UNCOMMON", 3: "RARE", 4: "EPIC", 5: "LEGENDARY", 6: "ARTIFACT"}
SAFE_NAME = re.compile(r"[^\W_][\w'-]{0,23}", re.UNICODE)


def _get_json(session, url, params=None):
    response = session.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                           timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fetch_profile(session, region, realm_slug, character, fields=PROFILE_FIELDS):
    """A character's Raider.IO profile, or None when Raider.IO does not know the region or the name."""
    if region not in RAIDERIO_REGIONS or not realm_slug or not SAFE_NAME.fullmatch(str(character or "")):
        return None
    payload = _get_json(session, f"{RAIDERIO_API}/characters/profile",
                        {"region": region, "realm": realm_slug, "name": character, "fields": fields})
    return payload if isinstance(payload, dict) and payload.get("name") else None


def mythic_score(profile):
    """The character's current-season Mythic+ score (all roles), 0 without one."""
    for season in (profile or {}).get("mythic_plus_scores_by_season") or []:
        if isinstance(season, dict):
            return _number((season.get("scores") or {}).get("all"))
    return 0.0


def raid_name(slug):
    """'the-venomous-abyss' -> 'The Venomous Abyss'."""
    return " ".join(word.capitalize() for word in str(slug or "").split("-") if word)


def raid_progress(profile):
    """The character's raids with any kills, furthest first: name and summary ('5/8 M')."""
    raids = []
    for slug, raid in ((profile or {}).get("raid_progression") or {}).items():
        if not isinstance(raid, dict) or not str(raid.get("summary") or "").strip():
            continue
        kills = tuple(int(_number(raid.get(f"{level}_bosses_killed"))) for level in ("mythic", "heroic", "normal"))
        raids.append((kills, int(_number(raid.get("total_bosses"))), {
            "name": raid_name(slug), "summary": str(raid["summary"]).strip()}))
    raids.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [raid for _kills, _total, raid in raids]


def best_runs(profile):
    runs = []
    for run in (profile or {}).get("mythic_plus_best_runs") or []:
        if isinstance(run, dict) and run.get("dungeon"):
            runs.append({"dungeon": str(run["dungeon"]), "level": run.get("mythic_level") or "-",
                         "score": _number(run.get("score"))})
    runs.sort(key=lambda run: run["score"], reverse=True)
    return runs


def gear_rows(profile):
    items = ((profile or {}).get("gear") or {}).get("items") or {}
    rows = []
    for key, slot in GEAR_SLOTS.items():
        item = items.get(key) if isinstance(items, dict) else None
        if isinstance(item, dict) and item.get("name"):
            rows.append({"slot": slot, "name": str(item["name"]), "level": item.get("item_level") or "-",
                         "quality": QUALITIES.get(item.get("item_quality"), "-")})
    return rows


def model_url(thumbnail):
    """The full-body render beside a Raider.IO avatar ('...-avatar.jpg' -> '...-main-raw.png')."""
    url = str(thumbnail or "")
    match = re.match(r"(https://render\.worldofwarcraft\.com/[a-z]{2}/character/[\w/-]+?)-avatar\.jpg", url)
    return f"{match.group(1)}-main-raw.png" if match else ""


def character_payload(profile, region_label, locale):
    """A Raider.IO profile as the dashboard's character payload."""
    gear = (profile.get("gear") or {}) if isinstance(profile.get("gear"), dict) else {}
    item_level = gear.get("item_level_equipped") or "-"
    crawled = str(profile.get("last_crawled_at") or "")[:10] or "-"
    return {
        "kind": "character",
        "mode": "raiderio",
        "status": "ok",
        "title": str(profile.get("name") or "WoW Character"),
        "subtitle": f"{profile.get('realm') or '-'} / {region_label}",
        "region": region_label,
        "locale": locale,
        "source": "Raider.IO",
        "level": "-",
        "race": profile.get("race") or "-",
        "class": profile.get("class") or "-",
        "spec": profile.get("active_spec_name") or "-",
        "faction": str(profile.get("faction") or "-").capitalize(),
        "achievement_points": profile.get("achievement_points") or 0,
        "average_item_level": item_level,
        "equipped_item_level": item_level,
        "last_login": crawled,
        "portrait_url": str(profile.get("thumbnail_url") or ""),
        "media_url": model_url(profile.get("thumbnail_url")),
        "equipment": gear_rows(profile),
        "mythic_rating": mythic_score(profile),
        "mythic_best": best_runs(profile)[:6],
        "raids": raid_progress(profile)[:3],
        "pvp_bracket": {},
        "notice": "",
    }


def short_affix(name):
    """An affix as the column prints it: the season's Xal'atath prefix dropped."""
    return re.sub(r"^Xal'atath's\s+", "", " ".join(str(name or "").split()))


def fetch_affixes(session, region):
    """This week's Mythic+ affixes in ``region``: id, name and icon URL each."""
    payload = _get_json(session, f"{RAIDERIO_API}/mythic-plus/affixes",
                        {"region": region if region in RAIDERIO_REGIONS else "us", "locale": "en"})
    affixes = []
    for affix in (payload or {}).get("affix_details") or []:
        if isinstance(affix, dict) and affix.get("name"):
            icon = str(affix.get("icon_url") or "")
            affixes.append({"id": int(_number(affix.get("id"))), "name": str(affix["name"]),
                            "icon_url": icon if icon.startswith("https://cdn.raiderio.net/") else ""})
    if not affixes:
        raise ValueError("Raider.IO listed no affixes")
    return affixes


def fetch_token(session, region):
    """The WoW Token's current gold price in ``region`` and when it was read."""
    payload = _get_json(session, WOWTOKEN_URL)
    entry = (payload or {}).get(region if region in RAIDERIO_REGIONS else "us")
    if not isinstance(entry, list) or len(entry) < 2 or _number(entry[1]) <= 0:
        raise ValueError("data.wowtoken.app has no price for the region")
    return {"gold": int(_number(entry[1])), "updated": str(entry[0])}


def gold_text(gold):
    """294637 -> '294,637'."""
    return f"{int(gold):,}"


def cache_fresh(entry, seconds, now=None):
    try:
        stamp = datetime.fromisoformat(str((entry or {}).get("fetched_at")))
    except ValueError:
        return False
    age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    return 0 <= age < seconds
