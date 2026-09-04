"""Bounded virtual-time soak across the real scheduler, cache and display seams."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

import display.display_manager as display_manager_module
import display.display_transaction as display_transaction_module
import refresh_task as refresh_task_module
from display.display_manager import DisplayManager
from runtime.refresh_contracts import JobStatus
from runtime.long_task_executor import current_task_context
from runtime.presentation_cache import PreparedPresentationCandidate, prepared_presentation_path
from runtime.runtime_state import PresentationRequestState, RefreshLane
from refresh_task import RefreshTask
from tests.test_refresh_task import (
    PresentationBankPlugin,
    RuntimeClock,
    RuntimeDeviceConfig,
    _presentation_manifest,
    _runtime_playlist,
    _runtime_plugin_data,
)


START = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)


def _make_machine(tmp_path, monkeypatch, *, supports_theme=False):
    clock = RuntimeClock(wall=START.timestamp())

    class VirtualDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(clock.wall_time(), tz=tz)

    monkeypatch.setattr(refresh_task_module, "datetime", VirtualDateTime)
    monkeypatch.setattr(display_transaction_module, "datetime", VirtualDateTime)
    monkeypatch.setattr(
        refresh_task_module.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(available=512 * 1024 * 1024, percent=20),
    )
    monkeypatch.setattr(
        refresh_task_module.psutil,
        "swap_memory",
        lambda: SimpleNamespace(percent=0),
    )

    class PhysicalPanel:
        """Only the hardware boundary is replaced; display commits remain real."""

        def __init__(self, _config):
            self.writes = []

        def display_image(self, image, image_settings=()):
            self.writes.append((clock.monotonic(), image.copy()))

    monkeypatch.setattr(display_manager_module, "MockDisplay", PhysicalPanel)
    playlist = _runtime_playlist(
        *[
            _runtime_plugin_data(
                f"presentation_plugin_{index}",
                f"Feed {index}",
                latest_refresh_time=START.isoformat(),
                interval=3600,
            )
            for index in range(3)
        ],
        name="Whole Machine",
    )
    config = RuntimeDeviceConfig(tmp_path / "cache", [playlist])
    config.current_image_file = str(tmp_path / "display" / "current.png")
    config.display_dir = tmp_path / "display"
    config.config.update(
        {
            "display_type": "mock",
            "orientation": "horizontal",
            "image_settings": {},
            "timezone": "UTC",
            "active_theme": "day",
            "theme_mode": "day",
            "plugin_cycle_interval_seconds": 300,
            "display_triggered_refresh_enabled": False,
        }
    )
    config.refresh_info.refresh_time = (START - timedelta(seconds=300)).isoformat()
    manifests = {
        plugin.plugin_id: _presentation_manifest(
            plugin.plugin_id, provider_refresh=True, supports_theme=supports_theme
        )
        for plugin in playlist.plugins
    }
    config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": True,
        "_manifest": manifests[plugin_id],
    }
    providers = {
        plugin.plugin_id: PresentationBankPlugin(prepared_color=color)
        for plugin, color in zip(playlist.plugins, ("red", "green", "blue"))
    }
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda plugin_config: providers[plugin_config["id"]],
    )
    display = DisplayManager(config)
    task = RefreshTask(
        config, display, clock=clock.monotonic, wall_clock=clock.wall_time
    )
    instances = [plugin.snapshot() for plugin in playlist.plugins]
    # A saved ordered shuffle bag is real playlist state, not a fake scheduler.
    playlist.plugin_rotation_pool = [item.instance_uuid for item in instances]
    playlist.plugin_rotation_queue = [item.instance_uuid for item in instances]
    playlist.plugin_rotation_recent_history = []
    for instance in instances:
        path = Path(task.cache_path_for_snapshot(instance))
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 16), "black").save(path)
        task.runtime_state.record_success(
            instance.instance_uuid, START.isoformat(), lane=RefreshLane.DATA
        )
    return task, config, clock, instances, providers, display


def _run_until_two_rounds(task, clock, instances, display, *, max_seconds=1800):
    commits = []
    jobs = {}
    last_commit = None
    for _second in range(max_seconds + 1):
        # This is the production non-blocking worker entrypoint, not a second
        # implementation of scheduling, retries, preparation or rotation.
        entry = task._run_one_iteration_for_test()
        if entry is not None:
            # Observe completion immediately, before the real queue's bounded
            # terminal-history TTL legitimately removes old completed jobs.
            jobs[entry.job.id] = task.refresh_queue.get_job(entry.job.id)
        current = display.transaction.current()
        if current is not None and current.commit_id != last_commit:
            last_commit = current.commit_id
            instance_uuid = current.logical_target["instance_uuid"]
            state = task.runtime_state.snapshot().instances[instance_uuid]
            commits.append((instance_uuid, current, state.presentation_receipt))
        counts = Counter(instance_uuid for instance_uuid, _, _ in commits)
        if all(counts[instance.instance_uuid] >= 2 for instance in instances):
            break
        clock.advance(1)
    return commits, list(jobs.values())


def test_every_feed_advances_for_two_rotations_with_an_existing_presentation(
    tmp_path, monkeypatch
):
    task, _config, clock, instances, _providers, display = _make_machine(
        tmp_path, monkeypatch
    )
    existing = PresentationRequestState(
        request_id="11111111111111111111111111111111",
        requested_at=(START - timedelta(minutes=20)).isoformat(),
        structural_generation=instances[1].structural_generation,
        settings_revision=instances[1].settings_revision,
        origin_display_commit_id="22222222222222222222222222222222",
        origin_theme_mode=None,
    )
    assert task.runtime_state.request_presentation(instances[1].instance_uuid, existing)

    commits, jobs = _run_until_two_rounds(task, clock, instances, display)

    counts = Counter(instance_uuid for instance_uuid, _, _ in commits)
    assert all(counts[instance.instance_uuid] >= 2 for instance in instances), counts
    assert jobs and all(job is not None and job.status is JobStatus.SUCCEEDED for job in jobs)
    assert len(display.display.writes) == len(commits)
    assert all(commit.hardware_written for _, commit, _ in commits)
    assert any(
        receipt is not None and receipt.request_id == existing.request_id
        for instance_uuid, _, receipt in commits
        if instance_uuid == instances[1].instance_uuid
    )
    for instance in instances:
        receipts = [
            receipt
            for instance_uuid, _, receipt in commits
            if instance_uuid == instance.instance_uuid and receipt is not None
        ]
        # Boot may display the first last-good cache before its first prepare;
        # every feed must nevertheless adopt fresh prepared content by round 2.
        assert receipts
        assert all(
            receipt.display_commit_id == commit.commit_id
            for instance_uuid, commit, receipt in commits
            if instance_uuid == instance.instance_uuid and receipt is not None
        )
        state = task.runtime_state.snapshot().instances[instance.instance_uuid]
        assert (
            state.presentation_request is None
            or state.presentation_request.request_id != existing.request_id
        )
        assert state.last_good_cache is not None


def test_expired_day_prepare_backs_off_even_when_an_old_night_bank_exists(
    tmp_path, monkeypatch
):
    task, config, clock, instances, providers, display = _make_machine(
        tmp_path, monkeypatch, supports_theme=True
    )
    target = instances[1]

    class ExpiringProvider(PresentationBankPlugin):
        def prepare_presentation(self, *args, **kwargs):
            context = current_task_context()
            assert context is not None
            # Simulate only external work exhausting its given time budget.
            clock.advance(context.remaining_seconds() + 1)
            context.raise_if_cancelled()

    providers[target.plugin_id] = ExpiringProvider()
    existing = PresentationRequestState(
        request_id="55555555555555555555555555555555",
        requested_at=(START - timedelta(minutes=20)).isoformat(),
        structural_generation=target.structural_generation,
        settings_revision=target.settings_revision,
        origin_display_commit_id="66666666666666666666666666666666",
        origin_theme_mode="night",
    )
    assert task.runtime_state.request_presentation(target.instance_uuid, existing)
    root = Path(config.plugin_image_dir) / ".refresh-presentation"
    candidate = PreparedPresentationCandidate(
        instance_uuid=target.instance_uuid,
        structural_generation=target.structural_generation,
        settings_revision=target.settings_revision,
        theme_mode="night",
        request_id=existing.request_id,
        cache_path=prepared_presentation_path(
            root,
            target.instance_uuid,
            target.structural_generation,
            target.settings_revision,
            "night",
            existing.request_id,
        ),
    )
    task.presentation_cache.save(candidate, Image.new("RGB", (32, 16), "navy"))
    assert task.runtime_state.mark_presentation_prepared(
        target.instance_uuid,
        existing.request_id,
        (START - timedelta(minutes=19)).isoformat(),
        "night",
    )
    expired_job = None
    for _ in range(10):
        entry = task._run_one_iteration_for_test()
        if entry is not None and entry.command.instance_uuid == target.instance_uuid:
            expired_job = task.refresh_queue.get_job(entry.job.id)
            if expired_job.status is JobStatus.ABANDONED:
                break
        clock.advance(1)

    assert expired_job is not None and expired_job.status is JobStatus.ABANDONED
    assert expired_job.error_code == "deadline_expired"
    state = task.runtime_state.snapshot().instances[target.instance_uuid]
    assert state.presentation.last_failure_at is not None
    assert state.presentation.next_retry_at is not None
    assert state.presentation_request.request_id == existing.request_id
    assert state.presentation_request.prepared_theme_mode == "night"
    assert state.presentation_receipt is None
    assert display.transaction.current().logical_target["instance_uuid"] != target.instance_uuid


def test_a_provider_failure_recovers_without_stalling_other_feeds(
    tmp_path, monkeypatch
):
    task, _config, clock, instances, providers, display = _make_machine(
        tmp_path, monkeypatch
    )

    class RecoveringProvider(PresentationBankPlugin):
        def __init__(self):
            super().__init__(prepared_color="green")
            self.fail_next_fetch = True

        def prepare_presentation(self, *args, **kwargs):
            if self.fail_next_fetch:
                self.fail_next_fetch = False
                raise RuntimeError("temporary external feed failure")
            return super().prepare_presentation(*args, **kwargs)

    providers[instances[1].plugin_id] = RecoveringProvider()
    existing = PresentationRequestState(
        request_id="33333333333333333333333333333333",
        requested_at=(START - timedelta(minutes=20)).isoformat(),
        structural_generation=instances[1].structural_generation,
        settings_revision=instances[1].settings_revision,
        origin_display_commit_id="44444444444444444444444444444444",
        origin_theme_mode=None,
    )
    assert task.runtime_state.request_presentation(instances[1].instance_uuid, existing)

    commits, jobs = _run_until_two_rounds(
        task, clock, instances, display, max_seconds=2400
    )

    counts = Counter(instance_uuid for instance_uuid, _, _ in commits)
    assert all(counts[instance.instance_uuid] >= 2 for instance in instances), counts
    assert all(
        job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED}
        for job in jobs
    )
    recovery = next(
        commit
        for instance_uuid, commit, receipt in commits
        if instance_uuid == instances[1].instance_uuid
        and receipt is not None
        and receipt.request_id == existing.request_id
    )
    assert recovery.hardware_written is True
    write_times = [when for when, _image in display.display.writes]
    assert all(
        later - earlier <= 301
        for earlier, later in zip(write_times, write_times[1:])
    )
    state = task.runtime_state.snapshot().instances[instances[1].instance_uuid]
    assert (
        state.presentation_request is None
        or state.presentation_request.request_id != existing.request_id
    )
    assert state.presentation.last_error == "temporary external feed failure"
    assert state.presentation.last_failure_at is not None
    assert state.presentation.last_success_at > state.presentation.last_failure_at
    assert state.presentation.next_retry_at is None
    assert len(display.display.writes) == len(commits)
