"""Durable, provider-payload-free checkpoints for isolated Sports rendering."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re

from utils.atomic_file import atomic_write_json, fsync_directory


SPORTS_CHECKPOINT_VERSION = 1
SPORTS_REGION_ORDER = ("esports", "football", "lower")
SPORTS_CHECKPOINT_MAX_PNG_BYTES = 1536 * 1024
SPORTS_CHECKPOINT_MAX_JSON_BYTES = 2200 * 1024
SPORTS_CHECKPOINT_TTL_SECONDS = 10 * 60
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE_VALUES = frozenset(
    {"live", "fresh_cache", "stale_cache", "local_fallback"}
)
_DOCUMENT_KEYS = frozenset(
    {
        "version",
        "fingerprint",
        "completed_regions",
        "base_png_b64",
        "panel_provenances",
        "render_now",
        "final_value",
    }
)
_FINAL_VALUE_KEYS = frozenset(
    {"composite_provenance", "skip_cache", "theme_mode"}
)


def _parse_aware_datetime(value):
    if type(value) is not str:
        raise ValueError("Sports checkpoint timestamp is invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Sports checkpoint timestamp must be timezone-aware")
    return parsed


def _checkpoint_is_fresh(checkpoint, now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        return False
    rendered_at = _parse_aware_datetime(checkpoint.render_now)
    age_seconds = (now - rendered_at).total_seconds()
    return 0 <= age_seconds <= SPORTS_CHECKPOINT_TTL_SECONDS


def _validated_final_value(completed_regions, final_value):
    is_complete = tuple(completed_regions) == SPORTS_REGION_ORDER
    if not is_complete:
        if final_value is not None:
            raise ValueError("Incomplete Sports checkpoint has a final value")
        return None
    if type(final_value) is not dict or frozenset(final_value) != _FINAL_VALUE_KEYS:
        raise ValueError("Complete Sports checkpoint final value is invalid")
    composite_provenance = final_value.get("composite_provenance")
    skip_cache = final_value.get("skip_cache")
    theme_mode = final_value.get("theme_mode")
    if (
        type(composite_provenance) is not str
        or composite_provenance not in _PROVENANCE_VALUES
    ):
        raise ValueError("Sports checkpoint composite provenance is invalid")
    if type(skip_cache) is not bool:
        raise ValueError("Sports checkpoint cache policy is invalid")
    if theme_mode is not None and (
        type(theme_mode) is not str or theme_mode not in ("day", "night")
    ):
        raise ValueError("Sports checkpoint theme is invalid")
    return {
        "composite_provenance": composite_provenance,
        "skip_cache": skip_cache,
        "theme_mode": theme_mode,
    }


@dataclass(frozen=True)
class SportsRegionCheckpoint:
    fingerprint: str
    completed_regions: tuple[str, ...]
    base_png: bytes
    panel_provenances: dict[str, str]
    render_now: str
    final_value: dict | None


class SportsRegionCheckpointStore:
    """Atomically publish the bounded output of completed Sports regions."""

    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def for_device(cls, device_config, instance_identity):
        """Resolve one non-identifying checkpoint path in the runtime cache."""

        cache_root = getattr(device_config, "cache_dir", None)
        runtime_paths = getattr(device_config, "runtime_paths", None)
        if cache_root is None:
            cache_root = getattr(runtime_paths, "cache_dir", None)
        if cache_root is None:
            cache_root = os.environ.get("INKYPI_CACHE_DIR")
        instance_uuid = getattr(instance_identity, "instance_uuid", None)
        if cache_root is None or not instance_uuid:
            return None
        instance_key = hashlib.sha256(
            str(instance_uuid).encode("utf-8")
        ).hexdigest()
        root = Path(cache_root).expanduser().absolute()
        return cls(
            root
            / "plugins"
            / "sports_dashboard"
            / "isolated-checkpoints"
            / f"{instance_key}.json"
        )

    def save(
        self,
        *,
        fingerprint,
        completed_regions,
        base_png,
        panel_provenances,
        render_now,
        final_value=None,
    ):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        completed_regions = tuple(completed_regions)
        final_value = _validated_final_value(completed_regions, final_value)
        render_now = str(render_now)
        _parse_aware_datetime(render_now)
        document = {
            "version": SPORTS_CHECKPOINT_VERSION,
            "fingerprint": str(fingerprint),
            "completed_regions": list(completed_regions),
            "base_png_b64": base64.b64encode(bytes(base_png)).decode("ascii"),
            "panel_provenances": dict(panel_provenances),
            "render_now": render_now,
            "final_value": final_value,
        }
        atomic_write_json(self.path, document, mode=0o600)

    def load(self, expected_fingerprint, *, now):
        try:
            if self.path.stat().st_size > SPORTS_CHECKPOINT_MAX_JSON_BYTES:
                raise ValueError("Sports checkpoint is oversized")
            document = json.loads(self.path.read_text(encoding="utf-8"))
            checkpoint = self._decode(document)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, ValueError, TypeError, binascii.Error):
            self.clear()
            return None
        if checkpoint.fingerprint != str(expected_fingerprint):
            self.clear()
            return None
        if not _checkpoint_is_fresh(checkpoint, now):
            self.clear()
            return None
        return checkpoint

    def clear(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        fsync_directory(self.path.parent)

    @staticmethod
    def _decode(document):
        if type(document) is not dict or frozenset(document) != _DOCUMENT_KEYS:
            raise ValueError("Sports checkpoint fields are invalid")
        if document.get("version") != SPORTS_CHECKPOINT_VERSION:
            raise ValueError("Sports checkpoint version is invalid")
        fingerprint = document.get("fingerprint")
        if type(fingerprint) is not str or _FINGERPRINT_RE.fullmatch(fingerprint) is None:
            raise ValueError("Sports checkpoint fingerprint is invalid")
        completed_regions = document.get("completed_regions")
        if type(completed_regions) is not list:
            raise ValueError("Sports checkpoint regions are invalid")
        completed_regions = tuple(completed_regions)
        if completed_regions != SPORTS_REGION_ORDER[: len(completed_regions)]:
            raise ValueError("Sports checkpoint regions are not a completed prefix")
        if not completed_regions:
            raise ValueError("Sports checkpoint does not contain completed work")

        encoded_png = document.get("base_png_b64")
        if type(encoded_png) is not str:
            raise ValueError("Sports checkpoint image is invalid")
        base_png = base64.b64decode(encoded_png.encode("ascii"), validate=True)
        if not base_png or len(base_png) > SPORTS_CHECKPOINT_MAX_PNG_BYTES:
            raise ValueError("Sports checkpoint image is oversized")

        panel_provenances = document.get("panel_provenances")
        if type(panel_provenances) is not dict:
            raise ValueError("Sports checkpoint provenances are invalid")
        if frozenset(panel_provenances) != frozenset(completed_regions):
            raise ValueError("Sports checkpoint provenance coverage is invalid")
        if any(
            type(value) is not str or value not in _PROVENANCE_VALUES
            for value in panel_provenances.values()
        ):
            raise ValueError("Sports checkpoint provenance value is invalid")

        render_now = document.get("render_now")
        _parse_aware_datetime(render_now)

        final_value = _validated_final_value(
            completed_regions,
            document.get("final_value"),
        )
        return SportsRegionCheckpoint(
            fingerprint=fingerprint,
            completed_regions=completed_regions,
            base_png=base_png,
            panel_provenances=dict(panel_provenances),
            render_now=render_now,
            final_value=final_value,
        )
