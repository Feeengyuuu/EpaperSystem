"""Bounded feeds for the Nature Nearby page: iNaturalist's newest finds, this week's species,
life-group counts and observer totals around home (keyless), and, with an eBird key, the
notable birds reported nearby this week."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from runtime.refresh_contracts import TaskCancelled
from utils.http_client import get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi NatureNearby/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (3, 9)
V2_OBSERVATIONS_URL = "https://api.inaturalist.org/v2/observations"
V1_URL = "https://api.inaturalist.org/v1"
PHOTO_HOSTS = ("inaturalist-open-data.s3.amazonaws.com", "static.inaturalist.org")
OBSERVATION_FIELDS = ("id,observed_on,time_observed_at,place_guess,location,user.login,photos.url,"
                      "photos.attribution,taxon.id,taxon.name,taxon.preferred_common_name,taxon.iconic_taxon_name")
# The newest find's species: its global IUCN Red List category (with the Red List's own
# colours) and whether it is native to California.
IUCN_CATEGORIES = {
    "LC": ("无危", "#60C659"), "NT": ("近危", "#CCE226"), "VU": ("易危", "#F9E814"), "EN": ("濒危", "#FC7F3F"),
    "CR": ("极危", "#D81E05"), "EW": ("野外灭绝", "#542344"), "EX": ("灭绝", "#000000"), "DD": ("数据缺乏", "#D1D1C6"),
}
ESTABLISHMENT = {"native": "加州本土", "endemic": "加州特有", "introduced": "外来物种"}
DEFAULT_HOME = (37.5485, -121.9886)  # Fremont
PREFERRED_PLACE = 14  # California, for regional common names
MAX_RECENT = 12
MAX_SPECIES = 12
# Life groups: Chinese label, icon, colour role (four native panel inks, then ink).
GROUPS = {
    "Plantae": ("植物", "leaf", "green"), "Insecta": ("昆虫", "bug", "yellow"), "Aves": ("鸟类", "bird", "blue"),
    "Fungi": ("真菌", "mushroom", "red"), "Arachnida": ("蛛形", "spider", "ink"), "Mammalia": ("哺乳", "paw", "ink"),
    "Reptilia": ("爬行", "lizard", "ink"), "Amphibia": ("两栖", "frog", "ink"),
    "Actinopterygii": ("鱼类", "fish", "ink"), "Mollusca": ("软体", "snail", "ink"),
}
OTHER_GROUP = ("其他", "info", "ink")
EBIRD_NOTABLE_URL = "https://api.ebird.org/v2/data/obs/geo/recent/notable"
EBIRD_DAYS = 7
EBIRD_MAX_KM = 50  # eBird's largest search radius
MAX_BIRDS = 6


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=PACIFIC)


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def group_info(name):
    return GROUPS.get(str(name or ""), OTHER_GROUP)


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def home_setting(latitude=None, longitude=None):
    if latitude in (None, "") and longitude in (None, ""):
        return DEFAULT_HOME
    lat, lon = _finite(latitude), _finite(longitude)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("请填写有效的纬度和经度")
    return (round(lat, 4), round(lon, 4))


def distance_km(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def safe_photo_url(value):
    url = str(value or "").strip()
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname in PHOTO_HOSTS else None


def photo_url(url, size="medium"):
    """iNaturalist photo URLs differ only by the size segment (square/small/medium/large)."""
    url = safe_photo_url(url)
    return re.sub(r"/(square|small|thumb|medium|large|original)\.", f"/{size}.", url) if url else None


def short_place(place):
    """'Grand Blvd, San Jose, CA, US' -> 'San Jose'."""
    parts = [part.strip() for part in str(place or "").split(",") if part.strip()]
    # Drop country names and state/ZIP fragments such as "CA", "95035" or "CA 95035".
    parts = [part for part in parts if part not in {"USA", "California", "United States"}
             and not re.fullmatch(r"(?:[A-Z]{2})?\s*(?:\d{5}(?:-\d{4})?)?", part)]
    return parts[-1][:28] if parts else ""


def _common(params, home, radius):
    return {**params, "lat": home[0], "lng": home[1], "radius": radius, "locale": "zh-CN",
            "preferred_place_id": PREFERRED_PLACE}


def _get_json(url, params, context, max_bytes=512 * 1024, headers=None):
    context.raise_if_cancelled()
    response = get_http_client().request_json("GET", url, context=context, timeout=REQUEST_TIMEOUT,
                                              max_bytes=max_bytes, params=params,
                                              headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                       **(headers or {})})
    context.raise_if_cancelled()
    return response.data


def week_start(now):
    return (now - timedelta(days=7)).date().isoformat()


def normalize_recent(payload, home):
    results = (payload or {}).get("results")
    if not isinstance(results, list):
        raise ValueError("iNaturalist returned no observations")
    items = []
    for row in results:
        taxon = (row or {}).get("taxon") or {}
        photos = row.get("photos") or []
        photo = photo_url((photos[0] or {}).get("url")) if photos else None
        stamp = parse_time(row.get("time_observed_at")) or parse_time(row.get("observed_on"))
        if not taxon.get("name") or not photo or stamp is None:
            continue
        lat = lon = None
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", str(row.get("location") or ""))
        if match:
            lat, lon = float(match.group(1)), float(match.group(2))
        taxon_id = taxon.get("id")
        items.append({
            "id": row.get("id"), "taxon_id": taxon_id if isinstance(taxon_id, int) and taxon_id > 0 else None,
            "name": str(taxon.get("preferred_common_name") or "")[:24],
            "scientific": str(taxon["name"])[:48], "group": str(taxon.get("iconic_taxon_name") or ""),
            "time": stamp.isoformat(), "place": short_place(row.get("place_guess")),
            "distance_km": round(distance_km(home[0], home[1], lat, lon), 1) if lat is not None else None,
            "observer": str(((row.get("user") or {}).get("login")) or "")[:30], "photo": photo,
            "attribution": str((photos[0] or {}).get("attribution") or "")[:80],
        })
    items.sort(key=lambda item: item["time"], reverse=True)
    if not items:
        raise ValueError("iNaturalist returned no usable observations")
    return {"items": items[:MAX_RECENT]}


def fetch_recent(home, radius, context):
    params = _common({"quality_grade": "research", "photos": "true", "order_by": "observed_on", "order": "desc",
                      "per_page": MAX_RECENT, "fields": OBSERVATION_FIELDS}, home, radius)
    return normalize_recent(_get_json(V2_OBSERVATIONS_URL, params, context), home)


def normalize_species(payload):
    results = (payload or {}).get("results")
    if not isinstance(results, list):
        raise ValueError("iNaturalist returned no species counts")
    items = []
    for row in results:
        taxon = (row or {}).get("taxon") or {}
        count = row.get("count")
        if not taxon.get("name") or not isinstance(count, int):
            continue
        default_photo = taxon.get("default_photo") or {}
        items.append({"count": count, "name": str(taxon.get("preferred_common_name") or "")[:24],
                      "scientific": str(taxon["name"])[:48], "group": str(taxon.get("iconic_taxon_name") or ""),
                      "photo": photo_url(default_photo.get("medium_url") or default_photo.get("square_url"))})
    return {"items": items[:MAX_SPECIES], "total": int((payload or {}).get("total_results") or len(items))}


def fetch_species(home, radius, now, context):
    params = _common({"d1": week_start(now), "quality_grade": "research", "per_page": MAX_SPECIES}, home, radius)
    return normalize_species(_get_json(f"{V1_URL}/observations/species_counts", params, context))


def normalize_groups(payload):
    results = (payload or {}).get("results")
    if not isinstance(results, list):
        raise ValueError("iNaturalist returned no group counts")
    groups = [{"group": str(((row or {}).get("taxon") or {}).get("name") or ""), "count": int(row.get("count") or 0)}
              for row in results if isinstance(row, dict)]
    groups = [group for group in groups if group["group"] and group["count"] > 0]
    groups.sort(key=lambda group: -group["count"])
    return {"groups": groups}


def fetch_groups(home, radius, now, context):
    params = _common({"d1": week_start(now)}, home, radius)
    return normalize_groups(_get_json(f"{V1_URL}/observations/iconic_taxa_species_counts", params, context))


def fetch_stats(home, radius, now, context):
    params = _common({"d1": week_start(now)}, home, radius)
    total = _get_json(f"{V1_URL}/observations", {**params, "per_page": 0}, context, max_bytes=64 * 1024)
    observers = _get_json(f"{V1_URL}/observations/observers", {**params, "per_page": 1}, context)
    top = ((observers or {}).get("results") or [{}])[0] or {}
    return {
        "observations": int((total or {}).get("total_results") or 0),
        "observers": int((observers or {}).get("total_results") or 0),
        "top_observer": str(((top.get("user") or {}).get("login")) or "")[:30],
        "top_observations": int(top.get("observation_count") or 0),
        "top_species": int(top.get("species_count") or 0),
    }


def ebird_place(name):
    """'Coyote Hills RP--Main Marsh' -> 'Coyote Hills RP'; a private spot's coordinates are dropped."""
    place = str(name or "").split("--")[0].split("(")[0].strip()
    return "" if re.fullmatch(r"[-\d.,\s]+", place) else place[:28]


def normalize_notable(rows, home):
    """eBird's notable sightings, one per species (its latest report), newest first:
    {species, items: [{name, scientific, place, time, count, distance_km, confirmed}]}."""
    if not isinstance(rows, list):
        raise ValueError("eBird returned no sightings")
    latest = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("speciesCode") or not row.get("comName"):
            continue
        stamp = parse_time(str(row.get("obsDt") or "").replace(" ", "T"))
        if stamp is None:
            continue
        lat, lon = _finite(row.get("lat")), _finite(row.get("lng"))
        item = {"name": str(row["comName"])[:24], "scientific": str(row.get("sciName") or "")[:48],
                "place": ebird_place(row.get("locName")), "time": stamp.isoformat(),
                "count": int(row.get("howMany") or 0) if str(row.get("howMany") or "").isdigit() else 0,
                "distance_km": (round(distance_km(home[0], home[1], lat, lon), 1)
                                if lat is not None and lon is not None else None),
                "confirmed": bool(row.get("obsValid"))}
        current = latest.get(row["speciesCode"])
        if current is None or item["time"] > current["time"]:
            latest[row["speciesCode"]] = item
    items = sorted(latest.values(), key=lambda item: item["time"], reverse=True)
    return {"species": len(latest), "items": items[:MAX_BIRDS]}


def chinese_bird_names(items, context):
    """Give each bird its Chinese common name from iNaturalist (by scientific name), since eBird's
    notable feed answers in English; a name not found stays as it is."""
    for item in items:
        if not item.get("scientific") or re.search(r"[一-鿿]", item.get("name") or ""):
            continue
        try:
            data = _get_json(f"{V1_URL}/taxa", {"q": item["scientific"], "rank": "species", "per_page": 1,
                                                "locale": "zh-CN"}, context, max_bytes=128 * 1024)
        except TaskCancelled:
            raise
        except Exception:
            continue
        taxon = ((data or {}).get("results") or [{}])[0]
        name = str(taxon.get("preferred_common_name") or "")
        if taxon.get("name") == item["scientific"] and re.search(r"[一-鿿]", name):
            item["name_en"], item["name"] = item["name"], name[:24]
    return items


def fetch_notable(home, radius, key, context):
    params = {"lat": home[0], "lng": home[1], "dist": min(radius, EBIRD_MAX_KM), "back": EBIRD_DAYS,
              "detail": "simple", "sppLocale": "zh_SIM", "maxResults": 200}
    data = normalize_notable(_get_json(EBIRD_NOTABLE_URL, params, context, headers={"X-eBirdApiToken": key}), home)
    chinese_bird_names(data["items"], context)
    return data


def normalize_taxon(payload):
    """A species' order and family (Chinese names where iNaturalist has them), its global IUCN
    category, how it stands in California and how often it has been observed worldwide."""
    results = (payload or {}).get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ValueError("iNaturalist returned no taxon")
    taxon = results[0]
    ranks = {ancestor.get("rank"): ancestor for ancestor in taxon.get("ancestors") or [] if isinstance(ancestor, dict)}

    def named(rank):
        ancestor = ranks.get(rank) or {}
        return str(ancestor.get("preferred_common_name") or ancestor.get("name") or "")[:16]

    iucn = next((str(status.get("status") or "").upper() for status in taxon.get("conservation_statuses") or []
                 if isinstance(status, dict) and "IUCN" in str(status.get("authority") or "") and not status.get("place")),
                "")
    means = str(taxon.get("preferred_establishment_means") or "")
    count = taxon.get("observations_count")
    return {"id": taxon.get("id"), "order": named("order"), "family": named("family"),
            "iucn": iucn if iucn in IUCN_CATEGORIES else "", "establishment": means if means in ESTABLISHMENT else "",
            "observations": count if isinstance(count, int) and count >= 0 else 0}


def fetch_taxon(taxon_id, context):
    params = {"locale": "zh-CN", "preferred_place_id": PREFERRED_PLACE}
    return normalize_taxon(_get_json(f"{V1_URL}/taxa/{int(taxon_id)}", params, context, max_bytes=1024 * 1024))


def valid_taxon(data):
    return isinstance(data, dict) and isinstance(data.get("observations"), int)


def valid_notable(data):
    items = (data or {}).get("items")
    return isinstance(items, list) and all(isinstance(item, dict) and item.get("name") for item in items)


def valid_recent(data):
    items = (data or {}).get("items")
    return isinstance(items, list) and all(isinstance(item, dict) and item.get("scientific") and item.get("photo")
                                           for item in items)


def valid_species(data):
    items = (data or {}).get("items")
    return isinstance(items, list) and all(isinstance(item, dict) and isinstance(item.get("count"), int)
                                           for item in items)


def valid_groups(data):
    groups = (data or {}).get("groups")
    return isinstance(groups, list) and all(isinstance(group, dict) and group.get("group") for group in groups)


def valid_stats(data):
    return isinstance(data, dict) and isinstance(data.get("observations"), int)
