"""Read-only runtime facade used by the independent web publication worker."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from secret_schema import ENV_NAME_RE, SecretSchema


_MAX_SECRET_BYTES = 64 * 1024
_DEVICE_KEYS = (
    "name",
    "resolution",
    "orientation",
    "timezone",
    "time_format",
    "plugin_cycle_interval_seconds",
    "image_settings",
)


class CloudDeviceConfig:
    """Small compatibility surface for rendering plugins without device control.

    It intentionally implements reads only.  There is no config persistence,
    refresh queue, display manager, or physical display reference on this type.
    """

    def __init__(
        self,
        device: Mapping[str, Any],
        *,
        data_dir: str | Path,
        cache_dir: str | Path,
        secret_dir: str | Path | None = None,
        playlist_manager=None,
        secret_schema: SecretSchema | None = None,
    ):
        if not isinstance(device, Mapping):
            raise ValueError("publication device must be an object")
        self._values = self._validated_device(device)
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.plugin_image_dir = self.data_dir / "plugin-images"
        self.current_image_file = self.data_dir / "current-image.png"
        self.display_dir = self.data_dir / "display"
        self._secret_dir = Path(secret_dir).expanduser().resolve() if secret_dir is not None else None
        self._playlist_manager = playlist_manager
        self._secret_schema = secret_schema or SecretSchema.load()

        for directory in (
            self.data_dir,
            self.cache_dir,
            self.plugin_image_dir,
            self.display_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validated_device(device: Mapping[str, Any]) -> dict[str, Any]:
        raw_resolution = device.get("resolution")
        if (
            not isinstance(raw_resolution, (list, tuple))
            or len(raw_resolution) != 2
            or any(isinstance(value, bool) for value in raw_resolution)
        ):
            raise ValueError("device resolution must contain width and height")
        try:
            resolution = [int(raw_resolution[0]), int(raw_resolution[1])]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("device resolution must contain integers") from exc
        if any(value < 16 or value > 8192 for value in resolution):
            raise ValueError("device resolution is outside the supported range")

        orientation = str(device.get("orientation") or "horizontal").strip().lower()
        if orientation not in {"horizontal", "vertical"}:
            raise ValueError("device orientation must be horizontal or vertical")

        timezone_name = str(device.get("timezone") or "").strip()
        if not timezone_name:
            raise ValueError("device timezone is required")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("device timezone is unknown") from exc

        time_format = str(device.get("time_format") or "12h").strip().lower()
        if time_format not in {"12h", "24h"}:
            raise ValueError("device time_format must be 12h or 24h")
        try:
            cycle_seconds = int(device.get("plugin_cycle_interval_seconds") or 300)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("plugin cycle interval must be an integer") from exc
        if cycle_seconds < 15 or cycle_seconds > 24 * 60 * 60:
            raise ValueError("plugin cycle interval is outside the supported range")

        image_settings = device.get("image_settings") or {}
        if not isinstance(image_settings, Mapping):
            raise ValueError("device image_settings must be an object")
        return {
            "name": str(device.get("name") or "EpaperSystem").strip() or "EpaperSystem",
            "resolution": resolution,
            "orientation": orientation,
            "timezone": timezone_name,
            "time_format": time_format,
            "plugin_cycle_interval_seconds": cycle_seconds,
            "image_settings": deepcopy(dict(image_settings)),
        }

    def __repr__(self):
        return (
            "CloudDeviceConfig("
            f"resolution={self.get_resolution()!r}, "
            f"orientation={self._values['orientation']!r}, "
            f"timezone={self._values['timezone']!r})"
        )

    def get_resolution(self):
        return tuple(self._values["resolution"])

    def get_config(self, key=None, default=None):
        if key is None:
            return deepcopy(self._values)
        if key not in self._values:
            return deepcopy(default)
        return deepcopy(self._values[key])

    def get_playlist_manager(self):
        if self._playlist_manager is None:
            raise RuntimeError("playlist manager is unavailable in this publication bundle")
        return self._playlist_manager

    def load_env_key(self, key):
        """Read one schema-known secret from env or the Docker secret mount."""

        names = self._secret_names(key)
        for name in names:
            value = os.getenv(name)
            if value:
                return value
        if self._secret_dir is None:
            return ""
        for name in names:
            value = self._read_secret_file(name)
            if value:
                return value
        return ""

    def _secret_names(self, key):
        name = str(key or "")
        if not ENV_NAME_RE.fullmatch(name):
            return ()
        try:
            return self._secret_schema.resolve_names(name)
        except KeyError:
            return (name,)

    def _read_secret_file(self, name):
        root = self._secret_dir
        if root is None:
            return ""
        candidate = (root / name).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return ""
        try:
            if candidate.stat().st_size > _MAX_SECRET_BYTES:
                return ""
            return candidate.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return ""

    @contextmanager
    def runtime_environment(self, *, context_dir: str | Path | None = None):
        """Temporarily route all mutable plugin output into worker-owned storage."""

        updates = {
            "INKYPI_DATA_DIR": str(self.data_dir),
            "INKYPI_CACHE_DIR": str(self.cache_dir),
        }
        if context_dir is not None:
            updates["INKYPI_CONTEXT_CACHE_DIR"] = str(Path(context_dir).resolve())
        previous = {key: os.environ.get(key) for key in updates}
        os.environ.update(updates)
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


__all__ = ["CloudDeviceConfig"]
