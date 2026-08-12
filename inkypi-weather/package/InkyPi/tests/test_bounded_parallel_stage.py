import hashlib
from io import BytesIO
import os
import queue
import sys
import threading
import time
from pathlib import Path

from PIL import Image
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime import bounded_parallel_stage as stage_module
from runtime.bounded_parallel_stage import (
    BoundedParallelStageRunner,
    InvalidImageWorkset,
    ImmutableImageWorkset,
    ParallelStageProcessLeak,
    StaleImageWorksetError,
)
from runtime.long_task_executor import InstanceIdentity
from runtime.refresh_contracts import TaskContext
from runtime.resource_governor import RuntimeResourceGovernor


def _context(seconds=5):
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + seconds,
    )


def _png_bytes(size=(4, 2), color=(220, 30, 40)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _governor(*, available_mb, swap_percent=0, cpu_quota_cores=4):
    return RuntimeResourceGovernor(
        snapshot_provider=lambda: {
            "available_mb": available_mb,
            "swap_percent": swap_percent,
            "cpu_quota_cores": cpu_quota_cores,
        }
    )


def test_low_resource_workset_runs_serially_and_normalizes_png(tmp_path):
    source = _png_bytes()
    identity = InstanceIdentity("magazine", 3, 7)
    workset = ImmutableImageWorkset(
        descriptors=(
            {
                "ordinal": 4,
                "source_bytes": source,
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "target_width": 2,
                "target_height": 2,
            },
        ),
        staging_dir=str(tmp_path),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(
        governor=_governor(available_mb=100),
    )

    artifacts = runner.run(workset, _context(), lambda value: value == identity)

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.ordinal == 4
    assert artifact.image_format == "PNG"
    assert (artifact.width, artifact.height) == (2, 1)
    assert artifact.sha256 == hashlib.sha256(Path(artifact.path).read_bytes()).hexdigest()
    with Image.open(artifact.path) as normalized:
        assert normalized.format == "PNG"
        assert normalized.mode == "RGB"
        assert normalized.getpixel((0, 0)) == (220, 30, 40)
    assert runner.last_run_snapshot["worker_count"] == 1
    assert runner.active_processes == ()


def test_parallel_only_low_resource_returns_before_source_validation(tmp_path):
    identity = InstanceIdentity("magazine", 3, 7)
    workset = ImmutableImageWorkset(
        descriptors=(
            {
                "ordinal": 0,
                "source_bytes": b"not-an-image",
                "source_sha256": "0" * 64,
            },
        ),
        staging_dir=str(tmp_path),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(
        governor=_governor(available_mb=100, cpu_quota_cores=1),
    )

    artifacts = runner.run_parallel_only(
        workset,
        _context(),
        lambda value: value == identity,
    )

    assert artifacts is None
    assert tuple(tmp_path.iterdir()) == ()
    assert runner.last_run_snapshot["parallel"] is False
    assert runner.last_run_snapshot["status"] == "not_run"
    assert runner.last_run_snapshot["reason"] == "cpu_quota_below_parallel_threshold"


def test_parallel_only_high_resource_runs_in_child(tmp_path):
    source = _png_bytes((900, 600), (12, 34, 56))
    identity = InstanceIdentity("magazine", 3, 7)
    workset = ImmutableImageWorkset(
        descriptors=(
            {
                "ordinal": 0,
                "source_bytes": source,
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "target_width": 320,
                "target_height": 240,
            },
        ),
        staging_dir=str(tmp_path),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(
        governor=_governor(available_mb=190, cpu_quota_cores=2),
    )

    artifacts = runner.run_parallel_only(
        workset,
        _context(10),
        lambda value: value == identity,
    )

    assert artifacts is not None
    assert len(artifacts) == 1
    assert runner.last_run_snapshot["parallel"] is True
    assert runner.last_run_snapshot["worker_count"] == 2


def test_parallel_workset_uses_one_child_and_returns_artifacts_in_ordinal_order(tmp_path):
    inputs = {
        9: _png_bytes((1400, 900), (9, 10, 11)),
        1: _png_bytes((1400, 900), (1, 2, 3)),
        5: _png_bytes((1400, 900), (5, 6, 7)),
    }
    identity = InstanceIdentity("comic", 2, 8)
    workset = ImmutableImageWorkset(
        descriptors=tuple(
            {
                "ordinal": ordinal,
                "source_bytes": payload,
                "source_sha256": hashlib.sha256(payload).hexdigest(),
                "target_width": 800,
                "target_height": 480,
            }
            for ordinal, payload in inputs.items()
        ),
        staging_dir=str(tmp_path),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(
        governor=_governor(available_mb=190, swap_percent=10, cpu_quota_cores=3),
    )

    artifacts = runner.run(workset, _context(10), lambda value: value == identity)

    assert tuple(item.ordinal for item in artifacts) == (1, 5, 9)
    snapshot = runner.last_run_snapshot
    assert snapshot["worker_count"] == 3
    assert snapshot["parallel"] is True
    assert isinstance(snapshot["child_pid"], int)
    assert snapshot["child_pid"] != os.getpid()
    assert snapshot["worker_thread_count"] >= 2
    assert snapshot["status"] == "succeeded"
    assert snapshot["cancellation_count"] == 0
    assert snapshot["child_peak_rss_bytes"] is None or snapshot["child_peak_rss_bytes"] > 0
    assert runner.active_processes == ()


def test_path_source_must_be_regular_hash_matched_and_under_an_allowed_root(tmp_path):
    source_root = tmp_path / "sources"
    stage_root = tmp_path / "stage"
    source_root.mkdir()
    stage_root.mkdir()
    source_path = source_root / "cover.png"
    source_path.write_bytes(_png_bytes())
    identity = InstanceIdentity("magazine", 1, 1)

    valid = ImmutableImageWorkset(
        descriptors=(
            {
                "ordinal": 0,
                "source_path": str(source_path),
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            },
        ),
        staging_dir=str(stage_root),
        source_roots=(str(source_root),),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(governor=_governor(available_mb=100))
    assert runner.run(valid, _context(), lambda value: value == identity)[0].width == 4

    escaped = tmp_path / "outside.png"
    escaped.write_bytes(_png_bytes())
    invalid = ImmutableImageWorkset(
        descriptors=(
            {
                "ordinal": 0,
                "source_path": str(escaped),
                "source_sha256": hashlib.sha256(escaped.read_bytes()).hexdigest(),
            },
        ),
        staging_dir=str(stage_root),
        source_roots=(str(source_root),),
        instance_identity=identity,
    )
    with pytest.raises(InvalidImageWorkset, match="escapes"):
        runner.run(invalid, _context(), lambda value: value == identity)


def test_staging_directory_rejects_symbolic_link_parent_component(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")
    staging = linked / "stage"
    staging.mkdir()
    source = _png_bytes()
    identity = InstanceIdentity("magazine", 1, 1)
    workset = ImmutableImageWorkset(
        descriptors=(
            {
                "ordinal": 0,
                "source_bytes": source,
                "source_sha256": hashlib.sha256(source).hexdigest(),
            },
        ),
        staging_dir=str(staging),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(
        governor=_governor(available_mb=100),
    )

    with pytest.raises(InvalidImageWorkset, match="symbolic"):
        runner.run_parallel_only(
            workset,
            _context(),
            lambda value: value == identity,
        )


def test_stale_identity_fails_closed_before_creating_any_artifact(tmp_path):
    source = _png_bytes()
    identity = InstanceIdentity("comic", 4, 9)
    workset = ImmutableImageWorkset(
        descriptors=(
            {
                "ordinal": 0,
                "source_bytes": source,
                "source_sha256": hashlib.sha256(source).hexdigest(),
            },
        ),
        staging_dir=str(tmp_path),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(governor=_governor(available_mb=100))

    with pytest.raises(StaleImageWorksetError):
        runner.run(workset, _context(), lambda _value: False)
    assert tuple(tmp_path.iterdir()) == ()


def test_canceled_context_reaps_parallel_child_and_removes_staged_artifacts(tmp_path):
    sources = tuple(
        _png_bytes((2600, 1800), (index, index, index))
        for index in range(12)
    )
    identity = InstanceIdentity("pixiv", 1, 2)
    runner = BoundedParallelStageRunner(
        governor=_governor(available_mb=190, cpu_quota_cores=3),
    )

    for expected_count in (1, 2):
        stage_dir = tmp_path / f"attempt-{expected_count}"
        stage_dir.mkdir()
        workset = ImmutableImageWorkset(
            descriptors=tuple(
                {
                    "ordinal": index,
                    "source_bytes": source,
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                    "target_width": 1600,
                    "target_height": 1200,
                }
                for index, source in enumerate(sources)
            ),
            staging_dir=str(stage_dir),
            instance_identity=identity,
        )
        cancel_event = threading.Event()
        context = TaskContext(
            cancel_event=cancel_event,
            deadline_monotonic=time.monotonic() + 10,
        )
        outcome = queue.Queue()

        def run_stage():
            try:
                outcome.put(
                    runner.run(workset, context, lambda value: value == identity)
                )
            except BaseException as error:
                outcome.put(error)

        thread = threading.Thread(target=run_stage)
        thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not runner.active_processes:
            time.sleep(0.01)
        assert runner.active_processes
        cancel_event.set()
        thread.join(timeout=5)

        assert not thread.is_alive()
        error = outcome.get_nowait()
        assert isinstance(error, Exception)
        assert "canceled" in str(error)
        assert runner.active_processes == ()
        assert tuple(stage_dir.glob("image-stage-*.png")) == ()
        snapshot = runner.last_run_snapshot
        assert snapshot["status"] == "canceled"
        assert snapshot["cancellation_count"] == expected_count


def test_descriptor_rejects_tampered_bytes_and_impossible_target_before_work(tmp_path):
    source = _png_bytes()
    identity = InstanceIdentity("magazine", 1, 1)
    tampered = ImmutableImageWorkset(
        descriptors=(
            {
                "ordinal": 0,
                "source_bytes": source,
                "source_sha256": "0" * 64,
            },
        ),
        staging_dir=str(tmp_path),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(governor=_governor(available_mb=100))
    with pytest.raises(InvalidImageWorkset, match="source_sha256"):
        runner.run(tampered, _context(), lambda value: value == identity)

    with pytest.raises(ValueError, match="target"):
        ImmutableImageWorkset(
            descriptors=(
                {
                    "ordinal": 0,
                    "source_bytes": source,
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                    "target_width": 9000,
                    "target_height": 9000,
                },
            ),
            staging_dir=str(tmp_path),
            instance_identity=identity,
        )


def test_serial_and_parallel_normalization_have_identical_png_hashes(tmp_path):
    source = _png_bytes((733, 511), (22, 44, 66))
    identity = InstanceIdentity("comic", 7, 3)

    def workset(stage_dir):
        return ImmutableImageWorkset(
            descriptors=(
                {
                    "ordinal": 0,
                    "source_bytes": source,
                    "source_sha256": hashlib.sha256(source).hexdigest(),
                    "target_width": 320,
                    "target_height": 240,
                },
            ),
            staging_dir=str(stage_dir),
            instance_identity=identity,
        )

    serial_dir = tmp_path / "serial"
    parallel_dir = tmp_path / "parallel"
    serial_dir.mkdir()
    parallel_dir.mkdir()
    serial = BoundedParallelStageRunner(
        governor=_governor(available_mb=100)
    ).run(workset(serial_dir), _context(), lambda value: value == identity)
    parallel = BoundedParallelStageRunner(
        governor=_governor(available_mb=190, cpu_quota_cores=3)
    ).run(workset(parallel_dir), _context(), lambda value: value == identity)

    assert serial[0].sha256 == parallel[0].sha256
    assert Path(serial[0].path).read_bytes() == Path(parallel[0].path).read_bytes()


def test_soft_pressure_pauses_new_dispatch_until_resources_recover(tmp_path):
    sources = tuple(
        _png_bytes((2600, 1800), (index * 20, 40, 80))
        for index in range(4)
    )
    identity = InstanceIdentity("magazine", 1, 1)
    state = {"samples": 0, "recovered": False}

    def snapshot():
        state["samples"] += 1
        return {
            "available_mb": (
                160 if state["samples"] == 1 or state["recovered"] else 149
            ),
            "swap_percent": 0,
            "cpu_quota_cores": 2,
        }

    workset = ImmutableImageWorkset(
        descriptors=tuple(
            {
                "ordinal": index,
                "source_bytes": source,
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "target_width": 800,
                "target_height": 480,
            }
            for index, source in enumerate(sources)
        ),
        staging_dir=str(tmp_path),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(
        governor=RuntimeResourceGovernor(snapshot_provider=snapshot),
    )
    outcome = queue.Queue()

    def run_stage():
        try:
            outcome.put(
                runner.run_parallel_only(
                    workset,
                    _context(10),
                    lambda value: value == identity,
                )
            )
        except BaseException as error:
            outcome.put(error)

    thread = threading.Thread(target=run_stage)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if len(tuple(tmp_path.glob("image-stage-*.png"))) >= 2:
            break
        time.sleep(0.01)
    assert len(tuple(tmp_path.glob("image-stage-*.png"))) == 2
    time.sleep(0.15)
    assert len(tuple(tmp_path.glob("image-stage-*.png"))) == 2
    assert thread.is_alive()

    state["recovered"] = True
    thread.join(timeout=10)

    assert not thread.is_alive()
    result = outcome.get_nowait()
    assert not isinstance(result, BaseException)
    assert tuple(item.ordinal for item in result) == (0, 1, 2, 3)
    assert runner.active_processes == ()


def test_sustained_soft_pressure_reaps_child_at_deadline_without_serial_remainder(
    tmp_path,
):
    sources = tuple(
        _png_bytes((2600, 1800), (index * 20, 40, 80))
        for index in range(4)
    )
    identity = InstanceIdentity("magazine", 1, 1)
    samples = {"count": 0}

    def snapshot():
        samples["count"] += 1
        return {
            "available_mb": 160 if samples["count"] == 1 else 149,
            "swap_percent": 0,
            "cpu_quota_cores": 2,
        }

    workset = ImmutableImageWorkset(
        descriptors=tuple(
            {
                "ordinal": index,
                "source_bytes": source,
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "target_width": 800,
                "target_height": 480,
            }
            for index, source in enumerate(sources)
        ),
        staging_dir=str(tmp_path),
        instance_identity=identity,
    )
    runner = BoundedParallelStageRunner(
        governor=RuntimeResourceGovernor(snapshot_provider=snapshot),
    )

    with pytest.raises(Exception, match="deadline"):
        runner.run_parallel_only(
            workset,
            _context(0.35),
            lambda value: value == identity,
        )

    assert runner.active_processes == ()
    assert tuple(tmp_path.glob("image-stage-*.png")) == ()


def test_unreapable_child_is_reported_and_remains_visible_to_health(tmp_path):
    source = _png_bytes()
    identity = InstanceIdentity("magazine", 1, 1)
    workset = ImmutableImageWorkset(
        descriptors=(
            {
                "ordinal": 0,
                "source_bytes": source,
                "source_sha256": hashlib.sha256(source).hexdigest(),
            },
        ),
        staging_dir=str(tmp_path),
        instance_identity=identity,
    )

    class Endpoint:
        def poll(self, _timeout=0):
            return True

        @staticmethod
        def recv():
            return ("succeeded", (), (0,), 1, 424242)

        @staticmethod
        def close():
            return None

    class Event:
        @staticmethod
        def set():
            return None

        @staticmethod
        def clear():
            return None

    process_state = {"alive": True}

    class Process:
        pid = 424242

        @staticmethod
        def start():
            return None

        @staticmethod
        def is_alive():
            return process_state["alive"]

        @staticmethod
        def join(timeout=None):
            del timeout

        @staticmethod
        def terminate():
            return None

        @staticmethod
        def kill():
            return None

        @staticmethod
        def close():
            return None

    class Context:
        @staticmethod
        def Pipe(duplex=False):
            assert duplex is False
            return Endpoint(), Endpoint()

        @staticmethod
        def Event():
            return Event()

        @staticmethod
        def Process(**_kwargs):
            return Process()

    runner = BoundedParallelStageRunner(
        governor=_governor(available_mb=190, cpu_quota_cores=2),
    )
    runner._mp = Context()

    with pytest.raises(ParallelStageProcessLeak):
        runner.run_parallel_only(
            workset,
            _context(),
            lambda value: value == identity,
        )

    assert runner.active_processes == (424242,)
    assert runner.last_run_snapshot["status"] == "failed"
    competing_runner = BoundedParallelStageRunner(
        governor=_governor(available_mb=190, cpu_quota_cores=2),
    )
    assert competing_runner.acquire_parallel_lease(_context()) is None

    process_state["alive"] = False
    assert runner.active_processes == ()
    admission = competing_runner.acquire_parallel_lease(_context())
    assert admission is not None
    admission.release()


def test_oversized_worker_artifact_is_rejected_before_parent_reads_it(
    tmp_path,
    monkeypatch,
):
    artifact_path = tmp_path / "oversized.png"
    with artifact_path.open("wb") as stream:
        stream.seek(stage_module.DEFAULT_MAX_ARTIFACT_BYTES)
        stream.write(b"x")
    artifact = {
        "ordinal": 0,
        "path": str(artifact_path),
        "sha256": "0" * 64,
        "byte_size": stage_module.DEFAULT_MAX_ARTIFACT_BYTES + 1,
        "width": 1,
        "height": 1,
        "image_format": "PNG",
    }
    payload = {
        "max_width": 8192,
        "max_height": 8192,
        "max_pixels": 8_000_000,
    }

    def refuse_unbounded_read(_path):
        raise AssertionError("oversized artifact payload must not be read")

    monkeypatch.setattr(Path, "read_bytes", refuse_unbounded_read)
    with pytest.raises(InvalidImageWorkset, match="result byte boundary"):
        stage_module._validate_artifact(artifact, payload, tmp_path)
