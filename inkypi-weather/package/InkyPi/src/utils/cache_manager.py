"""Path-safe, budgeted disk and image caches for plugin-owned data."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass
import itertools
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import threading
import time
from typing import Any
import weakref


logger = logging.getLogger(__name__)

DEFAULT_CACHE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
DEFAULT_CACHE_MAX_FILES = 256
DEFAULT_CACHE_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_GLOBAL_CACHE_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_IMAGE_CACHE_MAX_ENTRIES = 128
DEFAULT_IMAGE_CACHE_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_GLOBAL_IMAGE_CACHE_MAX_BYTES = 32 * 1024 * 1024
TEMP_MAX_AGE_SECONDS = 60 * 60
MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,16}$")
_GLOBAL_MANAGER = None
_GLOBAL_MANAGER_LOCK = threading.Lock()
_AUXILIARY_MANAGERS: OrderedDict[str, CacheManager] = OrderedDict()
_AUXILIARY_MANAGER_LIMIT = 128


class CacheError(RuntimeError):
    pass


class CachePathError(CacheError):
    pass


class CacheObjectTooLarge(CacheError):
    pass


@dataclass(frozen=True)
class CacheBudget:
    max_age_seconds: float = DEFAULT_CACHE_MAX_AGE_SECONDS
    max_files: int = DEFAULT_CACHE_MAX_FILES
    max_bytes: int = DEFAULT_CACHE_MAX_BYTES

    def __post_init__(self):
        try:
            age = float(self.max_age_seconds)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("max_age_seconds must be finite and non-negative") from error
        if not math.isfinite(age) or age < 0:
            raise ValueError("max_age_seconds must be finite and non-negative")
        if isinstance(self.max_files, bool) or not isinstance(self.max_files, int):
            raise ValueError("max_files must be a positive integer")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int):
            raise ValueError("max_bytes must be a positive integer")
        if self.max_files <= 0 or self.max_bytes <= 0:
            raise ValueError("cache file and byte budgets must be positive")
        object.__setattr__(self, "max_age_seconds", age)


DEFAULT_CACHE_BUDGET = CacheBudget()


@dataclass(frozen=True)
class CacheStatus:
    files: int
    bytes: int
    oldest_age_seconds: float
    evicted_total: int
    rejected_total: int


@dataclass(frozen=True)
class _FileRecord:
    path: Path
    size: int
    last_used: float


def _cache_root(source) -> Path:
    if hasattr(source, "cache_dir"):
        return Path(source.cache_dir).expanduser() / "plugins"
    return Path(source).expanduser()


def _normalized_parts(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        raise CachePathError(f"{label} must be a non-empty relative path")
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CachePathError(f"{label} must remain inside the managed cache")
    if not path.parts or any(not _SAFE_COMPONENT.fullmatch(part) for part in path.parts):
        raise CachePathError(f"{label} contains an unsafe path component")
    return tuple(path.parts)


def _normalized_suffix(value: Any) -> str:
    if value in {None, ""}:
        return ""
    if not isinstance(value, str) or not _SAFE_SUFFIX.fullmatch(value):
        raise CachePathError("cache suffix must be a simple extension")
    return value.lower()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _assert_no_symlink_between(root: Path, target: Path) -> None:
    root_absolute = root.absolute()
    target_absolute = target.absolute()
    if not _is_relative_to(target_absolute, root_absolute):
        raise CachePathError("cache path escaped its managed root")
    current = root_absolute
    if current.exists() and current.is_symlink():
        raise CachePathError("managed cache roots cannot be symlinks")
    for part in target_absolute.relative_to(root_absolute).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise CachePathError("cache paths cannot traverse symlinks")


def _assert_resolved_within(root: Path, target: Path) -> None:
    _assert_no_symlink_between(root, target)
    root_resolved = root.resolve(strict=True)
    target_resolved = target.resolve(strict=False)
    if not _is_relative_to(target_resolved, root_resolved):
        raise CachePathError("cache path escaped its managed root")


def _regular_files(root: Path) -> list[_FileRecord]:
    records = []
    if not root.exists() or root.is_symlink():
        return records
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        directory_names[:] = [
            name
            for name in directory_names
            if not (directory_path / name).is_symlink()
        ]
        for name in file_names:
            path = directory_path / name
            if name.endswith(".tmp") or path.is_symlink():
                continue
            try:
                info = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            records.append(
                _FileRecord(
                    path,
                    int(info.st_size),
                    max(float(info.st_atime), float(info.st_mtime)),
                )
            )
    return records


def _temporary_files(root: Path) -> list[_FileRecord]:
    records = []
    if not root.exists() or root.is_symlink():
        return records
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        directory_names[:] = [
            name
            for name in directory_names
            if not (directory_path / name).is_symlink()
        ]
        for name in file_names:
            if not name.endswith(".tmp"):
                continue
            path = directory_path / name
            if path.is_symlink():
                continue
            try:
                info = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                records.append(
                    _FileRecord(path, int(info.st_size), float(info.st_mtime))
                )
    return records


class CacheManager:
    """Own one cache root and enforce namespace plus aggregate disk budgets."""

    def __init__(
        self,
        root_or_runtime_paths,
        *,
        global_max_bytes: int = DEFAULT_GLOBAL_CACHE_MAX_BYTES,
        health_publisher=None,
        clock=time.time,
    ):
        if isinstance(global_max_bytes, bool) or not isinstance(global_max_bytes, int):
            raise ValueError("global_max_bytes must be a positive integer")
        if global_max_bytes <= 0:
            raise ValueError("global_max_bytes must be a positive integer")
        self.root = _cache_root(root_or_runtime_paths)
        if self.root.exists() and self.root.is_symlink():
            raise CachePathError("managed cache roots cannot be symlinks")
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise CachePathError("managed cache roots cannot be symlinks")
        self._root_resolved = self.root.resolve(strict=True)
        self.global_max_bytes = global_max_bytes
        self.health_publisher = health_publisher
        self._clock = clock
        self._lock = threading.RLock()
        self._namespaces: dict[str, CacheNamespace] = {}
        self._records: dict[Path, _FileRecord] = {}
        self._evicted_total = 0
        self._rejected_total = 0
        self._last_maintenance = float("-inf")
        self.maintenance(force=True)

    def namespace(
        self,
        name: str,
        budget: CacheBudget = DEFAULT_CACHE_BUDGET,
    ) -> CacheNamespace:
        parts = _normalized_parts(name, label="cache namespace")
        canonical = "/".join(parts)
        with self._lock:
            existing = self._namespaces.get(canonical)
            if existing is not None:
                if existing.budget != budget:
                    raise ValueError(
                        f"cache namespace '{canonical}' already has a different budget"
                    )
                return existing
            root = self.root.joinpath(*parts)
            _assert_no_symlink_between(self.root, root)
            root.mkdir(parents=True, exist_ok=True)
            _assert_resolved_within(self.root, root)
            namespace = CacheNamespace(self, canonical, root, budget)
            self._namespaces[canonical] = namespace
            namespace._reload_index_locked()
            namespace._maintenance_locked()
            self._enforce_global_locked()
            self._publish_locked()
            return namespace

    def _root_namespace(
        self,
        budget: CacheBudget = DEFAULT_CACHE_BUDGET,
    ) -> CacheNamespace:
        with self._lock:
            existing = self._namespaces.get(".")
            if existing is not None:
                if existing.budget != budget:
                    raise ValueError("managed directory already has a different budget")
                return existing
            namespace = CacheNamespace(self, ".", self.root, budget)
            self._namespaces["."] = namespace
            namespace._reload_index_locked()
            namespace._maintenance_locked()
            self._enforce_global_locked()
            self._publish_locked()
            return namespace

    def maintenance(self, *, force=False) -> CacheStatus:
        with self._lock:
            now = float(self._clock())
            if not force and now - self._last_maintenance < MAINTENANCE_INTERVAL_SECONDS:
                return self._status_locked()
            self._rebuild_index_locked()
            self._cleanup_temporary_files_locked(now)
            for namespace in tuple(self._namespaces.values()):
                namespace._maintenance_locked(clean_temps=False)
            self._enforce_global_locked()
            self._last_maintenance = now
            status = self._status_locked()
            self._publish_locked(status)
            return status

    def maintenance_if_due(self) -> bool:
        """Run the daily recovery scan without doing work on frequent health ticks."""

        with self._lock:
            now = float(self._clock())
            if now - self._last_maintenance < MAINTENANCE_INTERVAL_SECONDS:
                return False
            self._rebuild_index_locked()
            self._cleanup_temporary_files_locked(now)
            for namespace in tuple(self._namespaces.values()):
                namespace._maintenance_locked(clean_temps=False)
            self._enforce_global_locked()
            self._last_maintenance = now
            self._publish_locked()
            return True

    def status(self) -> CacheStatus:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> CacheStatus:
        records = tuple(self._records.values())
        now = float(self._clock())
        oldest_age = max(
            (max(0.0, now - item.last_used) for item in records),
            default=0.0,
        )
        return CacheStatus(
            files=len(records),
            bytes=sum(item.size for item in records),
            oldest_age_seconds=oldest_age,
            evicted_total=self._evicted_total,
            rejected_total=self._rejected_total,
        )

    def _cleanup_temporary_files_locked(self, now: float) -> None:
        for record in _temporary_files(self.root):
            if now - record.last_used <= TEMP_MAX_AGE_SECONDS:
                continue
            self._unlink_locked(record.path)

    def _maybe_maintenance_locked(self) -> None:
        now = float(self._clock())
        if now - self._last_maintenance >= MAINTENANCE_INTERVAL_SECONDS:
            self._rebuild_index_locked()
            self._cleanup_temporary_files_locked(now)
            for namespace in tuple(self._namespaces.values()):
                namespace._maintenance_locked(clean_temps=False)
            self._enforce_global_locked()
            self._last_maintenance = now

    def _prune_global_for_incoming_locked(self, target: Path, incoming: int) -> None:
        if incoming > self.global_max_bytes:
            self._rejected_total += 1
            raise CacheObjectTooLarge("cache object exceeds the global disk budget")
        records = [item for item in self._records.values() if item.path != target]
        total = sum(item.size for item in records)
        for record in sorted(records, key=lambda item: (item.last_used, str(item.path))):
            if total + incoming <= self.global_max_bytes:
                break
            if self._unlink_locked(record.path):
                total -= record.size
        if total + incoming > self.global_max_bytes:
            self._rejected_total += 1
            raise CacheObjectTooLarge("global cache budget could not accept the object")

    def _enforce_global_locked(self) -> None:
        records = tuple(self._records.values())
        total = sum(item.size for item in records)
        for record in sorted(records, key=lambda item: (item.last_used, str(item.path))):
            if total <= self.global_max_bytes:
                break
            if self._unlink_locked(record.path):
                total -= record.size

    def _unlink_locked(self, path: Path) -> bool:
        try:
            _assert_resolved_within(self.root, path)
            if path.is_symlink():
                return False
            if not path.exists():
                self._forget_path_locked(path)
                return False
            path.unlink(missing_ok=True)
        except (OSError, CachePathError):
            logger.warning("Could not remove managed cache file: %s", path)
            return False
        self._forget_path_locked(path)
        self._evicted_total += 1
        return True

    def _rebuild_index_locked(self) -> None:
        self._records = {
            record.path: record
            for record in _regular_files(self.root)
        }
        for namespace in self._namespaces.values():
            namespace._records = {
                path: record
                for path, record in self._records.items()
                if _is_relative_to(path.absolute(), namespace.root.absolute())
            }

    def _remember_path_locked(self, path: Path) -> _FileRecord | None:
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            self._forget_path_locked(path)
            return None
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            self._forget_path_locked(path)
            return None
        record = _FileRecord(
            path,
            int(info.st_size),
            max(float(info.st_atime), float(info.st_mtime)),
        )
        self._records[path] = record
        for namespace in self._namespaces.values():
            if _is_relative_to(path.absolute(), namespace.root.absolute()):
                namespace._records[path] = record
        return record

    def _forget_path_locked(self, path: Path) -> None:
        self._records.pop(path, None)
        for namespace in self._namespaces.values():
            namespace._records.pop(path, None)

    def _publish_locked(self, status: CacheStatus | None = None) -> None:
        if self.health_publisher is None:
            return
        status = status or self._status_locked()
        namespaces = {}
        for name, namespace in self._namespaces.items():
            item = namespace._status_locked()
            namespaces[name] = {
                "files": item.files,
                "bytes": item.bytes,
                "max_files": namespace.budget.max_files,
                "max_bytes": namespace.budget.max_bytes,
            }
        value = {
            "files": status.files,
            "bytes": status.bytes,
            "global_max_bytes": self.global_max_bytes,
            "evicted_total": status.evicted_total,
            "rejected_total": status.rejected_total,
            "namespaces": namespaces,
        }
        try:
            self.health_publisher.publish_component("cache", value)
        except Exception:
            logger.exception("Cache health status could not be published")


class CacheNamespace:
    """A path-safe namespace with age, count, and byte limits."""

    def __init__(self, manager, name, root, budget):
        if not isinstance(budget, CacheBudget):
            raise TypeError("budget must be a CacheBudget")
        self.manager = manager
        self.name = name
        self.root = Path(root)
        self.budget = budget
        self._records: dict[Path, _FileRecord] = {}

    def path(self, key: str, suffix: str = "") -> Path:
        parts = list(_normalized_parts(key, label="cache key"))
        suffix = _normalized_suffix(suffix)
        if parts[-1].endswith(".tmp"):
            raise CachePathError("the .tmp extension is reserved for atomic writes")
        parts[-1] = f"{parts[-1]}{suffix}"
        target = self.root.joinpath(*parts)
        _assert_resolved_within(self.root, target)
        return target

    def put_bytes(self, key: str, data, *, suffix: str = "") -> Path:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("cache values must be bytes-like")
        payload = bytes(data)
        target = self.path(key, suffix)
        with self.manager._lock:
            self.manager._maybe_maintenance_locked()
            maximum = min(self.budget.max_bytes, self.manager.global_max_bytes)
            if len(payload) > maximum:
                self.manager._rejected_total += 1
                self.manager._publish_locked()
                raise CacheObjectTooLarge("cache object exceeds its namespace budget")
            self._prune_for_incoming_locked(target, len(payload))
            self.manager._prune_global_for_incoming_locked(target, len(payload))
            _assert_resolved_within(self.root, target.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            _assert_resolved_within(self.root, target.parent)
            temporary = None
            descriptor = None
            try:
                descriptor, raw_path = tempfile.mkstemp(
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    dir=target.parent,
                )
                temporary = Path(raw_path)
                _assert_resolved_within(self.root, temporary)
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = None
                    handle.write(payload)
                    handle.flush()
                os.chmod(temporary, 0o600)
                _assert_resolved_within(self.root, temporary)
                _assert_resolved_within(self.root, target)
                os.replace(temporary, target)
                temporary = None
                now = float(self.manager._clock())
                os.utime(target, (now, now))
                self.manager._remember_path_locked(target)
            finally:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("Could not remove cache temp file: %s", temporary)
            self.manager._publish_locked()
            return target

    def get_bytes(self, key: str, *, suffix: str = "") -> bytes | None:
        target = self.path(key, suffix)
        with self.manager._lock:
            self.manager._maybe_maintenance_locked()
            if not target.is_file() or target.is_symlink():
                return None
            try:
                info = target.stat(follow_symlinks=False)
            except OSError:
                return None
            now = float(self.manager._clock())
            last_used = max(float(info.st_atime), float(info.st_mtime))
            if now - last_used > self.budget.max_age_seconds:
                self.manager._unlink_locked(target)
                self.manager._publish_locked()
                return None
            data = target.read_bytes()
            os.utime(target, (now, now))
            self.manager._remember_path_locked(target)
            return data

    def remove(self, key: str, *, suffix: str = "") -> bool:
        target = self.path(key, suffix)
        with self.manager._lock:
            if not target.exists() or target.is_symlink():
                return False
            removed = self.manager._unlink_locked(target)
            self.manager._publish_locked()
            return removed

    def clear(self) -> None:
        with self.manager._lock:
            for record in _regular_files(self.root):
                self.manager._unlink_locked(record.path)
            for record in _temporary_files(self.root):
                self.manager._unlink_locked(record.path)
            self.manager._publish_locked()

    def maintenance(self) -> CacheStatus:
        with self.manager._lock:
            self._maintenance_locked()
            self.manager._enforce_global_locked()
            status = self._status_locked()
            self.manager._publish_locked()
            return status

    def status(self) -> CacheStatus:
        with self.manager._lock:
            return self._status_locked()

    def _maintenance_locked(self, *, clean_temps=True) -> None:
        now = float(self.manager._clock())
        if clean_temps:
            for record in _temporary_files(self.root):
                if now - record.last_used > TEMP_MAX_AGE_SECONDS:
                    self.manager._unlink_locked(record.path)
        records = tuple(self._records.values())
        for record in records:
            if now - record.last_used > self.budget.max_age_seconds:
                self.manager._unlink_locked(record.path)
        self._enforce_limits_locked()

    def _prune_for_incoming_locked(self, target: Path, incoming: int) -> None:
        self._maintenance_locked()
        records = [item for item in self._records.values() if item.path != target]
        total = sum(item.size for item in records)
        count = len(records)
        for record in sorted(records, key=lambda item: (item.last_used, str(item.path))):
            if count + 1 <= self.budget.max_files and total + incoming <= self.budget.max_bytes:
                break
            if self.manager._unlink_locked(record.path):
                count -= 1
                total -= record.size
        if count + 1 > self.budget.max_files or total + incoming > self.budget.max_bytes:
            self.manager._rejected_total += 1
            raise CacheObjectTooLarge("namespace cache budget could not accept the object")

    def _enforce_limits_locked(self) -> None:
        records = tuple(self._records.values())
        total = sum(item.size for item in records)
        count = len(records)
        for record in sorted(records, key=lambda item: (item.last_used, str(item.path))):
            if count <= self.budget.max_files and total <= self.budget.max_bytes:
                break
            if self.manager._unlink_locked(record.path):
                count -= 1
                total -= record.size

    def _status_locked(self) -> CacheStatus:
        records = tuple(self._records.values())
        now = float(self.manager._clock())
        oldest_age = max(
            (max(0.0, now - item.last_used) for item in records),
            default=0.0,
        )
        return CacheStatus(
            len(records),
            sum(item.size for item in records),
            oldest_age,
            self.manager._evicted_total,
            self.manager._rejected_total,
        )

    def _reload_index_locked(self) -> None:
        for record in _regular_files(self.root):
            self.manager._records[record.path] = record
        self._records = {
            path: record
            for path, record in self.manager._records.items()
            if _is_relative_to(path.absolute(), self.root.absolute())
        }


def configure_cache_manager(runtime_paths, *, health_publisher=None) -> CacheManager:
    global _GLOBAL_MANAGER
    manager = CacheManager(
        runtime_paths,
        health_publisher=health_publisher,
    )
    with _GLOBAL_MANAGER_LOCK:
        _GLOBAL_MANAGER = manager
    return manager


def get_cache_manager() -> CacheManager | None:
    with _GLOBAL_MANAGER_LOCK:
        return _GLOBAL_MANAGER


def cache_namespace_for_directory(
    directory,
    budget: CacheBudget = DEFAULT_CACHE_BUDGET,
) -> CacheNamespace:
    """Use the global manager when possible, else own only the exact directory."""

    target = Path(directory).expanduser().absolute()
    manager = get_cache_manager()
    if manager is not None:
        manager_root = manager.root.absolute()
        if _is_relative_to(target, manager_root) and target != manager_root:
            relative = target.relative_to(manager_root)
            return manager.namespace(relative.as_posix(), budget)

    key = os.path.normcase(os.path.abspath(target))
    with _GLOBAL_MANAGER_LOCK:
        auxiliary = _AUXILIARY_MANAGERS.get(key)
        if auxiliary is None:
            auxiliary = CacheManager(target)
            _AUXILIARY_MANAGERS[key] = auxiliary
            while len(_AUXILIARY_MANAGERS) > _AUXILIARY_MANAGER_LIMIT:
                _AUXILIARY_MANAGERS.popitem(last=False)
        else:
            _AUXILIARY_MANAGERS.move_to_end(key)
    return auxiliary._root_namespace(budget)


@dataclass
class _ImageEntry:
    value: Any
    bytes: int
    sequence: int


class _ImageCachePool:
    """Shared lock and byte budget for one or more ImageLRUCache objects."""

    def __init__(self, *, max_bytes=DEFAULT_GLOBAL_IMAGE_CACHE_MAX_BYTES):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("image cache pool max_bytes must be a positive integer")
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._caches = weakref.WeakSet()
        self._sequence = itertools.count()

    @property
    def bytes(self) -> int:
        with self._lock:
            return sum(cache._bytes_locked() for cache in tuple(self._caches))

    def register(self, cache) -> None:
        with self._lock:
            self._caches.add(cache)

    def next_sequence(self) -> int:
        return next(self._sequence)

    def enforce(self) -> None:
        while self.bytes > self.max_bytes:
            oldest = None
            for cache in tuple(self._caches):
                candidate = cache._oldest_locked()
                if candidate is None:
                    continue
                sequence, key = candidate
                if oldest is None or sequence < oldest[0]:
                    oldest = (sequence, cache, key)
            if oldest is None:
                return
            oldest[1]._delete_locked(oldest[2])


_GLOBAL_IMAGE_CACHE_POOL = _ImageCachePool()


def _image_bytes(value) -> int:
    if value is None:
        return 0
    size = getattr(value, "size", None)
    bands = getattr(value, "getbands", None)
    if (
        isinstance(size, tuple)
        and len(size) == 2
        and callable(bands)
    ):
        width, height = size
        return max(0, int(width)) * max(0, int(height)) * max(1, len(bands()))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    return 0


class ImageLRUCache(MutableMapping):
    """MutableMapping-compatible LRU bounded by entries and estimated image bytes."""

    __hash__ = object.__hash__

    def __init__(
        self,
        *,
        max_entries=DEFAULT_IMAGE_CACHE_MAX_ENTRIES,
        max_bytes=DEFAULT_IMAGE_CACHE_MAX_BYTES,
        pool=None,
    ):
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries <= 0:
            raise ValueError("max_entries must be a positive integer")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._pool = pool or _GLOBAL_IMAGE_CACHE_POOL
        self._entries: OrderedDict[Any, _ImageEntry] = OrderedDict()
        self._pool.register(self)

    @property
    def bytes(self) -> int:
        with self._pool._lock:
            return self._bytes_locked()

    def __getitem__(self, key):
        with self._pool._lock:
            entry = self._entries.pop(key)
            entry.sequence = self._pool.next_sequence()
            self._entries[key] = entry
            return entry.value

    def __setitem__(self, key, value):
        size = _image_bytes(value)
        with self._pool._lock:
            self._entries.pop(key, None)
            if size > self.max_bytes or size > self._pool.max_bytes:
                return
            self._entries[key] = _ImageEntry(
                value,
                size,
                self._pool.next_sequence(),
            )
            self._enforce_locked()
            self._pool.enforce()

    def __delitem__(self, key):
        with self._pool._lock:
            del self._entries[key]

    def __iter__(self) -> Iterator:
        with self._pool._lock:
            return iter(tuple(self._entries))

    def __len__(self) -> int:
        with self._pool._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._pool._lock:
            self._entries.clear()

    def _bytes_locked(self) -> int:
        return sum(entry.bytes for entry in self._entries.values())

    def _oldest_locked(self):
        if not self._entries:
            return None
        key, entry = next(iter(self._entries.items()))
        return entry.sequence, key

    def _delete_locked(self, key) -> None:
        self._entries.pop(key, None)

    def _enforce_locked(self) -> None:
        while len(self._entries) > self.max_entries or self._bytes_locked() > self.max_bytes:
            self._entries.popitem(last=False)
