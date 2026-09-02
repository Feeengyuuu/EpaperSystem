from copy import deepcopy
import uuid

import pytest

from china_source_migration import MIGRATION, prepare_migration


def source_config():
    target = {"plugin_id": "box_office_top_movies", "instance_uuid": uuid.uuid4().hex,
              "structural_generation": 1, "settings_revision": 3,
              "plugin_settings": {"sourceMode": "maoyan_china", "itemsCount": 5},
              "refresh": {"interval": 21600}}
    peer = {**deepcopy(target), "instance_uuid": uuid.uuid4().hex, "plugin_id": "china_box_office_top_movies"}
    return {"config_revision": 9, "playlist_config": {"playlists": [{"plugins": [target, peer]}]}}, target


def test_migration_changes_only_exact_mainland_source_once():
    config, target = source_config()
    before = deepcopy(config)
    args = dict(instance_uuid=target["instance_uuid"], generation=1, revision=3)
    after = prepare_migration(config, **args)
    expected = deepcopy(before)
    expected["playlist_config"]["playlists"][0]["plugins"][0]["plugin_settings"]["sourceMode"] = "official_china"
    expected["playlist_config"]["playlists"][0]["plugins"][0]["settings_revision"] = 4
    expected["config_revision"] = 10
    expected["runtime_migrations"] = {MIGRATION: {
        "instance_uuid": target["instance_uuid"], "structural_generation": 1, "from_revision": 3, "to_revision": 4}}
    assert after == expected
    assert config == before
    assert prepare_migration(after, **args) == after


@pytest.mark.parametrize("change", ["uuid", "generation", "revision", "source", "north_america"])
def test_migration_rejects_drift_and_north_america(change):
    config, target = source_config()
    args = dict(instance_uuid=target["instance_uuid"], generation=1, revision=3)
    if change == "uuid":
        args["instance_uuid"] = uuid.uuid4().hex
    elif change == "generation":
        args["generation"] = 2
    elif change == "revision":
        args["revision"] = 4
    elif change == "source":
        target["plugin_settings"]["sourceMode"] = "the_numbers"
    else:
        args["instance_uuid"] = config["playlist_config"]["playlists"][0]["plugins"][1]["instance_uuid"]
    with pytest.raises(ValueError):
        prepare_migration(config, **args)
