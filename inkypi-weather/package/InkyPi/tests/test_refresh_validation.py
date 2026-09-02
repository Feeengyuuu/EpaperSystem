import json

import pytest


@pytest.mark.parametrize(
    "value",
    [
        "{not-json",
        None,
        [],
        {},
        {"refreshType": "unknown"},
        {"refreshType": "interval", "unit": "week", "interval": 1},
        {"refreshType": "interval", "unit": "minute", "interval": 0},
        {"refreshType": "interval", "unit": "minute", "interval": -1},
        {"refreshType": "interval", "unit": "minute", "interval": "abc"},
        {"refreshType": "interval", "unit": "minute", "interval": True},
        {"refreshType": "scheduled", "refreshTime": "24:00"},
        {"refreshType": "scheduled", "refreshTime": "not-a-time"},
    ],
)
def test_refresh_parser_rejects_invalid_config_with_stable_error_payload(value):
    from src.utils.refresh_validation import (
        RefreshValidationError,
        parse_refresh_config,
        validation_error_payload,
    )

    with pytest.raises(RefreshValidationError) as caught:
        parse_refresh_config(value)

    payload = validation_error_payload(caught.value)
    assert payload == {
        "success": False,
        "error_code": caught.value.error_code,
        "error": str(caught.value),
        "message": str(caught.value),
    }


def test_refresh_parser_normalizes_positive_interval_and_preserves_request_fields():
    from src.utils.refresh_validation import parse_refresh_config

    parsed = parse_refresh_config(json.dumps({
        "playlist": "Default",
        "instance_name": "Headlines",
        "refreshType": "interval",
        "unit": "minute",
        "interval": "2",
    }))

    assert parsed.request["playlist"] == "Default"
    assert parsed.request["instance_name"] == "Headlines"
    assert parsed.refresh == {"interval": 120}


def test_refresh_parser_accepts_strict_scheduled_time():
    from src.utils.refresh_validation import parse_refresh_config

    parsed = parse_refresh_config({
        "refreshType": "scheduled",
        "refreshTime": "08:05",
    })

    assert parsed.refresh == {"scheduled": "08:05"}


@pytest.mark.parametrize("refresh,expected", [
    ({"interval": 900}, {"interval": 900}),
    ({"interval": 1200}, {"interval": 1200}),
    ({"interval": 1800}, {"interval": 1800}),
    ({"interval": 2400}, {"interval": 3600}),
    ({"interval": 7200}, {"interval": 3600}),
    ({"scheduled": "08:05"}, {"interval": 3600}),
])
def test_game_calendar_refresh_uses_hourly_or_faster_divisor(refresh, expected):
    from utils.refresh_validation import normalize_plugin_refresh
    assert normalize_plugin_refresh("simple_calendar", {"showGameEvents": "true"}, refresh) == expected
    assert normalize_plugin_refresh("simple_calendar", {}, refresh) == refresh
    assert normalize_plugin_refresh("weather", {"showGameEvents": "true"}, refresh) == refresh
