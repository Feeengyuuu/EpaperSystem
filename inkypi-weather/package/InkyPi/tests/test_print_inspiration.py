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
COVER = "https://makerworld.bblmw.com/makerworld/model/US84481648c4b3be/design/8eac1f8389ec30b4.png"


def hit(model_id="3532319", title="Christmas Tree Ornament Train to Hogwarts", **fields):
    """A design as MakerWorld's search lists it."""
    return {"id": int(model_id) if model_id.isdigit() else model_id, "title": title, "cover": COVER,
            "likeCount": 1142, "downloadCount": 2088, "printCount": 1164, "collectionCount": 4107,
            "commentCount": 11, "readCount": 0, "createTime": "2026-11-01T23:51:11Z",
            "license": "Standard Digital File License", "isStaffPicked": False,
            "tags": ["hogwarts", "Train", "train", "flyingcar"],
            "designCreator": {"uid": 140291614, "name": "Hesic",
                              "avatar": "https://makerworld.bblmw.com/makerworld/user/20260329/140291614/e0253.jpg"},
            **fields}


DESIGN = {
    "id": 3532319, "title": "Christmas Tree Ornament Train to Hogwarts", "coverUrl": COVER,
    "summary": "<p>Christmas Tree Ornament Train to <b>Hogwarts</b></p><boostme><boosttitle>Boost Me</boosttitle>"
               "<boostcontent>Thank you</boostcontent></boostme>",
    "categories": [{"id": 401, "name": "Decor"}, {"id": 400, "name": "Household"}],
    "defaultInstanceId": 4038925,
    "instances": [
        {"id": 4038925, "title": "0.2mm layer, 2 walls, 15% infill", "prediction": 2415, "weight": 10, "needAms": True,
         "instanceFilaments": [{"type": "PLA", "color": "#F7F3F0"}, {"type": "PLA", "color": "#C12E1F"}],
         "ratingScoreTotal": 47, "ratingCount": 10},
        {"id": 4038926, "title": "0.16mm layer", "prediction": 3600, "weight": 12, "ratingScoreTotal": 0, "ratingCount": 0}],
    "designExtension": {"design_pictures": [
        {"url": COVER},
        {"url": "https://makerworld.bblmw.com/makerworld/model/US84481648c4b3be/design/ba67fceb989e6423.jpg"},
        {"url": "https://makerworld.bblmw.com/makerworld/model/US84481648c4b3be/design/second.jpg"},
        {"url": "https://makerworld.bblmw.com/makerworld/model/US84481648c4b3be/design/third.jpg"},
        {"url": "https://makerworld.bblmw.com/makerworld/model/US84481648c4b3be/design/fourth.jpg"}]},
    "designCreator": {"name": "Hesic", "level": 30, "fanCount": 20327},
}


def test_a_design_reads_with_its_resized_cover_counts_and_maker():
    model = sources.normalize_hit(hit())
    assert model["image"] == COVER + "?x-oss-process=image/resize,w_480"
    assert model["image_small"] == COVER + "?x-oss-process=image/resize,w_200"
    assert (model["likes"], model["downloads"], model["makes"], model["collections"]) == (1142, 2088, 1164, 4107)
    assert (model["maker"], model["license"], model["published"]) == ("Hesic", "标准授权", "2026-11-01T23:51:11+00:00")
    assert model["avatar"].endswith("e0253.jpg?x-oss-process=image/resize,w_64")
    assert model["tags"] == ["hogwarts", "Train", "train", "flyingcar"]
    # A design without a cover on MakerWorld's hosts, an id or a title is left out.
    assert sources.normalize_hit(hit(cover="https://evil.test/a.png")) is None
    assert sources.normalize_hit(hit(model_id="x")) is None and sources.normalize_hit(hit(title="")) is None


def test_only_makerworlds_picture_hosts_are_used():
    assert sources.safe_image_url("https://makerworld.bblmw.com/makerworld/a.png")
    assert sources.safe_image_url("https://public-cdn.bblmw.com/avatar.png")
    assert sources.safe_image_url("https://evil.test/makerworld.bblmw.com.png") is None
    assert sources.safe_image_url("http://makerworld.bblmw.com/a.png") is None


def test_licences_read_short():
    assert [sources.license_label(name) for name in (
        "Standard Digital File License", "CC0", "BY-NC", "BY-NC-SA", "CC BY", "MakerWorld Exclusive License",
        "GNU General Public License v3.0", "")] == [
        "标准授权", "CC0", "CC BY-NC", "CC BY-NC-SA", "CC BY", "独家授权", "GPL", ""]


def test_the_leads_page_adds_its_summary_category_pictures_and_print_profile():
    details = sources.design_details(DESIGN)

    assert details["summary"] == "Christmas Tree Ornament Train to Hogwarts"
    assert details["category"] == "家居 · 装饰"
    # The next three pictures after the cover, small.
    assert details["gallery"] == [url + "?x-oss-process=image/resize,w_200" for url in (
        "https://makerworld.bblmw.com/makerworld/model/US84481648c4b3be/design/ba67fceb989e6423.jpg",
        "https://makerworld.bblmw.com/makerworld/model/US84481648c4b3be/design/second.jpg",
        "https://makerworld.bblmw.com/makerworld/model/US84481648c4b3be/design/third.jpg")]
    # The default profile: two PLA colours with the AMS, 40 minutes, 10 g, 0.2 mm layers.
    assert details["print"] == {"material": "PLA", "colors": 2, "minutes": 40, "weight": 10, "layer": "0.2", "ams": True}
    assert (details["rating"], details["ratings"], details["files"], details["multi_material"]) == (4.7, 10, 2, True)
    assert (details["maker_level"], details["maker_followers"]) == (30, 20327)
    assert sources.design_details({}) == {}


def test_categories_read_in_chinese_parent_first():
    assert sources.category_name([{"id": 401, "name": "Decor"}, {"id": 400, "name": "Household"}]) == "家居 · 装饰"
    # An "Other ..." sub-category says nothing its parent does not.
    assert sources.category_name([{"id": 605, "name": "Other Miniatures"}, {"id": 600, "name": "Miniatures"}]) == "微缩模型"
    assert sources.category_name([{"id": 104, "name": "Sculptures"}, {"id": 100, "name": "Art"}]) == "艺术 · 雕塑"
    # A category MakerWorld adds later keeps its own name.
    assert sources.category_name([{"id": 3001, "name": "Lamps"}]) == "Lamps"
    assert sources.category_name([]) == ""


def test_the_lists_lead_with_the_trend_and_take_the_most_printed_from_the_rest():
    pool = [sources.normalize_hit(hit(str(1000 + index), f"Trend {index}", printCount=index * 10)) for index in range(12)]
    classics = [sources.normalize_hit(hit("9001", "Benchy"))]

    lists = sources.lists(pool, classics, {"1000": sources.design_details(DESIGN),
                                           "1002": {**sources.design_details(DESIGN), "summary": "Card two"}})

    assert [model["name"] for model in lists["week"]] == [f"Trend {index}" for index in range(7)]
    assert lists["week"][0]["summary"] and lists["week"][0]["category"] == "家居 · 装饰"
    # Not one of the five the lead and its cards show; the most printed first.
    assert [model["name"] for model in lists["makes"]] == ["Trend 11", "Trend 10", "Trend 9", "Trend 8", "Trend 7"]
    assert lists["classics"][0]["name"] == "Benchy"
    # Every model the lead and cards show takes its own page's details.
    assert lists["week"][2]["summary"] == "Card two" and lists["week"][2]["print"]["material"] == "PLA"
    assert lists["week"][1]["print"] == {}


def test_numbers_and_print_specs_read_short():
    assert [render.count_text(value) for value in (75, 1520, 23400, 593296)] == ["75", "1.5k", "23k", "593k"]
    model = {**sources.normalize_hit(hit()), **sources.design_details(DESIGN)}
    assert render.duration_text(40) == "40分" and render.duration_text(63) == "1小时3分"
    assert render.print_specs(model) == ["PLA", "40分", "10 g", "层高 0.2", "2 色 AMS"]
    assert render.card_line(model) == "PLA · 40分 · 10 g"
    # The lead's spec sheet: time, filament, layer height and colours, each with its glyph.
    assert render.spec_cells(model) == [("clock", "40分"), ("spool", "PLA · 10 g"), ("layers", "层高 0.2 mm"),
                                        ("colours", "2 色 AMS")]
    assert render.spec_cells(sources.normalize_hit(hit())) == []
    assert render.maker_line(model) == "Lv30 · 2.0万关注"
    plain = sources.normalize_hit(hit())
    assert render.print_specs(plain) == [] and render.card_line(plain) == "标准授权" and render.maker_line(plain) == ""


def _payload(count=7):
    week = [sources.normalize_hit(hit(str(1000 + index), f"Model {index} with a long descriptive name"))
            for index in range(count)]
    if week:
        week[0].update(sources.design_details(DESIGN))
    makes = [sources.normalize_hit(hit(str(2000 + index), f"Printed {index}")) for index in range(5)]
    classics = [sources.normalize_hit(hit(str(3000 + index), f"Classic {index}")) for index in range(3)]
    return {"week": week, "makes": makes, "classics": classics,
            "chinese": {"1000": {"title": "霍格沃茨圣诞火车挂饰", "summary": "挂在圣诞树上的霍格沃茨小火车。"}}}


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_renders_with_and_without_lists(mode):
    settings = {"_inkypi_theme": {"mode": mode}}
    pictures = {model["image"]: Image.new("RGB", (480, 360), (200, 120, 60)) for model in _payload()["week"]}
    image = render.render_page((800, 480), _payload(), settings, NOW, pictures)
    assert image.size == (800, 480)
    # The lead's picture fills the left of its card.
    assert image.getpixel((150, 160))[:3] == (200, 120, 60)
    empty = render.render_page((800, 480), {"week": [], "makes": [], "classics": []}, settings, NOW, {})
    assert empty.size == (800, 480)


def test_the_page_names_makerworld_and_shows_the_leads_details(monkeypatch):
    shown = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda canvas, xy, text, *args, **kwargs: shown.append(str(text))
                        or original(canvas, xy, text, *args, **kwargs))
    payload = _payload()
    gallery = {url: Image.new("RGB", (200, 150), (20, 160, 90)) for url in payload["week"][0]["gallery"]}

    page = render.render_page((800, 480), payload, {}, NOW, gallery)

    assert render.load_source_logo(render.WORDMARK, 16) is not None
    assert "霍格沃茨圣诞火车挂饰" in shown
    assert {"PLA · 10 g", "40分", "层高 0.2 mm", "2 色 AMS", "Lv30 · 2.0万关注"} <= set(shown)
    assert any(text.startswith("当前热门 7 款") for text in shown)
    strip = page.crop((render.LEAD[0], render.LEAD[1], render.LEAD[0] + render.LEAD_PICTURE[0] + 10, render.LEAD[3]))
    assert (20, 160, 90) in set(strip.convert("RGB").get_flattened_data())
    # The page's solid marks wear MakerWorld's green.
    assert render.BRAND in set(page.convert("RGB").get_flattened_data())


@pytest.mark.parametrize("mode", ["day", "night"])
def test_ranking_numbers_leave_every_model_picture_unobstructed(monkeypatch, mode):
    from PIL import ImageChops

    payload = _payload()
    for index, model in enumerate(payload["classics"]):
        model.update(rank=22 + index, of=30)
    picture = Image.new("RGB", (200, 150), (47, 86, 123))
    images = {model["image"]: picture for group in ("week", "makes", "classics") for model in payload[group]}
    placed = []
    original = render._picture

    def record(canvas, art, box, radius=7):
        original(canvas, art, box, radius)
        if art is picture:
            x1, y1, x2, y2 = box
            interior = (round(x1 + 6), round(y1 + 6), round(x2 - 6), round(y2 - 6))
            placed.append((interior, canvas.image.crop(interior)))

    monkeypatch.setattr(render, "_picture", record)
    page = render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW, images)
    assert len(placed) >= 8
    for box, original_pixels in placed:
        assert ImageChops.difference(page.crop(box), original_pixels).getbbox() is None


def test_the_plugin_keeps_its_lists_and_reads_them_back_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINT_INSPIRATION_CACHE_DIR", str(tmp_path))
    calls = []
    pool = [sources.normalize_hit(hit(str(1000 + index), f"Trend {index}")) for index in range(8)]
    monkeypatch.setattr(sources, "fetch_pool", lambda context: calls.append("pool") or {"models": pool})
    monkeypatch.setattr(sources, "fetch_classics", lambda context: calls.append("classics") or {"models": pool[:3]})
    monkeypatch.setattr(sources, "fetch_details", lambda model_id, context: calls.append(f"details-{model_id}") or {
        "details": sources.design_details(DESIGN)})
    plugin = PrintInspiration({"id": "print_inspiration"})
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 60)

    live = plugin._payload({}, NOW, allow_network=True, context=context)

    assert calls == ["pool", "classics"] + [f"details-{1000 + index}" for index in range(5)] and live["week"][0]["category"] == "家居 · 装饰"
    calls.clear()
    offline = plugin._payload({}, NOW, allow_network=False, context=context)
    assert calls == [] and offline["week"][0]["summary"] and offline["classics"][0]["name"] == "Trend 0"


def test_the_leads_gallery_pictures_are_fetched_small(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINT_INSPIRATION_CACHE_DIR", str(tmp_path))
    from plugins.print_inspiration import print_inspiration as module

    asked = []
    monkeypatch.setattr(module, "load_thumbnails", lambda urls, *args, **kwargs: asked.append(
        (tuple(urls), kwargs.get("size"))) or {url: Image.new("RGB", (4, 4)) for url in urls})
    lead = {**sources.normalize_hit(hit()), **sources.design_details(DESIGN)}
    plugin = PrintInspiration({"id": "print_inspiration"})
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 60)

    images = plugin._images({"week": [lead], "makes": [], "classics": []}, allow_network=True, context=context)

    assert images[lead["image"]] is not None and all(url in images for url in lead["gallery"])
    assert (tuple(lead["gallery"]), module.SMALL_PICTURE_PIXELS) in asked


def test_a_lead_without_a_print_profile_shows_its_tags_instead(monkeypatch):
    shown = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda canvas, xy, text, *args, **kwargs: shown.append(str(text))
                        or original(canvas, xy, text, *args, **kwargs))
    payload = _payload()
    payload["week"][0] = {**payload["week"][0], "print": {}}

    render.render_page((800, 480), payload, {}, NOW, {})

    assert {"#hogwarts", "#Train"} <= set(shown) and "PLA" not in shown


def test_the_right_column_names_the_trends_most_printed_and_a_lead_without_ratings_has_no_star(monkeypatch):
    shown, stats = [], []
    original, stat = render.PageCanvas.text, render._stat
    monkeypatch.setattr(render.PageCanvas, "text", lambda canvas, xy, text, *args, **kwargs: shown.append(str(text))
                        or original(canvas, xy, text, *args, **kwargs))
    monkeypatch.setattr(render, "_stat", lambda canvas, x, middle, kind, value, **kwargs: stats.append(kind)
                        or stat(canvas, x, middle, kind, value, **kwargs))
    payload = _payload()
    payload["week"][0] = {**payload["week"][0], "rating": 0.0, "ratings": 0}

    render.render_page((800, 480), payload, {}, NOW, {})

    assert "热门里打印最多" in shown and not any("本周" in text for text in shown)
    assert "star" not in stats[:5] and "–" not in shown


def test_a_cards_title_runs_on_into_the_room_under_it(monkeypatch):
    shown = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda canvas, xy, text, *args, **kwargs: shown.append(str(text))
                        or original(canvas, xy, text, *args, **kwargs))
    payload = _payload()
    payload["week"][1] = {**payload["week"][1], "name": "Articulated Baby Dragon Printed In Place"}

    render.render_page((800, 480), payload, {}, NOW, {})

    assert any(text.rstrip().endswith("Place") for text in shown)


@pytest.mark.parametrize("theme", ["day", "night"])
def test_card_continuation_uses_full_width_with_the_bundled_font(monkeypatch, theme):
    # Clean archives have the bundled UI font, without a user's local YaHei files.
    monkeypatch.delenv("INKYPI_DATA_DIR", raising=False)
    canvas = render.PageCanvas(render._palette({"themeMode": theme}), family="unavailable-local-font")
    assert Path(canvas.font(12).path).name == "NotoSansSC-VF.ttf"
    drawn = []
    original = canvas.text

    def text(xy, value, size=14, *args, **kwargs):
        if size == 12:
            bounds = canvas.draw.textbbox(xy, str(value), font=canvas.font(size),
                                          anchor=kwargs.get("anchor", "la"))
            drawn.append((str(value), bounds))
        return original(xy, value, size, *args, **kwargs)

    monkeypatch.setattr(canvas, "text", text)
    title = "Articulated Baby Dragon Printed In Place"
    model = {**_payload()["week"][1], "name": title}
    box = (10, 286, 132, 472)
    render._card(canvas, box, model, 2, {}, {})

    rank, *lines = drawn
    assert rank[0] == "2"
    assert " ".join(value for value, _bounds in lines) == title
    assert len(lines) == 3
    assert rank[1][2] < lines[0][1][0]
    assert all(bounds[0] == box[0] + 5 for _value, bounds in lines[1:])
    assert all(box[0] + 5 <= bounds[0] < bounds[2] <= box[2] - 5
               and 380 <= bounds[1] < bounds[3] <= box[3] - 38 for _value, bounds in lines)
    assert all(left[1][3] <= right[1][1] for left, right in zip(lines, lines[1:]))


def _with_avatar(model, index):
    return {**model, "maker": f"Maker {index}",
            "avatar": f"https://makerworld.bblmw.com/makerworld/user/{index}/a.jpg?x-oss-process=image/resize,w_64"}


def test_every_shown_models_maker_avatar_is_fetched(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINT_INSPIRATION_CACHE_DIR", str(tmp_path))
    from plugins.print_inspiration import print_inspiration as module

    asked = []
    monkeypatch.setattr(module, "load_thumbnails", lambda urls, *args, **kwargs: asked.append(
        (tuple(urls), kwargs.get("size"))) or {url: Image.new("RGB", (4, 4)) for url in urls})
    payload = _payload()
    payload = {key: [_with_avatar(model, f"{key}{index}") for index, model in enumerate(payload[key])]
               for key in ("week", "makes", "classics")}
    plugin = PrintInspiration({"id": "print_inspiration"})
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 60)

    images = plugin._images(payload, allow_network=True, context=context)

    shown = payload["week"][:render.CARDS + 1] + payload["makes"][:render.MAKES_ROWS] + payload["classics"]
    avatars = [url for urls, size in asked if size == module.AVATAR_PIXELS for url in urls]
    assert sorted(avatars) == sorted(model["avatar"] for model in shown)
    assert all(model["avatar"] in images for model in shown)


def test_the_cards_and_rows_show_each_makers_avatar_and_name(monkeypatch):
    shown = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda canvas, xy, text, *args, **kwargs: shown.append(str(text))
                        or original(canvas, xy, text, *args, **kwargs))
    payload = _payload()
    payload = {**payload, **{key: [_with_avatar(model, f"{key}{index}") for index, model in enumerate(payload[key])]
                             for key in ("week", "makes", "classics")}}
    # Short names leave a line for the maker; card 5's long name keeps all its room.
    for index in (1, 2, 3):
        payload["week"][index]["name"] = f"Model {index}"
    payload["week"][4]["name"] = "Articulated Baby Dragon Printed In Place"
    colour = (200, 30, 160)
    images = {model["avatar"]: Image.new("RGB", (64, 64), colour)
              for key in ("week", "makes", "classics") for model in payload[key]}

    page = render.render_page((800, 480), payload, {}, NOW, images).convert("RGB")

    assert {"Maker week1", "Maker week3", "Maker makes0", "Maker classics2"} <= set(shown)
    assert "Maker week4" not in shown and any(text.rstrip().endswith("Place") for text in shown)
    grid = page.crop(render.GRID)
    # The long-named card still shows its maker, by the avatar on its picture.
    width = (render.GRID[2] - render.GRID[0] - render.CARD_GAP * 3) / 4
    last = page.crop((round(render.GRID[0] + 3 * (width + render.CARD_GAP)), render.GRID[1], render.GRID[2], render.GRID[3]))
    assert colour in set(last.get_flattened_data())
    column = page.crop((render.RIGHT[0] + render.THUMB[0], render.RIGHT[1], render.RIGHT[2], render.RIGHT[3]))
    assert colour in set(grid.get_flattened_data()) and colour in set(column.get_flattened_data())


def test_a_short_named_card_fills_its_spare_line_with_category_and_rating(monkeypatch):
    shown = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda canvas, xy, text, *args, **kwargs: shown.append(str(text))
                        or original(canvas, xy, text, *args, **kwargs))
    payload = _payload()
    payload["week"][2] = {**_with_avatar(payload["week"][2], 2), "name": "Mrs. Claus",
                          "category": "家居 · 节庆", "rating": 4.9, "ratings": 12}
    payload["week"][3] = {**payload["week"][3], "name": "Articulated Baby Dragon Printed In Place",
                          "category": "玩具游戏", "rating": 3.9, "ratings": 3}
    payload["week"][4] = {**payload["week"][4], "name": "Gadget", "category": "", "tags": ["fidget", "toy"]}

    render.render_page((800, 480), payload, {}, NOW, {})

    # A one-line name leaves a line: its category and rating fill it (a model without a category
    # shows its first tag); a name that needs the room keeps it.
    assert {"家居 · 节庆", "4.9", "#fidget"} <= set(shown)
    assert "玩具游戏" not in shown and "3.9" not in shown


def test_the_classics_take_turns_three_at_a_time_through_the_all_time_list():
    from datetime import timedelta

    classics = [sources.normalize_hit(hit(str(3000 + index), f"Classic {index}")) for index in range(8)]
    turns = [sources.classics_turn(classics, NOW + timedelta(seconds=step * sources.CLASSICS_TURN_SECONDS))
             for step in range(4)]
    ranks = [[model["rank"] for model in turn] for turn in turns]
    # Three in a row with their places; two full turns in a list of eight, then round again.
    assert sorted(ranks[:2]) == [[1, 2, 3], [4, 5, 6]] and ranks[2:] == ranks[:2]
    assert all(model["of"] == 8 for turn in turns for model in turn)
    assert turns[0] == sources.classics_turn(classics, NOW + timedelta(minutes=5)) or ranks[0] != ranks[1]
    assert sources.classics_turn([], NOW) == [] and len(sources.classics_turn(classics[:2], NOW)) == 2
    assert sources.CLASSICS == ("likeCount", 30)


def test_a_classic_row_gives_its_place_counts_maker_and_year(monkeypatch):
    drawn = []
    original = render.PageCanvas.text

    def text(self, xy, value, *args, **kwargs):
        drawn.append(str(value))
        return original(self, xy, value, *args, **kwargs)

    monkeypatch.setattr(render.PageCanvas, "text", text)
    payload = _payload()
    payload["classics"] = [{**model, "rank": 7 + index, "of": 30, "downloads": 81000, "likes": 46000, "makes": 9200,
                            "maker": "Jakob", "published": "2023-05-02T10:00:00+00:00"}
                           for index, model in enumerate(payload["classics"])]
    image = render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})

    assert image.size == (800, 480)
    assert "点赞榜 7–9 / 30" in drawn and {"7", "8", "9"} <= set(drawn)
    assert {"81k", "46k", "9.2k", "2023 年", "Jakob"} <= set(drawn)
