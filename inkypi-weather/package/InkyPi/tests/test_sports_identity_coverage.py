from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from plugins.sports_dashboard import cs2_branding
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


def test_catalog_reads_real_small_event_layout_and_keeps_lan_out_of_the_title():
    html = '''
    <a class="ongoing-event" href="/events/8266/fissure-playground-3">
      <div class="event-name-small"><div class="text-ellipsis">FISSURE Playground 3</div>
      <div class="lan-marker">LAN</div></div>
      <span data-unix="1788861600000"></span><span data-unix="1789293600000"></span>
      <img class="logo day-only" title="FISSURE Playground 3"
        src="https://img-cdn.hltv.org/eventlogo/day.png"
        srcset="https://img-cdn.hltv.org/eventlogo/day-100.png 2x">
      <img class="small-team-logo" title="Team" src="https://img-cdn.hltv.org/teamlogo/team.png">
    </a>
    <a class="small-event" href="/events/9244/pgl-asia-qualifier">
      <td class="event-col"><div class="text-ellipsis">PGL Asia Qualifier</div></td>
      <span data-unix="1789293600000"></span>
      <img class="logo" title="PGL Asia Qualifier" src="https://img-cdn.hltv.org/eventlogo/pgl.png">
    </a>'''
    events = cs2_branding.parse_catalog(html)
    assert len(events) == 2
    assert events[0]["name"] == "FISSURE Playground 3"
    assert events[0]["event_logo_url"].endswith("day-100.png")
    assert events[1]["start"] == events[1]["end"]
    assert events[1]["event_logo_url"].endswith("pgl.png")


def test_catalog_logo_avoids_detail_request_and_missing_event_does_not_block_another(monkeypatch, tmp_path):
    monkeypatch.setattr(cs2_branding, "_local_events", lambda: [])
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    calls = []

    def fetch(_client, path, _context):
        calls.append(path)
        assert path == "/events", "Catalog images must avoid an extra request per event"
        return '''<a class="small-event" href="/events/1/fissure-playground-3">
        <td class="event-col"><div class="text-ellipsis">FISSURE Playground 3</div></td>
        <span data-unix="1788861600000"></span><span data-unix="1789293600000"></span>
        <img class="logo" title="FISSURE Playground 3" src="https://img-cdn.hltv.org/eventlogo/fissure.png"></a>'''

    monkeypatch.setattr(cs2_branding, "_text", fetch)
    for index, name in enumerate(("No such tournament", "FISSURE Playground 3")):
        card = {"event_id": f"series:{index}", "event_name": name,
                "main": {"start": now, "event_name": name}}
        result = cs2_branding.enrich_event_branding(plugin, card, {}, now, SimpleNamespace())
    assert result["event_logo_url"].endswith("fissure.png")
    assert calls == ["/events"]


def test_bundled_event_logos_match_exact_edition_and_date_offline():
    manifest = json.loads((cs2_branding.LOCAL_EVENT_DIR / "manifest.json").read_text(encoding="utf-8"))
    for asset in manifest["assets"]:
        path = cs2_branding.LOCAL_EVENT_DIR / asset["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]
        with Image.open(path) as logo:
            logo.load()
            assert "A" in logo.getbands() or "transparency" in logo.info
    for event in manifest["events"]:
        main = {"event_name": event["name"], "start": event["start"]}
        card = {"series": "CS", "event_name": event["name"], "main": main}
        assert cs2_branding.local_event_branding(card).get("event_logo_path")
        assert not cs2_branding.local_event_branding({**card, "main": {**main, "start": "2030-01-01T00:00:00Z"}})
    assert not cs2_branding.local_event_branding({"event_name": "Unannounced Cup", "main": {"start": "2026-09-12T00:00:00Z"}})


@pytest.mark.parametrize("code,english,short,full", [
    ("TA&M", "Texas A&M Aggies", "德克萨斯农工", "德克萨斯农工"),
    ("UAB", "UAB Blazers", "阿拉巴马伯明翰", "阿拉巴马伯明翰开拓者"),
    ("MASS", "Massachusetts Minutemen", "马萨诸塞", "马萨诸塞民兵"),
    ("GWEB", "Gardner-Webb", "加德纳韦伯", "加德纳韦伯奔跑斗牛犬"),
    ("RGV", "UT Rio Grande", "德州格兰德河谷", "德州格兰德河谷牛仔"),
    ("TCU", "TCU Horned Frogs", "德克萨斯基督教", "德克萨斯基督教角蛙"),
    ("UCLA", "UCLA Bruins", "加州洛杉矶", "加州洛杉矶棕熊"),
])
def test_ncaa_relocalizes_english_cached_zh_fields_in_every_name_entrypoint(code, english, short, full):
    event = {"team_a": english, "team_a_zh": english, "team_a_name": english,
             "team_a_code": code, "team_a_rank": 8, "possession": code}
    assert SportsDashboard._ncaa_school_label(event, "a") == short
    assert SportsDashboard._ncaa_school_label(event, "a", full=True) == full
    assert SportsDashboard._football_display_team(event, "a", "NCAA") == f"#8 {short}"
    assert SportsDashboard._football_display_team(event, "a", "NCAA", full=True) == f"#8 {full}"
    assert SportsDashboard._football_possession_display_label(event, "NCAA") == short


def test_ncaa_ids_disambiguate_shared_abbreviations_and_unknown_names_remain_honest():
    # SDST is South Dakota State; SDSU is already used for San Diego State.
    info = SportsDashboard._football_team_info({"team": {"id": "2571", "abbreviation": "SDSU"}}, "NCAA", False)
    assert info["zh"] == "南达科他州立"
    assert info["code"] == "SDST"
    event = {"team_a_id": "2571", "team_a_code": "SDSU", "team_a_zh": "South Dakota State"}
    assert SportsDashboard._ncaa_school_label(event, "a") == "南达科他州立"
    del event["team_a_id"]
    assert SportsDashboard._ncaa_school_label(event, "a") == "南达科他州立"
    assert SportsDashboard._ncaa_display_school_name("NEW", "New College") == "New College"
    assert SportsDashboard._ncaa_display_school_name("TBD") == "待定"
    assert SportsDashboard._ncaa_school_label({"team_a_zh": "自定义学校"}, "a") == "自定义学校"


def test_ncaa_legacy_short_aliases_work_without_team_code():
    assert SportsDashboard._ncaa_school_label({"team_a": "Abilene Chrstn", "team_a_zh": "Abilene Chrstn"}, "a") == "阿比林基督教"
    assert SportsDashboard._ncaa_display_school_name("TAMUC", "Texas A&M-Commerce") == "东德克萨斯农工"


def test_repeated_event_render_reuses_decoded_logo_and_never_fetches(monkeypatch):
    from plugins.sports_dashboard import common

    plugin = SportsDashboard({"id": "sports_dashboard"})
    card = {"series": "CS", "event_name": "FISSURE Playground 3",
            "event_logo_path": "/removed-previous-release/logo.png",
            "main": {"start": "2026-09-12T12:00:00Z"}}
    decodes = []
    original = common.safe_open_image

    def decode(*args, **kwargs):
        decodes.append(args[0])
        return original(*args, **kwargs)

    def no_network(*args, **kwargs):
        pytest.fail("Bundled logos must render without a remote download")

    monkeypatch.setattr(common, "safe_open_image", decode)
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", no_network)
    with Image.new("RGB", (181, 59), common.COLORS["panel"]) as image:
        for _ in range(5):
            assert plugin._draw_valve_focus_event_logo(image, (0, 0, 180, 58), card)
    assert len(decodes) == 1
