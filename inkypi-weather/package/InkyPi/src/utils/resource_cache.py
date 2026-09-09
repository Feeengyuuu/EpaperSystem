"""Reusable media with bounded retention, atomic replacement and observable reuse.

Media freshness uses mtime; access tracking never extends that freshness clock.
Only explicitly named image prefixes are eligible for cleanup, never plugin state.
"""

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time

from runtime.refresh_contracts import TaskCancelled
from utils.safe_image import ImageLimits, safe_open_image, safe_open_image_response


logger = logging.getLogger(__name__)
_METRICS = ContextVar("resource_cache_metrics", default=None)
_MAX_STALE = 90 * 24 * 3600
_LIMITS = ImageLimits(max_pixels=32_000_000)


def record_resource_event(label, **values):
    metrics = _METRICS.get()
    if metrics is None:
        logger.info("Resource cache event. | resource: %s | %s", label,
                    " | ".join(f"{key}: {value}" for key, value in values.items()))
    else:
        metrics.setdefault(label, Counter()).update(values)


@contextmanager
def resource_cache_metrics(plugin_id):
    if _METRICS.get() is not None:
        yield
        return
    metrics = {}
    token = _METRICS.set(metrics)
    try:
        yield
    finally:
        _METRICS.reset(token)
        for label, counts in sorted(metrics.items()):
            logger.info("Resource cache summary. | plugin: %s | resource: %s | %s",
                        plugin_id, label,
                        " | ".join(f"{key}: {value}" for key, value in sorted(counts.items())))


class _MeasuredResponse:
    def __init__(self, response):
        self.response = response
        self.bytes = 0

    def __getattr__(self, name):
        return getattr(self.response, name)

    def iter_content(self, chunk_size):
        for chunk in self.response.iter_content(chunk_size=chunk_size):
            self.bytes += len(chunk or b"")
            yield chunk


def measured_image_response(response, **kwargs):
    measured = _MeasuredResponse(response)
    image = safe_open_image_response(measured, **kwargs)
    image.info["resource_downloaded_bytes"] = measured.bytes
    return image


def _safe_path(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("media cache must not traverse a symlink")
    return path


def _atomic_bytes(path, payload):
    path = _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".resource-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        _safe_path(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_retry(path):
    try:
        path = _safe_path(path)
        if path.stat().st_size > 1024:
            return 0.0
        value = json.loads(path.read_text(encoding="utf-8"))
        return float(value.get("retry_after", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def _cached_image(path, label):
    try:
        stat = path.stat()
        image = safe_open_image(path, limits=_LIMITS)
        return image, stat
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        record_resource_event(label, invalid_entries=1)
        return None, None


def _touch_access(path, stat, now):
    if now - stat.st_atime < 24 * 3600:
        return
    try:
        os.utime(path, (now, stat.st_mtime))
    except OSError:
        pass


def _stale_image(cached, stat, now, max_stale, label):
    if cached is not None and max(0, now - stat.st_mtime) <= max_stale:
        record_resource_event(label, stale_hits=1)
        cached.info["resource_cache_state"] = "stale"
        return cached
    if cached is not None:
        cached.close()
    return None


def _negative_seconds(error, default):
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status in (404, 410) or re.search(r"\b(?:404|410)\b", str(error)):
        return 6 * 3600
    return max(1, default)


def _publish_image(path, image, now):
    output = BytesIO()
    image.save(output, format="PNG")
    payload = output.getvalue()
    if len(payload) > _LIMITS.max_bytes:
        raise ValueError("encoded media exceeds cache object budget")
    _atomic_bytes(path, payload)
    os.utime(path, (now, now))
    return len(payload)


def cached_resource_image(path, fetch, *, ttl, label, read_only=False,
                          max_stale=_MAX_STALE, retry_seconds=300):
    """Read a URL-identified image or atomically replace it after a valid fetch.

The callback owns network policy and returns a fully decoded image. Cancellation
propagates; provider failures may reuse old media without changing its timestamp.
"""
    path = _safe_path(path)
    now = time.time()
    cached, stat = _cached_image(path, label)
    if cached is not None and (read_only or max(0, now - stat.st_mtime) < ttl):
        if not read_only:
            _touch_access(path, stat, now)
        record_resource_event(label, disk_hits=1)
        return cached
    if read_only:
        record_resource_event(label, readonly_misses=1)
        return None
    retry_path = path.with_suffix(".retry.json")
    if _read_retry(retry_path) > now:
        record_resource_event(label, negative_hits=1)
        return _stale_image(cached, stat, now, max_stale, label)
    try:
        image = fetch()
        if image is None:
            raise ValueError("media source returned no image")
        image.load()
        if image.width * image.height > _LIMITS.max_pixels:
            image.close()
            raise ValueError("media source exceeds pixel budget")
        if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
            normalized = image.convert("RGB")
            image.close()
            image = normalized
    except TaskCancelled:
        if cached is not None:
            cached.close()
        raise
    except Exception as error:
        record_resource_event(label, download_failures=1)
        try:
            _atomic_bytes(retry_path, json.dumps({
                "retry_after": now + _negative_seconds(error, retry_seconds),
            }).encode("ascii"))
        except (OSError, ValueError):
            pass
        return _stale_image(cached, stat, now, max_stale, label)
    if cached is not None:
        cached.close()
    download_bytes = image.info.get("resource_downloaded_bytes")
    record_resource_event(label, downloads=1, **(
        {"downloaded_bytes": int(download_bytes)} if download_bytes is not None
        else {"unmeasured_downloads": 1}))
    try:
        written = _publish_image(path, image, now)
        retry_path.unlink(missing_ok=True)
        record_resource_event(label, written_bytes=written)
    except (OSError, ValueError):
        record_resource_event(label, write_failures=1)
    return image


def _image_records(directory, prefixes):
    pattern = re.compile(r"(?:" + "|".join(re.escape(p) for p in prefixes)
                         + r")[0-9a-f]{18,64}\.png$")
    records = []
    if not directory.is_dir():
        return records
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= 4096:
                break
            if not pattern.fullmatch(entry.name) or entry.is_symlink():
                continue
            try:
                if entry.is_file(follow_symlinks=False):
                    path = Path(entry.path)
                    records.append((path, path.stat(follow_symlinks=False)))
            except OSError:
                continue
    return records


def _prune_retries(directory, prefixes, now):
    removed = 0
    if not directory.is_dir():
        return removed
    pattern = re.compile(r"(?:" + "|".join(re.escape(p) for p in prefixes)
                         + r")[0-9a-f]{18,64}\.retry\.json$")
    pending = []
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= 4096:
                break
            if pattern.fullmatch(entry.name) and not entry.is_symlink():
                path = Path(entry.path)
                pending.append((_read_retry(path), path))
    for index, (until, path) in enumerate(sorted(pending, reverse=True)):
        if until > now and index < 256:
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return removed


def prune_resource_images(directory, *, prefixes, max_files, max_bytes,
                          max_age, label, protected=()):
    """Bound one known media family, preserving all currently referenced files."""
    directory = _safe_path(directory)
    if not prefixes:
        raise ValueError("resource cleanup requires explicit filename prefixes")
    records = _image_records(directory, prefixes)
    protected = {Path(path).absolute() for path in protected}
    count = len(records)
    total = sum(stat.st_size for _, stat in records)
    now = time.time()
    removed = removed_bytes = 0
    for path, stat in sorted(records, key=lambda item: max(item[1].st_atime, item[1].st_mtime)):
        if path in protected:
            continue
        expired = now - max(stat.st_atime, stat.st_mtime) > max_age
        if not expired and count <= max_files and total <= max_bytes:
            continue
        try:
            current = _safe_path(path).stat()
            if (current.st_mtime_ns, current.st_size, current.st_ino) != (
                    stat.st_mtime_ns, stat.st_size, stat.st_ino):
                continue
            path.unlink()
            path.with_suffix(".retry.json").unlink(missing_ok=True)
        except (OSError, ValueError):
            continue
        count -= 1
        total -= stat.st_size
        removed += 1
        removed_bytes += stat.st_size
    retries = _prune_retries(directory, prefixes, now)
    record_resource_event(label, evicted_files=removed, evicted_bytes=removed_bytes,
                          expired_retries=retries, protected_over_budget=int(
                              count > max_files or total > max_bytes))
    return {"files": count, "bytes": total, "evicted_files": removed,
            "evicted_bytes": removed_bytes}
