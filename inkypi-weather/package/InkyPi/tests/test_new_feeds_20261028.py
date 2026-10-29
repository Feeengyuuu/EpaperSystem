"""JPL's sky watch on the APOD page, OpenRouter's models on Tech Pulse and the NHL on the sports page."""
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"), str(Path(__file__).resolve().parents[1])]

from plugins.apod import apod_page, sky_watch  # noqa: E402
from plugins.sports_dashboard import hockey  # noqa: E402
from plugins.tech_pulse import openrouter  # noqa: E402

NOW = datetime(2026, 10, 29, 4, 0, tzinfo=timezone.utc)


def test_neos_are_nearest_first_with_lunar_distances_and_sizes():
    payload = {"fields": ["des", "orbit_id", "jd", "cd", "dist", "dist_min", "dist_max", "v_rel", "v_inf", "t_sigma_f",
                          "h", "diameter", "diameter_sigma"],
               "data": [["2025 UK9", "1", "", "2026-Oct-31 02:35", "0.0021", "", "", "7.83", "", "", "29.83", None, None],
                        ["2026 UE2", "1", "", "2026-Oct-29 01:26", "0.000827", "", "", "7.67", "", "", "29.47", None, None]]}
    neos = sky_watch.normalize_neos(payload)

    assert [neo["name"] for neo in neos["objects"]] == ["2026 UE2", "2025 UK9"]
    assert round(neos["objects"][0]["lunar"], 2) == 0.32
    assert 3 < neos["objects"][0]["size_m"] < 7
    name, detail = apod_page.neo_copy(neos["objects"][0])
    assert name == "2026 UE2" and detail.startswith("0.32 LD · 10/28 18:26")


def test_tonights_planet_is_its_highest_dark_hour():
    result = ("$$SOE\n"
              " 2026-Oct-29 03:00 *m   120.0  40.000   -2.0  5.0\n"
              " 2026-Oct-29 08:00  m   150.0  55.000   -2.0  5.0\n"
              " 2026-Oct-29 09:00 Nm   170.0  30.000   -2.0  5.0\n"
              " 2026-Oct-29 10:00      190.0   5.000   -2.0  5.0\n"
              "$$EOE\n")
    planet = sky_watch.parse_planet(result, "木星")

    assert (planet["elevation"], planet["direction"]) == (55.0, "东南")
    assert sky_watch.parse_planet("$$SOE\n 2026-Oct-29 08:00 *m   150.0  55.000  -2.0  5.0\n$$EOE", "木星") is None
    start, stop = sky_watch.tonight(datetime(2026, 10, 28, 21, 30, tzinfo=sky_watch.PACIFIC))
    assert (start.hour, stop.hour) == (1, 13)


def test_apod_page_draws_the_sky_watch_and_tonights_planets():
    from tests import test_apod

    sky = {"neos": {"count": 2, "objects": [{"name": "2026 UE2", "when": "2026-10-29T01:26:00+00:00", "lunar": 0.32,
                                              "speed": 7.7, "size_m": 5}, {"name": "2025 UK9", "lunar": 0.82,
                                                                          "when": "2026-10-31T02:35:00+00:00"}]},
           "planets": {"planets": [{"name": "木星", "when": "2026-10-29T13:00:00+00:00", "direction": "东南"}]}}
    weather = replace(test_apod._page_weather(), alert=None, donki_event=None, alert_state="confirmed_empty")
    record = test_apod._page_record(title_en="Noctilucent Clouds", title_zh="夜光云")
    args = dict(apod=record, title_zh=record.title_zh, translation_unavailable=False, weather=weather,
                source_image=Image.new("RGB", (1200, 900), (20, 30, 60)), rendered_at_utc=test_apod.NOW_UTC)
    with_sky = apod_page.render_apod_page(**args, sky=sky)
    without = apod_page.render_apod_page(**args)

    assert with_sky.size == (800, 480)
    region = apod_page.NEO_RECT
    assert with_sky.crop(region).tobytes() != without.crop(region).tobytes()
    assert apod_page.planets_copy(sky) == "今夜可见 木星 东南 06时"


def test_openrouter_keeps_the_newest_distinct_models_and_counts_the_week():
    week_ago = NOW.timestamp() - 3 * 86400
    rows = [{"id": "openai/gpt-6.1-terra-pro", "name": "OpenAI: GPT-6.1 Terra Pro", "created": week_ago + 10,
             "context_length": 1_100_000, "pricing": {"prompt": "0.000001", "completion": "0.000005"},
             "architecture": {"input_modalities": ["text", "image"]}},
            {"id": "openai/gpt-6.1-terra", "name": "OpenAI: GPT-6.1 Terra", "created": week_ago + 5},
            {"id": "openai/gpt-6.1-terra-pro:batch", "created": week_ago + 1},
            {"id": "moonshotai/kimi-k3.1-pro", "name": "MoonshotAI: Kimi K3.1 Pro", "created": week_ago,
             "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "old/model", "created": NOW.timestamp() - 30 * 86400}]
    data = openrouter.normalize_models({"data": rows}, NOW)

    assert [model["id"] for model in data["models"]] == ["openai/gpt-6.1-terra-pro", "moonshotai/kimi-k3.1-pro", "old/model"]
    assert data["week"] == 3
    first = data["models"][0]
    assert (first["name"], first["vision"], openrouter.context_text(first["context"])) == ("GPT-6.1 Terra Pro", True, "1.1M")
    assert openrouter.price_text(first) == "$1/$5"
    assert openrouter.price_text(data["models"][1]) == "免费"


def test_nhl_games_take_chinese_names_hockey_leaders_and_periods():
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    def competitor(code, side, score, goals):
        return {"homeAway": side, "score": str(score), "winner": side == "home",
                "team": {"abbreviation": code, "displayName": code, "shortDisplayName": code, "color": "d71830"},
                "linescores": [{"value": 1, "displayValue": "1", "period": 1}, {"value": 2, "displayValue": "2", "period": 2}],
                "records": [{"name": "YTD", "summary": "7-4-0"}],
                "leaders": [{"name": "goals", "leaders": [{"displayValue": str(goals), "athlete": {"shortName": "A. Tuch"}}]},
                            {"name": "points", "leaders": [{"displayValue": "3", "athlete": {"shortName": "D. Strome"}}]}]}
    payload = hockey.merge_scoreboards([
        {"events": [{"id": "1", "date": "2026-10-28T23:00Z", "competitions": [{
            "date": "2026-10-28T23:00Z", "status": {"period": 3, "type": {"state": "post", "completed": True,
                                                                          "shortDetail": "Final"}},
            "venue": {"fullName": "SAP Center at San Jose"},
            "competitors": [competitor("VAN", "away", 2, 1), competitor("SJ", "home", 5, 2)]}]}]},
        {"events": [{"id": "1"}]},
    ])
    events = hockey.parse_scoreboard(SportsDashboard, payload, timezone.utc)["events"]

    assert len(events) == 1
    game = events[0]
    assert (game["sport"], game["team_a"], game["team_b"]) == ("NHL", "加人", "鲨鱼")
    assert hockey.display_team(game, "b", full=True) == "圣何塞鲨鱼"
    assert [leader["label"] for leader in game["team_leaders_b"]] == ["G", "PTS"]
    assert [hockey.period_label(n) for n in (1, 3, 4, 5)] == ["P1", "P3", "OT", "2OT"]
    assert hockey.scoreboard_days(datetime(2026, 10, 28).date()) == ["20261027", "20261028", "20261029", "20261030"]
