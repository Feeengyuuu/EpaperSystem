import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.tech_play import sources  # noqa: E402
from plugins.tech_play.render import render_page  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402
from utils.http_client import HttpStatusError  # noqa: E402

NOW = datetime(2026, 10, 21, 13, 0, tzinfo=sources.PACIFIC)


def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)


def test_status_pages_degrade_per_service(monkeypatch):
    def fake_get(_method, url, _context, **_kwargs):
        if "githubstatus" in url:
            raise HttpStatusError("GET", url, 503)
        if "openai" in url and "incidents" in url:
            raise HttpStatusError("GET", url, 404)
        if "cloudflare" in url and "status.json" in url:
            return {"status": {"indicator": "major", "description": "Partial outage"}}
        if "cloudflare" in url:
            return {"incidents": [{"name": "Incorrect geo location", "impact": "major",
                                   "started_at": "2026-10-21T18:00:00Z"}]}
        if "incidents" in url:
            return {"incidents": []}
        return {"status": {"indicator": "none", "description": "All Systems Operational"}}

    monkeypatch.setattr(sources, "_get_json", fake_get)
    services = {item["id"]: item for item in sources.fetch_status(_context())["services"]}

    assert services["github"]["indicator"] == "unknown"
    assert services["openai"]["indicator"] == "none"
    assert services["cloudflare"]["indicator"] == "major"
    assert services["cloudflare"]["incident"] == "Incorrect geo location"


def _offer(start, end):
    return {"promotionalOffers": [{"startDate": start.isoformat(), "endDate": end.isoformat(),
                                   "discountSetting": {"discountPercentage": 0}}]}


def test_epic_splits_current_and_upcoming_giveaways():
    image = {"type": "OfferImageWide", "url": "https://cdn1.epicgames.com/spt-assets/a/b.png"}
    payload = {"data": {"Catalog": {"searchStore": {"elements": [
        {"title": "Bad Cheese", "keyImages": [image],
         "promotions": {"promotionalOffers": [_offer(NOW - timedelta(days=6), NOW + timedelta(days=1))]}},
        {"title": "Sker Ritual", "keyImages": [image],
         "promotions": {"upcomingPromotionalOffers": [_offer(NOW + timedelta(days=1), NOW + timedelta(days=8))]}},
        {"title": "Paid Game", "promotions": None},
    ]}}}}
    data = sources.normalize_epic(payload, NOW)

    assert [item["title"] for item in data["current"]] == ["Bad Cheese"]
    assert [item["title"] for item in data["upcoming"]] == ["Sker Ritual"]
    assert data["current"][0]["image"].endswith("resize=1")


def test_steam_dates_arxiv_and_run_times():
    assert sources.steam_release_date("2026 年 10 月 22 日").startswith("2026-10-22T12:00:00")
    assert sources.steam_release_date("2026 年第四季度") is None
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/2510.28632v1</id><published>2026-10-20T17:59:00Z</published>
      <title>Joints, Camera,
        Action!</title><author><name>Noam Rotstein</name></author><author><name>B</name></author>
      <arxiv:primary_category term="cs.CV"/></entry></feed>"""
    paper = sources.normalize_arxiv(atom)["papers"][0]

    assert (paper["id"], paper["title"], paper["authors"], paper["author_count"]) == (
        "2510.28632", "Joints, Camera, Action!", ["Noam Rotstein"], 2)
    assert sources.run_time(5714) == "1:35:14"
    assert sources.run_time(2565.65) == "42:45.65"
    assert sources.run_time(58.4) == "58.40s"
    assert sources.run_time(0.233) == "0.23s"


def _feed_run(game, minutes_ago, seconds=60.0, guest=None):
    stamp = (NOW - timedelta(minutes=minutes_ago)).astimezone(timezone.utc)
    players = [{"rel": "guest", "name": guest}] if guest else [{"rel": "user", "id": "u1"}]
    return {"game": game, "status": {"verify-date": stamp.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "times": {"primary_t": seconds}, "players": players}


def test_weekly_speedrun_counts_fold_in_only_new_real_runs():
    first_page = [_feed_run("dop4", 1), _feed_run("dop4", 2), _feed_run("sw", 3),
                  _feed_run("dadish", 4, seconds=0.001, guest="N/A"), _feed_run("old", 60 * 24 * 8)]
    days, newest, since = sources.count_new_runs({}, [first_page], NOW)
    week = {"days": days, "last_verified": newest.isoformat(), "since": since.isoformat(), "top": []}

    assert sources.weekly_totals(days) == {"dop4": 2, "sw": 1}
    later_page = [_feed_run("sw", -5), *first_page]
    days, newest_again, _since = sources.count_new_runs(week, [later_page], NOW + timedelta(minutes=6))

    assert sources.weekly_totals(days) == {"dop4": 2, "sw": 2}
    assert newest_again > newest


def test_run_entries_keep_levels_and_real_sub_second_times():
    run = {"times": {"primary_t": 0.233}, "status": {"verify-date": "2026-10-21T20:51:21Z"},
           "category": {"data": {"name": "Any%"}}, "level": {"data": {"name": "Level 300"}},
           "players": {"data": [{"rel": "user", "names": {"international": "Rockie"},
                                 "location": {"country": {"code": "us"}}}]}}
    placeholder = {**run, "times": {"primary_t": 0.001}}

    assert sources.run_entry(run)["category"] == "Level 300 · Any%"
    assert sources.run_entry(run)["country"] == "us"
    assert sources.run_entry(placeholder) is None
    assert sources.run_time(0.233) == "0.23s"
    assert sources.flag_url("ca") == "https://flagcdn.com/w40/ca.png"
    assert sources.flag_url("../x") is None


def test_deadlines_and_release_dates_read_as_time_left_and_days_ahead():
    from plugins.tech_play.render import days_until, time_left

    assert time_left(NOW + timedelta(days=4, hours=1), NOW) == "剩 4 天"
    assert time_left(NOW + timedelta(hours=5, minutes=30), NOW) == "剩 5 小时"
    assert time_left(NOW - timedelta(minutes=1), NOW) == ""
    # Release dates count calendar days, whatever the hour.
    assert days_until(NOW.replace(hour=23), NOW) == "今天"
    assert days_until(NOW + timedelta(days=1, hours=-12), NOW) == "明天"
    assert days_until(NOW + timedelta(days=7), NOW) == "7 天后"
    assert days_until(NOW - timedelta(days=1), NOW) == ""


def test_coverage_label_is_honest_until_a_full_week():
    from plugins.tech_play.render import _initials, coverage_label

    assert coverage_label({"since": (NOW - timedelta(hours=16)).isoformat()}, NOW) == "近 16 小时"
    assert coverage_label({"since": (NOW - timedelta(days=3)).isoformat()}, NOW) == "近 3 天"
    assert coverage_label({"since": (NOW - timedelta(days=7)).isoformat()}, NOW) == "本周"
    assert [_initials(name) for name in ("DOP 4: Draw One Part", "Super Mario 64")] == ["DOP4", "SM64"]


def test_wikipedia_cover_skips_disambiguation_and_unlisted_hosts(monkeypatch):
    monkeypatch.setattr(sources, "_get_json", lambda *_args, **_kwargs: {"query": {"pages": [
        {"title": "Celeste", "pageprops": {"disambiguation": ""},
         "thumbnail": {"source": "https://thumb.wikimedia.org/wrong.png"}},
        {"title": "Celeste (video game)",
         "thumbnail": {"source": "https://thumb.wikimedia.org/wikipedia/commons/thumb/0/0f/Celeste.png"}},
    ]}})

    assert sources.wikipedia_cover("Celeste", _context()).endswith("Celeste.png")
    assert sources.safe_image_url("https://evil.example/cover.png") is None
    assert sources.safe_image_url("http://cdn1.epicgames.com/a.png") is None


def _payload():
    return {
        "status": {"state": "live", "data": {"services": [
            {"id": "claude", "name": "Claude", "indicator": "none"},
            {"id": "cloudflare", "name": "Cloudflare", "indicator": "major", "incident": "Geo location issue"}]}},
        "epic": {"state": "live", "data": {"current": [
            {"title": "Bad Cheese", "start": NOW.isoformat(), "end": (NOW + timedelta(days=1)).isoformat(),
             "image": None}], "upcoming": []}},
        "upcoming": {"state": "live", "data": {"source": "Steam", "items": [
            {"name": "使命召唤", "date": sources.steam_release_date("2026 年 10 月 22 日"), "platforms": ["PC"],
             "image": None}]}},
        "arxiv": {"state": "live", "data": {"papers": [
            {"id": "2510.28632", "title": "Joints, Camera, Action!", "authors": ["Noam Rotstein"],
             "author_count": 2, "time": datetime(2026, 10, 20, tzinfo=timezone.utc).isoformat()}]}},
        "arxiv_category": "cs.AI",
        "speedrun": {"state": "live", "data": {
            "since": (NOW - timedelta(hours=16)).isoformat(), "total": 933, "days": {}, "top": [
                {"id": "sm64", "name": "Super Mario 64", "released": 1996, "cover": None, "week_runs": 70,
                 "runs": [{"category": "120 Star", "seconds": 5714.0, "verified": NOW.isoformat(),
                           "runner": "Suigi", "country": "ca"}]}]}},
    }


@pytest.mark.parametrize("mode", ["day", "night"])
def test_page_renders_without_any_images(mode):
    image = render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, NOW, {})

    assert image.size == (800, 480)


def test_platforms_group_into_families_in_a_fixed_order():
    from plugins.tech_play.render import platform_families

    assert platform_families(["Switch 2", "Series X|S", "iOS", "PS5", "Mac", "PC"]) == ["PC", "PS", "Xbox", "Switch"]
    assert platform_families(["PS4", "PS5", "XONE"]) == ["PS", "Xbox"]
    assert platform_families(["iOS", "Android"]) == []
    assert platform_families(None) == []


def test_every_platform_has_its_official_mark_for_the_legend():
    import json

    from plugins.tech_play import render

    folder = render.ASSETS / "platforms"
    record = json.loads((folder / "platforms.source.json").read_text(encoding="utf-8"))["logos"]
    for family in render.PLATFORMS:
        key = family.lower()
        assert record[key]["licence"].startswith("Public domain")
        mark = render._asset_icon(f"platforms/{key}", render.LEGEND_MARK)
        assert mark is not None and mark.height == render.LEGEND_MARK


def test_a_coming_game_is_framed_in_its_platforms_colours_side_by_side():
    from plugins.tech_play import render

    payload = _payload()
    payload["upcoming"]["data"] = {"source": "IGDB", "items": [
        {"name": "Grand Theft Auto VI", "date": (NOW + timedelta(days=18)).isoformat(),
         "platforms": ["PS5", "Series X|S"], "image": None}]}
    image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW, {})

    left = render.UPCOMING_CARD[0] + 8
    width = (render.UPCOMING_CARD[2] - 8 - left - 8 * 3) // 4
    middle = render.UPCOMING_CARD[1] + render.HEADER + 4 + 23
    assert image.getpixel((left, middle)) == (0, 112, 209)
    assert image.getpixel((left + width, middle)) == (16, 124, 16)
    # Its date row ends in the two platforms' marks, Xbox's last, in the same colours.
    row = middle - 23 + 70
    marks = image.crop((left + width - 40, row - 6, left + width + 1, row + 6)).get_flattened_data()
    assert sum(pixel == (0, 112, 209) for pixel in marks) > 10
    assert sum(pixel == (16, 124, 16) for pixel in image.crop(
        (left + width - render.ROW_MARK, row - 6, left + width + 1, row + 6)).get_flattened_data()) > 10


def test_a_service_links_to_its_state_by_a_live_wire_or_a_cut_one():
    from plugins.tech_play import render
    from utils.page_canvas import PageCanvas

    def wire(indicator):
        canvas = PageCanvas({"paper": (255, 255, 255), "ok": (5, 150, 105), "major": (200, 30, 50)}, size=(200, 20))
        render._status_link(canvas, 20, 180, 10, indicator, "ok" if indicator == "none" else "major")
        return canvas.image

    up, down = wire("none"), wire("major")
    # Up, the wire runs unbroken across its middle; down, the middle is cut.
    assert all(up.getpixel((x, 10)) == (5, 150, 105) for x in range(95, 106))
    assert all(down.getpixel((x, 10)) == (255, 255, 255) for x in range(97, 104))
    assert down.getpixel((40, 10)) == (200, 30, 50)
    assert render._asset_icon("icon-calendar", 13) is not None


def test_games_get_their_small_steam_icon_by_name_or_appid(monkeypatch):
    searches, batches = [], []

    def fake_get_json(method, url, context, **kwargs):
        params = kwargs.get("params") or {}
        if url == sources.STEAM_SEARCH_URL:
            searches.append(params["term"])
            catalogue = {"Phantom Blade Zero": [{"id": 4115450, "name": "Phantom Blade Zero", "type": "app"}],
                         "Home": [{"id": 559680, "name": "Home", "type": "app"}]}
            return {"items": catalogue.get(params["term"], [])}
        batches.append(params["input_json"])
        return {"response": {"store_items": [
            {"appid": 4115450, "assets": {"community_icon": "a" * 40}},
            {"appid": 559680, "assets": {"community_icon": "b" * 40}},
            {"appid": 3047, "assets": {"community_icon": "c" * 40}}]}}

    monkeypatch.setattr(sources, "_get_json", fake_get_json)

    icons = sources.fetch_steam_icons([("Phantom Blade Zero", None), ("Home - A Unique Horror Adventure", None),
                                       ("影之刃零", 3047), ("GTA VI", None)], None)["icons"]

    assert icons["Phantom Blade Zero"].endswith("/apps/4115450/" + "a" * 40 + ".jpg")
    # A subtitled name falls back to its head; a known appid skips the search; an unknown game has none.
    assert icons["Home - A Unique Horror Adventure"].endswith("/apps/559680/" + "b" * 40 + ".jpg")
    assert icons["影之刃零"].endswith("/apps/3047/" + "c" * 40 + ".jpg") and "GTA VI" not in icons
    assert "影之刃零" not in searches and len(batches) == 1
    # Steam's shared community_assets path: the older steamcommunity/public one 404s for newer games.
    assert icons["影之刃零"].startswith("https://shared.fastly.steamstatic.com/community_assets/images/apps/")
    assert sources.safe_image_url(icons["影之刃零"]) == icons["影之刃零"]


def test_a_steam_cover_url_names_its_app():
    url = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/2345670/header.jpg?t=1"
    assert sources.steam_appid_from_url(url) == 2345670
    assert sources.steam_appid_from_url("https://images.igdb.com/igdb/image/upload/apps/1/x.jpg") is None
    assert sources.steam_appid_from_url(None) is None


def test_an_igdb_game_names_its_steam_app():
    row = {"external_games": [{"category": 5, "uid": "gog-1"}, {"external_game_source": 1, "uid": "4115450"}]}
    assert sources.igdb_steam_appid(row) == 4115450
    assert sources.igdb_steam_appid({"external_games": [{"category": 1, "uid": "3161310"}]}) == 3161310
    assert sources.igdb_steam_appid({"external_games": [{"category": 36, "uid": "zelda"}]}) is None
    assert sources.igdb_steam_appid({}) is None


@pytest.mark.parametrize("rejects_store_fields", [False, True])
def test_igdb_upcoming_games_carry_their_steam_app(monkeypatch, rejects_store_fields):
    from utils.http_client import HttpClientError

    queries = []
    now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    row = {"name": "Phantom Blade Zero", "first_release_date": int(now.timestamp()) + 86400, "hypes": 9,
           "external_games": [{"external_game_source": 1, "uid": "4115450"}]}

    def fake_get_json(method, url, context, **kwargs):
        if url == sources.TWITCH_TOKEN_URL:
            return {"access_token": "token"}
        query = kwargs["data"].decode("utf-8")
        queries.append(query)
        if rejects_store_fields and "external_games" in query:
            raise HttpClientError("400 unknown field")
        return [row if "external_games" in query else {key: row[key] for key in row if key != "external_games"}]

    monkeypatch.setattr(sources, "_get_json", fake_get_json)

    item = sources.fetch_igdb("id", "secret", now, None)["items"][0]

    assert item["appid"] == (None if rejects_store_fields else 4115450)
    assert len(queries) == (2 if rejects_store_fields else 1)


def test_a_name_matches_a_steam_game_by_whole_words(monkeypatch):
    catalogue = {"Grand Theft Auto VI": [{"id": 12100, "name": "Grand Theft Auto: Vice City", "type": "app"}],
                 "Hela": [{"id": 3161310, "name": "Hela: of Mice & Magic", "type": "app"}],
                 "Hela: Of Mice & Magic": [{"id": 3161310, "name": "Hela: of Mice & Magic", "type": "app"}]}
    monkeypatch.setattr(sources, "_get_json", lambda method, url, context, **kwargs: {
        "items": catalogue.get(kwargs["params"]["term"], [])})

    assert sources.steam_appid("Grand Theft Auto VI", None) is None
    assert sources.steam_appid("Hela", None) == 3161310
    assert sources.steam_appid("Hela: Of Mice & Magic", None) == 3161310


ARXIV_PAGE = """<figure class="ltx_figure" id="S1.F1"><img src="2610.00001v1/x1.png" alt=""></figure>
<span class="ltx_contact ltx_role_affiliation"><span class="ltx_text">Meta FAIR</span>
<span class="ltx_text">Email: a@meta.com</span></span>
<span class="ltx_contact ltx_role_affiliation"><span>organization=Universit&eacute; Paris-Saclay,
 city=Palaiseau</span></span>
<span class="ltx_contact ltx_role_affiliation"><span>Meta FAIR</span></span>"""


class _Text:
    def __init__(self, data):
        self.data = data


def test_a_papers_arxiv_page_gives_its_figures_and_affiliations(monkeypatch):
    class Client:
        def request_text_head(self, _method, url, **_kwargs):
            if "2610.00002" in url:
                raise HttpStatusError("GET", url, 404)
            return _Text(ARXIV_PAGE)

    monkeypatch.setattr(sources, "get_http_client", Client)

    paper = sources.fetch_arxiv_paper("2610.00001", _context())
    assert paper["figures"] == ["https://arxiv.org/html/2610.00001v1/x1.png"]
    assert paper["affiliations"] == ["Meta FAIR", "Université Paris-Saclay, Palaiseau"]
    # A paper without an HTML version has neither, and keeps that answer.
    assert sources.fetch_arxiv_paper("2610.00002", _context()) == {"figures": [], "affiliations": []}
    assert sources.valid_arxiv_paper({"figures": [], "affiliations": []})
    with pytest.raises(ValueError):
        sources.fetch_arxiv_paper("../etc", _context())


def test_a_paper_stands_for_its_first_organisation_with_a_logo(monkeypatch):
    calls = []

    def get_json(_method, url, _context, *, params=None, **_kwargs):
        calls.append(params.get("affiliation") or params.get("action"))
        if url == sources.ROR_URL:
            known = {"Université Paris-Saclay": "Q1484306"}
            qid = known.get(params["affiliation"])
            return {"items": [{"chosen": bool(qid), "organization": {"external_ids": [
                {"type": "wikidata", "preferred": qid, "all": [qid]}]}}]}
        if params["action"] == "wbgetentities":
            return {"entities": {params["ids"]: {"labels": {"zh-cn": {"value": "巴黎-萨克雷大学"}}}}}
        logo = {"Q380": "Meta Platforms Inc. logo.svg", "Q1484306": None}[params["entity"]]
        return {"claims": {"P154": [{"mainsnak": {"datavalue": {"value": logo}}}] if logo else []}}

    monkeypatch.setattr(sources, "_get_json", get_json)

    # A lab known by its name skips ROR and keeps its own name.
    meta = sources.fetch_institution(["Meta FAIR"], _context())
    assert meta == {"name": "Meta", "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7b/"
                    "Meta_Platforms_Inc._logo.svg/250px-Meta_Platforms_Inc._logo.svg.png"}
    assert "Meta FAIR" not in calls
    # Without a logo anywhere, the first organisation ROR named still stands; with none, nothing.
    assert sources.fetch_institution(["Nowhere Lab", "Université Paris-Saclay"], _context()) == {
        "name": "巴黎-萨克雷大学", "logo": None}
    assert sources.fetch_institution(["Nowhere Lab"], _context()) == {}
    assert sources.valid_institution({}) and not sources.valid_institution({"name": "", "logo": None})


def _picture(size, colour=(40, 90, 160)):
    from PIL import Image

    return Image.new("RGB", size, colour)


def test_each_shown_paper_takes_its_best_fitting_figure_and_its_organisations_logo(monkeypatch, tmp_path):
    from plugins.tech_play import tech_play

    pictures = {"https://arxiv.org/html/1/wide.png": _picture((320, 60)),
                "https://arxiv.org/html/1/square.png": _picture((300, 240)),
                "https://upload.wikimedia.org/logo.png": _picture((200, 60))}
    asked = []

    def load_thumbnails(urls, *_args, **_kwargs):
        asked.append(list(urls))
        return {url: pictures[url].copy() for url in urls if url in pictures}

    monkeypatch.setattr(tech_play, "load_thumbnails", load_thumbnails)
    details = {"arxiv-paper": {"figures": ["https://arxiv.org/html/1/wide.png", "https://arxiv.org/html/1/square.png"],
                               "affiliations": ["Meta FAIR"]},
               "institution": {"name": "Meta", "logo": "https://upload.wikimedia.org/logo.png"}}
    papers = [{"id": "2610.00001", "title": "A"}, {"id": "2610.00002", "title": "B"}]

    images = tech_play.TechPlay._paper_images(papers, lambda spec: {"data": details[spec.name]}, tmp_path, {})

    # The later figure fills the row's box better than the first, a thin strip.
    assert papers[0]["figure"] == "https://arxiv.org/html/1/square.png"
    assert papers[0]["institution"]["name"] == "Meta"
    assert set(images) == {"https://arxiv.org/html/1/square.png", "https://upload.wikimedia.org/logo.png"}
    # Every paper's logo loads before any large figure, so slow figures never leave the logos no time.
    assert asked[0] == ["https://upload.wikimedia.org/logo.png"] * 2
    assert all("arxiv.org" in url for urls in asked[1:] for url in urls)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_papers_render_with_their_figures_logos_or_neither(mode):
    from PIL import Image, ImageDraw

    diagram = Image.new("RGB", (320, 240), (255, 255, 255))
    ImageDraw.Draw(diagram).rectangle((40, 40, 280, 200), outline=(20, 20, 20), width=3)
    logo = Image.new("RGBA", (200, 60), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((10, 10, 190, 50), fill=(20, 60, 140, 255))
    payload = _payload()
    base = payload["arxiv"]["data"]["papers"][0]
    payload["arxiv"]["data"]["papers"] = [
        {**base, "figure": "photo", "institution": {"name": "巴黎-萨克雷大学", "logo": "logo"}},
        {**base, "figure": "diagram", "institution": None},
        {**base, "institution": {"name": "Meta", "logo": "logo"}},
    ]
    images = {"photo": _picture((320, 240)), "diagram": diagram, "logo": logo}

    image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW, images)

    assert image.size == (800, 480)


def _embedded_run(run_id="r1", level=None, values=None):
    variables = [{"id": "seed", "is-subcategory": True}, {"id": "platform", "is-subcategory": False}]
    return {"id": run_id, "times": {"primary_t": 89.5}, "status": {"verify-date": "2026-10-21T20:51:21Z"},
            "category": {"data": {"id": "cat1", "name": "Any%", "variables": {"data": variables}}},
            "level": {"data": level or []}, "values": values or {"seed": "random", "platform": "pc"},
            "players": {"data": [{"rel": "user", "names": {"international": "Rockie"}}]}}


def test_a_run_keeps_its_leaderboard_and_only_the_variables_that_split_it():
    entry = sources.run_entry(_embedded_run())
    level_entry = sources.run_entry(_embedded_run(level={"id": "lvl9", "name": "World 1"}))

    assert (entry["id"], entry["category_id"], entry["level_id"]) == ("r1", "cat1", "")
    assert entry["subcategories"] == {"seed": "random"}
    assert (level_entry["level_id"], level_entry["category"]) == ("lvl9", "World 1 · Any%")


def test_a_world_record_is_first_place_on_its_own_leaderboard(monkeypatch):
    asked = []

    def get_json(method, url, context, **kwargs):
        asked.append((url, kwargs["params"]))
        return {"data": {"runs": [{"place": 1, "run": {"id": "r1"}}]}}

    monkeypatch.setattr(sources, "_get_json", get_json)
    full_game = sources.run_entry(_embedded_run())
    level = sources.run_entry(_embedded_run("r2", level={"id": "lvl9", "name": "World 1"}))

    assert sources.is_world_record("game1", full_game, _context()) is True
    assert sources.is_world_record("game1", level, _context()) is False
    assert asked == [
        (f"{sources.SPEEDRUN_LEADERBOARDS_URL}/game1/category/cat1", {"top": 1, "var-seed": "random"}),
        (f"{sources.SPEEDRUN_LEADERBOARDS_URL}/game1/level/lvl9/cat1", {"top": 1, "var-seed": "random"}),
    ]
    assert sources.is_world_record("../x", full_game, _context()) is None


def test_records_are_looked_up_once_a_run_and_a_failed_lookup_is_tried_again(monkeypatch):
    calls = []

    def lookup(game_id, run, context):
        calls.append(run["id"])
        if run["id"] == "bad":
            raise sources.HttpClientError("down")
        return run["id"] == "wr"

    monkeypatch.setattr(sources, "is_world_record", lookup)
    top = [{"id": "g", "runs": [{"id": "wr"}, {"id": "bad"}, {"id": "third"}]},
           {"id": "h", "runs": [{"id": "known"}]}]

    records = sources._world_records(top, {"known": True}, _context())

    assert calls == ["wr", "bad"]  # the third run is not shown; the known one is not asked again
    assert records == {"wr": True, "known": True}
    assert top[0]["runs"][0]["record"] is True and "record" not in top[0]["runs"][1]
    assert top[1]["runs"][0]["record"] is True


def test_the_run_feed_reads_at_most_three_pages(monkeypatch):
    pages = []

    def get_json(method, url, context, **kwargs):
        if url == sources.SPEEDRUN_RUNS_URL and "game" not in kwargs["params"]:
            pages.append(kwargs["params"]["offset"])
            start = len(pages) * sources.FEED_PAGE
            return {"data": [_feed_run("g", (start + index) / 100) for index in range(sources.FEED_PAGE)]}
        if url.startswith(sources.SPEEDRUN_GAMES_URL):
            return {"data": {"names": {"international": "Game"}}}
        return {"data": []}

    monkeypatch.setattr(sources, "_get_json", get_json)
    monkeypatch.setattr(sources, "wikipedia_cover", lambda name, context: None)
    monkeypatch.setattr(sources, "steam_cover", lambda name, context: None)

    sources.fetch_speedrun_week({}, NOW, _context())

    assert sources.MAX_FEED_PAGES == 3 and pages == [0, 200, 400]


def test_steam_wishlist_reads_names_and_app_ids():
    data = sources.normalize_wishlist({"items": [
        {"name": "Deadlock", "logo": "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/1422450/x/capsule_sm_120.jpg"},
        {"name": "地下城公会经理 Dungeons &amp; Dynasties", "logo": "https://x/no-app.jpg"},
        {"name": "  "}, "bad",
        *[{"name": f"Game {index}", "logo": ""} for index in range(6)]]})

    assert data["items"][:2] == [{"name": "Deadlock", "appid": 1422450},
                                 {"name": "地下城公会经理 Dungeons & Dynasties", "appid": None}]
    assert len(data["items"]) == sources.MAX_WISHLIST
    with pytest.raises(ValueError):
        sources.normalize_wishlist({"items": []})


def test_each_wishlisted_game_leads_with_its_small_steam_icon():
    from PIL import Image

    from plugins.tech_play import render

    payload = _payload()
    payload["wishlist"] = {"state": "live", "data": {"items": [
        {"name": "Deadlock", "appid": 1422450}, {"name": "Light No Fire 无火之光", "appid": 2719590},
        {"name": "No Icon Yet", "appid": 9}]}}
    deadlock = "https://shared.fastly.steamstatic.com/community_assets/images/apps/1422450/" + "a" * 40 + ".jpg"
    payload["icons"] = {"state": "live", "data": {"icons": {"Deadlock": deadlock}}}
    calls = []
    original = render._game_icon

    def spy(canvas, page, images, name, xy, size):
        calls.append((name, size))
        return original(canvas, page, images, name, xy, size)

    render._game_icon = spy
    try:
        image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW,
                            {deadlock: Image.new("RGB", (32, 32), (200, 30, 160))})
    finally:
        render._game_icon = original

    assert [call for call in calls if call[1] == render.WISHLIST_ICON] == [
        ("Deadlock", render.WISHLIST_ICON), ("Light No Fire 无火之光", render.WISHLIST_ICON),
        ("No Icon Yet", render.WISHLIST_ICON)]
    column = image.crop((render.UPCOMING_CARD[2] - render.WISHLIST_W - 8, render.UPCOMING_CARD[1],
                         render.UPCOMING_CARD[2], render.UPCOMING_CARD[3])).convert("RGB")
    assert sum(1 for pixel in column.get_flattened_data() if pixel == (200, 30, 160)) > 100


def test_the_icon_lookup_covers_the_wishlisted_games_by_their_app_ids():
    from plugins.tech_play.tech_play import icon_games

    payload = {"epic": {"data": {"current": [{"title": "Sorry We're Closed"}], "upcoming": []}},
               "upcoming": {"data": {"items": [{"name": "Deadlock", "image": None}, {"name": "Hela", "appid": 7}]}},
               "wishlist": {"data": {"items": [{"name": "Deadlock", "appid": 1422450},
                                               {"name": "神鬼寓言", "appid": 2769570}, {"name": ""}]}}}

    assert icon_games(payload) == [("Sorry We're Closed", None), ("Deadlock", None), ("Hela", 7),
                                   ("Deadlock", 1422450), ("神鬼寓言", 2769570)]
    # Without the list (its source failed), the rest still get theirs.
    assert icon_games({**payload, "wishlist": {"data": {}}})[-1] == ("Hela", 7)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_wishlist_column_and_world_record_tags_are_drawn(mode):
    from plugins.tech_play import render

    payload = _payload()
    payload["upcoming"]["data"]["items"] = [
        {"name": f"Game {index}", "date": None, "date_text": "2026", "platforms": ["PC"], "image": None}
        for index in range(4)]
    payload["wishlist"] = {"state": "live", "data": {"items": [{"name": "Deadlock"}, {"name": "Light No Fire 无火之光"}]}}
    payload["speedrun"]["data"]["top"][0]["runs"][0]["record"] = True
    drawn = []
    original = render.PageCanvas.text

    def text(canvas, xy, value, *args, **kwargs):
        drawn.append(str(value))
        return original(canvas, xy, value, *args, **kwargs)

    render.PageCanvas.text = text
    try:
        image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW, {})
    finally:
        render.PageCanvas.text = original

    assert image.size == (800, 480)
    assert {"愿望单最多", "Deadlock", "Light No Fire 无火之光", "WR", "Game 2"} <= set(drawn)
    assert "Game 3" not in drawn  # three coming games beside the list
    gold = render._rgb((render.NIGHT if mode == "night" else render.DAY)["gold"])
    speed = image.crop(render.SPEED_CARD).convert("RGB").get_flattened_data()
    assert sum(1 for pixel in speed if pixel == gold) > 60


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_header_names_the_sources_by_their_logos_between_the_title_and_the_date(monkeypatch, mode):
    from plugins.tech_play import render

    drawn = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda self, xy, text, *args, **kwargs: drawn.append(str(text)) or original(
        self, xy, text, *args, **kwargs))

    image = render.render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, NOW, {})

    # No subtitle in words, no decorative art: one row of official logos, clear of the title and the date.
    assert not any(text.startswith("服务状态 · ") for text in drawn)
    left, right = image.info["header_sources"]
    assert 220 < left < right < 650 and right - left > 250
    paper = image.getpixel((5, 5))
    assert sum(pixel != paper for pixel in image.crop((left, 8, right + 1, 48)).get_flattened_data()) > 2000
    # A row too wide gives up the spare logos first; nothing is drawn where nothing fits.
    canvas = render.PageCanvas(render._palette({"_inkypi_theme": {"mode": mode}}))
    wide = render._header_sources(canvas, 0, 2000)
    tight = render._header_sources(canvas, 0, (wide[1] - wide[0]) - 1)
    assert tight is not None and tight[1] - tight[0] < wide[1] - wide[0]
    assert render._header_sources(canvas, 0, 5) is None
