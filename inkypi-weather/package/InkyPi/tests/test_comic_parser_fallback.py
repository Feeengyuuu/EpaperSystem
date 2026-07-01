import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.comic import comic_parser


class Session:
    def get(self, *args, **kwargs):
        raise RuntimeError("dns failed")


def test_get_panel_uses_cached_panel_after_feed_failure(monkeypatch, tmp_path):
    cache_path = tmp_path / "panel.json"
    cache_path.write_text(
        json.dumps({"image_url": "https://example.test/comic.png", "title": "Cached", "caption": "Old"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(comic_parser, "COMICS", {"Broken": {"feed": "https://example.test/rss"}})
    monkeypatch.setattr(comic_parser, "get_http_session", lambda: Session())
    monkeypatch.setattr(comic_parser, "_panel_cache_path", lambda comic_name: str(cache_path))

    panel = comic_parser.get_panel("Broken")

    assert panel["title"] == "Cached"
    assert panel["image_url"] == "https://example.test/comic.png"


def test_get_panel_returns_placeholder_without_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "missing.json"
    monkeypatch.setattr(comic_parser, "COMICS", {"Broken": {"feed": "https://example.test/rss"}})
    monkeypatch.setattr(comic_parser, "get_http_session", lambda: Session())
    monkeypatch.setattr(comic_parser, "_panel_cache_path", lambda comic_name: str(cache_path))

    panel = comic_parser.get_panel("Broken")

    assert panel["placeholder"] is True
    assert panel["image_url"] is None