from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import random
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.safe_image import safe_open_image_response

try:
    import pytz
except Exception:  # pragma: no cover - pytz is present in the app runtime.
    pytz = None

logger = logging.getLogger(__name__)

GCD_BASE_URL = "https://www.comics.org"
COMIC_VINE_BASE_URL = "https://comicvine.gamespot.com/api"
STATE_VERSION = "gcd-comic-covers-state-v1"
MONTH_CACHE_VERSION = "gcd-comic-covers-month-v1"
ISSUE_CACHE_VERSION = "gcd-comic-covers-issue-v1"
COMIC_VINE_CACHE_VERSION = "gcd-comic-covers-comic-vine-v1"
MONTH_CACHE_TTL = timedelta(days=180)
ISSUE_CACHE_TTL = timedelta(days=365)
COMIC_VINE_CACHE_TTL = timedelta(hours=12)
MAX_MONTH_PAGES = 4
MAX_WEEKLY_API_PAGES = 2
MIN_CANDIDATES_BEFORE_BACKFILL_PAUSE = 120
DEFAULT_START_YEAR = 1938
DEFAULT_MAX_YEARS_PER_REFRESH = 10
DEFAULT_MAX_COVER_ATTEMPTS = 8
DEFAULT_COMIC_VINE_LIMIT = 24
DEFAULT_FIT_MODE = "triptych"
DEFAULT_SOURCE_MODE = "mixed"
TRIPTYCH_COVER_COUNT = 3
TRIPTYCH_FIT_MODES = {"triptych", "three_vertical", "three_covers", "three_posters", "gallery"}
GCD_API_CONNECT_TIMEOUT_SECONDS = 5
GCD_API_READ_TIMEOUT_SECONDS = 10
GCD_COVER_CONNECT_TIMEOUT_SECONDS = 5
GCD_COVER_READ_TIMEOUT_SECONDS = 8
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; InkyPi GCDComicCovers/1.0; "
        "+https://github.com/fatihak/InkyPi/)"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
}
JSON_HEADERS = {
    "User-Agent": REQUEST_HEADERS["User-Agent"],
    "Accept": "application/json",
}
COMIC_VINE_HEADERS = {
    "User-Agent": "InkyPi GCDComicCovers/1.0",
    "Accept": "application/json",
}
IMAGE_HEADERS = {
    "User-Agent": REQUEST_HEADERS["User-Agent"],
    "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
    "Referer": GCD_BASE_URL,
}
COMIC_VINE_ENV_KEYS = (
    "COMIC_VINE_API_KEY",
    "COMICVINE_API_KEY",
    "COMIC_VINE_KEY",
    "COMICVINE_KEY",
    "Comic_Vine",
    "Comic_Vine_Key",
    "ComicVine",
    "COMIC_VINE",
)


class GcdCoverImageUnavailable(RuntimeError):
    def __init__(self, message, candidate, detail, cover_url):
        super().__init__(message)
        self.candidate = dict(candidate or {})
        self.detail = dict(detail or {})
        self.cover_url = cover_url


class _GcdMonthlyParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.rows = []
        self._row = None
        self._link_href = None
        self._link_text = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "tr":
            self._row = {
                "text": [],
                "issue_id": "",
                "issue_label": "",
                "page_url": "",
                "cover_url": "",
                "country": "",
            }
            return

        if self._row is None:
            return

        if tag == "a":
            self._link_href = urljoin(self.base_url, attrs.get("href") or "")
            self._link_text = []
            return

        if tag == "img":
            alt = (attrs.get("alt") or "").strip()
            src = attrs.get("src") or attrs.get("data-src")
            if alt:
                self._row["text"].append(alt)
                country = _normalize_country_code(alt)
                if country and not self._row["country"]:
                    self._row["country"] = country
            if src and self._looks_like_cover(src, alt):
                self._row["cover_url"] = urljoin(self.base_url, src)

    def handle_data(self, data):
        if self._row is not None:
            self._row["text"].append(data)
        if self._link_href is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a" and self._link_href is not None and self._row is not None:
            issue_id = _issue_id_from_url(self._link_href)
            if issue_id:
                self._row["issue_id"] = issue_id
                self._row["page_url"] = self._link_href
                self._row["issue_label"] = _clean_text(" ".join(self._link_text))
            self._link_href = None
            self._link_text = []
            return

        if tag == "tr" and self._row is not None:
            row = self._row
            self._row = None
            if row.get("issue_id"):
                self.rows.append(row)

    def _looks_like_cover(self, src, alt):
        haystack = f"{src} {alt}".lower()
        if any(token in haystack for token in ["logo", "sprite", "favicon", "flag"]):
            return False
        return any(token in haystack for token in ["cover", "preview", "/covers", ".jpg", ".jpeg", ".png", ".webp"])


class GcdComicCovers(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["default_end_year"] = date.today().year
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        self._device_config = device_config
        dimensions = self._display_dimensions(device_config)
        today = self._current_date(device_config)
        candidates = self._candidate_pool(settings, today)
        if not candidates:
            return self._fallback_image(dimensions, "GCD Comic Covers", "No covers found for this date")

        state = self._read_state()
        ordered = self._candidate_order(candidates, state, today)
        attempt_limit = self._bounded_int(settings.get("maxCoverAttempts"), DEFAULT_MAX_COVER_ATTEMPTS, 1, 30)

        if self._fit_mode(settings) in TRIPTYCH_FIT_MODES:
            return self._generate_triptych_image(ordered, state, today, dimensions, settings, attempt_limit)

        return self._generate_single_cover_image(ordered, state, today, dimensions, settings, attempt_limit)

    def _generate_single_cover_image(self, ordered, state, today, dimensions, settings, attempt_limit):
        errors = []
        image_unavailable_cover = None
        detail_unavailable_cover = None
        for candidate in ordered[:attempt_limit]:
            try:
                cover = self._load_cover(candidate, dimensions, settings)
                image = self._fit_cover(cover["image"], dimensions, settings, cover)
                self._mark_seen(state, today, cover)
                self._write_state(state)
                self._write_cover_context(cover)
                logger.info(
                    "Selected GCD comic cover: %s #%s (%s) | %s",
                    cover.get("series_name") or "Unknown series",
                    cover.get("issue_number") or "",
                    cover.get("date_label") or "",
                    cover.get("cover_url"),
                )
                return image
            except GcdCoverImageUnavailable as exc:
                cover = self._cover_from_unavailable_image(exc)
                if image_unavailable_cover is None:
                    image_unavailable_cover = cover
                errors.append(f"{candidate.get('issue_id')}: {exc}")
                logger.warning("GCD cover candidate failed for issue %s: %s", candidate.get("issue_id"), exc)
            except Exception as exc:
                if detail_unavailable_cover is None and self._has_candidate_metadata(candidate):
                    detail_unavailable_cover = self._cover_from_candidate_metadata(candidate)
                errors.append(f"{candidate.get('issue_id')}: {exc}")
                logger.warning("GCD cover candidate failed for issue %s: %s", candidate.get("issue_id"), exc)

        if image_unavailable_cover:
            self._mark_seen(state, today, image_unavailable_cover)
            self._write_state(state)
            self._write_cover_context(image_unavailable_cover)
            logger.warning(
                "Using GCD metadata cover because source cover images were unavailable. | "
                "plugin_instance: GCD Comic Covers | issue_id: %s | cover_url: %s",
                image_unavailable_cover.get("issue_id"),
                image_unavailable_cover.get("cover_url"),
            )
            return self._metadata_cover_image(dimensions, settings, image_unavailable_cover)

        if detail_unavailable_cover:
            self._mark_seen(state, today, detail_unavailable_cover)
            self._write_state(state)
            self._write_cover_context(detail_unavailable_cover)
            logger.warning(
                "Using GCD candidate metadata cover because issue details were unavailable. | "
                "plugin_instance: GCD Comic Covers | issue_id: %s",
                detail_unavailable_cover.get("issue_id"),
            )
            return self._metadata_cover_image(dimensions, settings, detail_unavailable_cover)

        if len(ordered) > attempt_limit:
            errors.append(f"stopped after {attempt_limit} of {len(ordered)} candidates")
        detail = "; ".join(errors[-4:])
        logger.warning("No usable GCD comic cover could be rendered. %s", detail)
        return self._fallback_image(dimensions, "GCD Comic Covers", "No usable cover image")

    def _generate_triptych_image(self, ordered, state, today, dimensions, settings, attempt_limit):
        errors = []
        covers = []
        wide_fallback_covers = []
        image_unavailable_cover = None
        detail_unavailable_cover = None

        for candidate in ordered[:attempt_limit]:
            try:
                cover = self._load_cover(candidate, dimensions, settings)
                if self._is_wide_triptych_cover(cover):
                    wide_fallback_covers.append(cover)
                    continue
                covers.append(cover)
                if len(covers) >= TRIPTYCH_COVER_COUNT:
                    break
            except GcdCoverImageUnavailable as exc:
                cover = self._cover_from_unavailable_image(exc)
                if image_unavailable_cover is None:
                    image_unavailable_cover = cover
                errors.append(f"{candidate.get('issue_id')}: {exc}")
                logger.warning("GCD cover candidate failed for issue %s: %s", candidate.get("issue_id"), exc)
            except Exception as exc:
                if detail_unavailable_cover is None and self._has_candidate_metadata(candidate):
                    detail_unavailable_cover = self._cover_from_candidate_metadata(candidate)
                errors.append(f"{candidate.get('issue_id')}: {exc}")
                logger.warning("GCD cover candidate failed for issue %s: %s", candidate.get("issue_id"), exc)

        if len(covers) < TRIPTYCH_COVER_COUNT and wide_fallback_covers:
            covers.extend(wide_fallback_covers[:TRIPTYCH_COVER_COUNT - len(covers)])

        if covers:
            for cover in covers:
                self._mark_seen(state, today, cover)
            self._write_state(state)
            self._write_cover_context(covers)
            logger.info(
                "Selected GCD comic cover triptych: %s",
                " | ".join(self._label_text(cover) for cover in covers),
            )
            return self._compose_triptych_display_image(covers, dimensions, settings)

        if image_unavailable_cover:
            self._mark_seen(state, today, image_unavailable_cover)
            self._write_state(state)
            self._write_cover_context(image_unavailable_cover)
            logger.warning(
                "Using GCD metadata cover because source cover images were unavailable. | "
                "plugin_instance: GCD Comic Covers | issue_id: %s | cover_url: %s",
                image_unavailable_cover.get("issue_id"),
                image_unavailable_cover.get("cover_url"),
            )
            return self._metadata_cover_image(dimensions, settings, image_unavailable_cover)

        if detail_unavailable_cover:
            self._mark_seen(state, today, detail_unavailable_cover)
            self._write_state(state)
            self._write_cover_context(detail_unavailable_cover)
            logger.warning(
                "Using GCD candidate metadata cover because issue details were unavailable. | "
                "plugin_instance: GCD Comic Covers | issue_id: %s",
                detail_unavailable_cover.get("issue_id"),
            )
            return self._metadata_cover_image(dimensions, settings, detail_unavailable_cover)

        if len(ordered) > attempt_limit:
            errors.append(f"stopped after {attempt_limit} of {len(ordered)} candidates")
        detail = "; ".join(errors[-4:])
        logger.warning("No usable GCD comic cover triptych could be rendered. %s", detail)
        return self._fallback_image(dimensions, "GCD Comic Covers", "No usable cover image")

    def _write_cover_context(self, cover):
        covers = cover if isinstance(cover, list) else [cover]
        covers = [item for item in covers if isinstance(item, dict)]
        if not covers:
            return

        titles = []
        items = []
        facts = []
        for index, item in enumerate(covers[:TRIPTYCH_COVER_COUNT], start=1):
            series = str(item.get("series_name") or "Comic").strip()
            number = str(item.get("issue_number") or "").strip()
            date_label = str(item.get("date_label") or "").strip()
            title = series if not number else f"{series} #{number}"
            if date_label:
                title = f"{title} ({date_label})"
            titles.append(title)
            if len(covers) == 1:
                facts.extend([
                    {"label": "series", "value": series[:90]},
                    {"label": "issue", "value": number[:40]},
                    {"label": "date", "value": date_label[:40]},
                ])
            else:
                facts.extend([
                    {"label": f"series_{index}", "value": series[:90]},
                    {"label": f"issue_{index}", "value": number[:40]},
                    {"label": f"date_{index}", "value": date_label[:40]},
                ])
            items.append({
                "issue_id": item.get("issue_id"),
                "series": series[:120],
                "issue_number": number[:60],
                "date": date_label,
                "page_url": item.get("page_url"),
                "image_url": item.get("cover_url"),
            })

        summary_prefix = "GCD comic covers" if len(covers) > 1 else "GCD comic cover"
        summary = f"{summary_prefix}: {'; '.join(titles)}"

        write_context(
            "gcd_comic_covers",
            {
                "kind": "comic_cover",
                "source": "Grand Comics Database",
                "summary": summary[:180],
                "facts": facts,
                "items": items,
            },
            generated_at=datetime.now(timezone.utc),
            ttl_seconds=24 * 60 * 60,
        )

    def _display_dimensions(self, device_config):
        return self.get_dimensions(device_config)

    def _current_date(self, device_config):
        timezone_name = device_config.get_config("timezone", default=None)
        if timezone_name and pytz:
            try:
                return datetime.now(pytz.timezone(timezone_name)).date()
            except Exception:
                logger.warning("Invalid timezone for GCD comic cover date: %s", timezone_name)
        return date.today()

    def _candidate_pool(self, settings, today):
        source_mode = self._source_mode(settings)
        if source_mode == "comicvine":
            return self._comic_vine_candidate_pool(settings, today)

        gcd_candidates = self._gcd_candidate_pool(settings, today)
        if source_mode != "mixed":
            return gcd_candidates

        try:
            comic_vine_candidates = self._comic_vine_candidate_pool(settings, today)
        except Exception as exc:
            logger.warning("Comic Vine candidate fetch failed; using GCD candidates only: %s", exc)
            comic_vine_candidates = []
        return self._dedupe_candidates(comic_vine_candidates + gcd_candidates)

    def _gcd_candidate_pool(self, settings, today):
        years = self._target_years(settings, today)
        month = today.month
        candidates = []
        missing_years = []

        for year in years:
            target_date = self._target_date_for_year(year, today)
            cached = self._read_month_cache(target_date.year, target_date.month, target_date.day)
            if cached is None:
                missing_years.append(target_date)
            else:
                candidates.extend(cached)

        if missing_years:
            fetch_targets = self._prioritized_missing_dates(missing_years, today)
            fetch_limit = self._bounded_int(
                settings.get("maxYearsPerRefresh"),
                DEFAULT_MAX_YEARS_PER_REFRESH,
                1,
                50,
            )
            for target_date in fetch_targets[:fetch_limit]:
                if candidates and len(self._dedupe_candidates(candidates)) >= MIN_CANDIDATES_BEFORE_BACKFILL_PAUSE:
                    break
                try:
                    fetched = self._fetch_month_candidates(target_date.year, target_date.month, target_date.day)
                    self._write_month_cache(target_date.year, target_date.month, fetched, target_date.day)
                    candidates.extend(fetched)
                except Exception as exc:
                    logger.warning(
                        "Could not fetch GCD on-sale candidates for %s: %s",
                        target_date.isoformat(),
                        exc,
                    )
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    if status_code in {403, 429}:
                        break

        return self._dedupe_candidates(self._filter_candidates(candidates, settings, today))

    def _prioritized_missing_dates(self, target_dates, today):
        current_year = []
        older_years = []
        for target_date in target_dates:
            if target_date.year == today.year:
                current_year.append(target_date)
            else:
                older_years.append(target_date)
        random.shuffle(older_years)
        return current_year + older_years

    def _target_years(self, settings, today=None):
        start_year = self._bounded_int(settings.get("startYear"), DEFAULT_START_YEAR, 1933, 2050)
        default_end_year = (today or date.today()).year
        end_year = self._bounded_int(settings.get("endYear"), default_end_year, 1933, 2050)
        if end_year < start_year:
            start_year, end_year = end_year, start_year
        years = list(range(start_year, end_year + 1))
        if len(years) > 150:
            years = years[:150]
        return years

    def _target_date_for_year(self, year, today):
        try:
            return date(int(year), today.month, today.day)
        except ValueError:
            if today.month == 2 and today.day == 29:
                return date(int(year), 2, 28)
            raise

    def _filter_candidates(self, candidates, settings, today):
        country_codes = self._normalize_csv(settings.get("countryCodes") or "us")
        filtered = []
        for candidate in candidates:
            issue_id = str(candidate.get("issue_id") or "").strip()
            if not issue_id:
                continue

            if candidate.get("source") == "comicvine" and str(candidate.get("match_quality") or "").startswith("comicvine"):
                filtered.append(dict(candidate))
                continue

            country = _normalize_country_code(candidate.get("country"))
            if country_codes and country and country not in country_codes:
                continue

            date_text = self._candidate_date(candidate)
            if self._is_future_candidate_date(date_text, today):
                continue

            quality = self._date_match_quality(date_text, today)
            if not quality:
                continue

            item = dict(candidate)
            item["match_quality"] = quality
            filtered.append(item)
        return filtered

    def _is_future_candidate_date(self, date_text, today):
        parsed = _date_parts(date_text)
        if not parsed:
            return False
        year, month, day = parsed
        if day > 0:
            try:
                return date(year, month, day) > today
            except ValueError:
                return False
        return (year, month) > (today.year, today.month)

    def _date_match_quality(self, date_text, today):
        parsed = _date_parts(date_text)
        if not parsed:
            return None
        _year, month, day = parsed
        if month != today.month:
            return None
        if day == today.day:
            return "exact_day"
        return "month_fallback"

    def _candidate_order(self, candidates, state, today):
        date_key = today.strftime("%m-%d")
        bucket = state.setdefault("date_buckets", {}).setdefault(date_key, {})
        seen = {str(value) for value in bucket.get("seen_issue_ids", [])}

        priority = [candidate for candidate in candidates if candidate.get("match_quality") == "comicvine_recent"]
        priority_ids = {item.get("issue_id") for item in priority}
        exact = [
            candidate
            for candidate in candidates
            if candidate.get("match_quality") == "exact_day" and candidate.get("issue_id") not in priority_ids
        ]
        exact_ids = {item.get("issue_id") for item in exact}
        priority_unseen = self._unseen_pool(priority, seen)
        exact_unseen = self._unseen_pool(exact, seen)
        month_unseen = [
            candidate
            for candidate in self._unseen_pool(candidates, seen)
            if candidate.get("issue_id") not in exact_ids and candidate.get("issue_id") not in priority_ids
        ]
        reset_last_issue_id = ""

        if not priority_unseen and not exact_unseen and not month_unseen:
            reset_last_issue_id = str(bucket.get("last_issue_id") or "")
            bucket["seen_issue_ids"] = []
            seen = set()
            priority_unseen = self._unseen_pool(priority, seen)
            exact_unseen = self._unseen_pool(exact, seen)
            month_unseen = [
                candidate
                for candidate in self._unseen_pool(candidates, seen)
                if candidate.get("issue_id") not in exact_ids and candidate.get("issue_id") not in priority_ids
            ]

        if priority and not priority_unseen:
            reset_last_issue_id = str(bucket.get("last_issue_id") or reset_last_issue_id or "")
            priority_unseen = list(priority)
            if reset_last_issue_id and len(priority_unseen) > 1:
                recycled = [
                    candidate for candidate in priority_unseen
                    if str(candidate.get("issue_id") or "") != reset_last_issue_id
                ]
                if recycled:
                    priority_unseen = recycled

        priority_unseen = self._cover_candidates_first(priority_unseen)
        exact_unseen = self._cover_candidates_first(exact_unseen)
        month_unseen = self._cover_candidates_first(month_unseen)
        ordered = priority_unseen + exact_unseen + month_unseen
        if reset_last_issue_id and len(ordered) > 1 and ordered[0].get("issue_id") == reset_last_issue_id:
            for index, candidate in enumerate(ordered[1:], start=1):
                if candidate.get("issue_id") != reset_last_issue_id:
                    ordered[0], ordered[index] = ordered[index], ordered[0]
                    break
        return ordered

    def _unseen_pool(self, candidates, seen):
        return [candidate for candidate in candidates if str(candidate.get("issue_id") or "") not in seen]

    def _cover_candidates_first(self, candidates):
        with_cover = [candidate for candidate in candidates if candidate.get("cover_url")]
        without_cover = [candidate for candidate in candidates if not candidate.get("cover_url")]
        random.shuffle(with_cover)
        random.shuffle(without_cover)
        return with_cover + without_cover

    def _mark_seen(self, state, today, cover):
        issue_id = str(cover.get("issue_id") or "")
        if not issue_id:
            return

        date_key = today.strftime("%m-%d")
        state["version"] = STATE_VERSION
        bucket = state.setdefault("date_buckets", {}).setdefault(date_key, {})
        seen = [str(value) for value in bucket.get("seen_issue_ids", [])]
        if issue_id not in seen:
            seen.append(issue_id)
        bucket["seen_issue_ids"] = seen[-5000:]
        bucket["last_issue_id"] = issue_id
        bucket["last_displayed_at"] = datetime.now(timezone.utc).isoformat()

    def _load_cover(self, candidate, dimensions, settings):
        detail = self._issue_detail(candidate)
        self._validate_detail_filters(detail, settings)
        self._validate_detail_date(detail, candidate)
        cover_url = self._normalize_cover_url(detail.get("cover_url") or candidate.get("cover_url"))
        if not cover_url:
            raise RuntimeError("comic issue has no cover URL")

        image = self._download_cover_image(cover_url, candidate, detail)

        cover = dict(candidate)
        cover.update(detail)
        cover["cover_url"] = cover_url
        cover["image"] = image.convert("RGB")
        cover["date_label"] = self._date_label(cover)
        return cover

    def _cover_from_unavailable_image(self, exc):
        cover = dict(exc.candidate)
        cover.update(exc.detail)
        cover["cover_url"] = exc.cover_url
        cover["date_label"] = self._date_label(cover)
        return cover

    def _cover_from_candidate_metadata(self, candidate):
        cover = dict(candidate or {})
        cover["cover_url"] = self._normalize_cover_url(cover.get("cover_url"))
        cover["date_label"] = self._date_label(cover)
        return cover

    def _has_candidate_metadata(self, candidate):
        return any(_clean_text(candidate.get(key) or "") for key in [
            "series_name",
            "issue_label",
            "issue_number",
            "publisher",
            "on_sale_date",
            "publication_date",
            "key_date",
            "cover_date",
            "store_date",
            "date_added",
        ])

    def _normalize_cover_url(self, value):
        text = html.unescape(str(value or "")).strip()
        if not text:
            return ""
        if text.startswith("//"):
            text = "https:" + text
        if text.startswith("/"):
            text = urljoin(GCD_BASE_URL, text)

        parsed = urlparse(text)
        if parsed.scheme and parsed.netloc:
            path = re.sub(r"/{2,}", "/", parsed.path)
            return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, parsed.fragment))
        return text

    def _download_cover_image(self, cover_url, candidate, detail):
        try:
            response = requests.get(
                cover_url,
                timeout=(GCD_COVER_CONNECT_TIMEOUT_SECONDS, GCD_COVER_READ_TIMEOUT_SECONDS),
                headers=IMAGE_HEADERS,
                stream=True,
            )
            if response.status_code in {403, 429}:
                response.close()
                raise GcdCoverImageUnavailable(
                    f"cover image blocked by source ({response.status_code})",
                    candidate,
                    detail,
                    cover_url,
                )
            return safe_open_image_response(response)
        except GcdCoverImageUnavailable:
            raise
        except requests.exceptions.RequestException as exc:
            raise GcdCoverImageUnavailable(f"cover image request failed: {exc}", candidate, detail, cover_url) from exc
        except Exception as exc:
            raise GcdCoverImageUnavailable(f"cover image could not be decoded: {exc}", candidate, detail, cover_url) from exc

    def _validate_detail_filters(self, detail, settings):
        country_codes = self._normalize_csv(settings.get("countryCodes") or "us")
        if country_codes:
            country = _normalize_country_code(detail.get("country"))
            if country and country not in country_codes:
                raise RuntimeError(f"issue country '{country}' does not match filter")

    def _validate_detail_date(self, detail, candidate):
        if detail.get("source") == "comicvine" or candidate.get("match_quality") == "comicvine_recent":
            return

        target_text = candidate.get("target_date")
        if not target_text:
            return
        try:
            target_date = date.fromisoformat(str(target_text))
        except ValueError:
            return

        date_text = self._candidate_date(detail)
        if not self._date_match_quality(date_text, target_date):
            raise RuntimeError(f"issue date '{date_text}' does not match target month/day")

    def _issue_detail(self, candidate):
        if candidate.get("source") == "comicvine":
            return self._comic_vine_issue_detail(candidate)

        issue_id = str(candidate.get("issue_id") or "").strip()
        cached = self._read_issue_cache(issue_id)
        if cached:
            return cached

        url = f"{GCD_BASE_URL}/api/issue/{issue_id}/"
        data = self._fetch_json(url)
        detail = self._normalize_issue_detail(issue_id, data, candidate)

        series_url = data.get("series") if isinstance(data, dict) else None
        if series_url:
            try:
                series_data = self._fetch_json(series_url)
                if isinstance(series_data, dict):
                    detail["series_name"] = _first_text(series_data, ["name", "title"]) or detail.get("series_name")
                    detail["country"] = _first_text(series_data, ["country", "country_code"]) or detail.get("country")
                    detail["language"] = _first_text(series_data, ["language", "language_code"]) or detail.get("language")
                    detail["publisher"] = _first_text(series_data, ["publisher_name", "publisher"]) or detail.get("publisher")
            except Exception as exc:
                logger.warning("Could not fetch GCD series detail for issue %s: %s", issue_id, exc)

        self._write_issue_cache(issue_id, detail)
        return detail

    def _normalize_issue_detail(self, issue_id, data, candidate):
        data = data if isinstance(data, dict) else {}
        return {
            "issue_id": issue_id,
            "series_name": _first_text(data, ["series_name", "series"]) or candidate.get("series_name") or candidate.get("issue_label") or "Unknown series",
            "issue_number": _first_text(data, ["number", "issue_number"]) or candidate.get("issue_number") or "",
            "title": _first_text(data, ["title", "variant_name", "name"]) or candidate.get("title") or "",
            "publisher": _first_text(data, ["publisher_name", "indicia_publisher", "publisher"]) or candidate.get("publisher") or "",
            "country": _first_text(data, ["country", "country_code"]) or candidate.get("country") or "",
            "language": _first_text(data, ["language", "language_code"]) or candidate.get("language") or "",
            "on_sale_date": _first_text(data, ["on_sale_date"]) or candidate.get("on_sale_date") or "",
            "publication_date": _first_text(data, ["publication_date"]) or candidate.get("publication_date") or "",
            "key_date": _first_text(data, ["key_date"]) or candidate.get("key_date") or "",
            "cover_url": self._normalize_cover_url(_first_text(data, ["cover", "cover_url", "image_url"]) or candidate.get("cover_url") or ""),
            "cover_credits": _cover_credit_summary(data),
            "page_url": candidate.get("page_url") or f"{GCD_BASE_URL}/issue/{issue_id}/",
        }

    def _comic_vine_candidate_pool(self, settings, today):
        api_key = self._comic_vine_api_key(settings)
        if not api_key:
            logger.info("Comic Vine source skipped because no API key is configured.")
            return []

        limit = self._bounded_int(settings.get("comicVineLimit"), DEFAULT_COMIC_VINE_LIMIT, 3, 100)
        cached = self._read_comic_vine_cache(today, limit)
        if cached is not None:
            return cached

        candidates = self._fetch_comic_vine_recent_candidates(api_key, today, limit)
        self._write_comic_vine_cache(today, limit, candidates)
        return candidates

    def _fetch_comic_vine_recent_candidates(self, api_key, today, limit):
        payload = self._comic_vine_get(
            "issues/",
            api_key,
            {
                "field_list": "id,api_detail_url,site_detail_url,name,issue_number,cover_date,store_date,date_added,image,volume",
                "limit": int(limit),
                "sort": "date_added:desc",
            },
        )
        results = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(results, list):
            return []

        candidates = []
        seen = set()
        for record in results:
            if not isinstance(record, dict):
                continue
            candidate = self._comic_vine_candidate(record, today)
            issue_id = str(candidate.get("issue_id") or "")
            if not issue_id or issue_id in seen:
                continue
            seen.add(issue_id)
            candidates.append(candidate)
        return candidates

    def _comic_vine_candidate(self, record, today):
        raw_id = str(record.get("id") or "").strip()
        issue_id = f"comicvine:{raw_id}" if raw_id else ""
        volume = record.get("volume") if isinstance(record.get("volume"), dict) else {}
        cover_date = _first_text(record, ["cover_date"])
        store_date = _first_text(record, ["store_date"])
        date_added = _first_text(record, ["date_added"])
        return {
            "source": "comicvine",
            "source_label": "Comic Vine",
            "issue_id": issue_id,
            "comic_vine_id": raw_id,
            "series_name": _first_text(volume, ["name"]) or "Comic Vine Issue",
            "issue_number": _first_text(record, ["issue_number"]),
            "title": _first_text(record, ["name"]),
            "publisher": _first_text(volume, ["publisher"]),
            "country": "",
            "language": "",
            "on_sale_date": store_date or cover_date or date_added[:10] or today.isoformat(),
            "store_date": store_date,
            "cover_date": cover_date,
            "date_added": date_added,
            "cover_url": self._normalize_cover_url(self._comic_vine_image_url(record.get("image"))),
            "page_url": _first_url(record, ["site_detail_url"]),
            "api_url": _first_url(record, ["api_detail_url"]),
            "target_date": today.isoformat(),
            "year": today.year,
            "match_quality": "comicvine_recent",
        }

    def _comic_vine_issue_detail(self, candidate):
        issue_id = str(candidate.get("issue_id") or "").strip()
        cached = self._read_issue_cache(issue_id)
        if cached:
            return cached

        if candidate.get("cover_url"):
            detail = self._normalize_comic_vine_issue_detail({}, candidate)
            self._write_issue_cache(issue_id, detail)
            return detail

        api_key = self._comic_vine_api_key({})
        api_url = str(candidate.get("api_url") or "").strip()
        raw_id = str(candidate.get("comic_vine_id") or "").strip()
        if not api_url and raw_id:
            api_url = f"{COMIC_VINE_BASE_URL}/issue/4000-{raw_id}/"
        if not api_key or not api_url:
            return self._normalize_comic_vine_issue_detail({}, candidate)

        payload = self._comic_vine_get(
            api_url,
            api_key,
            {
                "field_list": "id,api_detail_url,site_detail_url,name,issue_number,cover_date,store_date,date_added,image,volume",
            },
        )
        record = payload.get("results") if isinstance(payload, dict) else {}
        detail = self._normalize_comic_vine_issue_detail(record if isinstance(record, dict) else {}, candidate)
        self._write_issue_cache(issue_id, detail)
        return detail

    def _normalize_comic_vine_issue_detail(self, record, candidate):
        record = record if isinstance(record, dict) else {}
        merged = dict(candidate or {})
        merged.update({key: value for key, value in record.items() if value not in (None, "", [])})
        volume = merged.get("volume") if isinstance(merged.get("volume"), dict) else {}
        raw_id = str(merged.get("comic_vine_id") or merged.get("id") or "").strip()
        issue_id = str(candidate.get("issue_id") or (f"comicvine:{raw_id}" if raw_id else "")).strip()
        cover_date = _first_text(merged, ["cover_date"])
        store_date = _first_text(merged, ["store_date"])
        date_added = _first_text(merged, ["date_added"])
        return {
            "source": "comicvine",
            "source_label": "Comic Vine",
            "issue_id": issue_id,
            "comic_vine_id": raw_id or candidate.get("comic_vine_id") or "",
            "series_name": _first_text(volume, ["name"]) or candidate.get("series_name") or "Comic Vine Issue",
            "issue_number": _first_text(merged, ["issue_number"]) or candidate.get("issue_number") or "",
            "title": _first_text(merged, ["name"]) or candidate.get("title") or "",
            "publisher": _first_text(volume, ["publisher"]) or candidate.get("publisher") or "",
            "country": candidate.get("country") or "",
            "language": candidate.get("language") or "",
            "on_sale_date": store_date or cover_date or date_added[:10] or candidate.get("on_sale_date") or "",
            "store_date": store_date or candidate.get("store_date") or "",
            "cover_date": cover_date or candidate.get("cover_date") or "",
            "date_added": date_added or candidate.get("date_added") or "",
            "cover_url": self._normalize_cover_url(self._comic_vine_image_url(merged.get("image")) or candidate.get("cover_url") or ""),
            "cover_credits": "",
            "page_url": _first_url(merged, ["site_detail_url"]) or candidate.get("page_url") or "",
            "api_url": _first_url(merged, ["api_detail_url"]) or candidate.get("api_url") or "",
        }

    def _comic_vine_get(self, path_or_url, api_key, params=None):
        url = path_or_url if str(path_or_url).startswith("http") else f"{COMIC_VINE_BASE_URL}/{str(path_or_url).lstrip('/')}"
        request_params = dict(params or {})
        request_params["api_key"] = api_key
        request_params["format"] = "json"
        response = requests.get(
            url,
            params=request_params,
            headers=COMIC_VINE_HEADERS,
            timeout=(GCD_API_CONNECT_TIMEOUT_SECONDS, GCD_API_READ_TIMEOUT_SECONDS),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Comic Vine HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Comic Vine response was not JSON") from exc
        status = str(payload.get("status_code") or "")
        if status and status != "1":
            raise RuntimeError(f"Comic Vine status {status}: {_clean_text(payload.get('error') or '')}")
        return payload

    def _comic_vine_image_url(self, image):
        if isinstance(image, str):
            return image
        if not isinstance(image, dict):
            return ""
        for key in ("super_url", "medium_url", "screen_url", "original_url", "small_url", "thumb_url", "icon_url"):
            value = image.get(key)
            if value:
                return str(value)
        return ""

    def _comic_vine_api_key(self, settings):
        for key in ("comicVineApiKey", "comic_vine_api_key"):
            value = str((settings or {}).get(key) or "").strip()
            if value:
                return value

        device_config = getattr(self, "_device_config", None)
        for env_name in COMIC_VINE_ENV_KEYS:
            value = ""
            if device_config is not None and hasattr(device_config, "load_env_key"):
                try:
                    value = device_config.load_env_key(env_name) or ""
                except Exception as exc:
                    logger.warning("Could not read Comic Vine env key %s: %s", env_name, exc)
            if not value:
                value = os.getenv(env_name, "")
            value = str(value or "").strip()
            if value:
                return value
        return ""

    def _fetch_month_candidates(self, year, month, day=None):
        if day:
            return self._fetch_weekly_api_candidates(date(int(year), int(month), int(day)))

        all_candidates = []
        seen_ids = set()
        for page in range(1, MAX_MONTH_PAGES + 1):
            page_candidates = self._fetch_month_export_page(year, month, page)
            if not page_candidates:
                if page == 1:
                    page_candidates = self._fetch_month_html_page(year, month, page)
                if not page_candidates:
                    break
            new_candidates = [
                candidate
                for candidate in page_candidates
                if str(candidate.get("issue_id") or "") not in seen_ids
            ]
            if not new_candidates and page > 1:
                break
            for candidate in new_candidates:
                seen_ids.add(str(candidate.get("issue_id") or ""))
            all_candidates.extend(new_candidates)
        return self._dedupe_candidates(all_candidates)

    def _fetch_weekly_api_candidates(self, target_date):
        iso_year, iso_week, _weekday = target_date.isocalendar()
        url = f"{GCD_BASE_URL}/api/issue/on_sale_weekly/{iso_year}/week/{iso_week}/"
        all_candidates = []
        seen_ids = set()

        for _page in range(MAX_WEEKLY_API_PAGES):
            data = self._fetch_json(url)
            results = data.get("results") if isinstance(data, dict) else []
            if not results:
                break

            for record in results:
                if not isinstance(record, dict):
                    continue
                candidate = self._weekly_api_candidate(record, target_date)
                issue_id = str(candidate.get("issue_id") or "")
                if not issue_id or issue_id in seen_ids:
                    continue
                seen_ids.add(issue_id)
                all_candidates.append(candidate)

            next_url = data.get("next") if isinstance(data, dict) else None
            if not next_url:
                break
            url = next_url

        return all_candidates

    def _weekly_api_candidate(self, record, target_date):
        issue_id = self._record_issue_id(record)
        api_url = _first_url(record, ["api_url"]) or f"{GCD_BASE_URL}/api/issue/{issue_id}/"
        return {
            "source": "gcd",
            "issue_id": issue_id,
            "series_name": _first_text(record, ["series_name", "series", "series_title", "title"]),
            "issue_number": _first_text(record, ["descriptor", "number", "issue_number"]),
            "publisher": _first_text(record, ["publisher", "publisher_name", "indicia_publisher"]),
            "country": _normalize_country_code(_first_text(record, ["country", "country_code", "publisher_country"])),
            "language": (_first_text(record, ["language", "language_code"]) or "").lower(),
                "on_sale_date": target_date.isoformat(),
                "publication_date": _first_text(record, ["publication_date"]),
                "cover_url": self._normalize_cover_url(_first_url(record, ["cover", "cover_url", "image", "image_url"])),
                "page_url": f"{GCD_BASE_URL}/issue/{issue_id}/",
                "api_url": api_url,
                "target_date": target_date.isoformat(),
            "year": target_date.year,
        }

    def _fetch_month_export_page(self, year, month, page):
        url = f"{GCD_BASE_URL}/on_sale_monthly/{year}/month/{month}/"
        params = {"_export": "json"}
        if page > 1:
            params["page"] = page
        response = self._gcd_get(url, params=params, headers=REQUEST_HEADERS)
        response.raise_for_status()
        try:
            data = response.json()
        except Exception:
            return []
        return self._extract_json_candidates(data, year, month)

    def _fetch_month_html_page(self, year, month, page):
        url = f"{GCD_BASE_URL}/on_sale_monthly/{year}/month/{month}/"
        params = {}
        if page > 1:
            params["page"] = page
        response = self._gcd_get(url, params=params, headers=REQUEST_HEADERS)
        response.raise_for_status()
        if not response.encoding:
            response.encoding = "utf-8"
        parser = _GcdMonthlyParser(response.url)
        parser.feed(response.text or "")

        candidates = []
        for row in parser.rows:
            text = _clean_text(" ".join(row.get("text") or []))
            date_text = self._date_from_text(text, year, month)
            candidates.append({
                "source": "gcd",
                "issue_id": row.get("issue_id"),
                "issue_label": row.get("issue_label"),
                "series_name": row.get("issue_label"),
                "country": row.get("country"),
                "on_sale_date": date_text,
                "cover_url": self._normalize_cover_url(row.get("cover_url")),
                "page_url": row.get("page_url") or f"{GCD_BASE_URL}/issue/{row.get('issue_id')}/",
                "year": year,
            })
        return candidates

    def _extract_json_candidates(self, data, year, month):
        records = []
        self._walk_json_records(data, records)
        candidates = []
        for record in records:
            issue_id = self._record_issue_id(record)
            if not issue_id:
                continue
            date_text = self._record_date(record, year, month)
            candidates.append({
                "source": "gcd",
                "issue_id": issue_id,
                "series_name": _first_text(record, ["series_name", "series", "series_title", "title"]),
                "issue_number": _first_text(record, ["number", "issue_number"]),
                "publisher": _first_text(record, ["publisher", "publisher_name", "indicia_publisher"]),
                "country": _normalize_country_code(_first_text(record, ["country", "country_code", "publisher_country"])),
                "language": (_first_text(record, ["language", "language_code"]) or "").lower(),
                "on_sale_date": date_text,
                "cover_url": self._normalize_cover_url(_first_url(record, ["cover", "cover_url", "image", "image_url"])),
                "page_url": _first_url(record, ["page_url", "url", "issue_url"]) or f"{GCD_BASE_URL}/issue/{issue_id}/",
                "year": year,
            })
        return candidates

    def _walk_json_records(self, value, records):
        if isinstance(value, list):
            for item in value:
                self._walk_json_records(item, records)
            return

        if not isinstance(value, dict):
            return

        if self._record_issue_id(value):
            records.append(value)

        for key in ["results", "rows", "objects", "data", "items"]:
            child = value.get(key)
            if isinstance(child, (list, dict)):
                self._walk_json_records(child, records)

    def _record_issue_id(self, record):
        for key in ["issue_id", "id", "pk"]:
            value = record.get(key)
            if value and str(value).isdigit():
                return str(value)
        for value in record.values():
            if isinstance(value, str):
                issue_id = _issue_id_from_url(value)
                if issue_id:
                    return issue_id
        return ""

    def _record_date(self, record, year, month):
        for key in ["on_sale_date", "on_sale", "sale_date", "date", "publication_date", "key_date"]:
            value = record.get(key)
            if value and _date_parts(str(value)):
                return str(value)
        return self._date_from_text(json.dumps(record, ensure_ascii=True), year, month)

    def _date_from_text(self, text, year, month):
        for match in re.finditer(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b", text or ""):
            found_year = int(match.group(1))
            found_month = int(match.group(2))
            if found_year == int(year) and found_month == int(month):
                return match.group(0)
        return f"{int(year):04d}-{int(month):02d}"

    def _candidate_date(self, candidate):
        for key in ["on_sale_date", "store_date", "cover_date", "publication_date", "key_date", "date_added"]:
            value = candidate.get(key)
            if value:
                return str(value)
        return ""

    def _date_label(self, cover):
        for key in ["on_sale_date", "store_date", "cover_date", "publication_date", "key_date", "date_added"]:
            value = str(cover.get(key) or "").strip()
            if value:
                return value
        return ""

    def _fit_mode(self, settings):
        return str(settings.get("fitMode") or DEFAULT_FIT_MODE).strip().lower()

    def _source_mode(self, settings):
        mode = str(settings.get("sourceMode") or DEFAULT_SOURCE_MODE).strip().lower()
        if mode in {"comicvine", "comic_vine", "comic-vine", "comic vine", "cv"}:
            return "comicvine"
        if mode in {"mixed", "both", "hybrid"}:
            return "mixed"
        return "gcd"

    def _is_wide_triptych_cover(self, cover):
        image = cover.get("image") if isinstance(cover, dict) else cover
        if not image:
            return False
        return image.width > image.height * 1.15

    def _compose_triptych_display_image(self, covers, dimensions, settings):
        width, height = dimensions
        images = []
        for cover in covers[:TRIPTYCH_COVER_COUNT]:
            image = cover.get("image") if isinstance(cover, dict) else cover
            if not image:
                continue
            images.append(ImageOps.exif_transpose(image).convert("RGB"))
        if not images:
            return self._fallback_image(dimensions, "GCD Comic Covers", "No usable cover image")

        canvas = self._triptych_backdrop(images, dimensions, settings)
        visible_count = min(len(images), TRIPTYCH_COVER_COUNT)
        column_width = width // visible_count

        for index, image in enumerate(images):
            x0 = index * column_width
            target_width = column_width if index < visible_count - 1 else width - x0
            fitted = ImageOps.contain(image, (target_width, height), method=Image.LANCZOS)
            x = x0 + (target_width - fitted.width) // 2
            y = (height - fitted.height) // 2
            canvas.paste(fitted, (x, y))

        return canvas

    def _triptych_backdrop(self, images, dimensions, settings):
        background = self._plain_background(dimensions, settings)
        if not images:
            return background

        try:
            backdrop = ImageOps.fit(images[0], dimensions, method=Image.LANCZOS)
            blur_radius = max(8, min(dimensions) // 26)
            backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            backdrop = ImageEnhance.Color(backdrop).enhance(0.35)
            backdrop = ImageEnhance.Contrast(backdrop).enhance(0.75)
            return Image.blend(backdrop, background, 0.72)
        except Exception as exc:
            logger.warning("Could not render GCD cover triptych backdrop: %s", exc)
            return background

    def _plain_background(self, dimensions, settings):
        color = (settings.get("backgroundColor") or "white").lower()
        base_color = (0, 0, 0) if color == "black" else (255, 255, 255)
        return Image.new("RGB", dimensions, base_color)

    def _fit_cover(self, image, dimensions, settings, cover):
        fit_mode = (settings.get("fitMode") or "rotate_ccw").lower()
        image = ImageOps.exif_transpose(image).convert("RGB")

        if fit_mode == "cover":
            canvas = ImageOps.fit(image, dimensions, method=Image.LANCZOS)
        elif fit_mode in {"rotate_ccw", "ccw", "rotate_left", "rotate90ccw", "landscape"}:
            image = image.rotate(90, expand=True)
            canvas = self._background(dimensions, settings, image)
            fitted = self._width_fit_crop(image, dimensions)
            canvas.paste(fitted, (0, 0))
        elif fit_mode in {"horizontal", "width", "full_width"}:
            canvas = self._background(dimensions, settings, image)
            fitted = self._width_fit_crop(image, dimensions)
            canvas.paste(fitted, (0, 0))
        else:
            should_rotate = fit_mode in {"rotate_full", "rotate", "auto"} and image.height > image.width and dimensions[0] > dimensions[1]
            if should_rotate:
                image = image.rotate(90, expand=True)
            canvas = self._background(dimensions, settings, image)
            fitted = ImageOps.contain(image, dimensions, method=Image.LANCZOS)
            x = (dimensions[0] - fitted.width) // 2
            y = (dimensions[1] - fitted.height) // 2
            canvas.paste(fitted, (x, y))

        if str(settings.get("showInfoLabel", "true")).lower() not in {"false", "0", "off", "no"}:
            canvas = self._with_info_label(canvas, cover)
        return canvas

    def _width_fit_crop(self, image, dimensions):
        fitted_height = max(1, round(image.height * dimensions[0] / image.width))
        fitted = image.resize((dimensions[0], fitted_height), Image.LANCZOS)
        if fitted.height > dimensions[1]:
            fitted = fitted.crop((0, 0, dimensions[0], dimensions[1]))
        return fitted

    def _background(self, dimensions, settings, image):
        color = (settings.get("backgroundColor") or "white").lower()
        base_color = (0, 0, 0) if color == "black" else (255, 255, 255)
        if (settings.get("backgroundStyle") or "blur").lower() in {"plain", "solid"}:
            return Image.new("RGB", dimensions, base_color)

        try:
            backdrop = ImageOps.fit(image, dimensions, method=Image.LANCZOS)
            backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=max(5, min(dimensions) // 55)))
            backdrop = ImageEnhance.Color(backdrop).enhance(0.38)
            backdrop = ImageEnhance.Contrast(backdrop).enhance(0.82)
            wash = Image.new("RGB", dimensions, base_color)
            return Image.blend(backdrop, wash, 0.5 if color != "black" else 0.32)
        except Exception as exc:
            logger.warning("Could not render GCD cover backdrop: %s", exc)
            return Image.new("RGB", dimensions, base_color)

    def _with_info_label(self, image, cover):
        label = self._label_text(cover)
        if not label:
            return image

        image = image.copy()
        draw = ImageDraw.Draw(image)
        width, height = image.size
        font_size = max(14, min(width, height) // 24)
        font = self._fallback_font(font_size, bold=True)
        max_label_width = max(160, int(width * 0.64))
        while font_size > 11 and draw.textlength(label, font=font) > max_label_width:
            font_size -= 1
            font = self._fallback_font(font_size, bold=True)

        text = self._fit_text(draw, label, font, max_label_width)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        pad_x = max(8, width // 90)
        pad_y = max(5, height // 100)
        x = max(8, width // 70)
        y = height - text_h - pad_y * 2 - max(8, height // 70)
        box = (x, y, x + text_w + pad_x * 2, y + text_h + pad_y * 2)
        draw.rectangle(box, fill="white", outline="black", width=1)
        draw.text((x + pad_x, y + pad_y - bbox[1]), text, fill="black", font=font)
        return image

    def _label_text(self, cover):
        series = _clean_text(cover.get("series_name") or cover.get("issue_label") or "Comic")
        number = _clean_text(cover.get("issue_number") or "")
        date_label = _clean_text(cover.get("date_label") or "")
        label = series
        if number:
            label += f" #{number}"
        if date_label:
            label += f" / {date_label}"
        return label[:140]

    def _metadata_cover_image(self, dimensions, settings, cover):
        width, height = dimensions
        color = (settings.get("backgroundColor") or "white").lower()
        dark = color == "black"
        bg = (18, 18, 18) if dark else (255, 255, 255)
        fg = (245, 245, 245) if dark else (15, 15, 15)
        muted = (190, 190, 190) if dark else (70, 70, 70)
        line = (230, 230, 230) if dark else (0, 0, 0)
        image = Image.new("RGB", dimensions, bg)
        draw = ImageDraw.Draw(image)

        margin = max(18, min(width, height) // 18)
        draw.rectangle((margin, margin, width - margin, height - margin), outline=line, width=4)
        draw.rectangle((margin + 10, margin + 10, width - margin - 10, height - margin - 10), outline=muted, width=1)

        eyebrow_font = self._fallback_font(max(13, width // 52), bold=True)
        title_font = self._fallback_font(max(28, width // 15), bold=True)
        meta_font = self._fallback_font(max(16, width // 36), bold=True)
        small_font = self._fallback_font(max(12, width // 54))

        y = margin + max(24, height // 18)
        self._draw_centered(draw, "GCD COMIC COVER", width // 2, y, eyebrow_font, muted)
        y += max(42, height // 11)

        title = _clean_text(cover.get("series_name") or cover.get("issue_label") or "Unknown Series")
        title_lines = self._wrap_text(draw, title, title_font, width - margin * 3, max_lines=3)
        for line_text in title_lines:
            self._draw_centered(draw, line_text, width // 2, y, title_font, fg)
            y += max(36, title_font.size + 8 if hasattr(title_font, "size") else 36)

        number = _clean_text(cover.get("issue_number") or "")
        date_label = _clean_text(cover.get("date_label") or "")
        publisher = _clean_text(cover.get("publisher") or "")
        meta_parts = []
        if number:
            meta_parts.append(f"#{number}")
        if date_label:
            meta_parts.append(date_label)
        if publisher:
            meta_parts.append(publisher)
        if meta_parts:
            y += max(8, height // 70)
            self._draw_centered(draw, " | ".join(meta_parts), width // 2, y, meta_font, fg)

        credits = _clean_text(cover.get("cover_credits") or "")
        if credits:
            y += max(40, height // 11)
            for line_text in self._wrap_text(draw, f"Cover: {credits}", small_font, width - margin * 4, max_lines=2):
                self._draw_centered(draw, line_text, width // 2, y, small_font, muted)
                y += max(18, small_font.size + 5 if hasattr(small_font, "size") else 18)

        note = "Image unavailable from source; rendered from GCD metadata"
        self._draw_centered(draw, note, width // 2, height - margin - max(18, height // 28), small_font, muted)
        return image

    def _fallback_image(self, dimensions, title, subtitle):
        image = Image.new("RGB", dimensions, "white")
        draw = ImageDraw.Draw(image)
        width, height = dimensions
        border = max(12, min(width, height) // 24)
        draw.rectangle((border, border, width - border, height - border), outline="black", width=3)
        draw.line((border, height // 2, width - border, height // 2), fill=(170, 170, 170), width=2)
        title_font = self._fallback_font(max(24, width // 14), bold=True)
        subtitle_font = self._fallback_font(max(16, width // 30))
        self._draw_centered(draw, title, width // 2, height // 2 - 38, title_font, "black")
        self._draw_centered(draw, subtitle, width // 2, height // 2 + 22, subtitle_font, (65, 65, 65))
        return image

    def _draw_centered(self, draw, text, x, y, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((x - (bbox[2] - bbox[0]) // 2, y - (bbox[3] - bbox[1]) // 2), text, font=font, fill=fill)

    def _fallback_font(self, size, bold=False):
        paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for path in paths:
            try:
                if Path(path).is_file():
                    return ImageFont.truetype(path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    def _fit_text(self, draw, text, font, max_width):
        if draw.textlength(text, font=font) <= max_width:
            return text
        candidate = text
        while candidate and draw.textlength(candidate + "...", font=font) > max_width:
            candidate = candidate[:-1].rstrip()
        return f"{candidate}..." if candidate else text[:1]

    def _wrap_text(self, draw, text, font, max_width, max_lines=3):
        words = _clean_text(text).split()
        if not words:
            return [""]

        lines = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break

        if len(lines) < max_lines and current:
            lines.append(current)

        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if len(lines) == max_lines and words:
            consumed = " ".join(lines).split()
            if len(consumed) < len(words):
                lines[-1] = self._fit_text(draw, lines[-1] + "...", font, max_width)
        return lines

    def _fetch_json(self, url):
        response = self._gcd_get(url, headers=JSON_HEADERS)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            content_type = response.headers.get("content-type", "unknown")
            snippet = _clean_text((response.text or "")[:180])
            raise RuntimeError(f"GCD JSON endpoint returned {content_type}: {snippet}") from exc

    def _gcd_get(self, url, params=None, headers=None):
        return requests.get(
            url,
            params=params,
            headers=headers or REQUEST_HEADERS,
            timeout=(GCD_API_CONNECT_TIMEOUT_SECONDS, GCD_API_READ_TIMEOUT_SECONDS),
        )

    def _read_comic_vine_cache(self, today, limit):
        path = self._comic_vine_cache_path(today, limit)
        try:
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != COMIC_VINE_CACHE_VERSION:
                return None
            fetched_at = datetime.fromisoformat(data.get("fetched_at"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched_at > COMIC_VINE_CACHE_TTL:
                return None
            return data.get("candidates") or []
        except Exception as exc:
            logger.warning("Could not read Comic Vine cache %s: %s", path, exc)
            return None

    def _write_comic_vine_cache(self, today, limit, candidates):
        path = self._comic_vine_cache_path(today, limit)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": COMIC_VINE_CACHE_VERSION,
            "date": today.isoformat(),
            "limit": int(limit),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "candidates": candidates,
        }
        self._write_json(path, payload)

    def _read_month_cache(self, year, month, day=None):
        path = self._month_cache_path(year, month, day)
        try:
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != MONTH_CACHE_VERSION:
                return None
            fetched_at = datetime.fromisoformat(data.get("fetched_at"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched_at > MONTH_CACHE_TTL:
                return None
            return data.get("candidates") or []
        except Exception as exc:
            logger.warning("Could not read GCD month cache %s: %s", path, exc)
            return None

    def _write_month_cache(self, year, month, candidates, day=None):
        path = self._month_cache_path(year, month, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": MONTH_CACHE_VERSION,
            "year": int(year),
            "month": int(month),
            "day": int(day or 0),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "candidates": candidates,
        }
        self._write_json(path, payload)

    def _read_issue_cache(self, issue_id):
        path = self._issue_cache_path(issue_id)
        try:
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != ISSUE_CACHE_VERSION:
                return None
            fetched_at = datetime.fromisoformat(data.get("fetched_at"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched_at > ISSUE_CACHE_TTL:
                return None
            return data.get("detail") or {}
        except Exception as exc:
            logger.warning("Could not read GCD issue cache %s: %s", path, exc)
            return None

    def _write_issue_cache(self, issue_id, detail):
        path = self._issue_cache_path(issue_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": ISSUE_CACHE_VERSION,
            "issue_id": str(issue_id),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "detail": detail,
        }
        self._write_json(path, payload)

    def _read_state(self):
        path = self._state_path()
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version") == STATE_VERSION:
                    return data
        except Exception as exc:
            logger.warning("Could not read GCD cover state %s: %s", path, exc)
        return {"version": STATE_VERSION, "date_buckets": {}}

    def _write_state(self, state):
        state["version"] = STATE_VERSION
        self._write_json(self._state_path(), state)

    def _write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=True, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            path.write_text(text, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _state_path(self):
        return self._cache_dir() / "state.json"

    def _month_cache_path(self, year, month, day=None):
        if day:
            return self._cache_dir() / "dates" / f"{int(year):04d}-{int(month):02d}-{int(day):02d}.json"
        return self._cache_dir() / "months" / f"{int(year):04d}-{int(month):02d}.json"

    def _issue_cache_path(self, issue_id):
        key = hashlib.sha256(str(issue_id).encode("utf-8")).hexdigest()[:16]
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(issue_id or "issue")).strip("._") or "issue"
        return self._cache_dir() / "issues" / f"{safe_id}_{key}.json"

    def _comic_vine_cache_path(self, today, limit):
        return self._cache_dir() / "comicvine" / f"{today.isoformat()}-recent-{int(limit)}.json"

    def _cache_dir(self):
        return self.cache_dir(env_var="INKYPI_GCD_COMIC_COVERS_CACHE", leaf=".gcd_comic_covers_cache", create=False)

    def _dedupe_candidates(self, candidates):
        deduped = {}
        for candidate in candidates:
            issue_id = str(candidate.get("issue_id") or "").strip()
            if issue_id and issue_id not in deduped:
                deduped[issue_id] = candidate
        return list(deduped.values())

    def _normalize_csv(self, value):
        if not value:
            return []
        if isinstance(value, list):
            values = value
        else:
            values = re.split(r"[,;\s]+", str(value))
        normalized = []
        for item in values:
            text = str(item).strip().lower()
            if not text:
                continue
            normalized.append(_normalize_country_code(text) or text)
        return normalized

    def _bounded_int(self, value, default, minimum, maximum):
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = int(default)
        return max(minimum, min(maximum, number))


def _first_text(record, keys):
    if not isinstance(record, dict):
        return ""
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            nested = _first_text(value, ["name", "title", "label", "value", "display_name"])
            if nested:
                return nested
            continue
        text = _clean_text(str(value))
        if text:
            return text
    return ""


def _first_url(record, keys):
    for key in keys:
        value = record.get(key) if isinstance(record, dict) else None
        if isinstance(value, dict):
            value = value.get("url") or value.get("href") or value.get("src")
        if not value:
            continue
        text = html.unescape(str(value)).strip()
        if text.startswith(("http://", "https://")):
            return text
        if text.startswith("/"):
            return urljoin(GCD_BASE_URL, text)
    return ""


def _cover_credit_summary(data):
    if not isinstance(data, dict):
        return ""
    stories = data.get("story_set")
    if not isinstance(stories, list):
        return ""
    cover_story = None
    for story in stories:
        if not isinstance(story, dict):
            continue
        if str(story.get("type") or "").strip().lower() == "cover":
            cover_story = story
            break
    if not cover_story:
        return ""

    parts = []
    for label, key in [
        ("Pencils", "pencils"),
        ("Inks", "inks"),
        ("Colors", "colors"),
        ("Letters", "letters"),
    ]:
        value = _clean_text(cover_story.get(key) or "")
        if value and value not in {"?", "??"}:
            parts.append(f"{label}: {value}")
    return "; ".join(parts)[:220]


def _issue_id_from_url(value):
    match = re.search(r"/issue/(\d+)", value or "")
    return match.group(1) if match else ""


def _date_parts(value):
    match = re.search(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b", value or "")
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3) or 0)
    return year, month, day


def _normalize_country_code(value):
    text = _clean_text(value).lower()
    if not text:
        return ""
    normalized = re.sub(r"[^a-z]+", " ", text).strip()
    aliases = {
        "us": "us",
        "u s": "us",
        "usa": "us",
        "u s a": "us",
        "united states": "us",
        "united states of america": "us",
        "america": "us",
        "canada": "ca",
        "ca": "ca",
        "united kingdom": "uk",
        "great britain": "uk",
        "uk": "uk",
        "u k": "uk",
    }
    if normalized in aliases:
        return aliases[normalized]
    if len(normalized) <= 3 and normalized.isalpha():
        return normalized
    return ""


def _clean_text(value):
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()
