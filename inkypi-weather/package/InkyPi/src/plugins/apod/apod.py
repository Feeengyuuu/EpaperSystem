"""
APOD Plugin for InkyPi
This plugin fetches the Astronomy Picture of the Day (APOD) from NASA's API
and displays it on the InkyPi device. It supports optional manual date selection or random dates.
For the API key, set `NASA_SECRET={API_KEY}` in your .env file.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from PIL import Image
from io import BytesIO
from utils.http_client import get_http_session
import logging
import os
import re
from random import randint
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

RANDOM_APOD_MAX_ATTEMPTS = 5


class Apod(BasePlugin):
    NASA_LOGO_FILE = "nasa_logo.png"

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "NASA",
            "expected_key": "NASA_SECRET"
        }
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        logger.info("=== APOD Plugin: Starting image generation ===")

        api_key = device_config.load_env_key("NASA_SECRET")
        if not api_key:
            logger.error("NASA API Key not configured")
            raise RuntimeError("NASA API Key not configured.")

        session = get_http_session()
        params = {"api_key": api_key}
        randomize = settings.get("randomizeApod") == "true"
        dimensions = self.get_dimensions(device_config)
        image = None
        image_url = None

        if randomize:
            data = None
            for random_date in self._random_apod_dates():
                params["date"] = random_date
                logger.info(f"Fetching random APOD from date: {random_date}")
                candidate = self._fetch_apod(session, params)
                if candidate.get("media_type") != "image":
                    logger.warning(
                        f"APOD media type for {random_date} is "
                        f"'{candidate.get('media_type')}', not 'image'"
                    )
                    continue

                candidate_url = candidate.get("hdurl") or candidate.get("url")
                candidate_image = self.image_loader.from_url(
                    candidate_url,
                    dimensions,
                    timeout_ms=40000,
                )
                if candidate_image:
                    data = candidate
                    image_url = candidate_url
                    image = candidate_image
                    break
                logger.warning(
                    "Could not load random APOD image for %s; trying another date",
                    random_date,
                )

            if image is None:
                raise RuntimeError(
                    "No usable APOD image found after "
                    f"{RANDOM_APOD_MAX_ATTEMPTS} random dates."
                )
        else:
            if settings.get("customDate"):
                params["date"] = settings["customDate"]
                logger.info(f"Fetching APOD from custom date: {params['date']}")
            else:
                logger.info("Fetching today's APOD")

            data = self._fetch_apod(session, params)
            if data.get("media_type") != "image":
                logger.warning(
                    f"APOD media type is '{data.get('media_type')}', not 'image'"
                )
                if settings.get("customDate"):
                    raise RuntimeError(
                        "APOD is not an image for the requested date."
                    )
                raise RuntimeError("APOD is not an image today.")
            image_url = data.get("hdurl") or data.get("url")
            image = self.image_loader.from_url(
                image_url,
                dimensions,
                timeout_ms=40000,
            )
            if not image:
                logger.error("Failed to load APOD image")
                raise RuntimeError("Failed to load APOD image.")

        logger.info(f"APOD image URL: {image_url}")
        logger.debug(f"Using {'HD URL' if data.get('hdurl') else 'standard URL'}")

        image = self._overlay_nasa_logo(image)
        self._write_apod_context(data, image_url)

        logger.info("=== APOD Plugin: Image generation complete ===")
        return image

    def _fetch_apod(self, session, params):
        logger.debug("Requesting NASA APOD API...")
        response = session.get(
            "https://api.nasa.gov/planetary/apod",
            params=params,
            timeout=10,
        )

        if response.status_code != 200:
            logger.error(f"NASA API error (status {response.status_code})")
            raise RuntimeError("Failed to retrieve NASA APOD.")

        data = response.json()
        logger.debug(
            f"APOD API response received: {data.get('title', 'No title')}"
        )
        return data

    @staticmethod
    def _random_apod_dates():
        start = datetime(2015, 1, 1)
        end = datetime.today()
        day_count = (end - start).days + 1
        attempt_count = min(RANDOM_APOD_MAX_ATTEMPTS, day_count)
        used_offsets = set()

        for _ in range(attempt_count):
            random_offset = randint(0, day_count - 1)
            for step in range(day_count):
                offset = (random_offset + step) % day_count
                if offset in used_offsets:
                    continue
                used_offsets.add(offset)
                yield (start + timedelta(days=offset)).strftime("%Y-%m-%d")
                break

    def _write_apod_context(self, data, image_url):
        title = str(data.get("title") or "Astronomy Picture of the Day").strip()
        date_text = str(data.get("date") or "").strip()
        explanation = re.sub(r"\s+", " ", str(data.get("explanation") or "")).strip()
        summary = f"NASA APOD: {title}"
        if date_text:
            summary += f" ({date_text})"

        facts = []
        if date_text:
            facts.append({"label": "date", "value": date_text})
        if data.get("copyright"):
            facts.append({"label": "credit", "value": str(data.get("copyright"))[:80]})

        write_context(
            "apod",
            {
                "kind": "space_photo",
                "source": "NASA APOD",
                "summary": summary[:180],
                "facts": facts,
                "items": [{
                    "title": title[:120],
                    "date": date_text,
                    "summary": explanation[:160],
                    "image_url": image_url,
                }],
            },
            generated_at=datetime.now(),
            ttl_seconds=24 * 60 * 60,
        )

    def _overlay_nasa_logo(self, image):
        logo_path = self.get_plugin_dir(self.NASA_LOGO_FILE)
        if not os.path.exists(logo_path):
            logger.warning(f"NASA logo asset not found: {logo_path}")
            return image

        try:
            canvas = image.convert("RGBA")
            logo = Image.open(logo_path).convert("RGBA")
            resample = getattr(Image, "Resampling", Image).LANCZOS

            target_width = min(96, max(64, int(canvas.width * 0.105)))
            target_height = max(1, int(target_width * logo.height / logo.width))
            logo = logo.resize((target_width, target_height), resample)

            margin = max(12, int(min(canvas.width, canvas.height) * 0.035))
            position = (margin, canvas.height - logo.height - margin)
            canvas.alpha_composite(logo, position)
            return canvas.convert("RGB")
        except Exception as e:
            logger.warning(f"Failed to overlay NASA logo: {e}")
            return image
