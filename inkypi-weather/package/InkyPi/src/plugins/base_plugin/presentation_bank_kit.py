"""Building blocks the plugins' presentation banks share.

Each bank (BacktotheDate, DailyArt, GCD covers, magazines, the newspaper, Pixiv, species,
Steam daily art) keeps its own document format, selection and migration logic, which differ
by plugin. These are the pieces they had each copied word for word: stable JSON hashes, UTC
timestamps, request and content ids, and the bounds checks on state documents and media,
with the plugin's own name passed in for the error messages.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image, ImageOps

HEX = frozenset("0123456789abcdef")


def json_hash(payload):
    """The SHA-256 of ``payload`` as canonical JSON (sorted keys, no spaces, ASCII)."""
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def instance_profile_fingerprint(base_fingerprint, instance_uuid):
    """Partition otherwise identical settings by trusted playlist instance."""
    return json_hash({"settings_fingerprint": base_fingerprint, "instance_uuid": instance_uuid})


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(value):
    """An ISO timestamp string as an aware UTC datetime; naive ones are taken as UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def valid_request_id(value):
    return isinstance(value, str) and len(value) == 32 and all(character in HEX for character in value)


def valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(character in HEX for character in value)


def bounded_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return max(int(minimum), min(int(maximum), number))


def bounded_text(value, limit):
    return str(value or "").strip()[:limit]


def bounded_date_buckets(value, *, limit, stamp, parse=parse_datetime, copy=False):
    """The newest ``limit`` date buckets of ``value``, ranked by each bucket's ``stamp`` time."""
    if not isinstance(value, dict):
        return {}
    candidates = {
        str(key): deepcopy(bucket) if copy else bucket
        for key, bucket in value.items()
        if isinstance(key, str) and isinstance(bucket, dict)
    }
    if len(candidates) <= limit:
        return candidates
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    ranked = sorted(candidates, key=lambda key: (parse(candidates[key].get(stamp)) or minimum, key))
    retained = set(ranked[-limit:])
    return {key: bucket for key, bucket in candidates.items() if key in retained}


def path_exists_no_follow(path, what):
    """Whether ``path`` exists, without following a link; ``what`` names it in the error."""
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError(f"{what} could not be inspected") from exc
    return True


def validate_state_payload_size(payload, *, validate_shape, max_bytes, label):
    """Reject state that cannot fit the durable JSON budget before opening a writer."""
    validate_shape(payload)
    try:
        encoded = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"{label} could not be encoded safely") from exc
    if len(encoded) > max_bytes:
        raise RuntimeError(f"{label} exceeds the size limit")
    return len(encoded)


def validate_media_dimensions(size, *, max_dimension, max_pixels, label):
    width, height = size
    if width <= 0 or height <= 0 or width > max_dimension or height > max_dimension or width * height > max_pixels:
        raise RuntimeError(f"{label} dimensions exceed the safety limit")


def normalize_media_image(image, *, validate_dimensions, label):
    """``image`` upright and in RGB, its size checked before and after the EXIF turn."""
    if not isinstance(image, Image.Image):
        raise RuntimeError(f"{label} is not an image")
    validate_dimensions(image.size)
    image = ImageOps.exif_transpose(image)
    validate_dimensions(image.size)
    return image.convert("RGB")


class SelectionBookkeeping:
    """The selection lookups every bank answers the same way."""

    def pending_for_request(self, profile, request_id):
        pending = profile.get("pending_selection")
        if isinstance(pending, dict) and pending.get("request_id") == request_id:
            return pending
        return None

    def _protected_record_keys(self, profile):
        protected = set()
        for name in ("current_selection", "pending_selection"):
            selection = profile.get(name)
            if isinstance(selection, dict):
                protected.update(selection.get("record_keys", []))
        return protected
