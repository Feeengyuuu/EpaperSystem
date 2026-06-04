import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.chinese_literature_clock import quote_picker  # noqa: E402
from plugins.chinese_literature_clock.chinese_literature_clock import ChineseLiteratureClock  # noqa: E402
from plugins.chinese_literature_clock.open_library import lookup_book_metadata  # noqa: E402


def test_source_random_balances_by_book_before_quote(monkeypatch):
    rows = [
        {"full_quote": "A1", "book_title": "Book A", "author_name": "Author A"},
        {"full_quote": "A2", "book_title": "Book A", "author_name": "Author A"},
        {"full_quote": "B1", "book_title": "Book B", "author_name": "Author B"},
    ]

    monkeypatch.setattr(quote_picker.random, "choice", lambda seq: seq[-1])

    picked = quote_picker.pick_quote(rows, "source_random", "2026-06-03-1200")

    assert picked["book_title"] == "Book B"
    assert picked["full_quote"] == "B1"


class FakeOpenLibraryResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "docs": [
                {
                    "key": "/works/OL_BAD",
                    "title": "红楼梦研究",
                    "author_name": ["曹雪芹"],
                    "language": ["chi"],
                    "edition_count": 3,
                },
                {
                    "key": "/works/OL123W",
                    "title": "红楼梦",
                    "author_name": ["曹雪芹"],
                    "language": ["chi"],
                    "first_publish_year": 1791,
                    "edition_count": 88,
                    "cover_i": 12345,
                    "publisher": ["上海文艺出版社"],
                },
            ]
        }


class FakeOpenLibrarySession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({
            "url": url,
            "params": params,
            "headers": headers,
            "timeout": timeout,
        })
        return FakeOpenLibraryResponse()


def test_open_library_lookup_ranks_exact_chinese_match_and_caches(tmp_path):
    session = FakeOpenLibrarySession()
    now = datetime(2026, 6, 3, tzinfo=timezone.utc)

    first = lookup_book_metadata("红楼梦", "曹雪芹", cache_dir=tmp_path, session=session, now=now)
    second = lookup_book_metadata("红楼梦", "曹雪芹", cache_dir=tmp_path, session=session, now=now)

    assert first["title"] == "红楼梦"
    assert first["first_publish_year"] == 1791
    assert first["edition_count"] == 88
    assert first["cover_url"] == "https://covers.openlibrary.org/b/id/12345-M.jpg"
    assert first["open_library_url"] == "https://openlibrary.org/works/OL123W"
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert len(session.calls) == 1


def test_source_block_formats_open_library_metadata():
    plugin = ChineseLiteratureClock({"id": "chinese_literature_clock"})

    block = plugin._build_source_block(
        "红楼梦",
        "曹雪芹",
        {
            "source": "Open Library",
            "first_publish_year": 1791,
            "edition_count": 88,
            "publisher": "上海文艺出版社",
            "open_library_url": "https://openlibrary.org/works/OL123W",
        },
    )

    assert block["book_line"] == "《红楼梦》 · 曹雪芹"
    assert block["source_label"] == "Open Library"
    assert "首版 1791" in block["meta_line"]
    assert "88 个版本" in block["meta_line"]
    assert "上海文艺出版社" in block["meta_line"]


def test_quote_renderer_accepts_source_block():
    plugin = ChineseLiteratureClock({"id": "chinese_literature_clock"})

    image = plugin._render_quote_image(
        (800, 480),
        "现在是晚上十二点钟。",
        "晚上十二点钟",
        {
            "book_line": "《红楼梦》 · 曹雪芹",
            "meta_line": "Open Library · 首版 1791 · 88 个版本",
            "source_label": "Open Library",
        },
        {},
    )

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
