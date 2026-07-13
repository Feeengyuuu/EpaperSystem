from __future__ import annotations

import hashlib
import json
import logging
import os
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
from plugins.context_cache import write_context
from utils.cache_manager import CacheBudget
from utils.http_client import get_http_client, get_http_session
from utils.safe_image import safe_open_image_response

logger = logging.getLogger(__name__)

DISCOVERY_EVENTS_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
STATE_VERSION = "ticketmaster-events-v1"
MAX_ITEMS = 5
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


class TicketmasterEvents(BoxOfficeTopMovies):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        items_count = self._bounded_int(settings.get("itemsCount"), 5, 1, MAX_ITEMS)
        cache_hours = self._bounded_int(settings.get("cacheHours"), DEFAULT_CACHE_HOURS, 1, 48)
        cache = self._read_cache()
        cache_key = self._cache_key(settings, dimensions, items_count)

        events = []
        source_label = "Ticketmaster Discovery"
        generated_at = self._now_for_device(device_config)
        stale = False

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
                return self._config_image(
                    dimensions,
                    "Events cache unavailable",
                    self._location_label(settings),
                )
        elif not force_refresh and self._cache_is_fresh(
            cache,
            cache_key,
            cache_hours,
        ):
            events = [TicketmasterEvent.from_dict(item) for item in cache.get("events", [])]
            generated_at = self._local_cached_datetime(cache.get("generated_at"), generated_at) or generated_at
        else:
            try:
                api_key = self._ticketmaster_api_key(settings, device_config)
                if not api_key:
                    raise MissingTicketmasterCredentials("Ticketmaster API key is not configured.")
                events = self._load_events(settings, items_count, api_key)
                self._download_event_images(events)
                generated_at = self._now_for_device(device_config)
                self._write_cache({
                    "version": STATE_VERSION,
                    "cache_key": cache_key,
                    "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
                    "events": [event.to_dict() for event in events],
                })
            except MissingTicketmasterCredentials as exc:
                events = self._cached_events_for_key(cache, cache_key)
                generated_at = self._local_cached_datetime(cache.get("generated_at"), generated_at) or generated_at
                stale = bool(events)
                if not events:
                    return self._config_image(
                        dimensions,
                        "Ticketmaster API key required",
                        "Configure TICKETMASTER_API_KEY",
                    )
                logger.warning("Ticketmaster refresh skipped: %s", exc)
            except Exception as exc:
                logger.warning(
                    "Ticketmaster events refresh failed. | error_type: %s",
                    type(exc).__name__,
                )
                events = self._cached_events_for_key(cache, cache_key)
                generated_at = self._local_cached_datetime(cache.get("generated_at"), generated_at) or generated_at
                stale = bool(events)

        if not events:
            return self._config_image(dimensions, "No events found", self._location_label(settings))

        events = events[:items_count]
        self._write_ticketmaster_context(events, generated_at, stale, settings)
        return self._render_events(dimensions, events, settings, source_label, generated_at, stale)

    def _load_events(self, settings, items_count, api_key):
        for params in self._request_param_candidates(settings, api_key, items_count):
            data = self._fetch_ticketmaster_json(params)
            events = self._events_from_discovery(data, items_count)
            if events:
                return events
        raise RuntimeError("Ticketmaster Discovery API returned no displayable events.")

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
            "size": str(max(items_count * 2, 10)),
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
        for row in rows:
            title = _clean_text(row.get("name") or "")
            if not title:
                continue
            event_id = _clean_text(row.get("id") or "")
            dates = row.get("dates") or {}
            start = dates.get("start") or {}
            status = _clean_text(((dates.get("status") or {}).get("code") or ""))
            if status.lower() in {"cancelled", "canceled"}:
                continue

            venue = (((row.get("_embedded") or {}).get("venues") or [{}])[0]) or {}
            city = _clean_text(((venue.get("city") or {}).get("name") or ""))
            state_code = _clean_text(((venue.get("state") or {}).get("stateCode") or ""))
            classification = ((row.get("classifications") or [{}])[0]) or {}
            segment = _clean_text(((classification.get("segment") or {}).get("name") or ""))
            genre = _clean_text(((classification.get("genre") or {}).get("name") or ""))
            image_url = self._best_image_url(row.get("images") or [])
            key = event_id or f"{title}|{start.get('localDate') or ''}|{venue.get('name') or ''}"
            if key in seen:
                continue
            seen.add(key)

            event = TicketmasterEvent(
                rank=len(events) + 1,
                title=title,
                event_id=event_id,
                local_date=_clean_text(start.get("localDate") or ""),
                local_time=_clean_text(start.get("localTime") or ""),
                venue_name=_clean_text(venue.get("name") or ""),
                city=city,
                state_code=state_code,
                segment=segment,
                genre=genre,
                status=status,
                price=self._price_text(row.get("priceRanges") or []),
                distance=self._distance_text(row.get("distance")),
                url=_clean_text(row.get("url") or ""),
                image_url=image_url,
                poster_url=image_url,
                extra={"timezone": start.get("timezone") or "", "source": "ticketmaster"},
            )
            events.append(event)
            if len(events) >= items_count:
                break
        return events

    def _best_image_url(self, images):
        best = None
        best_score = -1
        for item in images:
            url = _clean_text(item.get("url") or "")
            if not url:
                continue
            width = self._safe_int(item.get("width"), 0)
            height = self._safe_int(item.get("height"), 0)
            ratio = _clean_text(item.get("ratio") or "")
            score = width * height
            if ratio == "16_9":
                score += 2_000_000
            if "RETINA" in _clean_text(item.get("fallback") or "").upper():
                score += 200_000
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

    def _download_event_images(self, events):
        namespace = self._poster_cache_namespace()
        for event in events:
            if not event.image_url:
                continue
            try:
                key = self._event_image_cache_key(event)
                path = namespace.path(key, suffix=".jpg")
                if namespace.get_bytes(key, suffix=".jpg"):
                    event.poster_path = str(path)
                    continue
                response = get_http_session().get(
                    event.image_url,
                    timeout=18,
                    headers=IMAGE_HEADERS,
                    stream=True,
                )
                image = safe_open_image_response(response).convert("RGB")
                encoded = BytesIO()
                image.save(encoded, format="JPEG", quality=88)
                path = namespace.put_bytes(
                    key,
                    encoded.getvalue(),
                    suffix=".jpg",
                )
                event.poster_path = str(path)
            except Exception as exc:
                logger.warning("Ticketmaster image download failed for %s: %s", event.title, exc)

    def _render_events(self, dimensions, events, settings, source_label, generated_at, stale=False):
        width, height = dimensions
        colors = self._palette(settings)
        image = Image.new("RGB", dimensions, colors["paper"])
        draw = ImageDraw.Draw(image)
        self._draw_background(draw, dimensions, colors)

        margin = max(16, width // 48)
        header_h = max(62, height // 8)
        footer_h = max(24, height // 20)
        title_font = self._font(max(26, height // 15), bold=True)
        subtitle_font = self._font(max(12, height // 38), bold=True)
        small_font = self._font(max(11, height // 44))
        hero_title_font = self._font(max(22, height // 21), bold=True)
        body_font = self._font(max(14, height // 32), bold=True)
        meta_font = self._font(max(12, height // 40), bold=True)
        chip_font = self._font(max(12, height // 42), bold=True)
        day_font = self._font(max(21, height // 20), bold=True)
        right_scale = 1.2
        list_body_font = self._font(int(round(max(14, height // 32) * right_scale)), bold=True)
        list_meta_font = self._font(int(round(max(12, height // 40) * right_scale)), bold=True)
        list_small_font = self._font(int(round(max(11, height // 44) * right_scale)), bold=True)
        list_chip_font = self._font(int(round(max(12, height // 42) * right_scale)), bold=True)
        list_day_font = self._font(int(round(max(14, height // 32) * right_scale)), bold=True)

        wordmark_bottom = self._draw_header_wordmark(image, draw, margin, max(4, margin - 11), int(width * 0.38), 44, colors)
        if wordmark_bottom is None:
            draw.text((margin, margin - 2), "LOCAL EVENTS", fill=colors.get("title", colors["ink"]), font=title_font)
            subtitle_y = margin + 35
        else:
            subtitle_y = max(margin + 35, wordmark_bottom + 1)
        refresh = self._refresh_label(settings)
        draw.text((margin, subtitle_y), f"{source_label} | {refresh}", fill=colors.get("header_muted", colors["muted"]), font=subtitle_font)
        location = self._fit_text(draw, self._location_label(settings), subtitle_font, width // 3)
        location_w = draw.textlength(location, font=subtitle_font)
        draw.text((width - margin - location_w, margin + 35), location, fill=colors.get("accent", colors["muted"]), font=subtitle_font)

        top = margin + header_h
        bottom = height - margin - footer_h
        hero = events[0]
        left_w = int(width * 0.36)
        hero_x = margin
        hero_img_w = left_w - margin
        hero_img_h = max(176, int(height * 0.43))
        self._paste_event_image(image, hero, (hero_x, top, hero_img_w, hero_img_h), colors)
        if colors["mode"] == "color":
            draw.rectangle((hero_x, top + hero_img_h - 9, hero_x + hero_img_w, top + hero_img_h), fill=self._event_color(hero, colors))
        self._draw_date_chip(draw, hero, hero_x + 10, top + 10, 72, 54, colors, chip_font, day_font)
        self._draw_status_chip(draw, hero.status or "onsale", hero_x + 10, top + hero_img_h - 32, colors, chip_font)

        hero_text_y = top + hero_img_h + 14
        hero_text_bottom = height - margin - footer_h - 10
        self._draw_left_event_text(draw, hero, hero_x, hero_text_y, hero_img_w, hero_text_bottom, colors)

        list_x = margin + left_w + 14
        list_w = width - list_x - margin
        list_title = f"NEXT {len(events)}"
        list_top = top - int(round(28 * right_scale))
        draw.text((list_x, list_top), list_title, fill=colors.get("title", colors["ink"]), font=list_body_font)
        row_top = list_top + int(round(42 * right_scale))
        row_count = max(1, len(events[1:]))
        row_h = max(int(round(72 * right_scale)), (bottom - row_top) // row_count)
        thumb_w = max(int(round(88 * right_scale)), int(round((width // 10) * right_scale)))
        thumb_h = max(int(round(52 * right_scale)), min(row_h - int(round(14 * right_scale)), int(thumb_w * 0.66)))
        date_w = int(round(42 * right_scale))
        date_h = int(round(48 * right_scale))
        thumb_gap = int(round(7 * right_scale))
        text_gap = int(round(10 * right_scale))

        for idx, event in enumerate(events[1:]):
            y = row_top + idx * row_h
            if idx:
                separator_y = y - int(round(9 * right_scale))
                draw.line((list_x, separator_y, width - margin, separator_y), fill=colors["line"], width=1)
            if colors["mode"] == "color":
                draw.rectangle((list_x - 6, y, list_x - 3, y + date_h), fill=self._event_color(event, colors))
            self._draw_date_chip(draw, event, list_x, y, date_w, date_h, colors, list_chip_font, list_day_font)
            thumb_x = list_x + date_w + thumb_gap
            self._paste_event_image(image, event, (thumb_x, y, thumb_w, thumb_h), colors)
            text_x = thumb_x + thumb_w + text_gap
            title = self._fit_text(draw, event.title, list_body_font, width - margin - text_x)
            draw.text((text_x, y - 1), title, fill=colors["ink"], font=list_body_font)
            venue_y = y + int(round(22 * right_scale))
            draw.text((text_x, venue_y), self._fit_text(draw, self._venue_line(event), list_meta_font, width - margin - text_x), fill=colors["muted"], font=list_meta_font)
            tag = self._tag_line(event)
            if tag:
                tag_y = y + int(round(43 * right_scale))
                draw.text((text_x, tag_y), self._fit_text(draw, tag, list_small_font, width - margin - text_x), fill=colors["soft"], font=list_small_font)

        footer = self._updated_text(generated_at, stale)
        draw.text((margin, height - margin - footer_h + 8), footer, fill=colors.get("footer", colors["muted"]), font=small_font)
        source_w = draw.textlength("Ticketmaster Discovery API", font=small_font)
        draw.text((width - margin - source_w, height - margin - footer_h + 8), "Ticketmaster Discovery API", fill=colors.get("footer", colors["muted"]), font=small_font)
        return image

    def _draw_header_wordmark(self, target, draw, x, y, max_width, max_height, colors):
        path = Path(__file__).with_name("header_wordmark.png")
        if not path.is_file():
            return None
        try:
            with Image.open(path) as wordmark:
                wordmark = wordmark.convert("RGBA")
                wordmark.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                if colors["mode"] == "dark":
                    tint = Image.new("RGBA", wordmark.size, colors["ink"] + (0,))
                    tint.putalpha(wordmark.getchannel("A"))
                    wordmark = tint
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
        if colors["mode"] == "dark":
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
        w, h = size
        key = hashlib.sha256((event.event_id or event.title).encode("utf-8")).digest()
        base = (36 + key[0] // 7, 36 + key[1] // 8, 36 + key[2] // 8)
        if colors["mode"] == "paper":
            base = (180 + key[0] // 6, 176 + key[1] // 8, 166 + key[2] // 10)
        elif colors["mode"] == "color":
            accent = self._event_color(event, colors)
            base = self._blend(colors["paper"], accent, 0.38)
        image = Image.new("RGB", size, base)
        draw = ImageDraw.Draw(image)
        if colors["mode"] == "color":
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

    def _draw_date_chip(self, draw, event, x, y, w, h, colors, month_font, day_font):
        month, day = self._date_parts(event.local_date)
        fill = colors.get("date_chip", colors["chip"])
        ink = colors.get("date_ink", colors["chip_ink"])
        draw.rectangle((x, y, x + w, y + h), fill=fill, outline=colors["outline"])
        month_w = draw.textlength(month, font=month_font)
        day_w = draw.textlength(day, font=day_font)
        draw.text((x + (w - month_w) / 2, y + 6), month, fill=ink, font=month_font)
        draw.text((x + (w - day_w) / 2, y + h - 30), day, fill=ink, font=day_font)

    def _draw_status_chip(self, draw, status, x, y, colors, font):
        label = (status or "onsale").upper()
        label = "ON SALE" if label == "ONSALE" else label[:12]
        padding_x = 9
        width = int(draw.textlength(label, font=font)) + padding_x * 2
        fill = colors.get("status_chip", colors["chip"])
        ink = colors.get("status_ink", colors["chip_ink"])
        draw.rectangle((x, y, x + width, y + 23), fill=fill, outline=colors["outline"])
        draw.text((x + padding_x, y + 4), label, fill=ink, font=font)

    def _date_parts(self, local_date):
        try:
            parsed = datetime.strptime(local_date, "%Y-%m-%d")
        except Exception:
            return "TBA", "--"
        return parsed.strftime("%b").upper(), parsed.strftime("%d").lstrip("0") or "0"

    def _venue_line(self, event):
        place = event.venue_name or "Venue TBA"
        city = ", ".join(value for value in [event.city, event.state_code] if value)
        if city:
            return f"{place} - {city}"
        return place

    def _detail_line(self, event):
        pieces = [self._time_text(event.local_time), event.price, self._tag_line(event)]
        return " | ".join(piece for piece in pieces if piece)

    def _draw_left_event_text(self, draw, event, x, y, max_width, bottom, colors):
        title_text = event.title or "Untitled event"
        venue_text = self._venue_line(event)
        detail_text = self._detail_line(event)

        for title_size in range(26, 15, -1):
            title_font = self._font(title_size, bold=True)
            venue_font = self._font(max(13, title_size - 7), bold=True)
            detail_font = self._font(max(11, title_size - 10), bold=True)
            title_lines = self._wrap_full_text(draw, title_text, title_font, max_width)
            venue_lines = self._wrap_full_text(draw, venue_text, venue_font, max_width)
            detail_lines = self._wrap_full_text(draw, detail_text, detail_font, max_width) if detail_text else []
            title_lh = title_size + 5
            venue_lh = max(16, title_size - 3)
            detail_lh = max(14, title_size - 6)
            needed = len(title_lines) * title_lh + 6 + len(venue_lines) * venue_lh
            if detail_lines:
                needed += 4 + len(detail_lines) * detail_lh
            if y + needed <= bottom or title_size == 16:
                cursor = y
                for line in title_lines:
                    draw.text((x, cursor), line, fill=colors["ink"], font=title_font)
                    cursor += title_lh
                cursor += 6
                for line in venue_lines:
                    draw.text((x, cursor), line, fill=colors["muted"], font=venue_font)
                    cursor += venue_lh
                if detail_lines:
                    cursor += 4
                    for line in detail_lines:
                        draw.text((x, cursor), line, fill=colors["soft"], font=detail_font)
                        cursor += detail_lh
                return

    def _event_color(self, event, colors):
        segment = (event.segment or "").strip().lower()
        return (colors.get("segment_colors") or {}).get(segment, colors.get("accent", colors["line"]))

    @staticmethod
    def _blend(first, second, ratio):
        return tuple(int(round(first[index] * (1 - ratio) + second[index] * ratio)) for index in range(3))

    def _tag_line(self, event):
        pieces = []
        if event.segment:
            pieces.append(event.segment)
        if event.genre and event.genre.lower() != event.segment.lower():
            pieces.append(event.genre)
        if event.distance:
            pieces.append(event.distance)
        return " / ".join(pieces)

    def _time_text(self, value):
        if not value:
            return ""
        try:
            parsed = datetime.strptime(value[:8], "%H:%M:%S")
        except Exception:
            return value[:5]
        return parsed.strftime("%-I:%M %p") if os.name != "nt" else parsed.strftime("%#I:%M %p")

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
        if colors["mode"] == "dark":
            draw.rectangle((0, 0, width, height), fill=colors["paper"])
            return
        if colors["mode"] == "color":
            draw.rectangle((0, 0, width, height), fill=colors["paper"])
            draw.rectangle((0, 0, width, 74), fill=colors["header_band"])
            draw.rectangle((0, 74, width, 80), fill=colors["accent"])
            draw.rectangle((0, height - 42, width, height), fill=colors["footer_band"])
            draw.rectangle((0, 0, 10, height), fill=colors["accent"])
            return
        for y in range(0, height, 48):
            draw.line((0, y, width, y), fill=colors["line"], width=1)

    def _palette(self, settings):
        injected = settings.get("_inkypi_theme")
        if isinstance(injected, dict) and injected.get("mode") in {"day", "night"}:
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
                    "date_chip": accent,
                    "date_ink": paper,
                    "status_chip": accent,
                    "status_ink": paper,
                    "placeholder_ink": ink,
                    "shadow": panel,
                    "segment_colors": {
                        "music": accent,
                        "sports": (83, 159, 174),
                        "arts & theatre": (173, 126, 194),
                        "film": (126, 150, 203),
                        "miscellaneous": (229, 188, 88),
                        "undefined": muted,
                    },
                }
            return {
                "mode": "color",
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
                "paper": (0, 0, 0),
                "ink": (238, 238, 231),
                "muted": (176, 176, 168),
                "soft": (136, 138, 132),
                "line": (18, 18, 18),
                "outline": (213, 215, 208),
                "chip": (236, 236, 226),
                "chip_ink": (20, 22, 24),
                "shadow": (10, 12, 14),
            }
        return {
            "mode": "color",
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

    def _updated_text(self, generated_at, stale):
        when = generated_at.strftime("%m/%d %H:%M") if isinstance(generated_at, datetime) else ""
        prefix = "STALE " if stale else "Updated "
        return f"{prefix}{when}".strip()

    def _refresh_label(self, settings):
        cache_hours = self._bounded_int(
            settings.get("cacheHours"),
            DEFAULT_CACHE_HOURS,
            1,
            48,
        )
        return f"{cache_hours}H REFRESH"

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
        return self.cache_dir(env_var="INKYPI_TICKETMASTER_EVENTS_CACHE", leaf=".ticketmaster_events_cache", create=True)

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

    def _event_image_cache_path(self, event):
        return self._poster_cache_namespace().path(
            self._event_image_cache_key(event),
            suffix=".jpg",
        )

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
