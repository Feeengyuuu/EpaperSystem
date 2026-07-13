"""Bounded, fail-closed lifecycle management for scheduler render artifacts."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
import os
from pathlib import Path
import re
import secrets
import stat as stat_module
import time
from types import MappingProxyType
from typing import Callable, Mapping

from .cache_catalog import (
    authoritative_cache_path,
    cache_identity_prefix,
    parse_authoritative_cache_filename,
)
from .presentation_cache import (
    PRESENTATION_MAX_AGE_SECONDS,
    parse_prepared_presentation_filename,
    prepared_presentation_path,
)


CACHE_LIFECYCLE_INTERVAL_SECONDS = 60 * 60
STALE_TEMP_SECONDS = 2 * 60 * 60
OLD_REVISION_SECONDS = 24 * 60 * 60
OLD_REVISION_WITHOUT_CURRENT_SECONDS = 7 * 24 * 60 * 60
ORPHAN_SECONDS = 7 * 24 * 60 * 60
UNOWNED_ALIAS_SECONDS = 7 * 24 * 60 * 60

_KNOWN_IMAGE_EXTENSIONS = frozenset({"bmp", "gif", "jpeg", "jpg", "png", "webp"})
_ATOMIC_FILE_TEMP_RE = re.compile(r"^\.[^/\\]+\.[A-Za-z0-9_-]+\.tmp$")
_LEGACY_IMAGE_TEMP_RE = re.compile(
    r"^[^/\\]+\.tmp-[0-9]+-[0-9]+\.(?P<extension>[A-Za-z0-9]+)$"
)
_LIFECYCLE_QUARANTINE_RE = re.compile(
    r"^\.gc-lifecycle-(?P<nonce>[0-9a-f]{32})-"
    r"(?P<original>[A-Za-z0-9_-]+)\.hold$"
)
_MAX_QUARANTINE_FILENAME_BYTES = 240


class DiskPressureTier(str, Enum):
    HEALTHY = "healthy"
    SOFT = "soft"
    HARD = "hard"


class ArtifactClass(str, Enum):
    BROWSER_JOB = "browser_job"
    REFRESH_STAGING = "refresh_staging"
    ATOMIC_TEMP = "atomic_temp"
    ORPHAN_CACHE = "orphan_cache"
    OLD_REVISION_CACHE = "old_revision_cache"
    UNOWNED_ALIAS = "unowned_alias"
    PRESENTATION_CACHE = "presentation_cache"
    LIFECYCLE_QUARANTINE = "lifecycle_quarantine"


@dataclass(frozen=True)
class DiskThresholds:
    soft_min_free_bytes: int = 1024 * 1024 * 1024
    hard_min_free_bytes: int = 512 * 1024 * 1024
    soft_max_used_percent: float = 85.0
    hard_max_used_percent: float = 92.0


@dataclass(frozen=True)
class LifecycleBudget:
    """Absolute per-run limits shared by lifecycle-owned components."""

    max_scanned: int
    max_deleted: int
    max_deleted_bytes: int
    deadline_monotonic: float


@dataclass(frozen=True)
class CleanupBudget:
    """Tier configuration converted to one absolute shared run budget."""

    max_scanned_entries: int
    max_deleted_entries: int
    max_deleted_bytes: int
    max_duration_seconds: float

    def start(self, now_monotonic: float) -> LifecycleBudget:
        return LifecycleBudget(
            max_scanned=self.max_scanned_entries,
            max_deleted=self.max_deleted_entries,
            max_deleted_bytes=self.max_deleted_bytes,
            deadline_monotonic=(
                float(now_monotonic) + float(self.max_duration_seconds)
            ),
        )


@dataclass
class LifecycleAggregate:
    """Redacted counters that may be shared across maintenance components."""

    scanned_entries: int = 0
    candidate_entries: int = 0
    deleted_entries: int = 0
    deleted_bytes: int = 0
    retained_current: int = 0
    retained_last_good: int = 0
    retained_recent: int = 0
    skipped_unsafe: int = 0
    error_count: int = 0
    backlog_entries: int = 0


@dataclass
class LifecycleAllowance:
    """Mutable shared run allowance backed by one redacted aggregate."""

    budget: LifecycleBudget
    aggregate: LifecycleAggregate
    clock: Callable[[], float] = time.monotonic
    should_yield: Callable[[], bool] = lambda: False

    def consume_scan(self) -> bool:
        if self._must_stop() or self.aggregate.scanned_entries >= self.budget.max_scanned:
            self.mark_backlog()
            return False
        self.aggregate.scanned_entries += 1
        return True

    def can_delete(self, size: int) -> bool:
        if (
            self._must_stop()
            or self.aggregate.deleted_entries >= self.budget.max_deleted
            or size < 0
            or self.aggregate.deleted_bytes + size > self.budget.max_deleted_bytes
        ):
            self.mark_backlog()
            return False
        return True

    def consume_delete(self, size: int) -> None:
        self.aggregate.deleted_entries += 1
        self.aggregate.deleted_bytes += int(size)

    def mark_backlog(self, count: int = 1) -> None:
        self.aggregate.backlog_entries = max(
            self.aggregate.backlog_entries,
            max(1, int(count)),
        )

    def _must_stop(self) -> bool:
        try:
            return bool(self.should_yield()) or float(self.clock()) >= float(
                self.budget.deadline_monotonic
            )
        except Exception:
            self.aggregate.error_count += 1
            return True


@dataclass(frozen=True)
class FileStatToken:
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class CleanupCandidate:
    artifact_class: ArtifactClass
    path: Path
    stat_token: FileStatToken
    size: int
    age_seconds: float


@dataclass(frozen=True)
class CleanupPlan:
    candidates: tuple[CleanupCandidate, ...]
    scanned_entries: int
    retained_current: int
    retained_last_good: int
    retained_recent: int
    skipped_unsafe: int
    backlog_entries: int


@dataclass(frozen=True)
class CacheRetentionSnapshot:
    current_exact_paths: frozenset[Path]
    same_revision_last_good_paths: frozenset[Path]
    current_alias_paths: frozenset[Path]
    current_display_path: Path | None
    current_by_prefix: Mapping[str, tuple[int, int]]
    current_has_displayable: frozenset[str]
    current_exact_tokens: Mapping[Path, FileStatToken] = field(
        default_factory=lambda: MappingProxyType({})
    )
    ambiguous_prefixes: frozenset[str] = field(default_factory=frozenset)
    protected_presentation_paths: frozenset[Path] = field(
        default_factory=frozenset
    )
    pending_presentation_paths: frozenset[Path] = field(
        default_factory=frozenset
    )
    receipt_presentation_paths: frozenset[Path] = field(
        default_factory=frozenset
    )
    presentation_marker: tuple = ()


@dataclass(frozen=True)
class CacheLifecycleSnapshot:
    enabled: bool
    disk_tier: DiskPressureTier
    ran_at: str | None
    dry_run: bool
    scanned_entries: int
    candidate_entries: int
    deleted_entries: int
    deleted_bytes: int
    retained_current: int
    retained_last_good: int
    retained_recent: int
    skipped_unsafe: int
    error_count: int
    backlog_entries: int


HEALTHY_BUDGET = CleanupBudget(2048, 16, 16 * 1024 * 1024, 0.20)
SOFT_BUDGET = CleanupBudget(4096, 64, 128 * 1024 * 1024, 0.75)
HARD_BUDGET = CleanupBudget(8192, 128, 512 * 1024 * 1024, 2.00)

_DEFAULT_BUDGETS = MappingProxyType(
    {
        DiskPressureTier.HEALTHY: HEALTHY_BUDGET,
        DiskPressureTier.SOFT: SOFT_BUDGET,
        DiskPressureTier.HARD: HARD_BUDGET,
    }
)
_ARTIFACT_PRIORITY = MappingProxyType(
    {
        ArtifactClass.BROWSER_JOB: 0,
        ArtifactClass.LIFECYCLE_QUARANTINE: 1,
        ArtifactClass.REFRESH_STAGING: 2,
        ArtifactClass.ATOMIC_TEMP: 3,
        ArtifactClass.PRESENTATION_CACHE: 4,
        ArtifactClass.OLD_REVISION_CACHE: 5,
        ArtifactClass.ORPHAN_CACHE: 6,
        ArtifactClass.UNOWNED_ALIAS: 7,
    }
)


class CacheLifecycleManager:
    """Plan and remove only scheduler-owned direct-child render artifacts."""

    def __init__(
        self,
        plugin_image_dir,
        *,
        enabled: bool = True,
        budgets: Mapping[DiskPressureTier, CleanupBudget] | None = None,
        clock: Callable[[], float] = time.monotonic,
        presentation_marker_reader: Callable[[], object] | None = None,
    ) -> None:
        self.plugin_image_dir = Path(
            os.path.abspath(os.fspath(plugin_image_dir))
        )
        self.enabled = bool(enabled)
        self._budgets = MappingProxyType(
            dict(_DEFAULT_BUDGETS if budgets is None else budgets)
        )
        self._clock = clock
        self._presentation_marker_reader = presentation_marker_reader
        self._last_run_monotonic: float | None = None
        self._snapshot = self._snapshot_from_aggregate(
            DiskPressureTier.HEALTHY,
            None,
            False,
            LifecycleAggregate(),
        )

    def due(self, now_monotonic: float, tier: DiskPressureTier) -> bool:
        if not self.enabled:
            return False
        tier = DiskPressureTier(tier)
        if tier is not DiskPressureTier.HEALTHY:
            return True
        return (
            self._last_run_monotonic is None
            or float(now_monotonic) - self._last_run_monotonic
            >= CACHE_LIFECYCLE_INTERVAL_SECONDS
        )

    def plan(
        self,
        retention: CacheRetentionSnapshot,
        *,
        now_epoch,
        tier: DiskPressureTier,
    ) -> CleanupPlan:
        tier = DiskPressureTier(tier)
        aggregate = LifecycleAggregate()
        if not self.enabled:
            return self._cleanup_plan((), aggregate)
        started = float(self._clock())
        allowance = LifecycleAllowance(
            self._budget(tier).start(started),
            aggregate,
            clock=self._clock,
        )
        return self._plan(
            retention,
            now_epoch=float(now_epoch),
            allowance=allowance,
        )

    def maintain(
        self,
        retention: CacheRetentionSnapshot,
        *,
        now_epoch,
        now_monotonic,
        tier: DiskPressureTier,
        dry_run: bool = False,
        should_yield: Callable[[], bool] = lambda: False,
        allowance: LifecycleAllowance | None = None,
    ) -> CacheLifecycleSnapshot:
        tier = DiskPressureTier(tier)
        now_epoch = float(now_epoch)
        now_monotonic = float(now_monotonic)
        aggregate = (
            allowance.aggregate
            if allowance is not None
            else LifecycleAggregate()
        )
        if not self.enabled:
            snapshot = self._snapshot_from_aggregate(
                tier,
                self._iso_timestamp(now_epoch),
                bool(dry_run),
                aggregate,
            )
            self._snapshot = snapshot
            return snapshot

        if allowance is None:
            allowance = LifecycleAllowance(
                self._budget(tier).start(now_monotonic),
                aggregate,
                clock=self._clock,
                should_yield=should_yield,
            )
        root_identities: dict[Path, tuple[int, int]] = {}
        plan = self._plan(
            retention,
            now_epoch=now_epoch,
            allowance=allowance,
            root_identities=root_identities,
        )
        if not dry_run:
            for candidate in plan.candidates:
                if (
                    candidate.artifact_class
                    is ArtifactClass.LIFECYCLE_QUARANTINE
                ):
                    if not allowance.can_delete(0):
                        break
                    recovered = self._recover_quarantine(
                        candidate,
                        root_identities.get(candidate.path.parent),
                        aggregate,
                    )
                    if recovered is False:
                        allowance.mark_backlog()
                    continue
                if not allowance.can_delete(candidate.size):
                    break
                if (
                    candidate.artifact_class
                    is ArtifactClass.OLD_REVISION_CACHE
                    and candidate.age_seconds
                    <= OLD_REVISION_WITHOUT_CURRENT_SECONDS
                    and not self._old_revision_current_is_unchanged(
                        candidate,
                        retention,
                    )
                ):
                    aggregate.retained_recent += 1
                    allowance.mark_backlog()
                    continue
                if (
                    candidate.artifact_class
                    is ArtifactClass.PRESENTATION_CACHE
                    and not self._presentation_marker_is_fresh(
                        retention,
                        aggregate,
                    )
                ):
                    aggregate.skipped_unsafe += 1
                    allowance.mark_backlog()
                    continue
                before_skipped = aggregate.skipped_unsafe
                before_errors = aggregate.error_count
                deleted = self._unlink_candidate(
                    candidate,
                    root_identities.get(candidate.path.parent),
                    aggregate,
                    post_quarantine_check=lambda: (
                        self._post_quarantine_protection_is_fresh(
                            candidate,
                            retention,
                            aggregate,
                        )
                    ),
                )
                if deleted:
                    allowance.consume_delete(candidate.size)
                elif (
                    aggregate.skipped_unsafe > before_skipped
                    or aggregate.error_count > before_errors
                ):
                    allowance.mark_backlog()

        self._last_run_monotonic = now_monotonic
        snapshot = self._snapshot_from_aggregate(
            tier,
            self._iso_timestamp(now_epoch),
            bool(dry_run),
            aggregate,
        )
        self._snapshot = snapshot
        return snapshot

    def snapshot(self) -> CacheLifecycleSnapshot:
        return self._snapshot

    def _budget(self, tier: DiskPressureTier) -> CleanupBudget:
        try:
            budget = self._budgets[tier]
        except (KeyError, TypeError):
            return HARD_BUDGET
        return budget if isinstance(budget, CleanupBudget) else HARD_BUDGET

    def _plan(
        self,
        retention: CacheRetentionSnapshot,
        *,
        now_epoch: float,
        allowance: LifecycleAllowance,
        root_identities: dict[Path, tuple[int, int]] | None = None,
    ) -> CleanupPlan:
        candidates: list[CleanupCandidate] = []
        roots = (
            (self.plugin_image_dir / ".refresh-staging", "staging"),
            (self.plugin_image_dir / ".refresh-cache", "cache"),
            (
                self.plugin_image_dir / ".refresh-presentation",
                "presentation",
            ),
            (self.plugin_image_dir, "plugin"),
        )
        for root, root_kind in roots:
            completed = self._scan_root(
                root,
                root_kind,
                retention,
                now_epoch,
                allowance,
                candidates,
                root_identities,
            )
            if not completed:
                break
        candidates.sort(
            key=lambda candidate: (
                _ARTIFACT_PRIORITY[candidate.artifact_class],
                -candidate.age_seconds,
                candidate.path.name,
            )
        )
        return self._cleanup_plan(tuple(candidates), allowance.aggregate)

    def _scan_root(
        self,
        root: Path,
        root_kind: str,
        retention: CacheRetentionSnapshot,
        now_epoch: float,
        allowance: LifecycleAllowance,
        candidates: list[CleanupCandidate],
        root_identities: dict[Path, tuple[int, int]] | None,
    ) -> bool:
        root_fd = None
        scanner = None
        try:
            root_stat = os.lstat(root)
            if not _safe_directory_stat(root_stat):
                allowance.aggregate.skipped_unsafe += 1
                return True
            if os.name == "posix":
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                root_fd = os.open(root, flags)
                descriptor_stat = os.fstat(root_fd)
                if not _safe_directory_stat(descriptor_stat) or not _same_identity(
                    root_stat,
                    descriptor_stat,
                ):
                    allowance.aggregate.skipped_unsafe += 1
                    return True
                scanner = os.scandir(root_fd)
            else:
                second_stat = os.lstat(root)
                if not _same_identity(root_stat, second_stat):
                    allowance.aggregate.skipped_unsafe += 1
                    return True
                scanner = os.scandir(root)
            if root_identities is not None:
                root_identities[root] = (
                    int(root_stat.st_dev),
                    int(root_stat.st_ino),
                )

            for entry in scanner:
                if not allowance.consume_scan():
                    return False
                path = root / entry.name
                try:
                    entry_stat = (
                        entry.stat(follow_symlinks=False)
                        if os.name == "posix"
                        else os.lstat(path)
                    )
                except (OSError, TypeError, NotImplementedError):
                    allowance.aggregate.skipped_unsafe += 1
                    continue
                if not _safe_regular_file_stat(entry_stat):
                    allowance.aggregate.skipped_unsafe += 1
                    continue
                age_seconds = max(
                    0.0,
                    now_epoch - _mtime_seconds(entry_stat),
                )
                classified = self._classify_candidate(
                    path,
                    root_kind,
                    retention,
                    age_seconds,
                    allowance.aggregate,
                )
                if classified is None:
                    continue
                allowance.aggregate.candidate_entries += 1
                candidates.append(
                    CleanupCandidate(
                        artifact_class=classified,
                        path=path,
                        stat_token=_file_stat_token(entry_stat),
                        size=int(entry_stat.st_size),
                        age_seconds=age_seconds,
                    )
                )
            return True
        except FileNotFoundError:
            return True
        except (OSError, TypeError, NotImplementedError):
            allowance.aggregate.error_count += 1
            return True
        finally:
            if scanner is not None:
                try:
                    scanner.close()
                except (OSError, TypeError, NotImplementedError):
                    allowance.aggregate.error_count += 1
            if root_fd is not None:
                try:
                    os.close(root_fd)
                except (OSError, TypeError, NotImplementedError):
                    allowance.aggregate.error_count += 1

    def _classify_candidate(
        self,
        path: Path,
        root_kind: str,
        retention: CacheRetentionSnapshot,
        age_seconds: float,
        aggregate: LifecycleAggregate,
    ) -> ArtifactClass | None:
        if _parse_lifecycle_quarantine_filename(path.name) is not None:
            return ArtifactClass.LIFECYCLE_QUARANTINE
        if path in retention.same_revision_last_good_paths:
            aggregate.retained_last_good += 1
            return None
        if (
            path in retention.current_exact_paths
            or path in retention.current_alias_paths
            or path == retention.current_display_path
            or (
                root_kind == "plugin"
                and path.name == "current_image.png"
            )
        ):
            aggregate.retained_current += 1
            return None

        if _recognized_atomic_temp(path.name):
            if age_seconds > STALE_TEMP_SECONDS:
                return ArtifactClass.ATOMIC_TEMP
            aggregate.retained_recent += 1
            return None

        if root_kind == "staging":
            if parse_authoritative_cache_filename(path.name) is None:
                aggregate.skipped_unsafe += 1
                return None
            if age_seconds > STALE_TEMP_SECONDS:
                return ArtifactClass.REFRESH_STAGING
            aggregate.retained_recent += 1
            return None

        if root_kind == "presentation":
            return self._classify_presentation(
                path,
                retention,
                age_seconds,
                aggregate,
            )

        if root_kind == "cache":
            identity = parse_authoritative_cache_filename(path.name)
            if identity is None:
                aggregate.skipped_unsafe += 1
                return None
            identity_prefix = identity.uuid_hash_prefix
            revision = retention.current_by_prefix.get(identity_prefix)
            if (
                revision is not None
                and identity_prefix not in retention.ambiguous_prefixes
            ):
                grace = (
                    OLD_REVISION_SECONDS
                    if _current_prefix_still_displayable(
                        retention,
                        identity_prefix,
                    )
                    else OLD_REVISION_WITHOUT_CURRENT_SECONDS
                )
                if age_seconds > grace:
                    return ArtifactClass.OLD_REVISION_CACHE
                aggregate.retained_recent += 1
                return None
            if age_seconds > ORPHAN_SECONDS:
                return ArtifactClass.ORPHAN_CACHE
            aggregate.retained_recent += 1
            return None

        if root_kind == "plugin" and path.suffix.lower() == ".png":
            if age_seconds > UNOWNED_ALIAS_SECONDS:
                return ArtifactClass.UNOWNED_ALIAS
            aggregate.retained_recent += 1
            return None
        aggregate.skipped_unsafe += 1
        return None

    @staticmethod
    def _classify_presentation(
        path: Path,
        retention: CacheRetentionSnapshot,
        age_seconds: float,
        aggregate: LifecycleAggregate,
    ) -> ArtifactClass | None:
        if parse_prepared_presentation_filename(path.name) is None:
            aggregate.skipped_unsafe += 1
            return None
        if path in retention.receipt_presentation_paths:
            aggregate.retained_current += 1
            return None
        if path in retention.pending_presentation_paths:
            if age_seconds <= PRESENTATION_MAX_AGE_SECONDS:
                aggregate.retained_current += 1
                return None
            return ArtifactClass.PRESENTATION_CACHE
        if path in retention.protected_presentation_paths:
            aggregate.retained_current += 1
            return None
        if age_seconds > PRESENTATION_MAX_AGE_SECONDS:
            return ArtifactClass.PRESENTATION_CACHE
        aggregate.retained_recent += 1
        return None

    def _presentation_marker_is_fresh(
        self,
        retention: CacheRetentionSnapshot,
        aggregate: LifecycleAggregate,
    ) -> bool:
        if self._presentation_marker_reader is None:
            return False
        try:
            fresh = self._presentation_marker_reader()
            if isinstance(fresh, Mapping):
                fresh_marker = _presentation_marker_from_runtime(fresh)
            else:
                fresh_marker = tuple(fresh)
            return fresh_marker == retention.presentation_marker
        except Exception:
            aggregate.error_count += 1
            return False

    @staticmethod
    def _old_revision_current_is_unchanged(
        candidate: CleanupCandidate,
        retention: CacheRetentionSnapshot,
    ) -> bool:
        identity = parse_authoritative_cache_filename(candidate.path.name)
        return (
            identity is not None
            and _current_prefix_still_displayable(
                retention,
                identity.uuid_hash_prefix,
            )
        )

    def _post_quarantine_protection_is_fresh(
        self,
        candidate: CleanupCandidate,
        retention: CacheRetentionSnapshot,
        aggregate: LifecycleAggregate,
    ) -> bool:
        if (
            candidate.artifact_class is ArtifactClass.OLD_REVISION_CACHE
            and candidate.age_seconds
            <= OLD_REVISION_WITHOUT_CURRENT_SECONDS
            and not self._old_revision_current_is_unchanged(
                candidate,
                retention,
            )
        ):
            aggregate.retained_recent += 1
            return False
        if (
            candidate.artifact_class is ArtifactClass.PRESENTATION_CACHE
            and not self._presentation_marker_is_fresh(
                retention,
                aggregate,
            )
        ):
            return False
        return True

    def _unlink_candidate(
        self,
        candidate: CleanupCandidate,
        root_identity: tuple[int, int] | None,
        aggregate: LifecycleAggregate,
        *,
        post_quarantine_check: Callable[[], bool] = lambda: True,
    ) -> bool:
        if root_identity is None:
            aggregate.skipped_unsafe += 1
            return False
        try:
            if os.name == "posix":
                deleted = self._unlink_candidate_posix(
                    candidate,
                    root_identity,
                    post_quarantine_check,
                )
            else:
                deleted = self._unlink_candidate_fallback(
                    candidate,
                    root_identity,
                    post_quarantine_check,
                )
            if not deleted:
                aggregate.skipped_unsafe += 1
            return deleted
        except FileNotFoundError:
            return False
        except (OSError, TypeError, NotImplementedError):
            aggregate.error_count += 1
            return False

    def _recover_quarantine(
        self,
        candidate: CleanupCandidate,
        root_identity: tuple[int, int] | None,
        aggregate: LifecycleAggregate,
    ) -> bool | None:
        original_name = _parse_lifecycle_quarantine_filename(
            candidate.path.name
        )
        if root_identity is None or original_name is None:
            aggregate.skipped_unsafe += 1
            return False
        try:
            if os.name == "posix":
                recovered = self._recover_quarantine_posix(
                    candidate,
                    root_identity,
                    original_name,
                )
            else:
                recovered = self._recover_quarantine_fallback(
                    candidate,
                    root_identity,
                    original_name,
                )
            if not recovered:
                aggregate.skipped_unsafe += 1
            return recovered
        except FileNotFoundError:
            return None
        except (OSError, TypeError, NotImplementedError):
            aggregate.error_count += 1
            return False

    @classmethod
    def _unlink_candidate_posix(
        cls,
        candidate: CleanupCandidate,
        root_identity: tuple[int, int],
        post_quarantine_check: Callable[[], bool] = lambda: True,
    ) -> bool:
        quarantine_name = _lifecycle_quarantine_name(candidate.path.name)
        if quarantine_name is None:
            return False
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        root_fd = os.open(candidate.path.parent, flags)
        committed = False
        quarantined = False
        quarantine_token = None
        try:
            root_stat = os.fstat(root_fd)
            if (
                not _safe_directory_stat(root_stat)
                or _stat_identity(root_stat) != root_identity
            ):
                return False
            current = os.stat(
                candidate.path.name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if (
                not _safe_regular_file_stat(current)
                or _file_stat_token(current) != candidate.stat_token
            ):
                return False
            os.rename(
                candidate.path.name,
                quarantine_name,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
            )
            quarantined = True
            moved = os.stat(
                quarantine_name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if _safe_regular_file_stat(moved):
                quarantine_token = _file_stat_token(moved)
            if (
                quarantine_token != candidate.stat_token
                or not post_quarantine_check()
            ):
                cls._restore_quarantine_posix(
                    root_fd,
                    quarantine_name,
                    candidate.path.name,
                    quarantine_token,
                )
                quarantined = False
                return False
            try:
                os.unlink(quarantine_name, dir_fd=root_fd)
            except BaseException:
                cls._restore_quarantine_posix(
                    root_fd,
                    quarantine_name,
                    candidate.path.name,
                    quarantine_token,
                )
                quarantined = False
                raise
            quarantined = False
            committed = True
        except BaseException:
            if quarantined:
                try:
                    cls._restore_quarantine_posix(
                        root_fd,
                        quarantine_name,
                        candidate.path.name,
                        quarantine_token,
                    )
                except BaseException:
                    pass
            raise
        finally:
            try:
                os.close(root_fd)
            except OSError:
                if not committed:
                    raise
        return True

    @staticmethod
    def _restore_quarantine_posix(
        root_fd: int,
        quarantine_name: str,
        original_name: str,
        expected_token: FileStatToken | None,
    ) -> bool:
        quarantine_stat = os.stat(
            quarantine_name,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        if not _safe_regular_file_stat(quarantine_stat):
            return False
        quarantine_token = _file_stat_token(quarantine_stat)
        if expected_token is not None and quarantine_token != expected_token:
            return False
        try:
            os.link(
                quarantine_name,
                original_name,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False
        original_stat = os.stat(
            original_name,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        quarantine_after = os.stat(
            quarantine_name,
            dir_fd=root_fd,
            follow_symlinks=False,
        )
        if (
            not _safe_regular_file_stat(original_stat)
            or not _safe_regular_file_stat(quarantine_after)
            or not _same_identity(original_stat, quarantine_after)
            or _file_stat_token(quarantine_after) != quarantine_token
        ):
            return False
        os.unlink(quarantine_name, dir_fd=root_fd)
        return True

    @classmethod
    def _recover_quarantine_posix(
        cls,
        candidate: CleanupCandidate,
        root_identity: tuple[int, int],
        original_name: str,
    ) -> bool:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        root_fd = os.open(candidate.path.parent, flags)
        recovered = False
        try:
            root_stat = os.fstat(root_fd)
            if (
                not _safe_directory_stat(root_stat)
                or _stat_identity(root_stat) != root_identity
            ):
                return False
            quarantine_stat = os.stat(
                candidate.path.name,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
            if (
                not _safe_regular_file_stat(quarantine_stat)
                or _file_stat_token(quarantine_stat)
                != candidate.stat_token
            ):
                return False
            recovered = cls._restore_quarantine_posix(
                root_fd,
                candidate.path.name,
                original_name,
                candidate.stat_token,
            )
            return recovered
        finally:
            try:
                os.close(root_fd)
            except OSError:
                if not recovered:
                    raise

    @classmethod
    def _unlink_candidate_fallback(
        cls,
        candidate: CleanupCandidate,
        root_identity: tuple[int, int],
        post_quarantine_check: Callable[[], bool] = lambda: True,
    ) -> bool:
        quarantine_name = _lifecycle_quarantine_name(candidate.path.name)
        if quarantine_name is None:
            return False
        root = candidate.path.parent
        quarantine_path = root / quarantine_name
        root_stat = os.lstat(root)
        if (
            not _safe_directory_stat(root_stat)
            or _stat_identity(root_stat) != root_identity
        ):
            return False
        resolved_root = Path(os.path.abspath(os.path.realpath(root)))
        if os.path.normcase(str(resolved_root)) != os.path.normcase(str(root)):
            return False
        current = os.lstat(candidate.path)
        if (
            not _safe_regular_file_stat(current)
            or _file_stat_token(current) != candidate.stat_token
        ):
            return False
        root_after = os.lstat(root)
        if _stat_identity(root_after) != root_identity:
            return False
        quarantined = False
        quarantine_token = None
        try:
            os.rename(candidate.path, quarantine_path)
            quarantined = True
            moved = os.lstat(quarantine_path)
            if _safe_regular_file_stat(moved):
                quarantine_token = _file_stat_token(moved)
            root_after = os.lstat(root)
            if (
                _stat_identity(root_after) != root_identity
                or quarantine_token != candidate.stat_token
                or not post_quarantine_check()
            ):
                cls._restore_quarantine_fallback(
                    quarantine_path,
                    candidate.path,
                    quarantine_token,
                    root_identity,
                )
                quarantined = False
                return False
            try:
                os.unlink(quarantine_path)
            except BaseException:
                cls._restore_quarantine_fallback(
                    quarantine_path,
                    candidate.path,
                    quarantine_token,
                    root_identity,
                )
                quarantined = False
                raise
            quarantined = False
        except BaseException:
            if quarantined:
                try:
                    cls._restore_quarantine_fallback(
                        quarantine_path,
                        candidate.path,
                        quarantine_token,
                        root_identity,
                    )
                except BaseException:
                    pass
            raise
        return True

    @staticmethod
    def _restore_quarantine_fallback(
        quarantine_path: Path,
        original_path: Path,
        expected_token: FileStatToken | None,
        root_identity: tuple[int, int],
    ) -> bool:
        root = quarantine_path.parent
        root_stat = os.lstat(root)
        if (
            not _safe_directory_stat(root_stat)
            or _stat_identity(root_stat) != root_identity
        ):
            return False
        quarantine_stat = os.lstat(quarantine_path)
        if not _safe_regular_file_stat(quarantine_stat):
            return False
        quarantine_token = _file_stat_token(quarantine_stat)
        if expected_token is not None and quarantine_token != expected_token:
            return False
        try:
            os.rename(quarantine_path, original_path)
        except FileExistsError:
            return False
        restored = os.lstat(original_path)
        root_after = os.lstat(root)
        return (
            _stat_identity(root_after) == root_identity
            and _safe_regular_file_stat(restored)
            and _file_stat_token(restored) == quarantine_token
        )

    @classmethod
    def _recover_quarantine_fallback(
        cls,
        candidate: CleanupCandidate,
        root_identity: tuple[int, int],
        original_name: str,
    ) -> bool:
        return cls._restore_quarantine_fallback(
            candidate.path,
            candidate.path.parent / original_name,
            candidate.stat_token,
            root_identity,
        )

    @staticmethod
    def _cleanup_plan(
        candidates: tuple[CleanupCandidate, ...],
        aggregate: LifecycleAggregate,
    ) -> CleanupPlan:
        return CleanupPlan(
            candidates=candidates,
            scanned_entries=aggregate.scanned_entries,
            retained_current=aggregate.retained_current,
            retained_last_good=aggregate.retained_last_good,
            retained_recent=aggregate.retained_recent,
            skipped_unsafe=aggregate.skipped_unsafe,
            backlog_entries=aggregate.backlog_entries,
        )

    def _snapshot_from_aggregate(
        self,
        tier: DiskPressureTier,
        ran_at: str | None,
        dry_run: bool,
        aggregate: LifecycleAggregate,
    ) -> CacheLifecycleSnapshot:
        return CacheLifecycleSnapshot(
            enabled=self.enabled,
            disk_tier=tier,
            ran_at=ran_at,
            dry_run=dry_run,
            scanned_entries=aggregate.scanned_entries,
            candidate_entries=aggregate.candidate_entries,
            deleted_entries=aggregate.deleted_entries,
            deleted_bytes=aggregate.deleted_bytes,
            retained_current=aggregate.retained_current,
            retained_last_good=aggregate.retained_last_good,
            retained_recent=aggregate.retained_recent,
            skipped_unsafe=aggregate.skipped_unsafe,
            error_count=aggregate.error_count,
            backlog_entries=aggregate.backlog_entries,
        )

    @staticmethod
    def _iso_timestamp(now_epoch: float) -> str:
        return datetime.fromtimestamp(
            now_epoch,
            tz=timezone.utc,
        ).isoformat()


def classify_disk_pressure(
    total_bytes,
    used_bytes,
    free_bytes,
    thresholds: DiskThresholds,
) -> DiskPressureTier:
    """Classify hard-first and fail closed when disk telemetry is invalid."""

    try:
        if any(isinstance(value, bool) for value in (total_bytes, used_bytes, free_bytes)):
            raise ValueError
        total = float(total_bytes)
        used = float(used_bytes)
        free = float(free_bytes)
    except (TypeError, ValueError, OverflowError):
        return DiskPressureTier.HARD
    if (
        not all(math.isfinite(value) for value in (total, used, free))
        or total <= 0
        or used < 0
        or free < 0
    ):
        return DiskPressureTier.HARD
    used_percent = used * 100.0 / total
    if (
        free < thresholds.hard_min_free_bytes
        or used_percent >= thresholds.hard_max_used_percent
    ):
        return DiskPressureTier.HARD
    if (
        free < thresholds.soft_min_free_bytes
        or used_percent >= thresholds.soft_max_used_percent
    ):
        return DiskPressureTier.SOFT
    return DiskPressureTier.HEALTHY


def build_cache_retention(
    plugin_image_dir: Path,
    instances: tuple,
    runtime_instances: Mapping,
    current_display_path: Path | None,
) -> CacheRetentionSnapshot:
    """Build one detached retain set from immutable model/runtime snapshots."""

    plugin_root = Path(os.path.abspath(os.fspath(plugin_image_dir)))
    cache_root = plugin_root / ".refresh-cache"
    current_exact_paths: set[Path] = set()
    last_good_paths: set[Path] = set()
    alias_paths: set[Path] = set()
    current_by_prefix: dict[str, tuple[int, int]] = {}
    ambiguous_prefixes: set[str] = set()
    pending_presentation_paths: set[Path] = set()
    receipt_presentation_paths: set[Path] = set()
    presentation_root = plugin_root / ".refresh-presentation"

    for instance in instances:
        prefix = cache_identity_prefix(instance.instance_uuid)
        revision = (
            int(instance.structural_generation),
            int(instance.settings_revision),
        )
        prior = current_by_prefix.get(prefix)
        if prior is not None and prior != revision:
            ambiguous_prefixes.add(prefix)
            current_by_prefix.pop(prefix, None)
        elif prefix not in ambiguous_prefixes:
            current_by_prefix[prefix] = revision

        for theme_mode in (None, "day", "night"):
            current_exact_paths.add(
                Path(
                    authoritative_cache_path(
                        cache_root,
                        instance.instance_uuid,
                        instance.structural_generation,
                        instance.settings_revision,
                        theme_mode,
                    )
                )
            )

        alias = _direct_child_path(
            plugin_root,
            f"{instance.plugin_id}_{instance.name.replace(' ', '_')}.png",
        )
        if alias is not None:
            alias_paths.add(alias)

        runtime = runtime_instances.get(instance.instance_uuid)
        last_good = getattr(runtime, "last_good_cache", None)
        if (
            last_good is not None
            and last_good.structural_generation == instance.structural_generation
            and last_good.settings_revision == instance.settings_revision
        ):
            last_good_paths.add(
                Path(
                    authoritative_cache_path(
                        cache_root,
                        instance.instance_uuid,
                        instance.structural_generation,
                        instance.settings_revision,
                        last_good.theme_mode,
                    )
                )
            )

        request = getattr(runtime, "presentation_request", None)
        if (
            request is not None
            and request.structural_generation == instance.structural_generation
            and request.settings_revision == instance.settings_revision
            and request.prepared_at is not None
            and request.prepared_theme_mode in {None, "day", "night"}
        ):
            try:
                pending_presentation_paths.add(
                    Path(
                        prepared_presentation_path(
                            presentation_root,
                            instance.instance_uuid,
                            instance.structural_generation,
                            instance.settings_revision,
                            request.prepared_theme_mode,
                            request.request_id,
                        )
                    )
                )
            except (OSError, TypeError, ValueError):
                pass

        receipt = getattr(runtime, "presentation_receipt", None)
        if (
            receipt is not None
            and receipt.structural_generation == instance.structural_generation
            and receipt.settings_revision == instance.settings_revision
        ):
            try:
                receipt_presentation_paths.add(
                    Path(
                        prepared_presentation_path(
                            presentation_root,
                            instance.instance_uuid,
                            instance.structural_generation,
                            instance.settings_revision,
                            receipt.theme_mode,
                            receipt.request_id,
                        )
                    )
                )
            except (OSError, TypeError, ValueError):
                pass

    current_exact_tokens = {
        path: token
        for path in current_exact_paths
        for token in (_safe_existing_regular_file_token(path),)
        if token is not None
    }
    displayable_prefixes = frozenset(
        path.name[:32] for path in current_exact_tokens
    )
    normalized_display = None
    if current_display_path is not None:
        try:
            normalized_display = Path(
                os.path.abspath(os.fspath(current_display_path))
            )
        except (OSError, TypeError, ValueError):
            normalized_display = None

    protected_presentation_paths = (
        pending_presentation_paths | receipt_presentation_paths
    )
    return CacheRetentionSnapshot(
        current_exact_paths=frozenset(current_exact_paths),
        same_revision_last_good_paths=frozenset(last_good_paths),
        current_alias_paths=frozenset(alias_paths),
        current_display_path=normalized_display,
        current_by_prefix=MappingProxyType(dict(current_by_prefix)),
        current_has_displayable=displayable_prefixes,
        current_exact_tokens=MappingProxyType(current_exact_tokens),
        ambiguous_prefixes=frozenset(ambiguous_prefixes),
        protected_presentation_paths=frozenset(
            protected_presentation_paths
        ),
        pending_presentation_paths=frozenset(pending_presentation_paths),
        receipt_presentation_paths=frozenset(receipt_presentation_paths),
        presentation_marker=_presentation_marker_from_runtime(
            runtime_instances
        ),
    )


def _presentation_marker_from_runtime(runtime_instances: Mapping) -> tuple:
    marker = []
    try:
        items = sorted(runtime_instances.items(), key=lambda item: str(item[0]))
    except (AttributeError, TypeError, ValueError):
        return (("invalid",),)
    for instance_uuid, state in items:
        request = getattr(state, "presentation_request", None)
        receipt = getattr(state, "presentation_receipt", None)
        marker.append(
            (
                str(instance_uuid),
                _presentation_request_marker(request),
                _presentation_receipt_marker(receipt),
            )
        )
    return tuple(marker)


def _presentation_request_marker(request) -> tuple | None:
    if request is None:
        return None
    return (
        getattr(request, "request_id", None),
        getattr(request, "requested_at", None),
        getattr(request, "structural_generation", None),
        getattr(request, "settings_revision", None),
        getattr(request, "origin_theme_mode", None),
        getattr(request, "origin_display_commit_id", None),
        getattr(request, "prepared_at", None),
        getattr(request, "prepared_theme_mode", None),
    )


def _presentation_receipt_marker(receipt) -> tuple | None:
    if receipt is None:
        return None
    return (
        getattr(receipt, "request_id", None),
        getattr(receipt, "committed_at", None),
        getattr(receipt, "display_commit_id", None),
        getattr(receipt, "structural_generation", None),
        getattr(receipt, "settings_revision", None),
        getattr(receipt, "theme_mode", None),
    )


def _direct_child_path(root: Path, filename: str) -> Path | None:
    try:
        candidate = Path(os.path.abspath(os.fspath(root / filename)))
    except (OSError, TypeError, ValueError):
        return None
    return candidate if candidate.parent == root else None


def _recognized_atomic_temp(filename: str) -> bool:
    if _ATOMIC_FILE_TEMP_RE.fullmatch(filename) is not None:
        return True
    match = _LEGACY_IMAGE_TEMP_RE.fullmatch(filename)
    return (
        match is not None
        and match.group("extension").lower() in _KNOWN_IMAGE_EXTENSIONS
    )


def _lifecycle_quarantine_name(
    original_filename: str,
    *,
    nonce: str | None = None,
) -> str | None:
    if (
        not isinstance(original_filename, str)
        or not original_filename
        or os.path.basename(original_filename) != original_filename
    ):
        return None
    encoded = base64.urlsafe_b64encode(
        original_filename.encode("utf-8")
    ).decode("ascii").rstrip("=")
    nonce = secrets.token_hex(16) if nonce is None else nonce
    if re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        return None
    name = f".gc-lifecycle-{nonce}-{encoded}.hold"
    if len(name.encode("utf-8")) > _MAX_QUARANTINE_FILENAME_BYTES:
        return None
    return name


def _parse_lifecycle_quarantine_filename(filename: str) -> str | None:
    if not isinstance(filename, str) or os.path.basename(filename) != filename:
        return None
    match = _LIFECYCLE_QUARANTINE_RE.fullmatch(filename)
    if match is None:
        return None
    encoded = match.group("original")
    padding = "=" * (-len(encoded) % 4)
    try:
        original = base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    if (
        not original
        or os.path.basename(original) != original
        or _lifecycle_quarantine_name(
            original,
            nonce=match.group("nonce"),
        )
        != filename
    ):
        return None
    return original


def _current_prefix_still_displayable(
    retention: CacheRetentionSnapshot,
    identity_prefix: str,
) -> bool:
    if identity_prefix not in retention.current_has_displayable:
        return False
    return any(
        path.name.startswith(f"{identity_prefix}-")
        and _safe_existing_regular_file_token(path) == token
        for path, token in retention.current_exact_tokens.items()
    )


def _stat_identity(value) -> tuple[int, int]:
    return int(value.st_dev), int(value.st_ino)


def _same_identity(left, right) -> bool:
    return _stat_identity(left) == _stat_identity(right)


def _mtime_ns(value) -> int:
    raw = getattr(value, "st_mtime_ns", None)
    if raw is not None:
        return int(raw)
    return int(float(value.st_mtime) * 1_000_000_000)


def _mtime_seconds(value) -> float:
    return _mtime_ns(value) / 1_000_000_000.0


def _file_stat_token(value) -> FileStatToken:
    return FileStatToken(
        device=int(value.st_dev),
        inode=int(value.st_ino),
        size=int(value.st_size),
        mtime_ns=_mtime_ns(value),
    )


def _has_reparse_point(value) -> bool:
    reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(value, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _safe_directory_stat(value) -> bool:
    return (
        stat_module.S_ISDIR(value.st_mode)
        and not stat_module.S_ISLNK(value.st_mode)
        and not _has_reparse_point(value)
    )


def _safe_regular_file_stat(value) -> bool:
    return (
        stat_module.S_ISREG(value.st_mode)
        and not stat_module.S_ISLNK(value.st_mode)
        and not _has_reparse_point(value)
    )


def _safe_existing_regular_file_token(path: Path) -> FileStatToken | None:
    try:
        value = os.lstat(path)
    except OSError:
        return None
    if not _safe_regular_file_stat(value):
        return None
    return _file_stat_token(value)
