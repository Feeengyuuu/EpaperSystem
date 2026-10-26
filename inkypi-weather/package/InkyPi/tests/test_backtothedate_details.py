"""BacktotheDate's poster details: what the poster page, designer page and Wikipedia give, and the page that shows them."""

import base64
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.backtothedate import poster_layout, poster_meta  # noqa: E402

POSTER_PAGE = """
<h1>The people's armed forces, defenders of peace!</h1>
<div class="w3-section field field--name-field-titlechinese field--type-string w3-bar-item field__item">人民武装，和平保卫者！</div>
<div class="w3-section field field--name-field-titlepinyin field--type-string w3-bar-item field__item">Renmin wuzhuang, hepingde baowei zhe!</div>
<div class="w3-section field field--name-field-taxonomy-artists field--label-above">
  <label class="field__label">Designer</label>
  <div class="w3-bar-item field__item"><a href="/artists/chenxinghua" hreflang="en">Chen Xinghua (陈兴华)</a></div>
</div>
<div class="w3-section field field--name-field-datetxt field--label-above"><label class="field__label">Date</label>
  <div class="w3-bar-item field__item">1949</div></div>
<div class="w3-section field field--name-field-publisher field--label-above"><label class="field__label">Publisher</label>
  <div class="w3-bar-item field__item">Renmin huabaoshe (人民画报社), Beijing</div></div>
<div class="w3-section field field--name-field-call-nr- field--label-above"><label class="field__label">Call nr.</label>
  <div class="w3-bar-item field__item">PC-1949-001</div></div>
<div class="w3-section field field--name-field-see-also-these-pages- field--label-above"><label class="field__label">Theme</label>
  <div class="w3-bar-item field__item"><a href="/themes/pla" hreflang="en">People&#039;s Liberation Army</a></div></div>
<ul><li><a href="/tags/china">China</a></li><li><a href="/tags/jiefangjun">解放军</a></li><li><a href="/tags/parade">parade</a></li></ul>
"""


def _jpeg(size=(400, 300), colour=(90, 120, 150)):
    output = BytesIO()
    Image.new("RGB", size, colour).save(output, format="JPEG")
    return output.getvalue()


def test_the_poster_page_gives_titles_designer_publication_holding_theme_and_tags():
    meta = poster_meta.parse_poster_page(POSTER_PAGE, "The people's armed forces, defenders of peace!")

    assert meta["title_zh"] == "人民武装，和平保卫者！"
    assert meta["pinyin"].startswith("Renmin")
    assert meta["designers"] == [{"name": "Chen Xinghua", "zh": "陈兴华",
                                  "url": "https://chineseposters.net/artists/chenxinghua"}]
    assert (meta["date"], meta["call_nr"]) == ("1949", "PC-1949-001")
    assert meta["themes"] == [{"name": "People's Liberation Army", "slug": "pla"}]
    # The site's generic tags are dropped.
    assert meta["tags"] == ["解放军", "parade"]


def test_the_designer_page_gives_their_years_and_birthplace():
    page = ('<div class="field field--name-field-body field__item">Chen Xinghua (1924-1997) was born in Qingxu, '
            'Shanxi Province. His only education...</div>')

    assert poster_meta.designer_facts(page) == ("1924–1997", "Qingxu, Shanxi Province")


def test_the_theme_background_comes_from_chinese_wikipedia_with_its_photo():
    calls = []

    def fetch_json(url, params):
        calls.append((url, params))
        return {"displaytitle": '<span lang="zh-Hans-CN">上山下乡运动</span>', "title": "上山下鄉運動",
                "extract": "上山下乡运动是一场政治运动。发生于1950年代至1978年。" + "后来" * 200 + "。",
                "thumbnail": {"source": "https://thumb.wikimedia.org/a/b/photo.jpg"}}

    article = poster_meta.theme_article({"slug": "up-to-the-mountains", "name": "Up to the mountains"}, fetch_json)
    title, summary, photo = poster_meta.theme_summary(article, fetch_json)

    assert article == "上山下乡运动" and not calls[:-1]
    assert title == "上山下乡运动"
    assert summary == "上山下乡运动是一场政治运动。发生于1950年代至1978年。"
    assert photo == "https://thumb.wikimedia.org/a/b/photo.jpg"
    # A photo from anywhere else is not fetched.
    assert poster_meta.theme_summary("x", lambda *_: {"extract": "甲。", "thumbnail": {"source": "https://evil.example/p.jpg"}})[2] == ""


def test_an_unknown_theme_finds_its_article_through_english_wikipedia():
    def fetch_json(url, params):
        assert url == poster_meta.EN_API_URL and params["titles"] == "Lei Feng campaign"
        return {"query": {"pages": {"1": {"langlinks": [{"lang": "zh", "*": "学习雷锋"}]}}}}

    assert poster_meta.theme_article({"slug": "unknown", "name": "Lei Feng campaign"}, fetch_json) == "学习雷锋"


def test_stored_details_are_bounded_and_the_photo_survives_the_round_trip():
    photo = poster_meta.encode_photo(_jpeg((900, 600)))
    kept = poster_meta.bounded({"title_zh": "长" * 500, "notes": "n", "unknown": "x", "theme_photo": photo,
                                "designers": [{"name": "A", "zh": "甲", "url": "u", "extra": "dropped"}] * 9})

    assert len(kept["title_zh"]) == poster_meta.LIMITS["title_zh"] and "unknown" not in kept
    assert len(kept["designers"]) == poster_meta.MAX_DESIGNERS and "extra" not in kept["designers"][0]
    assert len(base64.b64decode(kept["theme_photo"])) <= poster_meta.PHOTO_MAX_BYTES
    assert poster_meta.decode_photo(kept["theme_photo"]).size[0] <= poster_meta.PHOTO_SIZE[0]
    assert "theme_photo" not in poster_meta.bounded({"theme_photo": "not base64!"})


def _meta():
    meta = poster_meta.parse_poster_page(POSTER_PAGE, "The people's armed forces, defenders of peace!")
    meta.update({"theme_zh": "中国人民解放军", "theme_summary": "中国人民解放军是中华人民共和国的武装力量。"})
    return {"title": meta["title_en"], "meta": meta}


def test_a_landscape_poster_fills_the_left_and_the_details_squeeze_into_the_right():
    image = Image.new("RGB", (900, 600), (200, 40, 40))

    page = poster_layout.render_single(_meta(), image, (800, 480), "day")

    paper = poster_layout.palette("day")["paper"]
    assert page.size == (800, 480)
    # The poster runs from the margin to within the narrow column's reach.
    assert page.getpixel((poster_layout.MARGIN + 2, 240)) == (200, 40, 40)
    assert page.getpixel((800 - poster_layout.MARGIN - poster_layout.MIN_COLUMN - poster_layout.GAP - 3, 240)) == (200, 40, 40)
    column = [page.getpixel((x, y)) for x in range(620, 780) for y in range(20, 120)]
    assert sum(1 for pixel in column if pixel != paper) > 300


def test_a_wide_poster_goes_on_top_with_the_details_in_a_strip_below():
    image = Image.new("RGB", (1500, 500), (30, 90, 160))

    page = poster_layout.render_single(_meta(), image, (800, 480), "night")

    assert page.getpixel((400, poster_layout.MARGIN + poster_layout.TITLE_BAND + 5)) == (30, 90, 160)
    assert page.getpixel((780, 200)) == (30, 90, 160)
    strip = [page.getpixel((x, y)) for x in range(20, 780) for y in range(420, 470)]
    assert sum(1 for pixel in strip if pixel != poster_layout.palette("night")["paper"]) > 300


def _title_band(page, box):
    x0, y0, x1, y1 = box
    return [page.getpixel((x, y)) for x in range(x0, x1) for y in range(y0, y1)]


def _lettered(pixels):
    """Whether the title's vermilion lettering is among ``pixels``."""
    return any(red > 180 and green < 80 and blue < 80 for red, green, blue in pixels)


def test_the_page_title_heads_a_landscape_poster_and_moves_to_the_column_for_a_tall_one():
    assert poster_layout.TITLE_ART.is_file()
    margin, band = poster_layout.MARGIN, poster_layout.TITLE_H
    landscape = poster_layout.render_single(_meta(), Image.new("RGB", (900, 600), (40, 40, 200)), (800, 480), "day")
    # The lettering and its quotation sit over the poster, the poster below the band.
    title = _title_band(landscape, (margin, margin, 560, margin + band))
    assert _lettered(title) and (40, 40, 200) not in title
    assert landscape.getpixel((300, margin + poster_layout.TITLE_BAND + 20)) == (40, 40, 200)

    portrait = Image.new("RGB", (400, 600), (40, 40, 200))
    tall = poster_layout.render_single(_meta(), portrait, (800, 480), "night")
    # A tall poster keeps the full height; the title heads the details column instead.
    assert tall.getpixel((100, margin + 2)) == (40, 40, 200)
    assert _lettered(_title_band(tall, (340, margin, 788, margin + band)))


def test_spare_room_in_the_column_takes_another_poster():
    portrait = Image.new("RGB", (400, 600), (200, 40, 40))
    other = Image.new("RGB", (400, 300), (20, 160, 60))
    bare = {"title": "Plain", "meta": {"title_en": "Plain"}}

    page = poster_layout.render_single(bare, portrait, (800, 480), "day", [({"meta": {"title_zh": "另一张"}}, other)])

    colours = {page.getpixel((x, y)) for x in range(360, 790, 6) for y in range(60, 470, 6)}
    assert (20, 160, 60) in colours


def test_three_portraits_share_one_shelf_with_captions():
    posters = [({"meta": {"title_zh": f"海报{index}", "date": "1966"}}, Image.new("RGB", (300, 440), colour))
               for index, colour in enumerate(((200, 40, 40), (20, 160, 60), (30, 90, 160)))]

    page = poster_layout.render_shelf(posters, (800, 480), "day")

    row = [page.getpixel((x, 200)) for x in range(0, 800)]
    for _poster, image in posters:
        assert image.getpixel((0, 0)) in row
    # The page's title runs across the top, above the shelf.
    assert _lettered(_title_band(page, (12, 12, 788, 12 + poster_layout.TITLE_H)))
    # The shelf leaves room for the captions under the posters.
    assert page.getpixel((130, 480 - poster_layout.MARGIN - 10)) != (200, 40, 40)


def test_paper_margins_are_trimmed_but_never_the_poster():
    scan = Image.new("RGB", (1000, 800), (245, 242, 235))
    scan.paste(Image.new("RGB", (800, 600), (180, 30, 30)), (100, 100))

    trimmed = poster_layout.trim_margins(scan)

    assert 800 <= trimmed.width <= 815 and 600 <= trimmed.height <= 615
    blank = Image.new("RGB", (500, 400), (250, 250, 250))
    assert poster_layout.trim_margins(blank).size == (500, 400)


def test_banked_posters_without_details_get_them_on_screen_first_a_few_at_a_time(monkeypatch):
    from plugins.backtothedate import backtothedate as module

    plugin = module.BacktotheDate({"id": "backtothedate"})
    records = [{"media_key": f"{index:064x}", "page_url": f"https://chineseposters.net/posters/p{index}",
                "image_url": f"https://chineseposters.net/sites/default/files/images/p{index}.jpg", "title": f"P{index}"}
               for index in range(8)]
    records[2]["meta"] = {"title_zh": "已有"}
    profile = {"records": records, "current_selection": {"media_keys": [records[6]["media_key"]]}}
    fetched, saves = [], []

    class Bank:
        @staticmethod
        def normalize_poster(poster):
            return {"meta": poster_meta.bounded(poster.get("meta"))}

    monkeypatch.setattr(plugin, "_fetch_text", lambda url: fetched.append(url) or POSTER_PAGE)
    monkeypatch.setattr(plugin, "_enrich_poster", lambda poster: poster)
    monkeypatch.setattr(plugin, "_save_data_progress", lambda bank, document: saves.append(document))

    plugin._backfill_details(Bank(), {"doc": 1}, profile)

    assert len(fetched) == module.DETAIL_BACKFILL_LIMIT
    assert fetched[0].endswith("/p6")
    assert records[6]["meta"]["title_zh"] == "人民武装，和平保卫者！"
    assert records[2]["meta"] == {"title_zh": "已有"} and saves == [{"doc": 1}]


def test_spare_room_pictures_pack_into_the_rows_that_cover_the_most():
    # A wide, low room takes one row as tall as the room; a narrow tall one stacks them.
    wide = poster_layout.pack_rows([1.4, 0.7, 0.7], (420, 220))
    tall = poster_layout.pack_rows([0.7, 0.7], (190, 300))

    assert len(wide) == 1 and wide[0][0][0] == 0
    assert sum(size[0] for _index, size in wide[0]) <= 420
    # The row runs out of either width or height: it is as large as the room lets it be.
    row_width = sum(size[0] for _index, size in wide[0]) + poster_layout.PACK_GAP * (len(wide[0]) - 1)
    assert row_width >= 420 - 3 or wide[0][0][1][1] >= 220 - poster_layout.PACK_CAPTION - 2
    assert len(tall) in (1, 2) and all(size[1] >= poster_layout.PACK_MIN_HEIGHT for row in tall for _i, size in row)
    used = sum(max(size[1] for _i, size in row) + poster_layout.PACK_CAPTION for row in tall)
    assert used <= 300
    assert poster_layout.pack_rows([1.0], (100, 40)) == []


def _red_rows(page, box):
    x0, y0, x1, y1 = box
    return {y for y in range(y0, y1) for x in range(x0, x1) if page.getpixel((x, y)) == poster_layout.TITLE_RED}


def test_the_little_red_book_and_a_quotation_follow_the_title_the_same_one_for_the_same_poster():
    from plugins.backtothedate.quotations import QUOTATIONS
    from utils.fonts import load_font

    # The brush hand draws every character of every quotation itself.
    hand = load_font(str(poster_layout.QUOTE_FONT), 20)
    assert all(hand.covers(text) for text, _source in QUOTATIONS)
    portrait = Image.new("RGB", (400, 600), (40, 40, 200))
    margin, band = poster_layout.MARGIN, poster_layout.TITLE_H

    def title(url):
        page = poster_layout.render_single({**_meta(), "url": url}, portrait, (800, 480), "day")
        return page.crop((480, margin, 788, margin + band))

    # The book's crimson cover stands the title's height beside the lettering, where the double rule was.
    assert poster_layout.BOOK_ART.is_file() and poster_layout.SIGNATURE_ART.is_file()
    quoted = title("https://chineseposters.net/posters/pc-1959-001")
    crimson = {y for y in range(quoted.height) for x in range(quoted.width)
               if (lambda r, g, b: r > 90 and g < 60 and b < 70)(*quoted.getpixel((x, y)))}
    assert len(crimson) >= band - 4 and not _red_rows(quoted, (0, band // 2 - 3, quoted.width, band // 2 + 3))
    assert quoted.tobytes() == title("https://chineseposters.net/posters/pc-1959-001").tobytes()
    assert len({title(f"https://chineseposters.net/posters/pc-1966-{index:03d}").tobytes() for index in range(6)}) > 1


def test_without_room_for_a_quotation_the_title_keeps_its_double_rule():
    canvas = Image.new("RGB", (300, 60), (243, 234, 216))

    poster_layout._page_title(canvas, (0, 10, 280, 10 + poster_layout.TITLE_H), poster_layout.palette("day"), "seed")

    rows = _red_rows(canvas, (200, 10, 280, 10 + poster_layout.TITLE_H))
    assert rows and len(rows) <= 4
