import os
import json
import logging
import threading
from collections.abc import Mapping

from dotenv import load_dotenv
from config_store import ConfigConflictError, ConfigStore, ConfigStoreError
from model import PlaylistManager, RefreshInfo
from plugins.plugin_manifest import CapabilityCache, PluginManifest
from secret_schema import SecretSchema

logger = logging.getLogger(__name__)

_MODEL_CONFIG_FIELDS = ("playlist_config", "refresh_info")
_MISSING_CONFIG_VALUE = object()
_UNSET_MODEL_BASELINE = object()
_SECRET_SCHEMA = SecretSchema.load()


class ConfigLoadError(ConfigStoreError):
    """The facade cannot expose a trustworthy initial device configuration."""


def _detach_json(value):
    """Return legacy mutable JSON containers from a frozen store value."""

    if isinstance(value, Mapping):
        return {key: _detach_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_detach_json(item) for item in value]
    return value


class Config:
    CONFIG_COMMIT_ATTEMPTS = 4

    # Base path for the project directory
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    # File paths relative to the script's directory
    config_file = os.path.join(BASE_DIR, "config", "device.json")

    # File path for storing the current image being displayed
    current_image_file = os.path.join(BASE_DIR, "static", "images", "current_image.png")

    # Directory path for storing plugin instance images
    plugin_image_dir = os.path.join(BASE_DIR, "static", "images", "plugins")

    def __init__(self, runtime_paths=None):
        self.runtime_paths = runtime_paths
        if runtime_paths is not None:
            self.config_file = runtime_paths.config_file
            self.current_image_file = runtime_paths.current_image_file
            self.plugin_image_dir = runtime_paths.plugin_image_dir
            self.data_dir = runtime_paths.data_dir
            self.cache_dir = runtime_paths.cache_dir
            self.env_file = runtime_paths.env_file
            self.display_dir = runtime_paths.display_dir
            self.flask_secret_file = runtime_paths.flask_secret_file
        self._write_lock = threading.RLock()
        self._env_file_mtimes = None
        self._config_store = ConfigStore(self.config_file)
        self._require_readable_state(self._config_store.load())
        self.plugins_list = self.read_plugins_list()
        self.playlist_manager = self.load_playlist_manager()
        self.refresh_info = self.load_refresh_info()

    def read_config(self):
        """Reload and return a detached device config, or report invalid state."""
        logger.debug(f"Reading device config from {self.config_file}")
        store = getattr(self, "_config_store", None)
        if store is None or str(store.config_path) != os.path.abspath(self.config_file):
            store = ConfigStore(self.config_file)
            self._config_store = store
        state = store.load()
        self._require_readable_state(state)
        config = {} if state.snapshot is None else _detach_json(state.snapshot.data)

        logger.debug("Loaded config:\n%s", json.dumps(config, indent=3))
        return config

    def _require_readable_state(self, state):
        if state.snapshot is not None or state.status.source == "missing":
            return
        reason = state.status.degraded_reason or state.status.source
        raise ConfigLoadError(
            f"device config is invalid or unavailable ({reason}): {self.config_file}"
        )

    def read_plugins_list(self):
        """Reads the plugin-info.json config JSON from each plugin folder. Excludes the base plugin."""
        # Iterate over all plugin folders
        plugins_list = []
        capability_cache = getattr(self, "_plugin_capability_cache", None)
        if capability_cache is None:
            capability_cache = CapabilityCache()
            self._plugin_capability_cache = capability_cache
        for plugin in sorted(os.listdir(os.path.join(self.BASE_DIR, "plugins"))):
            plugin_path = os.path.join(self.BASE_DIR, "plugins", plugin)
            if os.path.isdir(plugin_path) and plugin != "__pycache__":
                # Check if the plugin-info.json file exists
                plugin_info_file = os.path.join(plugin_path, "plugin-info.json")
                if os.path.isfile(plugin_info_file):
                    logger.debug(f"Reading plugin info from {plugin_info_file}")
                    try:
                        manifest = PluginManifest.from_path(
                            plugin_info_file,
                            capability_cache=capability_cache,
                        )
                    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                        logger.exception("Skipping unreadable plugin info: %s", plugin_info_file)
                        continue
                    plugin_info = dict(manifest.raw)
                    plugin_info["_manifest"] = manifest
                    plugins_list.append(plugin_info)

        return plugins_list

    def write_config(self):
        """Persist one detached model snapshot through a bounded CAS commit."""
        with self._get_write_lock():
            logger.debug(f"Writing device config to {self.config_file}")
            model_values = self._capture_model_values()
            self._commit_updates({}, model_values=model_values)

    def _get_write_lock(self):
        if not hasattr(self, "_write_lock"):
            self._write_lock = threading.RLock()
        return self._write_lock

    def get_config(self, key=None, default=None):
        """Read the current immutable snapshot through legacy mutable values."""
        store = getattr(self, "_config_store", None)
        if store is None:
            data = getattr(self, "_compat_config", {})
        else:
            state = store.current()
            self._require_readable_state(state)
            data = {} if state.snapshot is None else state.snapshot.data
        if key is not None:
            if key not in data:
                return default
            return _detach_json(data[key])
        return _detach_json(data)

    @property
    def config(self):
        """Detached compatibility view; the ConfigStore remains authoritative."""

        return self.get_config()

    @config.setter
    def config(self, value):
        """Keep legacy assignment safe, including lightweight ``__new__`` users."""

        replacement = dict(value)
        if not hasattr(self, "_config_store"):
            self._compat_config = _detach_json(replacement)
            return
        with self._get_write_lock():
            model_values = (
                self._capture_model_values()
                if hasattr(self, "playlist_manager") and hasattr(self, "refresh_info")
                else None
            )
            self._commit_updates(
                replacement,
                model_values=model_values,
                replace=True,
            )

    def get_plugins(self):
        """Return JSON-safe plugin configurations in the configured order."""

        return [
            {key: value for key, value in plugin.items() if key != "_manifest"}
            for plugin in self._ordered_plugins()
        ]

    def get_runtime_plugins(self):
        """Return ordered plugin configurations with internal manifest metadata."""

        return self._ordered_plugins()

    def _ordered_plugins(self):
        """Return the internal plugin configurations in the configured order."""

        plugin_order = self.get_config('plugin_order', [])

        if not plugin_order:
            return list(self.plugins_list)

        # Create a dict for quick lookup
        plugins_dict = {p['id']: p for p in self.plugins_list}

        # Build ordered list
        ordered = []
        for plugin_id in plugin_order:
            if plugin_id in plugins_dict:
                ordered.append(plugins_dict.pop(plugin_id))

        # Append any remaining plugins not in the order (new plugins)
        ordered.extend(plugins_dict.values())

        return ordered

    def set_plugin_order(self, order):
        """Sets the custom plugin display order."""
        self.update_value('plugin_order', order, write=True)

    def get_plugin(self, plugin_id):
        """Finds and returns a plugin config by its ID."""
        return next((plugin for plugin in self.plugins_list if plugin['id'] == plugin_id), None)

    def get_resolution(self):
        """Returns the display resolution as a tuple (width, height) from the configuration."""
        resolution = self.get_config("resolution")
        width, height = resolution
        return (int(width), int(height))

    def update_config(self, config):
        """Merge settings and the current models into one transactional commit."""
        updates = dict(config)
        with self._get_write_lock():
            model_values = self._capture_model_values()
            self._commit_updates(updates, model_values=model_values)

    def update_value(self, key, value, write=False):
        """Transactionally update one key while retaining the legacy signature."""
        with self._get_write_lock():
            # A full model snapshot also upgrades legacy playlist instances before
            # ConfigStore applies its strict post-migration validation.  ``write``
            # remains accepted for callers compiled against the former deferred API;
            # every mutation is durable now.
            model_values = self._capture_model_values()
            self._commit_updates({key: value}, model_values=model_values)

    def _capture_model_values(self):
        # PlaylistManager.to_dict() owns its lock and returns a detached point-in-time
        # value.  It has returned before ConfigStore.commit() can acquire its lock.
        playlist_config = self.playlist_manager.to_dict()
        playlist_names = {
            playlist.get("name")
            for playlist in playlist_config.get("playlists", [])
            if isinstance(playlist, dict)
        }
        if playlist_config.get("active_playlist") not in playlist_names:
            playlist_config["active_playlist"] = None
        return {
            "playlist_config": playlist_config,
            "refresh_info": self.refresh_info.to_dict(),
        }

    def _commit_updates(self, updates, *, model_values=None, replace=False):
        last_conflict = None
        model_baseline = _UNSET_MODEL_BASELINE
        for _attempt in range(self.CONFIG_COMMIT_ATTEMPTS):
            state = self._config_store.current()
            self._require_readable_state(state)
            snapshot = state.snapshot
            expected_version = snapshot.version if snapshot is not None else 0
            snapshot_data = {} if snapshot is None else snapshot.data
            current_model_values = tuple(
                _detach_json(snapshot_data[field])
                if field in snapshot_data
                else _MISSING_CONFIG_VALUE
                for field in _MODEL_CONFIG_FIELDS
            )
            if model_baseline is _UNSET_MODEL_BASELINE:
                model_baseline = current_model_values
            elif replace or (
                model_values is not None and current_model_values != model_baseline
            ):
                # A stale model snapshot or whole-config replacement cannot be
                # safely rebased.  Preserve the concurrent revision and make the
                # caller resolve the reported CAS conflict explicitly.
                raise last_conflict
            candidate = (
                {}
                if replace or snapshot is None
                else _detach_json(snapshot_data)
            )
            candidate.update(_detach_json(updates))
            if model_values is not None:
                candidate.update(_detach_json(model_values))
            try:
                self._config_store.commit(expected_version, candidate)
            except ConfigConflictError as error:
                last_conflict = error
                continue
            return
        raise last_conflict

    def load_env_key(self, key):
        """Loads an environment variable from stable InkyPi .env locations."""
        self._reload_env_if_changed()
        for candidate in self._env_key_candidates(key):
            value = os.getenv(candidate)
            if value:
                return value
        return ""

    def _reload_env_if_changed(self):
        """Re-parses the .env candidate files only when one is new or its mtime changed."""
        cached_mtimes = getattr(self, "_env_file_mtimes", None)
        current_mtimes = {}
        for env_file in self._env_file_candidates():
            if (
                env_file
                and os.path.isfile(env_file)
                and os.access(env_file, os.R_OK)
            ):
                try:
                    current_mtimes[env_file] = os.path.getmtime(env_file)
                except OSError:
                    current_mtimes[env_file] = None
        if cached_mtimes is not None and current_mtimes == cached_mtimes:
            return
        loaded_mtimes = {}
        for env_file, mtime in current_mtimes.items():
            try:
                load_dotenv(env_file, override=True)
            except OSError:
                continue
            loaded_mtimes[env_file] = mtime
        if getattr(self, "runtime_paths", None) is None:
            load_dotenv(override=True)
        self._env_file_mtimes = loaded_mtimes

    def _env_key_candidates(self, key):
        """Returns accepted names for a logical environment key."""
        try:
            return _SECRET_SCHEMA.resolve_names(key)
        except KeyError:
            return (key,)

    def _env_file_candidates(self):
        runtime_paths = getattr(self, "runtime_paths", None)
        if runtime_paths is not None:
            yield str(runtime_paths.env_file)
            return

        candidates = []
        explicit_file = os.getenv("INKYPI_ENV_FILE")
        if explicit_file:
            candidates.append(explicit_file)

        project_dir = os.getenv("PROJECT_DIR")
        if project_dir:
            candidates.append(os.path.join(project_dir, ".env"))

        candidates.extend([
            os.path.join(os.getcwd(), ".env"),
            os.path.join(self.BASE_DIR, ".env"),
            os.path.join(os.path.dirname(self.BASE_DIR), ".env"),
            os.path.join(os.path.realpath(self.BASE_DIR), ".env"),
            os.path.join(os.path.dirname(os.path.realpath(self.BASE_DIR)), ".env"),
        ])

        seen = set()
        for path in candidates:
            normalized = os.path.abspath(path)
            if normalized not in seen:
                seen.add(normalized)
                yield normalized

    def load_playlist_manager(self):
        """Loads the playlist manager object from the config."""
        playlist_manager = PlaylistManager.from_dict(self.get_config("playlist_config", default={}))
        if not playlist_manager.playlists:
            playlist_manager.add_default_playlist()
        playlist_snapshot = playlist_manager.to_dict()
        if self._rename_duplicate_legacy_instances(playlist_snapshot):
            playlist_manager = PlaylistManager.from_dict(playlist_snapshot)
        return playlist_manager

    @staticmethod
    def _rename_duplicate_legacy_instances(playlist_config):
        """Migrate ambiguous legacy names without dropping either instance."""

        playlists = playlist_config.get("playlists", [])
        reserved = {
            (instance.get("plugin_id"), instance.get("name"))
            for playlist in playlists
            for instance in playlist.get("plugins", [])
        }
        used = set()
        renamed = False
        for playlist in playlists:
            for instance in playlist.get("plugins", []):
                plugin_id = instance.get("plugin_id")
                original_name = instance.get("name")
                identity = (plugin_id, original_name)
                if identity not in used:
                    used.add(identity)
                    continue

                uuid_suffix = str(instance.get("instance_uuid", "instance"))
                uuid_suffix = uuid_suffix.replace("-", "")[:8] or "instance"
                migrated_name = f"{original_name} ({uuid_suffix})"
                sequence = 2
                candidate_identity = (plugin_id, migrated_name)
                while candidate_identity in reserved or candidate_identity in used:
                    migrated_name = f"{original_name} ({uuid_suffix}-{sequence})"
                    candidate_identity = (plugin_id, migrated_name)
                    sequence += 1
                instance["name"] = migrated_name
                used.add(candidate_identity)
                renamed = True
                logger.warning(
                    "Migrated duplicate legacy plugin identity '%s/%s' in playlist "
                    "'%s' to '%s'; instance UUID and settings were preserved.",
                    plugin_id,
                    original_name,
                    playlist.get("name"),
                    migrated_name,
                )
        return renamed

    def load_refresh_info(self):
        """Loads the refresh information from the config."""
        return RefreshInfo.from_dict(self.get_config("refresh_info", default={}))

    def get_playlist_manager(self):
        """Returns the playlist manager."""
        return self.playlist_manager

    def get_refresh_info(self):
        """Returns the refresh information."""
        return self.refresh_info
