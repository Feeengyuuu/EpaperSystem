"""Generated liveries must match an explicit season/team, with a neutral whole-field fallback."""

import json

import pytest
from PIL import Image, ImageDraw

from plugins.sports_dashboard import motogp_bikes


@pytest.fixture
def art_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(motogp_bikes, "ASSETS", tmp_path)
    for filename, colour in (("factory.png", (230, 30, 30)), ("castrol.png", (20, 170, 50)),
                             ("pro.png", (20, 80, 220)), ("rider.png", (160, 30, 160))):
        art = Image.new("RGBA", (66, 30))
        ImageDraw.Draw(art).rectangle((2, 2, 63, 27), fill=(*colour, 255))
        art.save(tmp_path / filename)
    manifest = {"kind": "generated_livery_schematic", "fallback": "neutral.png", "seasons": {
        "2026": {"teams": {
            "ducati_factory": {"file": "factory.png", "aliases": ["Ducati Lenovo Team"]},
            "lcr_castrol": {"file": "castrol.png", "aliases": ["Castrol Honda LCR"]},
            "lcr_pro": {"file": "pro.png", "aliases": ["Pro Honda LCR"]},
        }, "riders": {"93": {"file": "rider.png"}}}}}
    (tmp_path / "sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def _colours(art):
    return {pixel[:3] for pixel in art.get_flattened_data() if pixel[3] > 240}


@pytest.mark.parametrize("panel", [(255, 255, 255), (0, 0, 0)], ids=["day", "night"])
def test_matching_team_livery_and_rider_override_are_local_transparent_and_fit(art_dir, panel):
    factory = motogp_bikes.load_bike({"team": "Ducati-Lenovo TEAM", "number": 63}, 2026, (31, 14), panel)
    rider = motogp_bikes.load_bike({"team": "Ducati Lenovo Team", "number": 93}, 2026, (31, 14), panel)
    assert (230, 30, 30) in _colours(factory)
    assert (160, 30, 160) in _colours(rider)
    for art in (factory, rider):
        assert art.mode == "RGBA" and 1 <= art.width <= 31 and 1 <= art.height <= 14
        assert art.getchannel("A").getextrema()[0] < 255


def test_sponsor_variants_with_the_same_constructor_do_not_share_a_livery(art_dir):
    castrol = motogp_bikes.load_bike({"team": "Castrol Honda LCR", "constructor": "Honda"},
                                    2026, (31, 14), (0, 0, 0))
    pro = motogp_bikes.load_bike({"team": "Pro Honda LCR", "constructor": "Honda"},
                                2026, (31, 14), (0, 0, 0))
    assert (20, 170, 50) in _colours(castrol)
    assert (20, 80, 220) in _colours(pro)


@pytest.mark.parametrize("team,season,number", [("Ducati Lenovo Team", 2027, 93), ("Ducati Lenovo Team", None, 93),
                                               ("Ducati Lenovo Team", "2026", 93), ("Honda", 2026, 63),
                                               ("New Team", 2026, 63)])
def test_future_year_unknown_team_or_invalid_year_gets_a_neutral_bike(art_dir, team, season, number):
    art = motogp_bikes.load_bike({"team": team, "constructor": "Ducati", "number": number},
                                season, (31, 14), (255, 255, 255))
    assert art.getchannel("A").getbbox()
    assert all(red == green == blue for red, green, blue in _colours(art))


@pytest.mark.parametrize("failure", ["missing", "broken_json", "wrong_kind", "bad_file", "opaque", "traversal"])
def test_missing_or_invalid_assets_still_provide_a_neutral_bike(art_dir, failure):
    manifest_file = art_dir / "sources.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if failure == "missing":
        manifest_file.unlink()
    elif failure == "broken_json":
        manifest_file.write_text("{", encoding="utf-8")
    else:
        if failure == "wrong_kind":
            manifest["kind"] = "official_photograph"
        elif failure == "bad_file":
            (art_dir / "factory.png").write_bytes(b"broken")
        elif failure == "opaque":
            Image.new("RGB", (60, 24), "red").save(art_dir / "factory.png")
        elif failure == "traversal":
            manifest["seasons"]["2026"]["teams"]["ducati_factory"]["file"] = "../factory.png"
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    art = motogp_bikes.load_bike({"team": "Ducati Lenovo Team", "number": 63}, 2026, (31, 14), (0, 0, 0))
    assert art.getchannel("A").getbbox()
    assert all(red == green == blue for red, green, blue in _colours(art))
