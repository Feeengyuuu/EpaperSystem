import logging
import os
from plugins.plugin_registry import plugin_supports_day_night_theme
from plugins.plugin_settings import resolve_refresh_on_display
from utils.app_utils import resolve_path, get_fonts, resolve_dimensions
from utils.browser_renderer import get_browser_renderer
from utils.cache_manager import (
    CacheBudget,
    DEFAULT_CACHE_BUDGET,
    cache_namespace_for_directory,
)
from utils.image_loader import AdaptiveImageLoader
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
import asyncio
import base64

logger = logging.getLogger(__name__)

STATIC_DIR = resolve_path("static")
PLUGINS_DIR = resolve_path("plugins")
BASE_PLUGIN_DIR =  os.path.join(PLUGINS_DIR, "base_plugin")
BASE_PLUGIN_RENDER_DIR = os.path.join(BASE_PLUGIN_DIR, "render")

FRAME_STYLES = [
    {
        "name": "None",
        "icon": "frames/blank.png"
    },
    {
        "name": "Corner",
        "icon": "frames/corner.png"
    },
    {
        "name": "Top and Bottom",
        "icon": "frames/top_and_bottom.png"
    },
    {
        "name": "Rectangle",
        "icon": "frames/rectangle.png"
    }
]

class BasePlugin:
    """Base class for all plugins."""
    def __init__(self, config, **dependencies):
        self.config = config

        # Initialize adaptive image loader for device-aware image processing
        self.image_loader = AdaptiveImageLoader()

        self.render_dir = self.get_plugin_dir("render")
        if os.path.exists(self.render_dir):
            # instantiate jinja2 env with base plugin and current plugin render directories
            loader = FileSystemLoader([self.render_dir, BASE_PLUGIN_RENDER_DIR])
            self.env = Environment(
                loader=loader,
                autoescape=select_autoescape(['html', 'xml'])
            )

    def wants_refresh_on_display(self, settings):
        """Return whether cached playlist display should refresh this plugin instance."""
        return resolve_refresh_on_display(settings, self.config)

    def get_live_refresh_state(self, settings, current_dt):
        """Return live refresh state for scheduler cache refresh, or None when inactive."""
        return None

    def generate_image(self, settings, device_config):
        raise NotImplementedError("generate_image must be implemented by subclasses")

    def get_dimensions(self, device_config):
        """Return the display resolution as (width, height), swapped for vertical orientation."""
        return resolve_dimensions(device_config)

    def cleanup(self, settings):
        """Optional cleanup method that plugins can override to delete associated resources.

        Called when a plugin instance is deleted. Plugins should override this to clean up
        any files, external resources, or other data associated with the plugin instance.

        Args:
            settings: The plugin instance's settings dict, which may contain file paths or other resources
        """
        pass  # Default implementation does nothing

    def get_plugin_id(self):
        return self.config.get("id")

    def get_plugin_dir(self, path=None):
        plugin_dir = os.path.join(PLUGINS_DIR, self.get_plugin_id())
        if path:
            plugin_dir = os.path.join(plugin_dir, path)
        return plugin_dir

    def cache_dir(self, env_var=None, leaf=None, create=True, strip=False):
        """Resolve a plugin cache directory, honoring an optional env-var override.

        env_var: name of an override environment variable (or None to skip)
        leaf:    subdirectory under the plugin dir used when no override is set
        create:  mkdir(parents=True, exist_ok=True) before returning
        strip:   strip() whitespace from the override value before using it
        """
        override = os.getenv(env_var) if env_var else None
        if strip and override is not None:
            override = override.strip()

        runtime_root_raw = os.getenv("INKYPI_CACHE_DIR", "").strip()
        runtime_root = Path(runtime_root_raw).expanduser() if runtime_root_raw else None
        if runtime_root is not None:
            plugin_root = runtime_root / "plugins" / self.get_plugin_id()
        else:
            plugin_root = Path(self.get_plugin_dir())

        if override:
            path = Path(override).expanduser()
            if runtime_root is not None and not path.is_absolute():
                path = plugin_root / path
        elif leaf is not None:
            path = plugin_root / leaf
        else:
            path = plugin_root
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def managed_cache_namespace(
        self,
        directory,
        budget: CacheBudget = DEFAULT_CACHE_BUDGET,
    ):
        """Return a budgeted namespace owning exactly ``directory``."""

        return cache_namespace_for_directory(directory, budget)

    def data_dir(
        self,
        env_var=None,
        leaf=None,
        create=True,
        strip=False,
        legacy_leaf=None,
    ):
        """Resolve durable per-plugin state outside the immutable release tree.

        ``legacy_leaf`` preserves an existing development location while
        production uses ``INKYPI_DATA_DIR/plugins/<plugin id>/<leaf>``.
        """
        override = os.getenv(env_var) if env_var else None
        if strip and override is not None:
            override = override.strip()

        runtime_root_raw = os.getenv("INKYPI_DATA_DIR", "").strip()
        runtime_root = Path(runtime_root_raw).expanduser() if runtime_root_raw else None
        if runtime_root is not None:
            plugin_root = runtime_root / "plugins" / self.get_plugin_id()
        else:
            plugin_root = Path(self.get_plugin_dir())

        if override:
            path = Path(override).expanduser()
            if runtime_root is not None and not path.is_absolute():
                path = plugin_root / path
        elif runtime_root is not None:
            path = plugin_root / leaf if leaf is not None else plugin_root
        else:
            fallback_leaf = legacy_leaf if legacy_leaf is not None else leaf
            path = plugin_root / fallback_leaf if fallback_leaf is not None else plugin_root
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def generate_settings_template(self):
        template_params = {
            "settings_template": "base_plugin/settings.html",
            "supports_day_night_theme": plugin_supports_day_night_theme(
                self.config
            ),
        }

        settings_path = self.get_plugin_dir("settings.html")
        if Path(settings_path).is_file():
            template_params["settings_template"] = f"{self.get_plugin_id()}/settings.html"

        template_params['frame_styles'] = FRAME_STYLES
        return template_params

    def render_image(self, dimensions, html_file, css_file=None, template_params=None):
        template_params = dict(template_params or {})
        # load the base plugin and current plugin css files
        css_files = [os.path.join(BASE_PLUGIN_RENDER_DIR, "plugin.css")]
        if css_file:
            plugin_css = os.path.join(self.render_dir, css_file)
            css_files.append(plugin_css)

        template_params["style_sheets"] = css_files
        template_params["width"] = dimensions[0]
        template_params["height"] = dimensions[1]
        template_params["font_faces"] = get_fonts()
        template_params["static_dir"] = STATIC_DIR

        # load and render the given html template
        template = self.env.get_template(html_file)
        rendered_html = template.render(template_params)

        return get_browser_renderer().render_html(
            rendered_html,
            viewport=dimensions,
        )
