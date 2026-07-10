"""Canonical filesystem locations used by the InkyPi runtime.

Path selection is deliberately kept free of filesystem mutations so callers can
construct and validate an application before any runtime directory is created.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath


_PATH_ENV_KEYS = (
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


def _explicit_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _path_override(name: str, default: Path, *, require_absolute: bool) -> Path:
    value = _explicit_value(name)
    if value is None:
        return default

    path = Path(value)
    is_absolute = path.is_absolute() or PurePosixPath(value).is_absolute()
    if require_absolute and not is_absolute:
        raise ValueError(f"{name} must be an absolute path")
    return path


@dataclass(frozen=True)
class RuntimePaths:
    """Immutable identity for every mutable runtime filesystem location."""

    release_id: str
    config_file: Path
    data_dir: Path
    cache_dir: Path
    env_file: Path
    display_dir: Path
    current_image_file: Path
    plugin_image_dir: Path
    flask_secret_file: Path

    @property
    def config_dir(self) -> Path:
        return self.config_file.parent

    @classmethod
    def from_environment(cls, *, dev_mode: bool) -> "RuntimePaths":
        """Build paths from explicit environment values without touching disk."""

        # Validate every supported explicit value, including values that are not
        # used in the selected mode. A blank setting must never silently select a
        # different persistence location.
        release_id = _explicit_value("INKYPI_RELEASE_ID")
        for key in _PATH_ENV_KEYS:
            value = _explicit_value(key)
            if not dev_mode and value is not None:
                path = Path(value)
                if not (path.is_absolute() or PurePosixPath(value).is_absolute()):
                    raise ValueError(f"{key} must be an absolute path")

        if dev_mode:
            source_root = Path(__file__).parent
            dev_root = _path_override(
                "INKYPI_DEV_ROOT",
                source_root,
                require_absolute=True,
            )
            config_dir = _path_override(
                "INKYPI_CONFIG_DIR",
                dev_root / "config",
                require_absolute=True,
            )
            data_dir = _path_override(
                "INKYPI_DATA_DIR",
                dev_root / "data",
                require_absolute=True,
            )
            cache_dir = _path_override(
                "INKYPI_CACHE_DIR",
                dev_root / ".cache",
                require_absolute=True,
            )
            env_file = _path_override(
                "INKYPI_ENV_FILE",
                dev_root.parent / ".env",
                require_absolute=True,
            )
            display_dir = _path_override(
                "INKYPI_DISPLAY_DIR",
                dev_root / "static" / "display",
                require_absolute=True,
            )
            current_image_file = _path_override(
                "INKYPI_CURRENT_IMAGE_FILE",
                dev_root / "static" / "images" / "current_image.png",
                require_absolute=True,
            )
            plugin_image_dir = _path_override(
                "INKYPI_PLUGIN_IMAGE_DIR",
                dev_root / "static" / "images" / "plugins",
                require_absolute=True,
            )
            flask_secret_file = _path_override(
                "INKYPI_FLASK_SECRET_FILE",
                config_dir / ".flask_secret",
                require_absolute=True,
            )
            config_file = config_dir / "device_dev.json"
            return cls(
                release_id=release_id or "development",
                config_file=config_file,
                data_dir=data_dir,
                cache_dir=cache_dir,
                env_file=env_file,
                display_dir=display_dir,
                current_image_file=current_image_file,
                plugin_image_dir=plugin_image_dir,
                flask_secret_file=flask_secret_file,
            )

        config_dir = _path_override(
            "INKYPI_CONFIG_DIR",
            Path("/var/lib/inkypi/config"),
            require_absolute=True,
        )
        display_dir = _path_override(
            "INKYPI_DISPLAY_DIR",
            Path("/var/lib/inkypi/display"),
            require_absolute=True,
        )
        return cls(
            release_id=release_id or "unknown",
            config_file=config_dir / "device.json",
            data_dir=_path_override(
                "INKYPI_DATA_DIR",
                Path("/var/lib/inkypi/data"),
                require_absolute=True,
            ),
            cache_dir=_path_override(
                "INKYPI_CACHE_DIR",
                Path("/var/cache/inkypi"),
                require_absolute=True,
            ),
            env_file=_path_override(
                "INKYPI_ENV_FILE",
                Path("/etc/inkypi/inkypi.env"),
                require_absolute=True,
            ),
            display_dir=display_dir,
            current_image_file=_path_override(
                "INKYPI_CURRENT_IMAGE_FILE",
                display_dir / "current_image.png",
                require_absolute=True,
            ),
            plugin_image_dir=_path_override(
                "INKYPI_PLUGIN_IMAGE_DIR",
                Path("/var/lib/inkypi/plugins"),
                require_absolute=True,
            ),
            flask_secret_file=_path_override(
                "INKYPI_FLASK_SECRET_FILE",
                config_dir / "flask_secret",
                require_absolute=True,
            ),
        )
