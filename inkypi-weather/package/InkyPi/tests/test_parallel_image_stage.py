from pathlib import Path
import sys
import time

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.parallel_image_stage import prepare_local_bank_images  # noqa: E402
from plugins.base_plugin import parallel_image_stage as stage_module  # noqa: E402
from runtime.bounded_parallel_stage import (  # noqa: E402
    BoundedParallelStageRunner,
    ParallelStageProcessLeak,
)
from runtime.long_task_executor import (  # noqa: E402
    InstanceIdentity,
    bind_long_task_runtime,
)
from runtime.refresh_contracts import TaskContext  # noqa: E402
from runtime.resource_governor import RuntimeResourceGovernor  # noqa: E402


class _Admission:
    def release(self):
        return None


def _bound_runtime(runner):
    identity = InstanceIdentity("adapter-instance", 2, 3)
    context = TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + 10,
    )
    return bind_long_task_runtime(
        context,
        identity,
        identity_validator=lambda candidate: candidate == identity,
        parallel_image_runner=runner,
    )


def test_unknown_plugin_is_fail_closed_before_touching_bank_paths():
    class ExplodingRunner:
        @staticmethod
        def acquire_parallel_lease(_context):
            return _Admission()

        @staticmethod
        def run_parallel_only(*_args, **_kwargs):
            raise AssertionError("unknown plugin reached the parallel runner")

    with _bound_runtime(ExplodingRunner()):
        assert (
            prepare_local_bank_images(
                plugin_id="unreviewed_new_plugin",
                media_root="missing-media-root",
                source_paths=("missing.png",),
                target_sizes=(None,),
                expected_instance_uuid="adapter-instance",
            )
            is None
        )


def test_image_over_parallel_pixel_budget_returns_to_bank_serial_path(tmp_path):
    media_root = tmp_path / "presentation-media"
    media_root.mkdir()
    source = media_root / "large.png"
    image = Image.new("RGB", (4001, 2000), "navy")
    try:
        image.save(source, format="PNG")
    finally:
        image.close()

    class ExplodingRunner:
        @staticmethod
        def run_parallel_only(*_args, **_kwargs):
            raise AssertionError("oversized image reached the bounded child")

    with _bound_runtime(ExplodingRunner()):
        result = prepare_local_bank_images(
            plugin_id="magazine_covers",
            media_root=media_root,
            source_paths=(source,),
            target_sizes=(None,),
            expected_instance_uuid="adapter-instance",
        )

    assert result is None
    assert not list(tmp_path.glob(".parallel-image-stage-*"))


def test_parallel_admission_decline_returns_none_and_cleans_staging(tmp_path):
    media_root = tmp_path / "presentation-media"
    media_root.mkdir()
    source = media_root / "cover.png"
    with Image.new("RGB", (240, 420), "maroon") as image:
        image.save(source, format="PNG")
    calls = []

    class DecliningRunner:
        @staticmethod
        def acquire_parallel_lease(_context):
            return _Admission()

        @staticmethod
        def run_parallel_only(workset, _context, _identity_validator, *, lease):
            assert isinstance(lease, _Admission)
            calls.append(workset)
            assert Path(workset.staging_dir).is_dir()
            return None

    with _bound_runtime(DecliningRunner()):
        result = prepare_local_bank_images(
            plugin_id="gcd_comic_covers",
            media_root=media_root,
            source_paths=(source,),
            target_sizes=((200, 400),),
            expected_instance_uuid="adapter-instance",
        )

    assert result is None
    assert len(calls) == 1
    assert not list(tmp_path.glob(".parallel-image-stage-*"))


def test_serial_tier_declines_before_source_probe_hash_or_staging(monkeypatch):
    calls = []

    def forbidden(name):
        def fail(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"serial admission touched {name}")

        return fail

    monkeypatch.setattr(stage_module, "_ordinary_directory", forbidden("lstat"))
    monkeypatch.setattr(stage_module, "_ordinary_bank_file", forbidden("file-lstat"))
    monkeypatch.setattr(stage_module, "_parallel_safe_image_header", forbidden("header"))
    monkeypatch.setattr(stage_module, "_sha256_file", forbidden("hash"))
    monkeypatch.setattr(stage_module.tempfile, "mkdtemp", forbidden("staging"))
    runner = BoundedParallelStageRunner(
        governor=RuntimeResourceGovernor(
            snapshot_provider=lambda: {
                "available_mb": 200,
                "swap_percent": 0,
                "cpu_quota_cores": 1,
            }
        )
    )

    with _bound_runtime(runner):
        result = prepare_local_bank_images(
            plugin_id="pixiv_r18_ranking",
            media_root="missing-media-root",
            source_paths=("missing.png",),
            target_sizes=(None,),
            expected_instance_uuid="adapter-instance",
        )

    assert result is None
    assert calls == []
    assert runner.last_run_snapshot["status"] == "not_run"


def test_unreapable_child_preserves_staging_evidence(tmp_path):
    media_root = tmp_path / "presentation-media"
    media_root.mkdir()
    source = media_root / "cover.png"
    with Image.new("RGB", (240, 420), "olive") as image:
        image.save(source, format="PNG")

    class LeakingRunner:
        @staticmethod
        def acquire_parallel_lease(_context):
            return _Admission()

        @staticmethod
        def run_parallel_only(workset, *_args, **_kwargs):
            (Path(workset.staging_dir) / "orphan-evidence.png").write_bytes(b"live")
            raise ParallelStageProcessLeak("child still alive")

    with _bound_runtime(LeakingRunner()):
        try:
            prepare_local_bank_images(
                plugin_id="magazine_covers",
                media_root=media_root,
                source_paths=(source,),
                target_sizes=(None,),
                expected_instance_uuid="adapter-instance",
            )
        except ParallelStageProcessLeak:
            pass
        else:  # pragma: no cover - fail with a clearer assertion.
            raise AssertionError("process leak was swallowed")

    staging = list(tmp_path.glob(".parallel-image-stage-*"))
    assert len(staging) == 1
    assert (staging[0] / "orphan-evidence.png").read_bytes() == b"live"
