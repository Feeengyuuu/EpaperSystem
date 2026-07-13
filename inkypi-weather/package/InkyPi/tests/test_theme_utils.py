from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.utils import theme_utils


NOON = datetime(2026, 5, 27, 12, 0)


class FakeDeviceConfig:
    def __init__(self, config=None):
        self.config = dict(config or {})

    def get_config(self, key=None, default=None):
        if key is None:
            return self.config
        return self.config.get(key, default)


def _astronomy(
    *,
    date="2026-05-27",
    timezone_name="America/Los_Angeles",
    sunrise="2026-05-27T05:50:00-07:00",
    sunset="2026-05-27T20:15:00-07:00",
):
    return {
        "source": "weather",
        "date": date,
        "timezone": timezone_name,
        "sunrise": sunrise,
        "sunset": sunset,
    }


def _relative_luminance(rgb):
    channels = []
    for channel in rgb:
        value = channel / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first, second):
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("paper", "day"),
        ("light", "day"),
        ("comic", "day"),
        ("dark", "night"),
        ("cinema", "night"),
        ("streaming", "night"),
        ("midnight", "night"),
    ],
)
def test_normalize_theme_mode_accepts_legacy_aliases(raw, expected):
    assert theme_utils.normalize_theme_mode(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "unknown"])
def test_normalize_theme_mode_uses_default_for_missing_or_unknown_values(raw):
    assert theme_utils.normalize_theme_mode(raw, "auto") == "auto"


def test_plugin_forced_mode_overrides_device_auto(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])
    fake_device = FakeDeviceConfig({"timezone": "America/Los_Angeles", "theme_mode": "auto"})

    result = theme_utils.resolve_plugin_theme({"themeMode": "night"}, fake_device, now=NOON)

    assert result["requested_mode"] == "night"
    assert result["mode"] == "night"


def test_plugin_auto_uses_shared_sunrise_sunset(monkeypatch):
    monkeypatch.setattr(
        theme_utils,
        "read_contexts",
        lambda *args, **kwargs: [
            {
                "payload": {
                    "astronomy": {
                        "source": "weather",
                        "date": "2026-05-27",
                        "timezone": "America/Los_Angeles",
                        "sunrise": "2026-05-27T05:50:00-07:00",
                        "sunset": "2026-05-27T20:15:00-07:00",
                    }
                }
            }
        ],
    )
    fake_device = FakeDeviceConfig({"timezone": "America/Los_Angeles"})

    result = theme_utils.resolve_plugin_theme({"themeMode": "auto"}, fake_device, now=NOON)

    assert result["requested_mode"] == "auto"
    assert result["mode"] == "day"
    assert result["source"] == "weather"
    assert result["reason"] == "sunrise/sunset"


def test_plugin_theme_key_precedence(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])
    settings = {
        "themeMode": "night",
        "theme_mode": "day",
        "theme": "day",
        "sportsDashboardTheme": "day",
    }

    result = theme_utils.resolve_plugin_theme(settings, now=NOON)

    assert result["requested_mode"] == "night"
    assert result["mode"] == "night"


@pytest.mark.parametrize("settings", [None, {}, {"themeMode": ""}, {"themeMode": "unknown"}])
def test_missing_empty_or_unknown_plugin_theme_resolves_to_auto(monkeypatch, settings):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    result = theme_utils.resolve_plugin_theme(settings, now=NOON)

    assert result["requested_mode"] == "auto"
    assert result["mode"] == "day"


def test_missing_palette_roles_receive_readable_fallbacks(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    result = theme_utils.resolve_plugin_theme(
        {"themeMode": "night"},
        now=NOON,
        palette={"night": {"background": "#101010", "accent": "#ff8800"}},
    )

    required_roles = {"background", "panel", "ink", "muted", "rule", "accent"}
    assert required_roles <= result["palette"].keys()
    assert result["palette"]["background"] == (16, 16, 16)
    assert result["palette"]["accent"] == (255, 136, 0)
    assert _contrast_ratio(result["palette"]["background"], result["palette"]["ink"]) >= 4.5
    assert result["css"]["background"] == "#101010"
    assert result["css"]["accent"] == "#ff8800"


@pytest.mark.parametrize(
    "malformed_color",
    [
        (float("inf"), 0, 0),
        (12.9, 34.1, 56.8),
        (True, 0, 1),
    ],
    ids=["infinite", "fractional", "boolean"],
)
def test_plugin_theme_rejects_malformed_rgb_channels(monkeypatch, malformed_color):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    result = theme_utils.resolve_plugin_theme(
        {"themeMode": "night"},
        now=NOON,
        palette={"night": {"accent": malformed_color}},
    )

    assert result["palette"]["accent"] == theme_utils.NIGHT_PALETTE["accent"]


def test_plugin_theme_result_always_contains_canonical_contract(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    result = theme_utils.resolve_plugin_theme(now=NOON)

    assert {"mode", "requested_mode", "palette", "css", "source", "reason"} <= result.keys()


def test_theme_context_uses_day_fallback_without_weather(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 12, 0),
    )

    assert context["mode"] == "day"
    assert context["source"] == "fallback"
    assert context["palette"]["background"] == (255, 255, 255)


def test_theme_context_uses_night_fallback_without_weather(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 22, 0),
    )

    assert context["mode"] == "night"
    assert context["source"] == "fallback"
    assert context["palette"]["background"] == (0, 0, 0)


def test_theme_context_uses_weather_sunrise_sunset(monkeypatch):
    def fake_contexts(*args, **kwargs):
        return [
            {
                "payload": {
                    "astronomy": {
                        "source": "weather",
                        "date": "2026-05-27",
                        "timezone": "America/Los_Angeles",
                        "sunrise": "2026-05-27T05:50:00-07:00",
                        "sunset": "2026-05-27T20:15:00-07:00",
                    }
                }
            }
        ]

    monkeypatch.setattr(theme_utils, "read_contexts", fake_contexts)

    day = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 12, 0),
    )
    night = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 21, 0),
    )

    assert day["mode"] == "day"
    assert day["source"] == "weather"
    assert night["mode"] == "night"
    assert night["source"] == "weather"


def test_theme_context_forced_mode_skips_weather(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("weather context should not be read for forced mode")

    monkeypatch.setattr(theme_utils, "read_contexts", fail_if_called)

    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles", "display_theme_mode": "dark"}),
        now=datetime(2026, 5, 27, 12, 0),
    )

    assert context["mode"] == "night"
    assert context["source"] == "config"


def test_apply_theme_to_plugin_settings_overrides_render_colors():
    theme = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles", "theme_mode": "light"}),
        now=datetime(2026, 5, 27, 22, 0),
    )

    settings = theme_utils.apply_theme_to_plugin_settings({"displayGraph": "true"}, theme)

    assert settings["displayGraph"] == "true"
    assert settings["backgroundOption"] == "color"
    assert settings["backgroundColor"] == "#ffffff"
    assert settings["textColor"] == "#0a0c0f"


@pytest.mark.parametrize(
    ("now", "expected_mode"),
    [
        (datetime(2026, 5, 27, 5, 49, 59, 999999), "night"),
        (datetime(2026, 5, 27, 5, 50, 0), "day"),
        (datetime(2026, 5, 27, 20, 14, 59, 999999), "day"),
        (datetime(2026, 5, 27, 20, 15, 0), "night"),
    ],
    ids=[
        "one-microsecond-before-sunrise",
        "exact-sunrise",
        "one-microsecond-before-sunset",
        "exact-sunset",
    ],
)
def test_auto_uses_exact_half_open_sun_interval(now, expected_mode):
    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "UTC"}),
        now=now.replace(tzinfo=timezone(timedelta(hours=-7))),
        astronomy=_astronomy(),
    )

    assert context["mode"] == expected_mode
    assert {
        key: context[key]
        for key in ("source", "date", "timezone", "sunrise", "sunset")
    } == _astronomy()


def test_explicit_astronomy_uses_location_timezone_not_device_timezone():
    astronomy = _astronomy(
        date="2026-07-12",
        timezone_name="Asia/Tokyo",
        sunrise="2026-07-12T04:35:00+09:00",
        sunset="2026-07-12T18:58:00+09:00",
    )

    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 7, 11, 20, 0, tzinfo=timezone(timedelta(hours=-7))),
        astronomy=astronomy,
    )

    assert context["mode"] == "day"
    assert context["date"] == "2026-07-12"
    assert context["timezone"] == "Asia/Tokyo"
    assert context["sunrise"] == astronomy["sunrise"]
    assert context["sunset"] == astronomy["sunset"]


@pytest.mark.parametrize(
    ("now", "astronomy", "expected_offset"),
    [
        (
            datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
            _astronomy(
                date="2026-01-15",
                timezone_name="America/New_York",
                sunrise="2026-01-15T07:17:00-05:00",
                sunset="2026-01-15T16:53:00-05:00",
            ),
            "-05:00",
        ),
        (
            datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
            _astronomy(
                date="2026-07-15",
                timezone_name="America/New_York",
                sunrise="2026-07-15T05:38:00-04:00",
                sunset="2026-07-15T20:25:00-04:00",
            ),
            "-04:00",
        ),
    ],
)
def test_dst_offsets_are_preserved_for_each_location_date(
    now,
    astronomy,
    expected_offset,
):
    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "UTC"}),
        now=now,
        astronomy=astronomy,
    )

    assert context["timezone"] == "America/New_York"
    assert context["sunrise"].endswith(expected_offset)
    assert context["sunset"].endswith(expected_offset)


def test_stale_context_is_rejected_without_date_remap(monkeypatch):
    calls = []

    def fake_contexts(*args, **kwargs):
        calls.append((args, kwargs))
        return [{"stale": True, "payload": {"astronomy": _astronomy()}}]

    monkeypatch.setattr(theme_utils, "read_contexts", fake_contexts)

    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 12, 0),
    )

    assert calls[0][1]["include_stale"] is False
    assert context["source"] == "fallback"
    assert context["sunrise"] is None
    assert context["sunset"] is None


def test_yesterday_context_is_rejected_instead_of_moved_to_today(monkeypatch):
    yesterday = _astronomy(
        date="2026-05-26",
        sunrise="2026-05-26T05:51:00-07:00",
        sunset="2026-05-26T20:14:00-07:00",
    )
    monkeypatch.setattr(
        theme_utils,
        "read_contexts",
        lambda *_args, **_kwargs: [
            {"stale": False, "payload": {"astronomy": yesterday}}
        ],
    )

    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 12, 0),
    )

    assert context["source"] == "fallback"
    assert context["date"] == "2026-05-27"
    assert context["sunrise"] is None
    assert context["sunset"] is None


@pytest.mark.parametrize(
    "astronomy",
    [
        _astronomy(sunrise="not-a-time"),
        _astronomy(
            sunrise="2026-05-27T20:15:00-07:00",
            sunset="2026-05-27T05:50:00-07:00",
        ),
        _astronomy(timezone_name="PDT"),
    ],
    ids=["malformed", "inverted", "invalid-timezone"],
)
def test_invalid_or_inverted_sun_pair_uses_fallback(astronomy):
    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 12, 0),
        astronomy=astronomy,
    )

    assert context["source"] == "fallback"
    assert context["sunrise"] is None
    assert context["sunset"] is None


@pytest.mark.parametrize(
    ("timezone_name", "offset"),
    [("EST", "-05:00"), ("CET", "+02:00")],
)
def test_canonical_weather_astronomy_rejects_timezone_abbreviations(
    timezone_name,
    offset,
):
    astronomy = _astronomy(
        timezone_name=timezone_name,
        sunrise=f"2026-05-27T05:50:00{offset}",
        sunset=f"2026-05-27T20:15:00{offset}",
    )

    assert theme_utils.canonical_weather_astronomy(
        astronomy,
        now=datetime.fromisoformat(f"2026-05-27T12:00:00{offset}"),
    ) is None


@pytest.mark.parametrize("timezone_name", ["UTC", "Etc/UTC"])
def test_canonical_weather_astronomy_allows_explicit_utc_iana_names(
    timezone_name,
):
    astronomy = _astronomy(
        timezone_name=timezone_name,
        sunrise="2026-05-27T05:50:00+00:00",
        sunset="2026-05-27T20:15:00+00:00",
    )

    assert theme_utils.canonical_weather_astronomy(
        astronomy,
        now=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
    ) == astronomy


def test_polar_missing_sun_pair_uses_fallback_with_null_endpoints():
    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 12, 0),
        astronomy={**_astronomy(), "sunrise": None, "sunset": None},
    )

    assert context["mode"] == "day"
    assert context["source"] == "fallback"
    assert context["sunrise"] is None
    assert context["sunset"] is None


def test_explicit_astronomy_resolution_does_not_read_context_or_provider(
    monkeypatch,
):
    monkeypatch.setattr(
        theme_utils,
        "read_contexts",
        lambda *_args, **_kwargs: pytest.fail("explicit astronomy read context"),
    )

    result = theme_utils.resolve_plugin_theme(
        {"themeMode": "auto"},
        FakeDeviceConfig({"timezone": "UTC"}),
        now=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
        astronomy=_astronomy(),
    )

    assert result["source"] == "weather"
    assert result["timezone"] == "America/Los_Angeles"


def test_pinned_context_is_seen_by_nested_plugin_theme_reads(monkeypatch):
    monkeypatch.setattr(
        theme_utils,
        "read_contexts",
        lambda *_args, **_kwargs: pytest.fail("nested read escaped pin"),
    )
    pinned = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "UTC"}),
        now=datetime(2026, 5, 27, 22, 0, tzinfo=timezone.utc),
        astronomy=_astronomy(),
    )

    with theme_utils.pinned_theme_context(pinned):
        nested = theme_utils.resolve_plugin_theme(
            {"themeMode": "auto"},
            FakeDeviceConfig({"timezone": "UTC"}),
        )

    assert nested["mode"] == pinned["mode"]
    assert nested["timezone"] == pinned["timezone"]
    assert nested is not pinned


def test_pinned_context_resets_after_render_and_does_not_cross_threads(
    monkeypatch,
):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *_args, **_kwargs: [])
    device = FakeDeviceConfig({"timezone": "UTC"})
    pinned = theme_utils.get_theme_context(
        device,
        now=datetime(2026, 5, 27, 22, 0, tzinfo=timezone.utc),
        astronomy=_astronomy(),
    )

    with theme_utils.pinned_theme_context(pinned):
        inside = theme_utils.get_theme_context(device)
        with ThreadPoolExecutor(max_workers=1) as pool:
            cross_thread = pool.submit(
                theme_utils.get_theme_context,
                device,
                datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
            ).result()
    after = theme_utils.get_theme_context(
        device,
        now=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
    )

    assert inside["source"] == "weather"
    assert cross_thread["source"] == "fallback"
    assert after["source"] == "fallback"
