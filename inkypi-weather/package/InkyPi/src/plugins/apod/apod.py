"""
APOD Plugin for InkyPi
This plugin fetches the Astronomy Picture of the Day (APOD) from NASA's API
and displays it on the InkyPi device. It supports optional manual date selection or random dates.
For the API key, set `NASA_SECRET={API_KEY}` in your .env file.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from runtime.long_task_executor import current_instance_identity
from utils.atomic_file import atomic_write_json
from PIL import Image
from io import BytesIO
from utils.http_client import get_http_session
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Mapping
import hashlib
import json
import logging
import os
import random
import re
from random import randint
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

RANDOM_APOD_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class ApodRecord:
    selection_key: str
    requested_device_date: str
    date: str
    media_type: str
    title_en: str
    title_zh: str | None
    translation_state: Literal["pending", "live", "fresh_cache", "unavailable"]
    explanation: str
    copyright: str | None
    url: str | None
    hdurl: str | None
    image_url: str | None
    image_cache_key: str | None
    fetched_at_utc: datetime
    source_state: Literal["live", "fresh_cache", "stale_cache", "unavailable"]
    warning: str | None


@dataclass(frozen=True)
class InstancePaths:
    cache: Path
    data: Path
    media: Path
    identity_key: str


@dataclass(frozen=True)
class ApodSelection:
    device_day: str
    mode: Literal["today", "random", "custom"]
    requested_date: str
    fingerprint: str
    resolved_record_date: str | None = None
    record_cache_key: str | None = None
    provisional: bool = False


_SELECTION_FILENAME = "selection.json"
_RANDOM_APOD_START = date(2015, 1, 1)


def _instance_paths(
    plugin: "Apod", *, preview_namespace: str | None = None
) -> InstancePaths:
    """Return trusted instance storage, or an isolated explicit preview namespace."""

    if preview_namespace is not None:
        namespace = str(preview_namespace).strip()
        if not namespace:
            raise ValueError("preview namespace must be non-empty")
        identity_key = "preview-" + hashlib.sha256(
            namespace.encode("utf-8")
        ).hexdigest()
    else:
        identity = current_instance_identity()
        instance_uuid = getattr(identity, "instance_uuid", None)
        generation = getattr(identity, "structural_generation", None)
        if not isinstance(instance_uuid, str) or not instance_uuid.strip() or generation is None:
            raise RuntimeError("APOD requires a trusted runtime instance identity")
        identity_key = hashlib.sha256(
            f"{instance_uuid}:{generation}".encode("utf-8")
        ).hexdigest()

    cache = plugin.cache_dir(leaf=Path("instances") / identity_key)
    data = plugin.data_dir(leaf=Path("instances") / identity_key)
    media = cache / "media"
    media.mkdir(parents=True, exist_ok=True)
    return InstancePaths(cache=cache, data=data, media=media, identity_key=identity_key)


def _selection_fingerprint(
    *, mode: str, device_day: date, requested_date: str, custom_date: str
) -> str:
    """Hash the complete user-visible daily selection contract."""

    contract = {
        "mode": str(mode),
        "device_day": device_day.isoformat(),
        "requested_date": str(requested_date),
        "custom_date": str(custom_date),
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_selection(
    *,
    settings: Mapping[str, Any],
    device_day: date,
    paths: InstancePaths,
    rng: random.Random,
) -> ApodSelection:
    """Reuse one valid daily selection, or atomically persist a new one."""

    custom_date = str(settings.get("customDate") or "").strip()
    if settings.get("randomizeApod") == "true":
        mode: Literal["today", "random", "custom"] = "random"
        requested_date = ""
    elif custom_date:
        mode = "custom"
        requested_date = custom_date
    else:
        mode = "today"
        requested_date = device_day.isoformat()

    if mode == "random":
        fingerprint = _selection_fingerprint(
            mode=mode,
            device_day=device_day,
            requested_date=device_day.isoformat(),
            custom_date=custom_date,
        )
    else:
        fingerprint = _selection_fingerprint(
            mode=mode,
            device_day=device_day,
            requested_date=requested_date,
            custom_date=custom_date,
        )

    persisted = _read_selection(paths.data / _SELECTION_FILENAME)
    if persisted is not None and _selection_matches(persisted, device_day, mode, fingerprint):
        return persisted

    if mode == "random":
        latest = max(_RANDOM_APOD_START, device_day)
        selected_date = _RANDOM_APOD_START + timedelta(
            days=rng.randint(0, (latest - _RANDOM_APOD_START).days)
        )
        requested_date = selected_date.isoformat()
        provisional = True
    else:
        selected_date = date.fromisoformat(requested_date)
        provisional = False

    record_cache_key = hashlib.sha256(
        f"{paths.identity_key}:{selected_date.isoformat()}".encode("utf-8")
    ).hexdigest()
    selection = ApodSelection(
        device_day=device_day.isoformat(),
        mode=mode,
        requested_date=requested_date,
        fingerprint=fingerprint,
        resolved_record_date=selected_date.isoformat(),
        record_cache_key=record_cache_key,
        provisional=provisional,
    )
    _persist_selection(paths, selection)
    return selection


def _read_selection(path: Path) -> ApodSelection | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        mode = raw["mode"]
        if mode not in {"today", "random", "custom"}:
            return None
        selected = raw.get("selected_apod_date")
        record_key = raw.get("record_cache_key")
        if selected is not None and not isinstance(selected, str):
            return None
        if record_key is not None and not isinstance(record_key, str):
            return None
        return ApodSelection(
            device_day=str(raw["device_day"]),
            mode=mode,
            requested_date=str(raw["requested_date"]),
            fingerprint=str(raw["selection_fingerprint"]),
            resolved_record_date=selected,
            record_cache_key=record_key,
            provisional=bool(raw["provisional"]),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _selection_matches(
    selection: ApodSelection,
    device_day: date,
    mode: Literal["today", "random", "custom"],
    fingerprint: str,
) -> bool:
    return (
        selection.device_day == device_day.isoformat()
        and selection.mode == mode
        and selection.fingerprint == fingerprint
        and selection.resolved_record_date is not None
        and selection.record_cache_key is not None
    )


def _persist_selection(paths: InstancePaths, selection: ApodSelection) -> None:
    atomic_write_json(
        paths.data / _SELECTION_FILENAME,
        {
            "device_day": selection.device_day,
            "mode": selection.mode,
            "requested_date": selection.requested_date,
            "selected_apod_date": selection.resolved_record_date,
            "selection_fingerprint": selection.fingerprint,
            "provisional": selection.provisional,
            "record_cache_key": selection.record_cache_key,
        },
    )


def _resolved_selection(
    paths: InstancePaths, selection: ApodSelection, record_date: str
) -> ApodSelection:
    resolved = ApodSelection(
        device_day=selection.device_day,
        mode=selection.mode,
        requested_date=selection.requested_date,
        fingerprint=selection.fingerprint,
        resolved_record_date=record_date,
        record_cache_key=hashlib.sha256(
            f"{paths.identity_key}:{record_date}".encode("utf-8")
        ).hexdigest(),
        provisional=False,
    )
    _persist_selection(paths, resolved)
    return resolved


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
        paths = _instance_paths(self)
        selection = _resolve_selection(
            settings=settings,
            device_day=datetime.now().date(),
            paths=paths,
            rng=random.Random(),
        )
        dimensions = self.get_dimensions(device_config)
        image = None
        image_url = None

        if selection.mode == "random":
            data = None
            random_dates = [selection.resolved_record_date]
            if selection.provisional:
                random_dates.extend(
                    candidate
                    for candidate in self._random_apod_dates()
                    if candidate != selection.resolved_record_date
                )
            for random_date in random_dates[:RANDOM_APOD_MAX_ATTEMPTS]:
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
                    if selection.provisional:
                        selection = _resolved_selection(
                            paths,
                            selection,
                            str(candidate.get("date") or random_date),
                        )
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
            if selection.mode == "custom":
                params["date"] = selection.requested_date
                logger.info(f"Fetching APOD from custom date: {params['date']}")
            else:
                logger.info("Fetching today's APOD")

            data = self._fetch_apod(session, params)
            if data.get("media_type") != "image":
                logger.warning(
                    f"APOD media type is '{data.get('media_type')}', not 'image'"
                )
                if selection.mode == "custom":
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
