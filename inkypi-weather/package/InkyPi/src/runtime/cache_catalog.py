"""Authoritative, exact-revision display-cache discovery and validation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import threading

from PIL import Image


_THEME_MODES = {None, "day", "night"}
_CACHE_NAME_RE = re.compile(
    r"^(?P<prefix>[0-9a-f]{32})-"
    r"(?P<generation>[1-9][0-9]*)-"
    r"(?P<revision>[1-9][0-9]*)"
    r"(?:-(?P<theme>day|night))?\.png$"
)


@dataclass(frozen=True)
class CachePathIdentity:
    uuid_hash_prefix: str
    structural_generation: int
    settings_revision: int
    theme_mode: str | None


@dataclass(frozen=True)
class DisplayCacheCandidate:
    instance_uuid: str
    structural_generation: int
    settings_revision: int
    theme_mode: str | None
    cache_path: str
    promoted_at: str | None


def _positive_int(value, field_name) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _instance_uuid(value) -> str:
    if not isinstance(value, str):
        raise TypeError("instance_uuid must be a string")
    value = value.strip()
    if not value:
        raise ValueError("instance_uuid must not be empty")
    return value


def _theme_mode(value) -> str | None:
    if value not in _THEME_MODES:
        raise ValueError("theme_mode must be day, night, or None")
    return value


def authoritative_cache_path(
    cache_root,
    instance_uuid,
    structural_generation,
    settings_revision,
    theme_mode=None,
) -> str:
    """Derive the sole authoritative filename for one immutable revision."""

    instance_uuid = _instance_uuid(instance_uuid)
    structural_generation = _positive_int(
        structural_generation,
        "structural_generation",
    )
    settings_revision = _positive_int(settings_revision, "settings_revision")
    theme_mode = _theme_mode(theme_mode)
    prefix = hashlib.sha256(instance_uuid.encode("utf-8")).hexdigest()[:32]
    suffix = "" if theme_mode is None else f"-{theme_mode}"
    filename = (
        f"{prefix}-{structural_generation}-{settings_revision}{suffix}.png"
    )
    return str(Path(cache_root) / filename)


def parse_authoritative_cache_path(cache_root, cache_path):
    """Parse a direct child cache path, rejecting aliases and path traversal."""

    root = Path(os.path.abspath(os.fspath(cache_root)))
    path = Path(os.path.abspath(os.fspath(cache_path)))
    if path.parent != root or path.is_symlink():
        return None
    match = _CACHE_NAME_RE.fullmatch(path.name)
    if match is None:
        return None
    return CachePathIdentity(
        uuid_hash_prefix=match.group("prefix"),
        structural_generation=int(match.group("generation")),
        settings_revision=int(match.group("revision")),
        theme_mode=match.group("theme"),
    )


class CacheCatalog:
    """Resolve and decode-check displayable caches without plugin execution."""

    def __init__(self, cache_root):
        self.cache_root = Path(os.path.abspath(os.fspath(cache_root)))
        self._validation_cache: dict[tuple[str, int, int], bool] = {}
        self._validation_lock = threading.Lock()

    def resolve(
        self,
        instance,
        resolved_theme_mode,
        runtime_instance_state,
    ) -> DisplayCacheCandidate | None:
        resolved_theme_mode = _theme_mode(resolved_theme_mode)
        try:
            instance_uuid = _instance_uuid(instance.instance_uuid)
            generation = _positive_int(
                instance.structural_generation,
                "structural_generation",
            )
            revision = _positive_int(
                instance.settings_revision,
                "settings_revision",
            )
        except (AttributeError, TypeError, ValueError):
            return None

        last_good = getattr(runtime_instance_state, "last_good_cache", None)
        choices: list[tuple[str | None, str | None]] = []
        current_promoted_at = None
        if (
            last_good is not None
            and last_good.structural_generation == generation
            and last_good.settings_revision == revision
            and last_good.theme_mode == resolved_theme_mode
        ):
            current_promoted_at = last_good.promoted_at
        choices.append((resolved_theme_mode, current_promoted_at))

        if (
            last_good is not None
            and last_good.structural_generation == generation
            and last_good.settings_revision == revision
        ):
            choices.append((last_good.theme_mode, last_good.promoted_at))

        # An unsuffixed exact cache is the only migration cache eligible for
        # display.  A legacy success timestamp carries no exact identity and is
        # deliberately not promoted to LastGoodCacheState here.
        choices.append((None, None))

        seen_paths = set()
        for theme_mode, promoted_at in choices:
            cache_path = authoritative_cache_path(
                self.cache_root,
                instance_uuid,
                generation,
                revision,
                theme_mode,
            )
            normalized_path = os.path.normcase(os.path.abspath(cache_path))
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            candidate = DisplayCacheCandidate(
                instance_uuid=instance_uuid,
                structural_generation=generation,
                settings_revision=revision,
                theme_mode=theme_mode,
                cache_path=cache_path,
                promoted_at=promoted_at,
            )
            if self.validate(candidate):
                return candidate
        return None

    def validate(self, candidate: DisplayCacheCandidate) -> bool:
        if not isinstance(candidate, DisplayCacheCandidate):
            return False
        try:
            expected = authoritative_cache_path(
                self.cache_root,
                candidate.instance_uuid,
                candidate.structural_generation,
                candidate.settings_revision,
                candidate.theme_mode,
            )
        except (TypeError, ValueError):
            return False

        path = Path(os.path.abspath(os.fspath(candidate.cache_path)))
        expected_path = Path(os.path.abspath(expected))
        if os.path.normcase(str(path)) != os.path.normcase(str(expected_path)):
            return False
        identity = parse_authoritative_cache_path(self.cache_root, path)
        if identity is None:
            return False
        expected_prefix = hashlib.sha256(
            candidate.instance_uuid.encode("utf-8")
        ).hexdigest()[:32]
        if identity != CachePathIdentity(
            uuid_hash_prefix=expected_prefix,
            structural_generation=candidate.structural_generation,
            settings_revision=candidate.settings_revision,
            theme_mode=candidate.theme_mode,
        ):
            return False

        try:
            if path.is_symlink() or not path.is_file():
                return False
            if path.resolve(strict=True).parent != self.cache_root.resolve(strict=False):
                return False
            stat = path.stat()
        except OSError:
            return False

        cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self._validation_lock:
            cached = self._validation_cache.get(cache_key)
        if cached is not None:
            return cached

        valid = False
        try:
            with Image.open(path) as source:
                copied = source.copy()
            try:
                copied.load()
                valid = True
            finally:
                copied.close()
        except Exception:
            valid = False

        try:
            final_stat = path.stat()
        except OSError:
            return False
        if (final_stat.st_mtime_ns, final_stat.st_size) != (
            stat.st_mtime_ns,
            stat.st_size,
        ):
            return False

        with self._validation_lock:
            self._validation_cache = {
                key: value
                for key, value in self._validation_cache.items()
                if key[0] != str(path)
            }
            self._validation_cache[cache_key] = valid
        return valid

    def invalidate(self, candidate: DisplayCacheCandidate) -> None:
        try:
            path = str(Path(os.path.abspath(os.fspath(candidate.cache_path))))
        except (AttributeError, TypeError, ValueError):
            return
        with self._validation_lock:
            self._validation_cache = {
                key: value
                for key, value in self._validation_cache.items()
                if key[0] != path
            }
