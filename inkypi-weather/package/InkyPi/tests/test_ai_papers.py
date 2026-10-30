import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.ai_papers import render, sources, translate  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402

NOW = datetime(2026, 10, 29, 23, 30, tzinfo=sources.PACIFIC)
AVATAR = "https://cdn-avatars.huggingface.co/v1/production/uploads/a/logo.png"


def _raw(paper_id, title, upvotes, **paper):
    return {"paper": {"id": paper_id, "title": title, "upvotes": upvotes, "authors": [{"name": "Ada Lovelace"}],
                      "ai_summary": f"{title} in one sentence.", "ai_keywords": ["agents", "benchmarks"], **paper},
            "numComments": 2}


def _context():
    return TaskContext(threading.Event(), time.monotonic() + 30, time.monotonic)


def test_a_paper_keeps_its_stills_organisation_and_summary():
    paper = sources.normalize_paper(_raw(
        "2610.32749", "Efficient  Streaming\nTSE", 152, githubStars=20,
        mediaUrls=["https://cdn-uploads.huggingface.co/production/uploads/x/demo.mp4",
                   "https://cdn-uploads.huggingface.co/production/uploads/x/teaser.png"],
        organization={"fullname": "Tsinghua Machine Learning Group", "avatar": AVATAR}), "2026-10-30")

    assert paper["title"] == "Efficient Streaming TSE"
    assert paper["image"].endswith("/teaser.png")  # the video is not a still
    assert (paper["organization"], paper["org_avatar"]) == ("Tsinghua Machine Learning Group", AVATAR)
    assert (paper["upvotes"], paper["comments"], paper["stars"]) == (152, 2, 20)
    assert paper["summary"] == "Efficient Streaming\nTSE in one sentence.".replace("\n", " ").replace("  ", " ")


def test_a_listing_without_an_arxiv_id_or_a_title_is_dropped_and_a_foreign_image_ignored():
    assert sources.normalize_paper(_raw("not-an-id", "Title", 3), "2026-10-30") is None
    assert sources.normalize_paper(_raw("2610.00001", " ", 3), "2026-10-30") is None
    paper = sources.normalize_paper(_raw("2610.00002", "T", 1, mediaUrls=["https://evil.example/x.png"],
                                         organization={"name": "lab", "avatar": "http://cdn-avatars.huggingface.co/a.png"}),
                                    "2026-10-30")
    assert paper["image"] is None and paper["org_avatar"] is None and paper["organization"] == "lab"


def test_two_days_rank_once_each_by_upvotes():
    today = {"papers": [sources.normalize_paper(_raw("2610.00001", "A", 5), "2026-10-30"),
                        sources.normalize_paper(_raw("2610.00002", "B", 40), "2026-10-30")]}
    yesterday = {"papers": [sources.normalize_paper(_raw("2610.00002", "B", 39), "2026-10-29"),
                            sources.normalize_paper(_raw("2610.00003", "C", 152), "2026-10-29")]}

    papers = sources.ranked([today, yesterday])

    assert [(paper["id"], paper["upvotes"]) for paper in papers] == [
        ("2610.00003", 152), ("2610.00002", 40), ("2610.00001", 5)]
    assert sources.list_days(NOW) == ["2026-10-30", "2026-10-29"]


def test_an_arxiv_figure_comes_from_the_numbered_figures_not_the_title_block(monkeypatch):
    html = ('<div class="ltx_authors"><img src="2610.38568v1/trento-university-official.png" class="ltx_graphics"></div>'
            '<figure id="S1.F1" class="ltx_figure"><img src="2610.38568v1/figures/hf-logo.png" class="ltx_graphics">'
            '<img src="2610.38568v1/x1.png" class="ltx_graphics"></figure>'
            '<figure class="ltx_figure ltx_align_center"><img src="2610.38568v1/teaser.jpg"></figure>')
    seen = {}

    class Client:
        def request_text(self, method, url, **kwargs):
            seen["url"] = url
            return SimpleNamespace(data=html)

    monkeypatch.setattr(sources, "get_http_client", Client)

    figure = sources.fetch_arxiv_figure("2610.38568", _context())

    assert seen["url"] == "https://arxiv.org/html/2610.38568v1"
    assert figure == {"urls": ["https://arxiv.org/html/2610.38568v1/x1.png",
                               "https://arxiv.org/html/2610.38568v1/teaser.jpg"]}
    assert sources.valid_figure(figure) and not sources.valid_figure({"url": None})
    with pytest.raises(ValueError):
        sources.fetch_arxiv_figure("../etc", _context())


class _Responses:
    def __init__(self, replies):
        self.replies, self.models = list(replies), []

    def create(self, **kwargs):
        self.models.append(kwargs["model"])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(output_text=json.dumps(reply, ensure_ascii=False))


def test_titles_are_translated_once_and_kept(tmp_path):
    papers = [sources.normalize_paper(_raw("2610.00001", "Look Only When Needed", 5), "2026-10-30"),
              sources.normalize_paper(_raw("2610.00002", "RealCLI", 3), "2026-10-30")]
    responses = _Responses([{"papers": [
        {"id": "2610.00001", "title": "只在需要时看图", "summary": "预测一致时跳过图像。"},
        {"id": "9999.99999", "title": "未请求", "summary": ""}]}])

    entries = translate.translate(papers, "sk-test", client_factory=lambda key: SimpleNamespace(responses=responses))
    kept = translate.Translations(tmp_path / "translations.json")
    kept.add(entries)

    assert [entry["id"] for entry in entries] == ["2610.00001"]
    reopened = translate.Translations(tmp_path / "translations.json")
    assert reopened.get("2610.00001")["title"] == "只在需要时看图"
    assert [paper["id"] for paper in reopened.missing(papers)] == ["2610.00002"]
    assert translate.translate(papers, "", client_factory=lambda key: None) == []


def test_a_refused_model_asks_the_previous_one():
    class Refused(Exception):
        status_code = 404

    papers = [sources.normalize_paper(_raw("2610.00001", "A", 5), "2026-10-30")]
    responses = _Responses([Refused("model_not_found"), {"papers": [{"id": "2610.00001", "title": "甲", "summary": "乙"}]}])

    entries = translate.translate(papers, "sk-test", client_factory=lambda key: SimpleNamespace(responses=responses))

    assert responses.models == [translate.DEFAULT_MODEL, translate.PREVIOUS_MODEL]
    assert entries == [{"id": "2610.00001", "title": "甲", "summary": "乙"}]


def _papers(count):
    papers = []
    for index in range(count):
        paper = sources.normalize_paper(_raw(f"2610.{index:05d}", f"Paper number {index} on agents and benchmarks",
                                             200 - index, githubStars=index * 7,
                                             organization={"fullname": f"Lab {index}", "avatar": AVATAR}), "2026-10-30")
        paper["image"] = f"https://cdn-uploads.huggingface.co/production/uploads/x/{index}.png"
        papers.append(paper)
    return papers


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_draws_the_lead_cards_and_ranking(mode):
    papers = _papers(13)
    images = {paper["image"]: Image.new("RGB", (400, 300), (40, 120, 200)) for paper in papers[:5]}
    images[AVATAR] = Image.new("RGBA", (96, 96), (255, 157, 0, 255))
    chinese = {papers[0]["id"]: {"title": "首篇论文的中文标题", "summary": "一句话说明它做了什么。"}}

    page = render.render_page((800, 480), {"papers": papers, "chinese": chinese}, {"themeMode": mode}, NOW, images)

    assert page.size == (800, 480)
    lead_figure = page.crop(render.LEAD_FIGURE).convert("RGB")
    assert (40, 120, 200) in set(lead_figure.get_flattened_data())
    assert render.shown_text(papers[0], chinese)[:2] == ("首篇论文的中文标题", "一句话说明它做了什么。")
    assert render.shown_text(papers[1], chinese)[0] == papers[1]["title"]
    # The ranking rows carry the organisations' avatars (orange here).
    ranking = page.crop(render.RANKING).convert("RGB")
    assert sum(1 for pixel in ranking.get_flattened_data() if pixel == (255, 157, 0)) > 200


def test_a_paper_without_an_upload_shows_its_first_arxiv_figure_that_loaded():
    paper = {"image": None, "figures": ["https://arxiv.org/html/1/x1.png", "https://arxiv.org/html/1/x2.png"]}
    second = Image.new("RGB", (8, 8))

    assert render.picture_urls(paper) == paper["figures"]
    assert render._picture(paper, {"https://arxiv.org/html/1/x2.png": second}) is second


def test_without_papers_the_page_says_so():
    page = render.render_page((800, 480), {"papers": []}, {}, NOW, {})
    assert page.size == (800, 480)


def test_counts_read_short():
    assert (render.count_text(75), render.count_text(1520)) == ("75", "1.5k")
