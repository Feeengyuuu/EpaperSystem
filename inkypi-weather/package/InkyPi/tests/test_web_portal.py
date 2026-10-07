from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import re
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from web_portal import create_web_app
from web_portal.health import PortalHealthProbe, WorkerStateStore


BASE_URL = "https://portal.test"


class FakeCredentialStore:
    available = True

    def __init__(self, password="correct horse battery staple"):
        self.password = password
        self.revision = "credential-v1"
        self.verifications = []

    def has_admin(self):
        return True

    def verify_admin_password(self, password):
        self.verifications.append(password)
        return password == self.password

    def admin_session_revision(self):
        return self.revision if self.available else None


@dataclass(frozen=True)
class Asset:
    body: bytes
    content_type: str
    etag: str


class FakePublications:
    def __init__(self):
        self.catalog_calls = 0
        self.publication_calls = []
        self.asset_calls = []
        self.catalog_etag = "catalog-v1"
        self.publications = {
            "weather-home": {
                "slug": "weather-home",
                "title": "弗里蒙特天气",
                "plugin": "Weather",
                "kind": "native",
                "freshness": "fresh",
                "updated_at": "2026-08-02T16:30:00Z",
                "payload": {
                    "headline": "23°",
                    "summary": "晴，傍晚转多云",
                    "facts": [
                        {"label": "湿度", "value": "46%"},
                        {"label": "风速", "value": "11 km/h"},
                    ],
                },
            },
            "calendar-family": {
                "slug": "calendar-family",
                "title": "家庭日历",
                "plugin": "Calendar",
                "kind": "legacy_png",
                "freshness": "stale",
                "updated_at": "2026-08-02T14:00:00Z",
                "asset_id": "asset_calendar_01",
            },
            "tickets": {
                "slug": "tickets",
                "title": "附近活动",
                "plugin": "Ticketmaster",
                "kind": "native",
                "freshness": "unavailable",
                "updated_at": "2026-08-02T13:00:00Z",
                "message": "上次发布的内容暂不可用",
                "payload": {},
            },
        }
        self.assets = {
            "asset_calendar_01": Asset(
                body=b"\x89PNG\r\n\x1a\nprivate",
                content_type="image/png",
                etag="asset-v1",
            )
        }

    def list_publications(self):
        self.catalog_calls += 1
        return {
            "etag": self.catalog_etag,
            "active_playlist": "morning",
            "playlists": [
                {
                    "slug": "morning",
                    "name": "早间",
                    "active": True,
                    "window": "06:00–10:00",
                    "publication_slugs": ["weather-home", "calendar-family"],
                },
                {
                    "slug": "evening",
                    "name": "晚间",
                    "active": False,
                    "window": "18:00–23:00",
                    "publication_slugs": ["tickets"],
                },
            ],
            "publications": list(self.publications.values()),
        }

    def get_publication(self, slug):
        self.publication_calls.append(slug)
        return self.publications.get(slug)

    def get_asset(self, asset_id):
        self.asset_calls.append(asset_id)
        return self.assets.get(asset_id)


@pytest.fixture
def portal():
    publications = FakePublications()
    credentials = FakeCredentialStore()
    app = create_web_app(
        publications,
        credentials,
        "stable-test-secret",
        {
            "TESTING": True,
            "SERVER_NAME": "portal.test",
            "LOGIN_MAX_ATTEMPTS": 3,
            "LOGIN_WINDOW_SECONDS": 900,
        },
    )
    return app, publications, credentials


def _client_get(client, path, **kwargs):
    return client.get(path, base_url=BASE_URL, **kwargs)


def _client_post(client, path, **kwargs):
    return client.post(path, base_url=BASE_URL, **kwargs)


def _csrf(client):
    with client.session_transaction() as session:
        return session["csrf_token"]


def _login(client, password="correct horse battery staple", next_url=None):
    page = _client_get(client, "/login")
    assert page.status_code == 200
    data = {
        "username": "admin",
        "password": password,
        "csrf_token": _csrf(client),
    }
    if next_url:
        data["next"] = next_url
    return _client_post(client, "/login", data=data)


def test_factory_sets_private_30_day_session_and_security_defaults(portal):
    app, _, _ = portal

    assert app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(days=30)
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", 302),
        ("/p/weather-home", 302),
        ("/play", 302),
        ("/api/publications", 401),
        ("/assets/asset_calendar_01", 401),
    ],
)
def test_every_private_surface_requires_login(portal, path, expected):
    app, publications, _ = portal

    response = _client_get(app.test_client(), path)

    assert response.status_code == expected
    assert publications.catalog_calls == 0
    assert publications.publication_calls == []
    assert publications.asset_calls == []


@pytest.mark.parametrize("path", ["/healthz", "/livez"])
def test_liveness_is_public_dependency_free_and_reveals_only_status(portal, path):
    app, publications, _ = portal

    response = _client_get(app.test_client(), path)

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers
    assert publications.catalog_calls == 0


def test_readiness_requires_credentials_and_displayable_publications(portal):
    app, publications, credentials = portal
    publications.publications["tickets"]["freshness"] = "fresh"

    ready = _client_get(app.test_client(), "/readyz")
    assert ready.status_code == 200
    assert ready.get_json() == {"status": "ready"}
    assert ready.headers["Cache-Control"] == "no-store"
    assert publications.catalog_calls == 1

    ready_again = _client_get(app.test_client(), "/readyz")
    assert ready_again.status_code == 200
    assert ready_again.get_json() == {"status": "ready"}
    assert publications.catalog_calls == 1

    credentials.available = False
    not_ready = _client_get(app.test_client(), "/readyz")
    assert not_ready.status_code == 503
    assert not_ready.get_json() == {
        "reason": "credentials_unavailable",
        "status": "not_ready",
    }
    assert publications.catalog_calls == 1


def test_readiness_reports_partial_catalog_as_degraded(portal):
    app, publications, _ = portal

    response = _client_get(app.test_client(), "/readyz")

    assert response.status_code == 200
    assert response.get_json() == {
        "reason": "publication_degraded",
        "status": "degraded",
    }
    assert publications.catalog_calls == 1


def test_readiness_publication_cache_expires_without_caching_credentials():
    now = [100.0]
    publications = FakePublications()
    publications.publications["tickets"]["freshness"] = "fresh"
    credentials = FakeCredentialStore()
    probe = PortalHealthProbe(
        credentials,
        publications,
        publication_cache_seconds=5,
        cache_clock=lambda: now[0],
    )

    assert probe.inspect().status == "ready"
    assert probe.inspect().status == "ready"
    assert publications.catalog_calls == 1

    credentials.available = False
    assert probe.inspect().reason == "credentials_unavailable"
    assert publications.catalog_calls == 1

    credentials.available = True
    now[0] = 106.0
    assert probe.inspect().status == "ready"
    assert publications.catalog_calls == 2


def test_readiness_failure_is_fail_closed_and_never_logs_or_returns_exception_text(caplog):
    secret_canary = "signed-provider-url-secret-canary"

    class FailingPublications:
        def list_publications(self):
            raise RuntimeError(secret_canary)

        def get_publication(self, _slug):
            return None

        def get_asset(self, _asset_id):
            return None

    app = create_web_app(
        FailingPublications(),
        FakeCredentialStore(),
        "stable-test-secret",
        {"TESTING": True, "SERVER_NAME": "portal.test"},
    )

    response = _client_get(app.test_client(), "/readyz")

    assert response.status_code == 503
    assert response.get_json() == {
        "reason": "publication_unavailable",
        "status": "not_ready",
    }
    assert secret_canary not in response.get_data(as_text=True)
    assert secret_canary not in caplog.text


def test_readiness_stays_servable_but_degraded_when_worker_is_failed_or_stale(tmp_path):
    state_path = tmp_path / "worker-state.json"
    store = WorkerStateStore(state_path)
    store.record_completed({"attempted": 1, "published": 1}, 100.0)
    now = [120.0]
    publications = FakePublications()
    publications.publications["tickets"]["freshness"] = "fresh"
    app = create_web_app(
        publications,
        FakeCredentialStore(),
        "stable-test-secret",
        {
            "HEALTH_CLOCK": lambda: now[0],
            "SERVER_NAME": "portal.test",
            "TESTING": True,
            "WORKER_STATE_MAX_AGE_SECONDS": 60,
            "WORKER_STATE_PATH": str(state_path),
        },
    )

    ready = _client_get(app.test_client(), "/readyz")
    assert ready.status_code == 200
    assert ready.get_json() == {"status": "ready"}

    store.record_failed(130.0)
    now[0] = 140.0
    degraded = _client_get(app.test_client(), "/readyz")
    assert degraded.status_code == 200
    assert degraded.get_json() == {
        "reason": "worker_degraded",
        "status": "degraded",
    }

    now[0] = 1_000.0
    stale = _client_get(app.test_client(), "/readyz")
    assert stale.status_code == 200
    assert stale.get_json() == {
        "reason": "worker_stale",
        "status": "degraded",
    }


def test_login_uses_csrf_admin_credentials_and_rotates_session(portal):
    app, _, credentials = portal
    client = app.test_client()

    page = _client_get(client, "/login")
    page_text = page.get_data(as_text=True)
    old_csrf = _csrf(client)
    missing_csrf = _client_post(
        client,
        "/login",
        data={"username": "admin", "password": credentials.password},
    )
    wrong_user = _client_post(
        client,
        "/login",
        data={
            "username": "root",
            "password": credentials.password,
            "csrf_token": old_csrf,
        },
    )
    valid = _client_post(
        client,
        "/login",
        data={
            "username": "admin",
            "password": credentials.password,
            "csrf_token": old_csrf,
        },
    )

    assert 'name="csrf_token"' in page_text
    assert 'autocomplete="current-password"' in page_text
    assert missing_csrf.status_code == 400
    assert wrong_user.status_code == 401
    assert valid.status_code == 302
    assert valid.headers["Location"].endswith("/")
    with client.session_transaction() as session:
        assert session["admin_identity"] == "admin"
        assert session["admin_revision"] == credentials.revision
        assert session["csrf_token"] != old_csrf
        assert session.permanent
    cookies = valid.headers.getlist("Set-Cookie")
    assert any("Secure" in value for value in cookies)
    assert all("HttpOnly" in value for value in cookies)
    assert all("SameSite=Lax" in value for value in cookies)


def test_login_redirect_target_is_local_only(portal):
    app, _, _ = portal
    client = app.test_client()

    malicious = _login(client, next_url="https://attacker.invalid/steal")

    assert malicious.headers["Location"].endswith("/")


def test_password_rotation_or_credential_failure_revokes_existing_session(portal):
    app, _, credentials = portal
    client = app.test_client()
    assert _login(client).status_code == 302

    credentials.revision = "credential-v2"
    rotated = _client_get(client, "/")

    assert rotated.status_code == 302
    assert "/login" in rotated.headers["Location"]
    with client.session_transaction() as stored_session:
        assert "admin_identity" not in stored_session
        assert "admin_revision" not in stored_session

    assert _login(client).status_code == 302
    credentials.available = False

    assert _client_get(client, "/api/publications").status_code == 401


def test_login_is_rate_limited_without_echoing_password(portal):
    app, _, _ = portal
    client = app.test_client()
    page = _client_get(client, "/login")
    token = _csrf(client)

    responses = [
        _client_post(
            client,
            "/login",
            data={"username": "admin", "password": f"wrong-{number}", "csrf_token": token},
        )
        for number in range(4)
    ]

    assert page.status_code == 200
    assert [response.status_code for response in responses] == [401, 401, 401, 429]
    assert "wrong-3" not in responses[-1].get_data(as_text=True)
    assert responses[-1].headers["Retry-After"].isdigit()


def test_login_limiter_has_global_budget_and_bounded_client_tracking():
    credentials = FakeCredentialStore()
    app = create_web_app(
        FakePublications(),
        credentials,
        "stable-test-secret",
        {
            "TESTING": True,
            "SERVER_NAME": "portal.test",
            "LOGIN_MAX_ATTEMPTS": 10,
            "LOGIN_GLOBAL_MAX_ATTEMPTS": 3,
            "LOGIN_MAX_TRACKED_CLIENTS": 2,
        },
    )
    responses = []
    for number in range(4):
        client = app.test_client()
        address = f"203.0.113.{number + 1}"
        client.get("/login", base_url=BASE_URL, environ_base={"REMOTE_ADDR": address})
        responses.append(
            client.post(
                "/login",
                base_url=BASE_URL,
                environ_base={"REMOTE_ADDR": address},
                data={
                    "username": "admin",
                    "password": f"wrong-{number}",
                    "csrf_token": _csrf(client),
                },
            )
        )

    limiter = app.extensions["web_portal_login_limiter"]
    assert [response.status_code for response in responses] == [401, 401, 401, 429]
    assert len(credentials.verifications) == 3
    assert limiter.tracked_client_count <= 2


def test_logout_is_post_only_and_requires_csrf(portal):
    app, _, _ = portal
    client = app.test_client()
    assert _login(client).status_code == 302

    get_response = _client_get(client, "/logout")
    no_csrf = _client_post(client, "/logout")
    logged_out = _client_post(client, "/logout", data={"csrf_token": _csrf(client)})

    assert get_response.status_code == 405
    assert no_csrf.status_code == 400
    assert logged_out.status_code == 302
    with client.session_transaction() as session:
        assert "admin_identity" not in session


def test_catalog_renders_chinese_playlists_native_legacy_and_states(portal):
    app, _, _ = portal
    client = app.test_client()
    _login(client)

    response = _client_get(client, "/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<html lang="zh-CN"' in html
    assert "早间" in html and "晚间" in html
    assert "06:00–10:00" in html
    assert "弗里蒙特天气" in html and "23°" in html
    assert "家庭日历" in html and "/assets/asset_calendar_01" in html
    assert "最新" in html and "已过时" in html
    assert "2026年8月2日 09:30" in html
    assert "进入自动播放" in html
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "noindex" in response.headers["X-Robots-Tag"]


def test_playlist_query_preserves_source_order_and_selects_tab(portal):
    app, _, _ = portal
    client = app.test_client()
    _login(client)

    response = _client_get(client, "/?playlist=evening")
    html = response.get_data(as_text=True)
    tabs = re.search(r'<nav class="playlist-tabs".*?</nav>', html, re.DOTALL).group(0)

    assert tabs.index("早间") < tabs.index("晚间")
    assert re.search(r'aria-selected="true"[^>]*>\s*晚间', tabs)
    assert "附近活动" in html
    assert "上次发布的内容暂不可用" in html


def test_stale_last_good_edition_explains_what_is_being_shown(portal):
    app, publications, _ = portal
    publications.publications["calendar-family"]["message"] = "显示最近一次成功发布的内容"
    client = app.test_client()
    _login(client)

    response = _client_get(client, "/")

    assert "已过时" in response.get_data(as_text=True)
    assert "显示最近一次成功发布的内容" in response.get_data(as_text=True)


def test_publication_detail_supports_native_and_missing_slug(portal):
    app, publications, _ = portal
    client = app.test_client()
    _login(client)

    response = _client_get(client, "/p/weather-home")
    missing = _client_get(client, "/p/not-found")

    assert response.status_code == 200
    assert "晴，傍晚转多云" in response.get_data(as_text=True)
    assert publications.publication_calls == ["weather-home", "not-found"]
    assert missing.status_code == 404


def test_original_weather_raster_renders_inside_the_portal_structure(portal):
    app, publications, _ = portal
    original_png = b"\x89PNG\r\n\x1a\noriginal-weather-frame"
    publications.publications["weather-home"].update(
        {
            "kind": "legacy_png",
            "asset_id": "asset_weather_original",
            "payload": {"alt": "Fremont Weather", "width": 800, "height": 480},
        }
    )
    publications.assets["asset_weather_original"] = Asset(
        body=original_png,
        content_type="image/png",
        etag="weather-original-v1",
    )
    client = app.test_client()
    _login(client)

    page = _client_get(client, "/p/weather-home")
    asset = _client_get(client, "/assets/asset_weather_original")

    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "EpaperSystem 简报" in html
    assert "当地天气" in html
    assert "弗里蒙特天气" not in html
    assert "当地时间" in html
    assert "洛杉矶时间" not in html
    assert 'src="/assets/asset_weather_original"' in html
    assert "原版插件画面" in html
    assert "兼容图片视图" not in html
    assert asset.status_code == 200
    assert asset.data == original_png


def test_native_structured_payloads_render_useful_rows_without_unknown_fields(portal):
    app, publications, _ = portal
    publications.publications.update(
        {
            "stocks": {
                "slug": "stocks",
                "title": "投资组合",
                "plugin": "Stock Tracker",
                "kind": "native",
                "freshness": "fresh",
                "updated_at": "2026-08-02T16:30:00Z",
                "payload": {
                    "kind": "stock_portfolio",
                    "summary": "Portfolio USD 250.75; +20.00 (+8.67%)",
                    "portfolio": {
                        "value": 250.75,
                        "change": 20.0,
                        "change_percent": 8.67,
                        "currency": "USD",
                    },
                    "items": [
                        {
                            "symbol": "AAPL",
                            "name": "Apple",
                            "price": 110.0,
                            "change_percent": 10.0,
                            "shares": 2,
                            "internal_path": "C:/private/portfolio.json",
                        }
                    ],
                },
            },
            "sports": {
                "slug": "sports",
                "title": "赛事速览",
                "plugin": "Sports Dashboard",
                "kind": "native",
                "freshness": "fresh",
                "updated_at": "2026-08-02T16:30:00Z",
                "payload": {
                    "kind": "sports_dashboard",
                    "summary": "1 live across 2 sports sections",
                    "live_count": 1,
                    "events": [
                        {
                            "section": "NBA",
                            "team_a": "LAL",
                            "team_b": "GSW",
                            "score": "99–101",
                            "status": "Final",
                            "provider": "ESPN",
                            "source_url": "https://example.invalid/?token=must-not-leak",
                        }
                    ],
                },
            },
            "streams": {
                "slug": "streams",
                "title": "直播雷达",
                "plugin": "LiveRadar",
                "kind": "native",
                "freshness": "fresh",
                "updated_at": "2026-08-02T16:30:00Z",
                "payload": {
                    "kind": "stream_status",
                    "summary": "1 live: Channel One",
                    "live": [
                        {
                            "platform": "Twitch",
                            "owner": "Channel One",
                            "title": "Sunday stream",
                            "heat": 88,
                            "id": "private-room-id",
                        }
                    ],
                    "replay": [],
                },
            },
            "games": {
                "slug": "games",
                "title": "Steam 热门",
                "plugin": "Steam Charts",
                "kind": "native",
                "freshness": "fresh",
                "updated_at": "2026-08-02T16:30:00Z",
                "payload": {
                    "kind": "game_chart",
                    "summary": "Steam Charts Most Played",
                    "items": [
                        {
                            "rank": 1,
                            "name": "Counter-Strike 2",
                            "current_players": "1,250,000",
                            "change_24h": "+2.4%",
                            "appid": "730",
                        }
                    ],
                },
            },
            "events": {
                "slug": "events",
                "title": "附近活动",
                "plugin": "Ticketmaster",
                "kind": "native",
                "freshness": "fresh",
                "updated_at": "2026-08-02T16:30:00Z",
                "payload": {
                    "kind": "ticketmaster_events",
                    "summary": "Upcoming local events",
                    "items": [
                        {
                            "rank": 1,
                            "title": "Dodgers vs. Giants",
                            "local_date": "2026-08-08",
                            "local_time": "19:10",
                            "venue_name": "Dodger Stadium",
                            "city": "Los Angeles",
                            "price": "$45+",
                            "url": "https://example.invalid/?token=must-not-leak",
                            "poster_path": "C:/private/poster.jpg",
                        }
                    ],
                },
            },
            "forecast": {
                "slug": "forecast",
                "title": "四日天气",
                "plugin": "Weather",
                "kind": "native",
                "freshness": "fresh",
                "updated_at": "2026-08-02T16:30:00Z",
                "payload": {
                    "kind": "weather",
                    "summary": "Los Angeles; current 72°F",
                    "forecast": [
                        {"day": "周日", "high": 80, "low": 65},
                        {"day": "周一", "high": 78, "low": 64},
                    ],
                },
            },
        }
    )
    client = app.test_client()
    _login(client)

    pages = {
        slug: _client_get(client, f"/p/{slug}").get_data(as_text=True)
        for slug in ("stocks", "sports", "streams", "games", "events", "forecast")
    }

    assert "USD 250.75" in pages["stocks"]
    assert "AAPL" in pages["stocks"] and "Apple" in pages["stocks"]
    assert "LAL 对 GSW" in pages["sports"] and "99–101" in pages["sports"]
    assert "Channel One" in pages["streams"] and "Sunday stream" in pages["streams"]
    assert "Counter-Strike 2" in pages["games"] and "1,250,000" in pages["games"]
    assert "Dodgers vs. Giants" in pages["events"] and "Dodger Stadium" in pages["events"]
    assert "周日" in pages["forecast"] and "最高 80 / 最低 65" in pages["forecast"]
    combined = "".join(pages.values())
    assert "must-not-leak" not in combined
    assert "C:/private" not in combined
    assert "private-room-id" not in combined


def test_private_asset_supports_etag_and_never_accepts_path_traversal(portal):
    app, publications, _ = portal
    client = app.test_client()
    _login(client)

    response = _client_get(client, "/assets/asset_calendar_01")
    unchanged = _client_get(
        client,
        "/assets/asset_calendar_01",
        headers={"If-None-Match": response.headers["ETag"]},
    )
    traversal = _client_get(client, "/assets/..%2Fadmin_credentials.json")

    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")
    assert response.mimetype == "image/png"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert unchanged.status_code == 304
    assert unchanged.headers["Cache-Control"] == "private, no-store"
    assert traversal.status_code == 404
    assert publications.asset_calls == ["asset_calendar_01", "asset_calendar_01"]


def test_publications_api_is_private_json_with_conditional_etag(portal):
    app, publications, _ = portal
    client = app.test_client()
    _login(client)

    response = _client_get(client, "/api/publications")
    unchanged = _client_get(
        client,
        "/api/publications",
        headers={"If-None-Match": response.headers["ETag"]},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["active_playlist"] == "morning"
    assert payload["publications"][0]["slug"] == "weather-home"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert unchanged.status_code == 304
    assert unchanged.headers["Cache-Control"] == "private, no-store"
    assert publications.catalog_calls == 2


def test_play_route_has_20_second_controls_polling_and_accessibility(portal):
    app, _, _ = portal
    client = app.test_client()
    _login(client)

    response = _client_get(client, "/play?playlist=morning")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-interval-ms="20000"' in html
    assert 'data-poll-ms="30000"' in html
    assert "上一页" in html and "暂停播放" in html and "下一页" in html
    assert "全屏显示" in html
    assert 'data-play-chrome-toggle' in html
    assert 'aria-pressed="false"' in html
    assert "隐藏导航" in html
    assert 'aria-live="polite"' in html
    assert "legacy-png" in html


def test_playback_fills_vehicle_viewport_without_cropping_original_frame(portal):
    app, _, _ = portal
    css = _client_get(app.test_client(), "/web-static/portal.css").get_data(as_text=True)

    shell = re.search(r"\.play-shell\s*\{(?P<body>[^}]*)\}", css)
    frame = re.search(
        r"\.play-slide \.original-plugin-frame img\s*\{(?P<body>[^}]*)\}",
        css,
    )
    controls = re.search(r"\.play-controls\s*\{(?P<body>[^}]*)\}", css)
    chrome_toggle = re.search(r"\.play-chrome-toggle\s*\{(?P<body>[^}]*)\}", css)
    hidden_chrome = re.search(
        r"\.play-shell\.is-chrome-hidden \.play-heading,\s*"
        r"\.play-shell\.is-chrome-hidden \.play-publication-heading,\s*"
        r"\.play-shell\.is-chrome-hidden \.play-controls\s*\{(?P<body>[^}]*)\}",
        css,
    )

    assert shell is not None
    assert "position: fixed" in shell.group("body")
    assert "inset: 0" in shell.group("body")
    assert "height: 100dvh" in shell.group("body")
    assert "overflow: hidden" in shell.group("body")

    assert frame is not None
    assert "position: absolute" in frame.group("body")
    assert "inset: 0" in frame.group("body")
    assert "width: 100%" in frame.group("body")
    assert "height: 100%" in frame.group("body")
    assert "max-width: none" in frame.group("body")
    assert "max-height: none" in frame.group("body")
    assert "object-fit: contain" in frame.group("body")

    assert controls is not None
    assert "position: absolute" in controls.group("body")

    assert chrome_toggle is not None
    assert "position: absolute" in chrome_toggle.group("body")
    assert "min-width: 48px" in chrome_toggle.group("body")
    assert "min-height: 48px" in chrome_toggle.group("body")

    assert hidden_chrome is not None
    assert "display: none" in hidden_chrome.group("body")


def test_static_product_assets_use_no_third_party_code_and_required_a11y_rules(portal):
    app, _, _ = portal
    client = app.test_client()
    page = _client_get(client, "/login")
    css = _client_get(client, "/web-static/portal.css")
    js = _client_get(client, "/web-static/portal.js")
    favicon = _client_get(client, "/web-static/favicon.svg")

    assert css.status_code == 200
    assert js.status_code == 200
    assert favicon.status_code == 200
    assert 'rel="icon"' in page.get_data(as_text=True)
    css_text = css.get_data(as_text=True)
    js_text = js.get_data(as_text=True)
    assert "min-height: 48px" in css_text
    assert "prefers-color-scheme: dark" in css_text
    assert "prefers-reduced-motion: reduce" in css_text
    assert "setInterval" in js_text and "30000" not in js_text
    assert "document.hidden" in js_text
    assert "knownEtag !== nextEtag" in js_text
    assert "AbortController" in js_text
    assert "pollAgain" in js_text
    assert "http://" not in js_text and "https://" not in js_text


def test_publication_title_links_keep_a_model_y_sized_touch_target(portal):
    app, _, _ = portal
    css = _client_get(app.test_client(), "/web-static/portal.css").get_data(as_text=True)

    rule = re.search(r"\.publication-heading h2 a\s*\{(?P<body>[^}]*)\}", css)

    assert rule is not None
    assert "min-height: 48px" in rule.group("body")


def test_touch_autoplay_keeps_controls_available_after_idle(portal):
    app, _, _ = portal
    css = _client_get(app.test_client(), "/web-static/portal.css").get_data(as_text=True)

    touch_controls = re.search(
        r"@media \(hover: none\), \(pointer: coarse\).*?"
        r"\.play-controls\.is-idle:not\(:focus-within\)\s*\{(?P<body>[^}]*)\}",
        css,
        re.DOTALL,
    )

    assert touch_controls is not None
    assert "opacity: 1" in touch_controls.group("body")
    assert "pointer-events: auto" in touch_controls.group("body")


def test_security_headers_block_embedding_and_third_party_scripts(portal):
    app, _, _ = portal
    client = app.test_client()

    login = _client_get(client, "/login")

    assert login.headers["X-Frame-Options"] == "DENY"
    assert login.headers["X-Content-Type-Options"] == "nosniff"
    assert login.headers["Referrer-Policy"] == "no-referrer"
    csp = login.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp


def test_unavailable_credential_store_fails_closed():
    class BrokenCredentialStore(FakeCredentialStore):
        available = False

        def has_admin(self):
            raise RuntimeError("secret internal path")

    app = create_web_app(FakePublications(), BrokenCredentialStore(), "secret", {"TESTING": True})

    response = app.test_client().get("/login", base_url=BASE_URL)

    assert response.status_code == 503
    assert "secret internal path" not in response.get_data(as_text=True)
