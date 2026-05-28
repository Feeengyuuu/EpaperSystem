import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.comic import comic as comic_module


class DeviceConfig:
    def get_config(self, key, default=None):
        if key == "timezone":
            return "America/Los_Angeles"
        return default


def make_plugin():
    return comic_module.Comic({"id": "comic"})


def test_daily_rotation_keeps_one_comic_per_day(monkeypatch):
    monkeypatch.setattr(comic_module, "COMICS", {"A": {}, "B": {}, "C": {}})
    monkeypatch.setattr(
        comic_module.random,
        "shuffle",
        lambda items: items.__setitem__(slice(None), ["B", "A", "C"]),
    )
    plugin = make_plugin()
    settings = {}

    pool, queue, candidates = plugin._daily_comic_candidates(settings, "2026-05-27")
    assert candidates == ["B", "A", "C"]

    plugin._commit_daily_comic_selection(settings, "2026-05-27", pool, queue, "B")

    _, _, same_day_candidates = plugin._daily_comic_candidates(settings, "2026-05-27")
    assert same_day_candidates[0] == "B"
    assert settings[plugin.ROTATION_QUEUE_KEY] == ["A", "C"]


def test_daily_rotation_uses_next_source_on_next_day(monkeypatch):
    monkeypatch.setattr(comic_module, "COMICS", {"A": {}, "B": {}, "C": {}})
    plugin = make_plugin()
    settings = {
        plugin.ROTATION_DATE_KEY: "2026-05-27",
        plugin.ROTATION_SELECTED_KEY: "B",
        plugin.ROTATION_POOL_KEY: ["A", "B", "C"],
        plugin.ROTATION_QUEUE_KEY: ["A", "C"],
        plugin.ROTATION_LAST_KEY: "B",
    }

    _, _, candidates = plugin._daily_comic_candidates(settings, "2026-05-28")

    assert candidates[0] == "A"


def test_daily_rotation_falls_back_when_selected_feed_fails(monkeypatch):
    monkeypatch.setattr(comic_module, "COMICS", {"Broken": {}, "Working": {}})
    monkeypatch.setattr(
        comic_module.random,
        "shuffle",
        lambda items: items.__setitem__(slice(None), ["Broken", "Working"]),
    )
    monkeypatch.setattr(
        comic_module.Comic,
        "_current_date_key",
        lambda self, device_config: "2026-05-27",
    )
    calls = []

    def fake_get_panel(comic_name):
        calls.append(comic_name)
        if comic_name == "Broken":
            raise RuntimeError("feed down")
        return {"image_url": "https://example.test/comic.png", "title": "", "caption": ""}

    monkeypatch.setattr(comic_module, "get_panel", fake_get_panel)

    plugin = make_plugin()
    settings = {}

    comic_name, panel = plugin._get_comic_panel(settings, DeviceConfig())

    assert comic_name == "Working"
    assert panel["image_url"] == "https://example.test/comic.png"
    assert calls == ["Broken", "Working"]
    assert settings[plugin.ROTATION_SELECTED_KEY] == "Working"
    assert settings[plugin.ROTATION_QUEUE_KEY] == []
