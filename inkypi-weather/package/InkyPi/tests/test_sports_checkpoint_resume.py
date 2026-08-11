import json
import sys
import threading
import time
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    read_source_provenance,
)
from runtime.long_task_executor import InstanceIdentity, LongTaskResult
from runtime.refresh_contracts import TaskCancelled, TaskContext
from runtime import sports_isolated_renderer
from runtime.sports_region_checkpoint import SportsRegionCheckpointStore


def _png_bytes(color):
    output = BytesIO()
    Image.new("RGB", (800, 480), color).save(output, format="PNG")
    return output.getvalue()


class _DeviceConfig:
    runtime_paths = SimpleNamespace(env_file="/tmp/inkypi-test.env")

    def get_config(self, key, default=None):
        return {
            "resolution": [800, 480],
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
        }.get(key, default)

    def get_resolution(self):
        return 800, 480


class _DeviceConfigWithCache(_DeviceConfig):
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self.runtime_paths = SimpleNamespace(
            env_file="/tmp/inkypi-test.env",
            cache_dir=cache_dir,
        )


class _CompletedHandle:
    def __init__(self, result):
        self._result = result

    def result(self, timeout=None):
        return self._result

    def cancel(self):
        return True


class _CancelAfterFirstPollHandle:
    def __init__(self, cancel_event):
        self.cancel_event = cancel_event
        self.cancel_calls = 0
        self.result_calls = 0

    def result(self, timeout=None):
        self.result_calls += 1
        if self.cancel_calls:
            return LongTaskResult("canceled", error_code="task_canceled")
        self.cancel_event.set()
        raise TimeoutError("region remains active")

    def cancel(self):
        self.cancel_calls += 1
        return True


class _SingleHandleExecutor:
    def __init__(self, handle):
        self.handle = handle
        self.submissions = []

    def submit(self, task_name, payload, **kwargs):
        self.submissions.append((task_name, payload, kwargs))
        return self.handle


class _RegionExecutor:
    def __init__(self, *, final_overrides=None):
        self.submissions = []
        self.final_overrides = final_overrides or {}

    def submit(self, task_name, payload, **kwargs):
        self.submissions.append((task_name, payload, kwargs))
        if task_name == sports_isolated_renderer.SPORTS_EWC_PREFETCH_TASK:
            return _CompletedHandle(
                LongTaskResult(
                    "succeeded",
                    value={
                        "region": "ewc_prefetch",
                        "source_state": "EWC DETAIL CACHE",
                        "prefetch_source_state": "EWC DETAIL LIVE",
                        "has_detail": True,
                        "cache_handoff_verified": True,
                        "prefetch_handoff_matches": True,
                        "degraded_reason": "",
                        "worker_oom_score_adj": 800,
                        "worker_pid": 4241,
                    },
                )
            )
        region = payload["region"]
        index = sports_isolated_renderer.SPORTS_REGIONS.index(region)
        value = {
            "region": region,
            "region_provenance": SourceProvenance.LIVE.value,
            "image_png": _png_bytes((20 + index, 40, 60)),
            "worker_oom_score_adj": 800,
            "worker_pid": 4242 + index,
        }
        if region == sports_isolated_renderer.SPORTS_REGIONS[-1]:
            value.update(
                {
                    "composite_provenance": SourceProvenance.LIVE.value,
                    "skip_cache": False,
                    "theme_mode": "day",
                }
            )
            value.update(self.final_overrides)
        return _CompletedHandle(LongTaskResult("succeeded", value=value))


def _context():
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + 10,
    )


def _render(
    *,
    store=None,
    instance_identity=None,
    resource_sampler=None,
    settings=None,
    resolved_theme_context=None,
    context=None,
    device_config=None,
    attempt_token=None,
    now=None,
):
    if instance_identity is None:
        instance_identity = InstanceIdentity("sports-instance", 3, 7)
    if resource_sampler is None:
        resource_sampler = lambda: SimpleNamespace(
            available_mb=240,
            swap_percent=10,
        )
    if context is None:
        context = _context()
    if device_config is None:
        device_config = _DeviceConfig()
    if now is None:
        now = datetime.fromisoformat("2026-08-11T09:00:00-07:00")
    return sports_isolated_renderer.render_sports_dashboard_isolated(
        settings=settings or {"id": "sports_dashboard"},
        device_config=device_config,
        resolved_theme_context=resolved_theme_context
        or {
            "mode": "day",
            "palette": {"background": "#ffffff"},
        },
        context=context,
        instance_identity=instance_identity,
        identity_validator=lambda identity: identity == instance_identity,
        resource_sampler=resource_sampler,
        start_min_available_mb=180,
        start_max_swap_percent=60,
        abort_min_available_mb=150,
        abort_max_swap_percent=70,
        now=now,
        attempt_token=attempt_token,
        checkpoint_store=store,
    )


def test_first_permit_checkpoints_one_region_without_promotion(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as pending:
        _render(store=store)

    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert pending.value.completed_regions == ("esports",)
    assert pending.value.next_region == "football"
    assert persisted["completed_regions"] == ["esports"]
    assert [
        payload["region"]
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ] == ["esports"]


def test_next_permit_resumes_the_first_unfinished_region(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")

    with pytest.raises(sports_isolated_renderer.SportsIsolatedCheckpointPending):
        _render(store=store)
    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as pending:
        _render(store=store)

    region_submissions = [
        payload
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ]
    prefetch_submissions = [
        payload
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_EWC_PREFETCH_TASK
    ]
    assert [payload["region"] for payload in region_submissions] == [
        "esports",
        "football",
    ]
    assert len(prefetch_submissions) == 1
    assert region_submissions[1]["base_png"] == _png_bytes((20, 40, 60))
    assert region_submissions[1]["panel_provenances"] == {
        "esports": SourceProvenance.LIVE.value,
    }
    assert pending.value.completed_regions == ("esports", "football")
    assert pending.value.next_region == "lower"


def test_final_permit_returns_promotable_composite_and_clears_checkpoint(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")

    with pytest.raises(sports_isolated_renderer.SportsIsolatedCheckpointPending):
        _render(store=store)
    with pytest.raises(sports_isolated_renderer.SportsIsolatedCheckpointPending):
        _render(store=store)
    image = _render(store=store)

    assert [
        payload["region"]
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ] == list(sports_isolated_renderer.SPORTS_REGIONS)
    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert image.info["inkypi_theme_mode"] == "day"
    assert not store.path.exists()


def test_resource_pressure_retains_completed_regions_for_the_next_permit(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")

    with pytest.raises(sports_isolated_renderer.SportsIsolatedCheckpointPending):
        _render(store=store)
    with pytest.raises(sports_isolated_renderer.SportsIsolatedResourcePressure):
        _render(
            store=store,
            resource_sampler=lambda: SimpleNamespace(
                available_mb=100,
                swap_percent=90,
            ),
        )

    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert persisted["completed_regions"] == ["esports"]

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as pending:
        _render(store=store)
    assert pending.value.completed_regions == ("esports", "football")
    assert [
        payload["region"]
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ] == ["esports", "football"]


def test_cancellation_reaps_active_region_and_retains_completed_checkpoint(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")
    with pytest.raises(sports_isolated_renderer.SportsIsolatedCheckpointPending):
        _render(store=store)

    cancel_event = threading.Event()
    handle = _CancelAfterFirstPollHandle(cancel_event)
    cancelling_executor = _SingleHandleExecutor(handle)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_get_executor",
        lambda: cancelling_executor,
    )
    context = TaskContext(
        cancel_event=cancel_event,
        deadline_monotonic=time.monotonic() + 10,
    )

    with pytest.raises(TaskCancelled):
        _render(store=store, context=context)

    assert handle.cancel_calls == 1
    assert handle.result_calls == 2
    assert json.loads(store.path.read_text(encoding="utf-8"))[
        "completed_regions"
    ] == ["esports"]


def test_revision_mismatch_discards_checkpoint_and_restarts_from_esports(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as original:
        _render(store=store)
    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as replacement:
        _render(
            store=store,
            instance_identity=InstanceIdentity("sports-instance", 3, 8),
        )

    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert replacement.value.fingerprint != original.value.fingerprint
    assert persisted["fingerprint"] == replacement.value.fingerprint
    assert persisted["completed_regions"] == ["esports"]
    assert [
        payload["region"]
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ] == ["esports", "esports"]


@pytest.mark.parametrize(
    (
        "initial_settings",
        "replacement_settings",
        "initial_theme",
        "replacement_theme",
    ),
    [
        (
            {
                "id": "sports_dashboard",
                "forceRefresh": True,
                "force_refresh": True,
            },
            {"id": "sports_dashboard"},
            None,
            None,
        ),
        (
            {"id": "sports_dashboard", "_inkypiDisplayRender": True},
            {"id": "sports_dashboard"},
            None,
            None,
        ),
        (
            {"id": "sports_dashboard"},
            {"id": "sports_dashboard"},
            {"mode": "day", "palette": {"background": "#ffffff"}},
            {"mode": "night", "palette": {"background": "#000000"}},
        ),
    ],
    ids=("force-refresh", "display-render", "theme"),
)
def test_render_semantics_mismatch_discards_checkpoint_and_restarts_from_esports(
    monkeypatch,
    tmp_path,
    initial_settings,
    replacement_settings,
    initial_theme,
    replacement_theme,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as original:
        _render(
            store=store,
            settings=initial_settings,
            resolved_theme_context=initial_theme,
        )
    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as replacement:
        _render(
            store=store,
            settings=replacement_settings,
            resolved_theme_context=replacement_theme,
        )

    assert replacement.value.fingerprint != original.value.fingerprint
    assert replacement.value.completed_regions == ("esports",)
    assert [
        payload["region"]
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ] == ["esports", "esports"]


def test_equivalent_force_semantics_reuse_the_completed_checkpoint(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as first_permit:
        _render(
            store=store,
            settings={"id": "sports_dashboard", "forceRefresh": "yes"},
        )
    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as second_permit:
        _render(
            store=store,
            settings={"id": "sports_dashboard", "force_refresh": 1},
        )

    assert second_permit.value.fingerprint == first_permit.value.fingerprint
    assert second_permit.value.completed_regions == ("esports", "football")
    assert [
        payload["region"]
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ] == ["esports", "football"]


def test_forced_attempt_tokens_never_mix_completed_regions(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")
    forced_settings = {
        "id": "sports_dashboard",
        "forceRefresh": True,
        "force_refresh": True,
    }

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as attempt_a:
        _render(
            store=store,
            settings=forced_settings,
            attempt_token="manual-attempt-A",
        )
    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as attempt_b:
        _render(
            store=store,
            settings=forced_settings,
            attempt_token="manual-attempt-B",
        )

    persisted = store.path.read_text(encoding="utf-8")
    assert attempt_b.value.fingerprint != attempt_a.value.fingerprint
    assert attempt_b.value.completed_regions == ("esports",)
    assert "manual-attempt-A" not in persisted
    assert "manual-attempt-B" not in persisted
    assert [
        payload["region"]
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ] == ["esports", "esports"]


def test_nonforced_checkpoint_reuses_completed_region_within_ttl(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")
    started_at = datetime.fromisoformat("2026-08-11T09:00:00-07:00")

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as first_permit:
        _render(store=store, now=started_at)
    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as second_permit:
        _render(store=store, now=started_at + timedelta(minutes=9, seconds=59))

    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert second_permit.value.fingerprint == first_permit.value.fingerprint
    assert second_permit.value.completed_regions == ("esports", "football")
    assert persisted["render_now"] == started_at.isoformat()
    assert [
        payload["region"]
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ] == ["esports", "football"]


@pytest.mark.parametrize(
    "resume_offset",
    (timedelta(minutes=10, seconds=1), -timedelta(seconds=1)),
    ids=("expired", "future-clock"),
)
def test_checkpoint_outside_valid_time_window_restarts_from_esports(
    monkeypatch,
    tmp_path,
    resume_offset,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")
    started_at = datetime.fromisoformat("2026-08-11T09:00:00-07:00")
    resumed_at = started_at + resume_offset

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as original:
        _render(store=store, now=started_at)
    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as replacement:
        _render(store=store, now=resumed_at)

    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert replacement.value.fingerprint == original.value.fingerprint
    assert replacement.value.completed_regions == ("esports",)
    assert persisted["render_now"] == resumed_at.isoformat()
    assert [
        payload["region"]
        for task_name, payload, _kwargs in executor.submissions
        if task_name == sports_isolated_renderer.SPORTS_REGION_TASK
    ] == ["esports", "esports"]


def test_checkpoint_never_persists_settings_or_provider_payload(
    monkeypatch,
    tmp_path,
):
    secret = "provider-secret-that-must-not-reach-disk"
    executor = _RegionExecutor(
        final_overrides={
            "composite_provenance": {"provider_payload": secret},
        }
    )
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    store = SportsRegionCheckpointStore(tmp_path / "sports-checkpoint.json")
    settings = {
        "id": "sports_dashboard",
        "api_token": secret,
        "provider_payload": {"access_token": secret},
    }

    with pytest.raises(sports_isolated_renderer.SportsIsolatedCheckpointPending):
        _render(store=store, settings=settings)
    raw_checkpoint = store.path.read_text(encoding="utf-8")
    assert secret not in raw_checkpoint
    assert "api_token" not in raw_checkpoint
    assert "provider_payload" not in raw_checkpoint

    with pytest.raises(sports_isolated_renderer.SportsIsolatedCheckpointPending):
        _render(store=store, settings=settings)
    with pytest.raises(RuntimeError):
        _render(store=store, settings=settings)

    raw_checkpoint = store.path.read_text(encoding="utf-8")
    assert secret not in raw_checkpoint
    assert json.loads(raw_checkpoint)["completed_regions"] == [
        "esports",
        "football",
    ]


def test_runtime_cache_path_enables_checkpointing_without_refresh_task_wiring(
    monkeypatch,
    tmp_path,
):
    executor = _RegionExecutor()
    monkeypatch.setattr(sports_isolated_renderer, "_get_executor", lambda: executor)
    monkeypatch.setattr(
        sports_isolated_renderer,
        "_release_parent_transient_memory",
        lambda: (0, False),
    )
    device_config = _DeviceConfigWithCache(tmp_path / "cache")
    identity = InstanceIdentity("sports-instance", 3, 7)
    store = SportsRegionCheckpointStore.for_device(device_config, identity)

    with pytest.raises(
        sports_isolated_renderer.SportsIsolatedCheckpointPending
    ) as pending:
        _render(
            device_config=device_config,
            instance_identity=identity,
        )

    assert pending.value.completed_regions == ("esports",)
    assert store is not None
    assert store.path.is_file()
    assert "sports-instance" not in str(store.path)
    assert store.path.parent.name == "isolated-checkpoints"
