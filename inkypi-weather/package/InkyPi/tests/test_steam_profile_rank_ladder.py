"""The Steam level read as a ranked ladder with generated promotion borders."""

import pytest

from plugins.steam_profile_dashboard.rank_ladder import TIERS, rank_border, rank_for


@pytest.mark.parametrize(("level", "title", "next_name", "levels_to_next"), [
    (0, "黑铁 IV", "黑铁 III", 3),
    (9, "黑铁 I", "青铜", 1),
    (30, "黄金 IV", "黄金 III", 5),
    (104, "钻石 III", "钻石 II", 1),
    (119, "钻石 I", "大师", 1),
    (130, "大师", "宗师", 20),
    (199, "宗师", "王者", 1),
    (480, "王者", "", 0),
])
def test_levels_climb_tiers_and_divisions(level, title, next_name, levels_to_next):
    rank = rank_for(level)
    assert (rank.title, rank.next_name, rank.levels_to_next) == (title, next_name, levels_to_next)


def test_lp_is_the_xp_share_of_the_current_level():
    badges = {"player_xp": 59406, "player_xp_needed_current_level": 59100, "player_xp_needed_to_level_up": 694}
    assert rank_for(104, badges).lp == 31
    assert rank_for(104, {}).lp is None
    assert rank_for("not a level").title == "黑铁 IV"


def test_every_tier_has_a_border_with_an_empty_centre():
    for key, *_ in TIERS:
        border, (cx, cy, radius) = rank_border(key, 98)
        assert border is not None and border.size == (98, 98)
        assert radius > 12
        assert border.getpixel((round(cx), round(cy)))[3] < 24
        assert border.getchannel("A").getbbox() is not None
