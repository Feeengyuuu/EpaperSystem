import os
import json
import logging
import tempfile
import threading
from dotenv import load_dotenv
from model import PlaylistManager, RefreshInfo
from plugins.plugin_manifest import CapabilityCache, PluginManifest

logger = logging.getLogger(__name__)

ENV_KEY_ALIASES = {
    "GROQ_API_KEY": ("Groq_V2", "GROQ_KEY"),
    "BLIZZARD_CLIENT_ID": ("BNET_CLIENT_ID", "BATTLE_NET_CLIENT_ID", "WOW_CLIENT_ID", "WOW_KEY", "WoW_Key"),
    "BLIZZARD_CLIENT_SECRET": ("BNET_CLIENT_SECRET", "BATTLE_NET_CLIENT_SECRET", "WOW_CLIENT_SECRET"),
    "BLIZZARD_ACCESS_TOKEN": ("BNET_ACCESS_TOKEN", "BATTLE_NET_ACCESS_TOKEN", "WOW_ACCESS_TOKEN"),
    "BLIZZARD_USER_ACCESS_TOKEN": ("BNET_USER_ACCESS_TOKEN", "BATTLE_NET_USER_ACCESS_TOKEN", "WOW_PROFILE_ACCESS_TOKEN"),
    "PIXIV_PHPSESSID": ("PIXIV_COOKIE", "PIXIV_SESSION"),
    "TELEGRAM_BOT_TOKEN": ("TG_BOT_TOKEN", "TELEGRAM_TOKEN", "TELEGRAM_DIGEST_BOT_TOKEN"),
    "TELEGRAM_API_ID": ("TG_API_ID", "TELEGRAM_APP_ID", "TELEGRAM_DIGEST_API_ID"),
    "TELEGRAM_API_HASH": ("TG_API_HASH", "TELEGRAM_APP_HASH", "TELEGRAM_DIGEST_API_HASH"),
    "TELEGRAM_SESSION_PATH": ("TG_SESSION_PATH", "TELEGRAM_ACCOUNT_SESSION", "TELEGRAM_DIGEST_SESSION_PATH"),
}

class Config:
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
        self.config = self.read_config()
        self.plugins_list = self.read_plugins_list()
        self.playlist_manager = self.load_playlist_manager()
        self.refresh_info = self.load_refresh_info()

    def read_config(self):
        """Reads the device config JSON file and returns it as a dictionary."""
        logger.debug(f"Reading device config from {self.config_file}")
        try:
            with open(self.config_file, encoding="utf-8") as f:
                config = json.load(f)
        except FileNotFoundError:
            logger.warning("Device config file not found: %s", self.config_file)
            return {}
        except json.JSONDecodeError:
            logger.exception("Device config file is not valid JSON: %s", self.config_file)
            return {}

        if not isinstance(config, dict):
            logger.warning("Device config root must be an object, got %s", type(config).__name__)
            return {}

        logger.debug("Loaded config:\n%s", json.dumps(config, indent=3))

        return config

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
        """Updates the cached config from the model objects and writes to the config file."""
        with self._get_write_lock():
            logger.debug(f"Writing device config to {self.config_file}")
            self.update_value("playlist_config", self.playlist_manager.to_dict())
            self.update_value("refresh_info", self.refresh_info.to_dict())
            config_dir = os.path.dirname(self.config_file)
            os.makedirs(config_dir, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".device.",
                suffix=".json.tmp",
                dir=config_dir,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as outfile:
                    self._write_json(outfile)
                try:
                    os.replace(tmp_path, self.config_file)
                    tmp_path = None
                except OSError:
                    logger.exception(
                        "Atomic config replace failed; falling back to direct write: %s",
                        self.config_file,
                    )
                    with open(self.config_file, "w", encoding="utf-8") as outfile:
                        self._write_json(outfile)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        logger.warning("Could not remove temporary config file: %s", tmp_path)

    def _get_write_lock(self):
        if not hasattr(self, "_write_lock"):
            self._write_lock = threading.RLock()
        return self._write_lock

    def _write_json(self, outfile):
        json.dump(self.config, outfile, indent=4)
        outfile.write("\n")

    def get_config(self, key=None, default=None):
        """Gets the value of a specific configuration key or returns the entire config if none provided."""
        if key is not None:
            return self.config.get(key, default)
        return self.config

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

        plugin_order = self.config.get('plugin_order', [])

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
        """Updates the config with the new values provided and writes to the config file."""
        self.config.update(config)
        self.write_config()

    def update_value(self, key, value, write=False):
        """Updates a specific key in the configuration with a new value and optionally writes it to the config file."""
        self.config[key] = value
        if write:
            self.write_config()

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
            if env_file and os.path.isfile(env_file):
                try:
                    current_mtimes[env_file] = os.path.getmtime(env_file)
                except OSError:
                    current_mtimes[env_file] = None
        if cached_mtimes is not None and current_mtimes == cached_mtimes:
            return
        for env_file in current_mtimes:
            load_dotenv(env_file, override=True)
        if getattr(self, "runtime_paths", None) is None:
            load_dotenv(override=True)
        self._env_file_mtimes = current_mtimes

    def _env_key_candidates(self, key):
        """Returns accepted names for a logical environment key."""
        return (key, *ENV_KEY_ALIASES.get(key, ()))

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
        return playlist_manager

    def load_refresh_info(self):
        """Loads the refresh information from the config."""
        return RefreshInfo.from_dict(self.get_config("refresh_info", default={}))

    def get_playlist_manager(self):
        """Returns the playlist manager."""
        return self.playlist_manager

    def get_refresh_info(self):
        """Returns the refresh information."""
        return self.refresh_info
