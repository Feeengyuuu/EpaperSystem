"""Bounded reusable posters, separate from disposable provider caches."""
from __future__ import annotations

import hashlib
import io
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from utils.safe_image import safe_open_image

logger = logging.getLogger(__name__)

class PosterStore:
    """Retain URL-identified covers and evict unused media oldest first."""

    def __init__(self, root, *, clock=time.time, max_age_seconds=30 * 86400,
                 max_files=256, max_bytes=32 * 1024 * 1024):
        self.root = Path(root)
        self.clock = clock
        self.max_age = max_age_seconds
        self.max_files = max_files
        self.max_bytes = max_bytes
        self._lock = threading.RLock()

    def path(self, url):
        """Return the exact URL's path without creating or accessing files."""
        return self.root / (hashlib.sha256(url.encode('utf-8')).hexdigest() + '.jpg')

    def resolve(self, url, *, legacy=None, touch=True, protected_urls=()):
        """Return a decoded local cover, optionally importing its exact legacy file."""
        if not url or not self._safe_root():
            return None
        with self._lock:
            target = self.path(url)
            for candidate in (target, Path(legacy) if legacy else None):
                if candidate is None or candidate.is_symlink():
                    continue
                try:
                    with safe_open_image(candidate) as image:
                        if candidate != target:
                            if not touch:
                                return candidate
                            try:
                                return self.save(url, image, protected_urls=[url, *protected_urls])
                            except (OSError, ValueError) as error:
                                logger.warning("Poster migration deferred; using exact legacy cover. | error=%s", type(error).__name__)
                                return candidate
                    if touch and self.clock() - target.stat().st_mtime >= min(86400, self.max_age / 2):
                        try:
                            os.utime(target, (self.clock(), self.clock()))
                        except OSError:
                            logger.debug("Poster access timestamp update deferred. | key=%s", target.stem)
                    return target
                except (OSError, ValueError):
                    continue
        return None

    def save(self, url, image, *, protected_urls=()):
        """Atomically save a bounded JPEG without evicting active chart covers."""
        with image.convert('RGB') as encoded:
            encoded.thumbnail((600, 900))
            buffer = io.BytesIO()
            encoded.save(buffer, format='JPEG', quality=88)
            payload = buffer.getvalue()
        if len(payload) > min(self.max_bytes, 2 * 1024 * 1024):
            raise ValueError('Poster exceeds retained media budget')
        with self._lock:
            self._ensure_root()
            target = self.path(url)
            self._evict(protected_urls, incoming=len(payload), target=target)
            descriptor, name = tempfile.mkstemp(prefix='.poster-', suffix='.tmp', dir=self.root)
            try:
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(payload)
                os.replace(name, target)
                os.utime(target, (self.clock(), self.clock()))
                logger.info("Retained poster saved. | key=%s bytes=%s", target.stem, len(payload))
            finally:
                Path(name).unlink(missing_ok=True)
            return target

    def cleanup(self, *, protected_urls=()):
        """Delete expired covers and then least recently used excess files."""
        with self._lock:
            if self._safe_root() and self.root.is_dir():
                self._evict(protected_urls)

    def _safe_root(self):
        return not any(parent.is_symlink() for parent in (self.root, *self.root.parents))

    def _ensure_root(self):
        if not self._safe_root():
            raise ValueError('Retained poster directory cannot traverse a symlink')
        self.root.mkdir(parents=True, exist_ok=True)

    def _records(self):
        records = []
        for path in self.root.glob('*.jpg'):
            if not re.fullmatch(r'[0-9a-f]{64}\.jpg', path.name) or path.is_symlink():
                continue
            info = path.stat()
            records.append((path, info.st_size, info.st_mtime))
        return sorted(records, key=lambda row: (row[2], row[0].name))

    def _evict(self, protected_urls, *, incoming=0, target=None):
        protected = {self.path(url) for url in protected_urls if url}
        records = [row for row in self._records() if row[0] != target]
        size = sum(row[1] for row in records) + incoming
        count = len(records) + int(target is not None)
        for path, length, used in records:
            if path in protected:
                continue
            expired = self.clock() - used > self.max_age
            if expired or size > self.max_bytes or count > self.max_files:
                path.unlink()
                logger.info("Retained poster evicted. | key=%s reason=%s bytes=%s", path.stem, "age" if expired else "capacity", length)
                size -= length
                count -= 1
        if size > self.max_bytes or count > self.max_files:
            raise ValueError('Active posters fill retained media budget')
