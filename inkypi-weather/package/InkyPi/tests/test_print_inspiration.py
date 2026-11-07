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


GALLERY = ["media/prints/7a946055/images/2_b/second.jpg", "media/prints/7a946055/images/3_c/third.png",
           "media/prints/7a946055/images/4_d/fourth.jpg", "media/prints/7a946055/images/5_e/fifth.jpg"]


def rich(**fields):
    return raw(gcodes=[{"id": "9", "printer": {"name": "Prusa MK4"}, "material": {"name": "PETG"}, "printDuration": "1.05",
                        "weight": 13, "layerHeight": "0.15", "nozzleDiameter": "0.40"}],
               tags=[{"name": "reuse"}, {"name": "cartridge"}, {"name": "cap"}], remixCount=4, mmu=True,
               license={"name": "Creative Commons — Attribution  — Noncommercial  — Share Alike"},
               stls=[{"id": "1"}, {"id": "2"}], slas=[], otherFiles=[{"id": "3"}],
               user={"publicUsername": "VC Design", "avatarFilePath": "media/auth/avatars/2f/2f8b.png", "level": 24,
                     "followersCount": 20327},
               images=[{"filePath": PICTURE}] + [{"filePath": path} for path in GALLERY], **fields)


def test_a_model_carries_its_print_settings_files_licence_tags_and_makers_standing():
    model = sources.normalize_model(rich())

    assert model["print"] == {"printer": "Prusa MK4", "material": "PETG", "minutes": 63, "weight": 13, "layer": "0.15",
                              "nozzle": "0.40"}
    assert (model["remixes"], model["files"], model["multi_material"], model["license"]) == (4, 4, True, "CC BY-NC-SA")
    assert model["tags"] == ["reuse", "cartridge", "cap"]
    assert (model["maker_level"], model["maker_followers"]) == (24, 20327)
    # The lead's gallery: the next three pictures after the cover, as small thumbnails.
    assert len(model["gallery"]) == 3 and model["gallery"][0].endswith("/thumbs/inside/320x240/jpg/second.jpg")
    plain = sources.normalize_model(raw())
    assert plain["print"] == {} and plain["license"] == "" and plain["gallery"] == [] and plain["tags"] == []


def test_licences_read_short():
    assert [sources.license_label(name) for name in (
        "Creative Commons — Attribution", "Creative Commons — Attribution  — Noncommercial",
        "Creative Commons — Attribution  — No Derivatives", "Standard Digital File License",
        "Creative Commons — Public Domain", "GNU General Public License v3.0", "")] == [
        "CC BY", "CC BY-NC", "CC BY-ND", "标准授权", "CC0", "GPL", ""]


def test_print_settings_and_files_read_as_short_lines():
    model = sources.normalize_model(rich())
    assert render.duration_text(63) == "1小时3分" and render.duration_text(45) == "45分" and render.duration_text(0) == ""
    assert render.print_specs(model) == ["PETG", "1小时3分", "13 g", "层高 0.15", "Prusa MK4"]
    assert render.card_line(model) == "PETG · 1小时3分 · 13 g"
    plain = sources.normalize_model(raw(stls=[{"id": "1"}], gcodes=[], license={"name": "Creative Commons — Attribution"}))
    assert render.print_specs(plain) == [] and render.card_line(plain) == "1 个文件 · CC BY"
    assert render.maker_line(model) == "Lv24 · 2.0万关注" and render.maker_line(plain) == ""


def test_the_header_sums_the_weeks_list():
    payload = {"week": [sources.normalize_model(raw(str(1000 + index))) for index in range(7)]}
    assert render.week_totals(payload) == "本周热门 7 款 · 合计 6.6k 赞 · 133 次打印 · 21k 下载"
    assert render.week_totals({"week": []}) == ""


def test_the_page_shows_the_new_details(monkeypatch):
    shown = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda canvas, xy, text, *args, **kwargs: shown.append(str(text))
                        or original(canvas, xy, text, *args, **kwargs))
    payload = _payload()
    payload["week"][0] = sources.normalize_model(rich(id="1000"))
    payload["week"][1] = sources.normalize_model(rich(id="1001"))
    gallery = {url: Image.new("RGB", (320, 240), (20, 160, 90)) for url in payload["week"][0]["gallery"]}

    page = render.render_page((800, 480), payload, {}, NOW, gallery)

    assert any(text.startswith("本周热门 7 款") for text in shown)
    assert {"PETG", "1小时3分", "13 g", "Prusa MK4", "Lv24 · 2.0万关注", "PETG · 1小时3分 · 13 g"} <= set(shown)
    assert any("4 改版" in text for text in shown) and any("CC BY-NC-SA" in text for text in shown)
    # The gallery strip beside the lead's picture shows its next pictures.
    strip = page.crop((render.LEAD[0], render.LEAD[1], render.LEAD[0] + render.LEAD_PICTURE[0] + 10, render.LEAD[3]))
    assert (20, 160, 90) in set(strip.convert("RGB").get_flattened_data())


def test_the_leads_gallery_pictures_are_fetched_small(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINT_INSPIRATION_CACHE_DIR", str(tmp_path))
    from plugins.print_inspiration import print_inspiration as module

    asked = []
    monkeypatch.setattr(module, "load_thumbnails", lambda urls, *args, **kwargs: asked.append(
        (tuple(urls), kwargs.get("size"))) or {url: Image.new("RGB", (4, 4)) for url in urls})
    lead = sources.normalize_model(rich())
    plugin = PrintInspiration({"id": "print_inspiration"})
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 60)

    images = plugin._images({"week": [lead], "makes": [], "classics": []}, allow_network=True, context=context)

    assert all(url in images for url in lead["gallery"])
    assert (tuple(lead["gallery"]), module.SMALL_PICTURE_PIXELS) in asked


def test_a_lead_without_print_settings_shows_its_tags_instead(monkeypatch):
    shown = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda canvas, xy, text, *args, **kwargs: shown.append(str(text))
                        or original(canvas, xy, text, *args, **kwargs))
    payload = _payload()
    payload["week"][0] = {**sources.normalize_model(rich(id="1000")), "print": {}, "gcode": False}

    render.render_page((800, 480), payload, {}, NOW, {})

    assert {"#reuse", "#cartridge", "#cap"} <= set(shown) and "PETG" not in shown
