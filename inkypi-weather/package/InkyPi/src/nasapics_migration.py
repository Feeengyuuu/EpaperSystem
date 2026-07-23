"""Release-bound one-time migration for the production NASAPics instance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING
from uuid import UUID

from model import PlaylistManager, PluginInstanceSnapshot

if TYPE_CHECKING:
    from config import Config


DEFAULT_PLAYLIST_NAME = "DailyDoseOfDay"
DEFAULT_PLUGIN_ID = "apod"
DEFAULT_INSTANCE_NAME = "NASAPics"
NASAPICS_MIGRATION_ID = "nasapics_space_weather_v1"
NASAPICS_EXPECTATION_NAME = ".nasapics-space-weather-v1.expectation.json"
MAX_EXPECTATION_BYTES = 64 * 1024
TARGET_REFRESH = {"interval": 1800}
TARGET_SETTINGS = {
    "randomizeApod": False,
    "customDate": "",
    "refreshOnDisplay": False,
}


class NasapicsMigrationError(RuntimeError):
    """The exact production migration cannot be applied safely."""


@dataclass(frozen=True)
class ExpectedNasapicsIdentity:
    instance_uuid: str
    structural_generation: int
    settings_revision: int

    def __post_init__(self) -> None:
        try:
            UUID(self.instance_uuid)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("instance_uuid must be a UUID") from error
        for field_name in ("structural_generation", "settings_revision"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")


@dataclass(frozen=True)
class NasapicsMigrationResult:
    playlist_name: str
    before: PluginInstanceSnapshot
    after: PluginInstanceSnapshot


@dataclass(frozen=True)
class ReleaseBoundNasapicsExpectation:
    release_id: str
    expected: ExpectedNasapicsIdentity
    playlist_name: str = DEFAULT_PLAYLIST_NAME
    plugin_id: str = DEFAULT_PLUGIN_ID
    instance_name: str = DEFAULT_INSTANCE_NAME


def _thaw_json(value):
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_json(item) for item in value]
    return value


def _reject_duplicate_json_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def load_release_bound_expectation(
    path,
    *,
    release_id: str,
) -> ReleaseBoundNasapicsExpectation:
    expectation_path = Path(path)
    if expectation_path.is_symlink() or not expectation_path.is_file():
        raise NasapicsMigrationError("NASAPics expectation is not a regular file")
    try:
        with expectation_path.open("rb") as stream:
            payload = stream.read(MAX_EXPECTATION_BYTES + 1)
    except OSError as error:
        raise NasapicsMigrationError("NASAPics expectation cannot be read") from error
    if len(payload) > MAX_EXPECTATION_BYTES:
        raise NasapicsMigrationError("NASAPics expectation is too large")
    try:
        document = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise NasapicsMigrationError("NASAPics expectation is malformed") from error
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "migration",
        "release_id",
        "target",
    }:
        raise NasapicsMigrationError("NASAPics expectation has an invalid schema")
    if (
        type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["migration"] != NASAPICS_MIGRATION_ID
    ):
        raise NasapicsMigrationError("NASAPics expectation has an invalid schema")
    captured_release = document["release_id"]
    if (
        not isinstance(captured_release, str)
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}",
            captured_release,
        )
        or captured_release != release_id
    ):
        raise NasapicsMigrationError("NASAPics expectation release mismatch")
    target = document["target"]
    if not isinstance(target, dict) or set(target) != {
        "playlist_name",
        "plugin_id",
        "instance_name",
        "instance_uuid",
        "structural_generation",
        "settings_revision",
    }:
        raise NasapicsMigrationError("NASAPics expectation target is malformed")
    if (
        target["playlist_name"] != DEFAULT_PLAYLIST_NAME
        or target["plugin_id"] != DEFAULT_PLUGIN_ID
        or target["instance_name"] != DEFAULT_INSTANCE_NAME
    ):
        raise NasapicsMigrationError("NASAPics expectation target is malformed")
    try:
        expected = ExpectedNasapicsIdentity(
            instance_uuid=target["instance_uuid"],
            structural_generation=target["structural_generation"],
            settings_revision=target["settings_revision"],
        )
    except (TypeError, ValueError) as error:
        raise NasapicsMigrationError("NASAPics expectation target is malformed") from error
    return ReleaseBoundNasapicsExpectation(
        release_id=captured_release,
        expected=expected,
    )


def _prepare_detached_migration(
    playlist_manager: PlaylistManager,
    *,
    expected: ExpectedNasapicsIdentity,
    playlist_name: str,
    plugin_id: str,
    instance_name: str,
) -> tuple[dict, PlaylistManager, NasapicsMigrationResult]:
    baseline = playlist_manager.to_dict()
    matches = [
        instance
        for playlist in baseline.get("playlists", [])
        if playlist.get("name") == playlist_name
        for instance in playlist.get("plugins", [])
        if instance.get("plugin_id") == plugin_id
        and instance.get("name") == instance_name
    ]
    if len(matches) != 1:
        raise NasapicsMigrationError(
            f"expected exactly one {playlist_name}/{plugin_id}/{instance_name} target"
        )

    detached = PlaylistManager.from_dict(baseline)
    selection = detached.resolve_plugin_instance_snapshot(
        playlist_name,
        plugin_id,
        instance_name,
    )
    if selection is None:
        raise NasapicsMigrationError("NASAPics target could not be resolved")
    before = selection.instance
    if before.instance_uuid != expected.instance_uuid:
        raise NasapicsMigrationError("NASAPics UUID drift")
    if before.structural_generation != expected.structural_generation:
        raise NasapicsMigrationError("NASAPics structural generation drift")
    if before.settings_revision != expected.settings_revision:
        raise NasapicsMigrationError("NASAPics settings revision drift")

    settings = _thaw_json(before.settings)
    if (
        all(settings.get(key) == value for key, value in TARGET_SETTINGS.items())
        and _thaw_json(before.refresh) == TARGET_REFRESH
    ):
        raise NasapicsMigrationError(
            "NASAPics target is already migrated without a matching marker"
        )
    settings.update(TARGET_SETTINGS)
    mutation = detached.update_plugin_instance_atomic(
        before.instance_uuid,
        settings=settings,
        refresh=dict(TARGET_REFRESH),
        expected_generation=before.structural_generation,
        expected_settings_revision=before.settings_revision,
    )
    if mutation is None or mutation.new_snapshot is None:
        raise NasapicsMigrationError("NASAPics model CAS conflict")
    after = mutation.new_snapshot
    if after.settings_revision != before.settings_revision + 1:
        raise NasapicsMigrationError("NASAPics revision did not increase exactly once")
    return (
        baseline,
        detached,
        NasapicsMigrationResult(
            playlist_name=mutation.playlist_name,
            before=before,
            after=after,
        ),
    )


def _expected_playlist_after(
    playlist_config,
    *,
    expected: ExpectedNasapicsIdentity,
    playlist_name: str,
    plugin_id: str,
    instance_name: str,
) -> dict:
    expected_after = _thaw_json(playlist_config)
    matches = [
        instance
        for playlist in expected_after.get("playlists", [])
        if playlist.get("name") == playlist_name
        for instance in playlist.get("plugins", [])
        if instance.get("plugin_id") == plugin_id
        and instance.get("name") == instance_name
    ]
    if len(matches) != 1:
        raise NasapicsMigrationError(
            f"expected exactly one {playlist_name}/{plugin_id}/{instance_name} target"
        )
    target = matches[0]
    if (
        target.get("instance_uuid") != expected.instance_uuid
        or target.get("structural_generation") != expected.structural_generation
        or target.get("settings_revision") != expected.settings_revision
    ):
        raise NasapicsMigrationError(
            "authoritative NASAPics identity changed during exact-diff validation"
        )
    settings = target.get("plugin_settings")
    if not isinstance(settings, Mapping):
        raise NasapicsMigrationError("NASAPics settings are malformed")
    updated_settings = _thaw_json(settings)
    updated_settings.update(TARGET_SETTINGS)
    target["plugin_settings"] = updated_settings
    target["refresh"] = dict(TARGET_REFRESH)
    target["settings_revision"] = expected.settings_revision + 1
    return expected_after


def _assert_exact_playlist_transition(
    before,
    after,
    *,
    expected: ExpectedNasapicsIdentity,
    playlist_name: str,
    plugin_id: str,
    instance_name: str,
) -> dict:
    expected_after = _expected_playlist_after(
        before,
        expected=expected,
        playlist_name=playlist_name,
        plugin_id=plugin_id,
        instance_name=instance_name,
    )
    if _thaw_json(after) != expected_after:
        raise NasapicsMigrationError(
            "NASAPics migration would modify fields outside the approved exact diff"
        )
    return expected_after


def _assert_exact_config_transition(
    before,
    after,
    *,
    expected_playlist_after,
    config_updates,
) -> None:
    expected_after = _thaw_json(before)
    expected_after.update(_thaw_json(config_updates))
    expected_after["playlist_config"] = _thaw_json(expected_playlist_after)
    actual = _thaw_json(after)

    actual_revision = actual.get("config_revision")
    before_revision = expected_after.get("config_revision")
    if (
        type(actual_revision) is not int
        or actual_revision < 1
        or (
            type(before_revision) is int
            and actual_revision <= before_revision
        )
    ):
        raise NasapicsMigrationError(
            "persisted config revision did not advance after NASAPics migration"
        )
    expected_after["config_revision"] = actual_revision
    if "schema_version" not in expected_after:
        expected_after["schema_version"] = 1
    if actual != expected_after:
        raise NasapicsMigrationError(
            "persisted config differs outside the approved NASAPics exact diff"
        )


def _expected_marker(expectation, result):
    return {
        "release_id": expectation.release_id,
        "instance_uuid": result.before.instance_uuid,
        "structural_generation": result.before.structural_generation,
        "before_settings_revision": result.before.settings_revision,
        "after_settings_revision": result.after.settings_revision,
    }


def _validate_existing_marker(marker, expectation):
    if not isinstance(marker, Mapping) or set(marker) != {
        "release_id",
        "instance_uuid",
        "structural_generation",
        "before_settings_revision",
        "after_settings_revision",
    }:
        raise NasapicsMigrationError("NASAPics migration marker is malformed")
    expected = expectation.expected
    if (
        not isinstance(marker["release_id"], str)
        or not isinstance(marker["instance_uuid"], str)
        or type(marker["structural_generation"]) is not int
        or type(marker["before_settings_revision"]) is not int
        or type(marker["after_settings_revision"]) is not int
        or marker["release_id"] != expectation.release_id
        or marker["instance_uuid"] != expected.instance_uuid
        or marker["structural_generation"] != expected.structural_generation
        or marker["before_settings_revision"] != expected.settings_revision
        or marker["after_settings_revision"] != expected.settings_revision + 1
    ):
        raise NasapicsMigrationError("NASAPics migration marker does not match expectation")


def apply_release_bound_nasapics_migration(
    config: "Config",
    *,
    expectation_path,
    release_id: str,
) -> NasapicsMigrationResult | None:
    path = Path(expectation_path)
    if not os.path.lexists(path):
        return None
    expectation = load_release_bound_expectation(path, release_id=release_id)
    migrations = config.get_config("runtime_migrations", default={})
    if not isinstance(migrations, Mapping):
        raise NasapicsMigrationError("runtime migration state is malformed")
    if NASAPICS_MIGRATION_ID in migrations:
        _validate_existing_marker(
            migrations[NASAPICS_MIGRATION_ID],
            expectation,
        )
        return None

    (
        expected_config_version,
        authoritative_config,
        authoritative_baseline,
        authoritative_manager,
    ) = config.capture_detached_playlist_transaction()
    migrations = authoritative_config.get("runtime_migrations", {})
    if not isinstance(migrations, Mapping):
        raise NasapicsMigrationError("runtime migration state is malformed")
    if NASAPICS_MIGRATION_ID in migrations:
        _validate_existing_marker(
            migrations[NASAPICS_MIGRATION_ID],
            expectation,
        )
        return None
    _baseline, detached, result = _prepare_detached_migration(
        authoritative_manager,
        expected=expectation.expected,
        playlist_name=expectation.playlist_name,
        plugin_id=expectation.plugin_id,
        instance_name=expectation.instance_name,
    )
    expected_playlist_after = _assert_exact_playlist_transition(
        authoritative_baseline,
        detached.to_dict(),
        expected=expectation.expected,
        playlist_name=expectation.playlist_name,
        plugin_id=expectation.plugin_id,
        instance_name=expectation.instance_name,
    )
    marker = _expected_marker(expectation, result)
    migration_state = _thaw_json(migrations)
    migration_state[NASAPICS_MIGRATION_ID] = marker
    config.commit_detached_playlist_transaction(
        expected_config_version=expected_config_version,
        expected_config_data=authoritative_config,
        expected_playlist_config=authoritative_baseline,
        playlist_manager=detached,
        config_updates={"runtime_migrations": migration_state},
    )
    persisted_marker = config.get_config(
        "runtime_migrations",
        default={},
    ).get(NASAPICS_MIGRATION_ID)
    persisted = config.playlist_manager.resolve_plugin_instance_snapshot(
        expectation.playlist_name,
        expectation.plugin_id,
        expectation.instance_name,
    )
    persisted_playlist = config.get_config(
        "playlist_config",
        default={"playlists": [], "active_playlist": None},
    )
    _assert_exact_playlist_transition(
        authoritative_baseline,
        persisted_playlist,
        expected=expectation.expected,
        playlist_name=expectation.playlist_name,
        plugin_id=expectation.plugin_id,
        instance_name=expectation.instance_name,
    )
    _assert_exact_config_transition(
        authoritative_config,
        config.get_config(),
        expected_playlist_after=expected_playlist_after,
        config_updates={"runtime_migrations": migration_state},
    )
    if persisted is None or persisted.instance != result.after:
        raise NasapicsMigrationError("persisted NASAPics migration verification failed")
    if persisted_marker != marker:
        raise NasapicsMigrationError("persisted NASAPics marker verification failed")
    return result


def migrate_nasapics_instance(
    config: "Config",
    *,
    expected: ExpectedNasapicsIdentity,
    playlist_name: str = DEFAULT_PLAYLIST_NAME,
    plugin_id: str = DEFAULT_PLUGIN_ID,
    instance_name: str = DEFAULT_INSTANCE_NAME,
) -> NasapicsMigrationResult:
    """Perform one exact detached merge/CAS update through the ConfigStore."""

    (
        expected_config_version,
        authoritative_config,
        authoritative_baseline,
        authoritative_manager,
    ) = config.capture_detached_playlist_transaction()
    _baseline, detached, result = _prepare_detached_migration(
        authoritative_manager,
        expected=expected,
        playlist_name=playlist_name,
        plugin_id=plugin_id,
        instance_name=instance_name,
    )
    expected_playlist_after = _assert_exact_playlist_transition(
        authoritative_baseline,
        detached.to_dict(),
        expected=expected,
        playlist_name=playlist_name,
        plugin_id=plugin_id,
        instance_name=instance_name,
    )
    config.commit_detached_playlist_transaction(
        expected_config_version=expected_config_version,
        expected_config_data=authoritative_config,
        expected_playlist_config=authoritative_baseline,
        playlist_manager=detached,
        config_updates={},
    )
    persisted = config.playlist_manager.resolve_plugin_instance_snapshot(
        playlist_name,
        plugin_id,
        instance_name,
    )
    persisted_playlist = config.get_config(
        "playlist_config",
        default={"playlists": [], "active_playlist": None},
    )
    _assert_exact_playlist_transition(
        authoritative_baseline,
        persisted_playlist,
        expected=expected,
        playlist_name=playlist_name,
        plugin_id=plugin_id,
        instance_name=instance_name,
    )
    _assert_exact_config_transition(
        authoritative_config,
        config.get_config(),
        expected_playlist_after=expected_playlist_after,
        config_updates={},
    )
    if persisted is None or persisted.instance != result.after:
        raise NasapicsMigrationError("persisted NASAPics migration verification failed")
    return result
