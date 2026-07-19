import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw


try:
    import psutil  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault(
        "psutil",
        SimpleNamespace(
            virtual_memory=lambda: SimpleNamespace(total=2 * 1024**3),
            swap_memory=lambda: SimpleNamespace(percent=0.0),
        ),
    )
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.presentation import PresentationMode  # noqa: E402
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)
import plugins.orbital_signal.launch_photo as launch_photo_module  # noqa: E402
from plugins.orbital_signal.orbital_signal import (  # noqa: E402
    DAY_PALETTE,
    DEFAULT_FONT,
    NIGHT_PALETTE,
    PLUGIN_ID,
    OrbitalSignal,
)
from plugins.orbital_signal.launch_photo import (  # noqa: E402
    load_or_acquire_photo,
    photo_cache_key,
    photo_candidates,
)
from plugins.orbital_signal.sources import (  # noqa: E402
    LAUNCHES_URL,
    MARKETS_URL,
    fetch_launches,
    fetch_market_events,
    heat_score,
    normalize_launches,
    normalize_market_events,
)


NOW = datetime(2026, 7, 17, 21, 20, tzinfo=timezone.utc)


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone_name="America/Los_Angeles"):
        self.resolution = resolution
        self.timezone_name = timezone_name

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "timezone": self.timezone_name,
            "orientation": "horizontal",
            "theme_mode": "day",
        }
        return values if key is None else values.get(key, default)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class RecordingSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


class MemoryPhotoNamespace:
    def __init__(self):
        self.values = {}
        self.get_calls = []
        self.put_calls = []

    def get_bytes(self, key, *, suffix=""):
        self.get_calls.append((key, suffix))
        return self.values.get((key, suffix))

    def put_bytes(self, key, data, *, suffix=""):
        self.put_calls.append((key, suffix))
        self.values[(key, suffix)] = bytes(data)
        return Path(f"{key}{suffix}")


class FakeApprovedTarget:
    def __init__(self, url):
        self.normalized_url = url


class PermissivePhotoPolicy:
    def resolve_and_validate(self, url):
        return FakeApprovedTarget(url)


class StreamingPhotoResponse:
    def __init__(self, url, payload=b"", *, status=200, headers=None):
        self.url = url
        self.payload = payload
        self.status_code = status
        self.headers = dict(headers or {})
        self.closed = False

    def raise_for_status(self):
        if not 200 <= self.status_code < 300:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset : offset + chunk_size]

    def close(self):
        self.closed = True


class StreamingPhotoSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


def _png_bytes(color=(30, 120, 220), size=(64, 128)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class RecordingDraw:
    def __init__(self, size=(800, 480)):
        self.image = Image.new("RGB", size, "white")
        self.delegate = ImageDraw.Draw(self.image)
        self.text_calls = []
        self.ellipse_calls = []

    def text(self, xy, text, font=None, fill=None, **kwargs):
        bbox = self.delegate.textbbox(xy, text, font=font, **kwargs)
        self.text_calls.append({"xy": xy, "text": text, "bbox": bbox})
        return self.delegate.text(xy, text, font=font, fill=fill, **kwargs)

    def ellipse(self, xy, **kwargs):
        self.ellipse_calls.append(tuple(xy))
        return self.delegate.ellipse(xy, **kwargs)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


def launch_payload():
    return {
        "results": [
            {
                "id": "vikram-demo",
                "name": "Vikram-I | Demo Flight",
                "net": "2026-07-18T06:00:00Z",
                "status": {"abbrev": "Go", "name": "Go for Launch"},
                "launch_service_provider": {"name": "Skyroot Aerospace"},
                "rocket": {"configuration": {"full_name": "Vikram-I"}},
                "mission": {
                    "name": "Demo Flight",
                    "orbit": {"abbrev": "LEO"},
                },
                "pad": {
                    "name": "First Launch Pad",
                    "location": {"name": "Satish Dhawan Space Centre, India"},
                },
                "webcast_live": False,
            },
            {
                "id": "falcon-starlink",
                "name": "Falcon 9 Block 5 | Starlink Group 17-39",
                "net": "2026-07-20T14:00:00Z",
                "status": {"abbrev": "Go"},
                "launch_service_provider": {"name": "SpaceX"},
                "rocket": {"configuration": {"full_name": "Falcon 9 Block 5"}},
                "mission": {"name": "Starlink Group 17-39", "orbit": {"abbrev": "LEO"}},
                "pad": {"name": "SLC-4E", "location": {"name": "Vandenberg SFB, USA"}},
            },
            {"id": "bad-no-time", "name": "Missing NET"},
        ]
    }


def market_payload():
    return [
        {
            "id": "world-cup",
            "title": "World Cup Winner",
            "endDate": "2026-07-20T00:00:00Z",
            "volume24hr": 4_610_850,
            "liquidity": 18_218_089,
            "tags": [{"label": "Sports"}, {"label": "Soccer"}],
            "markets": [
                {
                    "question": "Will Argentina win the 2026 FIFA World Cup?",
                    "groupItemTitle": "Argentina",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.4045", "0.5955"]',
                    "oneDayPriceChange": -0.006,
                    "active": True,
                    "closed": False,
                },
                {
                    "question": "Will Spain win the 2026 FIFA World Cup?",
                    "groupItemTitle": "Spain",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.5915", "0.4085"]',
                    "oneDayPriceChange": 0.008,
                    "active": True,
                    "closed": False,
                },
            ],
        },
        {
            "id": "france-england",
            "title": "France vs. England: Team to Win",
            "endDate": "2026-07-18T21:00:00Z",
            "volume24hr": 1_191_752,
            "liquidity": 2_052_124,
            "tags": [{"label": "Sports"}],
            "markets": [
                {
                    "question": "France vs. England: Team to Win",
                    "outcomes": '["France", "England"]',
                    "outcomePrices": '["0.665", "0.335"]',
                    "oneDayPriceChange": 0.03,
                    "active": True,
                    "closed": False,
                }
            ],
        },
        {
            "id": "expired",
            "title": "Already resolved",
            "endDate": "2026-07-10T00:00:00Z",
            "volume24hr": 99_000_000,
            "markets": [
                {
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.99", "0.01"]',
                }
            ],
        },
    ]


def test_manifest_and_settings_match_static_data_plugin_contract():
    plugin_dir = Path(__file__).resolve().parents[1] / "src" / "plugins" / PLUGIN_ID
    info = json.loads((plugin_dir / "plugin-info.json").read_text(encoding="utf-8"))
    settings = (plugin_dir / "settings.html").read_text(encoding="utf-8")

    assert info["id"] == "orbital_signal"
    assert info["class"] == "OrbitalSignal"
    assert info["display_name"] == "Orbital Signal"
    assert info["capabilities"]["supports_live_refresh"] is False
    assert info["capabilities"]["supports_presentation_refresh"] is False
    assert info["capabilities"]["supports_day_night_theme"] is True
    assert info["refresh_on_display"] is True
    assert 'name="refreshOnDisplay"' in settings
    assert 'value="true"' in settings
    assert 'name="refreshMinutes"' in settings
    assert 'value="60"' in settings
    assert 'name="forceRefresh"' in settings
    assert 'name="fontFamily"' in settings
    assert DEFAULT_FONT == "Microsoft YaHei"


def test_normalize_launches_keeps_displayable_global_fields_and_order():
    rows = normalize_launches(launch_payload(), now=NOW, limit=4)

    assert [row["id"] for row in rows] == ["vikram-demo", "falcon-starlink"]
    assert rows[0] == {
        "id": "vikram-demo",
        "name": "Vikram-I | Demo Flight",
        "net": "2026-07-18T06:00:00+00:00",
        "status": "GO",
        "provider": "Skyroot Aerospace",
        "rocket": "Vikram-I",
        "mission": "Demo Flight",
        "orbit": "LEO",
        "pad": "First Launch Pad",
        "location": "Satish Dhawan Space Centre, India",
        "webcast_live": False,
        "image_url": "",
        "thumbnail_url": "",
        "image_credit": "",
        "image_license": "",
        "image_source": "",
        "fallback_image_url": "",
        "fallback_thumbnail_url": "",
        "fallback_image_credit": "",
        "fallback_image_license": "",
        "fallback_image_source": "",
    }


def test_normalize_launches_prefers_launch_image_and_keeps_launcher_fallback():
    payload = launch_payload()
    payload["results"][0]["image"] = {
        "image_url": "https://images.example/launch.jpg",
        "thumbnail_url": "https://images.example/launch-thumb.jpg",
        "credit": "Skyroot",
        "license": {"name": "CC BY 4.0"},
    }
    payload["results"][0]["rocket"]["configuration"]["image"] = {
        "image_url": "https://images.example/vikram.jpg",
        "thumbnail_url": "https://images.example/vikram-thumb.jpg",
        "credit": "Manufacturer",
        "license": "Unknown",
    }

    row = normalize_launches(payload, now=NOW)[0]

    assert row["image_url"] == "https://images.example/launch.jpg"
    assert row["thumbnail_url"] == "https://images.example/launch-thumb.jpg"
    assert row["image_credit"] == "Skyroot"
    assert row["image_license"] == "CC BY 4.0"
    assert row["image_source"] == "launch"
    assert row["fallback_image_url"] == "https://images.example/vikram.jpg"
    assert row["fallback_thumbnail_url"] == "https://images.example/vikram-thumb.jpg"
    assert row["fallback_image_credit"] == "Manufacturer"
    assert row["fallback_image_license"] == "Unknown"
    assert row["fallback_image_source"] == "launcher_configuration"


def test_normalize_launches_uses_launcher_image_then_stable_empty_fields():
    payload = launch_payload()
    payload["results"][0]["rocket"]["configuration"]["image"] = {
        "image_url": "https://images.example/vikram.jpg",
        "credit": "Manufacturer",
    }

    rows = normalize_launches(payload, now=NOW)

    assert rows[0]["image_url"] == "https://images.example/vikram.jpg"
    assert rows[0]["thumbnail_url"] == ""
    assert rows[0]["image_source"] == "launcher_configuration"
    assert rows[0]["fallback_image_url"] == ""
    assert rows[1]["image_url"] == ""
    assert rows[1]["thumbnail_url"] == ""
    assert rows[1]["image_credit"] == ""
    assert rows[1]["image_license"] == ""
    assert rows[1]["image_source"] == ""
    assert rows[1]["fallback_image_url"] == ""


def test_photo_candidates_keep_required_source_order_and_remove_duplicates():
    launch = {
        "image_url": "https://images.example/launch.jpg",
        "thumbnail_url": "https://images.example/launch-thumb.jpg",
        "image_credit": "Launch credit",
        "image_license": "CC BY",
        "image_source": "launch",
        "fallback_image_url": "https://images.example/rocket.jpg",
        "fallback_thumbnail_url": "https://images.example/launch-thumb.jpg",
        "fallback_image_credit": "Rocket credit",
        "fallback_image_license": "Unknown",
        "fallback_image_source": "launcher_configuration",
    }

    candidates = photo_candidates(launch)

    assert [item.url for item in candidates] == [
        "https://images.example/launch.jpg",
        "https://images.example/launch-thumb.jpg",
        "https://images.example/rocket.jpg",
    ]
    assert candidates[0].credit == "Launch credit"
    assert candidates[-1].source == "launcher_configuration"


def test_launch_photo_cache_hit_never_creates_an_http_session():
    launch = {
        "image_url": "https://images.example/launch.jpg",
        "image_credit": "Skyroot",
        "image_license": "CC BY 4.0",
        "image_source": "launch",
    }
    namespace = MemoryPhotoNamespace()
    key = photo_cache_key(launch["image_url"])
    namespace.values[(key, ".png")] = _png_bytes()

    cached = load_or_acquire_photo(
        launch,
        namespace,
        allow_network=False,
        session=lambda: (_ for _ in ()).throw(AssertionError("HTTP session used")),
    )

    assert cached is not None
    assert cached.cache_key == key
    assert cached.image.mode == "RGB"
    assert cached.credit == "Skyroot"
    assert namespace.put_calls == []


def test_launch_photo_corrupt_cache_and_empty_candidates_fail_closed():
    launch = {"image_url": "https://images.example/broken.jpg"}
    namespace = MemoryPhotoNamespace()
    namespace.values[(photo_cache_key(launch["image_url"]), ".png")] = b"not an image"

    assert load_or_acquire_photo(launch, namespace, allow_network=False) is None
    assert load_or_acquire_photo({}, namespace, allow_network=False) is None


def test_launch_photo_rejects_oversized_primary_then_caches_thumbnail(monkeypatch):
    launch = {
        "image_url": "https://images.example/launch.jpg",
        "thumbnail_url": "https://images.example/launch-thumb.jpg",
        "image_credit": "SpaceX",
        "image_license": "CC BY-NC 2.0",
        "image_source": "launch",
    }
    oversized = StreamingPhotoResponse(
        launch["image_url"],
        headers={"Content-Length": str(8 * 1024 * 1024 + 1)},
    )
    thumbnail = StreamingPhotoResponse(launch["thumbnail_url"], _png_bytes())
    session = StreamingPhotoSession([oversized, thumbnail])
    namespace = MemoryPhotoNamespace()
    monkeypatch.setattr(
        launch_photo_module,
        "get_ssrf_policy",
        lambda: PermissivePhotoPolicy(),
    )

    acquired = load_or_acquire_photo(
        launch,
        namespace,
        allow_network=True,
        session=session,
    )

    assert acquired is not None
    assert acquired.cache_key == photo_cache_key(launch["thumbnail_url"])
    assert acquired.credit == "SpaceX"
    assert acquired.image.getpixel((0, 0)) == (30, 120, 220)
    assert len(session.calls) == 2
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert oversized.closed is True
    assert thumbnail.closed is True
    assert namespace.put_calls == [(acquired.cache_key, ".png")]


def test_rocket_preserving_crop_is_exact_and_selects_tall_subject():
    source = Image.new("RGB", (360, 240), (176, 205, 225))
    draw = ImageDraw.Draw(source)
    draw.rectangle((302, 18, 324, 229), fill=(242, 242, 235))
    draw.rectangle((307, 18, 319, 229), fill=(35, 38, 44))
    draw.rectangle((15, 15, 55, 45), fill=(20, 20, 20))

    crop = launch_photo_module.rocket_preserving_crop(source, (113, 247))

    assert crop.size == (113, 247)
    assert crop.mode == "RGB"
    assert sum(1 for red, green, blue in crop.get_flattened_data() if max(red, green, blue) < 80) > 500


def test_normalize_market_events_selects_leading_outcome_and_drops_expired():
    rows = normalize_market_events(market_payload(), now=NOW, limit=4)

    assert [row["id"] for row in rows] == ["world-cup", "france-england"]
    assert rows[0]["title"] == "World Cup Winner"
    assert rows[0]["leader"] == "Spain"
    assert rows[0]["probability"] == pytest.approx(0.5915)
    assert rows[0]["change_24h"] == pytest.approx(0.008)
    assert rows[0]["category"] == "SPORT"
    assert rows[1]["leader"] == "France"
    assert rows[1]["probability"] == pytest.approx(0.665)
    assert rows[1]["change_24h"] == pytest.approx(0.03)


def test_market_normalizer_rejects_malformed_prices_instead_of_inventing_probability():
    payload = market_payload()
    payload[0]["markets"][0]["outcomePrices"] = "not-json"
    payload[0]["markets"][1]["outcomePrices"] = '["NaN", "0.4"]'

    rows = normalize_market_events(payload, now=NOW, limit=4)

    assert [row["id"] for row in rows] == ["france-england"]


def test_market_category_uses_event_subject_before_broad_political_tag():
    payload = [
        {
            "id": "fed-july",
            "title": "Fed Decision in July?",
            "endDate": "2026-07-30T00:00:00Z",
            "volume24hr": 3_200_000,
            "liquidity": 900_000,
            "tags": [{"label": "Politics"}, {"label": "Economy"}],
            "markets": [
                {
                    "question": "Will the Fed make no change?",
                    "groupItemTitle": "No change",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.94", "0.06"]',
                    "oneDayPriceChange": -0.014,
                }
            ],
        }
    ]

    rows = normalize_market_events(payload, now=NOW)

    assert rows[0]["category"] == "ECONOMY"


def test_heat_score_is_bounded_and_increases_with_volume_or_movement():
    quiet = heat_score(10_000, 0.001)
    liquid = heat_score(1_000_000, 0.001)
    moving = heat_score(1_000_000, 0.08)

    assert 0 <= quiet < liquid < moving <= 100
    assert heat_score(-1, -99) == 100


def test_fetchers_use_public_official_endpoints_and_bounded_requests():
    launch_session = RecordingSession(launch_payload())
    market_session = RecordingSession(market_payload())

    fetch_launches(session=launch_session, now=NOW)
    fetch_market_events(session=market_session, now=NOW)

    launch_url, launch_kwargs = launch_session.calls[0]
    market_url, market_kwargs = market_session.calls[0]
    assert launch_url == LAUNCHES_URL
    assert launch_kwargs["params"] == {
        "limit": 4,
        "mode": "normal",
        "ordering": "net",
        "hide_recent_previous": "true",
    }
    assert market_url == MARKETS_URL
    assert market_kwargs["params"] == {
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
        "limit": 30,
    }
    assert "User-Agent" in launch_kwargs["headers"]
    assert "User-Agent" in market_kwargs["headers"]


def test_presentation_mode_never_requests_internal_refresh():
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    assert plugin.presentation_mode({}) is PresentationMode.NO_CHANGE


def test_fresh_cache_avoids_network_and_stale_cache_survives_failure(tmp_path):
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    cache_file = plugin._cache_dir() / "launches.json"
    plugin._write_json(
        cache_file,
        {
            "schema": plugin.CACHE_SCHEMA,
            "fetched_at": NOW.isoformat(),
            "items": normalize_launches(launch_payload(), now=NOW),
        },
    )

    rows, state, error = plugin._resolve_source(
        "launches",
        NOW,
        force=False,
        fetcher=lambda: (_ for _ in ()).throw(AssertionError("network called")),
        fixture=[],
        ttl_minutes=60,
    )
    assert rows[0]["id"] == "vikram-demo"
    assert state == "fresh_cache"
    assert error == ""

    stale_record = plugin._read_json(cache_file, {})
    stale_record["fetched_at"] = (NOW - timedelta(hours=2)).isoformat()
    plugin._write_json(cache_file, stale_record)
    rows, state, error = plugin._resolve_source(
        "launches",
        NOW,
        force=False,
        fetcher=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        fixture=[],
        ttl_minutes=60,
    )
    assert rows[0]["id"] == "vikram-demo"
    assert state == "stale_cache"
    assert error == "RuntimeError"


def test_payload_keeps_one_live_panel_when_the_other_source_fails(tmp_path, monkeypatch):
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    launches = normalize_launches(launch_payload(), now=NOW)
    markets = normalize_market_events(market_payload(), now=NOW)
    monkeypatch.setattr(
        "plugins.orbital_signal.orbital_signal.fetch_launches",
        lambda now=None: (_ for _ in ()).throw(RuntimeError("launch source down")),
    )
    monkeypatch.setattr(
        "plugins.orbital_signal.orbital_signal.fetch_market_events",
        lambda now=None: markets,
    )

    payload = plugin._payload({}, FakeDeviceConfig(), NOW)

    assert payload["launches"]
    assert payload["markets"] == markets[:3]
    assert payload["status"]["sources"] == {"launches": "fixture", "markets": "live"}
    assert payload["_source_provenance"] == SourceProvenance.LOCAL_FALLBACK.value
    assert launches[0]["id"] == "vikram-demo"


def test_theme_only_render_uses_aggregate_without_network(tmp_path, monkeypatch):
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    fixture = plugin._fixture_payload(NOW)
    plugin._write_json(plugin._cache_dir() / "aggregate.json", fixture)
    monkeypatch.setattr(
        "plugins.orbital_signal.orbital_signal.fetch_launches",
        lambda now=None: (_ for _ in ()).throw(AssertionError("network called")),
    )
    monkeypatch.setattr(
        "plugins.orbital_signal.orbital_signal.fetch_market_events",
        lambda now=None: (_ for _ in ()).throw(AssertionError("network called")),
    )

    payload = plugin._payload({"_theme_render_only": True}, FakeDeviceConfig(), NOW)

    assert payload == fixture


def test_primary_launch_photo_hydrates_on_data_path_and_theme_only_stays_local(
    tmp_path,
    monkeypatch,
):
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    raw_launches = launch_payload()
    raw_launches["results"][0]["image"] = {
        "image_url": "https://images.example/vikram-launch.jpg",
        "credit": "Skyroot Aerospace",
        "license": {"name": "CC BY 4.0"},
    }
    launches = normalize_launches(raw_launches, now=NOW)
    markets = normalize_market_events(market_payload(), now=NOW)
    calls = []

    def acquire(launch, namespace, *, allow_network, session=None):
        calls.append((launch["id"], allow_network, namespace))
        return launch_photo_module.CachedLaunchPhoto(
            Image.new("RGB", (64, 128), (30, 120, 220)),
            "primary-cache-key",
            "Skyroot Aerospace",
            "CC BY 4.0",
            "launch",
        )

    monkeypatch.setattr(launch_photo_module, "load_or_acquire_photo", acquire)
    monkeypatch.setattr(
        "plugins.orbital_signal.orbital_signal.fetch_launches",
        lambda now=None: launches,
    )
    monkeypatch.setattr(
        "plugins.orbital_signal.orbital_signal.fetch_market_events",
        lambda now=None: markets,
    )

    payload = plugin._payload({"forceRefresh": True}, FakeDeviceConfig(), NOW)

    assert [(launch_id, allow_network) for launch_id, allow_network, _ in calls] == [
        ("vikram-demo", True)
    ]
    assert payload["launches"][0]["photo_cache_key"] == "primary-cache-key"
    assert payload["launches"][0]["photo_credit"] == "Skyroot Aerospace"
    assert payload["launches"][0]["photo_license"] == "CC BY 4.0"
    assert payload["launches"][0]["photo_source"] == "launch"
    assert "photo_cache_key" not in payload["launches"][1]

    monkeypatch.setattr(
        launch_photo_module,
        "load_or_acquire_photo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )
    theme_payload = plugin._payload(
        {"_theme_render_only": True},
        FakeDeviceConfig(),
        NOW + timedelta(minutes=5),
    )

    assert theme_payload == payload


def test_countdown_and_fixed_800x480_panel_geometry_match_mockup():
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    launch_time = NOW + timedelta(hours=8, minutes=40)

    assert plugin._format_countdown(NOW, launch_time) == "T- 08H 40M"
    assert plugin._panel_boxes(800, 480) == {
        "header": (0, 0, 800, 58),
        "launch": (0, 58, 440, 448),
        "markets": (440, 58, 800, 448),
        "footer": (0, 448, 800, 480),
    }


def test_cached_launch_photo_renders_in_color_for_day_and_night(tmp_path):
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    photo_key = "render-photo"
    plugin._photo_namespace().put_bytes(
        photo_key,
        _png_bytes(color=(20, 170, 230), size=(180, 320)),
        suffix=".png",
    )
    payload = plugin._fixture_payload(NOW)
    payload["launches"][0].update(
        {
            "photo_cache_key": photo_key,
            "photo_credit": "Skyroot Aerospace",
            "photo_license": "CC BY 4.0",
            "photo_source": "launch",
        }
    )

    day = plugin._render_page((800, 480), payload, {}, NOW)
    night = plugin._render_page((800, 480), payload, {"themeMode": "night"}, NOW)

    assert day.getpixel((60, 130)) == (20, 170, 230)
    assert night.getpixel((60, 130)) == (20, 170, 230)
    assert day.getpixel((139, 120)) == DAY_PALETTE["rule"]
    assert night.getpixel((139, 120)) == NIGHT_PALETTE["rule"]


def test_launch_photo_credit_is_one_line_and_empty_credit_draws_no_label(tmp_path):
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    photo_key = "credit-photo"
    plugin._photo_namespace().put_bytes(
        photo_key,
        _png_bytes(size=(180, 320)),
        suffix=".png",
    )
    launches = _representative_launches()
    launches[0].update(
        {
            "photo_cache_key": photo_key,
            "photo_credit": "A very long rocket image credit that must fit",
        }
    )
    draw = RecordingDraw()

    plugin._draw_launch(
        draw.image,
        draw,
        (0, 58, 440, 448),
        launches,
        NOW,
        DAY_PALETTE,
        None,
    )

    labels = [call for call in draw.text_calls if call["text"].startswith("PHOTO:")]
    assert len(labels) == 1
    assert labels[0]["bbox"][2] <= 126

    launches[0]["photo_credit"] = ""
    no_credit_draw = RecordingDraw()
    plugin._draw_launch(
        no_credit_draw.image,
        no_credit_draw,
        (0, 58, 440, 448),
        launches,
        NOW,
        DAY_PALETTE,
        None,
    )
    assert not any(call["text"].startswith("PHOTO:") for call in no_credit_draw.text_calls)


def test_missing_launch_photo_cache_keeps_vector_and_all_launch_text(tmp_path):
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    launches = _representative_launches()
    launches[0]["photo_cache_key"] = "missing-photo"
    draw = RecordingDraw()

    plugin._draw_launch(
        draw.image,
        draw,
        (0, 58, 440, 448),
        launches,
        NOW,
        DAY_PALETTE,
        None,
    )

    rendered = {call["text"] for call in draw.text_calls}
    assert "NEXT LAUNCH" in rendered
    assert "VIKRAM-I" in rendered
    assert "DEMO FLIGHT" in rendered
    assert draw.ellipse_calls


def test_img2_orbital_wordmark_is_wide_transparent_and_chroma_free():
    asset_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "orbital_signal"
        / "assets"
        / "orbital-signal-wordmark.png"
    )

    with Image.open(asset_path) as source:
        wordmark = source.convert("RGBA")

    alpha = wordmark.getchannel("A")
    visible_colors = {
        (red, green, blue)
        for red, green, blue, opacity in wordmark.get_flattened_data()
        if opacity > 16
    }
    corners = (
        (0, 0),
        (wordmark.width - 1, 0),
        (0, wordmark.height - 1),
        (wordmark.width - 1, wordmark.height - 1),
    )

    assert wordmark.width / wordmark.height > 6.5
    assert alpha.getextrema() == (0, 255)
    assert all(alpha.getpixel(point) == 0 for point in corners)
    assert visible_colors == {(11, 29, 58), (244, 81, 30)}


def test_header_wordmark_has_fitted_day_and_night_variants():
    plugin = OrbitalSignal({"id": PLUGIN_ID})

    day = plugin._prepare_header_wordmark((247, 34), DAY_PALETTE)
    night = plugin._prepare_header_wordmark((247, 34), NIGHT_PALETTE)

    assert day.mode == "RGBA"
    assert 0 < day.width <= 247
    assert 0 < day.height <= 34
    assert night.size == day.size
    assert any(
        red > 220 and green < 140 and blue < 90 and alpha > 128
        for red, green, blue, alpha in day.get_flattened_data()
    )
    assert any(
        red > 220 and green > 220 and blue > 190 and alpha > 128
        for red, green, blue, alpha in night.get_flattened_data()
    )


def test_rendered_header_uses_img2_wordmark_accent():
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    payload = plugin._fixture_payload(NOW)

    page = plugin._render_page((800, 480), payload, {}, NOW)
    title = page.crop((16, 8, 264, 48))

    assert any(
        red > 220 and green < 140 and blue < 90
        for red, green, blue in title.get_flattened_data()
    )


def test_market_header_keeps_an_optical_gap_before_thermometer():
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    draw = RecordingDraw()

    plugin._draw_markets(draw, (440, 58, 800, 448), [], DAY_PALETTE, None)

    title_box = next(call["bbox"] for call in draw.text_calls if call["text"] == "MARKET HEAT")
    thermometer_left = min(box[0] for box in draw.ellipse_calls)
    assert thermometer_left - title_box[2] >= 8


def test_market_card_keeps_representative_title_and_leader_complete():
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    draw = RecordingDraw()
    title = "SPAIN VS. ARGENTINA - EXACT SCORE?"
    leader = "SPAIN 1 - 1 ARGENTINA"
    market = {
        "title": title,
        "leader": leader,
        "category": "SPORT",
        "probability": 0.16,
        "change_24h": 0,
        "heat": 64,
    }

    plugin._draw_markets(draw, (440, 58, 800, 448), [market], DAY_PALETTE, None)

    rendered = {call["text"] for call in draw.text_calls}
    assert title in rendered
    assert leader in rendered


def _representative_launches():
    return [
        {
            "net": (NOW + timedelta(hours=8)).isoformat(),
            "rocket": "VIKRAM-I",
            "mission": "DEMO FLIGHT",
            "status": "GO",
            "location": "SATISH DHAWAN SPACE CENTRE",
            "provider": "SKYROOT AEROSPACE",
            "orbit": "LEO",
        },
        {
            "net": (NOW + timedelta(days=2)).isoformat(),
            "rocket": "FALCON 9 BLOCK 5",
            "mission": "STARLINK GROUP 17-10",
        },
        {
            "net": (NOW + timedelta(days=3)).isoformat(),
            "rocket": "STARSHIP V3",
            "mission": "FLIGHT 13",
        },
    ]


def test_up_next_rows_keep_representative_names_complete():
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    draw = RecordingDraw()

    plugin._draw_launch(
        draw.image,
        draw,
        (0, 58, 440, 448),
        _representative_launches(),
        NOW,
        DAY_PALETTE,
        None,
    )

    rendered = {call["text"] for call in draw.text_calls}
    assert "FALCON 9 BLOCK 5" in rendered
    assert "STARLINK GROUP 17-10" in rendered
    assert "STARSHIP V3" in rendered
    assert "FLIGHT 13" in rendered


def test_primary_launch_keeps_full_rocket_name_inside_panel():
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    launches = _representative_launches()
    full_name = "LONG MARCH 2C / YUANZHENG-1S"
    launches[0]["rocket"] = full_name
    draw = RecordingDraw()

    plugin._draw_launch(
        draw.image,
        draw,
        (0, 58, 440, 448),
        launches,
        NOW,
        DAY_PALETTE,
        None,
    )

    rocket_call = next(
        call for call in draw.text_calls if call["text"] == full_name
    )
    assert "..." not in rocket_call["text"]
    assert rocket_call["bbox"][2] <= 422


def test_up_next_rows_keep_six_pixel_footer_safety_zone():
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    draw = RecordingDraw()

    plugin._draw_launch(
        draw.image,
        draw,
        (0, 58, 440, 448),
        _representative_launches(),
        NOW,
        DAY_PALETTE,
        None,
    )

    row_text = [call for call in draw.text_calls if call["xy"][1] >= 400]
    assert row_text
    assert max(call["bbox"][3] for call in row_text) <= 442


def test_ui_copy_avoids_glyphs_that_render_as_boxes_on_the_device_font():
    source = inspect.getsource(OrbitalSignal)

    for unsupported in ("·", "—", "…", "−"):
        assert unsupported not in source


def test_rendered_day_and_night_pages_are_full_color_and_display_sized():
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    payload = plugin._fixture_payload(NOW)

    day = plugin._render_page((800, 480), payload, {}, NOW)
    night = plugin._render_page((800, 480), payload, {"themeMode": "night"}, NOW)

    assert day.size == (800, 480)
    assert night.size == (800, 480)
    assert day.mode == "RGB"
    assert night.mode == "RGB"
    assert day.getpixel((5, 80)) == DAY_PALETTE["paper"]
    assert night.getpixel((5, 80)) == NIGHT_PALETTE["paper"]
    assert len(day.getcolors(maxcolors=1_000_000)) >= 6
    assert any(red != green or green != blue for count, (red, green, blue) in night.getcolors(1_000_000))


def test_generate_image_attaches_local_fallback_provenance(monkeypatch):
    plugin = OrbitalSignal({"id": PLUGIN_ID})
    payload = plugin._fixture_payload(NOW)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _config: NOW)
    monkeypatch.setattr(plugin, "_payload", lambda settings, config, now: payload)

    image = plugin.generate_image({}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
