"""Conservative HLTV event identity lookup, independent of score freshness."""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from runtime.refresh_contracts import TaskContext
from utils.http_client import HttpClient
from .cs2_cards import normalized_name, safe_logo_url, utc_datetime


logger = logging.getLogger(__name__)
CATALOG_TTL = timedelta(hours=6)
RETRY_DELAY = timedelta(hours=1)
MAX_RECORDS = 64
CATALOG_VERSION = 2
LOCAL_EVENT_DIR = Path(__file__).parent / "assets" / "logos" / "cs2_events"
# PandaScore's current match feed calls HLTV event 9392 "Season 2".
# Keep this alias edition-specific; qualifiers and other seasons stay distinct.
EVENT_NAME_ALIASES = {
    "exortfiestaseason2": "exortfiestaseries2",
}
# PandaScore/Liquipedia number 1win Private Club editions "#N" where HLTV says
# "Season N" (#1 = HLTV 9327, #2 Closed Qualifier = 9408). The edition number
# and any stage suffix are kept, so qualifiers and other seasons stay distinct.
ONEWIN_EDITION = re.compile(r"^1winprivateclub(\d+)")
# Tolerant edition matching: PandaScore and HLTV name the same edition with
# different sponsors and words ("CIS LAN Championship #9" vs "WINLINE MPKBK CIS
# LAN Season 9"). Generic words and calendar years are ignored, every distinctive
# provider word must appear on the HLTV side, the stage and edition numbers must
# agree, and region words alone never identify a series.
GENERIC_WORDS = frozenset({
    "season", "series", "championship", "league", "cup", "edition", "episode", "the", "of",
    "esports", "cs", "cs2", "csgo", "tournament", "event", "s",
})
REGION_WORDS = frozenset({
    "cis", "eu", "europe", "european", "na", "north", "america", "american", "sa", "south", "asia",
    "pacific", "apac", "oceania", "mena", "africa", "china", "brazil", "international", "global", "world",
})
# Tier words name a different event of the same organiser; they never count as
# sponsor noise on the catalog side.
TIER_WORDS = frozenset({
    "contenders", "challengers", "challenger", "academy", "development", "juniors", "junior", "rising",
    "women", "female",
})
STAGE_WORDS = frozenset({
    "qualifier", "qualifiers", "closed", "finals", "final", "playoffs", "playin", "showmatch", "stage",
})
FAMILY_LOOKBACK = timedelta(days=200)


def _canonical(name):
    # Only strip a calendar year; edition/season numbers remain significant.
    name = normalized_name(re.sub(r"\b20\d{2}\b", "", str(name or "")))
    name = ONEWIN_EDITION.sub(r"1winprivateclubseason\1", name)
    return EVENT_NAME_ALIASES.get(name, name)


def parse_catalog(html):
    soup = BeautifulSoup(html, "html.parser")
    entries = {}
    for anchor in soup.select("a.ongoing-event, a.big-event, a.small-event"):
        link = str(anchor.get("href") or "")
        match = re.fullmatch(r"/events/(\d{1,10})/[a-zA-Z0-9-]+", link)
        title = anchor.select_one(".event-name-small .text-ellipsis, .event-col .text-ellipsis")
        if title is None:
            title = anchor.select_one(".big-event-name, .event-name-small, .small-event-name")
        if not match or title is None:
            continue
        dates = []
        for node in anchor.select("[data-unix]"):
            try:
                dates.append(datetime.fromtimestamp(int(node["data-unix"]) / 1000, timezone.utc))
            except (ValueError, TypeError, OverflowError, OSError):
                pass
        if not dates:
            continue
        name = title.get_text(" ", strip=True)
        if not name or len(name) > 180:
            continue
        entries[match[1]] = {
            "id": match[1],
            "name": name,
            "path": link,
            "start": min(dates).isoformat(),
            "end": max(dates).isoformat(),
            **_parse_logo_nodes(anchor.select("img.logo"), name),
        }
    return list(entries.values())[:200]


def match_event(entries, main):
    start = utc_datetime(main.get("start"))
    if not start:
        return None
    name = str(main.get("event_name") or "")
    league = str(main.get("league_name") or "")
    names = {_canonical(name)}
    if league and normalized_name(league) not in normalized_name(name):
        names.add(_canonical(league + " " + name))
    matches = []
    for entry in entries:
        if not isinstance(entry, Mapping) or _canonical(entry.get("name")) not in names:
            continue
        begin, end = utc_datetime(entry.get("start")), utc_datetime(entry.get("end"))
        if begin and end and begin - timedelta(days=1) <= start <= end + timedelta(days=1):
            matches.append(entry)
    return matches[0] if len(matches) == 1 else None


def _edition_tokens(name):
    text = re.sub(r"\b20\d{2}\b", " ", str(name or "").casefold())
    text = re.sub(r"(\D)(\d)", r"\1 \2", text)
    parts = re.findall(r"[a-z0-9]+", text)
    numbers = {part.lstrip("0") or "0" for part in parts if part.isdigit()}
    words = {part for part in parts if not part.isdigit()}
    return words - GENERIC_WORDS - STAGE_WORDS, words & STAGE_WORDS, numbers


def _distinctive(words):
    return any(len(word) >= 3 and word not in REGION_WORDS for word in words)


def _logo_identity(entry):
    value = entry.get("event_logo_url") or entry.get("event_logo_path") or ""
    return urlparse(str(value)).path or str(value)


def _provider_names(main):
    name = str(main.get("event_name") or "")
    league = str(main.get("league_name") or "")
    names = [name]
    if league and normalized_name(league) not in normalized_name(name):
        names.append(league + " " + name)
    return names


def fuzzy_match_event(entries, main):
    """One edition whose dates hold the match, despite sponsor and wording differences."""
    start = utc_datetime(main.get("start"))
    if not start:
        return None
    candidates = []
    for name in _provider_names(main):
        words, stage, numbers = _edition_tokens(name)
        if not _distinctive(words) or not numbers:
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            entry_words, entry_stage, entry_numbers = _edition_tokens(entry.get("name"))
            if not words <= entry_words or stage != entry_stage or not (entry_words & TIER_WORDS) <= words:
                continue
            if not (numbers == entry_numbers or numbers < entry_numbers):
                continue
            begin, end = utc_datetime(entry.get("start")), utc_datetime(entry.get("end"))
            if begin and end and begin - timedelta(days=1) <= start <= end + timedelta(days=1):
                candidates.append((len(entry_words - words), entry))
    if not candidates:
        return None
    best = min(extra for extra, _entry in candidates)
    top = [entry for extra, entry in candidates if extra == best]
    return top[0] if len({_logo_identity(entry) for entry in top}) == 1 else None


def family_match_event(entries, main):
    """The latest past edition of the same series, for its shared series logo."""
    start = utc_datetime(main.get("start"))
    if not start:
        return None
    matches = []
    for name in _provider_names(main):
        words, stage, _numbers = _edition_tokens(name)
        if not _distinctive(words):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            entry_words, entry_stage, _entry_numbers = _edition_tokens(entry.get("name"))
            end = utc_datetime(entry.get("end"))
            if words <= entry_words and stage == entry_stage and (entry_words & TIER_WORDS) <= words \
                    and end and end <= start + timedelta(days=1) and start - end <= FAMILY_LOOKBACK:
                matches.append(entry)
    return max(matches, key=lambda entry: utc_datetime(entry.get("end"))) if matches else None


def parse_event_logos(html, name):
    soup = BeautifulSoup(html, "html.parser")
    return _parse_logo_nodes(soup.select("img.event-logo"), name)


def _parse_logo_nodes(nodes, name):
    logos = {}
    for node in nodes:
        if normalized_name(name) not in {normalized_name(node.get("alt")), normalized_name(node.get("title"))}:
            continue
        url = safe_logo_url(node.get("src"))
        # Keep the publisher's signed high-DPI URL intact.
        for candidate in str(node.get("srcset") or "").split(","):
            parts = candidate.split()
            if len(parts) == 2 and parts[1] == "2x" and safe_logo_url(parts[0]):
                url = safe_logo_url(parts[0])
        parsed = urlparse(url)
        if parsed.hostname != "img-cdn.hltv.org" or not parsed.path.startswith("/eventlogo/"):
            continue
        key = "event_logo_url_dark" if "night-only" in (node.get("class") or []) else "event_logo_url"
        logos.setdefault(key, url)
    return logos if logos.get("event_logo_url") else {}


@lru_cache(maxsize=1)
def _local_events():
    try:
        data = json.loads((LOCAL_EVENT_DIR / "manifest.json").read_text(encoding="utf-8"))
        return data.get("events", [])
    except (OSError, ValueError):
        return []


def _bundled_paths(event, source):
    result = {}
    for key in ("event_logo_path", "event_logo_path_dark"):
        filename = str(event.get(key) or "")
        if re.fullmatch(r"[a-z0-9_-]+\.(?:png|webp)", filename):
            result[key] = str(LOCAL_EVENT_DIR / filename)
    if result:
        result["event_logo_source"] = source
    return result


def _local_main(card):
    main = (card or {}).get("main") or {}
    return {**main, "event_name": (card or {}).get("event_name") or main.get("event_name")}


def local_event_branding(card):
    """Bundled logo of this exact edition (also offline for persisted cards)."""
    main = _local_main(card)
    entries = _local_events()
    event = match_event(entries, main) or fuzzy_match_event(entries, main)
    return _bundled_paths(event, "LOCAL") if event else {}


def local_named_branding(name):
    """Bundled logo of the one edition HLTV calls exactly ``name``, for a match listed without its date."""
    key = normalized_name(name)
    events = [event for event in _local_events() if key and normalized_name(event.get("name")) == key]
    return _bundled_paths(events[0], "LOCAL") if len(events) == 1 else {}


def local_family_branding(card):
    """The bundled series logo of a recent past edition, when nothing edition-specific exists."""
    event = family_match_event(_local_events(), _local_main(card))
    return _bundled_paths(event, "LOCAL FAMILY") if event else {}


def _finish_branding(card):
    """Provider league art outranks a borrowed series logo; record what will be drawn."""
    if card.get("event_logo_source"):
        return card
    if safe_logo_url(card.get("event_logo_url")):
        card["event_logo_source"] = "PANDASCORE"
        return card
    family = local_family_branding(card)
    if family:
        card.update(family)
    return card


def _text(client, path, context):
    return client.request_text(
        "GET",
        "https://www.hltv.org" + path,
        context=context,
        timeout=8,
        max_bytes=1024 * 1024,
        headers={"User-Agent": "EpaperSystem/SportsDashboard"},
    ).data


def enrich_event_branding(plugin, card, settings, now, session, *, allow_network=True):
    """Reuse validated event metadata and the existing managed image disk cache."""
    if not card:
        return card
    local = local_event_branding(card)
    if local:
        card.update(local)
        return card
    if not plugin._bool_setting(settings, "pandaScoreCs2HltvLogos", True):
        return _finish_branding(card)
    path = plugin._sports_dashboard_cache_dir() / "cs2_event_branding.json"
    cache = plugin._read_json_file(path)
    records = cache.get("records")
    records = dict(records) if isinstance(records, Mapping) else {}
    key = card["event_id"]
    record = records.get(key)
    if isinstance(record, Mapping) and record.get("name") == card["event_name"]:
        valid_until = utc_datetime(record.get("valid_until"))
        if valid_until and now < valid_until:
            card.update({k: safe_logo_url(record.get(k)) for k in ("event_logo_url", "event_logo_url_dark")})
            card["event_logo_source"] = "HLTV"
            return card
    retry = utc_datetime(cache.get("retry_until"))
    if not allow_network or (retry and now < retry):
        return _finish_branding(card)
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 12)
    client = HttpClient(session=session, max_attempts=1)
    stamp = utc_datetime(cache.get("catalog_at"))
    entries = cache.get("catalog")
    changed = False
    try:
        if (
            cache.get("catalog_version") != CATALOG_VERSION
            or not isinstance(entries, list)
            or not stamp
            or not timedelta(0) <= now - stamp < CATALOG_TTL
        ):
            entries = parse_catalog(_text(client, "/events", context))
            if not entries:
                raise ValueError("HLTV event catalog unavailable")
            cache.update(catalog=entries, catalog_at=now.isoformat(), catalog_version=CATALOG_VERSION)
            changed = True
        event = match_event(entries, card["main"]) or fuzzy_match_event(entries, card["main"])
        if event:
            # Catalog paths are revalidated before using a persisted URL.
            event_path = str(event.get("path") or "")
            if not re.fullmatch(r"/events/\d{1,10}/[a-zA-Z0-9-]+", event_path):
                raise ValueError("Invalid event path")
            logos = {
                field: safe_logo_url(event.get(field))
                for field in ("event_logo_url", "event_logo_url_dark")
            }
            if not logos["event_logo_url"]:
                logos = parse_event_logos(_text(client, event_path, context), event["name"])
            if not logos:
                raise ValueError("No matching event logo")
            valid_until = (utc_datetime(event["end"]) + timedelta(days=3)).isoformat()
            records[key] = {
                **logos,
                "name": card["event_name"],
                "hltv_id": event["id"],
                "valid_until": valid_until,
                "updated_at": now.isoformat(),
            }
            card.update(logos, event_logo_url_dark=logos.get("event_logo_url_dark", ""), event_logo_source="HLTV")
            changed = True
        # A missing match needs no retry delay: the catalog TTL already bounds
        # requests, and another event must still be able to use that catalog.
    except Exception as exc:
        cache["retry_until"] = (now + RETRY_DELAY).isoformat()
        changed = True
        logger.info("CS2 event branding unavailable; retaining provider branding: %s", type(exc).__name__)
    if changed:
        records = {
            k: v
            for k, v in records.items()
            if isinstance(v, Mapping) and (utc_datetime(v.get("valid_until")) or now) > now
        }
        cache["records"] = dict(
            sorted(records.items(), key=lambda pair: str(pair[1].get("updated_at")), reverse=True)[:MAX_RECORDS]
        )
        try:
            plugin._write_json_file(path, cache)
        except OSError:
            logger.info("CS2 event branding metadata could not be cached")
    return _finish_branding(card)
