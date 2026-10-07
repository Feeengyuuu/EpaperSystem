"""Safe, read-only health projection for the private web portal."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import stat
import threading
import time

from utils.atomic_file import atomic_write_json


logger = logging.getLogger(__name__)
_MAX_WORKER_STATE_BYTES = 4096


@dataclass(frozen=True, slots=True)
class PublicationReadiness:
    """Result of validating the currently displayable publication set."""

    configured: int
    displayable: int
    assets_valid: bool = True

    @property
    def ready(self) -> bool:
        return (
            self.configured > 0
            and self.displayable == self.configured
            and self.assets_valid
        )


@dataclass(frozen=True, slots=True)
class PortalHealthSnapshot:
    """Small, stable status safe to expose on an unauthenticated route."""

    status: str
    reason: str | None = None

    @property
    def http_status(self) -> int:
        return 503 if self.status == "not_ready" else 200

    def public_document(self) -> dict[str, str]:
        document = {"status": self.status}
        if self.reason:
            document["reason"] = self.reason
        return document


@dataclass(frozen=True, slots=True)
class WorkerCycleState:
    """Validated, secret-free state from the most recent worker cycle."""

    cycle_completed_at: float
    cycle_status: str
    attempted: int
    published: int
    skipped: int
    unavailable: int
    last_successful_publication_at: float | None
    consecutive_failures: int

    def document(self) -> dict[str, int | float | str | None]:
        return {
            "attempted": self.attempted,
            "consecutive_failures": self.consecutive_failures,
            "cycle_completed_at": self.cycle_completed_at,
            "cycle_status": self.cycle_status,
            "last_successful_publication_at": self.last_successful_publication_at,
            "published": self.published,
            "skipped": self.skipped,
            "unavailable": self.unavailable,
            "version": 2,
        }


def _epoch(value, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("worker timestamp is invalid")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("worker timestamp is invalid")
    return result


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
        raise ValueError("worker count is invalid")
    return value


def _read_worker_state_document(path) -> str | None:
    target = Path(path)
    if target.is_symlink():
        return None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_WORKER_STATE_BYTES:
            return None
        chunks = []
        remaining = _MAX_WORKER_STATE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > _MAX_WORKER_STATE_BYTES:
            return None
        return encoded.decode("utf-8")
    finally:
        os.close(descriptor)


def load_worker_state(path) -> WorkerCycleState | None:
    """Load one bounded v2 state file; malformed or link-like inputs fail closed."""

    try:
        document = _read_worker_state_document(path)
        if document is None:
            return None
        payload = json.loads(document)
        if not isinstance(payload, dict) or payload.get("version") != 2:
            return None
        status = payload.get("cycle_status")
        if status not in {"ok", "degraded", "failed"}:
            return None
        state = WorkerCycleState(
            cycle_completed_at=_epoch(payload.get("cycle_completed_at")),
            cycle_status=status,
            attempted=_count(payload.get("attempted")),
            published=_count(payload.get("published")),
            skipped=_count(payload.get("skipped")),
            unavailable=_count(payload.get("unavailable")),
            last_successful_publication_at=_epoch(
                payload.get("last_successful_publication_at"),
                optional=True,
            ),
            consecutive_failures=_count(payload.get("consecutive_failures")),
        )
        if (
            state.published + state.skipped + state.unavailable > state.attempted
            or (
                state.last_successful_publication_at is not None
                and state.last_successful_publication_at > state.cycle_completed_at
            )
        ):
            return None
        return state
    except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None


def worker_state_is_fresh(path, *, max_age_seconds, now) -> bool:
    state = load_worker_state(path)
    try:
        current = _epoch(now)
        maximum_age = float(max_age_seconds)
    except (TypeError, ValueError):
        return False
    if state is None or not math.isfinite(maximum_age) or maximum_age <= 0:
        return False
    age = current - state.cycle_completed_at
    return 0 <= age <= maximum_age


def _report_count(report, name):
    value = report.get(name, 0) if isinstance(report, Mapping) else getattr(report, name, 0)
    return _count(value)


class WorkerStateStore:
    """Atomically maintain liveness separately from publication success."""

    def __init__(self, path):
        self._path = Path(path)

    def record_completed(self, report, completed_at) -> WorkerCycleState:
        completed_at = _epoch(completed_at)
        previous = load_worker_state(self._path)
        published = _report_count(report, "published")
        skipped = _report_count(report, "skipped")
        unavailable = _report_count(report, "unavailable")
        attempted = _report_count(report, "attempted")
        attempted = max(attempted, published + skipped + unavailable)
        last_success = completed_at if published > 0 else (
            previous.last_successful_publication_at if previous is not None else None
        )
        status = "degraded" if unavailable > 0 or last_success is None else "ok"
        state = WorkerCycleState(
            cycle_completed_at=completed_at,
            cycle_status=status,
            attempted=attempted,
            published=published,
            skipped=skipped,
            unavailable=unavailable,
            last_successful_publication_at=last_success,
            consecutive_failures=0,
        )
        self._persist(state)
        return state

    def record_failed(self, completed_at) -> WorkerCycleState:
        completed_at = _epoch(completed_at)
        previous = load_worker_state(self._path)
        state = WorkerCycleState(
            cycle_completed_at=completed_at,
            cycle_status="failed",
            attempted=0,
            published=0,
            skipped=0,
            unavailable=0,
            last_successful_publication_at=(
                previous.last_successful_publication_at if previous is not None else None
            ),
            consecutive_failures=(previous.consecutive_failures + 1 if previous is not None else 1),
        )
        self._persist(state)
        return state

    def _persist(self, state: WorkerCycleState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path, state.document(), mode=0o600)


def _credentials_ready(credentials) -> bool:
    if not getattr(credentials, "available", True):
        return False
    revision = credentials.admin_session_revision()
    return bool(credentials.has_admin() and isinstance(revision, str) and revision)


def _fallback_publication_readiness(publications) -> PublicationReadiness:
    catalog = publications.list_publications()
    if not isinstance(catalog, Mapping):
        return PublicationReadiness(0, 0, False)
    raw_publications = catalog.get("publications")
    if not isinstance(raw_publications, (list, tuple)):
        return PublicationReadiness(0, 0, False)
    configured = 0
    displayable = 0
    assets_valid = True
    for publication in raw_publications:
        if not isinstance(publication, Mapping) or not publication.get("slug"):
            continue
        configured += 1
        if publication.get("freshness") == "unavailable":
            continue
        if publication.get("kind") == "legacy_png":
            asset_id = publication.get("asset_id")
            if not isinstance(asset_id, str) or not asset_id or publications.get_asset(asset_id) is None:
                assets_valid = False
                continue
        displayable += 1
    return PublicationReadiness(configured, displayable, assets_valid)


class PortalHealthProbe:
    """Combine credentials and the injected read seam without provider access."""

    def __init__(
        self,
        credentials,
        publications,
        *,
        worker_state_path=None,
        worker_state_max_age_seconds=300,
        publication_cache_seconds=10,
        clock=time.time,
        cache_clock=time.monotonic,
    ):
        self._credentials = credentials
        self._publications = publications
        self._worker_state_path = Path(worker_state_path) if worker_state_path else None
        self._worker_state_max_age_seconds = float(worker_state_max_age_seconds)
        if not math.isfinite(self._worker_state_max_age_seconds) or self._worker_state_max_age_seconds <= 0:
            raise ValueError("worker state max age must be positive")
        self._publication_cache_seconds = float(publication_cache_seconds)
        if not math.isfinite(self._publication_cache_seconds) or self._publication_cache_seconds <= 0:
            raise ValueError("publication health cache duration must be positive")
        self._clock = clock
        self._cache_clock = cache_clock
        self._publication_cache_lock = threading.Lock()
        self._publication_cache_deadline = None
        self._publication_cache_result = None

    def _inspect_publications_uncached(self) -> PortalHealthSnapshot | None:
        try:
            inspect = getattr(self._publications, "inspect_readiness", None)
            result = inspect() if callable(inspect) else _fallback_publication_readiness(self._publications)
        except Exception:
            logger.warning("Portal readiness publication check failed closed")
            return PortalHealthSnapshot("not_ready", "publication_unavailable")
        if not isinstance(result, PublicationReadiness):
            return PortalHealthSnapshot("not_ready", "publication_unavailable")
        if not result.assets_valid:
            return PortalHealthSnapshot("not_ready", "asset_unavailable")
        if result.configured <= 0 or result.displayable <= 0:
            return PortalHealthSnapshot("not_ready", "publication_unavailable")
        if result.displayable < result.configured:
            return PortalHealthSnapshot("degraded", "publication_degraded")
        return None

    def _inspect_publications(self) -> PortalHealthSnapshot | None:
        try:
            now = float(self._cache_clock())
        except (TypeError, ValueError, OverflowError):
            logger.warning("Portal readiness cache clock failed; bypassing cache")
            return self._inspect_publications_uncached()
        if not math.isfinite(now):
            logger.warning("Portal readiness cache clock failed; bypassing cache")
            return self._inspect_publications_uncached()

        with self._publication_cache_lock:
            if (
                self._publication_cache_deadline is not None
                and now < self._publication_cache_deadline
            ):
                return self._publication_cache_result
            result = self._inspect_publications_uncached()
            self._publication_cache_result = result
            self._publication_cache_deadline = now + self._publication_cache_seconds
            return result

    def inspect(self) -> PortalHealthSnapshot:
        try:
            if not _credentials_ready(self._credentials):
                return PortalHealthSnapshot("not_ready", "credentials_unavailable")
        except Exception:
            logger.warning("Portal readiness credential check failed closed")
            return PortalHealthSnapshot("not_ready", "credentials_unavailable")

        publication_failure = self._inspect_publications()
        if publication_failure is not None:
            return publication_failure
        if self._worker_state_path is not None:
            state = load_worker_state(self._worker_state_path)
            try:
                now = _epoch(self._clock())
            except (TypeError, ValueError):
                logger.warning("Portal readiness worker clock failed closed")
                return PortalHealthSnapshot("degraded", "worker_stale")
            if (
                state is None
                or not 0 <= now - state.cycle_completed_at <= self._worker_state_max_age_seconds
            ):
                return PortalHealthSnapshot("degraded", "worker_stale")
            if state.cycle_status != "ok":
                return PortalHealthSnapshot("degraded", "worker_degraded")
        return PortalHealthSnapshot("ready")


__all__ = [
    "PortalHealthProbe",
    "PortalHealthSnapshot",
    "PublicationReadiness",
    "WorkerCycleState",
    "WorkerStateStore",
    "load_worker_state",
    "worker_state_is_fresh",
]
