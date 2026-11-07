"""打印灵感: what 3D-printing makers like and print most this week on Printables. The week's most
liked model leads with its picture, maker, category, summary and counts; the next ones stand in
cards beneath it; the right column lists the week's most printed models and the all-time classics.
With an OpenAI key the model names and summaries read in Chinese.
"""

from __future__ import annotations

import logging

from plugins.ai_papers import translate
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import attach_source_provenance
from plugins.base_plugin.source_book import Source, SourceBook, combined_provenance
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
PLUGIN_ID = "print_inspiration"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(14 * 86400, 32, 2 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 55
# The week's lists move through the day: a reading stands for three hours.
FRESH_SECONDS = 3 * 3600
USABLE_SECONDS = 4 * 86400
PICTURE_PIXELS = (480, 360)
SMALL_PICTURE_PIXELS = (160, 120)
# A PNG model picture's thumbnail runs to about 760 KB at 640 x 480.
AVATAR_PIXELS = (64, 64)
TRANSLATE_MIN_SECONDS = 30
INSTRUCTIONS = (
    "Translate each 3D-printable model's name and one-sentence summary into concise Simplified Chinese for a "
    "maker skimming a list. Keep product, printer and brand names (Benchy, Prusa, Bambu, Gridfinity, IKEA) and "
    "sizes as written; keep the name under 18 Chinese characters and the summary under 60. Return every id once."
)


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("打印灵感 instance changed during refresh")


class PrintInspiration(BasePlugin):
    translation_client_factory = None

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_root(self):
        return self.cache_dir(env_var="PRINT_INSPIRATION_CACHE_DIR", leaf="cache", create=True, strip=True)

    def _cache_subdir(self, name):
        path = self._cache_root() / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _payload(self, settings, now, *, allow_network, context):
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = SourceBook(self._cache_subdir("sources"), CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=256 * 1024,
                          checkpoint=_checkpoint)
        results = {key: book.load(Source(f"list-{key}", {"ordering": sources.LISTS[key][0]},
                                         lambda ctx, key=key: sources.fetch_list(key, ctx),
                                         fresh_seconds=FRESH_SECONDS, usable_seconds=USABLE_SECONDS,
                                         validate=sources.valid_list, timeout_seconds=15),
                                  now, allow_network=allow_network, context=context, force=force)
                   for key in sources.LISTS}
        lists = {key: (result.get("data") or {}).get("models") or [] for key, result in results.items()}
        return {**lists, "provenance": combined_provenance(list(results.values()))}

    def _translations(self, models, device_config, *, allow_network, context, settings):
        kept = translate.Translations(self._cache_root() / "translations.json")
        if not coerce_bool(settings.get("translate"), default=True):
            return {}
        items = [{"id": model["id"], "title": model["name"], "summary": model["summary"]} for model in models]
        missing = kept.missing(items)
        key = self._openai_key(device_config)
        if missing and key and allow_network and context.remaining_seconds() >= TRANSLATE_MIN_SECONDS:
            try:
                kept.add(translate.translate(missing, key, client_factory=self.translation_client_factory,
                                             instructions=INSTRUCTIONS, name="print_inspiration"))
            except TaskCancelled:
                raise
            except Exception as exc:  # the page stays in English until a later refresh translates it
                logger.warning("Print inspiration translation failed (%s)", type(exc).__name__)
        return {item["id"]: kept.get(item["id"]) for item in items if kept.get(item["id"])}

    @staticmethod
    def _openai_key(device_config):
        load = getattr(device_config, "load_env_key", None)
        if load is None:
            return ""
        return str(load("OPEN_AI_SECRET") or load("OPENAI_API_KEY") or "").strip()

    def _images(self, payload, *, allow_network, context):
        """The pictures the page shows (large for the lead and cards, small for the right column), each
        under its model's first picture URL though it may come from a later one (an old upload can have
        no thumbnail), and the lead's maker's avatar."""
        common = {"cached_only": not allow_network, "http": get_http_client(), "context": context,
                  "safe_url": sources.safe_image_url, "user_agent": sources.USER_AGENT}
        images = {}

        def first(model, urls_key, key, directory, pixels):
            for url in model.get(urls_key) or [model.get(key)]:
                if not url:
                    continue
                found = load_thumbnails([url], self._cache_subdir(directory), size=pixels, **common)
                if found:
                    images[model[key]] = found[url]
                    return

        week = payload["week"][:render.CARDS + 1]
        for model in payload["makes"] + payload["classics"]:
            first(model, "pictures_small", "image_small", "small", SMALL_PICTURE_PIXELS)
        for model in week:
            first(model, "pictures", "image", "pictures", PICTURE_PIXELS)
        images.update(load_thumbnails([model["avatar"] for model in week[:1] if model.get("avatar")],
                                      self._cache_subdir("avatars"), size=AVATAR_PIXELS, **common))
        # The lead's next pictures, small, in the column beside its cover.
        gallery = [url for model in week[:1] for url in model.get("gallery") or []]
        if gallery:
            images.update(load_thumbnails(gallery, self._cache_subdir("small"), size=SMALL_PICTURE_PIXELS, **common))
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
        now = sources.local_now()
        _checkpoint(context)
        payload = self._payload(settings, now, allow_network=not cache_only, context=context)
        images = self._images(payload, allow_network=not cache_only, context=context)
        shown = payload["week"][:render.CARDS + 1] + payload["makes"] + payload["classics"]
        payload["chinese"] = self._translations(shown, device_config, allow_network=not cache_only,
                                                context=context, settings=settings)
        _checkpoint(context)
        try:
            image = render.render_page(self.get_dimensions(device_config), payload, settings, now, images)
        finally:
            for picture in images.values():
                picture.close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
