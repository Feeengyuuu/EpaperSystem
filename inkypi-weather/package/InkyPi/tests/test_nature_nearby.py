import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance  # noqa: E402
from plugins.nature_nearby import sources  # noqa: E402
from plugins.nature_nearby.nature_nearby import NatureNearby  # noqa: E402
from plugins.nature_nearby.render import render_page  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402
from utils.remote_thumbnails import load_thumbnails  # noqa: E402

NOW = datetime(2026, 10, 21, 14, 0, tzinfo=sources.PACIFIC)
PHOTO = "https://inaturalist-open-data.s3.amazonaws.com/photos/753861248/square.jpg"


THREATENED = {"level": "VU", "total": 35, "partial": False, "items": [
    {"count": 71, "name": "君主斑蝶", "scientific": "Danaus plexippus", "group": "Insecta", "iucn": "VU",
     "authority": "NatureServe"},
    {"count": 24, "name": "美洲鹈鹕", "scientific": "Pelecanus erythrorhynchos", "group": "Aves", "iucn": "CR",
     "authority": "NatureServe"}]}
SEASON = {"months": [45, 32, 57, 31, 2, 0, 0, 0, 0, 28, 42, 39]}


@pytest.fixture(autouse=True)
def quiet_extras(monkeypatch):
    """No test reaches the threatened species, the species' months or Wikipedia: they read the
    samples above (and the blurb is Wikipedia's monarch lead) unless a test says otherwise."""
    monkeypatch.setattr(sources, "fetch_threatened", lambda *_args: THREATENED)
    monkeypatch.setattr(sources, "fetch_season", lambda *_args: SEASON)
    monkeypatch.setattr(sources, "fetch_blurb", lambda title, name, _ctx: {"text": "君主斑蝶是一种斑蝶。", "title": title})


class Device:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal", "timezone": "America/Los_Angeles"}
        return values if key is None else values.get(key, default)

    def load_env_key(self, _key):
        return None


def test_places_photos_and_hosts_normalize():
    assert sources.short_place("Grand Blvd, San Jose, CA, US") == "San Jose"
    assert sources.short_place("Santa Clara, CA 95050, USA") == "Santa Clara"
    assert sources.photo_url(PHOTO) == PHOTO.replace("/square.", "/medium.")
    assert sources.photo_url("https://evil.example/photos/1/square.jpg") is None
    assert sources.group_info("Aves") == ("鸟类", "bird", "blue")
    assert sources.group_info("Chromista")[0] == "其他"


def test_recent_observations_keep_photo_name_distance_and_observer():
    data = sources.normalize_recent({"results": [
        {"id": 1, "time_observed_at": "2026-10-21T13:20:43-07:00", "place_guess": "Grand Blvd, San Jose, CA, US",
         "location": "37.4326,-121.9624", "user": {"login": "abbyvb56"},
         "photos": [{"url": PHOTO, "attribution": "(c) abbyvb56"}],
         "taxon": {"name": "Ardea alba", "preferred_common_name": "大白鹭", "iconic_taxon_name": "Aves"}},
        {"id": 2, "observed_on": "2026-10-21", "photos": [], "taxon": {"name": "No photo"}},
    ]}, sources.DEFAULT_HOME)

    assert len(data["items"]) == 1
    item = data["items"][0]
    assert (item["name"], item["place"], item["observer"]) == ("大白鹭", "San Jose", "abbyvb56")
    assert 10 < item["distance_km"] < 16 and item["photo"].endswith("/medium.jpg")


def test_species_and_group_counts_normalize():
    species = sources.normalize_species({"total_results": 533, "results": [
        {"count": 140, "taxon": {"name": "Danaus plexippus", "preferred_common_name": "君主斑蝶",
                                 "iconic_taxon_name": "Insecta", "default_photo": {"medium_url": PHOTO}}}]})
    groups = sources.normalize_groups({"results": [
        {"count": 146, "taxon": {"name": "Aves"}}, {"count": 553, "taxon": {"name": "Plantae"}},
        {"count": 0, "taxon": {"name": "Protozoa"}}]})

    assert species["total"] == 533 and species["items"][0]["count"] == 140
    assert [group["group"] for group in groups["groups"]] == ["Plantae", "Aves"]


def test_notable_birds_keep_each_species_latest_report():
    rows = [
        {"speciesCode": "baleag", "comName": "白头海雕", "sciName": "Haliaeetus leucocephalus",
         "locName": "Coyote Hills RP--Main Marsh", "obsDt": "2026-10-20 08:10", "howMany": 1,
         "lat": 37.55, "lng": -122.09, "obsValid": True},
        {"speciesCode": "baleag", "comName": "白头海雕", "sciName": "Haliaeetus leucocephalus",
         "locName": "Lake Elizabeth", "obsDt": "2026-10-21 07:45", "howMany": 2, "lat": 37.55, "lng": -121.96},
        {"speciesCode": "tufduc", "comName": "凤头潜鸭", "sciName": "Aythya fuligula", "locName": "37.5, -122.0",
         "obsDt": "2026-10-19", "lat": 37.5, "lng": -122.0},
        {"speciesCode": "bad"},
    ]
    data = sources.normalize_notable(rows, sources.DEFAULT_HOME)

    assert data["species"] == 2
    eagle, duck = data["items"]
    assert (eagle["name"], eagle["place"], eagle["count"], eagle["confirmed"]) == ("白头海雕", "Lake Elizabeth", 2, False)
    assert eagle["distance_km"] < 5
    assert (duck["name"], duck["place"]) == ("凤头潜鸭", "")
    assert sources.ebird_place("Coyote Hills RP--Main Marsh") == "Coyote Hills RP"
    with pytest.raises(ValueError):
        sources.normalize_notable({"errors": []}, sources.DEFAULT_HOME)


def test_the_newest_finds_species_gives_its_family_red_list_category_and_reach():
    data = sources.normalize_taxon({"results": [{
        "id": 19975, "observations_count": 47923, "preferred_establishment_means": "native",
        "ancestors": [{"rank": "class", "name": "Aves", "preferred_common_name": "鸟纲"},
                      {"rank": "order", "name": "Strigiformes", "preferred_common_name": "鸮形目"},
                      {"rank": "family", "name": "Strigidae"}],
        "conservation_statuses": [
            {"authority": "NatureServe", "status": "S2", "place": {"id": 14}},
            {"authority": "IUCN Red List", "status": "lc", "place": None}],
    }]})

    assert data == {"id": 19975, "order": "鸮形目", "family": "Strigidae", "iucn": "LC", "establishment": "native",
                    "observations": 47923, "name": "", "wiki_title": "", "wiki_summary": ""}
    assert sources.normalize_taxon({"results": [{"id": 1, "conservation_statuses": [
        {"authority": "IUCN Red List", "status": "S1", "place": None}]}]})["iucn"] == ""
    with pytest.raises(ValueError):
        sources.normalize_taxon({"results": []})


def test_the_hero_shows_the_species_line_and_the_sources_logos():
    payload = _payload()
    plain = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})
    payload["taxon"] = {"state": "live", "data": {"order": "鹈形目", "family": "鹭科", "iucn": "LC",
                                                  "establishment": "native", "observations": 120345}}
    shown = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})

    green = tuple(int(sources.IUCN_CATEGORIES["LC"][1][index:index + 2], 16) for index in (1, 3, 5))
    assert green in set(shown.crop((150, 260, 300, 330)).get_flattened_data())
    assert green not in set(plain.crop((150, 260, 300, 330)).get_flattened_data())
    # iNaturalist's logo (its green "i") heads the page and the hero card.
    assert any(r < 140 and g > 150 and b < 90 for r, g, b in plain.crop((200, 10, 330, 50)).get_flattened_data())


def _payload():
    recent = [{"id": index, "name": name, "scientific": "Sci name", "group": group, "time": NOW.isoformat(),
               "place": "Fremont", "distance_km": 3.0, "observer": "someone",
               "photo": PHOTO.replace("/square.", "/medium.")}
              for index, (name, group) in enumerate([("大白鹭", "Aves"), ("郊狼", "Mammalia"), ("君主斑蝶", "Insecta")])]
    return {
        "recent": {"state": "live", "data": {"items": recent}},
        "species": {"state": "live", "data": {"total": 533, "items": [
            {"count": 140, "name": "君主斑蝶", "scientific": "Danaus plexippus", "group": "Insecta", "photo": None}]}},
        "groups": {"state": "live", "data": {"groups": [{"group": "Plantae", "count": 553},
                                                         {"group": "Insecta", "count": 388}]}},
        "stats": {"state": "live", "data": {"observations": 6229, "observers": 813, "top_observer": "sms273",
                                            "top_observations": 278, "top_species": 116}},
        "home": list(sources.DEFAULT_HOME), "radius_km": 40,
    }


@pytest.mark.parametrize("mode", ["day", "night"])
def test_page_renders_without_photos(mode):
    image = render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, NOW, {})

    assert image.size == (800, 480)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_page_shows_notable_birds_under_the_groups(mode):
    payload = _payload()
    payload["birds"] = {"state": "live", "data": {"species": 9, "items": [
        {"name": "白头海雕", "scientific": "Haliaeetus leucocephalus", "place": "Coyote Hills RP",
         "time": NOW.isoformat(), "count": 1, "distance_km": 9.0, "confirmed": True}]}}

    with_birds = render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW, {})
    without = render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, NOW, {})

    assert with_birds.size == (800, 480)
    assert with_birds.crop((12, 400, 300, 468)).tobytes() != without.crop((12, 400, 300, 468)).tobytes()
    assert with_birds.crop((310, 58, 788, 300)).tobytes() == without.crop((310, 58, 788, 300)).tobytes()


def test_birds_load_only_with_an_ebird_key(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    payload = _payload()
    for name in ("recent", "species", "groups", "stats"):
        monkeypatch.setattr(sources, f"fetch_{name}", lambda *_args, data=payload[name]["data"]: data)
    keys = []
    monkeypatch.setattr(sources, "fetch_notable",
                        lambda _home, _radius, key, _ctx: keys.append(key) or {"species": 0, "items": []})

    class KeyedDevice(Device):
        def load_env_key(self, key):
            return " ebird-test-key " if key == "EBIRD_API_KEY" else None

    plugin = NatureNearby({"id": "nature_nearby"})
    plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, Device())
    assert keys == []
    plugin.generate_image({"_inkypi_theme": {"mode": "day"}, "forceRefresh": True}, KeyedDevice())
    assert keys == ["ebird-test-key"]


def test_the_newest_finds_species_loads_once_per_species(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    payload = _payload()
    payload["recent"]["data"]["items"][0]["taxon_id"] = 7458
    for name in ("recent", "species", "groups", "stats"):
        monkeypatch.setattr(sources, f"fetch_{name}", lambda *_args, data=payload[name]["data"]: data)
    asked = []
    monkeypatch.setattr(sources, "fetch_taxon", lambda taxon_id, _ctx: asked.append(taxon_id) or {
        "order": "鹈形目", "family": "鹭科", "iucn": "LC", "establishment": "native", "observations": 9,
        "wiki_title": ""})
    plugin = NatureNearby({"id": "nature_nearby"})

    plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, Device())
    plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, Device())

    assert asked == [7458]


def test_cache_only_redraw_reuses_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    payload = _payload()
    monkeypatch.setattr(sources, "fetch_recent", lambda *_args: payload["recent"]["data"])
    monkeypatch.setattr(sources, "fetch_species", lambda *_args: payload["species"]["data"])
    monkeypatch.setattr(sources, "fetch_groups", lambda *_args: payload["groups"]["data"])
    monkeypatch.setattr(sources, "fetch_stats", lambda *_args: payload["stats"]["data"])
    plugin = NatureNearby({"id": "nature_nearby"})

    live = plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, Device())
    for name in ("fetch_recent", "fetch_species", "fetch_groups", "fetch_stats"):
        monkeypatch.setattr(sources, name, lambda *_args: pytest.fail("cache-only redraw fetched"))
    cached = plugin.generate_image({"_inkypi_theme": {"mode": "day"}, "_theme_render_only": True}, Device())

    assert read_source_provenance(live) is SourceProvenance.LIVE
    assert read_source_provenance(cached) is SourceProvenance.FRESH_CACHE


def test_thumbnails_respect_allowlist_cache_only_and_time_budget(tmp_path):
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 48), (0, 128, 0)).save(buffer, format="PNG")
    calls = []

    class Http:
        def request_bytes(self, _method, url, **_kwargs):
            calls.append(url)
            return type("Response", (), {"data": buffer.getvalue()})()

    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)
    kwargs = {"http": Http(), "context": context, "safe_url": sources.safe_photo_url, "user_agent": "test"}
    first = load_thumbnails([PHOTO, "https://evil.example/x.jpg"], tmp_path, cached_only=False, **kwargs)
    cached = load_thumbnails([PHOTO], tmp_path, cached_only=True, **kwargs)
    nothing = load_thumbnails(["https://static.inaturalist.org/photos/2/small.jpg"], tmp_path, cached_only=True,
                              **kwargs)

    assert list(first) == [PHOTO] and calls == [PHOTO]
    assert cached[PHOTO].size == (64, 48)
    assert nothing == {}


def test_birds_get_their_chinese_names_from_inaturalist(monkeypatch):
    calls = []

    def fake_get_json(url, params, _context, max_bytes=0, headers=None):
        calls.append(params["q"])
        if params["q"] == "Aegolius acadicus":
            return {"results": [{"name": "Aegolius acadicus", "preferred_common_name": "棕榈鬼鸮"}]}
        return {"results": [{"name": "Something else", "preferred_common_name": "别的"}]}

    monkeypatch.setattr(sources, "_get_json", fake_get_json)
    items = [{"name": "Northern Saw-whet Owl", "scientific": "Aegolius acadicus"},
             {"name": "Western Tanager", "scientific": "Piranga ludoviciana"},
             {"name": "白头海雕", "scientific": "Haliaeetus leucocephalus"}]

    sources.chinese_bird_names(items, None)

    assert items[0] == {"name": "棕榈鬼鸮", "name_en": "Northern Saw-whet Owl", "scientific": "Aegolius acadicus"}
    assert items[1]["name"] == "Western Tanager"  # the match was for another species
    assert calls == ["Aegolius acadicus", "Piranga ludoviciana"]  # an already-Chinese name is not looked up


def test_species_are_named_in_a_field_guides_faces_and_rare_characters_keep_the_page_face():
    from plugins.nature_nearby import render

    assert render._face("serif", 13) is not None and render._face("latin", 11) is not None
    assert render._covers("白冠带雀 Zonotrichia") and not render._covers("白冠带鹀") and not render._covers("棕榈鬼鸮")
    drawn = []
    original = render.PageCanvas.text

    def capture(self, xy, text, size=14, color="ink", **kwargs):
        drawn.append((type(self).__name__, getattr(self, "kind", ""), str(text)))
        return original(self, xy, text, size, color, **kwargs)

    render.PageCanvas.text = capture
    try:
        render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": "day"}}, NOW, {})
    finally:
        render.PageCanvas.text = original
    # The cards' titles go through the serif face.
    assert ("_FaceCanvas", "serif", "最新发现") in drawn and ("_FaceCanvas", "serif", "本周热门物种") in drawn


INAT_MONARCH_SUMMARY = ("<b>君主斑蝶</b>（学名：<i>Danaus plexippus</i>），又名<b>黑脉金斑蝶</b>，是一種斑蝶。"
                        "牠們可能是北美洲最廣為人知的蝴蝶。")


def test_the_species_wikipedia_lead_becomes_a_simplified_blurb(monkeypatch):
    taxon = sources.normalize_taxon({"results": [{
        "id": 48662, "observations_count": 565881, "preferred_common_name": "君主斑蝶",
        "wikipedia_url": "http://zh.wikipedia.org/wiki/%E5%90%9B%E4%B8%BB%E6%96%91%E8%9D%B6",
        "wikipedia_summary": INAT_MONARCH_SUMMARY}]})

    assert taxon["wiki_title"] == "君主斑蝶" and taxon["name"] == "君主斑蝶"
    # Markup, the parenthesis and the alias go; the copy stays Traditional, so it is not shown as is.
    assert taxon["wiki_summary"] == "君主斑蝶是一種斑蝶。牠們可能是北美洲最廣為人知的蝴蝶。"
    assert not sources.is_simplified(taxon["wiki_summary"])
    assert sources.is_simplified("白冠带鹀以昆虫为食。") and not sources.is_simplified("Monarch butterfly")
    assert sources.wiki_title("http://en.wikipedia.org/wiki/Monarch_butterfly") is None
    assert sources.wiki_title("https://zh.wikipedia.org/wiki/Special:Random") is None
    # Wikipedia's newer standard form (𫛭) takes the name's 鵟; another out-of-font character hides the blurb.
    assert sources.clean_blurb("赤肩\U0002B6ED为鹰科\U0002B6ED属的一种鸟", "赤肩鵟") == "赤肩鵟为鹰科鵟属的一种鸟"
    assert sources.clean_blurb("某鸟为\U0002B6ED属的一种鸟", "某鸟") == ""
    calls = []

    def fake_get_json(url, params, context, max_bytes=0, headers=None):
        calls.append((url, headers))
        return {"title": "君主斑蝶", "extract": "君主斑蝶 又名帝王蝶、大桦斑蝶，是一种斑蝶。它们可能是北美洲最广为人知的蝴蝶。"}

    monkeypatch.undo()
    monkeypatch.setattr(sources, "_get_json", fake_get_json)
    blurb = sources.fetch_blurb("君主斑蝶", "君主斑蝶", None)
    assert blurb["text"] == "君主斑蝶是一种斑蝶。它们可能是北美洲最广为人知的蝴蝶。"
    assert calls[0][0].endswith("/page/summary/%E5%90%9B%E4%B8%BB%E6%96%91%E8%9D%B6")
    assert calls[0][1] == {"Accept-Language": "zh-cn"}
    monkeypatch.setattr(sources, "_get_json", lambda *_args, **_kwargs: {"extract": "君主斑蝶是一種斑蝶。"})
    with pytest.raises(ValueError):
        sources.fetch_blurb("君主斑蝶", "君主斑蝶", None)


def test_months_and_threatened_species_normalize(monkeypatch):
    assert sources.normalize_season({"results": {"month_of_year": {str(month): month for month in range(1, 13)}}}) == {
        "months": list(range(1, 13))}
    with pytest.raises(ValueError):
        sources.normalize_season({"results": {}})
    def row(count, name, chinese, iucn):
        return {"count": count, "taxon": {"name": name, "preferred_common_name": chinese, "iconic_taxon_name": "Aves",
                                          "conservation_status": {"authority": "NatureServe", "iucn": iucn}}}

    data = sources.normalize_threatened({"total_results": 300, "results": [
        row(71, "Danaus plexippus", "君主斑蝶", 30), row(37, "Ardea alba", "大白鹭", 20),
        row(25, "Pelecanus erythrorhynchos", "美洲鹈鹕", 50), row(11, "Sequoia sempervirens", "北美红杉", 40),
        row(3, "Calidris canutus", "红腹滨鹬", 10), row(2, "Rallus obsoletus", "加州长嘴秧鸡", 70)]})
    # Near threatened (a locally rated S4 egret) and least concern drop out; VU, EN, CR, EX stay.
    assert data["total"] == 4 and data["level"] == "VU"
    assert [(item["name"], item["iucn"]) for item in data["items"]] == [
        ("君主斑蝶", "VU"), ("美洲鹈鹕", "CR"), ("北美红杉", "EN")]
    assert data["partial"] is True  # 300 reported, six received: more than one page, so "at least"
    assert sources.valid_threatened(data) and not sources.valid_threatened({"total": 56, "items": []})
    none = sources.normalize_threatened({"total_results": 1, "results": [row(37, "Ardea alba", "大白鹭", 20)]})
    assert (none["total"], none["items"], none["partial"]) == (0, [], False)
    calls = []
    monkeypatch.undo()  # the real fetch, not the autouse sample
    monkeypatch.setattr(sources, "_get_json", lambda url, params, ctx, **_k: calls.append((url, params)) or {
        "total_results": 0, "results": []})
    sources.fetch_threatened(sources.DEFAULT_HOME, 40, NOW, None)
    assert calls[0][0].startswith("https://api.inaturalist.org/v2/") and calls[0][1]["per_page"] == 200
    assert "taxon.conservation_status.iucn" in calls[0][1]["fields"] and calls[0][1]["threatened"] == "true"


def _rich_payload():
    payload = _payload()
    payload["taxon"] = {"state": "live", "data": {"order": "鳞翅目", "family": "蛱蝶科", "iucn": "", "establishment": "native",
                                                  "observations": 565881, "name": "君主斑蝶", "wiki_title": "君主斑蝶",
                                                  "wiki_summary": "君主斑蝶是一種斑蝶。"}}
    payload["blurb"] = {"state": "live", "data": {"text": "君主斑蝶是一种斑蝶。它们可能是北美洲最广为人知的蝴蝶。"}}
    payload["season"] = {"state": "live", "data": SEASON}
    payload["threatened"] = {"state": "live", "data": THREATENED}
    return payload


def _recorded(monkeypatch):
    from utils.page_canvas import PageCanvas

    drawn = []
    original = PageCanvas.text

    def record(self, xy, text, *args, **kwargs):
        drawn.append(str(text))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(PageCanvas, "text", record)
    return drawn


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_hero_gives_the_blurb_and_months_and_the_groups_card_the_threatened(monkeypatch, mode):
    from plugins.nature_nearby import render

    drawn = _recorded(monkeypatch)
    image = render_page((800, 480), _rich_payload(), {"_inkypi_theme": {"mode": mode}}, NOW, {})

    assert image.size == (800, 480)
    assert any(text.startswith("君主斑蝶是一种斑蝶。它们") for text in drawn) and "高峰 3 月" in drawn
    assert {"受威胁物种", "本周 35 种 · 易危及以上", "VU 易危", "CR 极危", "71 次"} <= set(drawn)
    # This month's bar in the card's green.
    green = render._palette({"_inkypi_theme": {"mode": mode}})["green"]
    assert green in set(image.crop((200, 300, 292, 325)).get_flattened_data())
    # Without the blurb, a Traditional copy is not shown; without months either, the hero is as before.
    plain = {**_rich_payload(), "blurb": None, "season": {"state": "live", "data": {"months": [0] * 12}}}
    drawn.clear()
    render_page((800, 480), plain, {"_inkypi_theme": {"mode": mode}}, NOW, {})
    assert not any("斑蝶是" in text for text in drawn) and not any(text.startswith("高峰") for text in drawn)


def test_with_ebirds_birds_the_threatened_species_take_the_filmstrips_foot(monkeypatch):
    drawn = _recorded(monkeypatch)
    payload = _rich_payload()
    payload["birds"] = {"state": "live", "data": {"species": 9, "items": [
        {"name": "白头海雕", "scientific": "Haliaeetus leucocephalus", "place": "Coyote Hills RP",
         "time": NOW.isoformat(), "count": 1, "distance_km": 9.0, "confirmed": True}]}}
    render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})

    assert "易危及以上 本周 35 种：君主斑蝶 71 次 · 美洲鹈鹕 24 次" in drawn
    assert not any(text.startswith("本周最活跃") for text in drawn) and "受威胁物种" not in drawn


def test_the_extras_load_with_the_species_and_never_decide_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    payload = _payload()
    payload["recent"]["data"]["items"][0]["taxon_id"] = 48662
    for name in ("recent", "species", "groups", "stats"):
        monkeypatch.setattr(sources, f"fetch_{name}", lambda *_args, data=payload[name]["data"]: data)
    monkeypatch.setattr(sources, "fetch_taxon", lambda taxon_id, _ctx: {
        "order": "鳞翅目", "family": "蛱蝶科", "iucn": "", "establishment": "native", "observations": 9,
        "name": "君主斑蝶", "wiki_title": "君主斑蝶", "wiki_summary": ""})
    plugin = NatureNearby({"id": "nature_nearby"})
    task = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)

    loaded, images = plugin._refresh({}, NOW, allow_network=True, context=task)
    assert loaded["blurb"]["data"]["text"] == "君主斑蝶是一种斑蝶。"
    assert loaded["season"]["data"] == SEASON and loaded["threatened"]["data"] == THREATENED

    def offline(*_args):
        raise OSError("offline")

    for name in ("fetch_threatened", "fetch_season", "fetch_blurb"):
        monkeypatch.setattr(sources, name, offline)
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "other"))
    failed, _images = plugin._refresh({}, NOW, allow_network=True, context=task)
    assert failed["threatened"]["state"] == "unavailable" and failed["season"]["state"] == "unavailable"
    assert failed["provenance"] is SourceProvenance.LIVE
    assert render_page((800, 480), failed, {"_inkypi_theme": {"mode": "day"}}, NOW, images).size == (800, 480)


def test_a_week_without_vulnerable_species_says_so_quietly(monkeypatch):
    drawn = _recorded(monkeypatch)
    payload = _rich_payload()
    payload["threatened"] = {"state": "live", "data": {"level": "VU", "total": 0, "partial": False, "items": []}}
    render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})
    assert {"受威胁物种", "本周无易危及以上物种"} <= set(drawn) and not any("次" == text[-1:] and "斑蝶" in text
                                                                    for text in drawn)
    # With eBird's birds, the filmstrip's foot keeps the week's most active observer instead.
    payload["birds"] = {"state": "live", "data": {"species": 1, "items": [
        {"name": "白头海雕", "scientific": "Haliaeetus leucocephalus", "place": "Coyote Hills RP",
         "time": NOW.isoformat(), "count": 1, "distance_km": 9.0, "confirmed": True}]}}
    drawn.clear()
    render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})
    assert any(text.startswith("本周最活跃") for text in drawn)
    payload["threatened"] = {"state": "live", "data": {**THREATENED, "partial": True}}
    drawn.clear()
    render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})
    assert any(text.startswith("易危及以上 本周 35+ 种") for text in drawn)
