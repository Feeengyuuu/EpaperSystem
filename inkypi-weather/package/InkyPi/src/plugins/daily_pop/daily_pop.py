"""Daily Pop: Bilibili trending, today's anime, Apple's most played, Chinese Wikipedia's
most read, the daily chess puzzle and today's whole Earth, on one page.

The page keeps moving on its own: the Earth frame follows the time of day, the charts
show how each entry moved since the previous refresh, and yesterday's puzzle answer is
revealed beside today's.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import attach_source_provenance
from plugins.base_plugin.source_book import LIVE, Source, SourceBook, combined_provenance
from runtime.long_task_executor import (
    current_instance_identity, current_instance_identity_validator, task_context_or_default,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext
from utils.app_utils import coerce_bool, get_available_font_names
from utils.atomic_file import atomic_write_json
from utils.cache_manager import CacheBudget
from utils.http_client import get_http_client
from utils.remote_thumbnails import load_thumbnails

from . import sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "daily_pop"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(30 * 86400, 16, 4 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 50
# EPIC's frames come about every 1 h 50 min: a refresh fetches the one for now and the next
# ones, kept apart from the covers at twice the globe's size.
EPIC_PREFETCH = 3
EARTH_PIXELS = (272, 272)
HISTORY_FILE = "history.json"
# Each shown song's artist by their official Apple Music photo (sources.fetch_artist_photo): a found
# photo kept 30 days, a page that names none 3, at most ARTIST_LOOKUPS pages read a refresh.
ARTISTS_FILE = "artists.json"
ARTIST_FOUND_TTL, ARTIST_MISSED_TTL = timedelta(days=30), timedelta(days=3)
ARTIST_LOOKUPS = 3
ARTIST_PIXELS = (96, 96)


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("Daily Pop instance changed during refresh")


def chart_moves(current, previous, key):
    """{key: movement} of a chart against its previous reading: "new", or rank places gained (+) or lost (-)."""
    before = {row.get(key): index for index, row in enumerate(previous or [])}
    moves = {}
    for index, row in enumerate(current or []):
        name = row.get(key)
        moves[name] = "new" if name not in before else before[name] - index
    return moves


class DailyPop(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_root(self):
        return self.cache_dir(env_var="DAILY_POP_CACHE_DIR", leaf="cache", create=True, strip=True)

    def _cache_subdir(self, name):
        """One directory per cache budget: feeds and covers never share one."""
        path = self._cache_root() / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _book(self):
        return SourceBook(self._cache_subdir("sources"), CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=1024 * 1024,
                          checkpoint=_checkpoint)

    # -- history: what each chart looked like at the previous refresh --------------------------
    def _history_path(self):
        return self._cache_root() / HISTORY_FILE

    def _read_history(self):
        try:
            data = json.loads(self._history_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _roll_history(self, history, name, result, rows_key):
        """When a feed was just fetched, its last reading becomes the previous one."""
        entry = dict(history.get(name) or {})
        data = result.get("data") or {}
        if result.get("state") == LIVE and data.get(rows_key) and data.get(rows_key) != entry.get("current"):
            entry = {"previous": entry.get("current"), "previous_at": entry.get("current_at"),
                     "current": data.get(rows_key), "current_at": result.get("fetched_at")}
            history[name] = entry
            return True
        return False

    def _roll_puzzle(self, history, result):
        """Keep yesterday's puzzle so today's page can show its answer."""
        data = result.get("data") or {}
        entry = dict(history.get("puzzle") or {})
        if result.get("state") == LIVE and data.get("fen") and data.get("fen") != (entry.get("current") or {}).get("fen"):
            history["puzzle"] = {"previous": entry.get("current"), "current": data}
            return True
        return False

    def _payload(self, settings, now, *, allow_network, context):
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = self._book()

        def load(spec):
            return book.load(spec, now, allow_network=allow_network, context=context, force=force)

        results = {
            "bilibili": load(Source("bilibili", {"ps": 10}, sources.fetch_bilibili, fresh_seconds=1800,
                                    usable_seconds=2 * 86400, validate=sources.valid_bilibili, timeout_seconds=8)),
            "bangumi": load(Source("bangumi", {"weekday": now.isoweekday()},
                                   lambda ctx: sources.fetch_bangumi(now, ctx), fresh_seconds=6 * 3600,
                                   usable_seconds=86400, validate=sources.valid_bangumi, timeout_seconds=10)),
            "songs": load(Source("songs", {"chart": "us-most-played"}, sources.fetch_songs, fresh_seconds=6 * 3600,
                                 usable_seconds=3 * 86400, validate=sources.valid_songs, timeout_seconds=16)),
            "wiki": load(Source("wiki", {"lang": "zh", "day": now.date().isoformat()},
                                lambda ctx: sources.fetch_most_read(now, ctx), fresh_seconds=6 * 3600,
                                usable_seconds=2 * 86400, validate=sources.valid_most_read, timeout_seconds=16)),
            "puzzle": load(Source("puzzle", {"site": "chess.com"}, sources.fetch_puzzle, fresh_seconds=3 * 3600,
                                  usable_seconds=2 * 86400, validate=sources.valid_puzzle, timeout_seconds=8)),
            "epic": load(Source("epic", {"collection": "natural"}, sources.fetch_epic, fresh_seconds=3 * 3600,
                                usable_seconds=4 * 86400, validate=sources.valid_epic, timeout_seconds=8)),
        }
        # The shown shows' episode count, genre and air dates: twelve small requests a day at most,
        # kept apart from the page's provenance (missing details only hide their marks).
        shown = [show["id"] for show in ((results["bangumi"].get("data") or {}).get("shows") or [])[:sources.BANGUMI_SHOWN]
                 if isinstance(show, dict) and show.get("id")]
        details = load(Source("bangumi_details", {"ids": ",".join(map(str, shown))},
                              lambda ctx: sources.fetch_show_details(shown, now, ctx), fresh_seconds=86400,
                              usable_seconds=3 * 86400, validate=sources.valid_show_details,
                              timeout_seconds=20)) if shown else {}
        history = self._read_history()
        changed = False
        if allow_network:
            changed |= self._roll_history(history, "bilibili", results["bilibili"], "videos")
            changed |= self._roll_history(history, "songs", results["songs"], "songs")
            changed |= self._roll_puzzle(history, results["puzzle"])
            if changed:
                try:
                    atomic_write_json(self._history_path(), history)
                except OSError as exc:
                    logger.warning("Daily Pop history not saved: %s", exc)
        songs = ((results["songs"].get("data") or {}).get("songs") or [])[:3]
        return {**results, "history": history, "bangumi_details": (details.get("data") or {}).get("shows") or {},
                "artist_photos": self._artist_photos(songs, now, allow_network=allow_network, context=context),
                "provenance": combined_provenance(results.values())}

    def _artist_photos(self, songs, now, *, allow_network, context):
        """{artist page: photo URL} for the shown songs' artists: the kept answers, with up to
        ARTIST_LOOKUPS pages read when the network is allowed; a redraw only reads what is kept."""
        now = now.astimezone(timezone.utc)
        path = self._cache_root() / ARTISTS_FILE
        try:
            kept = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept = {}
        kept = kept if isinstance(kept, dict) else {}

        def current(entry):
            try:
                return now - datetime.fromisoformat(entry["at"]) <= (ARTIST_FOUND_TTL if entry.get("photo")
                                                                     else ARTIST_MISSED_TTL)
            except (KeyError, TypeError, ValueError):
                return False

        pages = list(dict.fromkeys(song.get("artist_url") for song in songs if song.get("artist_url")))
        wanted = [page for page in pages if not (isinstance(kept.get(page), dict) and current(kept[page]))]
        if wanted and allow_network:
            changed = False
            for page in wanted[:ARTIST_LOOKUPS]:
                try:
                    photo = sources.fetch_artist_photo(page, context)
                except TaskCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - the song keeps its artist's name alone.
                    logger.info("Daily Pop artist photo unavailable: %s", type(exc).__name__)
                    continue
                kept[page] = {"photo": photo, "at": now.isoformat()}
                changed = True
            if changed:
                try:
                    atomic_write_json(path, dict(list(kept.items())[-64:]))
                except OSError as exc:
                    logger.warning("Daily Pop artist photos not kept: %s", exc)
        return {page: kept[page]["photo"] for page in pages
                if isinstance(kept.get(page), dict) and kept[page].get("photo")}

    def _images(self, payload, now, *, allow_network, context):
        urls = [video.get("cover") for video in payload["bilibili"]["data"].get("videos", [])[:4]]
        urls += [show.get("cover") for show in payload["bangumi"]["data"].get("shows", [])[:sources.BANGUMI_SHOWN]]
        urls += [song.get("artwork") for song in payload["songs"]["data"].get("songs", [])[:3]]
        urls += [payload["puzzle"]["data"].get("image")]
        options = {"http": get_http_client(), "context": context, "safe_url": sources.safe_image_url,
                   "user_agent": sources.USER_AGENT}
        images = load_thumbnails(urls, self._cache_subdir("covers"), cached_only=not allow_network,
                                 size=(480, 480), **options)
        # The shown songs' artists, small, beside their names.
        artists = list((payload.get("artist_photos") or {}).values())
        if artists:
            images.update(load_thumbnails(artists, self._cache_subdir("artists"), cached_only=not allow_network,
                                          size=ARTIST_PIXELS, **options))
        frame_index, frame_url, earth = self._earth(payload["epic"]["data"], now, allow_network, options)
        if earth is not None:
            images[frame_url] = earth
        return images, frame_url, frame_index

    def _earth(self, epic, now, allow_network, options):
        """The EPIC frame to show and its picture: the one for now when it is at hand, else the
        nearest one in time that is cached. A refresh also fetches the frames the clock turns
        to next, so the cached renders between refreshes keep a globe as the hours pass (a frame
        the cache lacks once left the circle empty)."""
        frames = (epic or {}).get("frames") or []
        directory = self._cache_subdir("earth")
        if allow_network:
            ahead = [frames[index]["image"] for index in sources.epic_frames_ahead(epic, now, EPIC_PREFETCH)]
            for picture in load_thumbnails(ahead, directory, cached_only=False, size=EARTH_PIXELS, **options).values():
                picture.close()
        for index in sources.epic_frames_by_nearness(epic, now):
            url = sources.safe_image_url(frames[index]["image"])
            found = load_thumbnails([url], directory, cached_only=True, size=EARTH_PIXELS, **options)
            if found:
                return index, url, found[url]
        index = sources.epic_frame_index(epic, now)
        return index, None if index is None else frames[index]["image"], None

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
        images, frame_url, frame_index = self._images(payload, now, allow_network=not cache_only, context=context)
        payload["epic_frame_url"], payload["epic_frame_index"] = frame_url, frame_index
        try:
            image = render_page(self.get_dimensions(device_config), payload, settings, now, images)
        finally:
            for picture in images.values():
                picture.close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
