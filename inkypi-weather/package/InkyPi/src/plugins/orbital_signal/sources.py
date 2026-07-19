from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from utils.http_client import get_http_session


LAUNCHES_URL = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/"
MARKETS_URL = "https://gamma-api.polymarket.com/events"
REQUEST_TIMEOUT = (4, 18)
USER_AGENT = "InkyPi OrbitalSignal/1.0"


def _utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _utc(parsed)


def _text(value, default=""):
    return value.strip() if isinstance(value, str) and value.strip() else default


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _http_image_url(value):
    text = _text(value)
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None and not 1 <= port <= 65535
    ):
        return ""
    return text


def _license_name(value):
    if isinstance(value, dict):
        return _text(value.get("name"), _text(value.get("spdx_id")))
    return _text(value)


def _image_metadata(value, source):
    image = _mapping(value)
    image_url = _http_image_url(image.get("image_url"))
    thumbnail_url = _http_image_url(image.get("thumbnail_url"))
    if not image_url and not thumbnail_url:
        return None
    return {
        "image_url": image_url,
        "thumbnail_url": thumbnail_url,
        "image_credit": _text(image.get("credit")),
        "image_license": _license_name(image.get("license")),
        "image_source": source,
    }


def _number(value, default=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _json_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    return list(value) if isinstance(value, list) else []


def normalize_launches(payload, now=None, limit=4):
    now_utc = _utc(now or datetime.now(timezone.utc))
    results = payload.get("results") if isinstance(payload, dict) else None
    rows = []
    for raw in results if isinstance(results, list) else []:
        if not isinstance(raw, dict):
            continue
        launch_id = _text(raw.get("id"))
        name = _text(raw.get("name"))
        net = _parse_time(raw.get("net"))
        if not launch_id or not name or net is None:
            continue
        if net < now_utc - timedelta(hours=6):
            continue

        status = _mapping(raw.get("status"))
        provider = _mapping(raw.get("launch_service_provider"))
        rocket = _mapping(raw.get("rocket"))
        configuration = _mapping(rocket.get("configuration"))
        mission = _mapping(raw.get("mission"))
        orbit = _mapping(mission.get("orbit"))
        pad = _mapping(raw.get("pad"))
        location = _mapping(pad.get("location"))
        mission_name = _text(mission.get("name"))
        if not mission_name and "|" in name:
            mission_name = name.split("|", 1)[1].strip()
        launch_image = _image_metadata(raw.get("image"), "launch")
        launcher_image = _image_metadata(
            configuration.get("image"),
            "launcher_configuration",
        )
        primary_image = launch_image or launcher_image or {}
        fallback_image = launcher_image if launch_image and launcher_image else {}

        rows.append(
            {
                "id": launch_id,
                "name": name,
                "net": net.isoformat(),
                "status": _text(status.get("abbrev"), "TBD").upper(),
                "provider": _text(provider.get("name"), "Unknown provider"),
                "rocket": _text(
                    configuration.get("full_name"),
                    _text(configuration.get("name"), name.split("|", 1)[0].strip()),
                ),
                "mission": mission_name or name,
                "orbit": _text(orbit.get("abbrev"), _text(orbit.get("name"), "—")),
                "pad": _text(pad.get("name"), "Pad TBD"),
                "location": _text(location.get("name"), "Location TBD"),
                "webcast_live": bool(raw.get("webcast_live", False)),
                "image_url": primary_image.get("image_url", ""),
                "thumbnail_url": primary_image.get("thumbnail_url", ""),
                "image_credit": primary_image.get("image_credit", ""),
                "image_license": primary_image.get("image_license", ""),
                "image_source": primary_image.get("image_source", ""),
                "fallback_image_url": fallback_image.get("image_url", ""),
                "fallback_thumbnail_url": fallback_image.get("thumbnail_url", ""),
                "fallback_image_credit": fallback_image.get("image_credit", ""),
                "fallback_image_license": fallback_image.get("image_license", ""),
                "fallback_image_source": fallback_image.get("image_source", ""),
            }
        )

    rows.sort(key=lambda row: row["net"])
    return rows[: max(0, int(limit))]


def heat_score(volume_24h, change_24h):
    volume = max(0.0, _number(volume_24h, 0.0))
    movement = abs(_number(change_24h, 0.0))
    volume_component = max(0.0, min(70.0, (math.log10(volume + 1) - 3.0) / 4.0 * 70.0))
    movement_component = min(100.0, movement * 1_000.0)
    return int(round(max(0.0, min(100.0, volume_component + movement_component))))


def _category_for_event(event):
    labels = {
        _text(tag.get("label")).casefold()
        for tag in event.get("tags", [])
        if isinstance(tag, dict) and _text(tag.get("label"))
    }
    joined = f"{_text(event.get('title')).casefold()} {' '.join(sorted(labels))}"
    if any(token in joined for token in ("econom", "fed", "interest rate", "finance", "business")):
        return "ECONOMY"
    if any(token in joined for token in ("crypto", "bitcoin", "ethereum")):
        return "CRYPTO"
    if any(token in joined for token in ("sport", "soccer", "football", "esport", "basketball")):
        return "SPORT"
    if any(token in joined for token in ("politic", "election", "government")):
        return "POLITICS"
    if any(token in joined for token in ("tech", "science", "ai")):
        return "TECH"
    return "MARKET"


def _market_candidate(raw, now_utc):
    if not isinstance(raw, dict) or raw.get("closed") is True or raw.get("active") is False:
        return None
    market_end = _parse_time(raw.get("endDate"))
    if market_end is not None and market_end < now_utc - timedelta(hours=6):
        return None

    outcomes = [_text(value) for value in _json_list(raw.get("outcomes"))]
    prices = [_number(value) for value in _json_list(raw.get("outcomePrices"))]
    if not outcomes or len(outcomes) != len(prices):
        return None
    if any(not outcome or price is None or not 0 <= price <= 1 for outcome, price in zip(outcomes, prices)):
        return None

    group_label = _text(raw.get("groupItemTitle"))
    normalized_outcomes = [outcome.casefold() for outcome in outcomes]
    raw_change = _number(raw.get("oneDayPriceChange"), 0.0)
    if group_label and "yes" in normalized_outcomes:
        selected_index = normalized_outcomes.index("yes")
        leader = group_label
    else:
        selected_index = max(range(len(prices)), key=prices.__getitem__)
        leader = outcomes[selected_index]
    change = raw_change if selected_index == 0 else -raw_change
    return {
        "leader": leader,
        "probability": prices[selected_index],
        "change_24h": change,
        "question": _text(raw.get("question")),
    }


def normalize_market_events(payload, now=None, limit=3):
    now_utc = _utc(now or datetime.now(timezone.utc))
    rows = []
    for event in payload if isinstance(payload, list) else []:
        if not isinstance(event, dict):
            continue
        event_id = _text(event.get("id"))
        title = _text(event.get("title"))
        if not event_id or not title or event.get("closed") is True or event.get("active") is False:
            continue
        event_end = _parse_time(event.get("endDate"))
        if event_end is not None and event_end < now_utc - timedelta(hours=6):
            continue

        event_markets = event.get("markets")
        event_markets = event_markets if isinstance(event_markets, list) else []
        candidates = [
            candidate
            for candidate in (
                _market_candidate(market, now_utc)
                for market in event_markets
            )
            if candidate is not None
        ]
        if not candidates:
            continue
        leader = max(candidates, key=lambda item: item["probability"])
        volume = max(0.0, _number(event.get("volume24hr"), 0.0))
        liquidity = max(0.0, _number(event.get("liquidity"), 0.0))
        score = heat_score(volume, leader["change_24h"])
        rows.append(
            {
                "id": event_id,
                "title": title,
                "category": _category_for_event(event),
                "leader": leader["leader"],
                "probability": leader["probability"],
                "change_24h": leader["change_24h"],
                "volume_24h": volume,
                "liquidity": liquidity,
                "heat": score,
                "heat_label": "HOT" if score >= 70 else "WARM" if score >= 45 else "CALM",
                "end_date": event_end.isoformat() if event_end is not None else "",
                "question": leader["question"],
            }
        )

    rows.sort(key=lambda row: (-row["volume_24h"], -row["heat"], row["title"].casefold()))
    return rows[: max(0, int(limit))]


def fetch_launches(session=None, now=None):
    session = session or get_http_session()
    response = session.get(
        LAUNCHES_URL,
        params={
            "limit": 4,
            "mode": "normal",
            "ordering": "net",
            "hide_recent_previous": "true",
        },
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    return normalize_launches(response.json(), now=now, limit=4)


def fetch_market_events(session=None, now=None):
    session = session or get_http_session()
    response = session.get(
        MARKETS_URL,
        params={
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
            "limit": 30,
        },
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    return normalize_market_events(response.json(), now=now, limit=3)
