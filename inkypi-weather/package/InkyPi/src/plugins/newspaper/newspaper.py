from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationPreparation,
    get_presentation_instance_uuid,
)
from plugins.base_plugin.theme_presentation import apply_media_theme_chrome
from plugins.plugin_settings import resolve_refresh_on_display
from datetime import datetime, timedelta, timezone
import html
from html.parser import HTMLParser
import http.client
import ipaddress
from io import BytesIO
from pathlib import Path
import socket
import ssl
import sys
import time
from urllib.parse import urljoin, urlparse, urlsplit
from security.ssrf import get_ssrf_policy
from utils.app_utils import get_font
from utils.browser_renderer import get_browser_renderer
from utils.image_utils import text_width
from runtime.refresh_contracts import TaskContext
from PIL import Image, ImageDraw, ImageFont
import hashlib
import json
import logging
import os
import re
from plugins.newspaper.constants import NEWSPAPERS
from plugins.newspaper.presentation_bank import (
    FRESH_SECONDS,
    READY_TARGET,
    REFILL_THRESHOLD,
    NewspaperPresentationBank,
    instance_profile_fingerprint,
    read_state,
    settings_fingerprint,
    settings_key,
    write_state,
)
from utils.http_client import HttpStatusError

logger = logging.getLogger(__name__)

FREEDOM_FORUM_URL = "https://cdn.freedomforum.org/dfp/jpg{}/lg/{}.jpg"
LYWB_A01_PDF_URL = "https://lywb.lyd.com.cn/images2/2/{year_month}/{day}/A01/{stamp}A01_pdf.pdf"
LYWB_LOOKBACK_DAYS = 10
NEWS_FRONTPAGE_ROTATION_VERSION = "news-frontpage-rotation-v1"
MAX_DATA_SECONDS = 90.0
MAX_BROWSER_SECONDS = 40.0
MAX_HTTP_SECONDS = 20.0
MAX_DATA_SOURCES = 4
MAX_BROWSER_SOURCES = 1
MAX_HTTP_SOURCES = 3
MAX_REDIRECTS = 4
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_PNG_BYTES = 16 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 32_000_000
REQUEST_HEADERS = {"User-Agent": "InkyPi News Front Pages/2.0"}
DEFAULT_MEDIA_SOURCES = """BBC News|url|https://www.bbc.com/news
CNN|url|https://www.cnn.com
CCTV News|url|https://news.cctv.com/index.shtml
Xinhua|url|https://www.xinhuanet.com/
Luoyang Evening News|lywb|A01
China Daily|newspaper|chi_cd
People's Daily|newspaper|chi_pd
The New York Times|newspaper|ny_nyt
The Washington Post|newspaper|dc_wp
USA Today|newspaper|usat"""

TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "\u4e26": "\u5e76",
        "\u4e9e": "\u4e9a",
        "\u4f48": "\u5e03",
        "\u50f9": "\u4ef7",
        "\u5104": "\u4ebf",
        "\u5167": "\u5185",
        "\u5169": "\u4e24",
        "\u52d5": "\u52a8",
        "\u52d9": "\u52a1",
        "\u570b": "\u56fd",
        "\u5831": "\u62a5",
        "\u5834": "\u573a",
        "\u5c0e": "\u5bfc",
        "\u5c08": "\u4e13",
        "\u5c0d": "\u5bf9",
        "\u5c64": "\u5c42",
        "\u5ee3": "\u5e7f",
        "\u5f8c": "\u540e",
        "\u6771": "\u4e1c",
        "\u689d": "\u6761",
        "\u696d": "\u4e1a",
        "\u6a19": "\u6807",
        "\u6a5f": "\u673a",
        "\u6aa2": "\u68c0",
        "\u6b50": "\u6b27",
        "\u6b0a": "\u6743",
        "\u6c23": "\u6c14",
        "\u6fdf": "\u6d4e",
        "\u70ba": "\u4e3a",
        "\u7522": "\u4ea7",
        "\u756b": "\u753b",
        "\u767c": "\u53d1",
        "\u7bc0": "\u8282",
        "\u7d00": "\u7eaa",
        "\u7d1a": "\u7ea7",
        "\u7d50": "\u7ed3",
        "\u7d71": "\u7edf",
        "\u7d93": "\u7ecf",
        "\u7dda": "\u7ebf",
        "\u7e3d": "\u603b",
        "\u7db2": "\u7f51",
        "\u8077": "\u804c",
        "\u805e": "\u95fb",
        "\u8207": "\u4e0e",
        "\u842c": "\u4e07",
        "\u83ef": "\u534e",
        "\u862d": "\u5170",
        "\u969b": "\u9645",
        "\u8655": "\u5904",
        "\u89c0": "\u89c2",
        "\u8a08": "\u8ba1",
        "\u8a0a": "\u8baf",
        "\u8a2d": "\u8bbe",
        "\u8a55": "\u8bc4",
        "\u8a71": "\u8bdd",
        "\u8a9e": "\u8bed",
        "\u8abf": "\u8c03",
        "\u8ad6": "\u8bba",
        "\u8b70": "\u8bae",
        "\u8b8a": "\u53d8",
        "\u8ca1": "\u8d22",
        "\u8cbf": "\u8d38",
        "\u8cc7": "\u8d44",
        "\u8cfd": "\u8d5b",
        "\u8eca": "\u8f66",
        "\u8f49": "\u8f6c",
        "\u8f09": "\u8f7d",
        "\u9078": "\u9009",
        "\u91ab": "\u533b",
        "\u91cb": "\u91ca",
        "\u91dd": "\u9488",
        "\u9577": "\u957f",
        "\u9580": "\u95e8",
        "\u958b": "\u5f00",
        "\u9593": "\u95f4",
        "\u95dc": "\u5173",
        "\u96fb": "\u7535",
        "\u9801": "\u9875",
        "\u9818": "\u9886",
        "\u982d": "\u5934",
        "\u983b": "\u9891",
        "\u984c": "\u9898",
        "\u98a8": "\u98ce",
        "\u9ad4": "\u4f53",
        "\u9ede": "\u70b9",
    }
)

MOJIBAKE_MARKERS = (
    "\ufffd",
    "\u00c3",
    "\u00c2",
    "\u00e5",
    "\u00e6",
    "\u00e7",
    "\u00e8",
    "\u00e9",
    "\u5d15",
    "\u5a34",
    "\u626e",
    "\u701b",
    "\u752f",
    "\u93c2",
)

_BLOCKED_BROWSER_TAGS = frozenset({"base", "embed", "iframe", "math", "object", "script", "style", "svg", "template"})
_VOID_BROWSER_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
_NETWORK_BROWSER_ATTRIBUTES = frozenset(
    {
        "action",
        "background",
        "cite",
        "data",
        "download",
        "formaction",
        "href",
        "longdesc",
        "manifest",
        "ping",
        "poster",
        "profile",
        "src",
        "srcdoc",
        "srcset",
        "usemap",
    }
)


class _NetworkClosedHTMLSanitizer(HTMLParser):
    """Rebuild provider HTML without executable or navigation-capable tokens."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = str(tag or "").lower()
        if self.blocked_depth:
            if tag not in _VOID_BROWSER_TAGS:
                self.blocked_depth += 1
            return
        if tag in _BLOCKED_BROWSER_TAGS or ":" in tag:
            if tag not in _VOID_BROWSER_TAGS:
                self.blocked_depth = 1
            return
        if not re.fullmatch(r"[a-z][a-z0-9-]*", tag):
            return
        normalized = [(str(name or "").lower(), value) for name, value in attrs]
        if tag == "meta" and any(
            name == "http-equiv" and str(value or "").strip().lower() == "refresh" for name, value in normalized
        ):
            return
        safe_attrs = []
        for name, value in normalized:
            if (
                not name
                or name in _NETWORK_BROWSER_ATTRIBUTES
                or name == "style"
                or name.startswith("on")
                or name.startswith("xmlns")
                or ":" in name
            ):
                continue
            if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
                continue
            escaped = html.escape(str(value or ""), quote=True)
            safe_attrs.append(f' {name}="{escaped}"')
        self.parts.append(f"<{tag}{''.join(safe_attrs)}>")

    def handle_startendtag(self, tag, attrs):
        normalized_tag = str(tag or "").lower()
        if self.blocked_depth or normalized_tag in _BLOCKED_BROWSER_TAGS or ":" in normalized_tag:
            return
        before = len(self.parts)
        self.handle_starttag(tag, attrs)
        if len(self.parts) > before and self.parts[-1].endswith(">"):
            self.parts[-1] = f"{self.parts[-1][:-1]}/>"

    def handle_endtag(self, tag):
        tag = str(tag or "").lower()
        if self.blocked_depth:
            if tag not in _VOID_BROWSER_TAGS:
                self.blocked_depth -= 1
            return
        if (
            tag not in _BLOCKED_BROWSER_TAGS
            and ":" not in tag
            and re.fullmatch(r"[a-z][a-z0-9-]*", tag)
            and tag not in _VOID_BROWSER_TAGS
        ):
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.blocked_depth:
            self.parts.append(html.escape(str(data or ""), quote=False))

    def result(self):
        return "".join(self.parts)


class _NetworkClosedHTMLAudit(HTMLParser):
    """Fail closed if a future sanitizer regression leaves an active token."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.unsafe = False

    def handle_starttag(self, tag, attrs):
        tag = str(tag or "").lower()
        normalized = [(str(name or "").lower(), value) for name, value in attrs]
        if tag in _BLOCKED_BROWSER_TAGS or ":" in tag:
            self.unsafe = True
        if tag == "meta" and any(
            name == "http-equiv" and str(value or "").strip().lower() == "refresh" for name, value in normalized
        ):
            self.unsafe = True
        for name, _value in normalized:
            if (
                name in _NETWORK_BROWSER_ATTRIBUTES
                or name == "style"
                or name.startswith("on")
                or name.startswith("xmlns")
                or ":" in name
            ):
                self.unsafe = True

    handle_startendtag = handle_starttag


def _enabled(value, default=False):
    if value is None:
        return default
    return value is True or str(value).lower() in {"1", "true", "on", "yes", "rotate"}


def _remaining_timeout(deadline, clock, configured):
    configured = max(0.001, float(configured))
    if deadline is None:
        return configured
    remaining = float(deadline) - float(clock())
    if remaining <= 0:
        raise RuntimeError("Newspaper DATA deadline is exhausted")
    return max(0.001, min(configured, remaining))


class _PinnedResponse:
    """HTTP(S) response pinned to one SSRF-approved numeric address."""

    def __init__(self, response, connection, url, *, deadline, clock, timeout):
        self._response = response
        self._connection = connection
        self.url = url
        self.status_code = int(response.status)
        self.headers = response.headers
        self._deadline = deadline
        self._clock = clock
        self._timeout = timeout

    @classmethod
    def open(cls, approved, *, headers, deadline, clock, timeout):
        last_error = None
        for address in approved.addresses:
            raw_socket = None
            connection = None
            try:
                raw_socket = socket.create_connection(
                    (address, approved.port),
                    timeout=_remaining_timeout(deadline, clock, timeout),
                )
                _remaining_timeout(deadline, clock, timeout)
                raw_socket.settimeout(_remaining_timeout(deadline, clock, timeout))
                if approved.scheme == "https":
                    connection = ssl.create_default_context().wrap_socket(
                        raw_socket,
                        server_hostname=approved.hostname,
                    )
                    raw_socket = None
                    _remaining_timeout(deadline, clock, timeout)
                else:
                    connection = raw_socket
                    raw_socket = None
                connection.settimeout(_remaining_timeout(deadline, clock, timeout))
                parsed = urlsplit(approved.normalized_url)
                target = parsed.path or "/"
                if parsed.query:
                    target = f"{target}?{parsed.query}"
                lines = [f"GET {target} HTTP/1.1", f"Host: {approved.authority}"]
                for name, value in headers.items():
                    name = str(name)
                    value = str(value)
                    if name.lower() in {"host", "connection"}:
                        continue
                    if not name or any(marker in name + value for marker in ("\r", "\n")):
                        raise RuntimeError("Newspaper request headers are invalid")
                    lines.append(f"{name}: {value}")
                lines.append("Connection: close")
                connection.settimeout(_remaining_timeout(deadline, clock, timeout))
                connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
                _remaining_timeout(deadline, clock, timeout)
                connection.settimeout(_remaining_timeout(deadline, clock, timeout))
                response = http.client.HTTPResponse(connection)
                response.begin()
                _remaining_timeout(deadline, clock, timeout)
                return cls(
                    response,
                    connection,
                    approved.normalized_url,
                    deadline=deadline,
                    clock=clock,
                    timeout=timeout,
                )
            except RuntimeError:
                if connection is not None:
                    connection.close()
                elif raw_socket is not None:
                    raw_socket.close()
                raise
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                if connection is not None:
                    connection.close()
                elif raw_socket is not None:
                    raw_socket.close()
                last_error = exc
        raise RuntimeError("Newspaper approved target could not be reached") from last_error

    def iter_content(self, chunk_size):
        while True:
            self._connection.settimeout(_remaining_timeout(self._deadline, self._clock, self._timeout))
            chunk = self._response.read(chunk_size)
            _remaining_timeout(self._deadline, self._clock, self._timeout)
            if not chunk:
                return
            yield chunk

    def close(self):
        try:
            self._response.close()
        finally:
            self._connection.close()


class _DataAttemptBudget:
    """One shared DATA quota for protected recovery and ordinary refill."""

    def __init__(self):
        self.total = 0
        self.browser = 0
        self.http = 0

    def can_claim(self, source):
        if self.total >= MAX_DATA_SOURCES:
            return False
        if source.get("type") == "url":
            return self.browser < MAX_BROWSER_SOURCES
        return self.http < MAX_HTTP_SOURCES

    def claim(self, source):
        if not self.can_claim(source):
            return False
        self.total += 1
        if source.get("type") == "url":
            self.browser += 1
        else:
            self.http += 1
        return True


class Newspaper(BasePlugin):
    def wants_refresh_on_display(self, settings):
        settings = settings or {}
        rotation_default = str(settings.get("mediaRotationMode") or "rotate").lower() != "single"
        return resolve_refresh_on_display(
            settings,
            self.config,
            base_default=rotation_default,
        )

    def generate_image(self, settings, device_config):
        settings = settings or {}
        instance_uuid = get_presentation_instance_uuid(settings)
        if instance_uuid is None:
            return self._generate_stateless_preview(settings, device_config)
        if settings.get("_theme_render_only") is True:
            return self._generate_theme_only(settings, device_config)
        deadline = self._monotonic() + MAX_DATA_SECONDS
        sources = self._sources_for_settings(settings)
        if not sources:
            raise RuntimeError("Newspaper input not provided.")
        dimensions = self.get_dimensions(device_config)
        bank = self._presentation_bank(settings, sources, dimensions)

        def check_deadline():
            _remaining_timeout(deadline, self._monotonic, MAX_DATA_SECONDS)

        document, profile = bank.load_for_data()
        profile_before = json.loads(json.dumps(profile))
        transaction = bank.transaction()
        captured_keys = set()
        attempt_budget = _DataAttemptBudget()
        try:
            protected_complete = self._recover_protected_selections(
                bank,
                profile,
                device_config,
                transaction=transaction,
                deadline=deadline,
                deadline_check=check_deadline,
                captured_keys=captured_keys,
                attempt_budget=attempt_budget,
            )
            if not protected_complete:
                transaction.commit(deadline_check=check_deadline)
                raise RuntimeError("Newspaper protected recovery exceeded the shared DATA quota")
            ready = bank.ready_records(profile, prune=True)
            fresh_ready = [item for item in ready if item.get("provenance") == "fresh_cache"]
            if len(fresh_ready) < REFILL_THRESHOLD:
                profile["refill_in_progress"] = True
            if profile.get("refill_in_progress") is True:
                cursor = int(profile.get("refill_cursor") or 0) % len(sources)
                scan_cursor = cursor
                first_unattempted = None
                scanned = 0
                while (
                    scanned < len(sources)
                    and attempt_budget.total < MAX_DATA_SOURCES
                    and len(fresh_ready) < READY_TARGET
                ):
                    check_deadline()
                    source_index = scan_cursor
                    source = sources[source_index]
                    scan_cursor = (scan_cursor + 1) % len(sources)
                    scanned += 1
                    is_browser = source["type"] == "url"
                    if not attempt_budget.claim(source):
                        if first_unattempted is None:
                            first_unattempted = source_index
                        continue
                    if is_browser:
                        source_budget = MAX_BROWSER_SECONDS
                    else:
                        source_budget = MAX_HTTP_SECONDS
                    source_deadline = min(
                        deadline,
                        self._monotonic() + source_budget,
                    )
                    try:
                        image = self._fetch_source_image(
                            source,
                            device_config,
                            deadline=source_deadline,
                        )
                        check_deadline()
                        if image is None:
                            continue
                        record = bank.ingest(
                            profile,
                            source,
                            image,
                            transaction=transaction,
                            deadline_check=check_deadline,
                        )
                        ready = [item for item in ready if item["source"]["id"] != source["id"]]
                        fresh_record = {**record, "provenance": "fresh_cache"}
                        ready.append(fresh_record)
                        fresh_ready = [item for item in fresh_ready if item["source"]["id"] != source["id"]]
                        fresh_ready.append(fresh_record)
                        captured_keys.add(record["record_key"])
                    except Exception as exc:
                        if self._monotonic() >= deadline:
                            raise RuntimeError("Newspaper DATA deadline is exhausted") from exc
                        logger.warning(
                            "Newspaper source failed for %s: %s",
                            source["name"],
                            exc,
                        )
                profile["refill_cursor"] = first_unattempted if first_unattempted is not None else scan_cursor
            bank.cleanup(
                document,
                profile,
                transaction=transaction,
                deadline_check=check_deadline,
            )
            ready = bank.ready_records(profile, prune=True)
            fresh_ready = [item for item in ready if item.get("provenance") == "fresh_cache"]
            profile["refill_in_progress"] = len(fresh_ready) < READY_TARGET
            if not ready:
                placeholder = self._render_metadata_placeholder(
                    sources[int(profile.get("refill_cursor") or 0) % len(sources)],
                    device_config,
                )
                bank.save(
                    document,
                    deadline_check=check_deadline,
                    transaction=transaction,
                )
                placeholder.info["inkypi_source_provenance"] = "local_fallback"
                return placeholder
            current = bank.ensure_current(profile, ready)
            record, image = bank.selection_media(profile, current)
            check_deadline()
            bank.save(
                document,
                deadline_check=check_deadline,
                transaction=transaction,
            )
        except Exception:
            profile.clear()
            profile.update(profile_before)
            transaction.rollback()
            raise

        provenance = self._record_provenance(record)
        image.info["inkypi_source_provenance"] = "live" if record["record_key"] in captured_keys else provenance
        return image

    def presentation_mode(self, settings):
        del settings
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
        sources = self._sources_for_settings(settings)
        dimensions = self.get_dimensions(device_config)
        bank = self._presentation_bank(settings, sources, dimensions)
        document, profile = bank.load_warm()
        ready = bank.ready_records(profile, prune=False)
        fresh_ready = [item for item in ready if item.get("provenance") == "fresh_cache"]
        pending = bank.pending_for_request(profile, request.request_id)
        fresh_keys = {item["record_key"] for item in fresh_ready}
        if pending is not None and pending.get("record_key") not in fresh_keys:
            raise RuntimeError("Newspaper pending presentation is not fresh")
        if pending is None and not fresh_ready:
            raise RuntimeError("Newspaper presentation bank has no fresh media")
        selection = pending or bank.choose_selection(profile, fresh_ready)
        image = self._render_bank_selection(bank, profile, selection)
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
        record, _media = bank.selection_media(profile, selection)
        image.info["inkypi_source_provenance"] = self._record_provenance(record)
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
        instance_uuid = get_presentation_instance_uuid(settings or {})
        if instance_uuid is None:
            raise RuntimeError("Newspaper receipt reconciliation requires trusted instance identity")
        path = self._presentation_state_path()
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        document = read_state(path)
        if NewspaperPresentationBank.reconcile_document(
            document,
            receipt,
            instance_uuid,
        ):
            write_state(path, document)
        return None

    def _sources_for_settings(self, settings):
        if self._rotation_enabled(settings):
            return self._parse_media_sources(settings.get("mediaSources") or DEFAULT_MEDIA_SOURCES)
        slug = str(settings.get("newspaperSlug") or "").strip().upper()
        if not slug:
            return []
        return [
            {
                "id": f"newspaper:{slug}",
                "name": slug,
                "type": "newspaper",
                "value": slug,
            }
        ]

    def _presentation_bank(self, settings, sources, dimensions):
        instance_uuid = get_presentation_instance_uuid(settings)
        if instance_uuid is None:
            raise RuntimeError("Newspaper bank requires trusted instance identity")
        key = settings_key(settings, sources)
        base = settings_fingerprint(settings, sources, dimensions)
        fingerprint = instance_profile_fingerprint(base, instance_uuid)
        return NewspaperPresentationBank(
            self._presentation_state_path(),
            self._presentation_media_dir(),
            fingerprint=fingerprint,
            base_fingerprint=base,
            profile_settings_key=key,
            instance_uuid=instance_uuid,
            now=self._now_utc,
        )

    def _presentation_state_path(self):
        return self.data_dir(create=False) / ".newspaper_presentation_state.json"

    def _presentation_media_dir(self):
        return self.data_dir(leaf="presentation-media", create=False)

    def _render_bank_selection(self, bank, profile, selection):
        _record, image = bank.selection_media(profile, selection)
        return image

    def _recover_protected_selections(
        self,
        bank,
        profile,
        device_config,
        *,
        transaction,
        deadline,
        deadline_check,
        captured_keys,
        attempt_budget,
    ):
        recovered = {}
        for selection_name in ("current_selection", "pending_selection"):
            selection = profile.get(selection_name)
            if not isinstance(selection, dict):
                continue
            old_key = selection.get("record_key")
            if old_key in recovered:
                selection["record_key"] = recovered[old_key]
                continue
            record = next(
                (item for item in profile.get("records") or [] if item.get("record_key") == old_key),
                None,
            )
            if record is None:
                raise RuntimeError(f"Newspaper protected {selection_name} metadata is missing")
            try:
                bank.load_media(record)
                continue
            except RuntimeError:
                pass
            source = bank.normalize_source(record.get("source"))
            if not attempt_budget.claim(source):
                return False
            source_budget = MAX_BROWSER_SECONDS if source["type"] == "url" else MAX_HTTP_SECONDS
            source_deadline = min(deadline, self._monotonic() + source_budget)
            try:
                image = self._fetch_source_image(
                    source,
                    device_config,
                    deadline=source_deadline,
                )
                deadline_check()
                if image is None:
                    raise RuntimeError("provider returned no image")
                if not bank.media_exists(record):
                    bank.rehydrate_missing_media(
                        record,
                        image,
                        transaction=transaction,
                        deadline_check=deadline_check,
                    )
                    replacement_key = old_key
                else:
                    replacement = bank.ingest(
                        profile,
                        source,
                        image,
                        transaction=transaction,
                        deadline_check=deadline_check,
                    )
                    replacement_key = replacement["record_key"]
            except Exception as exc:
                raise RuntimeError(f"Newspaper protected {selection_name} recovery failed") from exc
            selection["record_key"] = replacement_key
            recovered[old_key] = replacement_key
            captured_keys.add(replacement_key)
        return True

    def _generate_theme_only(self, settings, device_config):
        sources = self._sources_for_settings(settings)
        dimensions = self.get_dimensions(device_config)
        bank = self._presentation_bank(settings, sources, dimensions)
        _document, profile = bank.load_warm()
        current = profile.get("current_selection")
        if current is None:
            raise RuntimeError("Newspaper theme redraw has no current selection")
        record, image = bank.selection_media(profile, current)
        theme = settings.get("_inkypi_theme") or self.resolve_theme(
            settings,
            device_config,
            now=self._now_utc(),
        )
        image = apply_media_theme_chrome(
            image,
            self.get_plugin_id(),
            theme,
            dimensions,
        )
        mode = theme.get("mode")
        if mode in {"day", "night"}:
            image.info["inkypi_theme_mode"] = mode
        image.info["inkypi_source_provenance"] = self._record_provenance(record)
        return image

    def _generate_stateless_preview(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)
        image = Image.new("RGB", dimensions, "white")
        draw = ImageDraw.Draw(image)
        title = str(settings.get("newspaperSlug") or "Today's Newspaper")[:80]
        draw.rectangle((0, 0, dimensions[0], 72), fill="black")
        draw.text((20, 20), title, fill="white", font=self._font(28, bold=True))
        draw.text(
            (20, 100),
            "Preview uses no browser or provider.",
            fill="black",
            font=self._font(18),
        )
        image.info["inkypi_source_provenance"] = "local_fallback"
        return image

    def _render_metadata_placeholder(self, source, device_config):
        dimensions = self.get_dimensions(device_config)
        image = Image.new("RGB", dimensions, "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, dimensions[0], 72), fill="black")
        draw.text(
            (20, 18),
            str(source.get("name") or "Today's Newspaper")[:80],
            fill="white",
            font=self._font(28, bold=True),
        )
        draw.text(
            (20, 102),
            "Source metadata available; capture unavailable.",
            fill="black",
            font=self._font(18),
        )
        draw.text(
            (20, 138),
            str(source.get("value") or "")[:100],
            fill="black",
            font=self._font(14),
        )
        return image

    def _record_provenance(self, record):
        downloaded = datetime.fromisoformat(record["downloaded_at"])
        if downloaded.tzinfo is None:
            downloaded = downloaded.replace(tzinfo=timezone.utc)
        age = (self._now_utc() - downloaded.astimezone(timezone.utc)).total_seconds()
        return "fresh_cache" if 0 <= age <= FRESH_SECONDS else "stale_cache"

    def _now_utc(self):
        return datetime.now(timezone.utc)

    def _monotonic(self):
        return time.monotonic()

    def _rotation_enabled(self, settings):
        mode = settings.get("mediaRotationMode")
        if mode:
            return str(mode).lower() != "single"
        return _enabled(settings.get("mediaRotationEnabled"), default=True)

    def _parse_media_sources(self, sources_text):
        sources = []
        seen = set()
        for line in (sources_text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split("|")]
            name = ""
            source_type = ""
            value = ""

            if len(parts) >= 3:
                name, source_type, value = parts[0], parts[1].lower(), parts[2]
            elif len(parts) == 2:
                name, value = parts
                source_type = "url" if value.startswith(("http://", "https://")) else "newspaper"
            else:
                value = parts[0]
                source_type = "url" if value.startswith(("http://", "https://")) else "newspaper"

            if source_type in {"web", "website", "screenshot"}:
                source_type = "url"
            if source_type in {"headline", "headlines", "text"}:
                source_type = "headlines"
            if source_type in {"luoyang", "luoyang_evening_news", "lywb"}:
                source_type = "lywb"
            if source_type in {"paper", "slug", "frontpage"}:
                source_type = "newspaper"
            if source_type not in {"url", "headlines", "lywb", "newspaper"} or not value:
                logger.warning("Ignoring invalid media source line: %s", line)
                continue

            if source_type in {"url", "headlines"}:
                if not value.startswith(("http://", "https://")):
                    logger.warning("Ignoring media source with invalid URL: %s", line)
                    continue
                default_name = urlparse(value).netloc or value
                identity_value = value
            elif source_type == "lywb":
                value = value.upper()
                default_name = "Luoyang Evening News"
                identity_value = value
            else:
                value = value.upper()
                default_name = value
                identity_value = value

            source_id = f"{source_type}:{identity_value}"
            if source_id in seen:
                continue
            seen.add(source_id)
            sources.append(
                {
                    "id": source_id,
                    "name": name or default_name,
                    "type": source_type,
                    "value": value,
                }
            )

        return sources

    def _generate_rotating_image(self, sources, device_config):
        errors = []
        for _ in range(len(sources)):
            source = self._select_next_source(sources)
            try:
                image = self._fetch_source_image(source, device_config)
            except Exception as exc:
                logger.warning("News front page failed for %s: %s", source["name"], exc)
                errors.append(f"{source['name']}: {exc}")
                continue

            if image:
                logger.info("Selected news front page: %s", source["name"])
                return image

            errors.append(f"{source['name']}: no image")

        detail = "; ".join(errors[-4:])
        raise RuntimeError(f"No news front page could be fetched. {detail}")

    def _fetch_source_image(self, source, device_config, *, deadline=None):
        if source["type"] == "headlines":
            headlines = self._fetch_web_headlines(source["value"], deadline=deadline)
            if headlines:
                return self._render_headlines_page(source, headlines, device_config)
            return None

        if source["type"] == "url":
            image = self._fetch_url_screenshot(
                source["value"],
                device_config,
                deadline=deadline,
            )
            if image:
                return image
            return None

        if source["type"] == "lywb":
            return self._fetch_luoyang_evening_news_cover(
                device_config,
                deadline=deadline,
            )

        return self._fetch_newspaper_cover(
            source["value"],
            device_config,
            deadline=deadline,
        )

    def _fetch_url_screenshot(self, url, device_config, *, deadline=None):
        dimensions = self.get_dimensions(device_config)
        deadline = deadline or self._monotonic() + MAX_BROWSER_SECONDS
        allowed_hosts = self._allowed_hosts_for_url(url)
        payload, final_url, headers = self._download_provider_bytes(
            url,
            allowed_hosts=allowed_hosts,
            max_bytes=MAX_HTML_BYTES,
            timeout=MAX_BROWSER_SECONDS,
            deadline=deadline,
        )
        _remaining_timeout(deadline, self._monotonic, MAX_BROWSER_SECONDS)
        html_text = self._decode_response_text(payload, headers)
        html_text = self._sanitize_browser_html(html_text, final_url)
        self._assert_browser_html_network_closed(html_text)
        context = TaskContext.never_cancelled(
            deadline_monotonic=deadline,
            clock=self._monotonic,
        )
        logger.info("Rendering bounded news front page snapshot: %s", final_url)
        image = get_browser_renderer().render_html(
            html_text,
            viewport=dimensions,
            context=context,
            timeout_seconds=_remaining_timeout(
                deadline,
                self._monotonic,
                MAX_BROWSER_SECONDS,
            ),
        )
        _remaining_timeout(deadline, self._monotonic, MAX_BROWSER_SECONDS)
        if image is not None:
            self._validate_image_dimensions(image.size)
        return image

    def _fetch_web_headlines(self, url, *, deadline=None):
        try:
            payload, _final_url, headers = self._download_provider_bytes(
                url,
                allowed_hosts=self._allowed_hosts_for_url(url),
                timeout=MAX_HTTP_SECONDS,
                deadline=deadline,
                max_bytes=MAX_HTML_BYTES,
            )
        except Exception as exc:
            logger.warning("Could not fetch news front page HTML %s: %s", url, exc)
            return []

        return self._extract_headlines(self._decode_response_text(payload, headers))

    def _sanitize_browser_html(self, html_text, final_url):
        del final_url
        sanitizer = _NetworkClosedHTMLSanitizer()
        sanitizer.feed(html_text or "")
        sanitizer.close()
        safe = sanitizer.result()
        policy = (
            '<meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; navigate-to 'none'; form-action 'none'; "
            "base-uri 'none'; object-src 'none'\">"
        )
        return f"{policy}{safe}"

    def _assert_browser_html_network_closed(self, html_text):
        audit = _NetworkClosedHTMLAudit()
        try:
            audit.feed(html_text or "")
            audit.close()
        except Exception as exc:
            raise RuntimeError("Newspaper unsafe browser HTML was rejected") from exc
        if audit.unsafe:
            raise RuntimeError("Newspaper unsafe browser HTML was rejected")

    def _decode_response_text(self, content, headers=None):
        encodings = []

        content_type = (headers or {}).get("content-type", "")
        for match in re.finditer(r"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)", content_type, re.I):
            encodings.append(match.group(1))

        head = content[:4096].decode("ascii", errors="ignore")
        for match in re.finditer(r"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)", head, re.I):
            encodings.append(match.group(1))

        encodings.extend(
            [
                "utf-8",
                "gb18030",
                "gbk",
                "big5",
            ]
        )

        best_text = None
        best_score = None
        seen = set()
        for encoding in encodings:
            if not encoding:
                continue
            encoding_key = encoding.lower()
            if encoding_key in seen:
                continue
            seen.add(encoding_key)

            try:
                text = content.decode(encoding, errors="replace")
            except LookupError:
                continue

            score = (text.count("\ufffd") * 10) + self._mojibake_score(text)
            if best_score is None or score < best_score:
                best_text = text
                best_score = score

        if best_text is None:
            best_text = content.decode("utf-8", errors="replace")

        return self._repair_chinese_mojibake(best_text)

    def _extract_headlines(self, html_text):
        html_text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html_text or "")
        candidates = []
        preferred = []

        for match in re.finditer(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", html_text):
            text = self._clean_html_text(match.group(1))
            if self._looks_like_headline(text):
                candidates.append(text)

        for match in re.finditer(r"(?is)<a\b([^>]*)>(.*?)</a>", html_text):
            attrs = match.group(1) or ""
            text = self._clean_html_text(match.group(2))
            if not self._looks_like_headline(text):
                continue
            if "ckxxapp.ckxx.net" in attrs:
                preferred.append(text)
            else:
                candidates.append(text)

        unique = []
        seen = set()
        for text in preferred + candidates:
            key = re.sub(r"\W+", "", text.lower())
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(text)
            if len(unique) >= 9:
                break
        return unique

    def _clean_html_text(self, value):
        value = re.sub(r"<[^>]+>", " ", value or "")
        value = html.unescape(value)
        value = re.sub(r"\s+", " ", value).strip()
        value = self._repair_chinese_mojibake(value)
        return self._to_simplified_chinese(value)

    def _looks_like_headline(self, text):
        if not text or len(text) > 140:
            return False
        lower = text.lower()
        reject = {
            "sign in",
            "subscribe",
            "privacy policy",
            "terms of use",
            "cookie",
            "advertisement",
            "direct sponsorship",
            "edition",
            "weather",
            "video",
            "live tv",
            "首页",
            "平台热榜",
            "主题聚合",
            "历史归档",
            "广告投放",
            "联系投放",
            "其他平台",
            "参考消息实时热搜榜",
            "在 hotflashnews 投放广告",
        }
        reject_contains = [
            "投放广告",
            "aads",
            "direct sponsorship",
        ]
        if (
            lower in reject
            or any(lower.startswith(prefix) for prefix in ["skip to", "follow ", "share "])
            or any(marker in lower for marker in reject_contains)
        ):
            return False
        cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        if cjk_count >= 4:
            return True
        if len(text) < 12:
            return False
        return len(re.findall(r"[A-Za-z]{3,}", text)) >= 4

    def _repair_chinese_mojibake(self, text):
        if not text or self._mojibake_score(text) < 2:
            return text

        candidates = [text]
        for encoding in ["gb18030", "gbk", "latin1", "cp1252"]:
            try:
                candidates.append(text.encode(encoding, errors="strict").decode("utf-8"))
            except Exception:
                try:
                    candidates.append(text.encode(encoding, errors="ignore").decode("utf-8", errors="ignore"))
                except Exception:
                    continue

        return min(candidates, key=self._text_quality_score)

    def _mojibake_score(self, text):
        return sum(text.count(marker) for marker in MOJIBAKE_MARKERS)

    def _text_quality_score(self, text):
        cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        return (self._mojibake_score(text) * 20) + (text.count("?") * 2) - cjk_count

    def _to_simplified_chinese(self, text):
        if not self._has_cjk(text):
            return text

        converter = self._get_simplified_converter()
        if converter:
            try:
                return converter(text)
            except Exception as exc:
                logger.warning("Could not convert headline text to simplified Chinese: %s", exc)

        return text.translate(TRADITIONAL_TO_SIMPLIFIED)

    def _get_simplified_converter(self):
        if hasattr(self, "_simplified_converter"):
            return self._simplified_converter

        converter = None
        try:
            from opencc import OpenCC

            opencc_converter = OpenCC("t2s")
            converter = opencc_converter.convert
        except Exception:
            try:
                from zhconv import convert

                converter = lambda value: convert(value, "zh-cn")
            except Exception:
                converter = None

        self._simplified_converter = converter
        return converter

    def _render_headlines_page(self, source, headlines, device_config):
        dimensions = self.get_dimensions(device_config)
        width, height = dimensions
        headlines = [self._to_simplified_chinese(self._repair_chinese_mojibake(headline)) for headline in headlines]
        has_chinese = self._has_cjk(source["name"] + " " + " ".join(headlines))

        image = Image.new("RGB", dimensions, (255, 255, 255))
        draw = ImageDraw.Draw(image)
        black = (0, 0, 0)
        white = (255, 255, 255)

        title_font = self._font(32, bold=True)
        meta_font = self._font(14)
        item_font = self._font(20)
        source_font = self._font(13)

        draw.rectangle((0, 0, width, 64), fill=black)
        draw.text((18, 12), source["name"][:34], fill=white, font=title_font)
        subtitle = "\u6587\u5b57\u5934\u7248" if has_chinese else "front page headlines fallback"
        draw.text((18, 46), subtitle, fill=white, font=meta_font)

        host = urlparse(source["value"]).netloc or source["value"]
        draw.text((width - 18, 46), host[:34], fill=white, font=source_font, anchor="ra")

        y = 86
        line_gap = 6
        for index, headline in enumerate(headlines[:8], start=1):
            prefix = f"{index}."
            draw.text((20, y), prefix, fill=black, font=item_font)
            lines = self._wrap_text(draw, headline, item_font, width - 78)
            x = 58
            for line in lines[:2]:
                draw.text((x, y), line, fill=black, font=item_font)
                y += self._text_height(draw, line, item_font) + 2
            y += line_gap
            if y > height - 36:
                break

        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        generated_label = "\u751f\u6210" if has_chinese else "Generated"
        draw.line((18, height - 28, width - 18, height - 28), fill=black, width=1)
        draw.text((18, height - 22), f"{generated_label} {generated}", fill=black, font=source_font)
        return image

    def _font(self, size, bold=False):
        for family in ["LXGW WenKai", "FandolKai", "I.Ming", "Jost"]:
            try:
                font = get_font(family, size, "bold" if bold else "normal")
            except Exception as exc:
                logger.warning("Could not load font %s: %s", family, exc)
                font = None
            if font:
                return font

        src_dir = Path(__file__).resolve().parents[2]
        for relative_path in [
            Path("static") / "fonts" / "LXGWWenKai-Regular.ttf",
            Path("plugins") / "chinese_literature_clock" / "fonts" / "FandolKai-Regular.otf",
            Path("plugins") / "chinese_literature_clock" / "fonts" / "I.Ming-8.10.ttf",
        ]:
            try:
                font_path = src_dir / relative_path
                if font_path.is_file():
                    return ImageFont.truetype(str(font_path), size)
            except Exception as exc:
                logger.warning("Could not load font file %s: %s", relative_path, exc)

        return ImageFont.load_default()

    def _wrap_text(self, draw, text, font, max_width):
        tokens = list(text) if self._has_cjk(text) else text.split()
        separator = "" if self._has_cjk(text) else " "
        lines = []
        current = ""
        for token in tokens:
            candidate = token if not current else f"{current}{separator}{token}"
            if self._text_width(draw, candidate, font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = token
        if current:
            lines.append(current)
        return lines or [text]

    def _has_cjk(self, text):
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    def _text_width(self, draw, text, font):
        return text_width(draw, text, font)

    def _text_height(self, draw, text, font):
        box = draw.textbbox((0, 0), text or "A", font=font)
        return box[3] - box[1]

    def _fetch_newspaper_cover(self, newspaper_slug, device_config, *, deadline=None):
        newspaper_slug = newspaper_slug.upper()
        deadline = deadline or self._monotonic() + MAX_HTTP_SECONDS

        # Get today's date
        today = datetime.today()

        # check the next day, then today, then prior day
        days = [today + timedelta(days=diff) for diff in [1, 0, -1, -2]]

        image = None
        for date in days:
            _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
            image_url = FREEDOM_FORUM_URL.format(date.day, newspaper_slug)
            try:
                payload, _final_url, _headers = self._download_provider_bytes(
                    image_url,
                    allowed_hosts=("cdn.freedomforum.org",),
                    max_bytes=MAX_PNG_BYTES,
                    timeout=MAX_HTTP_SECONDS,
                    deadline=deadline,
                )
                image = self._decode_remote_image(payload, deadline=deadline)
            except Exception as exc:
                logger.warning("Could not fetch newspaper cover %s: %s", image_url, exc)
                image = None
            if image:
                logger.info(f"Found {newspaper_slug} front cover for {date.strftime('%Y-%m-%d')}")
                break

        if image:
            # expand height if newspaper is wider than resolution
            img_width, img_height = image.size

            dimensions = device_config.get_resolution()
            if device_config.get_config("orientation") == "horizontal":
                dimensions = dimensions[::-1]

            desired_width, desired_height = dimensions

            img_ratio = img_width / img_height
            desired_ratio = desired_width / desired_height

            if img_ratio < desired_ratio:
                new_height = int((img_width * desired_width) / desired_height)
                new_image = Image.new("RGB", (img_width, new_height), (255, 255, 255))
                new_image.paste(image, (0, 0))
                image = new_image
        else:
            return None

        return image

    def _fetch_luoyang_evening_news_cover(self, device_config, *, deadline=None):
        deadline = deadline or self._monotonic() + MAX_HTTP_SECONDS
        for date in self._lywb_candidate_dates():
            _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
            url = self._build_lywb_pdf_url(date)
            pdf_bytes = self._download_pdf(url, deadline=deadline)
            if not pdf_bytes:
                continue

            image = self._render_pdf_first_page(pdf_bytes, deadline=deadline)
            if not image:
                continue

            logger.info("Found Luoyang Evening News front page for %s", date.strftime("%Y-%m-%d"))
            return self._prepare_frontpage_image(image, device_config)

        return None

    def _lywb_candidate_dates(self):
        # Luoyang is UTC+8; use source-local date instead of the device timezone.
        today = datetime.utcnow() + timedelta(hours=8)
        return [today - timedelta(days=diff) for diff in range(LYWB_LOOKBACK_DAYS + 1)]

    def _build_lywb_pdf_url(self, date):
        return LYWB_A01_PDF_URL.format(
            year_month=date.strftime("%Y-%m"),
            day=date.strftime("%d"),
            stamp=date.strftime("%Y%m%d"),
        )

    def _download_pdf(self, url, *, deadline=None):
        try:
            payload, _final_url, _headers = self._download_provider_bytes(
                url,
                allowed_hosts=("lywb.lyd.com.cn",),
                timeout=MAX_HTTP_SECONDS,
                deadline=deadline,
                max_bytes=MAX_PDF_BYTES,
                headers={
                    "User-Agent": "Mozilla/5.0 InkyPi News Front Pages/1.0",
                    "Referer": "https://lywb.lyd.com.cn/",
                },
            )
        except HttpStatusError as exc:
            if exc.status == 404:
                return None
            logger.warning("Could not fetch PDF front page %s: %s", url, exc)
            return None
        except Exception as exc:
            logger.warning("Could not fetch PDF front page %s: %s", url, exc)
            return None

        if not payload.startswith(b"%PDF"):
            logger.warning("PDF front page response was not a PDF: %s", url)
            return None

        return payload

    def _render_pdf_first_page(self, pdf_bytes, *, deadline=None):
        if not isinstance(pdf_bytes, bytes) or len(pdf_bytes) > MAX_PDF_BYTES:
            raise RuntimeError("Newspaper PDF exceeds the size limit")
        _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
        try:
            fitz = self._import_pymupdf()
        except Exception as exc:
            raise RuntimeError("PyMuPDF is required to render PDF front pages") from exc

        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
            if len(document) < 1:
                return None
            if len(document) > MAX_PDF_PAGES:
                raise RuntimeError("Newspaper PDF exceeds the page limit")

            page = document.load_page(0)
            _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
            matrix = fitz.Matrix(2, 2)
            expected_width = int(page.rect.width * 2)
            expected_height = int(page.rect.height * 2)
            self._validate_image_dimensions((expected_width, expected_height))
            _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            self._validate_image_dimensions((pixmap.width, pixmap.height))
            image = Image.frombytes(
                "RGB",
                (pixmap.width, pixmap.height),
                pixmap.samples,
            )
            _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
            return image
        finally:
            document.close()

    def _import_pymupdf(self):
        try:
            import fitz

            return fitz
        except Exception:
            vendor_path = Path(__file__).resolve().parent / "_vendor"
            if vendor_path.is_dir() and str(vendor_path) not in sys.path:
                sys.path.insert(0, str(vendor_path))

            import fitz

            return fitz

    def _prepare_frontpage_image(self, image, device_config):
        self._validate_image_dimensions(image.size)
        img_width, img_height = image.size

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "horizontal":
            dimensions = dimensions[::-1]

        desired_width, desired_height = dimensions

        img_ratio = img_width / img_height
        desired_ratio = desired_width / desired_height

        if img_ratio < desired_ratio:
            new_height = int((img_width * desired_width) / desired_height)
            new_image = Image.new("RGB", (img_width, new_height), (255, 255, 255))
            new_image.paste(image, (0, 0))
            image = new_image

        return image

    def _decode_remote_image(self, payload, *, deadline=None):
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_PNG_BYTES:
            raise RuntimeError("Newspaper image exceeds the size limit")
        _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
        try:
            with Image.open(BytesIO(payload)) as source:
                self._validate_image_dimensions(source.size)
                _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
                source.load()
                _remaining_timeout(deadline, self._monotonic, MAX_HTTP_SECONDS)
                image = source.convert("RGB")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Newspaper image could not be decoded") from exc
        self._validate_image_dimensions(image.size)
        return image

    def _validate_image_dimensions(self, size):
        width, height = int(size[0]), int(size[1])
        if (
            width <= 0
            or height <= 0
            or width > MAX_IMAGE_DIMENSION
            or height > MAX_IMAGE_DIMENSION
            or width * height > MAX_IMAGE_PIXELS
        ):
            raise RuntimeError("Newspaper image dimensions exceed the safety limit")

    def _allowed_hosts_for_url(self, url):
        try:
            parsed = urlsplit(str(url or ""))
            host = (parsed.hostname or "").rstrip(".").lower()
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError("Newspaper source URL is invalid") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
        ):
            raise RuntimeError("Newspaper source URL is outside its allowlist")
        return (host,)

    def _validate_approved_target(self, approved, allowed_hosts):
        host = str(approved.hostname or "").rstrip(".").lower()
        normalized = tuple(str(value).rstrip(".").lower() for value in allowed_hosts)
        if host not in normalized:
            raise RuntimeError("Newspaper provider authority is outside its allowlist")
        if approved.scheme not in {"http", "https"}:
            raise RuntimeError("Newspaper provider scheme is not allowed")
        expected_port = 443 if approved.scheme == "https" else 80
        if int(approved.port) != expected_port:
            raise RuntimeError("Newspaper provider port is outside its allowlist")
        if not approved.addresses:
            raise RuntimeError("Newspaper provider has no approved address")
        for value in approved.addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise RuntimeError("Newspaper provider address is invalid") from exc
            if (
                isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None
            ) or not address.is_global:
                raise RuntimeError("Newspaper provider address is not public")
        return approved

    def _download_provider_bytes(
        self,
        url,
        *,
        allowed_hosts,
        max_bytes,
        timeout,
        deadline=None,
        headers=None,
    ):
        deadline = deadline or self._monotonic() + timeout
        current_url = str(url or "").strip()
        policy = get_ssrf_policy()
        headers = dict(headers or REQUEST_HEADERS)
        for redirect_count in range(MAX_REDIRECTS + 1):
            _remaining_timeout(deadline, self._monotonic, timeout)
            approved = self._validate_approved_target(
                policy.resolve_and_validate(current_url),
                allowed_hosts,
            )
            _remaining_timeout(deadline, self._monotonic, timeout)
            response = _PinnedResponse.open(
                approved,
                headers=headers,
                deadline=deadline,
                clock=self._monotonic,
                timeout=_remaining_timeout(deadline, self._monotonic, timeout),
            )
            try:
                status = int(response.status_code)
                if 300 <= status < 400:
                    if redirect_count >= MAX_REDIRECTS:
                        raise RuntimeError("Newspaper redirect limit was exceeded")
                    location = str(response.headers.get("Location") or "").strip()
                    if not location:
                        raise RuntimeError("Newspaper redirect has no Location")
                    next_url = urljoin(approved.normalized_url, location)
                    next_target = self._validate_approved_target(
                        policy.resolve_and_validate(next_url),
                        allowed_hosts,
                    )
                    current_url = next_target.normalized_url
                    continue
                if not 200 <= status < 300:
                    raise HttpStatusError("GET", approved.normalized_url, status)
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        if int(content_length) > max_bytes:
                            raise RuntimeError("Newspaper response exceeds its size limit")
                    except ValueError:
                        pass
                payload = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    _remaining_timeout(deadline, self._monotonic, timeout)
                    if len(payload) + len(chunk) > max_bytes:
                        raise RuntimeError("Newspaper response exceeds its size limit")
                    payload.extend(chunk)
                if not payload:
                    raise RuntimeError("Newspaper provider response is empty")
                _remaining_timeout(deadline, self._monotonic, timeout)
                return bytes(payload), approved.normalized_url, dict(response.headers)
            finally:
                response.close()
        raise RuntimeError("Newspaper redirect limit was exceeded")

    def _select_next_source(self, sources):
        pool_key = self._source_pool_key(sources)
        state = self._read_rotation_state()
        pool_state = state.get(pool_key, {})
        next_index = int(pool_state.get("next_index") or 0) % len(sources)
        selected = sources[next_index]

        state[pool_key] = {
            "next_index": (next_index + 1) % len(sources),
            "last_selected": selected["id"],
            "pool_size": len(sources),
            "source_ids": [source["id"] for source in sources],
        }
        self._write_rotation_state(state)
        return selected

    def _source_pool_key(self, sources):
        raw = "|".join([NEWS_FRONTPAGE_ROTATION_VERSION] + [source["id"] for source in sources])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _rotation_state_path(self):
        return self.cache_dir() / ".newspaper_rotation_state.json"

    def _read_rotation_state(self):
        path = self._rotation_state_path()
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read newspaper rotation state %s: %s", path, exc)
        return {}

    def _write_rotation_state(self, state):
        path = self._rotation_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(state, indent=2)
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

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["newspapers"] = sorted(NEWSPAPERS, key=lambda n: n["name"])
        template_params["default_media_sources"] = DEFAULT_MEDIA_SOURCES
        return template_params
