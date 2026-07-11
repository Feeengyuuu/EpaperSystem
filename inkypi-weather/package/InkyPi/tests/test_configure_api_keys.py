import sys
import stat
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))

import configure_api_keys as configure_module  # noqa: E402
from configure_api_keys import (  # noqa: E402
    main,
    merge_missing_values,
    parse_env,
    write_env,
)


REGISTRY = [
    {
        "key": "OPEN_WEATHER_MAP_SECRET",
        "aliases": [],
    },
    {
        "key": "TMDB_BEARER_TOKEN",
        "aliases": ["TMDB_READ_ACCESS_TOKEN", "TMDB_Access_Token"],
    },
    {
        "key": "TMDB_API_KEY",
        "aliases": ["THEMOVIEDB_API_KEY"],
    },
]


def test_merge_missing_values_preserves_managed_values_and_canonicalizes_legacy():
    current = {
        "OPEN_WEATHER_MAP_SECRET": "managed-weather",
        "CUSTOM_CURRENT": "managed-custom",
    }
    legacy = {
        "OPEN_WEATHER_MAP_SECRET": "stale-weather",
        "TMDB_Access_Token": "legacy-bearer",
        "TMDB_API_KEY": "legacy-api-key",
        "CUSTOM_CURRENT": "stale-custom",
        "CUSTOM_LEGACY_ONLY": "legacy-custom",
    }

    merged, additions = merge_missing_values(current, legacy, REGISTRY)

    assert merged["OPEN_WEATHER_MAP_SECRET"] == "managed-weather"
    assert merged["TMDB_BEARER_TOKEN"] == "legacy-bearer"
    assert merged["TMDB_API_KEY"] == "legacy-api-key"
    assert "TMDB_Access_Token" not in merged
    assert merged["CUSTOM_CURRENT"] == "managed-custom"
    assert merged["CUSTOM_LEGACY_ONLY"] == "legacy-custom"
    assert additions == 3

    repeated, repeated_additions = merge_missing_values(merged, legacy, REGISTRY)
    assert repeated == merged
    assert repeated_additions == 0


def test_merge_missing_values_prefers_a_managed_alias_over_legacy_primary():
    current = {"TMDB_Access_Token": "managed-bearer"}
    legacy = {"TMDB_BEARER_TOKEN": "legacy-bearer"}

    merged, additions = merge_missing_values(current, legacy, REGISTRY)

    assert merged["TMDB_BEARER_TOKEN"] == "managed-bearer"
    assert "TMDB_Access_Token" not in merged
    assert additions == 0


def test_merge_from_cli_writes_canonical_values_without_printing_secrets(
    tmp_path,
    capsys,
):
    target = tmp_path / "managed.env"
    legacy = tmp_path / "legacy.env"
    target.write_text(
        "OPEN_WEATHER_MAP_SECRET=managed-weather-secret\n",
        encoding="utf-8",
    )
    legacy.write_text(
        "TMDB_Access_Token=legacy-bearer-secret\n"
        "TMDB_API_KEY=legacy-api-secret\n"
        "Comic_Vine_Key=legacy-comic-secret\n"
        "BAMBU_ACCESS_CODE=legacy-bambu-secret\n",
        encoding="utf-8",
    )

    result = main(
        [
            "--env-file",
            str(target),
            "--merge-from",
            str(legacy),
        ]
    )

    values = parse_env(target)
    output = capsys.readouterr().out
    assert result == 0
    assert values["OPEN_WEATHER_MAP_SECRET"] == "managed-weather-secret"
    assert values["TMDB_BEARER_TOKEN"] == "legacy-bearer-secret"
    assert values["TMDB_API_KEY"] == "legacy-api-secret"
    assert values["COMIC_VINE_API_KEY"] == "legacy-comic-secret"
    assert values["BAMBU_ACCESS_CODE"] == "legacy-bambu-secret"
    assert "TMDB_Access_Token" not in values
    assert "managed-weather-secret" not in output
    assert "legacy-bearer-secret" not in output
    assert "legacy-api-secret" not in output
    assert "legacy-comic-secret" not in output
    assert "legacy-bambu-secret" not in output


def test_atomic_env_write_preserves_existing_metadata(tmp_path):
    target = tmp_path / "managed.env"
    target.write_text("ORIGINAL=value\n", encoding="utf-8")
    os_mode = 0o600
    target.chmod(os_mode)
    before = target.stat()

    write_env(target, {"TMDB_BEARER_TOKEN": "replacement"}, REGISTRY)

    after = target.stat()
    assert after.st_uid == before.st_uid
    assert after.st_gid == before.st_gid
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert parse_env(target)["TMDB_BEARER_TOKEN"] == "replacement"


def test_atomic_env_write_failure_leaves_original_file_untouched(
    monkeypatch,
    tmp_path,
):
    target = tmp_path / "managed.env"
    original = b"ORIGINAL=value\n"
    target.write_bytes(original)
    target.chmod(0o600)
    before = target.stat()

    def fail_replace(_source, _destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr(configure_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replace failure"):
        write_env(target, {"TMDB_BEARER_TOKEN": "replacement"}, REGISTRY)

    after = target.stat()
    assert target.read_bytes() == original
    assert after.st_uid == before.st_uid
    assert after.st_gid == before.st_gid
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert not list(tmp_path.glob(".managed.env.*.tmp"))
