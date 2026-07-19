from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urljoin

from PIL import Image, ImageOps

from security.ssrf import get_ssrf_policy
from utils.http_client import get_http_session
from utils.safe_image import ImageLimits, safe_open_image, safe_open_image_response


logger = logging.getLogger(__name__)

PHOTO_SUFFIX = ".png"
PHOTO_REDIRECTS = 2
PHOTO_TIMEOUT = (4, 12)
PHOTO_HEADERS = {
    "User-Agent": "InkyPi OrbitalSignal/1.0",
    "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8,*/*;q=0.5",
}
PHOTO_LIMITS = ImageLimits(
    max_bytes=8 * 1024 * 1024,
    max_width=8192,
    max_height=8192,
    max_pixels=16_000_000,
)


@dataclass(frozen=True)
class PhotoCandidate:
    url: str
    credit: str
    license: str
    source: str


@dataclass(frozen=True)
class CachedLaunchPhoto:
    image: Image.Image
    cache_key: str
    credit: str
    license: str
    source: str


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else ""


def photo_candidates(launch):
    row = launch if isinstance(launch, dict) else {}
    candidates = []
    seen = set()
    for prefix in ("", "fallback_"):
        credit = _text(row.get(f"{prefix}image_credit"))
        license_name = _text(row.get(f"{prefix}image_license"))
        source = _text(row.get(f"{prefix}image_source"))
        for field in ("image_url", "thumbnail_url"):
            url = _text(row.get(f"{prefix}{field}"))
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(PhotoCandidate(url, credit, license_name, source))
    return candidates


def photo_cache_key(url):
    return hashlib.sha256(str(url or "").encode("utf-8")).hexdigest()


def _cached_photo(namespace, candidate):
    key = photo_cache_key(candidate.url)
    try:
        payload = namespace.get_bytes(key, suffix=PHOTO_SUFFIX)
        if not payload:
            return None
        decoded = safe_open_image(payload, limits=PHOTO_LIMITS).convert("RGB")
        return CachedLaunchPhoto(
            decoded,
            key,
            candidate.credit,
            candidate.license,
            candidate.source,
        )
    except Exception as exc:
        logger.warning(
            "Orbital Signal cached launch photo rejected (%s, %s)",
            candidate.source or "unknown",
            type(exc).__name__,
        )
        return None


def load_cached_photo(namespace, cache_key):
    key = _text(cache_key)
    if not key:
        return None
    try:
        payload = namespace.get_bytes(key, suffix=PHOTO_SUFFIX)
        if not payload:
            return None
        return safe_open_image(payload, limits=PHOTO_LIMITS).convert("RGB")
    except Exception as exc:
        logger.warning(
            "Orbital Signal launch photo cache rejected: %s",
            type(exc).__name__,
        )
        return None


def _download_candidate(candidate, session):
    policy = get_ssrf_policy()
    current_url = candidate.url
    for redirect_count in range(PHOTO_REDIRECTS + 1):
        approved = policy.resolve_and_validate(current_url)
        response = session.get(
            approved.normalized_url,
            headers=PHOTO_HEADERS,
            timeout=PHOTO_TIMEOUT,
            stream=True,
            allow_redirects=False,
        )
        try:
            response_url = str(getattr(response, "url", approved.normalized_url))
            final_hop = policy.resolve_and_validate(response_url)
            status = int(response.status_code)
            if 300 <= status < 400:
                if redirect_count >= PHOTO_REDIRECTS:
                    raise RuntimeError("launch photo redirect limit exceeded")
                location = str(response.headers.get("Location") or "").strip()
                if not location:
                    raise RuntimeError("launch photo redirect has no location")
                next_url = urljoin(final_hop.normalized_url, location)
                current_url = policy.resolve_and_validate(next_url).normalized_url
                continue
            if not 200 <= status < 300:
                raise RuntimeError(f"launch photo request returned HTTP {status}")
            return safe_open_image_response(
                response,
                limits=PHOTO_LIMITS,
                draft_size=(339, 741),
            ).convert("RGB")
        finally:
            response.close()
    raise RuntimeError("launch photo redirect limit exceeded")


def _encode_png(image):
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _crop_score(image, center_distance):
    sample = image.convert("L").resize((48, 96), Image.Resampling.BILINEAR)
    values = list(sample.get_flattened_data())
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    active_rows = []
    for row in range(sample.height):
        offset = row * sample.width
        strong_edges = sum(
            abs(values[offset + column] - values[offset + column - 1]) >= 24
            for column in range(1, sample.width)
        )
        active_rows.append(strong_edges >= 2)
    longest_run = 0
    current_run = 0
    for active in active_rows:
        current_run = current_run + 1 if active else 0
        longest_run = max(longest_run, current_run)
    active_ratio = sum(active_rows) / len(active_rows)
    continuity_ratio = longest_run / len(active_rows)
    return (
        math.sqrt(variance)
        + active_ratio * 55.0
        + continuity_ratio * 80.0
        - center_distance * 4.0
    )


def rocket_preserving_crop(image, size):
    target_width, target_height = (int(size[0]), int(size[1]))
    if target_width <= 0 or target_height <= 0:
        raise ValueError("launch photo crop size must be positive")
    source = ImageOps.exif_transpose(image).convert("RGB")
    if source.width <= 0 or source.height <= 0:
        raise ValueError("launch photo source must be non-empty")

    scale = max(target_width / source.width, target_height / source.height)
    resized = source.resize(
        (
            max(target_width, int(math.ceil(source.width * scale))),
            max(target_height, int(math.ceil(source.height * scale))),
        ),
        Image.Resampling.LANCZOS,
    )
    max_x = max(0, resized.width - target_width)
    max_y = max(0, resized.height - target_height)
    y = max_y // 2
    center_x = max_x // 2
    x_positions = []
    for candidate_x in (center_x, 0, max_x):
        if candidate_x not in x_positions:
            x_positions.append(candidate_x)

    scored = []
    for candidate_x in x_positions:
        crop = resized.crop(
            (
                candidate_x,
                y,
                candidate_x + target_width,
                y + target_height,
            )
        )
        center_distance = abs(candidate_x - center_x) / max(1, max_x)
        scored.append((_crop_score(crop, center_distance), candidate_x, crop))
    best_score, _best_x, best_crop = max(scored, key=lambda item: (item[0], -item[1]))
    if best_score < 5.0:
        return resized.crop(
            (center_x, y, center_x + target_width, y + target_height)
        ).convert("RGB")
    return best_crop.convert("RGB")


def load_or_acquire_photo(launch, namespace, *, allow_network, session=None):
    candidates = photo_candidates(launch)
    for candidate in candidates:
        cached = _cached_photo(namespace, candidate)
        if cached is not None:
            return cached
    if not allow_network:
        return None
    active_session = session or get_http_session()
    for candidate in candidates:
        try:
            image = _download_candidate(candidate, active_session)
            key = photo_cache_key(candidate.url)
            namespace.put_bytes(key, _encode_png(image), suffix=PHOTO_SUFFIX)
            return CachedLaunchPhoto(
                image,
                key,
                candidate.credit,
                candidate.license,
                candidate.source,
            )
        except Exception as exc:
            logger.warning(
                "Orbital Signal launch photo candidate rejected (%s, %s)",
                candidate.source or "unknown",
                type(exc).__name__,
            )
    return None
