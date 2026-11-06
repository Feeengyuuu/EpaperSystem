import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.print_inspiration import render, sources  # noqa: E402
from plugins.print_inspiration.print_inspiration import PrintInspiration  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402

NOW = datetime(2026, 11, 6, 14, 20, tzinfo=sources.LOCAL_TZ)
PICTURE = "media/prints/7a946055/images/14223752_ddc8/v-image-22.jpg"


def raw(model_id="1890059", name="Silicone Cartridge Cap", **fields):
    return {"id": model_id, "name": name, "likesCount": 940, "downloadCount": 3079, "makesCount": 19,
            "ratingAvg": "4.8500000000", "ratingCount": 14, "commentCount": 35, "displayCount": 7808,
            "datePublished": "2026-10-25T11:37:47.412319+00:00",
            "summary": "keep your silicone cartridges sealed between uses",
            "category": {"path": [{"name": "Hobby & Makers"}, {"name": "Organizers"}]},
            "user": {"publicUsername": "VC Design", "avatarFilePath": "media/auth/avatars/2f/2f8b.png"},
            "image": {"filePath": PICTURE}, "gcodes": [{"id": "1"}], **fields}


def test_a_model_reads_with_its_thumbnail_counts_and_chinese_category():
    model = sources.normalize_model(raw())
    assert model["image"] == ("https://media.printables.com/media/prints/7a946055/images/14223752_ddc8/"
                              "thumbs/inside/640x480/jpg/v-image-22.jpg")
    assert (model["likes"], model["downloads"], model["makes"], model["rating"]) == (940, 3079, 19, 4.8)
    assert model["category"] == "爱好创客 · 收纳" and model["gcode"] and model["maker"] == "VC Design"
    assert model["avatar"].endswith("/media/auth/avatars/2f/2f8b.png")
    # A model without a picture, an id or a name is left out; an unknown category keeps its name.
    assert sources.normalize_model(raw(image={})) is None and sources.normalize_model(raw(model_id="x")) is None
    assert sources.category_name([{"name": "Robotics"}]) == "Robotics"


def test_only_printables_media_paths_and_host_are_used():
    assert sources.media_url("media/../../etc/passwd.jpg") is None
    assert sources.media_url("https://evil.test/a.jpg") is None
    assert sources.safe_image_url("https://media.printables.com/media/a.jpg")
    assert sources.safe_image_url("https://evil.test/media/a.jpg") is None
    assert sources.safe_image_url("http://media.printables.com/media/a.jpg") is None


def test_numbers_read_short():
    assert [render.count_text(value) for value in (75, 1520, 23400, 593296)] == ["75", "1.5k", "23k", "593k"]


def _payload(count=7):
    week = [sources.normalize_model(raw(str(1000 + index), f"Model {index} with a long descriptive name"))
            for index in range(count)]
    makes = [sources.normalize_model(raw(str(2000 + index), f"Printed {index}")) for index in range(5)]
    classics = [sources.normalize_model(raw(str(3000 + index), f"Classic {index}")) for index in range(3)]
    return {"week": week, "makes": makes, "classics": classics,
            "chinese": {"1000": {"title": "硅胶管保护帽", "summary": "用完的硅胶管封口，防止剩余胶体固化。"}}}


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_renders_with_and_without_lists(mode):
    settings = {"_inkypi_theme": {"mode": mode}}
    pictures = {model["image"]: Image.new("RGB", (640, 480), (200, 120, 60)) for model in _payload()["week"]}
    image = render.render_page((800, 480), _payload(), settings, NOW, pictures)
    assert image.size == (800, 480)
    # The lead's picture fills the left of its card.
    assert image.getpixel((150, 160))[:3] == (200, 120, 60)
    empty = render.render_page((800, 480), {"week": [], "makes": [], "classics": []}, settings, NOW, {})
    assert empty.size == (800, 480)


def test_the_lead_reads_in_chinese_with_its_original_name_beneath(monkeypatch):
    shown = []
    original = render.PageCanvas.text

    def record(canvas, xy, text, *args, **kwargs):
        shown.append(str(text))
        return original(canvas, xy, text, *args, **kwargs)

    monkeypatch.setattr(render.PageCanvas, "text", record)
    render.render_page((800, 480), _payload(), {}, NOW, {})
    assert "硅胶管保护帽" in shown and any(text.startswith("Model 0") for text in shown)


def test_the_plugin_keeps_its_lists_and_reads_them_back_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINT_INSPIRATION_CACHE_DIR", str(tmp_path))
    calls = []

    def fetch(key, context):
        calls.append(key)
        return {"models": [sources.normalize_model(raw(str(len(calls)), key))]}

    monkeypatch.setattr(sources, "fetch_list", fetch)
    plugin = PrintInspiration({"id": "print_inspiration"})
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 60)
    live = plugin._payload({}, NOW, allow_network=True, context=context)
    assert sorted(calls) == sorted(sources.LISTS) and live["week"][0]["name"] == "week"
    calls.clear()
    offline = plugin._payload({}, NOW, allow_network=False, context=context)
    assert calls == [] and offline["makes"][0]["name"] == "makes"


def test_a_thumbnail_keeps_its_originals_format_and_old_uploads_fall_back():
    png = sources.normalize_model(raw(image={"filePath": "media/prints/1/images/2/foto1.png"},
                                      images=[{"filePath": "media/prints/1/images/2/foto1.png"},
                                              {"filePath": "media/prints/1/images/3/side.jpg"}]))
    assert png["image"].endswith("/thumbs/inside/640x480/png/foto1.png")
    assert png["image_small"].endswith("/thumbs/inside/320x240/png/foto1.png")
    assert png["pictures"][1].endswith("/thumbs/inside/640x480/jpg/side.jpg")


def test_a_model_whose_cover_has_no_thumbnail_shows_its_next_picture(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINT_INSPIRATION_CACHE_DIR", str(tmp_path))
    from plugins.print_inspiration import print_inspiration as module

    model = sources.normalize_model(raw(images=[{"filePath": "media/prints/1/images/3/side.jpg"}]))
    loaded = Image.new("RGB", (4, 4))
    monkeypatch.setattr(module, "load_thumbnails",
                        lambda urls, *args, **kwargs: {urls[0]: loaded} if urls[0].endswith("side.jpg") else {})
    plugin = PrintInspiration({"id": "print_inspiration"})
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 60)
    images = plugin._images({"week": [model], "makes": [], "classics": []}, allow_network=True, context=context)
    assert images[model["image"]] is loaded
