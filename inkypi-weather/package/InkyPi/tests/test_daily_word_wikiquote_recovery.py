"""Date-faithful Wikiquote recovery through real rendering and cache output."""

from datetime import datetime, timezone
import json
from urllib.parse import parse_qs, unquote, urlsplit

import requests
import pytest

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.daily_word_poem import daily_word_poem as word_module
from plugins.daily_word_poem.daily_word_poem import DailyWordPoem


DATE = "2026-08-30"
YEAR_TITLE = "Wikiquote:Quote_of_the_day/August_30,_2026"
OLD_QUOTE = "A synthetic old-page sentence must not become today's quotation."


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 30, 12, tzinfo=timezone.utc)
        return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)


class Device:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"timezone": "America/Los_Angeles", "orientation": "horizontal"}
        return values if key is None else values.get(key, default)


def install_http(monkeypatch, *, raw=None, dated=None, today=None):
    visited = []

    class Session:
        def get(self, url, **_kwargs):
            visited.append(url)
            parsed = urlsplit(url)
            title = parse_qs(parsed.query).get("title", [""])[0]
            value = None
            if parsed.netloc == "en.wikiquote.org" and "action=raw" in parsed.query:
                value = raw if title == YEAR_TITLE else f"{OLD_QUOTE} ~ Wrong Year ~"
            elif parsed.path == f"/api/quotes/{DATE}":
                value = dated
            elif parsed.path == "/api/quote_of_the_day":
                value = today
            response = requests.Response()
            response.url = url
            response.status_code = 404 if value is None else 200
            response._content = (
                json.dumps(value) if isinstance(value, (dict, list)) else value or "not found"
            ).encode("utf-8")
            response.encoding = "utf-8"
            return response

    monkeypatch.setattr(word_module, "get_http_session", Session)
    return visited


def render(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("INKYPI_CONTEXT_CACHE_DIR", raising=False)
    monkeypatch.setattr(word_module, "datetime", FrozenDatetime)
    plugin = DailyWordPoem({"id": "daily_word_poem"})
    image = plugin.generate_image(
        {"fetch_dictionary": False, "fetch_wikiquote": True, "word_list": "tranquil"},
        Device(),
    )
    cache_path = tmp_path / "plugins" / "daily_word_poem" / "cache" / "daily.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else None
    return image, cached


def test_year_specific_template_becomes_fresh_cache_before_toolforge(tmp_path, monkeypatch):
    visited = install_http(monkeypatch, raw="""{{Wikiquote:Quote of the day/Template
|image1=Fixture.png
|quote=A [[Careful path|careful step]] today keeps the fixture clear.
|author=[[Fixture Author|A. Example]]
}}""")

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert cached["quote"]["text"] == "A careful step today keeps the fixture clear."
    assert cached["quote"]["author"] == "A. Example"
    assert cached["quote"]["featured_date"] == DATE
    assert unquote(cached["quote"]["source_url"]).endswith("/August_30,_2026")
    assert len(visited) == 1
    assert unquote(visited[0]).find(YEAR_TITLE) >= 0
    image.close()


@pytest.mark.parametrize("source", ["dated", "today"])
@pytest.mark.parametrize("featured_date", [None, "", "2026-08-31", "2025-08-30"])
def test_api_without_exact_featured_date_never_promotes_a_daily_cache(
    tmp_path, monkeypatch, source, featured_date,
):
    payload = {
        "quote": "A synthetic API sentence with an unproven day.",
        "author": "Fixture Author",
        "date": DATE,
    }
    if featured_date is not None:
        payload["featured_date"] = featured_date
    visited = install_http(monkeypatch, **{source: payload})

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert cached is None
    assert all(YEAR_TITLE in unquote(url) for url in visited if "action=raw" in url)
    image.close()


def test_template_pipes_inside_nowiki_and_nested_image_parameters_are_not_delimiters(
    tmp_path, monkeypatch,
):
    install_http(monkeypatch, raw="""{{Wikiquote:Quote of the day/Template
|image1={{FixtureImage|quote=Not the quote|author=Not the author}}
|quote=The fixture keeps <nowiki>a|b</nowiki> together with [[Link|visible words]].
|author=Fixture Author
}}""")

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert cached["quote"]["text"] == "The fixture keeps a|b together with visible words."
    assert cached["quote"]["author"] == "Fixture Author"
    image.close()


@pytest.mark.parametrize("raw", [
    "<!DOCTYPE html><html><body>Maintenance ~ Website ~</body></html>",
    "#REDIRECT [[Wikiquote:Quote of the day/August 30]]",
    "The quotation for this date has not been selected yet.",
])
def test_non_quote_date_page_cannot_be_published_as_a_daily_quote(tmp_path, monkeypatch, raw):
    visited = install_http(monkeypatch, raw=raw)

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert cached is None
    assert all(YEAR_TITLE in unquote(url) for url in visited if "action=raw" in url)
    image.close()


@pytest.mark.parametrize("source", ["dated", "today"])
def test_unavailable_official_page_can_use_only_a_date_verified_api_result(
    tmp_path, monkeypatch, source,
):
    payload = {
        "quote": "An exact-date synthetic backup quotation.",
        "author": "Fixture Author",
        "featured_date": DATE,
    }
    visited = install_http(monkeypatch, **{source: [payload]})

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert cached["quote"]["text"] == payload["quote"]
    assert cached["quote"]["featured_date"] == DATE
    assert cached["quote"]["source_url"] == visited[-1]
    assert len(visited) == (2 if source == "dated" else 3)
    assert YEAR_TITLE in unquote(visited[0])
    image.close()


@pytest.mark.parametrize("raw", [
    "{{Wikiquote:Quote of the day/Template|quote={{lang|en|Fixture}}|author=Author}}",
    "{{Wikiquote:Quote of the day/Template|quote=Fixture|author=Author",
    "{{Wikiquote:Quote of the day/Template|quote=First|quote=Second|author=Author}}",
    "{{Wikiquote:Quote of the day/Template|quote=<nowiki>Unclosed|author=Author}}",
])
def test_unresolved_template_content_uses_verified_backup_not_a_truncated_quote(
    tmp_path, monkeypatch, raw,
):
    payload = {
        "quote": "A verified synthetic backup keeps all its words.",
        "author": "Backup Author",
        "featured_date": DATE,
    }
    visited = install_http(monkeypatch, raw=raw, dated=payload)

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert cached["quote"]["text"] == payload["quote"]
    assert cached["quote"]["author"] == payload["author"]
    assert cached["quote"]["source_url"] == visited[-1]
    assert len(visited) == 2
    image.close()


def test_commented_templates_and_parameter_pipes_do_not_supply_quote_content(tmp_path, monkeypatch):
    install_http(monkeypatch, raw="""<!-- {{Wikiquote:Quote of the day/Template
|quote=This disabled template is not the quotation.|author=Wrong Author}} -->
{{Wikiquote:Quote of the day/Template
|quote=A visible <!-- |author=Not an author -->synthetic quotation.
|author=Fixture Author
}}""")

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert cached["quote"]["text"] == "A visible synthetic quotation."
    assert cached["quote"]["author"] == "Fixture Author"
    image.close()


def test_encoded_literal_pipe_is_content_not_a_template_parameter(tmp_path, monkeypatch):
    install_http(monkeypatch, raw="""{{Wikiquote:Quote of the day/Template
|quote=A literal &#124; divider keeps its ending.
|author=Fixture Author
}}""")

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert cached["quote"]["text"] == "A literal | divider keeps its ending."
    assert cached["quote"]["author"] == "Fixture Author"
    image.close()


def test_reference_markup_never_publishes_a_truncated_daily_quote(tmp_path, monkeypatch):
    install_http(monkeypatch, raw="""{{Wikiquote:Quote of the day/Template
|quote=A synthetic quotation <ref>annotation|not a parameter</ref> keeps its ending.
|author=Fixture Author
}}""")

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert cached is None
    image.close()


def test_legacy_nested_template_never_publishes_only_its_surrounding_words(tmp_path, monkeypatch):
    install_http(
        monkeypatch,
        raw="{{lang|en|Keep every word}} in this sentence. ~ [[Fixture Author]] ~",
    )

    image, cached = render(tmp_path, monkeypatch)

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert cached is None
    image.close()
