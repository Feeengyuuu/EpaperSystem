from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageDraw, ImageFont
import logging
import random
from datetime import datetime

import pytz

from .comic_parser import COMICS, get_panel
from plugins.context_cache import write_context
from utils.app_utils import get_font

logger = logging.getLogger(__name__)

class Comic(BasePlugin):
    ROTATION_MODE_KEY = "rotationMode"
    ROTATION_DATE_KEY = "comic_rotation_date"
    ROTATION_SELECTED_KEY = "comic_rotation_selected"
    ROTATION_QUEUE_KEY = "comic_rotation_queue"
    ROTATION_POOL_KEY = "comic_rotation_pool"
    ROTATION_LAST_KEY = "comic_rotation_last"

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['comics'] = list(COMICS)
        return template_params

    def generate_image(self, settings, device_config):
        logger.info("=== Comic Plugin: Starting image generation ===")

        is_caption = settings.get("titleCaption", "true") == "true"
        caption_font_size = settings.get("fontSize") or 14

        logger.debug(f"Settings: show_caption={is_caption}, font_size={caption_font_size}")

        logger.debug("Parsing comic panel...")
        comic, comic_panel = self._get_comic_panel(settings, device_config)
        logger.info(f"Fetching comic: {comic}")
        logger.info(f"Comic panel URL: {comic_panel.get('image_url', 'Unknown')}")

        if comic_panel.get("title"):
            logger.debug(f"Comic title: {comic_panel['title']}")
        if comic_panel.get("caption"):
            logger.debug(f"Comic caption: {comic_panel['caption']}")

        dimensions = self.get_dimensions(device_config)

        width, height = dimensions

        logger.debug("Composing comic image with captions...")
        image = self._compose_image(comic_panel, is_caption, caption_font_size, width, height)
        self._write_comic_context(comic, comic_panel, device_config)

        logger.info("=== Comic Plugin: Image generation complete ===")
        return image

    def _write_comic_context(self, comic, comic_panel, device_config):
        title = str(comic_panel.get("title") or comic or "Comic").strip()
        caption = str(comic_panel.get("caption") or "").strip()
        summary = f"Comic: {comic}"
        if title:
            summary += f" - {title}"

        write_context(
            "comic",
            {
                "kind": "comic_panel",
                "source": "Comic",
                "summary": summary[:180],
                "facts": [
                    {"label": "comic", "value": str(comic)[:80]},
                    {"label": "title", "value": title[:100]},
                ],
                "items": [{
                    "name": comic,
                    "title": title[:120],
                    "caption": caption[:140],
                    "image_url": comic_panel.get("image_url"),
                }],
            },
            generated_at=self._context_now(device_config),
            ttl_seconds=24 * 60 * 60,
        )

    def _context_now(self, device_config):
        timezone_name = device_config.get_config("timezone", default=None)
        if timezone_name:
            try:
                return datetime.now(pytz.timezone(timezone_name))
            except Exception:
                pass
        return datetime.now()

    def _get_comic_panel(self, settings, device_config):
        rotation_mode = self._get_rotation_mode(settings)
        if rotation_mode == "single":
            comic = settings.get("comic")
            if not comic or comic not in COMICS:
                logger.error(f"Invalid comic: {comic}")
                raise RuntimeError("Invalid comic provided.")
            return comic, get_panel(comic)

        date_key = self._current_date_key(device_config)
        pool, queue, candidates = self._daily_comic_candidates(settings, date_key)
        errors = []
        failed_comics = []

        for comic in candidates:
            try:
                panel = get_panel(comic)
                self._commit_daily_comic_selection(settings, date_key, pool, queue, comic, failed_comics)
                return comic, panel
            except Exception as exc:
                failed_comics.append(comic)
                errors.append(f"{comic}: {exc}")
                logger.warning(f"Failed to fetch daily comic candidate '{comic}': {exc}")

        raise RuntimeError("Failed to retrieve a daily comic. " + "; ".join(errors))

    def _get_rotation_mode(self, settings):
        mode = settings.get(self.ROTATION_MODE_KEY)
        if mode in {"single", "daily"}:
            return mode
        return "daily"

    def _daily_comic_candidates(self, settings, date_key):
        pool = list(COMICS)
        previous_pool = self._normalize_list(settings.get(self.ROTATION_POOL_KEY))
        queue = [
            comic
            for comic in self._normalize_list(settings.get(self.ROTATION_QUEUE_KEY))
            if comic in pool
        ]

        selected_today = settings.get(self.ROTATION_SELECTED_KEY)
        if settings.get(self.ROTATION_DATE_KEY) == date_key and selected_today in pool:
            candidates = [selected_today] + [comic for comic in pool if comic != selected_today]
            return pool, queue, candidates

        if previous_pool != pool:
            queue = []

        if not queue:
            queue = list(pool)
            random.shuffle(queue)
            self._avoid_immediate_repeat(queue, settings.get(self.ROTATION_LAST_KEY))

        candidates = list(queue) + [comic for comic in pool if comic not in queue]
        return pool, queue, candidates

    def _commit_daily_comic_selection(self, settings, date_key, pool, queue, selected_comic, failed_comics=None):
        failed_comics = set(failed_comics or [])
        settings[self.ROTATION_DATE_KEY] = date_key
        settings[self.ROTATION_SELECTED_KEY] = selected_comic
        settings[self.ROTATION_POOL_KEY] = list(pool)
        settings[self.ROTATION_QUEUE_KEY] = [
            comic
            for comic in queue
            if comic != selected_comic and comic not in failed_comics
        ]
        settings[self.ROTATION_LAST_KEY] = selected_comic

    def _avoid_immediate_repeat(self, queue, last_selected):
        if not last_selected or len(queue) < 2 or queue[0] != last_selected:
            return

        for index, comic in enumerate(queue[1:], start=1):
            if comic != last_selected:
                queue[0], queue[index] = queue[index], queue[0]
                return

    def _normalize_list(self, value):
        if not value:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _current_date_key(self, device_config):
        timezone_name = device_config.get_config("timezone", default=None)
        if timezone_name:
            try:
                return datetime.now(pytz.timezone(timezone_name)).date().isoformat()
            except Exception:
                logger.warning(f"Invalid device timezone for comic rotation: {timezone_name}")

        return datetime.now().date().isoformat()

    def _compose_image(self, comic_panel, is_caption, caption_font_size, width, height):
        # Use adaptive loader for memory-efficient processing
        # Note: Comic images are usually reasonable size, but still benefit from optimization
        img = self.image_loader.from_url(
            comic_panel["image_url"],
            dimensions=(width, height),
            resize=False  # We'll handle custom sizing below
        )

        if not img:
            raise RuntimeError("Failed to load comic image")

        with img:
            background = Image.new("RGB", (width, height), "white")
            font = get_font("Jost", font_size=int(caption_font_size))
            draw = ImageDraw.Draw(background)
            top_padding, bottom_padding = 0, 0

            if is_caption:
                if comic_panel["title"]:
                    lines, wrapped_text = self._wrap_text(comic_panel["title"], font, width)
                    draw.multiline_text((width // 2, 0), wrapped_text, font=font, fill="black", anchor="ma")
                    top_padding = font.getbbox(wrapped_text)[3] * lines + 1

                if comic_panel["caption"]:
                    lines, wrapped_text = self._wrap_text(comic_panel["caption"], font, width)
                    draw.multiline_text((width // 2, height), wrapped_text, font=font, fill="black", anchor="md")
                    bottom_padding = font.getbbox(wrapped_text)[3] * lines + 1

            scale = min(width / img.width, (height - top_padding - bottom_padding) / img.height)
            new_size = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new_size, Image.LANCZOS)

            y_middle = (height - img.height) // 2
            y_top_bound = top_padding
            y_bottom_bound = height - img.height - bottom_padding

            x = (width - img.width) // 2
            y = y = min(max(y_middle, y_top_bound), y_bottom_bound)

            background.paste(img, (x, y))

            return background

    def _wrap_text(self, text, font, width):
        lines = []
        words = text.split()[::-1]

        while words:
            line = words.pop()
            while words and font.getbbox(line + ' ' + words[-1])[2] < width:
                line += ' ' + words.pop()
            lines.append(line)

        return len(lines), '\n'.join(lines)
