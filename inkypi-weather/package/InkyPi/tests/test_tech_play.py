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
