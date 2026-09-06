from datetime import timedelta
from pathlib import Path
import pytest
from runtime.refresh_contracts import TaskCancelled

from tests.test_box_office_poster_cache_recovery import chart, source_facts, assert_valid_posters


def test_generation_uses_durable_posters_and_recovers_legacy_cover_without_network(chart, monkeypatch, tmp_path):
    before = chart.seed(count=1)
    seeded = Path(before['movies'][0]['poster_path'])
    old = tmp_path / 'posters' / '6181741cf046f75c3f.jpg'
    old.parent.mkdir(exist_ok=True)
    old.write_bytes(seeded.read_bytes())
    seeded.unlink()
    before['movies'][0]['poster_path'] = str(old)
    chart.plugin._write_cache(before)
    monkeypatch.setenv('INKYPI_DATA_DIR', str(tmp_path / 'durable'))
    image = chart.generate()
    after = chart.cache()
    retained = Path(after['movies'][0]['poster_path'])
    assert retained.is_relative_to(tmp_path / 'durable')
    assert retained != old and old.exists()
    assert not chart.calls
    assert source_facts(after) == source_facts(before)
    old.unlink()
    chart.generate()
    assert_valid_posters(chart.cache())
    assert not chart.calls
    assert image.info['inkypi_media_ready'] == 1


def test_missing_posters_request_bounded_background_repair_then_stop(chart):
    chart.durations.update(chart=4.0, search=3.5, poster=3.5)
    first_image = chart.generate(forceRefresh=True)
    first = chart.cache()
    assert 0 < first_image.info['inkypi_media_ready'] < 5
    assert chart.plugin.get_live_refresh_state(chart.settings, chart.now) is None
    for _ in range(4):
        chart.now += timedelta(minutes=30)
        if not chart.plugin.get_live_refresh_state(chart.settings, chart.now):
            break
        call_start = len(chart.calls)
        image = chart.generate()
        assert source_facts(chart.cache()) == source_facts(first)
        assert not any(kind == 'chart' for kind, _, _ in chart.calls[call_start:])
    assert_valid_posters(chart.cache())
    assert chart.plugin.get_live_refresh_state(chart.settings, chart.now + timedelta(minutes=30)) is None
    assert image.info['inkypi_media_ready'] == 5


def test_queued_media_repair_expiring_before_execution_never_fetches_new_chart(chart):
    chart.durations.update(chart=4.0, search=3.5, poster=3.5)
    chart.generate(forceRefresh=True)
    before = chart.cache()
    chart.now += timedelta(hours=5, minutes=59)
    assert chart.plugin.get_live_refresh_state(chart.settings, chart.now)
    chart.now += timedelta(minutes=2)
    call_start = len(chart.calls)
    with pytest.raises(TaskCancelled, match='media repair'):
        chart.generate(_movie_media_only=True)
    assert not chart.calls[call_start:]
    assert chart.cache() == before


def test_media_cleanup_failure_keeps_valid_chart_and_posters(chart, monkeypatch):
    from plugins.box_office_top_movies.poster_store import PosterStore
    chart.seed(count=1)
    monkeypatch.setattr(PosterStore, 'cleanup', lambda *_a, **_k: (_ for _ in ()).throw(PermissionError('fixture')))
    image = chart.generate()
    assert image.info['inkypi_media_ready'] == 1
    assert not chart.calls
