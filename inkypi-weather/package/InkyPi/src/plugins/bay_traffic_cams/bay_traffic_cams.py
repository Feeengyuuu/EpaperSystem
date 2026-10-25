"""Bay Traffic Cams: the Caltrans freeway cameras nearest home, live, with a time-lapse.

The main camera fills the left with its last three hourly stills beneath it; four more
cameras on other roads fill the right. Each refresh takes the newest stills; when one
cannot be fetched the last copy on disk stands in, marked with its age.
"""

from __future__ import annotations

import hashlib
import json
import logging

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from plugins.base_plugin.source_book import LIVE, Source, SourceBook
from runtime.long_task_executor import (
    current_instance_identity, current_instance_identity_validator, task_context_or_default,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext
from utils.app_utils import coerce_bool, get_available_font_names
from utils.atomic_file import atomic_write_json
from utils.cache_manager import CacheBudget, cache_namespace_for_directory
from utils.http_client import get_http_client
from utils.safe_image import ImageLimits, safe_open_image

from . import sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "bay_traffic_cams"
CACHE_SCHEMA = 2
CACHE_BUDGET = CacheBudget(30 * 86400, 8, 2 * 1024 * 1024)
FRAME_BUDGET = CacheBudget(7 * 86400, 96, 6 * 1024 * 1024)
FRAME_LIMITS = ImageLimits(max_bytes=512 * 1024, max_width=1280, max_height=1024, max_pixels=1280 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 45
CAMERA_COUNT = 5
SIDE_COUNT = CAMERA_COUNT - 1
SPARE_CAMERAS = 4
PATROL_CANDIDATES = 6
MIN_SECONDS_PER_FRAME = 3.0


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("Bay Traffic Cams instance changed during refresh")


def _float(value, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if -180 <= number <= 180 else fallback


class BayTrafficCams(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _home(self, settings):
        return (_float(settings.get("homeLat"), sources.FREMONT[0]), _float(settings.get("homeLon"), sources.FREMONT[1]))

    def _cache_subdir(self, name):
        """One directory per cache budget, under the plugin's cache (or the override)."""
        path = self.cache_dir(env_var="BAY_TRAFFIC_CAMS_CACHE_DIR", leaf="cache", create=True, strip=True) / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _frames_dir(self):
        return self._cache_subdir("frames")

    @staticmethod
    def _frame_name(url):
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]

    def _read_meta(self, directory):
        try:
            data = json.loads((directory / "frames.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _load_frames(self, urls, now, *, allow_network, context, want=None):
        """{url: {"image", "taken", "fetched", "live"}} for every still that is fetched or on disk.

        Caltrans' "Temporarily Unavailable" card is left out, as if that camera had no still.
        With ``want`` it stops once that many stills are in hand, so spare cameras are only
        fetched when nearer ones are down.
        """
        directory = self._frames_dir()
        meta = self._read_meta(directory)
        namespace = None
        frames, changed = {}, False
        http = get_http_client()
        for url in dict.fromkeys(url for url in urls if sources.safe_image_url(url)):
            if want is not None and len(frames) >= want:
                break
            name = self._frame_name(url)
            path = directory / f"{name}.jpg"
            live = False
            if allow_network and context.remaining_seconds() >= MIN_SECONDS_PER_FRAME:
                try:
                    response = http.request_bytes("GET", url, timeout=6, max_bytes=FRAME_LIMITS.max_bytes,
                                                  context=context, headers={"User-Agent": sources.USER_AGENT},
                                                  allow_redirects=False)
                    with safe_open_image(response.data, limits=FRAME_LIMITS):
                        pass
                    namespace = namespace or cache_namespace_for_directory(directory, FRAME_BUDGET)
                    namespace.put_bytes(path.name, response.data)
                    taken = sources.frame_time(response.headers)
                    meta[name] = {"taken": taken.isoformat() if taken else None, "fetched": now.isoformat()}
                    changed = live = True
                except TaskCancelled:
                    raise
                except Exception as exc:  # one camera down never blanks the wall
                    logger.warning("Traffic camera still unavailable (%s)", type(exc).__name__)
            try:
                if path.is_file() and not path.is_symlink():
                    with safe_open_image(path, limits=FRAME_LIMITS) as source:
                        image = source.convert("RGB")
                    if sources.is_placeholder(image):
                        image.close()
                    else:
                        frames[url] = {"image": image, "live": live, **(meta.get(name) or {})}
            except Exception as exc:
                logger.debug("Stored traffic still unreadable: %s", exc)
        if changed:
            try:
                atomic_write_json(directory / "frames.json", meta)
            except OSError as exc:
                logger.warning("Traffic still index not saved: %s", exc)
        return frames

    def _payload(self, settings, now, *, allow_network, context):
        home = self._home(settings)
        book = SourceBook(self._cache_subdir("sources"), CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=512 * 1024, checkpoint=_checkpoint)
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        cameras = book.load(Source("cameras", {"home": [round(home[0], 3), round(home[1], 3)]},
                                   lambda ctx: sources.fetch_cameras(home, ctx), fresh_seconds=12 * 3600,
                                   usable_seconds=14 * 86400, validate=sources.valid_cameras, timeout_seconds=25,
                                   min_remaining_seconds=8),
                            now, allow_network=allow_network, context=context, force=force)
        data = cameras["data"]
        main_id = str(settings.get("mainCamera") or "").strip()
        nearby = sources.choose(data.get("cameras"), SIDE_COUNT + SPARE_CAMERAS + (1 if main_id else 0), main_id)
        if main_id:
            # A named camera leads the page; the rest of the wall is still the nearest.
            leads, nearby = nearby[:1], nearby[1:]
        else:
            # The main view patrols the whole Bay Area; the wall stays the nearest to home.
            leads = [dict(camera, patrol=True) for camera in sources.patrol(
                data.get("patrol"), now, {camera["id"] for camera in nearby[:SIDE_COUNT]}, PATROL_CANDIDATES)]
        frames = self._load_frames([camera["image"] for camera in leads], now, allow_network=allow_network,
                                   context=context, want=1)
        lead = next((camera for camera in leads if camera["image"] in frames), None)
        # Spare cameras stand by in order for any of the nearest that are down.
        nearby = [camera for camera in nearby if lead is None or camera["id"] != lead["id"]]
        frames.update(self._load_frames([camera["image"] for camera in nearby], now, allow_network=allow_network,
                                        context=context, want=SIDE_COUNT + (0 if lead else 1)))
        chosen = ([lead] if lead else []) + [camera for camera in nearby if camera["image"] in frames]
        chosen = chosen[:CAMERA_COUNT]
        if chosen:
            frames.update(self._load_frames(chosen[0].get("history") or [], now, allow_network=allow_network,
                                            context=context))
        live = sum(1 for camera in chosen if (frames.get(camera["image"]) or {}).get("live"))
        if not frames:
            provenance = SourceProvenance.LOCAL_FALLBACK
        elif allow_network and live < len(chosen):
            provenance = SourceProvenance.STALE_CACHE
        elif cameras["state"] == LIVE or live:
            provenance = SourceProvenance.LIVE
        else:
            provenance = SourceProvenance.FRESH_CACHE
        return {"cameras": chosen, "frames": frames, "list_state": cameras["state"], "provenance": provenance}

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        return self.render_themed_image(settings, device_config, theme_render_only=True,
                                        resolved_theme_context=resolved_theme_context)

    def generate_image(self, settings, device_config):
        from .render import render_page

        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
        cache_only = any(coerce_bool(settings.get(key), default=False)
                         for key in ("_theme_render_only", "_cached_render_only", "cached_render_only"))
        parent = task_context_or_default(timeout_seconds=TASK_SECONDS)
        context = TaskContext(parent.cancel_event, min(parent.deadline_monotonic, parent.clock() + TASK_SECONDS),
                              parent.clock)
        now = sources.local_now()
        _checkpoint(context)
        payload = self._payload(settings, now, allow_network=not cache_only, context=context)
        try:
            image = render_page(self.get_dimensions(device_config), payload, settings, now)
        finally:
            for frame in payload["frames"].values():
                frame["image"].close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
