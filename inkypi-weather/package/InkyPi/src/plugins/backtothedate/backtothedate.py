from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationPreparation,
    get_presentation_instance_uuid,
)
from plugins.base_plugin.theme_presentation import apply_media_theme_chrome
from plugins.backtothedate.presentation_bank import (
    MAX_HISTORY_URLS,
    READY_TARGET,
    REFILL_THRESHOLD,
    PosterPresentationBank,
    instance_profile_fingerprint,
    read_bounded_json_object,
    settings_fingerprint,
    settings_key,
    validate_state_payload_size,
    validate_state_shape,
)
from utils.atomic_file import atomic_write_json
from utils.http_client import get_http_session
import logging
import random
import re

logger = logging.getLogger(__name__)

BASE_URL = "https://chineseposters.net"
POSTERS_URL = f"{BASE_URL}/posters/posters"
DEFAULT_SOURCE_MODE = "mao_era"
MAO_ERA_THEME_URLS = [
    f"{BASE_URL}/themes/great-leap-forward",
    f"{BASE_URL}/themes/cultural-revolution-campaigns",
    f"{BASE_URL}/themes/monsters-demons",
    f"{BASE_URL}/themes/revolutionary-networking",
    f"{BASE_URL}/themes/shanghai-commune",
    f"{BASE_URL}/themes/revolutionary-committees",
    f"{BASE_URL}/themes/red-sea-movement",
    f"{BASE_URL}/themes/may-seven-cadre-schools",
    f"{BASE_URL}/themes/up-to-the-mountains",
    f"{BASE_URL}/themes/pla-cultural-revolution",
    f"{BASE_URL}/themes/mao-cult",
]
DEFAULT_MAX_PAGE = 141
MAX_PAGE_CACHE_TTL = timedelta(days=7)
POSTER_PATH_RE = re.compile(r"^/posters/(?!posters(?:$|\?))[-a-z0-9]+/?$", re.I)
THEME_PATH_RE = re.compile(r"^/themes/[-a-z0-9]+/?$", re.I)
IMAGE_PATH_RE = re.compile(r"/sites/default/files/images/[^\"'\s<>]+\.(?:jpg|jpeg|png)", re.I)
REQUEST_HEADERS = {
    "User-Agent": "InkyPi BacktotheDate/1.0 (+https://chineseposters.net/)"
}
POSTER_DETAIL_CANDIDATE_LIMIT = 8
THEME_PAGE_SAMPLE_LIMIT = 4
DEFAULT_FIT_MODE = "triptych"
TRIPTYCH_POSTER_COUNT = 3
STATELESS_PREVIEW_SETTING = "_inkypiStatelessPreview"


class _PosterLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._active_href = None
        self._text = []
        self._img_alt = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "a":
            self._active_href = attrs.get("href")
            self._text = []
            self._img_alt = ""
        elif tag.lower() == "img" and self._active_href:
            self._img_alt = attrs.get("alt") or self._img_alt

    def handle_data(self, data):
        if self._active_href:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or not self._active_href:
            return

        title = _clean_text(" ".join(self._text)) or _clean_text(self._img_alt)
        self.links.append({"href": self._active_href, "title": title})
        self._active_href = None
        self._text = []
        self._img_alt = ""


class _PosterDetailParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.images = []
        self._in_h1 = False
        self._h1_text = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "h1":
            self._in_h1 = True
            self._h1_text = []
        elif tag == "img":
            src = attrs.get("src")
            if src:
                self.images.append({
                    "src": src,
                    "alt": attrs.get("alt") or "",
                })

    def handle_data(self, data):
        if self._in_h1:
            self._h1_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "h1" and self._in_h1:
            self.title = _clean_text(" ".join(self._h1_text))
            self._in_h1 = False


def _clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


class BacktotheDate(BasePlugin):
    def generate_image(self, settings, device_config):
        logger.info("=== BacktotheDate Plugin: Starting image generation ===")
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        attempts = self._safe_int(settings.get("attempts"), 8, minimum=1, maximum=20)
        if get_presentation_instance_uuid(settings) is None:
            if settings.get(STATELESS_PREVIEW_SETTING) is not True:
                raise RuntimeError(
                    "BacktotheDate playlist rendering requires trusted instance identity"
                )
            return self._generate_stateless_preview(settings, dimensions, attempts)
        bank = self._presentation_bank(settings, dimensions)
        document, profile = bank.load_for_data()
        ready = bank.ready_records(document, profile, prune=True)
        errors = []

        protected_missing = bank.missing_protected_records(profile, ready)
        for record in protected_missing:
            try:
                image = self._load_poster_image(record["image_url"], dimensions)
            except Exception as exc:
                raise RuntimeError(
                    "Could not rehydrate protected BacktotheDate media"
                ) from exc
            if image is None:
                raise RuntimeError(
                    "Could not rehydrate protected BacktotheDate current/pending media"
                )
            bank.ingest(profile, record, image)
        if protected_missing:
            bank.save(document)
            ready = bank.ready_records(document, profile, prune=True)
            if bank.missing_protected_records(profile, ready):
                raise RuntimeError(
                    "Protected BacktotheDate current/pending media remains unavailable"
                )

        forced_poster = self._forced_poster_from_settings(settings)
        target = 1 if forced_poster else READY_TARGET
        refill_threshold = 1 if forced_poster else REFILL_THRESHOLD
        maximum_attempts = target + attempts
        tries = 0
        refill_bank = len(ready) < refill_threshold
        while refill_bank and len(ready) < target and tries < maximum_attempts:
            tries += 1
            try:
                poster = forced_poster or self._select_random_poster(settings)
                poster = bank.normalize_poster(poster)
                if any(item["image_url"] == poster["image_url"] for item in ready):
                    if forced_poster:
                        break
                    continue
                image = self._load_poster_image(poster["image_url"], dimensions)
                if image is not None:
                    record = bank.ingest(profile, poster, image)
                    ready.append(record)
                    if len(ready) >= target:
                        break
                    continue
                errors.append(f"{poster.get('title') or poster['page_url']}: image load failed")
            except Exception as exc:
                logger.warning("BacktotheDate poster attempt failed: %s", exc)
                errors.append(str(exc))

        latest = self._read_state()
        for key in ("max_page", "max_page_checked_at"):
            if key in latest:
                document[key] = latest[key]
        bank.save(document)
        ready = bank.ready_records(document, profile, prune=True)
        if not ready:
            detail = "; ".join(errors[-3:])
            raise RuntimeError(f"Could not hydrate a Chinese poster bank. {detail}")

        current = bank.ensure_current(
            document,
            profile,
            ready,
            self._fit_mode(settings),
        )
        image = self._render_bank_selection(
            bank,
            profile,
            current,
            dimensions,
            settings,
        )
        logger.info(
            "BacktotheDate bank ready: %s decoded posters; DATA kept current selection",
            len(ready),
        )
        return image

    def presentation_mode(self, settings):
        return PresentationMode.PREPARED_BANK

    def prepare_presentation(
        self,
        settings,
        device_config,
        *,
        request,
        resolved_theme_context,
    ):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        bank = self._presentation_bank(settings, dimensions)
        document, profile = bank.load_warm()
        bank.apply_trusted_origin(document, profile, request)
        ready = bank.ready_records(document, profile, prune=False)

        pending = bank.pending_for_request(profile, request.request_id)
        if pending is None:
            discarded_page_keys, discarded_image_keys = bank.discarded_keys(document)
            selection = bank.choose_selection(
                profile,
                ready,
                self._fit_mode(settings),
                discarded_page_keys,
                discarded_image_keys,
            )
        else:
            selection = pending

        image = self._render_bank_selection(
            bank,
            profile,
            selection,
            dimensions,
            settings,
        )
        if resolved_theme_context is not None:
            image = apply_media_theme_chrome(
                image,
                self.get_plugin_id(),
                resolved_theme_context,
                dimensions,
            )
            mode = resolved_theme_context.get("mode")
            if mode in {"day", "night"}:
                image.info["inkypi_theme_mode"] = mode

        if pending is None:
            bank.set_pending(document, profile, request, selection)
        return PresentationPreparation(
            request_id=request.request_id,
            image=image,
            changed=True,
        )

    def reconcile_presentation_receipt(self, settings, receipt):
        if receipt is None:
            return None
        instance_uuid = get_presentation_instance_uuid(settings)
        if instance_uuid is None:
            raise RuntimeError(
                "BacktotheDate receipt reconciliation requires trusted instance identity"
            )
        document = self._read_state()
        validate_state_shape(document)
        changed = PosterPresentationBank.reconcile_document(
            document,
            receipt,
            self._normalize_history_url,
            instance_uuid,
        )
        if changed:
            self._write_state(document)
        return None

    def _render_bank_selection(self, bank, profile, selection, dimensions, settings):
        poster_images = bank.selection_media(profile, selection)
        if len(poster_images) == TRIPTYCH_POSTER_COUNT:
            return self._compose_triptych_display_image(
                poster_images,
                dimensions,
                settings,
            )
        poster, image = poster_images[0]
        return self._compose_single_display_image(
            poster,
            image,
            dimensions,
            settings,
        )

    def _presentation_bank(self, settings, dimensions):
        instance_uuid = get_presentation_instance_uuid(settings)
        if instance_uuid is None:
            raise RuntimeError(
                "BacktotheDate presentation bank requires trusted instance identity"
            )
        source_urls = self._source_theme_urls(settings)
        fit_mode = self._fit_mode(settings)
        profile_key = settings_key(settings, source_urls, fit_mode)
        base_fingerprint = settings_fingerprint(
            settings,
            source_urls,
            fit_mode,
            dimensions,
        )
        fingerprint = instance_profile_fingerprint(base_fingerprint, instance_uuid)
        return PosterPresentationBank(
            self._state_path(),
            self._legacy_state_path(),
            self._presentation_media_dir(),
            fingerprint=fingerprint,
            base_fingerprint=base_fingerprint,
            profile_settings_key=profile_key,
            instance_uuid=instance_uuid,
            normalize_history_url=self._normalize_history_url,
        )

    def _presentation_media_dir(self):
        return self.cache_dir(leaf="presentation-media")

    def _generate_stateless_preview(self, settings, dimensions, attempts):
        preview_settings = dict(settings)
        preview_settings["maxPage"] = self._safe_int(
            preview_settings.get("maxPage"),
            DEFAULT_MAX_PAGE,
            minimum=0,
            maximum=10000,
        )
        forced_poster = self._forced_poster_from_settings(preview_settings)
        if forced_poster:
            image = self._load_poster_image(forced_poster["image_url"], dimensions)
            if image is None:
                raise RuntimeError(
                    f"Could not load forced Chinese poster image: {forced_poster['image_url']}"
                )
            return self._compose_single_display_image(
                forced_poster,
                self._normalize_image(image),
                dimensions,
                preview_settings,
            )

        errors = []
        if self._fit_mode(preview_settings) in {
            "triptych",
            "three_vertical",
            "three_posters",
            "gallery",
        }:
            try:
                return self._generate_triptych_image(
                    preview_settings,
                    dimensions,
                    attempts,
                    remember=False,
                )
            except Exception as exc:
                logger.warning(
                    "BacktotheDate stateless triptych preview failed; falling back: %s",
                    exc,
                )
                errors.append(str(exc))

        for _ in range(attempts):
            try:
                poster = self._select_random_poster(preview_settings)
                image = self._load_poster_image(poster["image_url"], dimensions)
                if image is not None:
                    return self._compose_display_image(
                        poster,
                        self._normalize_image(image),
                        dimensions,
                        preview_settings,
                    )[0]
                errors.append(f"{poster.get('title') or poster['page_url']}: image load failed")
            except Exception as exc:
                logger.warning("BacktotheDate stateless preview attempt failed: %s", exc)
                errors.append(str(exc))
        raise RuntimeError(
            f"Could not fetch a Chinese poster preview. {'; '.join(errors[-3:])}"
        )

    def _generate_triptych_image(
        self,
        settings,
        dimensions,
        attempts,
        *,
        remember=True,
    ):
        selected = []
        seen_urls = set()
        max_attempts = max(attempts * 3, TRIPTYCH_POSTER_COUNT * 3)

        for _ in range(max_attempts):
            poster = self._select_random_poster(settings)
            page_key = self._normalize_history_url(poster.get("page_url"))
            image_key = self._normalize_history_url(poster.get("image_url"))
            unique_key = image_key or page_key
            if unique_key in seen_urls:
                continue
            seen_urls.add(unique_key)

            image = self._load_poster_image(poster["image_url"], dimensions)
            if not image:
                continue
            image = self._normalize_image(image)

            if not self._is_portrait(image):
                if remember:
                    self._remember_success([poster])
                logger.info(
                    "Selected landscape Chinese poster as single display: %s | %s",
                    poster.get("title") or poster["page_url"],
                    poster["image_url"],
                )
                return self._compose_single_display_image(poster, image, dimensions, settings)

            selected.append((poster, image))
            if len(selected) >= TRIPTYCH_POSTER_COUNT:
                break

        poster_images = selected[:TRIPTYCH_POSTER_COUNT]
        if len(poster_images) < TRIPTYCH_POSTER_COUNT:
            raise RuntimeError(f"Only found {len(poster_images)} portrait posters for triptych layout.")

        posters = [poster for poster, _image in poster_images]
        if remember:
            self._remember_success(posters)
        logger.info(
            "Selected Chinese poster triptych: %s",
            " | ".join((poster.get("title") or poster["page_url"]) for poster in posters),
        )
        return self._compose_triptych_display_image(poster_images, dimensions, settings)

    def _forced_poster_from_settings(self, settings):
        image_url = str(settings.get("posterImageUrl") or settings.get("previewImageUrl") or "").strip()
        if not image_url:
            return None

        image_url = self._canonical_provider_url(image_url, kind="image")

        page_url = str(settings.get("posterPageUrl") or "").strip()
        if page_url:
            page_url = self._canonical_provider_url(page_url, kind="page")
        else:
            page_url = image_url

        return {
            "page_url": page_url,
            "image_url": image_url,
            "title": _clean_text(settings.get("posterTitle") or "Chinese poster preview"),
        }

    def _canonical_provider_url(self, value, *, kind):
        raw = str(value or "").strip()
        if not raw:
            raise RuntimeError("BacktotheDate provider URL is missing")
        parsed = urlparse(urljoin(BASE_URL, raw))
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("BacktotheDate provider URL authority is invalid") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or (parsed.hostname or "").lower() != "chineseposters.net"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
        ):
            raise RuntimeError("BacktotheDate provider URL authority is invalid")
        if kind == "image":
            valid_path = IMAGE_PATH_RE.fullmatch(parsed.path) is not None
        elif kind == "page":
            valid_path = POSTER_PATH_RE.match(parsed.path) is not None
        else:
            raise ValueError(f"Unknown BacktotheDate provider URL kind: {kind}")
        if not valid_path:
            raise RuntimeError("BacktotheDate provider URL path is invalid")
        return urlunparse(
            ("https", "chineseposters.net", parsed.path, "", parsed.query, "")
        )

    def _display_dimensions(self, device_config):
        return self.get_dimensions(device_config)

    def _select_random_poster(self, settings):
        state = self._read_state()
        discarded_page_urls = self._discarded_url_keys(
            state,
            "discarded_page_urls",
            legacy_keys=("last_page_url", "last_page_urls"),
        )
        discarded_image_urls = self._discarded_url_keys(
            state,
            "discarded_image_urls",
            legacy_keys=("last_image_url", "last_image_urls"),
        )

        theme_urls = self._source_theme_urls(settings)
        if theme_urls:
            poster = self._select_random_theme_poster(theme_urls, discarded_page_urls, discarded_image_urls)
            if poster:
                return poster
            logger.warning("No target-era poster found in configured theme sources; falling back to full poster archive.")

        return self._select_random_archive_poster(settings, discarded_page_urls, discarded_image_urls)

    def _select_random_archive_poster(self, settings, discarded_page_urls, discarded_image_urls):
        max_page = self._get_max_page(settings)
        seen_fallbacks = []

        for _ in range(8):
            page = random.randint(0, max_page)
            list_html = self._fetch_text(POSTERS_URL, params={"page": page})
            links = self._extract_poster_links(list_html)
            if not links:
                continue

            candidates = [
                link
                for link in links
                if self._normalize_history_url(link.get("url")) not in discarded_page_urls
            ] or links
            random.shuffle(candidates)
            for link in candidates[:POSTER_DETAIL_CANDIDATE_LIMIT]:
                detail_html = self._fetch_text(link["url"])
                poster = self._extract_poster_data(detail_html, link["url"])
                if link.get("title") and not poster.get("title"):
                    poster["title"] = link["title"]
                if poster.get("image_url"):
                    page_key = self._normalize_history_url(poster.get("page_url"))
                    image_key = self._normalize_history_url(poster.get("image_url"))
                    if page_key in discarded_page_urls or image_key in discarded_image_urls:
                        seen_fallbacks.append(poster)
                        continue
                    return poster

        if seen_fallbacks:
            logger.info("BacktotheDate found only previously displayed posters in sampled pages; reusing one fallback.")
            return random.choice(seen_fallbacks)

        raise RuntimeError("No poster image link found on sampled pages.")

    def _select_random_theme_poster(self, theme_urls, discarded_page_urls, discarded_image_urls):
        seen_fallbacks = []
        sources = list(theme_urls)
        random.shuffle(sources)

        for source_url in sources:
            try:
                first_html = self._fetch_text(source_url)
                max_page = self._discover_max_page(first_html) or 0
                pages = list(range(max_page + 1))
                random.shuffle(pages)
                for page in pages[:THEME_PAGE_SAMPLE_LIMIT]:
                    html_text = first_html if page == 0 else self._fetch_text(source_url, params={"page": page})
                    links = self._extract_poster_links(html_text)
                    if not links:
                        continue
                    candidates = [
                        link
                        for link in links
                        if self._normalize_history_url(link.get("url")) not in discarded_page_urls
                    ] or links
                    random.shuffle(candidates)
                    for link in candidates[:POSTER_DETAIL_CANDIDATE_LIMIT]:
                        detail_html = self._fetch_text(link["url"])
                        poster = self._extract_poster_data(detail_html, link["url"])
                        if link.get("title") and not poster.get("title"):
                            poster["title"] = link["title"]
                        if not poster.get("image_url"):
                            continue
                        page_key = self._normalize_history_url(poster.get("page_url"))
                        image_key = self._normalize_history_url(poster.get("image_url"))
                        if page_key in discarded_page_urls or image_key in discarded_image_urls:
                            seen_fallbacks.append(poster)
                            continue
                        return poster
            except Exception as exc:
                logger.warning("BacktotheDate target theme source failed %s: %s", source_url, exc)

        if seen_fallbacks:
            logger.info("BacktotheDate target theme sources only found displayed posters; reusing one fallback.")
            return random.choice(seen_fallbacks)
        return None

    def _source_theme_urls(self, settings):
        mode = str(settings.get("sourceMode") or DEFAULT_SOURCE_MODE).strip().lower()
        if mode in {"all", "archive", "all_archive", "legacy"}:
            return []

        custom_urls = self._parse_theme_urls(settings.get("themeUrls"))
        if mode == "custom":
            return custom_urls
        if custom_urls:
            return self._dedupe_urls(MAO_ERA_THEME_URLS + custom_urls)
        return list(MAO_ERA_THEME_URLS)

    def _parse_theme_urls(self, value):
        if not value:
            return []
        if isinstance(value, str):
            raw_items = re.split(r"[\s,]+", value)
        elif isinstance(value, list):
            raw_items = value
        else:
            return []

        urls = []
        for item in raw_items:
            text = str(item or "").strip()
            if not text:
                continue
            url = urljoin(BASE_URL, text)
            parsed = urlparse(url)
            if parsed.netloc.lower() != urlparse(BASE_URL).netloc.lower():
                continue
            if not THEME_PATH_RE.match(parsed.path):
                continue
            urls.append(f"{BASE_URL}{parsed.path.rstrip('/')}")
        return self._dedupe_urls(urls)

    def _dedupe_urls(self, urls):
        result = []
        seen = set()
        for url in urls:
            key = self._normalize_history_url(url)
            if not key or key in seen:
                continue
            result.append(url)
            seen.add(key)
        return result

    def _get_max_page(self, settings):
        configured_page = self._safe_int(settings.get("maxPage"), None, minimum=0, maximum=10000)
        if configured_page is not None:
            return configured_page

        state = self._read_state()
        cached = self._safe_int(state.get("max_page"), None, minimum=0, maximum=10000)
        checked_at = self._parse_datetime(state.get("max_page_checked_at"))
        if cached is not None and checked_at and datetime.now(timezone.utc) - checked_at < MAX_PAGE_CACHE_TTL:
            return cached

        try:
            html_text = self._fetch_text(POSTERS_URL)
            discovered = self._discover_max_page(html_text)
        except Exception as exc:
            logger.warning("Could not discover Chinese Posters page count: %s", exc)
            discovered = None

        max_page = discovered if discovered is not None else cached
        if max_page is None:
            max_page = DEFAULT_MAX_PAGE

        state["max_page"] = max_page
        state["max_page_checked_at"] = datetime.now(timezone.utc).isoformat()
        self._write_state(state)
        return max_page

    def _discover_max_page(self, html_text):
        pages = [int(match.group(1)) for match in re.finditer(r"[?&]page=(\d+)", html_text or "")]
        return max(pages) if pages else None

    def _extract_poster_links(self, html_text):
        parser = _PosterLinkParser()
        parser.feed(html_text or "")

        by_url = {}
        for link in parser.links:
            href = link.get("href") or ""
            try:
                url = self._canonical_provider_url(href, kind="page")
            except RuntimeError:
                continue

            existing = by_url.get(url)
            title = link.get("title") or ""
            if not existing or (title and not existing.get("title")):
                by_url[url] = {"url": url, "title": title}

        return list(by_url.values())

    def _extract_poster_data(self, html_text, page_url):
        parser = _PosterDetailParser()
        parser.feed(html_text or "")
        page_url = self._canonical_provider_url(page_url, kind="page")

        image_url = None
        image_alt = ""
        for image in parser.images:
            src = image.get("src") or ""
            try:
                image_url = self._canonical_provider_url(src, kind="image")
            except RuntimeError:
                continue
            image_alt = image.get("alt") or ""
            break

        title = parser.title or _clean_text(image_alt)
        return {
            "page_url": page_url,
            "image_url": image_url,
            "title": title,
        }

    def _compose_display_image(self, poster, image, dimensions, settings):
        return self._compose_single_display_image(poster, image, dimensions, settings), [poster]

    def _compose_single_display_image(self, poster, image, dimensions, settings):
        fit_mode = self._fit_mode(settings)
        if fit_mode in {"triptych", "three_vertical", "three_posters", "gallery"} and not self._is_portrait(image):
            return self._fit_landscape(image, dimensions, settings)
        return self._fit_image(image, dimensions, settings)

    def _load_poster_image(self, image_url, dimensions):
        image = self.image_loader.from_url(
            image_url,
            dimensions,
            timeout_ms=40000,
            resize=False,
            headers=REQUEST_HEADERS,
        )
        if not image:
            return None

        return image

    def _normalize_image(self, image):
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")
        return image

    def _is_portrait(self, image):
        return image.size[0] < image.size[1]

    def _fit_image(self, image, dimensions, settings):
        image = self._normalize_image(image)

        fit_mode = self._fit_mode(settings)
        if fit_mode == "cover":
            return ImageOps.fit(image, dimensions, method=Image.LANCZOS)
        if fit_mode in {"rotate_portrait", "rotate", "mosaic", "wall", "auto"}:
            if self._is_portrait(image):
                image = image.rotate(90, expand=True)
                return self._fit_blur_contain(image, dimensions, settings)
            return self._fit_plain_contain(image, dimensions, settings)
        if fit_mode in {"landscape", "adaptive", "ambient"}:
            return self._fit_landscape(image, dimensions, settings)

        return self._fit_plain_contain(image, dimensions, settings)

    def _fit_mode(self, settings):
        return str(settings.get("fitMode") or DEFAULT_FIT_MODE).strip().lower()

    def _fit_plain_contain(self, image, dimensions, settings):
        fitted = ImageOps.contain(image, dimensions, method=Image.LANCZOS)
        background = self._background(settings).copy()
        if background.size != dimensions:
            background = Image.new("RGB", dimensions, background.getpixel((0, 0)))
        x = (dimensions[0] - fitted.size[0]) // 2
        y = (dimensions[1] - fitted.size[1]) // 2
        background.paste(fitted, (x, y))
        return background

    def _fit_landscape(self, image, dimensions, settings):
        return self._fit_plain_contain(image, dimensions, settings)

    def _compose_triptych_display_image(self, poster_images, dimensions, settings):
        width, height = dimensions
        canvas = self._triptych_backdrop([image for _poster, image in poster_images], dimensions, settings)
        column_width = width // TRIPTYCH_POSTER_COUNT

        for index, (_poster, image) in enumerate(poster_images[:TRIPTYCH_POSTER_COUNT]):
            x0 = index * column_width
            target_width = column_width if index < TRIPTYCH_POSTER_COUNT - 1 else width - x0
            fitted = ImageOps.contain(image, (target_width, height), method=Image.LANCZOS)
            x = x0 + (target_width - fitted.size[0]) // 2
            y = (height - fitted.size[1]) // 2
            canvas.paste(fitted, (x, y))

        return canvas

    def _triptych_backdrop(self, images, dimensions, settings):
        background = self._background(settings)
        if background.size != dimensions:
            background = Image.new("RGB", dimensions, background.getpixel((0, 0)))
        if not images:
            return background

        backdrop = ImageOps.fit(images[0], dimensions, method=Image.LANCZOS)
        blur_radius = max(8, min(dimensions) // 26)
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        backdrop = ImageEnhance.Color(backdrop).enhance(0.35)
        backdrop = ImageEnhance.Contrast(backdrop).enhance(0.75)
        return Image.blend(backdrop, background, 0.72)

    def _fit_blur_contain(self, image, dimensions, settings, max_width_ratio=1.0):
        backdrop = ImageOps.fit(image, dimensions, method=Image.LANCZOS)
        blur_radius = max(2, min(dimensions) // 90)
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        backdrop = ImageEnhance.Color(backdrop).enhance(0.35)
        backdrop = ImageEnhance.Contrast(backdrop).enhance(0.82)

        wash_color = (255, 255, 255) if (settings.get("backgroundColor") or "white").lower() != "black" else (18, 18, 18)
        wash = Image.new("RGB", dimensions, wash_color)
        backdrop = Image.blend(backdrop, wash, 0.42)

        max_width = int(dimensions[0] * max_width_ratio)
        inset_bounds = (max(1, max_width), dimensions[1])
        poster = ImageOps.contain(image, inset_bounds, method=Image.LANCZOS)

        canvas = backdrop.copy()
        x = (dimensions[0] - poster.size[0]) // 2
        y = (dimensions[1] - poster.size[1]) // 2
        matte = 8
        draw = ImageDraw.Draw(canvas)
        box = (
            max(0, x - matte),
            max(0, y - matte),
            min(dimensions[0] - 1, x + poster.size[0] + matte - 1),
            min(dimensions[1] - 1, y + poster.size[1] + matte - 1),
        )
        draw.rectangle(box, fill=(255, 255, 255), outline=(0, 0, 0), width=2)
        canvas.paste(poster, (x, y))
        return canvas

    def _background(self, settings):
        color = (settings.get("backgroundColor") or "white").lower()
        if color == "black":
            return Image.new("RGB", (1, 1), (0, 0, 0))
        return Image.new("RGB", (1, 1), (255, 255, 255))

    def _fetch_text(self, url, params=None):
        session = get_http_session()
        response = session.get(url, params=params, timeout=20, headers=REQUEST_HEADERS)
        response.raise_for_status()
        if not response.encoding:
            response.encoding = "utf-8"
        return response.text

    def _state_path(self):
        return self.data_dir() / ".backtothedate_state.json"

    def _legacy_state_path(self):
        return self.cache_dir() / ".backtothedate_state.json"

    def _read_state(self):
        path = self._state_path()
        legacy_path = self._legacy_state_path()
        try:
            path.lstat()
            durable_exists = True
        except FileNotFoundError:
            durable_exists = False
        except OSError as exc:
            raise RuntimeError(f"Could not safely inspect BacktotheDate state {path}") from exc
        if not durable_exists and legacy_path != path:
            try:
                legacy_path.lstat()
                path = legacy_path
                durable_exists = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise RuntimeError(
                    f"Could not safely inspect BacktotheDate state {legacy_path}"
                ) from exc
        if not durable_exists:
            return {}
        try:
            return read_bounded_json_object(path)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not safely read BacktotheDate state {path}") from exc

    def _write_state(self, state):
        if not isinstance(state, dict):
            raise TypeError("BacktotheDate state must be a dictionary")
        validate_state_payload_size(state)
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, state, mode=0o600)

    def _remember_success(self, posters):
        if isinstance(posters, dict):
            posters = [posters]
        first = posters[0] if posters else {}
        state = self._read_state()
        existing_page_urls = self._url_list(state.get("discarded_page_urls"))
        existing_page_urls.extend(self._url_list(state.get("last_page_url")))
        existing_page_urls.extend(self._url_list(state.get("last_page_urls")))
        existing_image_urls = self._url_list(state.get("discarded_image_urls"))
        existing_image_urls.extend(self._url_list(state.get("last_image_url")))
        existing_image_urls.extend(self._url_list(state.get("last_image_urls")))

        state["last_page_url"] = first.get("page_url")
        state["last_image_url"] = first.get("image_url")
        state["last_title"] = first.get("title")
        state["last_page_urls"] = [poster.get("page_url") for poster in posters if poster.get("page_url")]
        state["last_image_urls"] = [poster.get("image_url") for poster in posters if poster.get("image_url")]
        state["last_displayed_at"] = datetime.now(timezone.utc).isoformat()
        state["discarded_page_urls"] = self._append_unique_urls(
            existing_page_urls,
            state["last_page_urls"],
        )
        state["discarded_image_urls"] = self._append_unique_urls(
            existing_image_urls,
            state["last_image_urls"],
        )
        self._write_state(state)

    def _discarded_url_keys(self, state, key, legacy_keys=()):
        urls = self._url_list(state.get(key))
        for legacy_key in legacy_keys:
            urls.extend(self._url_list(state.get(legacy_key)))
        return {
            normalized
            for normalized in (self._normalize_history_url(url) for url in urls)
            if normalized
        }

    def _append_unique_urls(self, existing, additions):
        result = []
        seen = set()
        for url in self._url_list(existing) + self._url_list(additions):
            normalized = self._normalize_history_url(url)
            if not normalized or normalized in seen:
                continue
            result.append(url)
            seen.add(normalized)
        return result[-MAX_HISTORY_URLS:]

    def _url_list(self, value):
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str) and item.strip()]
        if isinstance(value, str) and value.strip():
            return [value]
        return []

    def _normalize_history_url(self, url):
        if not isinstance(url, str):
            return ""
        url = url.strip()
        if not url:
            return ""

        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            path = parsed.path.rstrip("/")
            return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"
        return url.rstrip("/")

    def _parse_datetime(self, value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    def _safe_int(self, value, default, minimum=None, maximum=None):
        if value in (None, ""):
            return default
        try:
            result = int(value)
        except (TypeError, ValueError):
            return default
        if minimum is not None:
            result = max(minimum, result)
        if maximum is not None:
            result = min(maximum, result)
        return result
