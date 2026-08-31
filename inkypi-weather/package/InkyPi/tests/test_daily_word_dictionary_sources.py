"""Contract coverage for source-backed English Wiktionary definitions."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_word_poem.dictionary_sources import parse_wiktionary_entry


# Reduced Action API HTML retrieved 2026-08-31, tranquil revision 92094244.
# Source: https://en.wiktionary.org/w/index.php?title=tranquil&oldid=92094244
# Wiktionary contributors, CC BY-SA 4.0; examples/citations shortened in fixture.
TRANQUIL_HTML = """
<div class="mw-parser-output">
<div class="mw-heading mw-heading2"><h2 id="English">English</h2></div>
<div class="mw-heading mw-heading3"><h3 id="Etymology">Etymology</h3></div>
<p>Borrowed from French.</p>
<div class="mw-heading mw-heading3"><h3 id="Pronunciation">Pronunciation</h3></div>
<ul><li>IPA: <span class="IPA nowrap">/ˈtɹæŋ.kwɪl/</span></li></ul>
<div class="mw-heading mw-heading3"><h3 id="Adjective">Adjective</h3>
<span class="mw-editsection">[edit]</span></div>
<p><span class="headword-line"><strong lang="en">tranquil</strong></span></p>
<ol><li><a href="/wiki/free">Free</a> from <a href="/wiki/emotional">emotional</a>
or <a href="/wiki/mental">mental</a> disturbance.
<dl><dd><span class="nyms synonym">Synonyms: calm, peaceful, serene</span></dd></dl>
<ul><li><div class="citation-whole">1847, Charlotte Brontë, Jane Eyre:
<dl><dd><span class="cited-passage">A literary quotation.</span></dd></dl>
</div></li></ul></li>
<li>Calm; without motion or sound.</li></ol>
<div class="mw-heading mw-heading4"><h4 id="Translations">Translations</h4></div>
<ol><li>A translation list is not a definition.</li></ol>
<div class="mw-heading mw-heading2"><h2 id="Catalan">Catalan</h2></div>
<span class="IPA">/wrong-language/</span><h3>Adjective</h3>
<ol><li>tranquil, calm</li></ol></div>
"""


def _payload(html=TRANQUIL_HTML, **overrides):
    return {"parse": {"title": "tranquil", "revid": 92094244, "text": html, **overrides}}


def test_pronunciation_does_not_come_from_a_foreign_etymology():
    document = TRANQUIL_HTML.replace(
        "<p>Borrowed from French.</p>",
        '<p>Borrowed from French <span class="IPA">/french-origin/</span>.</p>',
    )
    assert parse_wiktionary_entry(_payload(document), "tranquil")["phonetic"] == "/ˈtɹæŋ.kwɪl/"


def test_actual_english_definition_excludes_quotes_examples_and_other_languages():
    entry = parse_wiktionary_entry(_payload(), "tranquil")

    assert entry == {
        "word": "tranquil",
        "phonetic": "/ˈtɹæŋ.kwɪl/",
        "part_of_speech": "adjective",
        "definition": "Free from emotional or mental disturbance.",
        "example": "",
        "source": "Wiktionary",
        "source_url": "https://en.wiktionary.org/w/index.php?title=tranquil&oldid=92094244#English",
        "source_revision": 92094244,
        "source_license": "CC BY-SA 4.0",
        "source_license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_attribution": "Wiktionary contributors (CC BY-SA 4.0)",
    }


@pytest.mark.parametrize(
    "data,word",
    [
        (None, "tranquil"),
        ([], "tranquil"),
        ({}, "tranquil"),
        ({"parse": None}, "tranquil"),
        ({"error": {"code": "missingtitle"}}, "tranquil"),
        ({"error": {"code": "maxlag"}, **_payload()}, "tranquil"),
        (_payload(title="peaceful"), "tranquil"),
        (_payload(title=12), "tranquil"),
        (_payload(), ""),
        (_payload(), None),
        (_payload(revid=0), "tranquil"),
        (_payload(revid=-10), "tranquil"),
        (_payload(revid=True), "tranquil"),
        (_payload(revid="92094244"), "tranquil"),
        (_payload(text={"*": TRANQUIL_HTML}), "tranquil"),
        (_payload(text=""), "tranquil"),
        (_payload(text="x" * (256 * 1024 + 1) + TRANQUIL_HTML), "tranquil"),
        (_payload(text="é" * (128 * 1024) + TRANQUIL_HTML), "tranquil"),
    ],
)
def test_invalid_api_envelope_or_unrelated_redirect_is_not_trusted(data, word):
    with pytest.raises(ValueError):
        parse_wiktionary_entry(data, word)


def test_case_only_title_normalization_preserves_requested_word_and_source_identity():
    entry = parse_wiktionary_entry(_payload(title="Tranquil"), "tranquil")

    assert entry["word"] == "tranquil"
    assert entry["source_url"] == (
        "https://en.wiktionary.org/w/index.php?title=Tranquil&oldid=92094244#English"
    )


def test_nested_etymology_legacy_headings_preserve_qualifiers_but_drop_examples():
    html = """
    <h2><span class="mw-headline" id="Dutch">Dutch</span></h2>
    <span class="IPA">/wrong/</span><h3>Noun</h3><ol><li>Wrong language.</li></ol>
    <h2><span class="mw-headline" id="English">English</span></h2>
    <h3>Etymology 1</h3><ol><li>Not a definition.</li></ol>
    <h4>Pronunciation</h4><span class="IPA">/correct/</span>
    <h4><span class="mw-headline" id="Noun">Noun</span>
    <span class="mw-editsection">[edit]</span></h4>
    <p><strong lang="en">tranquil</strong></p>
    <ol><li><span class="ib-brac">(</span><span class="ib-content">figurative</span>
    <span class="ib-brac">)</span> A <a href="/wiki/calm">calm</a> state.
    <span class="h-usage-example">He sought tranquility.</span>
    <span class="h-quotation">A quoted sentence.</span>
    <sup class="reference">[1]</sup><script>do not show this</script>
    </li></ol><h2>Latin</h2><span class="IPA">/also-wrong/</span>
    """
    entry = parse_wiktionary_entry(_payload(html), "tranquil")

    assert entry["definition"] == "(figurative) A calm state."
    assert entry["phonetic"] == "/correct/"
    assert entry["part_of_speech"] == "noun"


@pytest.mark.parametrize(
    "html",
    [
        '<h2 id="French">French</h2><h3>Adjective</h3><ol><li>Calm.</li></ol>',
        '<h2 id="English">English</h2><ol><li>Not a definition.</li></ol>',
        '<h2>English</h2><h3>Etymology</h3><ol><li>Latin origin.</li></ol>',
        '<h2>English</h2><h3>Adjective</h3><h4>Translations</h4><ol><li>Calm.</li></ol>',
        '<h2>English</h2><h3>Adjective</h3><h2>French</h2><ol><li>Calm.</li></ol>',
        '<h2>English</h2><h3>Adjective</h3><h3>Etymology 2</h3><ol><li>Calm.</li></ol>',
        '<h2>English</h2><h3>Adjective</h3><div><ol><li>Navigation list.</li></ol></div>',
        '<h2>English</h2><h3>Adjective</h3><ol><li><dl><dd>Only an example.</dd></dl></li></ol>',
        '<h2>English</h2><h3>Adjective</h3><ol><li><span class="h-quotation">Only a quote.</span></li></ol>',
        '<h2>English</h2><h3>Adjective</h3><ol><li>A truncated definition',
        '<h2>English</h2><h3>Adjective</h3><ol><li>(rare)</li></ol>',
        '<h2>English</h2><h3>Adjective</h3><ol><li>Used for:</li></ol>',
        '<h2>English</h2><h3>Adjective</h3><ol><li>...</li></ol>',
        '<h2>English</h2><h3>Adjective</h3><ol><li>{{unexpanded template}}</li></ol>',
        '<h2>English</h2><h3>Adjective</h3><ol><li>Free from (unfinished qualifier.</li></ol>',
        '<html><body><h1>Temporary maintenance</h1><ol><li>Try later.</li></ol></body></html>',
    ],
)
def test_missing_english_definition_or_malformed_content_is_not_silently_accepted(html):
    with pytest.raises(ValueError):
        parse_wiktionary_entry(_payload(html), "tranquil")


def test_serendipity_real_definition_does_not_include_nested_long_citation():
    # Reduced source: serendipity revision 92156688, retrieved 2026-08-31.
    # https://en.wiktionary.org/w/index.php?title=serendipity&oldid=92156688
    # Wiktionary contributors, CC BY-SA 4.0; citation shortened.
    html = """
    <div class="mw-heading mw-heading2"><h2 id="English">English</h2></div>
    <div class="mw-heading mw-heading3"><h3 id="Noun">Noun</h3></div>
    <p><span class="headword-line"><strong lang="en">serendipity</strong></span></p>
    <ol><li>The <a href="/wiki/phenomenon">phenomenon</a> of making an
    <a href="/wiki/unplanned">unplanned</a>, <a href="/wiki/fortunate">fortunate</a>
    <a href="/wiki/discovery">discovery</a> through a <a href="/wiki/combination">combination</a>
    of <a href="/wiki/unexpected">unexpected</a> <a href="/wiki/circumstance">circumstances</a>
    and <a href="/wiki/insightful">insightful</a> <a href="/wiki/recognition">recognition</a>.
    <dl><dd>Antonyms: Murphy's law, perfect storm.</dd></dl>
    <ul><li><b>1754</b>, Horace Walpole, <i>The Letters of Horace Walpole</i>
    <dl><dd>A very long nested quotation.</dd></dl></li></ul></li></ol>
    <div class="mw-heading mw-heading4"><h4 id="Translations">Translations</h4></div>
    """
    entry = parse_wiktionary_entry(
        _payload(html, title="serendipity", revid=92156688), "serendipity"
    )

    assert entry["definition"] == (
        "The phenomenon of making an unplanned, fortunate discovery through a "
        "combination of unexpected circumstances and insightful recognition."
    )
    assert entry["phonetic"] == ""
    assert entry["part_of_speech"] == "noun"
