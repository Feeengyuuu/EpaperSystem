"""Pure adapters for external dictionary response formats."""

from copy import copy
from html.parser import HTMLParser
import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag


_PARTS_OF_SPEECH = {
    "adjective", "adverb", "article", "conjunction", "determiner", "interjection",
    "noun", "numeral", "particle", "postposition", "preposition", "pronoun",
    "proper noun", "verb",
}


class _DefinitionStructureGuard(HTMLParser):
    """Reject truncated headings/lists that a forgiving HTML parser repairs."""

    _STRUCTURAL_TAGS = {"h2", "h3", "h4", "h5", "h6", "ol", "li"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag in self._STRUCTURAL_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self._STRUCTURAL_TAGS:
            if not self.stack or self.stack.pop() != tag:
                raise ValueError("Malformed Wiktionary heading or definition list")

    def close(self):
        super().close()
        if self.stack:
            raise ValueError("Truncated Wiktionary heading or definition list")


def _heading(node):
    if not isinstance(node, Tag):
        return None
    if re.fullmatch(r"h[2-6]", node.name):
        return node
    if "mw-heading" in node.get("class", []):
        return node.find(re.compile(r"^h[2-6]$"), recursive=False)
    return None


def _heading_text(node):
    clean = copy(node)
    for edit in clean.select(".mw-editsection"):
        edit.decompose()
    return " ".join(clean.get_text(" ", strip=True).split()).casefold()


def _definition_text(item):
    clean = copy(item)
    for nested in clean.select(
        "ol, ul, dl, table, script, style, .citation-whole, .cited-source, "
        ".cited-passage, .reference, .references, .h-usage-example, .h-quotation, "
        ".e-quotation, .e-example, .nyms, .translation, .mw-editsection"
    ):
        nested.decompose()
    text = " ".join(clean.get_text("", strip=False).split())
    text = re.sub(r"\s+([,.;:!?\)\]])", r"\1", text)
    text = re.sub(r"([\(\[])\s+", r"\1", text)
    if not text or len(text) > 2048 or text.endswith((":", "...", "…")):
        return ""
    if "{{" in text or "}}" in text or not re.search(r"[A-Za-z]", text):
        return ""
    if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
        return ""
    if not re.sub(r"^(?:\([^)]*\)\s*)+", "", text).strip(" .;:!?"):
        return ""
    return text


def parse_wiktionary_entry(data, word):
    """Return one English definition from a MediaWiki Action API parse result.

    Raises ValueError for unsupported or untrustworthy input. This function does
    not perform requests, mutate the response, or manufacture missing content.
    """
    if not isinstance(word, str) or not word.strip() or len(word) > 128:
        raise ValueError("Invalid requested dictionary word")
    if not isinstance(data, dict) or "error" in data or not isinstance(data.get("parse"), dict):
        raise ValueError("Invalid Wiktionary API response")
    parsed = data["parse"]
    title = parsed.get("title")
    revision = parsed.get("revid")
    html = parsed.get("text")
    if not isinstance(title, str) or title.casefold() != word.casefold():
        raise ValueError("Wiktionary response title does not match requested word")
    if type(revision) is not int or revision <= 0:
        raise ValueError("Wiktionary response has no valid source revision")
    if not isinstance(html, str) or not html.strip() or len(html.encode("utf-8")) > 256 * 1024:
        raise ValueError("Wiktionary response HTML is empty or oversized")
    guard = _DefinitionStructureGuard()
    guard.feed(html)
    guard.close()
    soup = BeautifulSoup(html, "html.parser")
    english = next((h for h in soup.find_all("h2") if _heading_text(h) == "english"), None)
    if english is None:
        raise ValueError("Wiktionary entry has no English section")

    anchor = english.parent if "mw-heading" in english.parent.get("class", []) else english
    phonetic = ""
    part_of_speech = None
    pronunciation = False
    for node in anchor.next_siblings:
        if not isinstance(node, Tag):
            continue
        heading = _heading(node)
        if heading is not None:
            if heading.name == "h2":
                break
            name = _heading_text(heading)
            pronunciation = bool(re.fullmatch(r"pronunciation(?: \d+)?", name))
            part_of_speech = name if heading.name in {"h3", "h4"} and name in _PARTS_OF_SPEECH else None
            continue
        if pronunciation and not phonetic:
            ipa = node if "IPA" in node.get("class", []) else node.select_one(".IPA")
            if ipa is not None:
                phonetic = " ".join(ipa.get_text(" ", strip=True).split())
        if part_of_speech is None or node.name != "ol":
            continue
        for item in node.find_all("li", recursive=False):
            definition = _definition_text(item)
            if definition:
                return {
                    "word": word,
                    "phonetic": phonetic,
                    "part_of_speech": part_of_speech,
                    "definition": definition,
                    "example": "",
                    "source": "Wiktionary",
                    "source_url": "https://en.wiktionary.org/w/index.php?" + urlencode({"title": title, "oldid": revision}) + "#English",
                    "source_revision": revision,
                    "source_license": "CC BY-SA 4.0",
                    "source_license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                    "source_attribution": "Wiktionary contributors (CC BY-SA 4.0)",
                }
    raise ValueError("Wiktionary entry has no supported English definition")
