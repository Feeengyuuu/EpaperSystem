"""Mandatory source failures stop optional work and retain useful diagnostics."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.apod.space_weather import (
    KP_ENDPOINT, SCALES_ENDPOINT, SpaceWeatherRepository, refresh_space_weather,
    SourceResult, require_current_core,
)


def test_strict_core_admission_stops_optional_requests_and_reports_source(tmp_path):
    calls = []
    kp = (Path(__file__).parent / 'fixtures/apod/noaa_kp_objects.json').read_bytes()

    class Http:
        def request_bytes(self, method, endpoint, **kwargs):
            calls.append(endpoint)
            if endpoint == SCALES_ENDPOINT:
                return SimpleNamespace(data=json.dumps({'bad': 'shape'}).encode())
            if endpoint == KP_ENDPOINT:
                return SimpleNamespace(data=kp)
            raise AssertionError('Optional source requested after mandatory failure')

    repository = SpaceWeatherRepository(cache_dir=tmp_path, http=Http())
    with pytest.raises(RuntimeError, match='core admission') as failure:
        refresh_space_weather(
            repository, nasa_api_key='test-key',
            now_utc=datetime(2026, 7, 22, 12, 20, tzinfo=timezone.utc),
            context=None, require_live_core=True,
        )
    assert calls == [SCALES_ENDPOINT, KP_ENDPOINT]
    assert 'scales' in str(failure.value)
    assert 'unavailable' in str(failure.value)
    assert 'key -1 is missing or invalid' in str(failure.value)
    assert 'test-key' not in str(failure.value)


def test_core_failure_details_are_bounded_and_redacted():
    failure = SourceResult(
        name='scales', state='live', envelope=None,
        error='request failed https://example.test/?api_key=hidden-secret\n' + 'x' * 1000,
    )
    with pytest.raises(RuntimeError) as error:
        require_current_core(failure)
    text = str(error.value)
    assert 'state=live' in text
    assert 'hidden-secret' not in text
    assert '\n' not in text
    assert len(text) < 400


@pytest.mark.parametrize('state', ['fresh_cache', 'stale_cache', 'unavailable'])
def test_cached_core_never_passes_current_cycle_admission(state):
    with pytest.raises(RuntimeError, match='scales'):
        require_current_core(SourceResult(name='scales', state=state, envelope=None))


def test_live_core_preserves_optional_error_contract_for_snapshot_adapters():
    require_current_core(SimpleNamespace(state='live'), SimpleNamespace(state='live'))
