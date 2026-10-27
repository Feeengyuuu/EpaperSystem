from __future__ import annotations
from utils.resource_cache import record_resource_event, measured_image_response as safe_open_image_response

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from plugins.box_office_top_movies.box_office_top_movies import (
    IMAGE_HEADERS,
    BoxOfficeTopMovies,
    _clean_text,
)
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.context_cache import write_context

from .week_page import HEADER_H, WeekPageMixin, category_color, category_key, event_category
from utils.cache_manager import CacheBudget
from utils.http_client import get_http_client, get_http_session

logger = logging.getLogger(__name__)

DISCOVERY_EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
# v3 adds the week's counts, the on-sale-soon list and each event's sale and tour facts. An event's
# age limit, family and multi-day flags are optional extras: a cache from before them shows none.
STATE_VERSION = "ticketmaster-events-v3"
# An age limit as the event's notes state it: "21+", "18 and over", "ages 21 and up".
AGE_PATTERN = re.compile(r"\b(21|18|16)\s*(?:\+|and\s+(?:over|up|older)|or\s+older)", re.IGNORECASE)
ALL_AGES_PATTERN = re.compile(r"\ball\s+ages\b", re.IGNORECASE)
# The page lists a featured event and LIST_EVENTS - 1 more, the soonest first.
MAX_ITEMS = LIST_EVENTS = 8
# Repeat showtimes collapse into one listing, so ask for enough rows to fill.
DISCOVERY_ROW_FLOOR = 20
# The week strip: each of WEEK_DAYS days is asked for its own count (the page total), with up
# to DAY_ROWS of its events for the mix of kinds and to fill the list.
WEEK_DAYS, DAY_ROWS = 7, 30
# The featured event is the biggest tour (most dates on Ticketmaster) among the listed events
# starting within FEATURE_HOURS, with real artwork.
FEATURE_HOURS = 48
# Events whose public sale opens within ON_SALE_DAYS, soonest first.
ON_SALE_DAYS, ON_SALE_ROWS, ON_SALE_SHOWN = 7, 20, 3
SEGMENT_KEYS = ("music", "sports", "arts & theatre", "film", "miscellaneous")
DEFAULT_CACHE_HOURS = 3
DISCOVERY_JSON_MAX_BYTES = 2 * 1024 * 1024
STATE_CACHE_BUDGET = CacheBudget(
    max_age_seconds=14 * 24 * 60 * 60,
    max_files=1,
    max_bytes=512 * 1024,
)
POSTER_CACHE_BUDGET = CacheBudget(
    max_age_seconds=14 * 24 * 60 * 60,
    max_files=64,
    max_bytes=16 * 1024 * 1024,
)
# Artwork for an event without a usable poster, by Ticketmaster segment; a
# segment with two pieces alternates them by event id.
PLACEHOLDER_DIR = Path(__file__).with_name("placeholders")
SEGMENT_PLACEHOLDERS = {
    "music": ("music_a", "music_b"),
    "sports": ("sports",),
    "arts & theatre": ("arts_theatre",),
    "film": ("film",),
}
DEFAULT_PLACEHOLDER = ("miscellaneous",)
DEFAULT_POSTAL_CODE = "94539"
DEFAULT_CITY = "Fremont"
DEFAULT_STATE_CODE = "CA"
DEFAULT_COUNTRY_CODE = "US"
ZIP_LATLONG_FALLBACKS = {
    "94539": "37.5202,-121.9264",
}
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; InkyPi TicketmasterEvents/1.0; "
        "+https://github.com/fatihak/InkyPi/)"
    ),
    "Accept": "application/json",
}


class MissingTicketmasterCredentials(RuntimeError):
    pass


def _classification_name(node):
    """Ticketmaster's literal "Undefined" category carries no information."""
    name = _clean_text(((node or {}).get("name") or ""))
    return "" if name.casefold() == "undefined" else name


def _is_category_art(item, url):
    """Generic segment art (swirls, bokeh) is flagged and served from /dam/c/."""
    flag = item.get("fallback")
    return flag is True or str(flag).strip().lower() == "true" or "/dam/c/" in url


@dataclass
class TicketmasterEvent:
    rank: int
    title: str
    event_id: str = ""
    local_date: str = ""
    local_time: str = ""
    venue_name: str = ""
    city: str = ""
    state_code: str = ""
    segment: str = ""
    genre: str = ""
    status: str = ""
    price: str = ""
    distance: str = ""
    url: str = ""
    image_url: str = ""
    poster_url: str = ""
    poster_path: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "rank": self.rank,
            "title": self.title,
            "event_id": self.event_id,
            "local_date": self.local_date,
            "local_time": self.local_time,
            "venue_name": self.venue_name,
            "city": self.city,
            "state_code": self.state_code,
            "segment": self.segment,
            "genre": self.genre,
            "status": self.status,
            "price": self.price,
            "distance": self.distance,
            "url": self.url,
            "image_url": self.image_url,
            "poster_url": self.poster_url,
            "poster_path": self.poster_path,
            "extra": dict(self.extra or {}),
        }

    @classmethod
    def from_dict(cls, data):
        payload = dict(data or {})
        payload["rank"] = int(payload.get("rank") or 0)
        return cls(**{key: payload.get(key) for key in cls.__dataclass_fields__})


class TicketmasterEvents(WeekPageMixin, BoxOfficeTopMovies):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        items_count = LIST_EVENTS
        cache_hours = self._bounded_int(settings.get("cacheHours"), DEFAULT_CACHE_HOURS, 1, 48)
        cache = self._read_cache()
        cache_key = self._cache_key(settings, dimensions, items_count)

        events, overview = [], {}
        source_label = "Ticketmaster Discovery"
        generated_at = self._now_for_device(device_config)
        stale = False
        provenance = SourceProvenance.LOCAL_FALLBACK

        theme_render_only = self._truthy(
            settings.get("_theme_render_only"),
            default=False,
        )
        force_refresh = self._truthy(
            settings.get("forceRefresh"),
            default=False,
        ) or self._truthy(
            settings.get("force_refresh"),
            default=False,
        )
        if theme_render_only:
            events = self._cached_events_for_key(cache, cache_key)
            generated_at = self._local_cached_datetime(
                cache.get("generated_at"),
                generated_at,
            ) or generated_at
            stale = bool(events) and not self._cache_is_fresh(
                cache,
                cache_key,
                cache_hours,
            )
            if not events:
                return self._local_fallback_image(
                    self._config_image(
                        dimensions,
                        "Events cache unavailable",
                        self._location_label(settings),
                    )
                )
            provenance = (
                SourceProvenance.STALE_CACHE
                if stale
                else SourceProvenance.FRESH_CACHE
            )
        elif not force_refresh and self._cache_is_fresh(
            cache,
            cache_key,
            cache_hours,
        ):
            events = [TicketmasterEvent.from_dict(item) for item in cache.get("events", [])]
            generated_at = self._local_cached_datetime(cache.get("generated_at"), generated_at) or generated_at
            provenance = SourceProvenance.FRESH_CACHE
        else:
            try:
                api_key = self._ticketmaster_api_key(settings, device_config)
                if not api_key:
                    raise MissingTicketmasterCredentials("Ticketmaster API key is not configured.")
                events, week, on_sale = self._load_overview(settings, api_key, generated_at)
                overview = {"week": week, "on_sale": on_sale}
                self._download_event_images(events, dimensions)
                generated_at = self._now_for_device(device_config)
                self._write_cache({
                    "version": STATE_VERSION,
                    "cache_key": cache_key,
                    "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
                    "events": [event.to_dict() for event in events],
                    "week": week,
                    "on_sale": on_sale,
                })
                provenance = SourceProvenance.LIVE
            except MissingTicketmasterCredentials as exc:
                events = self._cached_events_for_key(cache, cache_key)
                generated_at = self._local_cached_datetime(cache.get("generated_at"), generated_at) or generated_at
                stale = bool(events)
                if not events:
                    return self._local_fallback_image(
                        self._config_image(
                            dimensions,
                            "Ticketmaster API key required",
                            "Configure TICKETMASTER_API_KEY",
                        )
                    )
                provenance = SourceProvenance.STALE_CACHE
                logger.warning("Ticketmaster refresh skipped: %s", exc)
            except Exception as exc:
                logger.warning(
                    "Ticketmaster events refresh failed. | error_type: %s",
                    type(exc).__name__,
                )
                events = self._cached_events_for_key(cache, cache_key)
                generated_at = self._local_cached_datetime(cache.get("generated_at"), generated_at) or generated_at
                stale = bool(events)
                if events:
                    provenance = SourceProvenance.STALE_CACHE

        if not events:
            return self._local_fallback_image(
                self._config_image(
                    dimensions,
                    "No events found",
                    self._location_label(settings),
                )
            )

        events = events[:items_count]
        self._write_ticketmaster_context(events, generated_at, stale, settings)
        if not overview and cache.get("version") == STATE_VERSION and cache.get("cache_key") == cache_key:
            overview = cache
        image = self._render_events(
            dimensions,
            events,
            settings,
            source_label,
            generated_at,
            stale,
            week=(overview.get("week") if overview else None) or {},
            on_sale=(overview.get("on_sale") if overview else None) or [],
        )
        if provenance is SourceProvenance.STALE_CACHE:
            image.info["inkypi_skip_cache"] = True
        return attach_source_provenance(image, provenance)

    @staticmethod
    def _local_fallback_image(image):
        image.info["inkypi_skip_cache"] = True
        return attach_source_provenance(image, SourceProvenance.LOCAL_FALLBACK)

    def _request_param_candidates(self, settings, api_key, items_count):
        params = self._base_params(settings, api_key, items_count)
        yield params

        postal_code = self._postal_code(settings)
        latlong = ZIP_LATLONG_FALLBACKS.get(postal_code)
        if latlong:
            fallback = dict(params)
            fallback.pop("postalCode", None)
            fallback["latlong"] = latlong
            yield fallback

        if postal_code:
            fallback = dict(params)
            fallback.pop("postalCode", None)
            city = _clean_text(settings.get("city") or DEFAULT_CITY)
            state_code = _clean_text(settings.get("stateCode") or DEFAULT_STATE_CODE).upper()
            country_code = _clean_text(settings.get("countryCode") or DEFAULT_COUNTRY_CODE).upper()
            if city:
                fallback["city"] = city
            if state_code:
                fallback["stateCode"] = state_code
            if country_code:
                fallback["countryCode"] = country_code
            yield fallback

    def _fetch_ticketmaster_json(self, params):
        response = get_http_client().request_json(
            "GET",
            DISCOVERY_EVENTS_URL,
            params=params,
            timeout=18,
            headers=REQUEST_HEADERS,
            max_bytes=DISCOVERY_JSON_MAX_BYTES,
        )
        return response.data

    def _base_params(self, settings, api_key, items_count):
        now = datetime.now(timezone.utc)
        days_ahead = self._bounded_int(settings.get("daysAhead"), 7, 1, 180)
        params = {
            "apikey": api_key,
            "size": str(max(items_count * 4, DISCOVERY_ROW_FLOOR)),
            "sort": "date,asc",
            "unit": "miles",
            "radius": str(self._bounded_int(settings.get("radiusMiles"), 50, 1, 250)),
            "includeTBA": "no",
            "includeTBD": "no",
            "startDateTime": self._ticketmaster_datetime(now),
            "endDateTime": self._ticketmaster_datetime(now + timedelta(days=days_ahead)),
        }

        postal_code = self._postal_code(settings)
        city = _clean_text(settings.get("city") or DEFAULT_CITY)
        state_code = _clean_text(settings.get("stateCode") or DEFAULT_STATE_CODE).upper()
        country_code = _clean_text(settings.get("countryCode") or DEFAULT_COUNTRY_CODE).upper()
        keyword = _clean_text(settings.get("keyword") or "")
        classification = _clean_text(settings.get("classificationName") or "")

        if postal_code:
            params["postalCode"] = postal_code
        else:
            if city:
                params["city"] = city
            if state_code:
                params["stateCode"] = state_code
            if country_code:
                params["countryCode"] = country_code
        if keyword:
            params["keyword"] = keyword
        if classification:
            params["classificationName"] = classification
        return params

    @staticmethod
    def _ticketmaster_datetime(value):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _events_from_discovery(self, data, items_count):
        rows = (((data or {}).get("_embedded") or {}).get("events") or [])
        events = []
        seen = set()
        listings = {}
        for row in rows:
            title = _clean_text(row.get("name") or "")
            # Venues list their private bookings as "Private Event"; nobody can attend them.
            if not title or title.casefold().startswith("private event"):
                continue
            event_id = _clean_text(row.get("id") or "")
            dates = row.get("dates") or {}
            start = dates.get("start") or {}
            status = _clean_text(((dates.get("status") or {}).get("code") or ""))
            if status.lower() in {"cancelled", "canceled"}:
                continue

            venue = (((row.get("_embedded") or {}).get("venues") or [{}])[0]) or {}
            local_date = _clean_text(start.get("localDate") or "")
            key = event_id or f"{title}|{local_date}|{venue.get('name') or ''}"
            if key in seen:
                continue
            seen.add(key)
            # Rows arrive in date order, so a repeat of a listed show is a later showtime.
            listing = (title.casefold(), _clean_text(venue.get("name") or "").casefold(), local_date)
            if listing in listings:
                listed = listings[listing]
                listed.extra["more_showtimes"] = listed.extra.get("more_showtimes", 0) + 1
                continue
            if len(events) >= items_count:
                continue

            classification = ((row.get("classifications") or [{}])[0]) or {}
            image_url = self._event_artwork_url(row, venue)
            local_time = _clean_text(start.get("localTime") or "")
            if start.get("noSpecificTime") or start.get("timeTBA"):
                # Ticketmaster fills these with 00:00, which is not a real start time.
                local_time = ""
            event = TicketmasterEvent(
                rank=len(events) + 1,
                title=title,
                event_id=event_id,
                local_date=local_date,
                local_time=local_time,
                venue_name=_clean_text(venue.get("name") or ""),
                city=_clean_text(((venue.get("city") or {}).get("name") or "")),
                state_code=_clean_text(((venue.get("state") or {}).get("stateCode") or "")),
                segment=_classification_name(classification.get("segment")),
                genre=_classification_name(classification.get("genre")),
                status=status,
                price=self._price_text(row.get("priceRanges") or []),
                distance=self._distance_text(row.get("distance")),
                url=_clean_text(row.get("url") or ""),
                image_url=image_url,
                poster_url=image_url,
                extra={"timezone": start.get("timezone") or "", "source": "ticketmaster",
                       "subgenre": _classification_name(classification.get("subGenre")),
                       "sale_start": _clean_text((((row.get("sales") or {}).get("public") or {}).get("startDateTime") or "")),
                       "tour_dates": self._tour_dates(row),
                       "age": self._age_limit(row),
                       "family": classification.get("family") is True,
                       "multi_day": dates.get("spanMultipleDays") is True,
                       "has_art": bool(image_url)},
            )
            listings[listing] = event
            events.append(event)
        return events

    @staticmethod
    def _tour_dates(row):
        """How many dates the event's headliner has on Ticketmaster: the size of the tour."""
        attractions = ((row.get("_embedded") or {}).get("attractions") or [])
        try:
            return int((((attractions[0] or {}).get("upcomingEvents") or {}).get("_total")) or 0) if attractions else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _age_limit(row):
        """'21+' / '18+' from the event's notes, '限龄' when Ticketmaster only says an age limit is
        enforced, '全龄' for a show billed as all ages, else ""."""
        notes = " ".join(_clean_text(row.get(key) or "") for key in ("pleaseNote", "info"))
        match = AGE_PATTERN.search(notes)
        if match:
            return f"{match.group(1)}+"
        if ((row.get("ageRestrictions") or {}).get("legalAgeEnforced")) is True:
            return "限龄"
        return "全龄" if ALL_AGES_PATTERN.search(notes) else ""

    @staticmethod
    def _segment_key(row):
        name = _classification_name((((row.get("classifications") or [{}])[0] or {}).get("segment")))
        return name.casefold() if name.casefold() in SEGMENT_KEYS else "miscellaneous"

    @staticmethod
    def _category_key(row):
        classification = ((row.get("classifications") or [{}])[0] or {})
        return category_key(*(_classification_name(classification.get(name)) for name in ("segment", "genre", "subGenre")))

    def _load_overview(self, settings, api_key, now):
        """(events, week, on_sale): the soonest events, each of the next WEEK_DAYS days' count and
        mix of kinds, and the events whose public sale opens soon. The first response with events
        selects the location for later days and the on-sale list. Until then, each day retries the
        preferred locations; a failed later day or on-sale list is left out rather than failing the page."""
        today = now.date()
        base, rows, days = None, [], []
        for offset in range(WEEK_DAYS):
            day = today + timedelta(days=offset)
            start = f"{now:%H:%M}:00" if not offset else "00:00:00"
            window = {"localStartDateTime": f"{day:%Y-%m-%d}T{start},{day:%Y-%m-%d}T23:59:59", "size": str(DAY_ROWS)}
            data = None
            try:
                if base is None:
                    for params in self._request_param_candidates(settings, api_key, DAY_ROWS):
                        params = self._day_params(params, window)
                        data = self._fetch_ticketmaster_json(params)
                        # An empty evening must not pin later days to the city fallback.
                        if ((data or {}).get("_embedded") or {}).get("events"):
                            base = params
                            break
                else:
                    data = self._fetch_ticketmaster_json(self._day_params(base, window))
            except Exception as exc:
                if not offset:
                    raise
                logger.warning("Ticketmaster day %s unavailable. | error_type: %s", day, type(exc).__name__)
                days.append({"date": day.isoformat(), "count": None, "segments": {}})
                continue
            day_rows = (((data or {}).get("_embedded") or {}).get("events") or [])
            rows.extend(day_rows)
            segments, kinds = {}, {}
            for row in day_rows:
                key = self._segment_key(row)
                segments[key] = segments.get(key, 0) + 1
                kind = self._category_key(row)
                kinds[kind] = kinds.get(kind, 0) + 1
            total = self._safe_int(((data or {}).get("page") or {}).get("totalElements"), len(day_rows))
            days.append({"date": day.isoformat(), "count": total, "segments": segments,
                         "kind_counts": kinds, "sample_count": len(day_rows)})
        events = self._featured_first(self._events_from_discovery({"_embedded": {"events": rows}}, LIST_EVENTS), now)
        if not events:
            raise RuntimeError("Ticketmaster Discovery API returned no displayable events.")
        try:
            on_sale = self._load_on_sale(base, now)
        except Exception as exc:
            logger.warning("Ticketmaster on-sale list unavailable. | error_type: %s", type(exc).__name__)
            on_sale = []
        return events, {"days": days}, on_sale

    @staticmethod
    def _day_params(params, window):
        day = {key: value for key, value in params.items() if key not in ("startDateTime", "endDateTime")}
        return {**day, **window}

    def _featured_first(self, events, now):
        """The listed events with the featured one first: the biggest tour starting within
        FEATURE_HOURS with real artwork (the soonest event when none has art), then the rest by time."""
        if not events:
            return events
        horizon = (now + timedelta(hours=FEATURE_HOURS)).date().isoformat()
        candidates = [event for event in events if event.local_date <= horizon and (event.extra or {}).get("has_art")]
        featured = max(candidates, key=lambda event: (event.extra or {}).get("tour_dates") or 0) if candidates else events[0]
        ordered = [featured] + [event for event in events if event is not featured]
        for rank, event in enumerate(ordered, 1):
            event.rank = rank
        return ordered

    def _load_on_sale(self, base, now):
        """Events near by whose public sale opens within ON_SALE_DAYS, soonest sale first:
        [{title, venue, city, date, sale_start, segment}]."""
        if not base:
            return []
        params = {key: value for key, value in base.items()
                  if key not in ("startDateTime", "endDateTime", "localStartDateTime", "sort", "size")}
        params.update({"sort": "onSaleStartDate,asc", "size": str(ON_SALE_ROWS),
                       "onsaleStartDateTime": self._ticketmaster_datetime(now),
                       "onsaleEndDateTime": self._ticketmaster_datetime(now + timedelta(days=ON_SALE_DAYS))})
        rows = ((((self._fetch_ticketmaster_json(params) or {}).get("_embedded") or {}).get("events")) or [])
        listed, seen = [], set()
        for row in rows:
            title = _clean_text(row.get("name") or "")
            sale = _clean_text((((row.get("sales") or {}).get("public") or {}).get("startDateTime") or ""))
            sale_at = self._parse_datetime(sale)
            if not title or not sale_at or sale_at <= now or title.casefold() in seen:
                continue
            seen.add(title.casefold())
            venue = (((row.get("_embedded") or {}).get("venues") or [{}])[0]) or {}
            listed.append({"title": title, "venue": _clean_text(venue.get("name") or ""),
                           "city": _clean_text(((venue.get("city") or {}).get("name") or "")),
                           "date": _clean_text((((row.get("dates") or {}).get("start") or {}).get("localDate") or "")),
                           "sale_start": sale, "segment": self._segment_key(row), "kind": self._category_key(row)})
            if len(listed) >= ON_SALE_SHOWN:
                break
        return listed

    def _event_artwork_url(self, row, venue):
        """Prefer the event's own art, then its attraction's, then the venue's."""
        attractions = ((row.get("_embedded") or {}).get("attractions") or [])
        for images in [row.get("images") or []] + [
            (attraction or {}).get("images") or [] for attraction in attractions
        ] + [venue.get("images") or []]:
            url = self._best_image_url(images)
            if url:
                return url
        return ""

    def _best_image_url(self, images):
        best = None
        best_score = -1
        for item in images:
            url = _clean_text(item.get("url") or "")
            if not url or _is_category_art(item, url):
                continue
            width = self._safe_int(item.get("width"), 0)
            height = self._safe_int(item.get("height"), 0)
            score = width * height
            if _clean_text(item.get("ratio") or "") == "16_9":
                score += 2_000_000
            if score > best_score:
                best = url
                best_score = score
        return best or ""

    def _price_text(self, ranges):
        if not ranges:
            return ""
        first = ranges[0] or {}
        currency = _clean_text(first.get("currency") or "USD")
        symbol = "$" if currency.upper() == "USD" else f"{currency} "
        minimum = first.get("min")
        maximum = first.get("max")
        try:
            if minimum is not None and maximum is not None and float(minimum) != float(maximum):
                return f"{symbol}{float(minimum):.0f}-{float(maximum):.0f}"
            if minimum is not None:
                return f"From {symbol}{float(minimum):.0f}"
        except (TypeError, ValueError):
            return ""
        return ""

    def _distance_text(self, value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        return f"{number:.1f} mi"

    def _download_event_images(self, events, display_dimensions):
        poster_size = (
            max(1, int(display_dimensions[0])),
            max(1, int(display_dimensions[1])),
        )
        namespace = self._poster_cache_namespace()
        for event in events:
            if not event.image_url:
                continue
            try:
                key = self._event_image_cache_key(event)
                cached_path = namespace.get_path(key, suffix=".jpg")
                if cached_path is not None:
                    try:
                        cached_size = cached_path.stat().st_size
                    except OSError:
                        cached_size = 0
                    if cached_size > 0:
                        event.poster_path = str(cached_path)
                        record_resource_event("event_posters", disk_hits=1)
                        continue
                    namespace.remove(key, suffix=".jpg")
                response = get_http_session().get(
                    event.image_url,
                    timeout=18,
                    headers=IMAGE_HEADERS,
                    stream=True,
                )
                image = safe_open_image_response(
                    response,
                    draft_size=poster_size,
                )
                downloaded = image.info.get("resource_downloaded_bytes")
                record_resource_event("event_posters", downloads=1, **(
                    {"downloaded_bytes": downloaded} if downloaded is not None else {"unmeasured_downloads": 1}))
                try:
                    image.thumbnail(poster_size, Image.Resampling.LANCZOS)
                    if image.mode != "RGB":
                        converted = image.convert("RGB")
                        image.close()
                        image = converted
                    encoded = BytesIO()
                    image.save(encoded, format="JPEG", quality=88)
                finally:
                    image.close()
                path = namespace.put_bytes(
                    key,
                    encoded.getvalue(),
                    suffix=".jpg",
                )
                event.poster_path = str(path)
            except Exception as exc:
                logger.warning("Ticketmaster image download failed for %s: %s", event.title, exc)

    def _draw_header_wordmark(self, target, draw, x, y, max_width, max_height, colors):
        path = Path(__file__).with_name("header_wordmark.png")
        if not path.is_file():
            return None
        try:
            with Image.open(path) as wordmark:
                wordmark = wordmark.convert("RGBA")
                wordmark.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                if colors["mode"] == "dark":
                    alpha = wordmark.getchannel("A")
                    grayscale = ImageOps.grayscale(wordmark.convert("RGB"))
                    wordmark = ImageOps.colorize(
                        grayscale,
                        black=colors.get("wordmark_shadow", colors.get("accent", colors["ink"])),
                        white=colors.get("wordmark_highlight", colors["ink"]),
                    )
                    wordmark.putalpha(alpha)
                target.paste(wordmark, (x, y), wordmark)
                return y + wordmark.height
        except Exception as exc:
            logger.warning("Could not render Ticketmaster header wordmark: %s", exc)
            return None

    def _paste_event_image(self, target, event, box, colors):
        x, y, w, h = box
        image = self._load_event_image(event)
        if image is None:
            image = self._placeholder_event_image(event, (w, h), colors)
        image = ImageOps.fit(image.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)
        chromatic_media = colors.get("chromatic_media", colors["mode"] == "color")
        if colors["mode"] == "dark" and chromatic_media:
            image = ImageEnhance.Color(image).enhance(1.08)
            image = ImageEnhance.Contrast(image).enhance(1.10)
            image = ImageEnhance.Brightness(image).enhance(0.88)
        elif colors["mode"] == "dark":
            image = ImageOps.grayscale(image).convert("RGB")
        elif colors["mode"] == "color":
            image = ImageEnhance.Color(image).enhance(0.82)
            image = ImageEnhance.Contrast(image).enhance(1.08)
        target.paste(image, (x, y))
        draw = ImageDraw.Draw(target)
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=colors["outline"], width=1)

    def _load_event_image(self, event):
        path = event.poster_path or ""
        if not path or not Path(path).is_file():
            return None
        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except Exception:
            return None

    def _placeholder_event_image(self, event, size, colors):
        key = hashlib.sha256((event.event_id or event.title).encode("utf-8")).digest()
        choices = SEGMENT_PLACEHOLDERS.get((event.segment or "").strip().lower(), DEFAULT_PLACEHOLDER)
        try:
            with Image.open(PLACEHOLDER_DIR / f"{choices[key[3] % len(choices)]}.jpg") as artwork:
                return artwork.convert("RGB")
        except OSError:
            pass
        w, h = size
        base = (36 + key[0] // 7, 36 + key[1] // 8, 36 + key[2] // 8)
        if colors["mode"] == "paper":
            base = (180 + key[0] // 6, 176 + key[1] // 8, 166 + key[2] // 10)
        elif colors.get("chromatic_media", colors["mode"] == "color"):
            accent = self._event_color(event, colors)
            base = self._blend(colors["paper"], accent, 0.38)
        image = Image.new("RGB", size, base)
        draw = ImageDraw.Draw(image)
        if colors.get("chromatic_media", colors["mode"] == "color"):
            shade = self._blend(base, self._event_color(event, colors), 0.45)
            draw.rectangle((0, h - max(18, h // 7), w, h), fill=shade)
        else:
            shade = tuple(max(0, min(255, channel + 12)) for channel in base)
            draw.rectangle((0, h - max(12, h // 8), w, h), fill=shade)
        font = self._font(max(14, h // 8), bold=True)
        label = self._fit_text(draw, event.segment or "LIVE", font, max(20, w - 20))
        bbox = draw.textbbox((0, 0), label, font=font)
        label_color = colors.get("placeholder_ink", colors["ink"])
        draw.text(((w - (bbox[2] - bbox[0])) // 2, (h - (bbox[3] - bbox[1])) // 2), label, fill=label_color, font=font)
        return image

    def _venue_line(self, event):
        place = event.venue_name or "Venue TBA"
        city = ", ".join(value for value in [event.city, event.state_code] if value)
        if city:
            return f"{place} - {city}"
        return place

    def _event_color(self, event, colors):
        return category_color(event_category(event), colors)

    @staticmethod
    def _blend(first, second, ratio):
        return tuple(int(round(first[index] * (1 - ratio) + second[index] * ratio)) for index in range(3))

    def _wrap_text(self, draw, text, font, max_width, max_lines):
        lines = self._wrap_full_text(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return lines
        clipped = lines[:max_lines]
        clipped[-1] = self._fit_text(draw, clipped[-1] + " ...", font, max_width)
        return clipped

    def _wrap_full_text(self, draw, text, font, max_width):
        words = str(text or "").split()
        if not words:
            return [""]
        lines = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines or [""]

    def _draw_background(self, draw, dimensions, colors):
        width, height = dimensions
        draw.rectangle((0, 0, width, height), fill=colors["paper"])
        if colors["mode"] == "dark":
            return
        if colors["mode"] == "color":
            draw.rectangle((0, 0, width, HEADER_H), fill=colors["header_band"])
            draw.rectangle((0, HEADER_H, width, HEADER_H + 3), fill=colors["accent"])
            draw.rectangle((0, 0, 6, height), fill=colors["accent"])

    def _palette(self, settings):
        injected = settings.get("_inkypi_theme")
        if isinstance(injected, dict) and injected.get("mode") == "night":
            canonical = injected.get("palette") or {}
            paper = tuple(canonical.get("background", (229, 236, 232)))
            panel = tuple(canonical.get("panel", paper))
            ink = tuple(canonical.get("ink", (28, 43, 48)))
            muted = tuple(canonical.get("muted", (52, 72, 76)))
            line = tuple(canonical.get("rule", (175, 197, 194)))
            accent = tuple(canonical.get("accent", (177, 68, 53)))
            if injected["mode"] == "night":
                return {
                    "mode": "dark",
                    "chromatic_media": True,
                    "paper": paper,
                    "ink": ink,
                    "muted": muted,
                    "soft": muted,
                    "line": line,
                    "outline": ink,
                    "chip": accent,
                    "chip_ink": paper,
                    "title": ink,
                    "header_muted": muted,
                    "footer": muted,
                    "accent": accent,
                    "wordmark_shadow": accent,
                    "wordmark_highlight": ink,
                    "date_chip": accent,
                    "date_ink": paper,
                    "status_chip": accent,
                    "status_ink": paper,
                    "placeholder_ink": ink,
                    "shadow": panel,
                    "segment_colors": {
                        "music": (238, 106, 92),
                        "sports": (83, 159, 174),
                        "arts & theatre": (173, 126, 194),
                        "film": (126, 150, 203),
                        "miscellaneous": (229, 188, 88),
                        "undefined": muted,
                    },
                }
            return {
                "mode": "color",
                "chromatic_media": True,
                "paper": paper,
                "header_band": panel,
                "footer_band": panel,
                "title": ink,
                "ink": ink,
                "muted": muted,
                "header_muted": muted,
                "soft": muted,
                "footer": muted,
                "line": line,
                "outline": ink,
                "accent": accent,
                "chip": (229, 188, 88),
                "chip_ink": ink,
                "date_chip": (229, 188, 88),
                "date_ink": ink,
                "status_chip": (39, 112, 128),
                "status_ink": (244, 248, 242),
                "placeholder_ink": ink,
                "shadow": panel,
                "segment_colors": {
                    "music": accent,
                    "sports": (39, 112, 128),
                    "arts & theatre": (122, 83, 142),
                    "film": (78, 102, 153),
                    "miscellaneous": (191, 127, 48),
                    "undefined": (92, 118, 101),
                },
            }

        mode = (settings.get("themeMode") or "color").lower()
        if mode == "paper":
            return {
                "mode": "paper",
                "chromatic_media": False,
                "paper": (235, 232, 222),
                "ink": (28, 30, 32),
                "muted": (86, 88, 84),
                "soft": (112, 112, 104),
                "line": (210, 204, 190),
                "outline": (42, 42, 40),
                "chip": (31, 33, 35),
                "chip_ink": (246, 244, 235),
                "shadow": (224, 219, 207),
            }
        if mode == "dark":
            return {
                "mode": "dark",
                "chromatic_media": True,
                "paper": (0, 0, 0),
                "ink": (238, 238, 231),
                "muted": (176, 176, 168),
                "soft": (136, 138, 132),
                "line": (18, 18, 18),
                "outline": (213, 215, 208),
                "chip": (236, 236, 226),
                "chip_ink": (20, 22, 24),
                "wordmark_shadow": (229, 188, 88),
                "wordmark_highlight": (238, 238, 231),
                "shadow": (10, 12, 14),
            }
        return {
            "mode": "color",
            "chromatic_media": True,
            "paper": (229, 236, 232),
            "header_band": (207, 226, 220),
            "footer_band": (216, 228, 224),
            "title": (18, 49, 57),
            "ink": (28, 43, 48),
            "muted": (52, 72, 76),
            "header_muted": (56, 78, 82),
            "soft": (69, 88, 90),
            "footer": (88, 102, 103),
            "line": (175, 197, 194),
            "outline": (54, 76, 79),
            "accent": (177, 68, 53),
            "chip": (229, 188, 88),
            "chip_ink": (28, 43, 48),
            "date_chip": (229, 188, 88),
            "date_ink": (26, 41, 46),
            "status_chip": (39, 112, 128),
            "status_ink": (244, 248, 242),
            "placeholder_ink": (24, 42, 46),
            "shadow": (196, 213, 210),
            "segment_colors": {
                "music": (177, 68, 53),
                "sports": (39, 112, 128),
                "arts & theatre": (122, 83, 142),
                "film": (78, 102, 153),
                "miscellaneous": (191, 127, 48),
                "undefined": (92, 118, 101),
            },
        }

    def _location_label(self, settings):
        postal_code = self._postal_code(settings)
        if postal_code:
            return f"Within {self._bounded_int(settings.get('radiusMiles'), 50, 1, 250)} mi of {postal_code}"
        city = _clean_text(settings.get("city") or DEFAULT_CITY)
        state_code = _clean_text(settings.get("stateCode") or DEFAULT_STATE_CODE).upper()
        if city and state_code:
            return f"{city}, {state_code}"
        return city or "Local"

    def _config_image(self, dimensions, title, subtitle):
        width, height = dimensions
        image = Image.new("RGB", dimensions, (238, 236, 228))
        draw = ImageDraw.Draw(image)
        title_font = self._font(max(28, height // 13), bold=True)
        subtitle_font = self._font(max(16, height // 28), bold=True)
        body_font = self._font(max(13, height // 38))
        self._draw_centered(draw, "LOCAL EVENTS", width // 2, height // 2 - 72, title_font, (24, 26, 28))
        self._draw_centered(draw, title, width // 2, height // 2 - 18, subtitle_font, (24, 26, 28))
        self._draw_centered(draw, subtitle, width // 2, height // 2 + 24, body_font, (84, 84, 78))
        self._draw_centered(draw, "Ticketmaster Discovery API", width // 2, height // 2 + 68, body_font, (84, 84, 78))
        return image

    def _ticketmaster_api_key(self, settings, device_config=None):
        value = self._setting_secret(settings, "apiKey")
        if value:
            return value
        return self._env_secret(settings.get("apiKeyEnv"), [
            "TICKETMASTER_API_KEY",
            "TICKETMASTER_CONSUMER_KEY",
            "TICKETMASTER_DISCOVERY_API_KEY",
            "TICKETMASTER_KEY",
            "TICKETMASTER_DISCOVERY_KEY",
            "TM_API_KEY",
        ], device_config)

    def _read_cache(self):
        try:
            payload = self._state_cache_namespace().get_bytes(
                "ticketmaster_events_cache",
                suffix=".json",
            )
            if payload:
                return json.loads(payload.decode("utf-8"))
        except Exception as exc:
            logger.warning("Could not read Ticketmaster events cache: %s", exc)
        return {}

    def _write_cache(self, payload):
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        self._state_cache_namespace().put_bytes(
            "ticketmaster_events_cache",
            encoded,
            suffix=".json",
        )

    def _cache_is_fresh(self, cache, cache_key, cache_hours):
        if cache.get("version") != STATE_VERSION or cache.get("cache_key") != cache_key:
            return False
        generated = self._parse_datetime(cache.get("generated_at"))
        if not generated or not cache.get("events"):
            return False
        return datetime.now(timezone.utc) - generated.astimezone(timezone.utc) < timedelta(hours=cache_hours)

    def _cached_events_for_key(self, cache, cache_key):
        if cache.get("version") != STATE_VERSION or cache.get("cache_key") != cache_key:
            return []
        return [TicketmasterEvent.from_dict(item) for item in cache.get("events", [])]

    def _local_cached_datetime(self, value, local_now):
        parsed = self._parse_datetime(value)
        if not parsed:
            return None
        if getattr(local_now, "tzinfo", None):
            return parsed.astimezone(local_now.tzinfo)
        return parsed

    def _cache_key(self, settings, _dimensions, items_count):
        fields = [
            STATE_VERSION,
            str(items_count),
            self._postal_code(settings),
            settings.get("city") or DEFAULT_CITY,
            settings.get("stateCode") or DEFAULT_STATE_CODE,
            settings.get("countryCode") or DEFAULT_COUNTRY_CODE,
            str(self._bounded_int(settings.get("radiusMiles"), 50, 1, 250)),
            str(self._bounded_int(settings.get("daysAhead"), 7, 1, 180)),
            settings.get("keyword") or "",
            settings.get("classificationName") or "",
        ]
        return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()

    @staticmethod
    def _postal_code(settings):
        if "postalCode" in settings:
            return _clean_text(settings.get("postalCode") or "")
        return DEFAULT_POSTAL_CODE

    def _cache_path(self):
        return self._state_cache_namespace().path(
            "ticketmaster_events_cache",
            suffix=".json",
        )

    def _cache_dir(self):
        leaf = (
            "ticketmaster_events_cache"
            if os.getenv("INKYPI_CACHE_DIR", "").strip()
            else ".ticketmaster_events_cache"
        )
        return self.cache_dir(
            env_var="INKYPI_TICKETMASTER_EVENTS_CACHE",
            leaf=leaf,
            create=True,
        )

    def _state_cache_namespace(self):
        return self.managed_cache_namespace(
            self._cache_dir() / "state",
            STATE_CACHE_BUDGET,
        )

    def _poster_cache_namespace(self):
        return self.managed_cache_namespace(
            self._cache_dir() / "images",
            POSTER_CACHE_BUDGET,
        )

    def _event_image_cache_key(self, event):
        return hashlib.sha256(
            (event.image_url or event.event_id or event.title).encode("utf-8")
        ).hexdigest()[:18]

    def _write_ticketmaster_context(self, events, generated_at, stale, settings):
        cache_hours = self._bounded_int(
            settings.get("cacheHours"),
            DEFAULT_CACHE_HOURS,
            1,
            48,
        )
        write_context(
            "ticketmaster_events",
            {
                "kind": "ticketmaster_events",
                "source": "Ticketmaster Discovery API",
                "summary": "Upcoming local events: " + ", ".join(event.title for event in events[:3]),
                "facts": [
                    {"label": "location", "value": self._location_label(settings)},
                    {"label": "stale", "value": str(bool(stale)).lower()},
                    {"label": "cache_hours", "value": str(cache_hours)},
                ],
                "items": [event.to_dict() for event in events],
            },
            generated_at=generated_at,
            ttl_seconds=cache_hours * 60 * 60,
        )

    @staticmethod
    def _safe_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
