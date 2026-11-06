"""万象速览: one calm page of twelve small public feeds. A line of poetry heads it; the live sun and
the month's Webb or Hubble picture stand on the left, DW 中文, 纽约时报中文网 and 少数派 headlines in
the middle, the US public debt, California's gasoline price, the crypto Fear & Greed Index and
yesterday's Wordle answer on the right, and along the foot a NetEase chart's top songs with their
album covers (soaring, new, original or hot, by the hour), and Douban's films now showing and its
American series with their posters and ratings.

Each feed keeps its own cache and freshness (sources.py): one that fails or runs late only hides its
own card, and the rest of the page draws from what is at hand.
"""

from __future__ import annotations

import logging
import time

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import attach_source_provenance
from plugins.base_plugin.source_book import FRESH_CACHE, LIVE, STALE_CACHE, Source, SourceBook, combined_provenance
from runtime.long_task_executor import (
    current_instance_identity, current_instance_identity_validator, task_context_or_default,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext
from utils.app_utils import coerce_bool, get_available_font_names
from utils.cache_manager import CacheBudget
from utils.http_client import get_http_client
from utils.remote_thumbnails import load_thumbnails

from . import render, sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "daily_digest"
# 2: the Douban lists carry their posters and NetEase is read chart by chart.
CACHE_SCHEMA = 2
CACHE_BUDGET = CacheBudget(21 * 86400, 32, 2 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 50
HOUR, DAY = 3600, 86400
# (fresh, usable) seconds per feed: how long a reading stands, and how long it may stand in when
# its provider fails. Headlines move hourly; charts, ratings, the debt and the index daily; the
# gasoline survey weekly (EIA publishes Monday evenings); the pictures of the month monthly.
FRESHNESS = {
    "poem": (HOUR, 30 * DAY),
    "news": (HOUR, DAY),
    "music": (6 * HOUR, 3 * DAY),
    "douban": (6 * HOUR, 3 * DAY),
    "gas": (12 * HOUR, 21 * DAY),
    "space": (12 * HOUR, 62 * DAY),
    "debt": (6 * HOUR, 7 * DAY),
    "fear_greed": (3 * HOUR, 3 * DAY),
    "wordle": (7 * DAY, 7 * DAY),
}
# The sun is fetched once per slot and an older slot's copy stands in for up to SUN_USABLE.
SUN_SLOT_SECONDS = 30 * 60
SUN_USABLE_SLOTS = 12
SUN_PIXELS = (320, 320)
SUN_BUDGET = CacheBudget(2 * 86400, 6, 2 * 1024 * 1024)
SPACE_PIXELS = (480, 360)
SPACE_BUDGET = CacheBudget(70 * 86400, 6, 2 * 1024 * 1024)
# The foot's album covers (four charts' top three) and posters (two collections' first three), small:
# they print about 64 px wide.
COVER_PIXELS = (160, 160)
POSTER_PIXELS = (160, 240)
ART_BUDGET = CacheBudget(7 * 86400, 48, 4 * 1024 * 1024)
SPACE_STATE_RANK = {LIVE: 0, FRESH_CACHE: 1, STALE_CACHE: 2}


def healthiest_result(results):
    """The healthiest of feeds that stand in for each other on the page (live before a fresh cache
    before a stale one), so one slow member alone does not degrade the page."""
    return min(results, key=lambda result: SPACE_STATE_RANK.get(result.get("state"), len(SPACE_STATE_RANK)))


def space_result(webb, hubble):
    """Webb's and Hubble's pictures stand in for each other (the page shows whichever it has), so the
    pair counts as its healthier member: ESA's slow Hubble site alone does not degrade the page."""
    return healthiest_result((webb, hubble))


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("万象速览 instance changed during refresh")


class DailyDigest(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_root(self):
        return self.cache_dir(env_var="DAILY_DIGEST_CACHE_DIR", leaf="cache", create=True, strip=True)

    def _cache_subdir(self, name):
        """One directory per cache budget: feeds and pictures never share one."""
        path = self._cache_root() / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _zone(device_config):
        get = getattr(device_config, "get_config", None)
        return sources.device_zone(get("timezone") if get else None)

    def _payload(self, settings, now, *, allow_network, context):
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = SourceBook(self._cache_subdir("sources"), CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=256 * 1024,
                          checkpoint=_checkpoint)

        def load(name, kind, identity, fetch, validate, timeout=8):
            fresh, usable = FRESHNESS[kind]
            return book.load(Source(name, identity, fetch, fresh_seconds=fresh, usable_seconds=usable,
                                    validate=validate, timeout_seconds=timeout),
                             now, allow_network=allow_network, context=context, force=force)

        wordle_day = sources.wordle_day(now)
        payload = {
            "poem": load("poem", "poem", {"c": "i", "max": sources.POEM_MAX_LENGTH}, sources.fetch_poem,
                         sources.valid_poem, timeout=5),
            "news": {name: load(f"news-{name}", "news", {"feed": name},
                                lambda ctx, name=name: sources.fetch_news(name, ctx), sources.valid_items)
                     for name in sources.NEWS_FEEDS},
            # "fields" 2: each song also carries its album, release day, length and move.
            "music": {chart_id: load(f"music-{chart_id}", "music", {"chart": chart_id, "fields": 2},
                                     lambda ctx, chart_id=chart_id: sources.fetch_chart(chart_id, ctx),
                                     sources.valid_chart)
                      for chart_id in sources.NETEASE_CHARTS},
            "showing": load("douban-showing", "douban", {"collection": "showing"},
                            lambda ctx: sources.fetch_douban("showing", ctx), sources.valid_items),
            "us_tv": load("douban-us-tv", "douban", {"collection": "us_tv"},
                          lambda ctx: sources.fetch_douban("us_tv", ctx), sources.valid_items),
            "gas": load("gas", "gas", {"series": sources.EIA_SERIES}, sources.fetch_gas, sources.valid_gas),
            "webb": load("space-webb", "space", {"page": "webb"}, lambda ctx: sources.fetch_space("webb", ctx),
                         sources.valid_items),
            "hubble": load("space-hubble", "space", {"page": "hubble"},
                           lambda ctx: sources.fetch_space("hubble", ctx), sources.valid_items),
            "debt": load("debt", "debt", {"series": "debt_to_penny"}, sources.fetch_debt, sources.valid_debt),
            "fear_greed": load("fear-greed", "fear_greed", {"days": 7}, sources.fetch_fear_greed,
                               sources.valid_fear_greed),
            "wordle": load("wordle", "wordle", {"day": wordle_day},
                           lambda ctx: sources.fetch_wordle(wordle_day, ctx), sources.valid_wordle, timeout=5),
        }
        results = [result for key, result in payload.items() if key not in ("news", "music", "webb", "hubble")]
        # The headline feeds share one column, so one late feed alone does not degrade the page.
        results += [healthiest_result(list(payload["news"].values())), *payload["music"].values(),
                    space_result(payload["webb"], payload["hubble"])]
        payload["provenance"] = combined_provenance(results)
        return payload

    def _sun(self, now_epoch, *, allow_network, context, options):
        """The latest sun picture at hand: this slot's (fetched when the network is allowed), else the
        newest earlier slot still cached. (picture, slot epoch) or (None, None)."""
        directory = self._cache_subdir("sun")
        slot = int(now_epoch // SUN_SLOT_SECONDS) * SUN_SLOT_SECONDS
        for index in range(SUN_USABLE_SLOTS):
            stamp = slot - index * SUN_SLOT_SECONDS
            url = sources.sun_url(stamp)
            found = load_thumbnails([url], directory, cached_only=not (allow_network and index == 0),
                                    size=SUN_PIXELS, budget=SUN_BUDGET, safe_url=sources.safe_sun_image, **options)
            if found:
                return found[url], stamp
        return None, None

    def _art(self, payload, now, *, allow_network, options):
        """The album covers and posters along the foot, the chart shown this hour first: {url: picture}.
        One that does not load only leaves its song or title without a picture."""
        shown = render.music_chart(payload["music"], now)
        charts = [shown] + [data for data in render.music_charts(payload["music"]) if data is not shown]
        covers = [track.get("cover") for data in charts if data for track in data["tracks"]]
        posters = [item.get("poster") for key in ("showing", "us_tv")
                   for item in ((payload[key].get("data") or {}).get("items") or [])[:render.MEDIA_ROWS]]
        directory = self._cache_subdir("art")
        art = load_thumbnails([url for url in covers if url], directory, cached_only=not allow_network,
                              size=COVER_PIXELS, budget=ART_BUDGET, safe_url=sources.safe_music_image, **options)
        art.update(load_thumbnails([url for url in posters if url], directory, cached_only=not allow_network,
                                   size=POSTER_PIXELS, budget=ART_BUDGET, safe_url=sources.safe_douban_image,
                                   headers={"Referer": sources.DOUBAN_REFERER}, **options))
        return art

    def _images(self, payload, now, now_epoch, *, allow_network, context):
        options = {"http": get_http_client(), "context": context, "user_agent": sources.USER_AGENT}
        today = now.date()
        images = {}
        sun, stamp = self._sun(now_epoch, allow_network=allow_network, context=context, options=options)
        if sun is not None:
            images["sun"] = sun
        payload["sun_slot"] = stamp
        name, picture = sources.space_pick(payload["webb"].get("data"), payload["hubble"].get("data"), today)
        if picture:
            found = load_thumbnails([picture["image"]], self._cache_subdir("space"), cached_only=not allow_network,
                                    size=SPACE_PIXELS, budget=SPACE_BUDGET, safe_url=sources.safe_space_image,
                                    **options)
            if picture["image"] in found:
                images["space"] = found[picture["image"]]
        payload["space"] = {"name": name, **(picture or {})} if picture else None
        images.update(self._art(payload, now, allow_network=allow_network, options=options))
        return images

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        return self.render_themed_image(settings, device_config, theme_render_only=True,
                                        resolved_theme_context=resolved_theme_context)

    def generate_image(self, settings, device_config):
        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
        cache_only = any(coerce_bool(settings.get(key), default=False)
                         for key in ("_theme_render_only", "_cached_render_only", "cached_render_only"))
        parent = task_context_or_default(timeout_seconds=TASK_SECONDS)
        context = TaskContext(parent.cancel_event, min(parent.deadline_monotonic, parent.clock() + TASK_SECONDS),
                              parent.clock)
        now = sources.local_now(zone=self._zone(device_config))
        _checkpoint(context)
        payload = self._payload(settings, now, allow_network=not cache_only, context=context)
        images = self._images(payload, now, time.time(), allow_network=not cache_only, context=context)
        _checkpoint(context)
        try:
            image = render.render_page(self.get_dimensions(device_config), payload, settings, now, images)
        finally:
            for picture in images.values():
                picture.close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
