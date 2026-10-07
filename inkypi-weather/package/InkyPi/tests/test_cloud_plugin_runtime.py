from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from publication_runtime import CloudDeviceConfig  # noqa: E402


def _device():
    return {
        "name": "Model Y Portal",
        "resolution": [800, 480],
        "orientation": "horizontal",
        "timezone": "America/Los_Angeles",
        "time_format": "12h",
        "plugin_cycle_interval_seconds": 300,
        "image_settings": {"saturation": 0.8},
    }


def test_cloud_device_config_exposes_only_read_only_device_contract(tmp_path):
    config = CloudDeviceConfig(
        _device(),
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        secret_dir=tmp_path / "secrets",
    )

    assert config.get_resolution() == (800, 480)
    assert config.get_config("timezone") == "America/Los_Angeles"
    assert config.get_config("missing", default="fallback") == "fallback"
    snapshot = config.get_config()
    snapshot["timezone"] = "mutated"
    assert config.get_config("timezone") == "America/Los_Angeles"
    assert Path(config.plugin_image_dir).is_relative_to(tmp_path / "data")
    assert not hasattr(config, "update_config")


def test_cloud_device_config_reads_canonical_secret_without_exposing_it(
    tmp_path,
    monkeypatch,
):
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / "OPEN_WEATHER_MAP_SECRET").write_text(
        "from-file\n",
        encoding="utf-8",
    )
    config = CloudDeviceConfig(
        _device(),
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        secret_dir=secret_dir,
    )

    assert config.load_env_key("OPEN_WEATHER_MAP_SECRET") == "from-file"
    monkeypatch.setenv("OPEN_WEATHER_MAP_SECRET", "from-environment")
    assert config.load_env_key("OPEN_WEATHER_MAP_SECRET") == "from-environment"
    assert "from-file" not in repr(config)
    assert "from-environment" not in repr(config)
    assert "from-file" not in str(config.get_config())


@pytest.mark.parametrize(
    "device",
    [
        {**_device(), "resolution": [0, 480]},
        {**_device(), "resolution": [800]},
        {**_device(), "orientation": "diagonal"},
        {**_device(), "timezone": ""},
    ],
)
def test_cloud_device_config_rejects_invalid_publication_device(device, tmp_path):
    with pytest.raises(ValueError):
        CloudDeviceConfig(
            device,
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
        )


def test_cloud_device_config_rejects_secret_path_traversal(tmp_path):
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (tmp_path / "outside").write_text("do-not-read", encoding="utf-8")
    config = CloudDeviceConfig(
        _device(),
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        secret_dir=secret_dir,
    )

    assert config.load_env_key("../outside") == ""
