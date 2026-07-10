import dataclasses
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime_paths import RuntimePaths


PATH_ENV_KEYS = (
    "INKYPI_DEV_ROOT",
    "INKYPI_CONFIG_DIR",
    "INKYPI_DATA_DIR",
    "INKYPI_CACHE_DIR",
    "INKYPI_ENV_FILE",
    "INKYPI_DISPLAY_DIR",
    "INKYPI_CURRENT_IMAGE_FILE",
    "INKYPI_PLUGIN_IMAGE_DIR",
    "INKYPI_FLASK_SECRET_FILE",
)


@pytest.fixture(autouse=True)
def clean_runtime_path_environment(monkeypatch):
    for key in ("INKYPI_RELEASE_ID", *PATH_ENV_KEYS):
        monkeypatch.delenv(key, raising=False)


def test_production_defaults_are_external_and_complete(monkeypatch):
    monkeypatch.setenv("INKYPI_RELEASE_ID", "abc123")

    paths = RuntimePaths.from_environment(dev_mode=False)

    assert paths.release_id == "abc123"
    assert paths.config_file == Path("/var/lib/inkypi/config/device.json")
    assert paths.config_dir == Path("/var/lib/inkypi/config")
    assert paths.data_dir == Path("/var/lib/inkypi/data")
    assert paths.cache_dir == Path("/var/cache/inkypi")
    assert paths.env_file == Path("/etc/inkypi/inkypi.env")
    assert paths.display_dir == Path("/var/lib/inkypi/display")
    assert paths.current_image_file == Path("/var/lib/inkypi/display/current_image.png")
    assert paths.plugin_image_dir == Path("/var/lib/inkypi/plugins")
    assert paths.flask_secret_file == Path("/var/lib/inkypi/config/flask_secret")
    assert "/opt/inkypi/current" not in str(paths.config_file).replace("\\", "/")


def test_development_defaults_use_device_dev_and_source_compatible_locations(tmp_path, monkeypatch):
    dev_root = tmp_path / "checkout" / "src"
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(dev_root))

    paths = RuntimePaths.from_environment(dev_mode=True)

    assert paths.release_id == "development"
    assert paths.config_file == dev_root / "config" / "device_dev.json"
    assert paths.data_dir == dev_root / "data"
    assert paths.cache_dir == dev_root / ".cache"
    assert paths.env_file == dev_root.parent / ".env"
    assert paths.display_dir == dev_root / "static" / "display"
    assert paths.current_image_file == dev_root / "static" / "images" / "current_image.png"
    assert paths.plugin_image_dir == dev_root / "static" / "images" / "plugins"
    assert paths.flask_secret_file == dev_root / "config" / ".flask_secret"


def test_explicit_overrides_are_independent_of_cwd(tmp_path, monkeypatch):
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    override_root = tmp_path / "runtime"
    overrides = {
        "INKYPI_CONFIG_DIR": override_root / "configuration",
        "INKYPI_DATA_DIR": override_root / "data",
        "INKYPI_CACHE_DIR": override_root / "cache",
        "INKYPI_ENV_FILE": override_root / "secrets" / "inkypi.env",
        "INKYPI_DISPLAY_DIR": override_root / "display",
        "INKYPI_CURRENT_IMAGE_FILE": override_root / "published" / "current.png",
        "INKYPI_PLUGIN_IMAGE_DIR": override_root / "plugins",
        "INKYPI_FLASK_SECRET_FILE": override_root / "secrets" / "flask",
    }
    for key, value in overrides.items():
        monkeypatch.setenv(key, str(value))

    monkeypatch.chdir(first_cwd)
    first = RuntimePaths.from_environment(dev_mode=False)
    monkeypatch.chdir(second_cwd)
    second = RuntimePaths.from_environment(dev_mode=False)

    assert first == second
    assert first.config_file == overrides["INKYPI_CONFIG_DIR"] / "device.json"
    assert first.data_dir == overrides["INKYPI_DATA_DIR"]
    assert first.cache_dir == overrides["INKYPI_CACHE_DIR"]
    assert first.env_file == overrides["INKYPI_ENV_FILE"]
    assert first.display_dir == overrides["INKYPI_DISPLAY_DIR"]
    assert first.current_image_file == overrides["INKYPI_CURRENT_IMAGE_FILE"]
    assert first.plugin_image_dir == overrides["INKYPI_PLUGIN_IMAGE_DIR"]
    assert first.flask_secret_file == overrides["INKYPI_FLASK_SECRET_FILE"]


@pytest.mark.parametrize("key", PATH_ENV_KEYS)
def test_production_rejects_relative_path_overrides(key, monkeypatch):
    monkeypatch.setenv(key, os.path.join("relative", "path"))

    with pytest.raises(ValueError, match=key):
        RuntimePaths.from_environment(dev_mode=False)


@pytest.mark.parametrize("key", ("INKYPI_RELEASE_ID", *PATH_ENV_KEYS))
def test_empty_explicit_values_are_rejected(key, monkeypatch):
    monkeypatch.setenv(key, "   ")

    with pytest.raises(ValueError, match=key):
        RuntimePaths.from_environment(dev_mode=False)


def test_runtime_paths_is_frozen(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path))
    paths = RuntimePaths.from_environment(dev_mode=True)

    with pytest.raises(dataclasses.FrozenInstanceError):
        paths.data_dir = tmp_path / "other"
