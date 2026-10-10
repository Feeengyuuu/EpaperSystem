"""Content and image fidelity requirements for the selected Swiss page."""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_wiki_page.daily_wiki_page import DailyWikiPage  # noqa: E402
from plugins.daily_wiki_page import swiss_renderer  # noqa: E402
from plugins.daily_wiki_page.swiss_renderer import LayoutOverflowError  # noqa: E402


DAILY_URL = "https://media.example.test/daily.png"
HISTORY_URL = "https://media.example.test/history.png"
NOW = datetime(2026, 10, 6, 10, 0)
CORNER_COLORS = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 0, 255))


@pytest.fixture
def payload():
    return {
        "date": "2026-10-05",
        "language": "zh-cn",
        "source": "Wikimedia",
        "source_state": "fresh_cache",
        "image_url": DAILY_URL,
        "image_caption": (
            "Twin mills (front: green mill, 1856; rear: red mill or Schoof's mill, "
            "1706), Greetsiel, Krummhörn, Lower Saxony, Germany"
        ),
        "daily_image_title": "Greetsiel twin mills",
        "history_image_url": HISTORY_URL,
        "history_image_title": "糯康",
        "history_image_year": "2011",
        "on_this_day": [
            {"year": "2011", "text": "缅甸毒枭糯康及同伙在金三角一带的湄公河水域劫杀13名船员，引发多国政府介入。"},
            {"year": "1991", "text": "芬兰电脑程序员托瓦兹开发的Linux内核首个公开版本0.02版发布。"},
            {"year": "1962", "text": "披头士乐队首张单曲《Love Me Do》在英国正式发行。"},
            {"year": "1789", "text": "数千名不满物价高昂和面包短缺的妇女在法国巴黎发动大规模游行，并前往凡尔赛宫抗议。"},
            {"year": "610", "text": "希拉克略在君士坦丁堡加冕成为拜占庭皇帝，并亲自处决前任皇帝福卡斯。"},
        ],
    }


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    instance = DailyWikiPage({"id": "daily_wiki_page"})
    monkeypatch.setattr(instance, "_cache_dir", lambda create=True: tmp_path)
    monkeypatch.setattr(
        instance,
        "_download_image",
        lambda *_args, **_kwargs: Image.new("RGB", (320, 240), (80, 120, 160)),
    )
    return instance


def settings(mode="day", **overrides):
    return {
        "language": "zh-cn",
        "showImage": "true",
        "showOnThisDay": "true",
        "_inkypi_theme": {"mode": mode},
        **overrides,
    }


def compact(text):
    """Ignore only layout whitespace, never characters or punctuation."""
    return re.sub(r"\s+", "", str(text))


def assert_inside(inner, outer):
    x0, y0, x1, y1 = inner
    left, top, right, bottom = outer
    assert left <= x0 < x1 <= right
    assert top <= y0 < y1 <= bottom


def assert_disjoint(first, second):
    assert (
        first[2] <= second[0] or second[2] <= first[0]
        or first[3] <= second[1] or second[3] <= first[1]
    ), "Complete text must not be covered by the history image"


def assert_complete_text(block, source, drawn_text):
    assert block["complete"] is True
    assert compact(block["source_text"]) == compact(source)
    assert compact("".join(line["text"] for line in block["lines"])) == compact(source)
    previous_bottom = -1
    for line in block["lines"]:
        assert line["text"] in drawn_text, "Audit must describe text actually drawn"
        assert_inside(line["bounds"], block["bounds"])
        assert line["bounds"][1] >= previous_bottom
        previous_bottom = line["bounds"][3]


def corner_marked_image(size):
    image = Image.new("RGB", size, (245, 245, 245))
    draw = ImageDraw.Draw(image)
    width, height = size
    boxes = (
        (0, 0, width // 5, height // 5),
        (width - 1 - width // 5, 0, width - 1, height // 5),
        (0, height - 1 - height // 5, width // 5, height - 1),
        (width - 1 - width // 5, height - 1 - height // 5, width - 1, height - 1),
    )
    for box, color in zip(boxes, CORNER_COLORS):
        draw.rectangle(box, fill=color)
    return image


def assert_complete_image(image, record, source_size):
    assert record["fit"] == "contain"
    assert tuple(record["source_size"]) == source_size
    box = record["bounds"]
    assert_inside(box, (0, 0, 800, 480))
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    # A one-pixel resize rounding allowance, independent of the selected slot.
    assert abs(width * source_size[1] - height * source_size[0]) <= max(source_size)
    for (x_ratio, y_ratio), color in zip(
        ((0.08, 0.08), (0.92, 0.08), (0.08, 0.92), (0.92, 0.92)),
        CORNER_COLORS,
    ):
        pixel = image.getpixel((left + int((width - 1) * x_ratio), top + int((height - 1) * y_ratio)))
        assert max(abs(actual - expected) for actual, expected in zip(pixel, color)) <= 4


@pytest.mark.parametrize("mode", ["day", "night"])
def test_native_page_draws_all_five_events_and_complete_caption(plugin, payload, monkeypatch, mode):
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(self, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    image = plugin._render_page((800, 480), payload, settings(mode), NOW)
    audit = image.info["daily_wiki_layout"]

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == ((255, 255, 255) if mode == "day" else (0, 0, 0))
    assert audit["mode"] == mode
    assert audit["complete"] is True
    assert audit["omitted_events"] == []
    assert len(audit["events"]) == len(payload["on_this_day"]) == 5
    json.dumps(audit, ensure_ascii=False)

    previous_bottom = -1
    for index, (event, source) in enumerate(zip(audit["events"], payload["on_this_day"])):
        assert event["index"] == index
        assert str(event["year"]) == source["year"]
        assert event["font_size"] >= 14
        assert_inside(event["bounds"], (0, 0, 800, 480))
        assert event["bounds"][1] >= previous_bottom
        previous_bottom = event["bounds"][3]
        assert_complete_text(event, source["text"], drawn_text)
        if event["image"]:
            for line in event["lines"]:
                assert_disjoint(line["bounds"], event["image"]["bounds"])
    assert_complete_text(audit["caption"], payload["image_caption"], drawn_text)
    title = audit["header"]["title"]
    assert title["text"] == "每日图片"
    assert title["asset_used"] is True
    title_pixels = image.crop(title["bounds"]).convert("L")
    assert title_pixels.getextrema()[1] - title_pixels.getextrema()[0] >= 200
    assert "历史上的今天" in drawn_text
    assert "2026.10.05" in drawn_text
    assert "2026.10.06" not in drawn_text


def test_mixed_cjk_latin_and_unbroken_word_wrap_without_omissions(plugin, payload, monkeypatch):
    payload["on_this_day"][1]["text"] = (
        "1991年，Linux 0.02 正式发布。\n版本标识 "
        + "LongUnbrokenReleaseIdentifier" * 3
        + " 仍应完整展示，保留末尾标点。"
    )
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(self, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    image = plugin._render_page((800, 480), payload, settings(), NOW)
    audit = image.info["daily_wiki_layout"]
    assert len(audit["events"]) == 5
    for event, source in zip(audit["events"], payload["on_this_day"]):
        assert_complete_text(event, source["text"], drawn_text)


@pytest.mark.parametrize("source_size", [(320, 100), (100, 320)])
def test_history_image_is_complete_and_belongs_to_its_matching_year(plugin, payload, monkeypatch, source_size):
    payload["history_image_year"] = "1962"
    payload["history_image_title"] = "Love Me Do"
    source = corner_marked_image(source_size)
    calls = []

    def download(url, target_size, _settings):
        calls.append((url, target_size))
        return source.copy() if url == HISTORY_URL else Image.new("RGB", (320, 240), (80, 120, 160))

    monkeypatch.setattr(plugin, "_download_image", download)
    image = plugin._render_page((800, 480), payload, settings(), NOW)
    events = image.info["daily_wiki_layout"]["events"]
    pictured = [event for event in events if event.get("image")]
    assert len(pictured) == 1
    assert pictured[0]["index"] == 2
    assert str(pictured[0]["year"]) == "1962"
    assert_inside(pictured[0]["image"]["bounds"], pictured[0]["bounds"])
    assert_complete_image(image, pictured[0]["image"], source_size)
    assert sum(url == HISTORY_URL for url, _size in calls) == 1


@pytest.mark.parametrize("source_size", [(360, 100), (100, 360)])
def test_daily_photograph_preserves_all_four_corners_and_source_aspect(plugin, payload, monkeypatch, source_size):
    source = corner_marked_image(source_size)

    def download(url, _target_size, _settings):
        return source.copy() if url == DAILY_URL else Image.new("RGB", (100, 120), (80, 120, 160))

    monkeypatch.setattr(plugin, "_download_image", download)
    image = plugin._render_page((800, 480), payload, settings(), NOW)
    audit = image.info["daily_wiki_layout"]
    photograph = audit["regions"]["daily_image"]
    assert_complete_image(image, photograph, source_size)
    assert photograph["bounds"][3] < audit["caption"]["bounds"][1]


@pytest.mark.parametrize("unknown_year", [None, "", "2000"])
def test_history_image_with_unknown_year_is_not_attached_to_another_event(plugin, payload, unknown_year):
    payload["history_image_year"] = unknown_year
    with pytest.raises(LayoutOverflowError):
        plugin._render_page((800, 480), payload, settings(), NOW)


@pytest.mark.parametrize("use_provider_index", [True, False])
def test_provider_image_for_second_same_year_event_keeps_its_exact_identity(plugin, use_provider_index):
    feed = {
        "tfa": {"titles": {"normalized": "Reference article"}, "extract": "Reference text."},
        "image": {"thumbnail": {"source": DAILY_URL}, "description": {"text": "Twin mills."}},
        "onthisday": [
            {"year": 1991, "text": "The institute opened a new research building."},
            {
                "year": 1991,
                "text": "A composer released the Second concerto.",
                "pages": [{"titles": {"normalized": "Second concerto"}, "thumbnail": {"source": HISTORY_URL}}],
            },
        ],
    }
    source_payload = plugin._payload_from_feed(feed, "en", {})
    assert source_payload["history_image_event_index"] == 1
    assert source_payload["history_image_year"] == "1991"
    if not use_provider_index:
        # Old caches have no event index; the unique title still identifies row 2.
        source_payload.pop("history_image_event_index")
    image = plugin._render_page((800, 480), source_payload, settings(), NOW)
    events = image.info["daily_wiki_layout"]["events"]
    assert len(events) == 2
    assert events[0]["image"] is None
    assert events[1]["image"]["event_year"] == "1991"
    assert events[1]["source_text"] == feed["onthisday"][1]["text"]


def test_ambiguous_legacy_same_year_image_is_not_guessed(plugin, payload):
    payload["on_this_day"][0]["year"] = "1991"
    payload["history_image_year"] = "1991"
    payload["history_image_title"] = "An unrelated photograph"
    with pytest.raises(LayoutOverflowError):
        plugin._render_page((800, 480), payload, settings(), NOW)


@pytest.mark.parametrize("invalid_index", [-1, 20, "1", True])
def test_invalid_explicit_event_index_is_rejected(plugin, payload, invalid_index):
    payload["history_image_event_index"] = invalid_index
    with pytest.raises(LayoutOverflowError):
        plugin._render_page((800, 480), payload, settings(), NOW)


def test_explicit_event_index_cannot_override_mismatched_source_year(plugin, payload):
    payload["history_image_event_index"] = 1
    assert payload["on_this_day"][1]["year"] != payload["history_image_year"]
    with pytest.raises(LayoutOverflowError):
        plugin._render_page((800, 480), payload, settings(), NOW)


def october_tenth_payload(payload):
    """The live zh-cn source of 2026-10-10, whose enriched 1911 entry overflowed."""
    payload.update(
        date="2026-10-10",
        image_caption=(
            "Bust of Germanicus, Getty Villa, California. The young Germanicus is depicted "
            "before the Roman rite of depositio barbae, the first shaving of the beard. "
            "Adopted by emperor Tiberius, Germanicus should succed him as Emperor, if he "
            "hadn't died on this day 2007 years ago."
        ),
        history_image_title="卡萊斯·普吉德蒙",
        history_image_year="2017",
        history_image_event_index=0,
        on_this_day=[
            {"year": "2017", "text": "加泰罗尼亚政府主席卡莱斯·普吉德蒙签署《加泰罗尼亚独立宣言》，然而随即宣布暂缓独立进程。"},
            {"year": "1964", "text": "第十八届夏季奥林匹克运动会在日本东京开幕，首次使用人造卫星向全世界直播奥运实况。"},
            {"year": "1911", "text": (
                "中国湖北省武昌革命组织文学社和共进会发动武昌起义，随后演变为推翻清朝政府的辛亥革命；"
                "因中国各地革命行动成功，孙中山回中国肇建了中华民国，形成孙中山领导的南京政府及清朝"
                "袁世凯的北京政府南北对峙，直到袁世凯翌年逼清帝退位，中华民国始取代清朝。"
            )},
            {"year": "1780", "text": "历史上最致命的大西洋飓风袭击加勒比海地区，最终共造成至少22,000人死亡。"},
            {"year": "732", "text": "查理·马特率领的法兰克军队在图尔战役中击败拉赫曼率领的倭马亚军队。"},
        ],
    )
    return payload


def portrait_history_download(source_size=(224, 300)):
    """Serve a portrait history thumbnail, as the 2026-10-10 source did."""
    def download(url, _target_size, _settings):
        if url == HISTORY_URL:
            return corner_marked_image(source_size)
        return Image.new("RGB", (320, 240), (80, 120, 160))
    return download


def assert_history_rows_do_not_overlap(audit):
    history = audit["regions"]["history"]["bounds"]
    previous_bottom = -1
    for event in audit["events"]:
        assert_inside(event["bounds"], history)
        assert event["bounds"][1] >= previous_bottom
        previous_bottom = event["bounds"][3]
        for line in event["lines"]:
            assert_inside(line["bounds"], event["bounds"])
        if event["image"]:
            assert_inside(event["image"]["bounds"], event["bounds"])


def test_long_enriched_history_day_renders_every_event_at_a_legible_size(plugin, payload, monkeypatch):
    """Regression for 2026-10-10: 341px of history in a 319px column raised at 13px."""
    payload = october_tenth_payload(payload)
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(self, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(plugin, "_download_image", portrait_history_download())
    image = plugin._render_page((800, 480), payload, settings(), NOW)
    audit = image.info["daily_wiki_layout"]

    assert audit["complete"] is True
    assert audit["omitted_events"] == []
    assert len(audit["events"]) == 5
    for event, source in zip(audit["events"], payload["on_this_day"]):
        assert event["font_size"] >= swiss_renderer.MIN_BODY_FONT_SIZE
        assert_complete_text(event, source["text"], drawn_text)
        if event["image"]:
            for line in event["lines"]:
                assert_disjoint(line["bounds"], event["image"]["bounds"])
    assert_history_rows_do_not_overlap(audit)
    pictured = [event for event in audit["events"] if event["image"]]
    assert [event["year"] for event in pictured] == ["2017"]
    assert_complete_image(image, pictured[0]["image"], (224, 300))
    assert_complete_text(audit["caption"], payload["image_caption"], drawn_text)


def test_long_daily_image_caption_keeps_every_word_over_a_shorter_photograph(plugin, payload, monkeypatch):
    """Regression for 01-25: a 463-character caption left the photo under 200px at 13px."""
    payload["image_caption"] = (
        "This stained glass window from Eglise Sainte-Madeleine, a church in Gramond, France, "
        "depicts Saints Victor of Damascus and Paul the Apostle. Though they were not "
        "contemporaries, both men have a connection to Damascus. Moreover, legend has it that "
        "each were martyred by beheading, hence they are displayed holding swords. Today is the "
        "feast of The Conversion of St. Paul and the conclusion of the Week of Prayer for "
        "Christian Unity in much of Western Christianity."
    )
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(self, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    image = plugin._render_page((800, 480), payload, settings(), NOW)
    audit = image.info["daily_wiki_layout"]

    assert_complete_text(audit["caption"], payload["image_caption"], drawn_text)
    assert audit["caption"]["font_size"] >= swiss_renderer.MIN_BODY_FONT_SIZE
    photo = audit["regions"]["daily_image"]["bounds"]
    assert photo[3] - photo[1] >= 120
    assert photo[3] <= audit["caption"]["bounds"][1]


def test_over_capacity_content_fails_explicitly_instead_of_dropping_events(plugin, payload):
    payload["on_this_day"][-1]["text"] = "不可悄悄删除的完整历史正文。" * 300
    with pytest.raises(LayoutOverflowError):
        plugin._render_page((800, 480), payload, settings(), NOW)


def test_over_capacity_caption_fails_explicitly_instead_of_truncating(plugin, payload):
    payload["image_caption"] = "Complete photograph description must remain intact. " * 200
    with pytest.raises(LayoutOverflowError, match="caption"):
        plugin._render_page((800, 480), payload, settings(), NOW)


@pytest.mark.parametrize("missing_role", ["daily_image", "history_image"])
def test_missing_requested_media_reports_incomplete_without_losing_text(plugin, payload, monkeypatch, missing_role):
    payload["language"] = "en"
    missing_url = DAILY_URL if missing_role == "daily_image" else HISTORY_URL
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(self, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    monkeypatch.setattr(
        plugin, "_download_image",
        lambda url, *_args: None if url == missing_url else Image.new("RGB", (320, 240), (80, 120, 160)),
    )
    image = plugin._render_page((800, 480), payload, settings(), NOW)
    audit = image.info["daily_wiki_layout"]
    assert audit["media_requested"][missing_role] is True
    assert audit["media_available"][missing_role] is False
    assert audit["complete"] is False
    assert audit["text_complete"] is True
    assert audit["omitted_events"] == []
    assert len(audit["events"]) == 5
    for event, source in zip(audit["events"], payload["on_this_day"]):
        assert_complete_text(event, source["text"], drawn_text)
    assert_complete_text(audit["caption"], payload["image_caption"], drawn_text)
    if missing_role == "daily_image":
        assert "Image unavailable" in drawn_text
    else:
        status = audit["regions"]["history_image_status"]
        assert status["text"] == "History image unavailable"
        assert status["text"] in drawn_text
        assert_disjoint(status["bounds"], audit["header"]["history"]["bounds"])
        assert all(event["image"] is None for event in audit["events"])


def test_disabled_media_and_history_do_not_download_or_draw_hidden_content(plugin, payload, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Disabled image/history must not acquire media")

    monkeypatch.setattr(plugin, "_download_image", forbidden)
    image = plugin._render_page(
        (800, 480), payload, settings(showImage="false", showOnThisDay="false"), NOW,
    )
    assert image.size == (800, 480)
    audit = image.info["daily_wiki_layout"]
    assert audit["events"] == []
    assert audit["media_requested"] == {"daily_image": False, "history_image": False}
    assert audit["complete"] is True


def test_native_wordmark_alpha_is_preserved_and_day_night_shapes_match(plugin, payload):
    asset_path = swiss_renderer.TITLE_ASSET_PATH
    before = hashlib.sha256(asset_path.read_bytes()).hexdigest()
    with Image.open(asset_path) as native:
        assert native.mode == "RGBA"
        source_size = native.size
        alpha = native.getchannel("A")
        assert alpha.getextrema() == (0, 255)
        alpha_bounds = alpha.getbbox()
        assert alpha_bounds is not None
        assert alpha_bounds[0] > 0 and alpha_bounds[1] > 0
        assert alpha_bounds[2] < source_size[0] and alpha_bounds[3] < source_size[1]

    day = plugin._render_page((800, 480), payload, settings("day"), NOW)
    night = plugin._render_page((800, 480), payload, settings("night"), NOW)
    day_title = day.info["daily_wiki_layout"]["header"]["title"]
    night_title = night.info["daily_wiki_layout"]["header"]["title"]
    assert day_title["asset_used"] is night_title["asset_used"] is True
    assert day_title["bounds"] == night_title["bounds"]
    assert tuple(day_title["source_size"]) == source_size
    assert tuple(day_title["alpha_source_bounds"]) == alpha_bounds
    day_mask = ImageOps.invert(day.crop(day_title["bounds"]).convert("L"))
    night_mask = night.crop(night_title["bounds"]).convert("L")
    assert ImageChops.difference(day_mask, night_mask).getbbox() is None
    # Both glyphs and internal transparent counters remain distinguishable.
    assert day_mask.getextrema()[0] <= 5
    assert day_mask.getextrema()[1] >= 245
    assert hashlib.sha256(asset_path.read_bytes()).hexdigest() == before


def test_swiss_typography_uses_real_noto_variable_weights(plugin, payload, monkeypatch):
    assert swiss_renderer.SWISS_FONT_PATH.is_file()
    original_set_axes = ImageFont.FreeTypeFont.set_variation_by_axes
    applied_weights = []

    def record_axes(self, axes):
        for axis, value in zip(self.get_variation_axes(), axes):
            if axis["name"] == b"Weight":
                applied_weights.append(value)
        return original_set_axes(self, axes)

    monkeypatch.setattr(ImageFont.FreeTypeFont, "set_variation_by_axes", record_axes)
    image = plugin._render_page((800, 480), payload, settings(), NOW)
    typography = image.info["daily_wiki_layout"]["typography"]
    assert typography["family"] == "Noto Sans SC"
    assert typography["font_file"] == "NotoSansSC-VF.ttf"
    assert typography["weights"]["heading"] == 900
    assert typography["weights"]["body"] == 500
    assert typography["fallback_used"] is False
    assert {500, 800, 900}.issubset(applied_weights)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_missing_wordmark_uses_visible_real_heavy_font(plugin, payload, monkeypatch, tmp_path, mode):
    monkeypatch.setattr(swiss_renderer, "TITLE_ASSET_PATH", tmp_path / "missing-wordmark.png")
    drawn_titles = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(self, xy, text, *args, **kwargs):
        if text == "每日图片":
            drawn_titles.append(kwargs.get("font"))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    image = plugin._render_page((800, 480), payload, settings(mode), NOW)
    audit = image.info["daily_wiki_layout"]
    title = audit["header"]["title"]
    assert title["asset_used"] is False
    assert title["font_weight"] == 900
    assert audit["typography"]["fallback_used"] is False
    assert len(drawn_titles) == 1
    assert isinstance(drawn_titles[0], ImageFont.FreeTypeFont)
    assert Path(drawn_titles[0].path).name == "NotoSansSC-VF.ttf"
    pixels = image.crop(title["bounds"]).convert("L")
    assert pixels.getextrema()[1] - pixels.getextrema()[0] >= 200
    assert audit["complete"] is True


def test_missing_swiss_font_uses_shared_fallback_without_losing_content(plugin, payload, monkeypatch, tmp_path):
    monkeypatch.setattr(swiss_renderer, "SWISS_FONT_PATH", tmp_path / "missing-font.ttf")
    image = plugin._render_page((800, 480), payload, settings(), NOW)
    audit = image.info["daily_wiki_layout"]
    assert audit["typography"]["fallback_used"] is True
    assert audit["complete"] is True
    assert len(audit["events"]) == len(payload["on_this_day"]) == 5
    for event, source in zip(audit["events"], payload["on_this_day"]):
        assert compact("".join(line["text"] for line in event["lines"])) == compact(source["text"])
