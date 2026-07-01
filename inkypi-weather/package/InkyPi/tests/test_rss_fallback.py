import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.rss import rss as rss_module


class Response:
    def __init__(self, content=b"", exc=None):
        self.content = content
        self.exc = exc

    def raise_for_status(self):
        if self.exc:
            raise self.exc


class Session:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, *args, **kwargs):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_plugin(tmp_path):
    plugin = rss_module.Rss({"id": "rss"})
    plugin.cache_dir = lambda **kwargs: tmp_path
    return plugin


def test_parse_rss_feed_uses_cached_items_after_fetch_failure(monkeypatch, tmp_path):
    feed = b"""
    <rss><channel><item><title>Hello</title><description>World</description><link>https://example.test/a</link></item></channel></rss>
    """
    session = Session([Response(feed), RuntimeError("dns failed")])
    monkeypatch.setattr(rss_module, "get_http_session", lambda: session)
    plugin = make_plugin(tmp_path)

    first_items = plugin.parse_rss_feed("https://example.test/rss.xml")
    second_items = plugin.parse_rss_feed("https://example.test/rss.xml")

    assert first_items[0]["title"] == "Hello"
    assert second_items == first_items


def test_parse_rss_feed_returns_placeholder_without_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(rss_module, "get_http_session", lambda: Session([RuntimeError("dns failed")]))
    plugin = make_plugin(tmp_path)

    items = plugin.parse_rss_feed("https://example.test/rss.xml")

    assert items[0]["title"] == "RSS feed temporarily unavailable"