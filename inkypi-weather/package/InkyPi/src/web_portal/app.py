"""Flask application factory for the private Model Y publication portal.

The web process deliberately depends on a tiny read-only seam.  A publication
source supplies ``list_publications()``, ``get_publication(slug)`` and
``get_asset(asset_id)``.  Opening a route therefore cannot collect provider
data, run Chromium, enqueue refreshes, or touch display hardware.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .health import PortalHealthProbe


logger = logging.getLogger(__name__)

ADMIN_SESSION_KEY = "admin_identity"
ADMIN_SESSION_REVISION_KEY = "admin_revision"
CSRF_SESSION_KEY = "csrf_token"
DEFAULT_TIMEZONE = "America/Los_Angeles"
OPAQUE_ASSET_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
SAFE_FRESHNESS = {"fresh", "stale", "unavailable"}
SAFE_KINDS = {"native", "legacy_png"}
SAFE_DATA_MODES = {"provider", "cache", "sample", "unknown"}


class _LoginRateLimiter:
    """Small process-local fixed-window limiter for the single login form."""

    def __init__(
        self,
        *,
        maximum,
        window_seconds,
        global_maximum=100,
        maximum_keys=4096,
        clock=time.monotonic,
    ):
        self.maximum = max(1, int(maximum))
        self.global_maximum = max(1, int(global_maximum))
        self.maximum_keys = max(1, int(maximum_keys))
        self.window_seconds = max(1.0, float(window_seconds))
        self.clock = clock
        self._attempts: dict[str, list[float]] = {}
        self._global_attempts: list[float] = []
        self._lock = threading.Lock()

    @property
    def tracked_client_count(self) -> int:
        now = float(self.clock())
        with self._lock:
            self._prune_locked(now)
            return len(self._attempts)

    def retry_after(self, key: str) -> int:
        now = float(self.clock())
        with self._lock:
            self._prune_locked(now)
            waits = [
                self._bucket_retry_after(
                    self._global_attempts,
                    self.global_maximum,
                    now,
                ),
                self._bucket_retry_after(
                    self._attempts.get(key, ()),
                    self.maximum,
                    now,
                ),
            ]
            return max(waits)

    def record_failure(self, key: str) -> None:
        now = float(self.clock())
        with self._lock:
            self._prune_locked(now)
            self._global_attempts.append(now)
            if key not in self._attempts and len(self._attempts) >= self.maximum_keys:
                oldest_key = min(
                    self._attempts,
                    key=lambda candidate: self._attempts[candidate][-1],
                )
                self._attempts.pop(oldest_key, None)
            attempts = self._attempts.get(key, [])
            attempts.append(now)
            self._attempts[key] = attempts

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window_seconds
        self._global_attempts = [value for value in self._global_attempts if value > cutoff]
        for key, values in tuple(self._attempts.items()):
            current = [value for value in values if value > cutoff]
            if current:
                self._attempts[key] = current
            else:
                self._attempts.pop(key, None)

    def _bucket_retry_after(self, attempts, maximum, now):
        if len(attempts) < maximum:
            return 0
        return max(1, int(self.window_seconds - (now - attempts[0]) + 0.999))


def _field(value, name, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _plain_text(value, *, default="", maximum=500):
    if value is None:
        return default
    text = str(value).replace("\x00", "").strip()
    return text[:maximum] if text else default


def _normalize_timestamp(value):
    if isinstance(value, datetime):
        instant = value
        original = value.isoformat()
    elif isinstance(value, str):
        original = value
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            instant = datetime.fromisoformat(candidate)
        except ValueError:
            return None, "更新时间未知"
    else:
        return None, "更新时间未知"
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return original, instant


def _display_time(value, timezone_name):
    original, instant = _normalize_timestamp(value)
    if instant == "更新时间未知":
        return original, instant
    if instant is None:
        return None, "更新时间未知"
    try:
        local = instant.astimezone(ZoneInfo(timezone_name))
    except (KeyError, ValueError):
        local = instant.astimezone(ZoneInfo(DEFAULT_TIMEZONE))
    return original, f"{local.year}年{local.month}月{local.day}日 {local:%H:%M}"


def _normalize_fact(value):
    return {
        "label": _plain_text(_field(value, "label"), maximum=80),
        "value": _plain_text(_field(value, "value"), maximum=240),
    }


def _number_text(value, *, decimals=None):
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if decimals is not None:
            return f"{value:,.{decimals}f}"
        return f"{value:,}".rstrip("0").rstrip(".")
    return _plain_text(value, maximum=80)


def _joined(*parts, separator=" · "):
    return separator.join(text for text in (_plain_text(part, maximum=160) for part in parts) if text)


def _native_row(*, leading="", title="", meta="", value="", state=""):
    return {
        "leading": _plain_text(leading, maximum=80),
        "title": _plain_text(title, maximum=180),
        "meta": _plain_text(meta, maximum=240),
        "value": _plain_text(value, maximum=120),
        "state": _plain_text(state, maximum=32),
    }


def _stock_rows(payload):
    rows = []
    items = _field(payload, "items", ())
    if not isinstance(items, (list, tuple)):
        return rows
    portfolio = _field(payload, "portfolio", {})
    currency = _plain_text(_field(portfolio, "currency"), maximum=12)
    for item in items[:6]:
        symbol = _plain_text(_field(item, "symbol"), maximum=24)
        name = _plain_text(_field(item, "name"), maximum=100)
        shares = _number_text(_field(item, "shares"))
        price = _number_text(_field(item, "price"))
        change = _number_text(_field(item, "change_percent"))
        value = _joined(
            _joined(currency, price, separator=" ") if price else "",
            f"{change}%" if change else "",
        )
        rows.append(
            _native_row(
                leading=symbol,
                title=name or symbol,
                meta=f"{shares} 股" if shares else "",
                value=value,
            )
        )
    return rows


def _sports_rows(payload):
    events = _field(payload, "events", ())
    if not isinstance(events, (list, tuple)):
        return []
    rows = []
    for event in events[:6]:
        team_a = _field(event, "team_a") or _field(event, "home_name")
        team_b = _field(event, "team_b") or _field(event, "away_name")
        title = _joined(team_a, team_b, separator=" 对 ")
        if not title:
            title = _field(event, "game_name") or _field(event, "tournament")
        score = _field(event, "score")
        if score in (None, ""):
            score_a = _field(event, "score_a", _field(event, "home_score"))
            score_b = _field(event, "score_b", _field(event, "away_score"))
            if score_a not in (None, "") or score_b not in (None, ""):
                score = f"{_number_text(score_a)}–{_number_text(score_b)}"
        status = _field(event, "status") or _field(event, "status_text") or _field(event, "state")
        rows.append(
            _native_row(
                leading=_field(event, "section"),
                title=title or "赛事",
                meta=_joined(status, _field(event, "provider")),
                value=score,
                state="live" if _field(event, "has_live") is True else "",
            )
        )
    return rows


def _stream_rows(payload):
    rows = []
    for key, label in (("live", "直播"), ("replay", "回放")):
        items = _field(payload, key, ())
        if not isinstance(items, (list, tuple)):
            continue
        for item in items:
            if len(rows) >= 6:
                return rows
            heat = _number_text(_field(item, "heat"))
            rows.append(
                _native_row(
                    leading=_joined(label, _field(item, "platform")),
                    title=_field(item, "owner") or "未命名频道",
                    meta=_field(item, "title"),
                    value=f"热度 {heat}" if heat else "",
                    state=key,
                )
            )
    return rows


def _game_item_row(item, group_title=""):
    rank = _number_text(_field(item, "rank"))
    current = _plain_text(
        _field(item, "current_players") or _field(item, "primary_metric"),
        maximum=80,
    )
    change = _plain_text(_field(item, "change_24h"), maximum=40)
    return _native_row(
        leading=_joined(group_title, f"#{rank}" if rank else ""),
        title=_field(item, "name") or "未命名游戏",
        meta=_field(item, "secondary_name"),
        value=_joined(f"在线 {current}" if current else "", change),
    )


def _game_rows(payload):
    rows = []
    groups = _field(payload, "groups", ())
    if isinstance(groups, (list, tuple)):
        for group in groups:
            items = _field(group, "items", ())
            if not isinstance(items, (list, tuple)):
                continue
            for item in items:
                rows.append(_game_item_row(item, _field(group, "title")))
                if len(rows) >= 6:
                    return rows
    items = _field(payload, "items", ())
    if isinstance(items, (list, tuple)):
        for item in items:
            rows.append(_game_item_row(item))
            if len(rows) >= 6:
                break
    return rows


def _ticket_rows(payload):
    items = _field(payload, "items", ())
    if not isinstance(items, (list, tuple)):
        return []
    rows = []
    for item in items[:6]:
        rank = _number_text(_field(item, "rank"))
        when = _joined(_field(item, "local_date"), _field(item, "local_time"))
        place = _joined(
            _field(item, "venue_name"),
            _field(item, "city"),
            _field(item, "state_code"),
        )
        rows.append(
            _native_row(
                leading=f"#{rank}" if rank else "活动",
                title=_field(item, "title") or "未命名活动",
                meta=_joined(when, place),
                value=_joined(
                    _field(item, "price"),
                    _field(item, "distance"),
                    _field(item, "status"),
                ),
            )
        )
    return rows


def _weather_rows(payload):
    forecast = _field(payload, "forecast", ())
    if not isinstance(forecast, (list, tuple)):
        return []
    rows = []
    for item in forecast[:6]:
        high = _number_text(_field(item, "high"))
        low = _number_text(_field(item, "low"))
        temperature = _joined(
            f"最高 {high}" if high else "",
            f"最低 {low}" if low else "",
            separator=" / ",
        )
        rows.append(
            _native_row(
                leading=_field(item, "day"),
                title=temperature or "天气预报",
            )
        )
    return rows


def _normalize_native_payload(payload):
    kind = _plain_text(_field(payload, "kind"), maximum=64)
    headline = _plain_text(_field(payload, "headline"), maximum=240)
    rows = []
    if kind == "stock_portfolio":
        portfolio = _field(payload, "portfolio", {})
        currency = _plain_text(_field(portfolio, "currency"), maximum=12)
        value = _number_text(_field(portfolio, "value"), decimals=2)
        if not headline and value:
            headline = _joined(currency, value, separator=" ")
        rows = _stock_rows(payload)
    elif kind == "sports_dashboard":
        live_count = _number_text(_field(payload, "live_count"))
        if not headline and live_count:
            headline = f"{live_count} 场直播"
        rows = _sports_rows(payload)
    elif kind == "stream_status":
        live = _field(payload, "live", ())
        if not headline and isinstance(live, (list, tuple)):
            headline = f"{len(live)} 个直播"
        rows = _stream_rows(payload)
    elif kind in {"game_chart", "game_chart_overview"}:
        rows = _game_rows(payload)
    elif kind == "ticketmaster_events":
        rows = _ticket_rows(payload)
    elif kind == "weather":
        rows = _weather_rows(payload)

    facts = _field(payload, "facts", ())
    if not isinstance(facts, (list, tuple)):
        facts = ()
    return {
        "kind": kind,
        "headline": headline,
        "summary": _plain_text(_field(payload, "summary"), maximum=800),
        "facts": [_normalize_fact(fact) for fact in facts[:12]],
        "rows": rows,
    }


def _normalize_raster_payload(payload):
    width = _field(payload, "width")
    height = _field(payload, "height")
    return {
        "alt": _plain_text(_field(payload, "alt"), maximum=240),
        "width": width if isinstance(width, int) and not isinstance(width, bool) and width > 0 else None,
        "height": height if isinstance(height, int) and not isinstance(height, bool) and height > 0 else None,
    }


def _normalize_publication(value, timezone_name):
    slug = _plain_text(_field(value, "slug"), maximum=160)
    title = _plain_text(_field(value, "title"), default="未命名内容", maximum=160)
    plugin = _plain_text(_field(value, "plugin"), maximum=120)
    kind = _plain_text(_field(value, "kind"), default="native", maximum=32)
    if kind not in SAFE_KINDS:
        kind = "native"
    original_plugin_frame = kind == "legacy_png"
    if original_plugin_frame and plugin.casefold() == "weather":
        title = "当地天气"
    freshness = _plain_text(_field(value, "freshness"), default="unavailable", maximum=32)
    if freshness not in SAFE_FRESHNESS:
        freshness = "unavailable"
    updated_at, display_updated_at = _display_time(_field(value, "updated_at"), timezone_name)
    payload = _field(value, "payload", {})
    if not isinstance(payload, Mapping):
        payload = {}
    data_mode = _plain_text(_field(payload, "data_mode"), default="unknown", maximum=32)
    if data_mode not in SAFE_DATA_MODES:
        data_mode = "unknown"
    source_label = _plain_text(_field(payload, "source_label"), maximum=120)
    source_updated_at, display_source_updated_at = _display_time(
        _field(payload, "source_updated_at"),
        timezone_name,
    )
    if source_updated_at:
        display_time = display_source_updated_at
        display_time_value = source_updated_at
        time_label = "样例生成于" if data_mode == "sample" else "数据截至"
    else:
        display_time = display_updated_at
        display_time_value = updated_at
        time_label = "发布于"
    labels = {
        "fresh": "最新",
        "stale": "已过时",
        "unavailable": "暂不可用",
    }
    if data_mode == "sample":
        freshness_label = ""
    elif data_mode in {"provider", "cache"} and freshness == "stale":
        freshness_label = "更新延迟"
    else:
        freshness_label = labels[freshness]
    data_mode_labels = {
        "provider": "实际数据",
        "cache": "缓存数据",
        "sample": "离线样例",
        "unknown": "",
    }
    message = _plain_text(_field(value, "message"), maximum=240)
    if not message and freshness == "unavailable":
        message = "当前没有可显示的已发布内容"
    normalized = {
        "slug": slug,
        "title": title,
        "plugin": plugin,
        "kind": kind,
        "original_plugin_frame": original_plugin_frame,
        "freshness": freshness,
        "freshness_label": freshness_label,
        "data_mode": data_mode,
        "data_mode_label": data_mode_labels[data_mode],
        "source_label": source_label,
        "source_updated_at": source_updated_at,
        "display_source_updated_at": display_source_updated_at,
        "time_label": time_label,
        "display_time": display_time,
        "display_time_value": display_time_value,
        "updated_at": updated_at,
        "display_updated_at": display_updated_at,
        "asset_id": _plain_text(_field(value, "asset_id"), maximum=128),
        "message": message,
        "payload": _normalize_raster_payload(payload) if kind == "legacy_png" else _normalize_native_payload(payload),
    }
    return normalized


def _publication_slug(value):
    if isinstance(value, str):
        return _plain_text(value, maximum=160)
    return _plain_text(_field(value, "slug"), maximum=160)


def _normalize_catalog(source, timezone_name):
    publications_source = _field(source, "publications", ())
    if not isinstance(publications_source, (list, tuple)):
        publications_source = ()
    publications = [_normalize_publication(publication, timezone_name) for publication in publications_source]
    publications = [publication for publication in publications if publication["slug"]]
    known_slugs = {publication["slug"] for publication in publications}

    playlists_source = _field(source, "playlists", ())
    if not isinstance(playlists_source, (list, tuple)):
        playlists_source = ()
    playlists = []
    for position, playlist in enumerate(playlists_source):
        slug = _plain_text(_field(playlist, "slug"), default=f"playlist-{position + 1}", maximum=160)
        members = _field(playlist, "publication_slugs", None)
        if members is None:
            members = _field(playlist, "publications", ())
        if not isinstance(members, (list, tuple)):
            members = ()
        member_slugs = [
            slug_value for slug_value in (_publication_slug(member) for member in members) if slug_value in known_slugs
        ]
        playlists.append(
            {
                "slug": slug,
                "name": _plain_text(
                    _field(playlist, "name"),
                    default=f"播放列表 {position + 1}",
                    maximum=120,
                ),
                "active": bool(_field(playlist, "active", False)),
                "window": _plain_text(_field(playlist, "window"), maximum=100),
                "publication_slugs": member_slugs,
            }
        )

    active_playlist = _plain_text(_field(source, "active_playlist"), maximum=160)
    playlist_slugs = {playlist["slug"] for playlist in playlists}
    if active_playlist not in playlist_slugs:
        active = next((playlist for playlist in playlists if playlist["active"]), None)
        active_playlist = active["slug"] if active else (playlists[0]["slug"] if playlists else "")
    return {
        "active_playlist": active_playlist,
        "playlists": playlists,
        "publications": publications,
    }


def _source_catalog(publications):
    method = getattr(publications, "list_publications", None)
    if not callable(method):
        raise RuntimeError("publication source must provide list_publications()")
    return method()


def _source_publication(publications, slug):
    method = getattr(publications, "get_publication", None)
    if not callable(method):
        raise RuntimeError("publication source must provide get_publication(slug)")
    return method(slug)


def _source_asset(publications, asset_id):
    method = getattr(publications, "get_asset", None)
    if not callable(method):
        raise RuntimeError("publication source must provide get_asset(asset_id)")
    return method(asset_id)


def _catalog_etag(source, normalized):
    supplied = _plain_text(_field(source, "etag"), maximum=160)
    if supplied:
        return supplied.strip('"')
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _safe_next(value):
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        return "/"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    return value


def _csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _valid_csrf():
    expected = session.get(CSRF_SESSION_KEY)
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    return bool(isinstance(expected, str) and isinstance(supplied, str) and hmac.compare_digest(expected, supplied))


def _selected_playlist(catalog, requested_slug):
    playlists = catalog["playlists"]
    requested = next((playlist for playlist in playlists if playlist["slug"] == requested_slug), None)
    if requested is not None:
        return requested
    active = next(
        (playlist for playlist in playlists if playlist["slug"] == catalog["active_playlist"]),
        None,
    )
    return active or (playlists[0] if playlists else None)


def _playlist_publications(catalog, playlist):
    if playlist is None:
        return []
    by_slug = {publication["slug"]: publication for publication in catalog["publications"]}
    return [by_slug[slug] for slug in playlist["publication_slugs"] if slug in by_slug]


def create_web_app(publications, credential_store, secret_key, config=None):
    """Create the authenticated, read-only web portal.

    ``publications`` is intentionally injected so tests and the cloud runtime
    can provide a read-only implementation without importing the scheduler.
    """

    if not secret_key:
        raise ValueError("secret_key is required")
    package_root = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(package_root / "templates"),
        static_folder=str(package_root / "static"),
        static_url_path="/web-static",
    )
    app.config.from_mapping(
        SECRET_KEY=secret_key,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_NAME="__Host-epaper_portal",
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_PATH="/",
        SESSION_REFRESH_EACH_REQUEST=True,
        LOGIN_MAX_ATTEMPTS=5,
        LOGIN_GLOBAL_MAX_ATTEMPTS=100,
        LOGIN_MAX_TRACKED_CLIENTS=4096,
        LOGIN_WINDOW_SECONDS=15 * 60,
        MAX_CONTENT_LENGTH=16 * 1024,
        PORTAL_TIMEZONE=DEFAULT_TIMEZONE,
        PUBLICATION_HEALTH_CACHE_SECONDS=10,
        WORKER_STATE_MAX_AGE_SECONDS=300,
        WORKER_STATE_PATH=None,
    )
    if config:
        app.config.from_mapping(config)
    app.config["CREDENTIAL_STORE"] = credential_store
    app.extensions["web_portal_publications"] = publications
    app.extensions["web_portal_credentials"] = credential_store
    app.extensions["web_portal_health"] = PortalHealthProbe(
        credential_store,
        publications,
        worker_state_path=app.config.get("WORKER_STATE_PATH"),
        worker_state_max_age_seconds=app.config["WORKER_STATE_MAX_AGE_SECONDS"],
        publication_cache_seconds=app.config["PUBLICATION_HEALTH_CACHE_SECONDS"],
        clock=app.config.get("HEALTH_CLOCK", time.time),
        cache_clock=app.config.get("HEALTH_CACHE_CLOCK", time.monotonic),
    )
    rate_limiter = _LoginRateLimiter(
        maximum=app.config["LOGIN_MAX_ATTEMPTS"],
        global_maximum=app.config["LOGIN_GLOBAL_MAX_ATTEMPTS"],
        maximum_keys=app.config["LOGIN_MAX_TRACKED_CLIENTS"],
        window_seconds=app.config["LOGIN_WINDOW_SECONDS"],
        clock=app.config.get("LOGIN_CLOCK", time.monotonic),
    )
    app.extensions["web_portal_login_limiter"] = rate_limiter

    def credential_revision():
        if not getattr(credential_store, "available", True):
            return None
        method = getattr(credential_store, "admin_session_revision", None)
        if not callable(method):
            return None
        try:
            revision = method()
        except Exception:
            logger.warning("Web portal credential revision is unavailable")
            return None
        if not isinstance(revision, str) or not revision:
            return None
        return revision

    def authenticated():
        if session.get(ADMIN_SESSION_KEY) != "admin":
            return False
        expected = session.get(ADMIN_SESSION_REVISION_KEY)
        current = credential_revision()
        valid = bool(isinstance(expected, str) and isinstance(current, str) and hmac.compare_digest(expected, current))
        if not valid:
            session.clear()
        return valid

    def login_key():
        return request.remote_addr or "unknown"

    def credentials_ready():
        if not getattr(credential_store, "available", True):
            return False
        try:
            return bool(credential_store.has_admin() and credential_revision())
        except Exception:
            logger.warning("Web portal credential store is unavailable")
            return False

    def read_catalog():
        source = _source_catalog(publications)
        normalized = _normalize_catalog(source, app.config["PORTAL_TIMEZONE"])
        return source, normalized

    @app.before_request
    def require_authentication():
        if request.endpoint in {"login", "healthz", "livez", "readyz", "static"}:
            return None
        if authenticated():
            return None
        if request.path.startswith("/api/") or request.path.startswith("/assets/"):
            response = jsonify({"error": "authentication_required"})
            response.status_code = 401
            response.headers["WWW-Authenticate"] = 'Session realm="EpaperSystem"'
            response.headers["Cache-Control"] = "private, no-store"
            return response
        next_url = request.full_path.rstrip("?")
        return redirect(url_for("login", next=next_url))

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'",
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if response.status_code != 304 and response.mimetype == "text/html":
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.context_processor
    def portal_context():
        return {
            "csrf_token": _csrf_token,
            "authenticated": authenticated(),
            "portal_timezone": app.config["PORTAL_TIMEZONE"],
        }

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if authenticated():
            return redirect(url_for("catalog"))
        if not credentials_ready():
            return (
                render_template(
                    "login.html",
                    error="管理员凭据尚未配置或暂不可用。",
                    next_url="/",
                ),
                503,
            )
        next_url = _safe_next(request.values.get("next"))
        if request.method == "GET":
            _csrf_token()
            return render_template("login.html", error=None, next_url=next_url)
        if not _valid_csrf():
            return (
                render_template(
                    "login.html",
                    error="页面已过期，请重新载入后再登录。",
                    next_url=next_url,
                ),
                400,
            )
        retry_after = rate_limiter.retry_after(login_key())
        if retry_after:
            response = app.make_response(
                (
                    render_template(
                        "login.html",
                        error="登录尝试过多，请稍后再试。",
                        next_url=next_url,
                    ),
                    429,
                )
            )
            response.headers["Retry-After"] = str(retry_after)
            return response
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        revision_before = credential_revision()
        try:
            password_valid = bool(credential_store.verify_admin_password(password))
        except Exception:
            logger.warning("Web portal credential verification failed closed")
            password_valid = False
        revision_after = credential_revision()
        revision_stable = bool(
            isinstance(revision_before, str)
            and isinstance(revision_after, str)
            and hmac.compare_digest(revision_before, revision_after)
        )
        if username != "admin" or not password_valid or not revision_stable:
            rate_limiter.record_failure(login_key())
            return (
                render_template(
                    "login.html",
                    error="用户名或密码不正确。",
                    next_url=next_url,
                ),
                401,
            )
        rate_limiter.reset(login_key())
        session.clear()
        session.permanent = True
        session[ADMIN_SESSION_KEY] = "admin"
        session[ADMIN_SESSION_REVISION_KEY] = revision_after
        session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
        return redirect(next_url)

    @app.post("/logout")
    def logout():
        if not _valid_csrf():
            return (
                render_template(
                    "error.html",
                    title="请求已过期",
                    message="请返回页面后重新退出登录。",
                ),
                400,
            )
        session.clear()
        return redirect(url_for("login"))

    @app.get("/healthz")
    def healthz():
        response = jsonify({"status": "ok"})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/livez")
    def livez():
        return healthz()

    @app.get("/readyz")
    def readyz():
        snapshot = app.extensions["web_portal_health"].inspect()
        response = jsonify(snapshot.public_document())
        response.status_code = snapshot.http_status
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def catalog():
        _, normalized = read_catalog()
        selected = _selected_playlist(normalized, request.args.get("playlist"))
        return render_template(
            "catalog.html",
            catalog=normalized,
            selected_playlist=selected,
            selected_publications=_playlist_publications(normalized, selected),
        )

    @app.get("/p/<slug>")
    def publication_detail(slug):
        source = _source_publication(publications, slug)
        if source is None:
            abort(404)
        publication = _normalize_publication(source, app.config["PORTAL_TIMEZONE"])
        return render_template("publication.html", publication=publication)

    @app.get("/play")
    def play():
        _, normalized = read_catalog()
        selected = _selected_playlist(normalized, request.args.get("playlist"))
        return render_template(
            "play.html",
            catalog=normalized,
            selected_playlist=selected,
            slides=_playlist_publications(normalized, selected),
        )

    @app.get("/api/publications")
    def publications_api():
        source, normalized = read_catalog()
        etag = _catalog_etag(source, normalized)
        if request.if_none_match.contains(etag):
            response = app.response_class(status=304)
        else:
            response = jsonify(normalized)
        response.set_etag(etag)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        return response

    @app.get("/assets/<asset_id>")
    def publication_asset(asset_id):
        if not OPAQUE_ASSET_ID.fullmatch(asset_id):
            abort(404)
        asset = _source_asset(publications, asset_id)
        if asset is None:
            abort(404)
        body = _field(asset, "body")
        if not isinstance(body, (bytes, bytearray, memoryview)):
            abort(404)
        content_type = _plain_text(_field(asset, "content_type"), default="application/octet-stream", maximum=100)
        etag = _plain_text(_field(asset, "etag"), maximum=160).strip('"')
        if not etag:
            etag = hashlib.sha256(bytes(body)).hexdigest()
        if request.if_none_match.contains(etag):
            response = app.response_class(status=304)
        else:
            response = app.response_class(bytes(body), mimetype=content_type)
        response.set_etag(etag)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        return response

    @app.errorhandler(404)
    def not_found(_error):
        return (
            render_template(
                "error.html",
                title="没有找到这项内容",
                message="它可能已从播放列表中移除。",
            ),
            404,
        )

    return app
