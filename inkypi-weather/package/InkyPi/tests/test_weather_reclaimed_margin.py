"""Admission consumes the resource state after its own memory maintenance."""

from datetime import datetime, timezone

import pytest

from tests.test_refresh_task import ResourceSample, _weather_margin_runtime


def test_weather_starts_in_same_turn_when_maintenance_restores_margin(monkeypatch):
    now = datetime(2026, 9, 5, 14, 0, tzinfo=timezone.utc)
    task, _clock, weather, _ordinary = _weather_margin_runtime(
        'weather-reclaimed-margin', now, ordinary_due=False,
    )
    sample = [ResourceSample(146, 20)]
    monkeypatch.setattr(task, '_resource_sample', lambda: sample[0])

    def reclaim(*_args, **_kwargs):
        sample[0] = ResourceSample(160, 20)

    monkeypatch.setattr(task, '_run_memory_maintenance', reclaim)
    command = task._select_independent_refresh_command(now)
    assert command is not None and command.instance_uuid == weather.instance_uuid
    assert command.payload.get('weather_liveness_concession') is not True
    assert task._weather_liveness_window is None


@pytest.mark.parametrize('after', [ResourceSample(130, 20), ResourceSample(160, None), ResourceSample(None, None)])
def test_weather_does_not_reserve_a_quiet_window_after_margin_is_lost(monkeypatch, after):
    now = datetime(2026, 9, 5, 14, 0, tzinfo=timezone.utc)
    task, _clock, _weather, _ordinary = _weather_margin_runtime(
        'weather-reclaim-lost-margin', now, ordinary_due=False,
    )
    sample = [ResourceSample(146, 20)]
    monkeypatch.setattr(task, '_resource_sample', lambda: sample[0])
    monkeypatch.setattr(task, '_run_memory_maintenance', lambda *_a, **_k: sample.__setitem__(0, after))
    assert task._select_independent_refresh_command(now) is None
    assert task._weather_liveness_window is None
