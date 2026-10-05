from plugins.steam_profile_dashboard.steam_profile_dashboard import SteamProfileDashboard


def plugin():
    return SteamProfileDashboard({"id": "steam_profile_dashboard"})


def test_remark_follows_steamid_when_persona_changes_and_duplicate_names_exist():
    subject = plugin()
    data = {"friends": [
        {"steamid": "76561198000000001", "personaname": "New name"},
        {"steamid": "76561198000000002", "personaname": "New name"},
    ], "online_friend_count": 6, "friend_count": 57}
    subject._apply_friend_remarks(data, {"friendRemarks": "76561198000000001=城"})
    assert [subject._friend_display_id(friend) for friend in data["friends"]] == ["城", "New name"]
    assert (data["online_friend_count"], data["friend_count"]) == (6, 57)


def test_remark_survives_replacing_public_friend_summaries(monkeypatch):
    subject = plugin()
    data = {"friends": [{"steamid": "76561198000000001", "personaname": "Before"}]}
    settings = {"friendRemarks": {"76561198000000001": "Chao Chao"}}
    subject._apply_friend_remarks(data, settings)
    monkeypatch.setattr(subject, "_steam_api", lambda *args: {
        "response": {"players": [{"steamid": "76561198000000001", "personaname": "After", "personastate": 1}]},
    })
    assert subject._refresh_cached_friend_statuses("unused", data) == 1
    subject._apply_friend_remarks(data, settings)
    assert data["friends"][0]["personaname"] == "After"
    assert subject._friend_display_id(data["friends"][0]) == "Chao Chao"


def test_removing_remark_restores_current_persona_including_cached_data():
    subject = plugin()
    data = {"friends": [{"steamid": "76561198000000001", "personaname": "Current"}]}
    subject._apply_friend_remarks(data, {"friendRemarks": "76561198000000001=Old remark"})
    subject._apply_friend_remarks(data, {})
    assert subject._friend_display_id(data["friends"][0]) == "Current"
    assert subject._friend_display_id({"steamid": "76561198000000002"}) == "76561198000000002"


def test_json_and_multiline_settings_have_equivalent_cache_identity():
    subject = plugin()
    line = {"friendRemarks": "76561198000000001=  Chao  Chao \n"}
    document = {"friendRemarks": '{"76561198000000001":"Chao Chao"}'}
    assert subject._cache_key(line, (800, 480), "owner") == subject._cache_key(document, (800, 480), "owner")
    assert subject._cache_key(line, (800, 480), "owner") != subject._cache_key({}, (800, 480), "owner")


def test_malformed_and_non_id_remark_keys_do_not_relabel_a_friend():
    subject = plugin()
    assert subject._friend_remarks({"friendRemarks": '{broken'}) == {}
    assert subject._friend_remarks({"friendRemarks": {"Name": "Wrong", "76561198000000001": 3}}) == {}
    assert subject._friend_remarks({"friendRemarks": "# comment\nName=Wrong\n76561198000000001=Valid"}) == {"76561198000000001": "Valid"}


def test_missing_assets_do_not_break_real_plugin_provider_integration(tmp_path):
    from plugins.steam_profile_dashboard.game_assets import SteamGameAssets
    subject = plugin()
    with SteamGameAssets(tmp_path, budget_seconds=0) as assets:
        subject._game_assets = assets
        assert subject._game_square_icon({}, "108600", 28) is None
        assert subject._game_background({}, "108600", (390, 160)) is None
