"""Cloud-side plugin producer for the immutable publication ledger."""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import re
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from model import PlaylistManager
from plugins.plugin_manifest import PluginManifest
from publication import (
    AdapterRegistry,
    AssetInput,
    PlaylistEntry,
    PlaylistSpec,
    ProducerBatch,
    ProductionFailure,
    PublicationDraft,
    RasterAssetLegacyAdapter,
    TimeWindow,
    WorkBudget,
)
from publication_runtime import CloudDeviceConfig


NATIVE_PLUGIN_IDS = frozenset(
    {
        "sports_dashboard",
        "mini_weather",
        "live_radar",
        "steam_charts",
        "stocktracker",
        "ticketmaster_events",
    }
)
ORIGINAL_RASTER_PLUGIN_IDS = NATIVE_PLUGIN_IDS | frozenset({"weather"})
_STRUCTURED_CONTEXT_PLUGIN_IDS = NATIVE_PLUGIN_IDS | ORIGINAL_RASTER_PLUGIN_IDS

_PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9_]{0,79}$")
_SECRET_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_RESOURCE_PATH = re.compile(r"^resources/([0-9a-f]{64})(\.[A-Za-z0-9]{1,8})$")
_MAX_CONFIG_BYTES = 16 * 1024 * 1024
_MAX_CONTEXT_BYTES = 2 * 1024 * 1024
_MAX_RESOURCE_BYTES = 64 * 1024 * 1024
_MAX_RASTER_BYTES = 24 * 1024 * 1024
_MAX_IMAGE_PIXELS = 24 * 1024 * 1024
_FAILURE_CODES = frozenset(
    {
        "credentials_unavailable",
        "plugin_unavailable",
        "render_failed",
        "resource_unavailable",
    }
)


class _MissingSecret(ValueError):
    pass


class _UnavailableResource(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("publication clock must return an aware datetime")
    return value.astimezone(timezone.utc)


def _safe_positive_int(value, default=1):
    if isinstance(value, bool):
        return default
    try:
        return max(1, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _playlist_slug(name: str, order: int) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"playlist-{order + 1}-{digest}"


def build_default_adapter_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    for plugin_id in sorted(ORIGINAL_RASTER_PLUGIN_IDS):
        registry.register_legacy(plugin_id, RasterAssetLegacyAdapter())
    registry.register_default_legacy(RasterAssetLegacyAdapter())
    return registry


class _LocalPluginFactory:
    """Load only a class confirmed by its checked-in local manifest."""

    def __init__(self, plugins_root: Path):
        self._plugins_root = plugins_root.resolve()

    def __call__(self, catalog_entry: Mapping[str, Any]):
        plugin_id = str(catalog_entry.get("id") or "")
        requested_class = str(catalog_entry.get("class") or "")
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise LookupError("plugin_unavailable")
        plugin_dir = (self._plugins_root / plugin_id).resolve()
        if plugin_dir.parent != self._plugins_root:
            raise LookupError("plugin_unavailable")
        manifest_path = plugin_dir / "plugin-info.json"
        module_path = plugin_dir / f"{plugin_id}.py"
        if not manifest_path.is_file() or not module_path.is_file():
            raise LookupError("plugin_unavailable")
        manifest = PluginManifest.from_path(manifest_path)
        if manifest.id != plugin_id or manifest.class_name != requested_class:
            raise LookupError("plugin_unavailable")
        module = importlib.import_module(f"plugins.{plugin_id}.{plugin_id}")
        plugin_class = getattr(module, manifest.class_name, None)
        if not isinstance(plugin_class, type):
            raise LookupError("plugin_unavailable")
        config = dict(manifest.raw)
        config["_manifest"] = manifest
        return plugin_class(config)


class CloudPublicationProducer:
    """Render imported playlist instances without any display-side dependency."""

    def __init__(
        self,
        bundle_dir: str | Path,
        *,
        data_dir: str | Path,
        cache_dir: str | Path,
        secret_dir: str | Path | None = None,
        plugin_factory: Callable[[Mapping[str, Any]], Any] | None = None,
        context_loader: Callable[[Path, str], Mapping[str, Any] | None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.bundle_dir = Path(bundle_dir).expanduser().resolve()
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._document = self._load_document(self.bundle_dir / "web_config.json")
        self._catalog_revision = self._catalog_revision_for(self._document)
        self._playlists = self._parse_playlists(self._document.get("playlists"))
        self._instances = tuple(
            instance
            for playlist in self._playlists
            for instance in playlist.get("plugins", ())
            if isinstance(instance, Mapping)
        )
        playlist_manager = PlaylistManager.from_dict(
            {
                "active_playlist": self._document.get("active_playlist"),
                "playlists": deepcopy(self._playlists),
            }
        )
        self.device_config = CloudDeviceConfig(
            self._document.get("device") or {},
            data_dir=self.data_dir / "runtime",
            cache_dir=self.cache_dir / "runtime",
            secret_dir=secret_dir,
            playlist_manager=playlist_manager,
        )
        raw_catalog = self._document.get("plugin_catalog")
        if not isinstance(raw_catalog, list):
            raise ValueError("publication plugin_catalog must be an array")
        self._plugin_catalog = {
            str(entry.get("id")): dict(entry)
            for entry in raw_catalog
            if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)
        }
        plugins_root = Path(__file__).resolve().parent / "plugins"
        self._plugin_factory = plugin_factory or _LocalPluginFactory(plugins_root)
        self._context_loader = context_loader or self._read_context
        self._plugins: dict[str, Any] = {}
        self._next_due: dict[str, datetime] = {}
        self._last_source_revision = 0
        self._lock = threading.Lock()

    @staticmethod
    def _load_document(path: Path) -> dict[str, Any]:
        try:
            if path.stat().st_size > _MAX_CONFIG_BYTES:
                raise ValueError("web_config.json is too large")
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("web_config.json is unavailable") from exc
        if not isinstance(document, dict) or document.get("format_version") != 1:
            raise ValueError("unsupported web publication bundle")
        return document

    @staticmethod
    def _parse_playlists(value) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ValueError("publication playlists must be an array")
        playlists = []
        for raw in value:
            if not isinstance(raw, Mapping):
                raise ValueError("publication playlist must be an object")
            name = str(raw.get("name") or "").strip()
            start = str(raw.get("start_time") or "00:00").strip()
            end = str(raw.get("end_time") or "24:00").strip()
            plugins = raw.get("plugins")
            if not name or not isinstance(plugins, list):
                raise ValueError("publication playlist is invalid")
            playlists.append(
                {
                    **dict(raw),
                    "name": name,
                    "start_time": start,
                    "end_time": end,
                    "plugins": [dict(item) for item in plugins if isinstance(item, Mapping)],
                }
            )
        return playlists

    @staticmethod
    def _catalog_revision_for(document: Mapping[str, Any]) -> int:
        source = document.get("source")
        value = source.get("config_revision") if isinstance(source, Mapping) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return int(hashlib.sha256(canonical).hexdigest()[:15], 16)

    def collect_due(self, budget: WorkBudget) -> ProducerBatch:
        max_items = max(0, int(budget.max_items))
        with self._lock:
            now = _utc(self._clock())
            playlists = self._playlist_specs()
            drafts = []
            failures = []
            attempted = 0
            for instance in self._instances:
                if attempted >= max_items or self._deadline_reached(budget.deadline):
                    break
                instance_uuid = str(instance.get("instance_uuid") or "").strip()
                if not instance_uuid or self._next_due.get(instance_uuid, now) > now:
                    continue
                attempted += 1
                source_revision = self._next_source_revision(now)
                try:
                    draft = self._render_instance(instance, now, source_revision)
                except _MissingSecret:
                    failures.append(self._failure(instance, now, source_revision, "credentials_unavailable"))
                except _UnavailableResource:
                    failures.append(self._failure(instance, now, source_revision, "resource_unavailable"))
                except (ImportError, LookupError):
                    failures.append(self._failure(instance, now, source_revision, "plugin_unavailable"))
                except Exception:
                    failures.append(self._failure(instance, now, source_revision, "render_failed"))
                else:
                    drafts.append(draft)
                self._next_due[instance_uuid] = now + timedelta(seconds=self._refresh_interval(instance))
            return ProducerBatch(
                catalog_revision=self._catalog_revision,
                drafts=tuple(drafts),
                playlists=playlists,
                failures=tuple(failures),
                active_playlist=self._active_playlist_slug(playlists),
            )

    def _active_playlist_slug(self, playlists):
        configured = self._document.get("active_playlist")
        if not isinstance(configured, str) or not configured.strip():
            return None
        configured = configured.strip()
        for raw_playlist, playlist in zip(self._playlists, playlists, strict=True):
            if configured in {raw_playlist["name"], playlist.slug}:
                return playlist.slug
        return None

    def _deadline_reached(self, deadline):
        if deadline is None:
            return False
        return _utc(self._clock()) >= _utc(deadline)

    def _next_source_revision(self, now):
        candidate = int(now.timestamp() * 1_000_000)
        self._last_source_revision = max(candidate, self._last_source_revision + 1)
        return self._last_source_revision

    def _failure(self, instance, now, source_revision, reason_code):
        if reason_code not in _FAILURE_CODES:
            reason_code = "render_failed"
        return ProductionFailure(
            instance_uuid=str(instance.get("instance_uuid") or ""),
            plugin_id=str(instance.get("plugin_id") or ""),
            title=str(instance.get("name") or "Untitled")[:160],
            settings_revision=_safe_positive_int(instance.get("settings_revision")),
            source_revision=source_revision,
            attempted_at=now,
            reason_code=reason_code,
        )

    def _playlist_specs(self):
        specs = []
        for playlist_order, playlist in enumerate(self._playlists):
            name = playlist["name"]
            entries = []
            for instance_order, instance in enumerate(playlist["plugins"]):
                instance_uuid = str(instance.get("instance_uuid") or "").strip()
                plugin_id = str(instance.get("plugin_id") or "").strip()
                if not instance_uuid or not plugin_id:
                    continue
                entries.append(
                    PlaylistEntry(
                        instance_uuid=instance_uuid,
                        title=str(instance.get("name") or plugin_id)[:160],
                        plugin_id=plugin_id,
                        order=instance_order,
                    )
                )
            specs.append(
                PlaylistSpec(
                    slug=_playlist_slug(name, playlist_order),
                    title=name,
                    order=playlist_order,
                    window=TimeWindow(
                        start=playlist["start_time"],
                        end=playlist["end_time"],
                    ),
                    entries=tuple(entries),
                )
            )
        return tuple(specs)

    def _render_instance(self, instance, now, source_revision):
        instance_uuid = str(instance.get("instance_uuid") or "").strip()
        plugin_id = str(instance.get("plugin_id") or "").strip()
        title = str(instance.get("name") or plugin_id or "Untitled")[:160]
        catalog_entry = self._plugin_catalog.get(plugin_id)
        if catalog_entry is None or bool(catalog_entry.get("disabled")):
            raise LookupError("plugin_unavailable")
        raw_settings = instance.get("plugin_settings")
        if raw_settings is None:
            raw_settings = instance.get("settings")
        if not isinstance(raw_settings, Mapping):
            raise ValueError("invalid plugin settings")
        settings = self._resolve_setting_value(deepcopy(dict(raw_settings)))
        interval = self._refresh_interval(instance)
        context_parent = self.cache_dir / "publication-context"
        context_dir = context_parent / hashlib.sha256(f"{instance_uuid}:{source_revision}".encode("utf-8")).hexdigest()
        context_dir.mkdir(parents=True, exist_ok=False)

        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            plugin = self._plugin_factory(catalog_entry)
            if plugin is None:
                raise LookupError("plugin_unavailable")
            self._plugins[plugin_id] = plugin
        try:
            with self.device_config.runtime_environment(context_dir=context_dir):
                render = getattr(plugin, "render_themed_image", None)
                if callable(render):
                    image = render(settings, self.device_config)
                else:
                    image = plugin.generate_image(settings, self.device_config)
                raster, width, height = self._encode_png(image)
                if (width, height) != tuple(self.device_config.get_resolution()):
                    raise ValueError("plugin raster does not match the configured device resolution")
                context = self._context_loader(context_dir, plugin_id)
        finally:
            self._remove_context_dir(context_dir)

        snapshot: dict[str, Any] = {
            "rasterMetadata": {
                "alt": title,
                "height": height,
                "width": width,
            }
        }
        if plugin_id in _STRUCTURED_CONTEXT_PLUGIN_IDS and isinstance(context, Mapping):
            snapshot["context"] = self._json_safe_context(context)
        fresh_until = now + timedelta(seconds=interval)
        stale_until = fresh_until + max(
            timedelta(hours=24),
            timedelta(seconds=interval * 4),
        )
        return PublicationDraft(
            instance_uuid=instance_uuid,
            plugin_id=plugin_id,
            title=title,
            settings_revision=_safe_positive_int(instance.get("settings_revision")),
            source_revision=source_revision,
            generated_at=now,
            fresh_until=fresh_until,
            stale_until=stale_until,
            snapshot=snapshot,
            source_assets=(AssetInput(name="raster", media_type="image/png", body=raster),),
        )

    @staticmethod
    def _remove_context_dir(path: Path):
        try:
            for child in path.iterdir():
                if child.is_file() and child.parent == path:
                    child.unlink()
            path.rmdir()
        except OSError:
            pass

    def _resolve_setting_value(self, value):
        if isinstance(value, dict):
            return {key: self._resolve_setting_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_setting_value(item) for item in value]
        if not isinstance(value, str):
            return value
        resource_match = _RESOURCE_PATH.fullmatch(value.replace("\\", "/"))
        if resource_match is not None:
            return str(self._verified_resource(value, resource_match.group(1)))

        def substitute(match):
            secret = self.device_config.load_env_key(match.group(1))
            if not secret:
                raise _MissingSecret("credentials_unavailable")
            return secret

        return _SECRET_PLACEHOLDER.sub(substitute, value)

    def _verified_resource(self, relative_value, expected_digest):
        candidate = (self.bundle_dir / Path(relative_value)).resolve()
        if not candidate.is_relative_to(self.bundle_dir) or not candidate.is_file():
            raise _UnavailableResource("resource_unavailable")
        try:
            if candidate.stat().st_size > _MAX_RESOURCE_BYTES:
                raise _UnavailableResource("resource_unavailable")
            digest = hashlib.sha256()
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise _UnavailableResource("resource_unavailable") from exc
        if digest.hexdigest() != expected_digest:
            raise _UnavailableResource("resource_unavailable")
        return candidate

    @staticmethod
    def _encode_png(image):
        if not isinstance(image, Image.Image):
            raise TypeError("plugin did not return an image")
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > _MAX_IMAGE_PIXELS:
            raise ValueError("plugin image size is invalid")
        normalized = image if image.mode in {"RGB", "RGBA"} else image.convert("RGB")
        output = io.BytesIO()
        normalized.save(output, format="PNG")
        body = output.getvalue()
        if len(body) > _MAX_RASTER_BYTES:
            raise ValueError("plugin image is too large")
        return body, width, height

    @staticmethod
    def _json_safe_context(context):
        encoded = json.dumps(
            dict(context),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_CONTEXT_BYTES:
            raise ValueError("native context is too large")
        return json.loads(encoded.decode("utf-8"))

    @staticmethod
    def _read_context(context_dir: Path, plugin_id: str):
        safe_plugin_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", plugin_id).strip("._-")
        path = context_dir / f"{safe_plugin_id or 'unknown'}.json"
        try:
            if path.stat().st_size > _MAX_CONTEXT_BYTES:
                return None
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(entry, Mapping)
            or entry.get("schema_version") != 1
            or entry.get("plugin_id") != safe_plugin_id
            or not isinstance(entry.get("payload"), Mapping)
        ):
            return None
        return dict(entry["payload"])

    def _refresh_interval(self, instance):
        refresh = instance.get("refresh")
        value = refresh.get("interval") if isinstance(refresh, Mapping) else None
        if isinstance(value, bool):
            value = None
        try:
            seconds = (
                int(float(value))
                if value is not None
                else int(self.device_config.get_config("plugin_cycle_interval_seconds", 300))
            )
        except (TypeError, ValueError, OverflowError):
            seconds = 300
        return max(60, min(7 * 24 * 60 * 60, seconds))


__all__ = [
    "CloudPublicationProducer",
    "NATIVE_PLUGIN_IDS",
    "ORIGINAL_RASTER_PLUGIN_IDS",
    "build_default_adapter_registry",
]
