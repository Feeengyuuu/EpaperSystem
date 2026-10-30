"""Chinese titles and summaries for the papers AI 论文日报 shows.

With an OpenAI key (the one Telegram 精选 and 每日 AI 新闻 use), the shown papers' titles and
one-sentence summaries are put into Simplified Chinese once each, in one request, and kept by
arXiv id; a paper stays in English until then, and on a device without a key.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path


logger = logging.getLogger(__name__)
# OpenAI's current small model, and the one asked once more when OpenAI refuses it.
DEFAULT_MODEL = "gpt-6-luna"
PREVIOUS_MODEL = "gpt-5-mini"
TIMEOUT_SECONDS = 30
MAX_OUTPUT_TOKENS = 4000
# Translations kept, newest last.
CACHE_LIMIT = 400
INSTRUCTIONS = (
    "Translate each AI research paper's title and one-sentence summary into concise Simplified Chinese "
    "for a Chinese reader skimming a daily list. Keep model, dataset, benchmark and method names "
    "(e.g. RAG, LoRA, Qwen, GUI agent) in their original form; keep the title under 40 Chinese "
    "characters and the summary under 70. Return every id once."
)
SCHEMA = {
    "type": "object",
    "properties": {"papers": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "title": {"type": "string"}, "summary": {"type": "string"}},
        "required": ["id", "title", "summary"], "additionalProperties": False}}},
    "required": ["papers"], "additionalProperties": False,
}


class Translations:
    """The kept translations in ``path`` (a JSON object by arXiv id)."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.items = {key: value for key, value in data.items()
                      if isinstance(value, dict) and value.get("title")} if isinstance(data, dict) else {}

    def get(self, paper_id):
        return self.items.get(paper_id)

    def missing(self, papers):
        return [paper for paper in papers if paper["id"] not in self.items]

    def add(self, entries):
        for entry in entries:
            self.items.pop(entry["id"], None)
            self.items[entry["id"]] = {"title": entry["title"], "summary": entry["summary"]}
        while len(self.items) > CACHE_LIMIT:
            self.items.pop(next(iter(self.items)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(self.items, stream, ensure_ascii=False)
            os.replace(temporary, self.path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


def checked(content, papers):
    """The reply's translations for the asked papers, each with a title."""
    asked = {paper["id"] for paper in papers}
    try:
        rows = json.loads(content).get("papers") or []
    except (ValueError, AttributeError):
        return []
    entries = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in asked:
            continue
        title = " ".join(str(row.get("title") or "").split())[:80]
        if title:
            entries[row["id"]] = {"id": row["id"], "title": title,
                                  "summary": " ".join(str(row.get("summary") or "").split())[:160]}
    return list(entries.values())


def translate(papers, api_key, *, client_factory=None, model=DEFAULT_MODEL):
    """Chinese titles and summaries for ``papers`` in one request; [] without a key or a reply."""
    if not api_key or not papers:
        return []
    if client_factory is not None:
        client = client_factory(api_key)
    else:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0)
    request = [{"id": paper["id"], "title": paper["title"], "summary": paper["summary"]} for paper in papers]

    def ask(name):
        return client.responses.create(
            model=name, instructions=INSTRUCTIONS, input=json.dumps(request, ensure_ascii=False),
            max_output_tokens=MAX_OUTPUT_TOKENS, reasoning={"effort": "low"},
            text={"format": {"type": "json_schema", "name": "ai_papers", "strict": True, "schema": SCHEMA},
                  "verbosity": "low"})

    try:
        response = ask(model)
    except Exception as exc:
        refused = getattr(exc, "status_code", None) in (403, 404) or "model_not_found" in str(exc).casefold()
        if not refused or model == PREVIOUS_MODEL:
            raise
        logger.warning("AI Papers translation model %s refused; asking %s", model, PREVIOUS_MODEL)
        response = ask(PREVIOUS_MODEL)
    return checked((getattr(response, "output_text", "") or "").strip(), papers)
