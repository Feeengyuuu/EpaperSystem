"""Bounded image normalization with a serial safety path.

Only primitive work descriptors cross the process boundary.  Workers may write
new PNG files inside the caller-owned staging directory; the parent validates
every artifact before exposing it to callers.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import multiprocessing
import os
from pathlib import Path
import stat
import threading
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import uuid4

from PIL import Image, ImageOps

from .long_task_executor import InstanceIdentity
from .refresh_contracts import TaskCancelled, TaskContext
from .resource_deferral import ResourcePressureDeferred
from .resource_governor import ResourceLease, RuntimeResourceGovernor
from utils.safe_image import ImageLimits, safe_open_image


DEFAULT_MAX_SOURCE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_WIDTH = 8192
DEFAULT_MAX_HEIGHT = 8192
DEFAULT_MAX_PIXELS = 8_000_000
DEFAULT_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
DEFAULT_POLL_INTERVAL_SECONDS = 0.02
DEFAULT_TERMINATE_GRACE_SECONDS = 0.25
HARD_MIN_AVAILABLE_MB = 70.0
HARD_MAX_SWAP_PERCENT = 75.0
SOFT_MAX_SWAP_PERCENT = 70.0
_DESCRIPTOR_KEYS = frozenset(
    {
        "ordinal",
        "source_path",
        "source_bytes",
        "source_sha256",
        "target_width",
        "target_height",
    }
)


class InvalidImageWorkset(ValueError):
    """The workset cannot safely cross or return from the child boundary."""


class StaleImageWorksetError(RuntimeError):
    """The plugin identity changed before staged artifacts could be accepted."""


class ParallelStageProcessLeak(RuntimeError):
    """A parallel image child survived the bounded terminate/kill sequence."""

    def __init__(self, message: str, *, process=None):
        super().__init__(message)
        self.process = process


class _ParallelStageAdmission:
    """One opaque, single-use lease held across adapter workset construction."""

    def __init__(self, owner: BoundedParallelStageRunner, lease: ResourceLease):
        self._owner = owner
        self._lease = lease
        self._lock = threading.Lock()
        self._claimed = False
        self._retained = False

    def claim(self, owner: BoundedParallelStageRunner) -> ResourceLease:
        with self._lock:
            if owner is not self._owner or self._claimed or self._lease.released:
                raise RuntimeError("parallel image admission is invalid or already used")
            self._claimed = True
            return self._lease

    def release(self) -> None:
        with self._lock:
            if self._retained:
                return
        self._lease.release()

    def retain_for_quarantine(self) -> None:
        with self._lock:
            if not self._claimed:
                raise RuntimeError("parallel image admission was not claimed")
            self._retained = True


class _ParallelLeaseGuard:
    """Release normal leases, but quarantine capacity with a surviving child."""

    def __init__(self, owner, lease, admission):
        self._owner = owner
        self._lease = lease
        self._admission = admission

    def __enter__(self):
        return self._lease

    def __exit__(self, exc_type, exc_value, _traceback):
        if (
            isinstance(exc_value, ParallelStageProcessLeak)
            and self._lease.parallel
            and exc_value.process is not None
        ):
            if self._admission is not None:
                self._admission.retain_for_quarantine()
            self._owner._quarantine_process(exc_value.process, self._lease)
            return False
        self._lease.release()
        return False


@dataclass(frozen=True)
class PreparedImageArtifact:
    ordinal: int
    path: str
    sha256: str
    byte_size: int
    width: int
    height: int
    image_format: str = "PNG"

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int):
            raise TypeError("artifact ordinal must be an integer")
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("artifact path must be non-empty")
        _require_sha256(self.sha256, "artifact sha256")
        for name in ("byte_size", "width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"artifact {name} must be a positive integer")
        if self.image_format != "PNG":
            raise ValueError("prepared artifacts must be PNG")


@dataclass(frozen=True)
class ImmutableImageWorkset:
    descriptors: tuple[Mapping[str, Any], ...]
    staging_dir: str
    instance_identity: InstanceIdentity
    source_roots: tuple[str, ...] = ()
    transform: str = "normalize_png"
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    max_width: int = DEFAULT_MAX_WIDTH
    max_height: int = DEFAULT_MAX_HEIGHT
    max_pixels: int = DEFAULT_MAX_PIXELS
    _primitive_descriptors: tuple[Mapping[str, Any], ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.instance_identity, InstanceIdentity):
            raise TypeError("instance_identity must be an InstanceIdentity")
        if not isinstance(self.staging_dir, str) or not self.staging_dir:
            raise ValueError("staging_dir must be a non-empty absolute path")
        if not Path(self.staging_dir).is_absolute():
            raise ValueError("staging_dir must be an absolute path")
        if self.transform != "normalize_png":
            raise ValueError("unknown image stage transform")
        for name in ("max_source_bytes", "max_width", "max_height", "max_pixels"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        roots = tuple(str(Path(root)) for root in self.source_roots)
        if any(not Path(root).is_absolute() for root in roots):
            raise ValueError("source_roots must contain only absolute paths")
        object.__setattr__(self, "source_roots", roots)

        if not isinstance(self.descriptors, tuple) or not self.descriptors:
            raise ValueError("descriptors must be a non-empty tuple")
        frozen = tuple(_freeze_descriptor(item) for item in self.descriptors)
        for descriptor in frozen:
            if "target_width" in descriptor:
                target_width = descriptor["target_width"]
                target_height = descriptor["target_height"]
                if target_width > self.max_width or target_height > self.max_height:
                    raise ValueError("descriptor target dimension exceeds workset limits")
                if target_width * target_height > self.max_pixels:
                    raise ValueError("descriptor target pixel count exceeds workset limits")
        ordinals = tuple(item["ordinal"] for item in frozen)
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("descriptor ordinals must be unique")
        object.__setattr__(self, "descriptors", frozen)
        object.__setattr__(self, "_primitive_descriptors", frozen)

    def primitive_payload(self) -> Mapping[str, Any]:
        return {
            "descriptors": tuple(dict(item) for item in self._primitive_descriptors),
            "staging_dir": self.staging_dir,
            "source_roots": self.source_roots,
            "transform": self.transform,
            "max_source_bytes": self.max_source_bytes,
            "max_width": self.max_width,
            "max_height": self.max_height,
            "max_pixels": self.max_pixels,
        }


class BoundedParallelStageRunner:
    """Normalize one immutable workset with a resource-governed worker tier."""

    def __init__(
        self,
        *,
        governor: RuntimeResourceGovernor | None = None,
        start_method: str = "spawn",
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        terminate_grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS,
    ):
        self._governor = governor or RuntimeResourceGovernor()
        self._mp = multiprocessing.get_context(start_method)
        self._poll_interval = max(0.005, float(poll_interval_seconds))
        self._terminate_grace = max(0.01, float(terminate_grace_seconds))
        self._active_lock = threading.Lock()
        self._active: set[Any] = set()
        self._quarantined_leases: dict[Any, ResourceLease] = {}
        self._snapshot_lock = threading.Lock()
        self._cancellation_count = 0
        self._admission_tier_counts = {
            "serial": 0,
            "2_worker": 0,
            "3_worker": 0,
        }
        self._serial_fallback_reason_counts: dict[str, int] = {}
        self._batch_count = 0
        self._batch_duration_ms_total = 0.0
        self._normalized_work_pixels_total = 0
        self._child_peak_rss_bytes = None
        self._last_run_snapshot: dict[str, Any] = {
            "worker_count": 1,
            "reason": "not_run",
            "parallel": False,
            "batch_duration_ms": 0.0,
            "child_pid": None,
            "worker_thread_count": 0,
            "canceled": False,
            "status": "not_run",
            "cancellation_count": 0,
            "child_peak_rss_bytes": None,
        }

    @property
    def active_processes(self) -> tuple[int, ...]:
        self._reap_quarantined_processes()
        with self._active_lock:
            return tuple(
                sorted(
                    process.pid
                    for process in self._active
                    if process.pid is not None and process.is_alive()
                )
            )

    @property
    def last_run_snapshot(self) -> Mapping[str, Any]:
        with self._snapshot_lock:
            return dict(self._last_run_snapshot)

    @property
    def cumulative_snapshot(self) -> Mapping[str, Any]:
        """Return process-lifetime image-stage counters without job identity."""

        with self._snapshot_lock:
            return {
                "admission_tier_counts": dict(self._admission_tier_counts),
                "serial_fallback_reason_counts": dict(
                    self._serial_fallback_reason_counts
                ),
                "batch_count": self._batch_count,
                "batch_duration_ms_total": self._batch_duration_ms_total,
                "normalized_work_pixels_total": (
                    self._normalized_work_pixels_total
                ),
                "child_peak_rss_bytes": self._child_peak_rss_bytes,
                "cancellation_count": self._cancellation_count,
            }

    def run(
        self,
        workset: ImmutableImageWorkset,
        context: TaskContext,
        identity_validator: Callable[[InstanceIdentity], bool],
    ) -> tuple[PreparedImageArtifact, ...]:
        result = self._run(
            workset,
            context,
            identity_validator,
            parallel_only=False,
        )
        if result is None:  # pragma: no cover - guarded by ``parallel_only``.
            raise RuntimeError("serial image stage unexpectedly declined work")
        return result

    def run_parallel_only(
        self,
        workset: ImmutableImageWorkset,
        context: TaskContext,
        identity_validator: Callable[[InstanceIdentity], bool],
        *,
        lease: _ParallelStageAdmission | None = None,
    ) -> tuple[PreparedImageArtifact, ...] | None:
        """Run only when the governor grants a true parallel tier.

        ``None`` is an admission result, not an error.  It is returned before
        source descriptors are hashed or decoded so plugin adapters can invoke
        their established serial bank loader without paying staging costs.
        """

        return self._run(
            workset,
            context,
            identity_validator,
            parallel_only=True,
            admission=lease,
        )

    def acquire_parallel_lease(
        self,
        context: TaskContext,
    ) -> _ParallelStageAdmission | None:
        """Reserve a real 2/3-worker tier before adapters touch bank media."""

        if not isinstance(context, TaskContext):
            raise TypeError("context must be a TaskContext")
        context.raise_if_cancelled()
        self._reap_quarantined_processes()
        started = time.monotonic()
        lease = self._governor.acquire(
            "parallel_image_batch",
            {"max_workers": 3},
            context,
        )
        self._record_admission(lease)
        if lease.parallel:
            return _ParallelStageAdmission(self, lease)
        lease.release()
        with self._snapshot_lock:
            self._last_run_snapshot = {
                "worker_count": 1,
                "reason": lease.reason,
                "parallel": False,
                "batch_duration_ms": max(
                    0.0,
                    (time.monotonic() - started) * 1000.0,
                ),
                "child_pid": None,
                "worker_thread_count": 0,
                "canceled": False,
                "status": "not_run",
                "cancellation_count": self._cancellation_count,
                "child_peak_rss_bytes": None,
            }
        return None

    def _run(
        self,
        workset: ImmutableImageWorkset,
        context: TaskContext,
        identity_validator: Callable[[InstanceIdentity], bool],
        *,
        parallel_only: bool,
        admission: _ParallelStageAdmission | None = None,
    ) -> tuple[PreparedImageArtifact, ...] | None:
        if not isinstance(workset, ImmutableImageWorkset):
            raise TypeError("workset must be an ImmutableImageWorkset")
        if not isinstance(context, TaskContext):
            raise TypeError("context must be a TaskContext")
        if not callable(identity_validator):
            raise TypeError("identity_validator must be callable")
        context.raise_if_cancelled()
        _require_current_identity(workset.instance_identity, identity_validator)
        staging_dir = _validate_staging_dir(workset.staging_dir)
        self._reap_quarantined_processes()
        started = time.monotonic()
        created_paths: list[Path] = []
        if admission is None:
            lease = self._governor.acquire(
                "parallel_image_batch",
                {"max_workers": 3},
                context,
            )
            self._record_admission(lease)
        else:
            if not parallel_only or not isinstance(admission, _ParallelStageAdmission):
                raise TypeError("lease must be a parallel image admission")
            lease = admission.claim(self)
        child_pid = None
        worker_thread_count = 0
        effective_worker_count = 1
        used_parallel_child = False
        run_reason = lease.reason
        status = "running"
        cancellation_count = 0
        child_peak_rss_bytes = None
        artifacts: tuple[PreparedImageArtifact, ...] = ()
        work_started = False
        try:
            with _ParallelLeaseGuard(self, lease, admission):
                if parallel_only and not lease.parallel:
                    status = "not_run"
                    return None

                work_started = True

                primitive_payload = workset.primitive_payload()
                # Validate parent-owned inputs only after parallel admission.
                # The child repeats these checks to close the mutation window
                # for path sources.  The regular ``run`` serial path retains
                # the same validation before doing any image work.
                for descriptor in primitive_payload["descriptors"]:
                    _validated_source(descriptor, primitive_payload)

                if lease.parallel:
                    effective_worker_count = lease.worker_count
                    used_parallel_child = True
                    (
                        artifacts,
                        child_pid,
                        worker_thread_count,
                        child_peak_rss_bytes,
                    ) = self._run_parallel(
                        workset,
                        context,
                        identity_validator,
                        staging_dir,
                        created_paths,
                        lease.worker_count,
                    )
                else:
                    artifacts = self._run_serial(
                        workset,
                        context,
                        identity_validator,
                        staging_dir,
                        created_paths,
                    )
            status = "succeeded"
            return artifacts
        except BaseException as error:
            if _is_cancellation(error):
                status = "canceled"
                cancellation_count = 1
            else:
                status = "failed"
            # An unreapable child may still have file handles or writes in
            # flight.  Preserve its staging evidence and never race it with
            # cleanup; health continues to expose the live PID.
            if not isinstance(error, ParallelStageProcessLeak):
                _remove_created_paths(created_paths)
            raise
        finally:
            duration_ms = max(0.0, (time.monotonic() - started) * 1000.0)
            with self._snapshot_lock:
                self._cancellation_count += cancellation_count
                if work_started:
                    self._batch_count += 1
                    self._batch_duration_ms_total += duration_ms
                    self._normalized_work_pixels_total += sum(
                        artifact.width * artifact.height
                        for artifact in artifacts
                    )
                    if child_peak_rss_bytes is not None:
                        if self._child_peak_rss_bytes is None:
                            self._child_peak_rss_bytes = child_peak_rss_bytes
                        else:
                            self._child_peak_rss_bytes = max(
                                self._child_peak_rss_bytes,
                                child_peak_rss_bytes,
                            )
                self._last_run_snapshot = {
                    "worker_count": effective_worker_count,
                    "reason": run_reason,
                    "parallel": used_parallel_child,
                    "batch_duration_ms": duration_ms,
                    "child_pid": child_pid,
                    "worker_thread_count": worker_thread_count,
                    "canceled": status == "canceled",
                    "status": status,
                    "cancellation_count": self._cancellation_count,
                    "child_peak_rss_bytes": child_peak_rss_bytes,
                }

    def _record_admission(self, lease: ResourceLease) -> None:
        tier = {
            1: "serial",
            2: "2_worker",
            3: "3_worker",
        }.get(lease.worker_count, "serial")
        with self._snapshot_lock:
            self._admission_tier_counts[tier] += 1
            if tier == "serial" and lease.reason:
                reason = str(lease.reason)
                self._serial_fallback_reason_counts[reason] = (
                    self._serial_fallback_reason_counts.get(reason, 0) + 1
                )

    def _run_parallel(
        self,
        workset: ImmutableImageWorkset,
        context: TaskContext,
        identity_validator: Callable[[InstanceIdentity], bool],
        staging_dir: Path,
        created_paths: list[Path],
        worker_count: int,
    ) -> tuple[tuple[PreparedImageArtifact, ...], int, int, int | None]:
        run_id = uuid4().hex
        payload = workset.primitive_payload()
        receiver, sender = self._mp.Pipe(duplex=False)
        cancel_event = self._mp.Event()
        dispatch_event = self._mp.Event()
        dispatch_event.set()
        process = self._mp.Process(
            target=_parallel_child_main,
            args=(
                payload,
                int(worker_count),
                run_id,
                cancel_event,
                dispatch_event,
                sender,
            ),
            name="inkypi-parallel-image-stage",
        )
        try:
            process.start()
        except BaseException:
            receiver.close()
            sender.close()
            raise RuntimeError("parallel image stage process could not start")
        sender.close()
        with self._active_lock:
            self._active.add(process)
        child_pid = int(process.pid)
        child_peak_rss_bytes = None
        message = None
        try:
            while True:
                try:
                    context.raise_if_cancelled()
                except BaseException:
                    self._terminate_process(process, cancel_event)
                    _remove_run_paths(staging_dir, run_id)
                    raise
                try:
                    _require_current_identity(
                        workset.instance_identity,
                        identity_validator,
                    )
                except BaseException:
                    self._terminate_process(process, cancel_event)
                    _remove_run_paths(staging_dir, run_id)
                    raise

                sample = self._governor.sample()
                child_peak_rss_bytes = _max_optional_int(
                    child_peak_rss_bytes,
                    _sample_process_rss_bytes(child_pid),
                )
                available = sample.available_mb
                swap = sample.swap_percent
                if (
                    (available is not None and available < HARD_MIN_AVAILABLE_MB)
                    or (swap is not None and swap >= HARD_MAX_SWAP_PERCENT)
                ):
                    self._terminate_process(process, cancel_event)
                    _remove_run_paths(staging_dir, run_id)
                    raise ResourcePressureDeferred(
                        reason="parallel_image_hard_resource_limit",
                        phase="parallel_image_batch",
                        available_mb=available,
                        swap_percent=swap,
                    )
                soft_available = 170.0 if worker_count >= 3 else 150.0
                if (
                    (available is not None and available < soft_available)
                    or (swap is not None and swap >= SOFT_MAX_SWAP_PERCENT)
                ):
                    dispatch_event.clear()
                else:
                    # Paused dispatch is reversible: when pressure recovers the
                    # child may submit its remaining descriptors.
                    dispatch_event.set()

                if receiver.poll(self._poll_interval):
                    try:
                        message = receiver.recv()
                    except EOFError:
                        message = None
                    break
                if not process.is_alive():
                    process.join(timeout=0)
                    if receiver.poll(self._poll_interval):
                        try:
                            message = receiver.recv()
                        except EOFError:
                            message = None
                    break

            self._reap_process(process, cancel_event)
            if process.is_alive():
                raise ParallelStageProcessLeak(
                    "parallel image stage child remained alive after reap"
                )
        finally:
            receiver.close()
            with self._active_lock:
                # Keep an unreapable child visible to health reporting.  A
                # successfully reaped process is removed immediately.
                if not process.is_alive():
                    self._active.discard(process)

        if (
            not isinstance(message, tuple)
            or len(message) != 5
            or message[0] != "succeeded"
        ):
            _remove_run_paths(staging_dir, run_id)
            error_code = (
                str(message[1])
                if isinstance(message, tuple) and len(message) >= 2
                else "child_exited_without_result"
            )
            raise RuntimeError(f"parallel image stage failed safely: {error_code}")
        _, artifact_values, remaining_ordinals, worker_thread_count, reported_pid = message
        if reported_pid != child_pid:
            _remove_run_paths(staging_dir, run_id)
            raise InvalidImageWorkset("parallel image child identity did not match")
        if (
            isinstance(worker_thread_count, bool)
            or not isinstance(worker_thread_count, int)
            or worker_thread_count < 1
            or worker_thread_count > worker_count
        ):
            _remove_run_paths(staging_dir, run_id)
            raise InvalidImageWorkset("parallel image child reported invalid worker usage")
        if not isinstance(artifact_values, (tuple, list)):
            _remove_run_paths(staging_dir, run_id)
            raise InvalidImageWorkset("parallel image child returned invalid artifacts")
        try:
            _require_current_identity(workset.instance_identity, identity_validator)
        except BaseException:
            _remove_run_paths(staging_dir, run_id)
            raise
        artifacts = []
        try:
            for value in artifact_values:
                if isinstance(value, Mapping) and isinstance(value.get("path"), str):
                    created_paths.append(Path(value["path"]))
                artifacts.append(_validate_artifact(value, payload, staging_dir))
        except BaseException:
            _remove_run_paths(staging_dir, run_id)
            raise

        if not isinstance(remaining_ordinals, (tuple, list)) or any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in remaining_ordinals
        ):
            raise InvalidImageWorkset("parallel image child returned invalid remainder")
        expected_ordinals = {item["ordinal"] for item in payload["descriptors"]}
        completed_ordinals = {item.ordinal for item in artifacts}
        remainder_set = set(remaining_ordinals)
        if (
            completed_ordinals & remainder_set
            or completed_ordinals | remainder_set != expected_ordinals
            or len(completed_ordinals) != len(artifacts)
            or len(remainder_set) != len(remaining_ordinals)
        ):
            raise InvalidImageWorkset("parallel image child returned inconsistent work")

        if remaining_ordinals:
            _remove_run_paths(staging_dir, run_id)
            raise InvalidImageWorkset(
                "parallel image child returned undispatched work"
            )
        _require_current_identity(workset.instance_identity, identity_validator)
        artifacts.sort(key=lambda item: item.ordinal)
        return tuple(artifacts), child_pid, worker_thread_count, child_peak_rss_bytes

    def _run_serial(
        self,
        workset: ImmutableImageWorkset,
        context: TaskContext,
        identity_validator: Callable[[InstanceIdentity], bool],
        staging_dir: Path,
        created_paths: list[Path],
    ) -> tuple[PreparedImageArtifact, ...]:
        artifacts = []
        run_id = uuid4().hex
        payload = workset.primitive_payload()
        for descriptor in sorted(payload["descriptors"], key=lambda item: item["ordinal"]):
            context.raise_if_cancelled()
            _require_current_identity(workset.instance_identity, identity_validator)
            artifact = _normalize_descriptor(descriptor, payload, run_id)
            created_paths.append(Path(artifact["path"]))
            artifacts.append(_validate_artifact(artifact, payload, staging_dir))
        _require_current_identity(workset.instance_identity, identity_validator)
        return tuple(artifacts)

    def _run_serial_descriptors(
        self,
        workset: ImmutableImageWorkset,
        context: TaskContext,
        identity_validator: Callable[[InstanceIdentity], bool],
        staging_dir: Path,
        created_paths: list[Path],
        ordinals: set[int],
    ) -> tuple[PreparedImageArtifact, ...]:
        artifacts = []
        run_id = uuid4().hex
        payload = workset.primitive_payload()
        descriptors = sorted(
            (
                item
                for item in payload["descriptors"]
                if item["ordinal"] in ordinals
            ),
            key=lambda item: item["ordinal"],
        )
        for descriptor in descriptors:
            context.raise_if_cancelled()
            _require_current_identity(workset.instance_identity, identity_validator)
            value = _normalize_descriptor(descriptor, payload, run_id)
            created_paths.append(Path(value["path"]))
            artifacts.append(_validate_artifact(value, payload, staging_dir))
        return tuple(artifacts)

    def _terminate_process(self, process, cancel_event) -> None:
        cancel_event.set()
        if process.is_alive():
            process.terminate()
        process.join(timeout=self._terminate_grace)
        if process.is_alive():
            kill = getattr(process, "kill", None)
            if callable(kill):
                kill()
            else:
                process.terminate()
            process.join(timeout=self._terminate_grace)
        if process.is_alive():
            raise ParallelStageProcessLeak(
                "parallel image stage child could not be reaped",
                process=process,
            )

    def _reap_process(self, process, cancel_event) -> None:
        process.join(timeout=self._terminate_grace)
        if process.is_alive():
            self._terminate_process(process, cancel_event)

    def _quarantine_process(self, process, lease: ResourceLease) -> None:
        """Keep global parallel capacity unavailable while a child survives."""

        with self._active_lock:
            self._active.add(process)
            self._quarantined_leases[process] = lease

    def _reap_quarantined_processes(self) -> None:
        releasable = []
        with self._active_lock:
            for process, lease in tuple(self._quarantined_leases.items()):
                try:
                    alive = process.is_alive()
                except (AssertionError, OSError, ValueError):
                    alive = True
                if alive:
                    continue
                try:
                    process.join(timeout=0)
                except (AssertionError, OSError, ValueError):
                    continue
                self._quarantined_leases.pop(process, None)
                self._active.discard(process)
                releasable.append((process, lease))
        for process, lease in releasable:
            lease.release()
            try:
                process.close()
            except (AttributeError, OSError, ValueError):
                pass


def _parallel_child_main(
    payload: Mapping[str, Any],
    worker_count: int,
    run_id: str,
    cancel_event,
    dispatch_event,
    sender,
) -> None:
    artifacts: list[Mapping[str, Any]] = []
    worker_thread_ids: set[int] = set()
    thread_ids_lock = threading.Lock()

    def execute(descriptor):
        if cancel_event.is_set():
            raise RuntimeError("parallel image stage canceled")
        thread_id = threading.get_ident()
        with thread_ids_lock:
            worker_thread_ids.add(thread_id)
        return _normalize_descriptor(descriptor, payload, run_id)

    descriptors = sorted(payload["descriptors"], key=lambda item: item["ordinal"])
    next_index = 0
    pending = {}
    executor = ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="inkypi-image-stage",
    )
    try:
        while pending or next_index < len(descriptors):
            if cancel_event.is_set():
                raise RuntimeError("parallel image stage canceled")
            while (
                (
                    dispatch_event.is_set()
                    or next_index < min(worker_count, len(descriptors))
                )
                and next_index < len(descriptors)
                and len(pending) < worker_count
            ):
                descriptor = descriptors[next_index]
                next_index += 1
                future = executor.submit(execute, descriptor)
                pending[future] = descriptor["ordinal"]
            if not pending:
                # Soft resource pressure pauses dispatch.  Do not report the
                # remaining descriptors back to the parent: doing so would let
                # the parent immediately execute them on its serial staging
                # path and defeat the pause.  Wait until resources recover or
                # the parent cancels/reaps this whole child.
                dispatch_event.wait(DEFAULT_POLL_INTERVAL_SECONDS)
                continue
            completed, _ = wait(
                tuple(pending),
                timeout=DEFAULT_POLL_INTERVAL_SECONDS,
                return_when=FIRST_COMPLETED,
            )
            for future in completed:
                pending.pop(future, None)
                artifacts.append(future.result())
        if cancel_event.is_set():
            raise RuntimeError("parallel image stage canceled")
        artifacts.sort(key=lambda item: item["ordinal"])
        sender.send(
            (
                "succeeded",
                tuple(dict(item) for item in artifacts),
                (),
                len(worker_thread_ids),
                os.getpid(),
            )
        )
    except BaseException as error:
        cancel_event.set()
        for future in pending:
            future.cancel()
        try:
            sender.send(
                (
                    "failed",
                    type(error).__name__[:64],
                    (),
                    max(1, len(worker_thread_ids)),
                    os.getpid(),
                )
            )
        except Exception:
            pass
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        try:
            sender.close()
        except Exception:
            pass


def _freeze_descriptor(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("each descriptor must be a mapping")
    unknown = set(value) - _DESCRIPTOR_KEYS
    if unknown:
        raise ValueError(f"unknown descriptor fields: {', '.join(sorted(unknown))}")
    ordinal = value.get("ordinal")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("descriptor ordinal must be a non-negative integer")
    has_path = "source_path" in value
    has_bytes = "source_bytes" in value
    if has_path == has_bytes:
        raise ValueError("descriptor requires exactly one source_path or source_bytes")
    frozen: dict[str, Any] = {"ordinal": ordinal}
    if has_path:
        source_path = value["source_path"]
        if not isinstance(source_path, str) or not Path(source_path).is_absolute():
            raise ValueError("source_path must be an absolute path")
        frozen["source_path"] = source_path
    else:
        source_bytes = value["source_bytes"]
        if type(source_bytes) is not bytes:
            raise TypeError("source_bytes must be immutable bytes")
        frozen["source_bytes"] = bytes(source_bytes)
    source_sha256 = value.get("source_sha256")
    _require_sha256(source_sha256, "source_sha256")
    frozen["source_sha256"] = source_sha256.lower()
    target_width = value.get("target_width")
    target_height = value.get("target_height")
    if (target_width is None) != (target_height is None):
        raise ValueError("target_width and target_height must be provided together")
    if target_width is not None:
        for name, item in (("target_width", target_width), ("target_height", target_height)):
            if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
                raise ValueError(f"{name} must be a positive integer")
            frozen[name] = item
    return MappingProxyType(frozen)


def _require_sha256(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field_name} must be a lowercase hexadecimal SHA-256")
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(f"{field_name} must be a lowercase hexadecimal SHA-256") from error
    if value.lower() != value:
        raise ValueError(f"{field_name} must be a lowercase hexadecimal SHA-256")


def _require_current_identity(
    identity: InstanceIdentity,
    validator: Callable[[InstanceIdentity], bool],
) -> None:
    try:
        current = validator(identity) is True
    except BaseException as error:
        raise StaleImageWorksetError("image workset identity could not be validated") from error
    if not current:
        raise StaleImageWorksetError("image workset identity is stale")


def _validate_staging_dir(value: str) -> Path:
    path = Path(value)
    absolute = Path(os.path.abspath(os.fspath(path)))
    if path != absolute:
        raise InvalidImageWorkset("staging_dir must be a normalized absolute path")
    if path.is_symlink() or not path.is_dir():
        raise InvalidImageWorkset("staging_dir must be an existing ordinary directory")
    resolved = path.resolve(strict=True)
    if resolved != absolute:
        raise InvalidImageWorkset("staging_dir contains a symbolic-link path component")
    current = absolute
    anchor = Path(absolute.anchor)
    while True:
        try:
            info = os.lstat(current)
        except OSError as error:
            raise InvalidImageWorkset("staging_dir path component is unavailable") from error
        if stat.S_ISLNK(info.st_mode):
            raise InvalidImageWorkset(
                "staging_dir contains a symbolic-link path component"
            )
        if current == anchor:
            break
        current = current.parent
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise InvalidImageWorkset("staging_dir must be an ordinary directory")
    return resolved


def _normalize_descriptor(
    descriptor: Mapping[str, Any],
    payload: Mapping[str, Any],
    run_id: str,
) -> Mapping[str, Any]:
    source = _validated_source(descriptor, payload)
    limits = ImageLimits(
        max_bytes=int(payload["max_source_bytes"]),
        max_width=int(payload["max_width"]),
        max_height=int(payload["max_height"]),
        max_pixels=int(payload["max_pixels"]),
    )
    with safe_open_image(source, limits=limits) as decoded:
        normalized = decoded.convert("RGBA" if "A" in decoded.getbands() else "RGB")
        if "target_width" in descriptor:
            target = (int(descriptor["target_width"]), int(descriptor["target_height"]))
            resized = ImageOps.contain(normalized, target, Image.Resampling.LANCZOS)
            if resized is not normalized:
                normalized.close()
            normalized = resized
        output = BytesIO()
        try:
            normalized.save(output, format="PNG", optimize=False)
            width, height = normalized.size
        finally:
            normalized.close()
    output_bytes = output.getvalue()
    staging = Path(str(payload["staging_dir"]))
    output_path = staging / f"image-stage-{run_id}-{descriptor['ordinal']}.png"
    _write_exclusive_file(output_path, output_bytes)
    return {
        "ordinal": int(descriptor["ordinal"]),
        "path": str(output_path),
        "sha256": hashlib.sha256(output_bytes).hexdigest(),
        "byte_size": len(output_bytes),
        "width": width,
        "height": height,
        "image_format": "PNG",
    }


def _validated_source(
    descriptor: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bytes | str:
    expected_hash = descriptor["source_sha256"]
    max_bytes = int(payload["max_source_bytes"])
    if "source_bytes" in descriptor:
        source = descriptor["source_bytes"]
        if type(source) is not bytes or len(source) > max_bytes:
            raise InvalidImageWorkset("source bytes exceed the workset boundary")
        actual_hash = hashlib.sha256(source).hexdigest()
        if actual_hash != expected_hash:
            raise InvalidImageWorkset("source bytes do not match source_sha256")
        return source

    roots = tuple(Path(root).resolve(strict=True) for root in payload["source_roots"])
    if not roots:
        raise InvalidImageWorkset("path sources require at least one source_root")
    source_path = Path(descriptor["source_path"])
    if source_path.is_symlink():
        raise InvalidImageWorkset("source paths cannot be symbolic links")
    resolved = source_path.resolve(strict=True)
    containing_root = next((root for root in roots if _is_within(resolved, root)), None)
    if containing_root is None:
        raise InvalidImageWorkset("source path escapes configured source_roots")
    _assert_no_symlink_chain(source_path, containing_root)
    source_stat = os.lstat(source_path)
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size > max_bytes:
        raise InvalidImageWorkset("source path is not a bounded ordinary file")
    digest = hashlib.sha256()
    with source_path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    if digest.hexdigest() != expected_hash:
        raise InvalidImageWorkset("source path does not match source_sha256")
    return str(source_path)


def _validate_artifact(
    value: Mapping[str, Any],
    payload: Mapping[str, Any],
    staging_dir: Path,
) -> PreparedImageArtifact:
    try:
        artifact = PreparedImageArtifact(**dict(value))
    except (TypeError, ValueError) as error:
        raise InvalidImageWorkset("worker returned malformed image metadata") from error
    path = Path(artifact.path)
    if not path.is_absolute() or path.is_symlink():
        raise InvalidImageWorkset("worker artifact path is not an ordinary absolute path")
    resolved = path.resolve(strict=True)
    if resolved.parent != staging_dir or not _is_within(resolved, staging_dir):
        raise InvalidImageWorkset("worker artifact escaped the staging directory")
    _assert_no_symlink_chain(path, staging_dir)
    file_stat = os.lstat(path)
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != artifact.byte_size:
        raise InvalidImageWorkset("worker artifact is not the declared ordinary file")
    if file_stat.st_size > DEFAULT_MAX_ARTIFACT_BYTES:
        raise InvalidImageWorkset("worker artifact exceeds the result byte boundary")
    payload_bytes = path.read_bytes()
    if hashlib.sha256(payload_bytes).hexdigest() != artifact.sha256:
        raise InvalidImageWorkset("worker artifact SHA-256 does not match")
    with safe_open_image(
        payload_bytes,
        limits=ImageLimits(
            max_bytes=DEFAULT_MAX_ARTIFACT_BYTES,
            max_width=int(payload["max_width"]),
            max_height=int(payload["max_height"]),
            max_pixels=int(payload["max_pixels"]),
            allowed_formats=frozenset({"PNG"}),
        ),
    ) as image:
        # safe_open_image already admitted only PNG, then returns a detached
        # copy whose Pillow ``format`` attribute is intentionally unset.
        if image.size != (artifact.width, artifact.height):
            raise InvalidImageWorkset("worker artifact image metadata does not match")
    return artifact


def _write_exclusive_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _assert_no_symlink_chain(path: Path, root: Path) -> None:
    current = path
    while True:
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise InvalidImageWorkset("symbolic links are not allowed in image paths")
        except FileNotFoundError as error:
            raise InvalidImageWorkset("image path disappeared during validation") from error
        if current == root:
            return
        if current.parent == current:
            raise InvalidImageWorkset("image path is outside the validated root")
        current = current.parent


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _remove_created_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        except OSError:
            pass


def _remove_run_paths(staging_dir: Path, run_id: str) -> None:
    for path in staging_dir.glob(f"image-stage-{run_id}-*.png"):
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
        except OSError:
            pass


def _is_cancellation(error: BaseException) -> bool:
    return isinstance(error, (TaskCancelled, ResourcePressureDeferred))


def _max_optional_int(current: int | None, candidate: int | None) -> int | None:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return max(current, candidate)


def _sample_process_rss_bytes(pid: int) -> int | None:
    """Read Linux RSS without a psutil dependency; unsupported hosts return None."""

    try:
        status_path = Path("/proc") / str(int(pid)) / "status"
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                pieces = line.split()
                if len(pieces) >= 2:
                    value = int(pieces[1]) * 1024
                    return value if value >= 0 else None
    except (OSError, TypeError, ValueError):
        return None
    return None
