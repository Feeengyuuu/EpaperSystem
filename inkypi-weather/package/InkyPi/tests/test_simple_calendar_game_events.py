"""Game data joins real personal-calendar rendering without presentation IO."""
from datetime import datetime, timedelta

from PIL import Image, ImageDraw
import pytest

from plugins.base_plugin.render_provenance import read_source_provenance, SourceProvenance
from plugins.simple_calendar.game_events import GameEventProvider
from plugins.simple_calendar.simple_calendar import SimpleCalendar
from tests.test_game_events import Web, SETTINGS, NOW
from tests.test_simple_calendar_holidays import PresentationDeviceConfig, _presentation_request, _calendar_theme


@pytest.fixture(autouse=True)
def freeze_game_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr("plugins.simple_calendar.game_events.datetime", Clock)


def test_failed_game_source_does_not_prevent_new_personal_event_render(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    web = Web()
    provider = GameEventProvider(tmp_path / "games", fetch_text=web)
    provider.refresh(SETTINGS, now=NOW - timedelta(hours=4))
    web.failure = True
    provider.refresh(SETTINGS, now=NOW)
    calendar_dir = tmp_path / "plugins/simple_calendar/calendars"
    calendar_dir.mkdir(parents=True)
    personal = calendar_dir / "local.ics"
    personal.write_text("BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:meeting\nSUMMARY:New meeting\nDTSTART:20260903T120000Z\nEND:VEVENT\nEND:VCALENDAR\n", encoding="utf-8")
    settings = dict(SETTINGS, customDate="2026-09-02", showHolidays="off", holidayPreset="off", showPersonalCalendars="true", weatherPanelBackground="false", dateHeroOverlays="false")
    settings.update({"personalCalendarURLs[]": [personal.as_uri()], "personalCalendarLabels[]": ["CAL"], "personalCalendarColors[]": ["#2e7d32"]})
    texts = []
    original_text = ImageDraw.ImageDraw.text
    def record_text(self, xy, text, *args, **kwargs):
        texts.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    plugin = SimpleCalendar({"id": "simple_calendar"}, game_event_provider=provider)
    image = plugin.generate_image(settings, PresentationDeviceConfig())
    assert image.size == (800, 480)
    assert any("New meeting" in text for text in texts)
    assert any("待核验" in text for text in texts)
    assert read_source_provenance(image) == SourceProvenance.STALE_CACHE

    # Exercise the real DATA cache commit as well as rendering: stale game data
    # must not prevent a newly fetched personal event from reaching the PNG.
    from tests.test_refresh_task import _make_runtime_task, _runtime_playlist, _runtime_plugin_data
    from runtime.refresh_contracts import CommandKind, CommandSource, RefreshIntent
    from refresh_task import _image_allows_cache
    assert _image_allows_cache(image)
    playlist = _runtime_playlist(_runtime_plugin_data("simple_calendar", latest_refresh_time=NOW.isoformat()))
    task, _, _ = _make_runtime_task(tmp_path / "runtime", playlists=[playlist])
    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(playlist.name, instance, source=CommandSource.BACKGROUND,
                                    intent=RefreshIntent.DATA_REFRESH, display_cached_only=False,
                                    kind=CommandKind.CACHE_REFRESH, current_dt=NOW)
    resolved = task._resolve_playlist_command(command)
    task._set_render_metadata(True, True, {})
    task._commit_command_result(command, resolved, image, NOW)
    with Image.open(task._snapshot_cache_path(instance)) as saved:
        assert saved.convert("RGB").tobytes() == image.convert("RGB").tobytes()
    assert task.runtime_state.snapshot().instances[instance.instance_uuid].data.last_failure_at == NOW.isoformat()


def test_prepared_presentation_replays_games_without_network_or_state_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    web = Web()
    provider = GameEventProvider(tmp_path / "games", fetch_text=web)
    settings = dict(SETTINGS, customDate="2026-09-02", showHolidays="off", holidayPreset="off", showPersonalCalendars="false", weatherPanelBackground="false", dateHeroOverlays="false")
    plugin = SimpleCalendar({"id": "simple_calendar"}, game_event_provider=provider)
    provider.refresh(SETTINGS, now=NOW - timedelta(hours=4))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    web.calls.clear()
    result = plugin.prepare_presentation(settings, PresentationDeviceConfig(), request=_presentation_request(NOW), resolved_theme_context=_calendar_theme("day"))
    assert result.image.size == (800, 480)
    assert read_source_provenance(result.image) == SourceProvenance.STALE_CACHE
    assert web.calls == []
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


def test_settings_template_shows_six_series_and_cached_diagnostics(tmp_path):
    from bs4 import BeautifulSoup
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from pathlib import Path
    web = Web()
    provider = GameEventProvider(tmp_path, fetch_text=web)
    provider.refresh(SETTINGS, now=NOW)
    plugin = SimpleCalendar({"id": "simple_calendar"}, game_event_provider=provider)
    params = plugin.generate_settings_template()
    environment = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "src/plugins"), autoescape=select_autoescape())
    template = environment.get_template(params.pop("settings_template"))
    before = provider.path.read_bytes()
    web.calls.clear()
    html = template.render(plugin_settings=SETTINGS, **params)
    soup = BeautifulSoup(html, "html.parser")
    assert len(soup.select('input[type="checkbox"][name="gameEventSeries[]"]')) == 6
    assert "2026-09-02T12:00:00+00:00" in html
    assert "查看公告" in html
    assert web.calls == [] and provider.path.read_bytes() == before
    defaults = BeautifulSoup(template.render(**params), "html.parser")
    assert not defaults.select_one('#showGameEvents').has_attr('checked')
