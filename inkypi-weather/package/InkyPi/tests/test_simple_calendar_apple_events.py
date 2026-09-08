"""Apple data participates in actual calendar rendering and cached presentation."""
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import ImageDraw
import pytest

from plugins.base_plugin.render_provenance import read_source_provenance, SourceProvenance
from plugins.simple_calendar.apple_events import AppleEventProvider
from plugins.simple_calendar.game_events import GameEventProvider
from plugins.simple_calendar.simple_calendar import SimpleCalendar
from tests.test_apple_events import NOW, SETTINGS, Web
from tests.test_game_events import Web as GameWeb
from tests.test_simple_calendar_holidays import PresentationDeviceConfig, _presentation_request, _calendar_theme


@pytest.fixture(autouse=True)
def freeze_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr("plugins.simple_calendar.game_events.datetime", Clock)


@pytest.mark.parametrize("apple_failure", [False, True])
def test_apple_renders_with_personal_and_optional_game_calendar(tmp_path, monkeypatch, apple_failure):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    web = Web()
    provider = AppleEventProvider(fetch_text=web)
    if apple_failure:
        provider.refresh(SETTINGS, now=NOW - timedelta(hours=3))
        web.failure = True
    games = GameWeb()
    games.day = 9
    game_provider = GameEventProvider(fetch_text=games)
    directory = tmp_path / "plugins/simple_calendar/calendars"
    directory.mkdir(parents=True)
    personal = directory / "personal.ics"
    personal.write_text("BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:personal\nSUMMARY:Dentist\nDTSTART:20260910T120000Z\nEND:VEVENT\nEND:VCALENDAR", encoding="utf-8")
    settings = dict(SETTINGS, customDate="2026-09-08", showGameEvents="false", showHolidays="off",
                    holidayPreset="off", showPersonalCalendars="true", weatherPanelBackground="false", dateHeroOverlays="false")
    settings.update({"personalCalendarURLs[]": [personal.as_uri()], "personalCalendarLabels[]": ["CAL"], "personalCalendarColors[]": ["#2e7d32"]})
    if apple_failure:
        settings.update({"showGameEvents": "true", "gameEventSeries[]": ["state_of_play"], "allowGameEventMedia": "false"})
    texts = []
    original = ImageDraw.ImageDraw.text
    def record(self, xy, text, *args, **kwargs):
        texts.append(str(text))
        return original(self, xy, text, *args, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    plugin = SimpleCalendar({"id": "simple_calendar"}, apple_event_provider=provider, game_event_provider=game_provider)
    image = plugin.generate_image(settings, PresentationDeviceConfig())
    assert image.size == (800, 480)
    assert any("苹果发布会" in text for text in texts)
    assert any("Dentist" in text for text in texts)
    assert provider.path.exists()
    if apple_failure:
        assert any("State of Play" in text for text in texts)
        assert any("待核验" in text for text in texts)
        assert read_source_provenance(image) == SourceProvenance.STALE_CACHE
        assert game_provider.path.exists() and game_provider.path != provider.path
    else:
        assert not game_provider.path.exists()


def test_prepared_presentation_and_settings_read_apple_cache_without_io(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    web = Web()
    provider = AppleEventProvider(fetch_text=web)
    settings = dict(SETTINGS, customDate="2026-09-08", showGameEvents="false", showHolidays="off",
                    holidayPreset="off", showPersonalCalendars="false", weatherPanelBackground="false", dateHeroOverlays="false")
    plugin = SimpleCalendar({"id": "simple_calendar"}, apple_event_provider=provider)
    plugin.generate_image(settings, PresentationDeviceConfig())
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    web.calls.clear()
    result = plugin.prepare_presentation(settings, PresentationDeviceConfig(), request=_presentation_request(NOW), resolved_theme_context=_calendar_theme("night"))
    assert result.image.size == (800, 480)
    params = plugin.generate_settings_template()
    env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "src/plugins"), autoescape=select_autoescape())
    template = env.get_template(params.pop("settings_template"))
    html = template.render(plugin_settings=settings, **params)
    soup = BeautifulSoup(html, "html.parser")
    assert soup.select_one('#showAppleEvents') is not None
    assert "查看苹果官方活动页" in html and "2026-09-08T08:00:00+00:00" in html
    assert len(soup.select('input[type="checkbox"][name="gameEventSeries[]"]')) == 6
    assert not BeautifulSoup(template.render(**params), "html.parser").select_one('#showAppleEvents').has_attr('checked')
    assert web.calls == [] and before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
