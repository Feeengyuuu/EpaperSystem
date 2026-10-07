from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.sports_dashboard.common as sports_common  # noqa: E402
import plugins.stocktracker.stocktracker as stocktracker_module  # noqa: E402
import plugins.weather.weather as weather_module  # noqa: E402
from plugins.mini_weather.mini_weather import MiniWeather  # noqa: E402
from plugins.sports_dashboard.common import SportsDashboardCommonMixin  # noqa: E402
from plugins.stocktracker.stocktracker import StockTracker  # noqa: E402


def test_mini_weather_can_publish_under_its_own_plugin_identity(monkeypatch):
    captured = {}

    def capture(plugin_id, payload, **metadata):
        captured.update(plugin_id=plugin_id, payload=payload, metadata=metadata)
        return True

    monkeypatch.setattr(weather_module, "write_context", capture)
    plugin = MiniWeather({"id": "mini_weather"})
    generated_at = datetime(2026, 8, 2, 18, 30, tzinfo=timezone.utc)

    assert (
        plugin._write_weather_context(
            {
                "title": "Los Angeles",
                "current_temperature": 72,
                "temperature_unit": "°F",
                "feels_like": 71,
                "forecast": [{"day": "Sun", "high": 80, "low": 65}],
                "data_points": [],
            },
            generated_at,
            plugin_id="mini_weather",
        )
        is True
    )

    assert captured["plugin_id"] == "mini_weather"
    assert captured["payload"]["kind"] == "weather"


class _History:
    empty = False
    index = ("2026-08-01", "2026-08-02")

    class _Loc:
        def __getitem__(self, key):
            date, column = key
            assert column == "Close"
            return {"2026-08-01": 100.0, "2026-08-02": 110.0}[date]

    loc = _Loc()


def test_stocktracker_publishes_json_safe_native_context(monkeypatch):
    captured = {}

    def capture(plugin_id, payload, **metadata):
        captured.update(plugin_id=plugin_id, payload=payload, metadata=metadata)
        return True

    monkeypatch.setattr(stocktracker_module, "write_context", capture)
    plugin = StockTracker({"id": "stocktracker"})
    generated_at = datetime(2026, 8, 2, 18, 30, tzinfo=timezone.utc)
    stock_data = [
        {
            "symbol": "AAPL",
            "name": "Apple",
            "price": 110.0,
            "change": 10.0,
            "change_percent": 10.0,
            "shares": 2.0,
            "total_value": 220.0,
            "total_change": 20.0,
            "history": _History(),
            "quote_source": "massive_daily",
        },
        {
            "symbol": "CASH",
            "name": "Cash",
            "price": 1.0,
            "change": 0.0,
            "change_percent": 0.0,
            "shares": 25.5,
            "total_value": 25.5,
            "total_change": 0.0,
            "is_cash": True,
        },
    ]

    assert (
        plugin._write_stock_context(
            stock_data,
            generated_at,
            {"account_value": 250.75, "currency": "USD", "buying_power": 20.25},
        )
        is True
    )

    assert captured["plugin_id"] == "stocktracker"
    assert captured["metadata"] == {
        "generated_at": generated_at,
        "ttl_seconds": 2 * 60 * 60,
    }
    assert captured["payload"]["kind"] == "stock_portfolio"
    assert captured["payload"]["portfolio"] == {
        "value": 250.75,
        "change": 20.0,
        "change_percent": round(20.0 / 230.75 * 100, 4),
        "currency": "USD",
    }
    assert captured["payload"]["items"][0] == {
        "symbol": "AAPL",
        "name": "Apple",
        "price": 110.0,
        "change": 10.0,
        "change_percent": 10.0,
        "shares": 2.0,
        "total_value": 220.0,
        "quote_source": "massive_daily",
        "is_cash": False,
    }
    assert "history" not in captured["payload"]["items"][0]
    assert "buying_power" not in str(captured["payload"])


class _SportsContextProbe(SportsDashboardCommonMixin):
    def __init__(self, states):
        self._states = states

    def _sports_native_state_sources(self):
        return tuple((name, Path(name)) for name in self._states)

    def _read_json_file(self, path):
        return self._states[path.name]


def test_sports_dashboard_context_uses_explicit_safe_event_fields(monkeypatch):
    captured = {}

    def capture(plugin_id, payload, **metadata):
        captured.update(plugin_id=plugin_id, payload=payload, metadata=metadata)
        return True

    monkeypatch.setattr(sports_common, "write_context", capture)
    generated_at = datetime(2026, 8, 2, 18, 30, tzinfo=timezone.utc)
    plugin = _SportsContextProbe(
        {
            "worldcup": {
                "updated_at": "2026-08-02T18:29:00+00:00",
                "source_state": "ESPN CACHE",
                "has_live": True,
                "team_a": "USA",
                "team_b": "Japan",
                "score": "1-0",
                "status": "Second half",
                "started_at": "2026-08-02T18:00:00+00:00",
                "provider": "ESPN",
                "source_url": "https://example.invalid/?token=must-not-leak",
                "api_key": "must-not-leak",
            },
            "nba": {
                "updated_at": "2026-08-02T18:28:00+00:00",
                "source_state": "NBA STALE",
                "has_live": False,
                "team_a": "LAL",
                "team_b": "GSW",
                "score_a": 99,
                "score_b": 101,
                "status_text": "Final",
                "internal_path": "C:/private/cache.json",
            },
        }
    )

    assert plugin._write_sports_dashboard_context(generated_at) is True

    assert captured["plugin_id"] == "sports_dashboard"
    assert captured["metadata"] == {
        "generated_at": generated_at,
        "ttl_seconds": 15 * 60,
    }
    assert captured["payload"]["kind"] == "sports_dashboard"
    assert captured["payload"]["live_count"] == 1
    assert captured["payload"]["events"] == [
        {
            "section": "worldcup",
            "source_state": "ESPN CACHE",
            "has_live": True,
            "team_a": "USA",
            "team_b": "Japan",
            "score": "1-0",
            "status": "Second half",
            "started_at": "2026-08-02T18:00:00+00:00",
            "provider": "ESPN",
            "updated_at": "2026-08-02T18:29:00+00:00",
        },
        {
            "section": "nba",
            "source_state": "NBA STALE",
            "has_live": False,
            "team_a": "LAL",
            "team_b": "GSW",
            "score_a": 99,
            "score_b": 101,
            "status_text": "Final",
            "updated_at": "2026-08-02T18:28:00+00:00",
        },
    ]
    serialized = str(captured["payload"])
    assert "must-not-leak" not in serialized
    assert "internal_path" not in serialized
