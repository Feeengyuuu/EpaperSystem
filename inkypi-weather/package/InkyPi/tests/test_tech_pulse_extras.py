"""Tech Pulse's bottom strip takes turns: OpenRouter's new models, LMArena's text leaderboard and
the AI SDKs' weekly downloads. The fixtures are cut from the providers' answers of 2026-11-11."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.tech_pulse import extras, tech_pulse as module  # noqa: E402
from plugins.tech_pulse.tech_pulse import TechPulse  # noqa: E402

NOW = datetime(2026, 11, 11, 21, 0, tzinfo=timezone.utc)


def _arena_row(rank, name, maker, rating, votes, category="overall"):
    return {"row_idx": rank - 1, "truncated_cells": [], "row": {
        "model_name": name, "organization": maker, "license": "Proprietary", "rating": rating,
        "rating_lower": rating - 9.5, "rating_upper": rating + 9.5, "variance": 23.69, "vote_count": float(votes),
        "rank": float(rank), "category": category, "leaderboard_publish_date": "2026-11-09"}}


ARENA = {"features": [], "num_rows_total": 11175, "num_rows_per_page": 100, "partial": False, "rows": [
    _arena_row(1, "Gemini 4 Carbon (High)", "google", 1538.2768191996465, 4282),
    _arena_row(2, "Gemini 4 Argon (High)", "google", 1530.160034009476, 19787),
    _arena_row(3, "Claude Opus 5.5 (High)", "anthropic", 1513.0021399366474, 15249),
    _arena_row(4, "Claude Fable 5.1 (Max)", "anthropic", 1512.3784035606031, 20087),
    _arena_row(5, "Claude Opus 5 (Max)", "anthropic", 1506.3172184201153, 35710),
    _arena_row(6, "Muse Wonder (Max)", "meta", 1503.0484713809174, 12032),
    _arena_row(1, "Some Coder", "openai", 1400.0, 900, category="coding"),
]}
NPM_BULK = {"openai": {"downloads": 57910951, "package": "openai", "start": "2026-11-04", "end": "2026-11-10"},
            "langchain": {"downloads": 4060209, "package": "langchain", "start": "2026-11-04", "end": "2026-11-10"},
            "ai": {"downloads": 45363657, "package": "ai", "start": "2026-11-04", "end": "2026-11-10"}}
NPM_SCOPED = {"downloads": 68126267, "start": "2026-11-04", "end": "2026-11-10", "package": "@anthropic-ai/sdk"}
PYPI_TORCH = {"data": {"last_day": 1897790, "last_month": 58964343, "last_week": 13470975}, "package": "torch",
              "type": "recent_downloads"}


def _answers(url, **_options):
    if "datasets-server" in url:
        return ARENA
    if url.endswith("/@anthropic-ai/sdk"):
        return NPM_SCOPED
    if "api.npmjs.org" in url:
        return NPM_BULK
    package = url.rstrip("/").split("/")[-2]
    return {"data": {"last_week": {"torch": 13470975}.get(package, 1000000)}, "package": package}


def test_the_arenas_rows_give_the_overall_top_with_rank_maker_and_score():
    board = extras.parse_arena(ARENA)

    assert board["date"] == "2026-11-09" and len(board["models"]) == extras.ARENA_KEPT == 6
    assert board["models"][0] == {"rank": 1, "name": "Gemini 4 Carbon (High)", "maker": "google", "score": 1538,
                                  "votes": 4282}
    # Another category's first place is not the overall board's.
    assert [row["rank"] for row in board["models"]] == [1, 2, 3, 4, 5, 6]
    for broken in ({}, {"rows": []}, {"error": "503"}, None, {"rows": [{"row": {"model_name": "x"}}]}):
        with pytest.raises(ValueError):
            extras.parse_arena(broken)


def test_download_counts_read_npms_bulk_and_single_answers_and_pypis():
    assert extras.parse_npm(NPM_BULK) == {"openai": 57910951, "langchain": 4060209, "ai": 45363657}
    assert extras.parse_npm(NPM_SCOPED) == {"@anthropic-ai/sdk": 68126267}
    assert extras.parse_pypi(PYPI_TORCH) == {"torch": 13470975}
    for parse in (extras.parse_npm, extras.parse_pypi):
        with pytest.raises(ValueError):
            parse({"error": "package not found"})
    # A scoped package cannot ride in npm's bulk answer (it answers 400): it is asked alone.
    assert all(not name.startswith("@") for name in extras.NPM_BULK)
    assert extras.NPM_SINGLE == ("@anthropic-ai/sdk",)
    assert extras.count_text(57910951) == "5791万" and extras.count_text(123456789) == "1.2亿"
    assert extras.count_text(9500) == "9500"


def test_a_refresh_asks_the_arena_once_and_one_stale_count_per_registry():
    asked = []

    def fetch(url, **options):
        asked.append(url)
        return _answers(url)

    state = extras.refresh({}, NOW, fetch)
    assert asked == ["https://datasets-server.huggingface.co/rows",
                     "https://api.npmjs.org/downloads/point/last-week/openai,langchain,ai",
                     "https://pypistats.org/api/packages/openai/recent"]
    assert state["arena"]["models"][0]["score"] == 1538 and state["downloads"]["npm"]["openai"]["count"] == 57910951
    # Each later refresh asks one more stale count per registry until all are in.
    for _turn in range(5):
        state = extras.refresh(state, NOW + timedelta(minutes=30), fetch)
    assert set(state["downloads"]["npm"]) == {"openai", "langchain", "ai", "@anthropic-ai/sdk"}
    assert set(state["downloads"]["pypi"]) == set(extras.PYPI)
    assert asked.count("https://datasets-server.huggingface.co/rows") == 1
    asked.clear()
    assert extras.refresh(state, NOW + timedelta(hours=1), fetch) == state and asked == []
    # A day on, the counts are asked again, still one per registry per refresh; the arena after six hours.
    extras.refresh(state, NOW + timedelta(hours=25), fetch)
    assert len(asked) == 3


def test_a_provider_that_fails_keeps_its_last_good_copy():
    state = extras.refresh({}, NOW, lambda url, **_options: _answers(url))

    def down(url, **_options):
        raise OSError("503")

    later = extras.refresh(state, NOW + timedelta(days=3), down)
    assert later["arena"] == state["arena"] and later["downloads"] == state["downloads"]
    assert extras.refresh({}, NOW, down) == {"arena": {}, "downloads": {"npm": {}, "pypi": {}}}
    assert extras.refresh("junk", NOW, down) == {"arena": {}, "downloads": {"npm": {}, "pypi": {}}}


def test_the_strip_takes_turns_and_openrouter_keeps_half_of_them():
    full = extras.refresh({}, NOW, lambda url, **_options: _answers(url))
    slot = extras.TURN_MINUTES * 60
    start = datetime.fromtimestamp(slot * 4000, tz=timezone.utc)
    turns = [extras.strip_turn(start + timedelta(seconds=slot * index), full) for index in range(8)]
    assert turns == ["openrouter", "arena", "openrouter", "sdk"] * 2
    # A board without data gives its turn back to OpenRouter.
    assert {extras.strip_turn(start + timedelta(seconds=slot * index), {}) for index in range(4)} == {"openrouter"}
    assert {extras.strip_turn(start + timedelta(seconds=slot * index), None) for index in range(4)} == {"openrouter"}


def _plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    return TechPulse({"id": "tech_pulse"})


def test_the_plugin_keeps_the_extras_on_disk_and_a_theme_redraw_never_asks(monkeypatch, tmp_path):
    plugin = _plugin(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(extras, "fetch_json", lambda url, **options: calls.append(url) or _answers(url))

    assert plugin._extras(NOW, cache_only=True) is None and calls == []
    state = plugin._extras(NOW)
    assert state["arena"]["models"] and len(calls) == 3
    calls.clear()
    assert plugin._extras(NOW, cache_only=True) == state and calls == []

    def down(url, **_options):
        raise OSError("down")

    monkeypatch.setattr(extras, "fetch_json", down)
    assert plugin._extras(NOW + timedelta(days=2))["arena"] == state["arena"]


def _strip(plugin, turn, state, mode="day", models=None):
    settings = {"_inkypi_theme": plugin.resolve_theme({"themeMode": mode}, None)} if mode == "day" else {
        "_inkypi_theme": {"mode": "night", "palette": {"background": (0, 0, 0), "panel": (10, 12, 14), "ink": (238, 238, 231),
                                                        "muted": (176, 176, 168), "rule": (60, 62, 66), "accent": (229, 188, 88)}}}
    palette = plugin._palette(settings)
    image = Image.new("RGB", module.PAGE_SIZE, palette["background"])
    draw = ImageDraw.Draw(image)
    fonts = {name: plugin._load_font(module.DEFAULT_FONT, size, weight) for name, (size, weight) in module.FONT_SPECS.items()}
    drawn = []
    original = plugin._draw_text
    plugin._draw_text = lambda d, xy, text, font, fill, anchor=None: (drawn.append((str(text), fill)),
                                                                      original(d, xy, text, font, fill, anchor))[1]
    plugin._draw_bottom_strip(image, draw, module.OPENROUTER_BOX, turn, models, state, palette, fonts, NOW)
    return image, drawn, palette


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_arena_strip_lists_rank_maker_model_and_score(monkeypatch, tmp_path, mode):
    plugin = _plugin(monkeypatch, tmp_path)
    state = extras.refresh({}, NOW, lambda url, **_options: _answers(url))

    image, drawn, palette = _strip(plugin, "arena", state, mode)
    texts = [text for text, _fill in drawn]

    assert "LMArena" in texts and "文本总榜 · 11/09" in texts
    for label in ("Gemini 4 Carbon (High)", "1538", "Claude Opus 5.5 (High)", "1513"):
        assert label in texts, label
    assert len([text for text in texts if text in {"1", "2", "3", "4"}]) == 4
    # Small type is the page's ink or its muted tone, never a colour.
    assert {fill for _text, fill in drawn} <= {palette["ink"], palette["muted"]}
    # Google's own mark names the first model's maker.
    x0, y0, _x1, y1 = module.OPENROUTER_BOX
    card = image.crop((x0 + 148, y0 + 6, x0 + 300, y1 - 6))
    assert len(set(card.get_flattened_data())) > 8


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_sdk_strip_draws_each_packages_weekly_downloads_as_a_bar(monkeypatch, tmp_path, mode):
    plugin = _plugin(monkeypatch, tmp_path)
    state = extras.refresh({}, NOW, lambda url, **_options: _answers(url))
    for _turn in range(5):
        state = extras.refresh(state, NOW, lambda url, **_options: _answers(url))

    image, drawn, palette = _strip(plugin, "sdk", state, mode)
    texts = [text for text, _fill in drawn]

    assert "AI SDK 周下载" in texts and "npm" in texts and "PyPI" in texts
    for label in ("openai", "anthropic", "langchain", "torch", "transformers", "5791万", "6813万", "1347万"):
        assert label in texts, label
    assert {fill for _text, fill in drawn} <= {palette["ink"], palette["muted"]}
    # The bars carry the colour: npm's and PyPI's, the longest npm bar the scoped Anthropic SDK's.
    colours = set(image.crop(module.OPENROUTER_BOX).get_flattened_data())
    assert palette["down"] in colours and palette["cyan"] in colours


def test_openrouters_turn_and_a_page_without_extras_draw_the_strip_as_before(monkeypatch, tmp_path):
    plugin = _plugin(monkeypatch, tmp_path)
    models = {"week": 3, "models": [{"id": "openai/gpt-6.1-terra", "provider": "openai", "name": "GPT-6.1 Terra",
                                     "created": "2026-11-10T00:00:00+00:00", "context": 400000, "prompt": 2.0,
                                     "completion": 8.0, "vision": True, "hub": ""}]}
    state = extras.refresh({}, NOW, lambda url, **_options: _answers(url))
    before, _drawn, palette = _strip(plugin, "openrouter", None, models=models)
    fonts = {name: plugin._load_font(module.DEFAULT_FONT, size, weight) for name, (size, weight) in module.FONT_SPECS.items()}
    reference = Image.new("RGB", module.PAGE_SIZE, palette["background"])
    plugin._draw_openrouter(reference, ImageDraw.Draw(reference), module.OPENROUTER_BOX, models, palette, fonts, NOW)
    assert before.tobytes() == reference.tobytes()
    # A turn whose board is empty falls back to OpenRouter's strip too.
    empty, _drawn, _palette = _strip(plugin, "arena", {"arena": {}, "downloads": {"npm": {}, "pypi": {}}}, models=models)
    assert empty.tobytes() == reference.tobytes()
    assert _strip(plugin, "arena", state, models=models)[0].tobytes() != reference.tobytes()


def test_the_page_passes_the_turn_to_the_strip(monkeypatch, tmp_path):
    plugin = _plugin(monkeypatch, tmp_path)
    seen = []
    monkeypatch.setattr(plugin, "_draw_bottom_strip", lambda image, draw, box, turn, *_args, **_kwargs: seen.append(turn))
    monkeypatch.setattr(extras, "strip_turn", lambda now, state: "sdk" if state else "openrouter")
    settings = {"_inkypi_theme": plugin.resolve_theme({"themeMode": "day"}, None)}
    payload = {"stories": [], "status": {}}

    plugin._render_page((800, 480), payload, settings, NOW, extras={"arena": {}})
    plugin._render_page((800, 480), payload, settings, NOW)

    assert seen == ["sdk", "openrouter"]
