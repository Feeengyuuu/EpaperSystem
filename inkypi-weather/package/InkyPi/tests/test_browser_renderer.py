import os
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from src.runtime.cache_lifecycle import (
    CleanupBudget,
    LifecycleAggregate,
    LifecycleAllowance,
)
from src.runtime.refresh_contracts import TaskContext
from src.utils import browser_renderer as browser_renderer_module
from src.utils.browser_renderer import BrowserRenderer


def _context(seconds=2):
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + seconds,
    )


def _cleanup_allowance(
    *,
    scanned=64,
    deleted=16,
    deleted_bytes=1024 * 1024,
    duration=1.0,
    clock=lambda: 0.0,
):
    return LifecycleAllowance(
        CleanupBudget(
            max_scanned_entries=scanned,
            max_deleted_entries=deleted,
            max_deleted_bytes=deleted_bytes,
            max_duration_seconds=duration,
        ).start(clock()),
        LifecycleAggregate(),
        clock=clock,
    )


def _abandoned_job(root, name, *, now, age, payload=b"residue"):
    job = root / name
    job.mkdir(parents=True)
    (job / "payload.bin").write_bytes(payload)
    modified = now - age
    os.utime(job / "payload.bin", (modified, modified))
    os.utime(job, (modified, modified))
    return job


class CleanupSlot:
    def __init__(self, available=True):
        self.available = available
        self.acquire_calls = []
        self.release_calls = 0

    def acquire(self, blocking=True, timeout=None):
        self.acquire_calls.append((blocking, timeout))
        return self.available

    def release(self):
        self.release_calls += 1


class TimeoutProcess:
    returncode = None
    pid = 1234

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls <= 2:
            raise subprocess.TimeoutExpired("chromium", timeout)
        self.returncode = -9
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def poll(self):
        return self.returncode


def test_timeout_terminates_kills_waits_and_removes_all_temp_paths(tmp_path):
    process = TimeoutProcess()
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: process,
    )

    result = renderer.render_html(
        "<p>x</p>",
        viewport=(800, 480),
        context=_context(),
        timeout_seconds=0.01,
    )

    assert result is None
    assert process.terminated
    assert process.killed
    assert process.wait_calls == 3
    assert renderer.active_processes == ()
    assert list(tmp_path.iterdir()) == []


def test_each_render_uses_clean_profile_without_disabling_sandbox(tmp_path):
    commands = []

    class SuccessProcess:
        returncode = 0
        pid = 2222

        def __init__(self, command):
            commands.append(command)
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (8, 8), "white").save(output)

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda command, **_kwargs: SuccessProcess(command),
        run_as_root=False,
    )

    first = renderer.render_html("<p>one</p>", viewport=(800, 480), context=_context())
    second = renderer.render_html("<p>two</p>", viewport=(800, 480), context=_context())

    assert first.size == (8, 8)
    assert second.size == (8, 8)
    profiles = [
        next(arg for arg in command if arg.startswith("--user-data-dir="))
        for command in commands
    ]
    assert profiles[0] != profiles[1]
    assert all("--no-sandbox" not in command for command in commands)
    assert all("--no-zygote" not in command for command in commands)
    assert all("--disk-cache-size=1" in command for command in commands)
    assert all("--in-process-gpu" in command for command in commands)
    assert all("--use-gl=swiftshader" in command for command in commands)
    assert all("--js-flags=--jitless" in command for command in commands)
    assert all("--disable-zero-copy" in command for command in commands)
    assert all("--virtual-time-budget=2000" in command for command in commands)
    assert all("--virtual-time-budget=60000" not in command for command in commands)
    assert all(
        "--disable-gpu-memory-buffer-compositor-resources" in command
        for command in commands
    )
    assert all(
        any(argument.startswith("--proxy-server=http://127.0.0.1:") for argument in command)
        for command in commands
    )
    assert all("--proxy-bypass-list=<-loopback>" in command for command in commands)
    assert list(tmp_path.iterdir()) == []


def test_root_renderer_adds_required_no_sandbox_without_disabling_zygote(tmp_path):
    commands = []

    class FailedProcess:
        returncode = 1
        pid = 2444

        def __init__(self, command):
            commands.append(command)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda command, **_kwargs: FailedProcess(command),
        run_as_root=True,
    )

    assert renderer.render_html("<p>root</p>", viewport=(80, 48), context=_context()) is None
    assert "--no-sandbox" in commands[0]
    assert "--no-zygote" not in commands[0]


def test_html_timeout_opens_cross_document_circuit_until_cooldown(tmp_path):
    now = {"value": 0.0}
    launches = []

    def popen(*_args, **_kwargs):
        process = TimeoutProcess()
        process.pid += len(launches)
        launches.append(process)
        return process

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        clock=lambda: now["value"],
        html_circuit_ttl_seconds=60,
    )

    assert renderer.render_html(
        "<p>first timestamp</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
    ) is None
    assert renderer.render_html(
        "<p>different timestamp</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
    ) is None
    assert len(launches) == 1

    now["value"] = 61.0
    assert renderer.render_html(
        "<p>after cooldown</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
    ) is None
    assert len(launches) == 2


def test_two_renderer_instances_never_overlap(tmp_path):
    state_lock = threading.Lock()
    active = 0
    maximum = 0
    next_pid = 3000

    class SlowProcess:
        returncode = 0

        def __init__(self, command):
            nonlocal active, maximum, next_pid
            with state_lock:
                next_pid += 1
                self.pid = next_pid
                active += 1
                maximum = max(maximum, active)
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (4, 4), "white").save(output)

        def wait(self, timeout=None):
            nonlocal active
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            pass

    popen = lambda command, **_kwargs: SlowProcess(command)
    renderers = [
        BrowserRenderer(binary="chromium", temp_root=tmp_path, popen=popen),
        BrowserRenderer(binary="chromium", temp_root=tmp_path, popen=popen),
    ]
    results = []

    threads = [
        threading.Thread(
            target=lambda renderer=renderer: results.append(
                renderer.render_html("<p>x</p>", viewport=(80, 48), context=_context())
            )
        )
        for renderer in renderers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert maximum == 1


def test_remote_url_requires_validator_and_negative_cache_is_bounded(tmp_path):
    calls = []
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: calls.append(True),
    )

    assert renderer.render_url(
        "https://example.test/page?secret=value",
        viewport=(800, 480),
        context=_context(),
        validator=None,
    ) is None
    assert renderer.render_url(
        "https://example.test/page?secret=value",
        viewport=(800, 480),
        context=_context(),
        validator=lambda _url: False,
    ) is None

    assert calls == []
    assert renderer.negative_cache_size <= 1


def test_repeated_failures_leave_no_processes_or_temp_growth(tmp_path):
    next_pid = 5000

    class FailedProcess:
        returncode = 1

        def __init__(self):
            nonlocal next_pid
            next_pid += 1
            self.pid = next_pid

        def wait(self, timeout=None):
            return 1

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            pass

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: FailedProcess(),
    )

    for index in range(100):
        assert renderer.render_html(
            f"<p>{index}</p>",
            viewport=(80, 48),
            context=_context(),
        ) is None

    assert renderer.active_processes == ()
    assert next_pid == 5001
    assert renderer.negative_cache_size == 1
    assert list(tmp_path.iterdir()) == []


def test_abandoned_browser_job_cleanup_uses_global_slot_and_two_hour_grace(
    tmp_path,
    monkeypatch,
):
    now = 20_000.0
    stale = 2 * 60 * 60
    old_job = _abandoned_job(tmp_path, "render-old", now=now, age=stale + 1)
    recent_job = _abandoned_job(tmp_path, "render-recent", now=now, age=stale)
    slot = CleanupSlot()
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", slot)
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: pytest.fail("cleanup started Chromium"),
        clock=lambda: 0.0,
    )

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert not old_job.exists()
    assert recent_job.is_dir()
    assert aggregate.deleted_entries == 1
    assert aggregate.deleted_bytes == len(b"residue")
    assert slot.acquire_calls == [(False, None)]
    assert slot.release_calls == 1


def test_active_browser_process_or_busy_slot_skips_cleanup(tmp_path, monkeypatch):
    now = 20_000.0
    stale = 2 * 60 * 60
    job = _abandoned_job(tmp_path, "render-busy", now=now, age=stale + 1)
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    busy_slot = CleanupSlot(available=False)
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", busy_slot)
    busy = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert job.is_dir()
    assert busy.deleted_entries == 0
    assert busy.backlog_entries == 1
    assert busy_slot.release_calls == 0

    active_slot = CleanupSlot()
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", active_slot)
    active_process = SimpleNamespace(pid=8822)
    renderer._register_process(active_process)
    try:
        active = renderer.cleanup_abandoned_jobs(
            now_epoch=now,
            stale_seconds=stale,
            allowance=_cleanup_allowance(),
            dry_run=False,
        )
    finally:
        renderer._unregister_process(active_process)

    assert job.is_dir()
    assert active.deleted_entries == 0
    assert active.backlog_entries == 1
    assert active_slot.release_calls == 1


def test_browser_cleanup_renames_to_gc_before_rmtree_and_recovers_gc_tombstone(
    tmp_path,
    monkeypatch,
):
    now = 20_000.0
    stale = 2 * 60 * 60
    job = _abandoned_job(tmp_path, "render-crashed", now=now, age=stale + 1)
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)
    real_rmtree = browser_renderer_module.shutil.rmtree
    removals = []

    def interrupted_rmtree(path, *args, **kwargs):
        removals.append(Path(path).name)
        raise OSError("simulated cleanup interruption")

    monkeypatch.setattr(browser_renderer_module.shutil, "rmtree", interrupted_rmtree)
    interrupted = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    tombstone = tmp_path / ".gc-render-crashed"
    assert not job.exists()
    assert tombstone.is_dir()
    assert removals == [tombstone.name]
    assert interrupted.deleted_entries == 0
    assert interrupted.error_count == 1

    monkeypatch.setattr(browser_renderer_module.shutil, "rmtree", real_rmtree)
    recovered = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert not tombstone.exists()
    assert recovered.deleted_entries == 1


def test_browser_cleanup_rejects_symlink_reparse_and_unknown_children(
    tmp_path,
    monkeypatch,
):
    now = 20_000.0
    stale = 2 * 60 * 60
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"keep")
    symlink = tmp_path / "render-symlink"
    try:
        symlink.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        symlink = None
    unknown = _abandoned_job(tmp_path, "unknown-job", now=now, age=stale + 1)
    reparse = _abandoned_job(tmp_path, "render-reparse", now=now, age=stale + 1)
    real_lstat = browser_renderer_module.os.lstat

    def mark_reparse(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if Path(path) != reparse:
            return info
        return SimpleNamespace(
            st_mode=info.st_mode,
            st_dev=info.st_dev,
            st_ino=info.st_ino,
            st_size=info.st_size,
            st_mtime=info.st_mtime,
            st_mtime_ns=info.st_mtime_ns,
            st_file_attributes=0x400,
        )

    monkeypatch.setattr(browser_renderer_module.os, "lstat", mark_reparse)
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert sentinel.read_bytes() == b"keep"
    if symlink is not None:
        assert symlink.is_symlink()
    assert unknown.is_dir()
    assert reparse.is_dir()
    assert aggregate.deleted_entries == 0
    assert aggregate.skipped_unsafe >= 2 + int(symlink is not None)


def test_browser_cleanup_rejects_symlink_or_reparse_temp_root(tmp_path, monkeypatch):
    now = 20_000.0
    stale = 2 * 60 * 60
    target = tmp_path / "target"
    target.mkdir()
    job = _abandoned_job(target, "render-root", now=now, age=stale + 1)
    root = tmp_path / "root-link"
    try:
        root.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        root = target
        real_lstat = browser_renderer_module.os.lstat

        def mark_root_reparse(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if Path(path) != root:
                return info
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=0x400,
            )

        monkeypatch.setattr(browser_renderer_module.os, "lstat", mark_root_reparse)
    slot = CleanupSlot()
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", slot)
    renderer = BrowserRenderer(binary="chromium", temp_root=root, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert job.is_dir()
    assert aggregate.deleted_entries == 0
    assert aggregate.skipped_unsafe == 1
    assert aggregate.backlog_entries == 1
    assert slot.release_calls == 1


def test_browser_cleanup_scan_limit_stops_before_any_delete(tmp_path):
    now = 20_000.0
    stale = 2 * 60 * 60
    jobs = {
        _abandoned_job(
            tmp_path,
            f"render-scan-{index}",
            now=now,
            age=stale + 1,
        )
        for index in range(2)
    }
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(scanned=1),
        dry_run=False,
    )

    assert all(job.is_dir() for job in jobs)
    assert aggregate.scanned_entries == 1
    assert aggregate.candidate_entries == 0
    assert aggregate.deleted_entries == 0
    assert aggregate.backlog_entries == 1


def test_browser_cleanup_stat_change_skips_rename_and_remove(tmp_path, monkeypatch):
    now = 20_000.0
    stale = 2 * 60 * 60
    job = _abandoned_job(tmp_path, "render-raced", now=now, age=stale + 1)
    real_lstat = browser_renderer_module.os.lstat
    job_stats = 0

    def change_second_job_stat(path, *args, **kwargs):
        nonlocal job_stats
        info = real_lstat(path, *args, **kwargs)
        if Path(path) != job:
            return info
        job_stats += 1
        if job_stats == 1:
            return info
        return SimpleNamespace(
            st_mode=info.st_mode,
            st_dev=info.st_dev,
            st_ino=info.st_ino,
            st_size=info.st_size,
            st_mtime=info.st_mtime,
            st_mtime_ns=info.st_mtime_ns + 1,
            st_file_attributes=getattr(info, "st_file_attributes", 0),
        )

    monkeypatch.setattr(browser_renderer_module.os, "lstat", change_second_job_stat)
    monkeypatch.setattr(
        browser_renderer_module.os,
        "rename",
        lambda *_args, **_kwargs: pytest.fail("changed job was renamed"),
    )
    monkeypatch.setattr(
        browser_renderer_module.shutil,
        "rmtree",
        lambda *_args, **_kwargs: pytest.fail("changed job was removed"),
    )
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert job.is_dir()
    assert aggregate.candidate_entries == 1
    assert aggregate.deleted_entries == 0
    assert aggregate.skipped_unsafe == 1
    assert aggregate.backlog_entries == 1


def test_browser_cleanup_records_job_tree_io_failure_as_error(tmp_path, monkeypatch):
    now = 20_000.0
    stale = 2 * 60 * 60
    job = _abandoned_job(tmp_path, "render-io-error", now=now, age=stale + 1)
    real_scandir = browser_renderer_module.os.scandir

    def fail_job_scan(path):
        if Path(path) == job:
            raise PermissionError("simulated unreadable job")
        return real_scandir(path)

    monkeypatch.setattr(browser_renderer_module.os, "scandir", fail_job_scan)
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert job.is_dir()
    assert aggregate.deleted_entries == 0
    assert aggregate.error_count == 1
    assert aggregate.skipped_unsafe == 0
    assert aggregate.backlog_entries == 1


def test_browser_cleanup_obeys_shared_budget_and_returns_aggregate_only(
    tmp_path,
    monkeypatch,
):
    now = 20_000.0
    stale = 2 * 60 * 60
    for index in range(3):
        _abandoned_job(
            tmp_path,
            f"render-budget-{index}",
            now=now,
            age=stale + 1,
            payload=b"four",
        )
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    limited_allowance = _cleanup_allowance(
        scanned=8,
        deleted=1,
        deleted_bytes=4,
    )
    limited = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=limited_allowance,
        dry_run=False,
    )

    expected_keys = {
        "scanned_entries",
        "candidate_entries",
        "deleted_entries",
        "deleted_bytes",
        "retained_current",
        "retained_last_good",
        "retained_recent",
        "skipped_unsafe",
        "error_count",
        "backlog_entries",
    }
    assert limited is limited_allowance.aggregate
    assert set(vars(limited)) == expected_keys
    assert limited.deleted_entries == 1
    assert limited.deleted_bytes == 4
    assert limited.backlog_entries == 1
    assert all(not isinstance(value, (Path, str)) for value in vars(limited).values())
    remaining_after_first = set(tmp_path.glob("render-budget-*"))
    repeated = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=limited_allowance,
        dry_run=False,
    )
    assert repeated is limited
    assert repeated.deleted_entries == 1
    assert set(tmp_path.glob("render-budget-*")) == remaining_after_first

    dry_root = tmp_path / "dry"
    dry_job = _abandoned_job(
        dry_root,
        "render-dry",
        now=now,
        age=stale + 1,
        payload=b"four",
    )
    dry_renderer = BrowserRenderer(binary="chromium", temp_root=dry_root, clock=lambda: 0.0)
    dry_allowance = _cleanup_allowance()
    dry = dry_renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=dry_allowance,
        dry_run=True,
    )
    assert dry_job.is_dir()
    assert dry is dry_allowance.aggregate
    assert dry.candidate_entries == 1
    assert dry.deleted_entries == 0
    real = dry_renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )
    assert not dry_job.exists()
    assert real.candidate_entries == dry.candidate_entries

    deadline_root = tmp_path / "deadline"
    deadline_job = _abandoned_job(
        deadline_root,
        "render-deadline",
        now=now,
        age=stale + 1,
    )
    ticks = iter((0.0, 1.0))
    deadline_clock = lambda: next(ticks, 1.0)
    deadline_renderer = BrowserRenderer(
        binary="chromium",
        temp_root=deadline_root,
        clock=deadline_clock,
    )
    timed_out = deadline_renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(duration=0.5, clock=deadline_clock),
        dry_run=False,
    )
    assert deadline_job.is_dir()
    assert timed_out.deleted_entries == 0
    assert timed_out.backlog_entries == 1
