from dataclasses import asdict, replace
import os
from pathlib import Path
import stat

import pytest

from src.model import PluginInstance
from src.runtime import cache_lifecycle as cache_lifecycle_module
from src.runtime.cache_catalog import (
    authoritative_cache_path,
    cache_identity_prefix,
)
from src.runtime.cache_lifecycle import (
    ArtifactClass,
    CacheLifecycleManager,
    CleanupBudget,
    DiskPressureTier,
    DiskThresholds,
    LifecycleAggregate,
    LifecycleAllowance,
    LifecycleBudget,
    build_cache_retention,
    classify_disk_pressure,
)
from src.runtime.presentation_cache import prepared_presentation_path
from src.runtime.runtime_state import (
    InstanceRuntimeState,
    LastGoodCacheState,
    PresentationCommitReceipt,
    PresentationRequestState,
)


MIB = 1024 * 1024
VALID_UUID = "123e4567-e89b-12d3-a456-426614174000"
REQUEST_ID = "0123456789abcdef0123456789abcdef"
RECEIPT_ID = "fedcba9876543210fedcba9876543210"


def _snapshot(
    *,
    instance_uuid="instance-one",
    plugin_id="weather",
    name="Home",
    generation=2,
    revision=5,
):
    return PluginInstance(
        plugin_id,
        name,
        instance_uuid=instance_uuid,
        structural_generation=generation,
        settings_revision=revision,
    ).snapshot()


def _last_good(*, mode="day", generation=2, revision=5):
    return LastGoodCacheState(
        theme_mode=mode,
        structural_generation=generation,
        settings_revision=revision,
        promoted_at="2026-07-12T10:00:00+00:00",
    )


def _request(
    *,
    request_id=REQUEST_ID,
    generation=2,
    revision=5,
    prepared=True,
    mode="day",
):
    return PresentationRequestState(
        request_id=request_id,
        requested_at="2026-07-12T10:00:00+00:00",
        structural_generation=generation,
        settings_revision=revision,
        origin_theme_mode=mode,
        origin_display_commit_id="display-1",
        prepared_at=(
            "2026-07-12T10:01:00+00:00" if prepared else None
        ),
        prepared_theme_mode=mode if prepared else None,
    )


def _receipt(
    *,
    request_id=RECEIPT_ID,
    generation=2,
    revision=5,
    mode="night",
):
    return PresentationCommitReceipt(
        request_id=request_id,
        committed_at="2026-07-12T10:02:00+00:00",
        display_commit_id="display-2",
        structural_generation=generation,
        settings_revision=revision,
        theme_mode=mode,
    )


def _write(path, payload=b"cache"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _age(path, *, now, seconds):
    timestamp = float(now) - float(seconds)
    os.utime(path, (timestamp, timestamp))
    return Path(path)


def _retention(plugin_root, instances=(), runtime_instances=None, display=None):
    return build_cache_retention(
        plugin_root,
        tuple(instances),
        runtime_instances or {},
        display,
    )


def _candidate_paths(plan):
    return {candidate.path for candidate in plan.candidates}


def test_cleanup_budget_starts_one_shared_absolute_lifecycle_budget():
    configured = CleanupBudget(20, 4, 8 * MIB, 0.75)

    running = configured.start(100.25)

    assert running == LifecycleBudget(
        max_scanned=20,
        max_deleted=4,
        max_deleted_bytes=8 * MIB,
        deadline_monotonic=101.0,
    )
    assert LifecycleAggregate() == LifecycleAggregate(
        scanned_entries=0,
        candidate_entries=0,
        deleted_entries=0,
        deleted_bytes=0,
        retained_current=0,
        retained_last_good=0,
        retained_recent=0,
        skipped_unsafe=0,
        error_count=0,
        backlog_entries=0,
    )


def test_lifecycle_allowance_shares_counters_and_checks_yield_and_deadline():
    now = [10.0]
    should_yield = [False]
    aggregate = LifecycleAggregate()
    allowance = LifecycleAllowance(
        LifecycleBudget(2, 1, 8, 11.0),
        aggregate,
        clock=lambda: now[0],
        should_yield=lambda: should_yield[0],
    )

    assert allowance.consume_scan() is True
    assert allowance.consume_scan() is True
    assert allowance.consume_scan() is False
    assert aggregate.scanned_entries == 2
    assert aggregate.backlog_entries == 1

    aggregate.backlog_entries = 0
    assert allowance.can_delete(8) is True
    allowance.consume_delete(8)
    assert aggregate.deleted_entries == 1
    assert aggregate.deleted_bytes == 8
    assert allowance.can_delete(1) is False
    assert aggregate.backlog_entries == 1

    yielded = LifecycleAllowance(
        LifecycleBudget(10, 10, 10, 11.0),
        LifecycleAggregate(),
        clock=lambda: now[0],
        should_yield=lambda: should_yield[0],
    )
    should_yield[0] = True
    assert yielded.consume_scan() is False
    assert yielded.can_delete(1) is False
    assert yielded.aggregate.backlog_entries == 1

    should_yield[0] = False
    now[0] = 11.0
    expired = LifecycleAllowance(
        LifecycleBudget(10, 10, 10, 11.0),
        LifecycleAggregate(),
        clock=lambda: now[0],
    )
    assert expired.consume_scan() is False
    assert expired.can_delete(1) is False
    assert expired.aggregate.backlog_entries == 1


@pytest.mark.parametrize(
    ("total", "used", "free", "expected"),
    [
        (10_000, 5_000, 5_000, DiskPressureTier.HEALTHY),
        (10_000, 8_600, 1_400, DiskPressureTier.SOFT),
        (10_000, 9_200, 800, DiskPressureTier.HARD),
        (10_000, 1_000, 400, DiskPressureTier.HARD),
        (0, 0, 0, DiskPressureTier.HARD),
        (10_000, -1, 10_001, DiskPressureTier.HARD),
        (None, 1, 1, DiskPressureTier.HARD),
    ],
)
def test_disk_pressure_classification_is_hard_first_and_fails_closed(
    total,
    used,
    free,
    expected,
):
    thresholds = DiskThresholds(
        soft_min_free_bytes=1_500,
        hard_min_free_bytes=500,
        soft_max_used_percent=85,
        hard_max_used_percent=92,
    )

    assert classify_disk_pressure(total, used, free, thresholds) is expected


def test_disk_pressure_tier_is_a_string_enum():
    assert isinstance(DiskPressureTier.HEALTHY, DiskPressureTier)
    assert str(DiskPressureTier.HEALTHY.value) == "healthy"


def test_retention_keeps_all_current_revision_modes_and_same_revision_last_good(
    tmp_path,
):
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    instance = _snapshot()
    day = _write(
        authoritative_cache_path(
            cache_root,
            instance.instance_uuid,
            instance.structural_generation,
            instance.settings_revision,
            "day",
        )
    )
    current_display = _write(plugin_root / "current_image.png")
    runtime_instances = {
        instance.instance_uuid: InstanceRuntimeState(
            last_good_cache=_last_good(mode="day")
        )
    }

    retention = build_cache_retention(
        plugin_root,
        (instance,),
        runtime_instances,
        current_display,
    )

    expected_modes = {
        Path(
            authoritative_cache_path(
                cache_root,
                instance.instance_uuid,
                2,
                5,
                mode,
            )
        )
        for mode in (None, "day", "night")
    }
    assert retention.current_exact_paths == frozenset(expected_modes)
    assert retention.same_revision_last_good_paths == frozenset({day})
    assert retention.current_alias_paths == frozenset(
        {plugin_root / "weather_Home.png"}
    )
    assert retention.current_display_path == current_display
    assert retention.current_by_prefix == {
        cache_identity_prefix(instance.instance_uuid): (2, 5)
    }
    assert retention.current_has_displayable == frozenset(
        {cache_identity_prefix(instance.instance_uuid)}
    )


def test_mismatched_last_good_revision_is_not_protected_as_current(tmp_path):
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    instance = _snapshot(revision=5)
    stale = _write(
        authoritative_cache_path(
            cache_root,
            instance.instance_uuid,
            2,
            4,
            "day",
        )
    )

    retention = build_cache_retention(
        plugin_root,
        (instance,),
        {
            instance.instance_uuid: InstanceRuntimeState(
                last_good_cache=_last_good(mode="day", revision=4)
            )
        },
        None,
    )

    assert stale not in retention.current_exact_paths
    assert stale not in retention.same_revision_last_good_paths


def test_old_revision_requires_24h_and_current_cache_before_prune(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    instance = _snapshot()
    _write(
        authoritative_cache_path(
            cache_root,
            instance.instance_uuid,
            2,
            5,
            "day",
        )
    )
    recent = _age(
        _write(
            authoritative_cache_path(
                cache_root,
                instance.instance_uuid,
                2,
                4,
                "day",
            )
        ),
        now=now,
        seconds=23 * 60 * 60,
    )
    stale = _age(
        _write(
            authoritative_cache_path(
                cache_root,
                instance.instance_uuid,
                1,
                4,
                "night",
            )
        ),
        now=now,
        seconds=25 * 60 * 60,
    )
    retention = _retention(plugin_root, (instance,))

    plan = CacheLifecycleManager(plugin_root).plan(
        retention,
        now_epoch=now,
        tier=DiskPressureTier.HEALTHY,
    )

    assert recent not in _candidate_paths(plan)
    assert stale in _candidate_paths(plan)
    assert next(
        item for item in plan.candidates if item.path == stale
    ).artifact_class is ArtifactClass.OLD_REVISION_CACHE


def test_current_cache_disappearing_after_retention_snapshot_restores_7d_grace(
    tmp_path,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    instance = _snapshot()
    current = _write(
        authoritative_cache_path(
            cache_root,
            instance.instance_uuid,
            2,
            5,
            "day",
        )
    )
    old = _age(
        _write(
            authoritative_cache_path(
                cache_root,
                instance.instance_uuid,
                2,
                4,
                "day",
            )
        ),
        now=now,
        seconds=2 * 24 * 60 * 60,
    )
    retention = _retention(plugin_root, (instance,))
    current.unlink()

    plan = CacheLifecycleManager(plugin_root).plan(
        retention,
        now_epoch=now,
        tier=DiskPressureTier.HARD,
    )

    assert old not in _candidate_paths(plan)


def test_current_cache_disappearing_after_plan_blocks_early_old_revision_unlink(
    tmp_path,
    monkeypatch,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    instance = _snapshot()
    current = _write(
        authoritative_cache_path(
            cache_root,
            instance.instance_uuid,
            2,
            5,
            "day",
        )
    )
    old = _age(
        _write(
            authoritative_cache_path(
                cache_root,
                instance.instance_uuid,
                2,
                4,
                "day",
            )
        ),
        now=now,
        seconds=2 * 24 * 60 * 60,
    )
    retention = _retention(plugin_root, (instance,))
    manager = CacheLifecycleManager(plugin_root, clock=lambda: 10)
    real_plan = manager._plan

    def remove_current_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        current.unlink()
        return plan

    monkeypatch.setattr(manager, "_plan", remove_current_after_plan)

    snapshot = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert old.exists()
    assert snapshot.deleted_entries == 0
    assert snapshot.backlog_entries >= 1


def test_current_cache_replacement_token_blocks_early_old_revision_unlink(
    tmp_path,
    monkeypatch,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    instance = _snapshot()
    current = _write(
        authoritative_cache_path(
            cache_root,
            instance.instance_uuid,
            2,
            5,
            "day",
        ),
        b"first",
    )
    old = _age(
        _write(
            authoritative_cache_path(
                cache_root,
                instance.instance_uuid,
                2,
                4,
                "day",
            )
        ),
        now=now,
        seconds=2 * 24 * 60 * 60,
    )
    retention = _retention(plugin_root, (instance,))
    manager = CacheLifecycleManager(plugin_root, clock=lambda: 10)
    real_plan = manager._plan

    def replace_current_after_plan(*args, **kwargs):
        plan = real_plan(*args, **kwargs)
        replacement = tmp_path / "replacement.png"
        replacement.write_bytes(b"replacement")
        os.replace(replacement, current)
        return plan

    monkeypatch.setattr(manager, "_plan", replace_current_after_plan)

    snapshot = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert current.read_bytes() == b"replacement"
    assert old.exists()
    assert snapshot.deleted_entries == 0
    assert snapshot.backlog_entries >= 1


def test_old_revision_without_current_gets_seven_day_grace(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    instance = _snapshot()
    recent = _age(
        _write(
            authoritative_cache_path(
                cache_root,
                instance.instance_uuid,
                2,
                4,
                None,
            )
        ),
        now=now,
        seconds=6 * 24 * 60 * 60,
    )
    stale = _age(
        _write(
            authoritative_cache_path(
                cache_root,
                instance.instance_uuid,
                1,
                4,
                "night",
            )
        ),
        now=now,
        seconds=8 * 24 * 60 * 60,
    )

    plan = CacheLifecycleManager(plugin_root).plan(
        _retention(plugin_root, (instance,)),
        now_epoch=now,
        tier=DiskPressureTier.HEALTHY,
    )

    assert recent not in _candidate_paths(plan)
    assert stale in _candidate_paths(plan)


def test_orphan_uuid_and_unowned_alias_get_seven_day_grace(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    orphan = _snapshot(instance_uuid="orphan")
    orphan_recent = _age(
        _write(
            authoritative_cache_path(cache_root, "orphan", 1, 1, None)
        ),
        now=now,
        seconds=6 * 24 * 60 * 60,
    )
    orphan_stale = _age(
        _write(
            authoritative_cache_path(cache_root, "deleted", 1, 1, None)
        ),
        now=now,
        seconds=8 * 24 * 60 * 60,
    )
    alias_recent = _age(
        _write(plugin_root / "old_recent.png"),
        now=now,
        seconds=6 * 24 * 60 * 60,
    )
    alias_stale = _age(
        _write(plugin_root / "old_stale.png"),
        now=now,
        seconds=8 * 24 * 60 * 60,
    )

    plan = CacheLifecycleManager(plugin_root).plan(
        _retention(plugin_root),
        now_epoch=now,
        tier=DiskPressureTier.HEALTHY,
    )

    paths = _candidate_paths(plan)
    assert orphan_recent not in paths
    assert alias_recent not in paths
    assert orphan_stale in paths
    assert alias_stale in paths
    classes = {item.path: item.artifact_class for item in plan.candidates}
    assert classes[orphan_stale] is ArtifactClass.ORPHAN_CACHE
    assert classes[alias_stale] is ArtifactClass.UNOWNED_ALIAS
    assert orphan.instance_uuid == "orphan"


def test_current_alias_display_and_user_saved_subdir_are_never_candidates(
    tmp_path,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    instance = _snapshot()
    current_alias = _age(
        _write(plugin_root / "weather_Home.png"),
        now=now,
        seconds=30 * 24 * 60 * 60,
    )
    current_display = _age(
        _write(plugin_root / "current_image.png"),
        now=now,
        seconds=30 * 24 * 60 * 60,
    )
    user_saved = _age(
        _write(plugin_root / "uploads" / "user.png"),
        now=now,
        seconds=30 * 24 * 60 * 60,
    )

    plan = CacheLifecycleManager(plugin_root).plan(
        _retention(plugin_root, (instance,), display=current_display),
        now_epoch=now,
        tier=DiskPressureTier.HARD,
    )

    paths = _candidate_paths(plan)
    assert current_alias not in paths
    assert current_display not in paths
    assert user_saved not in paths


def test_staging_and_both_atomic_temp_shapes_require_two_hours(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    staging = plugin_root / ".refresh-staging"
    cache_root = plugin_root / ".refresh-cache"
    stale_stage = _age(
        _write(
            authoritative_cache_path(staging, "staging", 1, 1, None)
        ),
        now=now,
        seconds=3 * 60 * 60,
    )
    recent_stage = _age(
        _write(
            authoritative_cache_path(staging, "recent", 1, 1, None)
        ),
        now=now,
        seconds=60 * 60,
    )
    unknown_stage = _age(
        _write(staging / "unknown-provider-data.bin"),
        now=now,
        seconds=30 * 24 * 60 * 60,
    )
    atomic_temp = _age(
        _write(cache_root / ".target.png.token.tmp"),
        now=now,
        seconds=3 * 60 * 60,
    )
    legacy_temp = _age(
        _write(cache_root / "target.tmp-123-456.png"),
        now=now,
        seconds=3 * 60 * 60,
    )
    unknown_temp = _age(
        _write(cache_root / "please_tmp_keep.bin"),
        now=now,
        seconds=30 * 24 * 60 * 60,
    )

    plan = CacheLifecycleManager(plugin_root).plan(
        _retention(plugin_root),
        now_epoch=now,
        tier=DiskPressureTier.HEALTHY,
    )

    paths = _candidate_paths(plan)
    assert stale_stage in paths
    assert recent_stage not in paths
    assert unknown_stage not in paths
    assert atomic_temp in paths
    assert legacy_temp in paths
    assert unknown_temp not in paths
    assert {
        item.artifact_class
        for item in plan.candidates
        if item.path in {atomic_temp, legacy_temp}
    } == {ArtifactClass.ATOMIC_TEMP}


def test_symlink_root_child_directory_escape_and_special_file_fail_closed(
    tmp_path,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    outside = _age(
        _write(tmp_path / "outside.png"),
        now=now,
        seconds=30 * 24 * 60 * 60,
    )
    cache_root.mkdir(parents=True)
    directory_child = cache_root / "nested"
    directory_child.mkdir()
    _write(directory_child / "escaped.png")
    unsafe_paths = {outside, directory_child}
    link = cache_root / "link.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pass
    else:
        unsafe_paths.add(link)
    fifo = cache_root / "special"
    if hasattr(os, "mkfifo"):
        try:
            os.mkfifo(fifo)
        except OSError:
            pass
        else:
            unsafe_paths.add(fifo)

    plan = CacheLifecycleManager(plugin_root).plan(
        _retention(plugin_root),
        now_epoch=now,
        tier=DiskPressureTier.HARD,
    )

    assert _candidate_paths(plan).isdisjoint(unsafe_paths)
    assert plan.skipped_unsafe >= len(unsafe_paths) - 1


def test_symlink_managed_root_is_never_scanned_or_reclaimed(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    plugin_root.mkdir()
    outside_cache = tmp_path / "outside-cache"
    outside_file = _age(
        _write(
            authoritative_cache_path(
                outside_cache,
                "deleted",
                1,
                1,
                None,
            )
        ),
        now=now,
        seconds=30 * 24 * 60 * 60,
    )
    try:
        (plugin_root / ".refresh-cache").symlink_to(
            outside_cache,
            target_is_directory=True,
        )
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")

    plan = CacheLifecycleManager(plugin_root).plan(
        _retention(plugin_root),
        now_epoch=now,
        tier=DiskPressureTier.HARD,
    )

    assert plan.candidates == ()
    assert outside_file.exists()
    assert plan.skipped_unsafe >= 1


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse metadata")
def test_windows_reparse_file_is_rejected_even_when_regular(
    tmp_path,
    monkeypatch,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    stale = _age(
        _write(plugin_root / "stale.png"),
        now=now,
        seconds=30 * 24 * 60 * 60,
    )
    real_lstat = cache_lifecycle_module.os.lstat
    reparse_flag = 0x400

    class ReparseStat:
        def __init__(self, original):
            self.st_mode = original.st_mode
            self.st_dev = original.st_dev
            self.st_ino = original.st_ino
            self.st_size = original.st_size
            self.st_mtime = original.st_mtime
            self.st_mtime_ns = original.st_mtime_ns
            self.st_file_attributes = reparse_flag

    def fake_lstat(path):
        value = real_lstat(path)
        return ReparseStat(value) if Path(path) == stale else value

    monkeypatch.setattr(
        cache_lifecycle_module.stat_module,
        "FILE_ATTRIBUTE_REPARSE_POINT",
        reparse_flag,
        raising=False,
    )
    monkeypatch.setattr(cache_lifecycle_module.os, "lstat", fake_lstat)

    plan = CacheLifecycleManager(plugin_root).plan(
        _retention(plugin_root),
        now_epoch=now,
        tier=DiskPressureTier.HARD,
    )

    assert stale not in _candidate_paths(plan)
    assert plan.skipped_unsafe >= 1


def test_hard_tier_never_relaxes_age_or_retention_rules(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    cache_root = plugin_root / ".refresh-cache"
    instance = _snapshot()
    current = _age(
        _write(
            authoritative_cache_path(cache_root, "instance-one", 2, 5, None)
        ),
        now=now,
        seconds=30 * 24 * 60 * 60,
    )
    recent_old = _age(
        _write(
            authoritative_cache_path(cache_root, "instance-one", 2, 4, None)
        ),
        now=now,
        seconds=23 * 60 * 60,
    )
    retention = _retention(plugin_root, (instance,))
    manager = CacheLifecycleManager(plugin_root)

    healthy = manager.plan(
        retention, now_epoch=now, tier=DiskPressureTier.HEALTHY
    )
    hard = manager.plan(retention, now_epoch=now, tier=DiskPressureTier.HARD)

    assert current not in _candidate_paths(hard)
    assert recent_old not in _candidate_paths(hard)
    assert _candidate_paths(hard) == _candidate_paths(healthy)


def test_presentation_protects_only_exact_prepared_request_and_receipt(
    tmp_path,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    presentation_root = plugin_root / ".refresh-presentation"
    instance = _snapshot(instance_uuid=VALID_UUID)
    state = InstanceRuntimeState(
        presentation_request=_request(mode="day"),
        presentation_receipt=_receipt(mode="night"),
    )

    def artifact(
        request_id,
        mode,
        *,
        generation=2,
        revision=5,
        seconds=2 * 24 * 60 * 60,
    ):
        return _age(
            _write(
                prepared_presentation_path(
                    presentation_root,
                    VALID_UUID,
                    generation,
                    revision,
                    mode,
                    request_id,
                )
            ),
            now=now,
            seconds=seconds,
        )

    exact_pending = artifact(REQUEST_ID, "day", seconds=2 * 60 * 60)
    wrong_mode_same_request = artifact(REQUEST_ID, "night")
    exact_receipt = artifact(RECEIPT_ID, "night")
    replaced = artifact("1" * 32, "day")
    wrong_revision = artifact("2" * 32, "day", revision=4)
    orphan = _age(
        _write(
            prepared_presentation_path(
                presentation_root,
                "223e4567-e89b-12d3-a456-426614174000",
                1,
                1,
                None,
                "3" * 32,
            )
        ),
        now=now,
        seconds=2 * 24 * 60 * 60,
    )
    retention = _retention(
        plugin_root,
        (instance,),
        {VALID_UUID: state},
    )

    plan = CacheLifecycleManager(plugin_root).plan(
        retention,
        now_epoch=now,
        tier=DiskPressureTier.HEALTHY,
    )

    candidates = _candidate_paths(plan)
    assert retention.pending_presentation_paths == frozenset({exact_pending})
    assert retention.receipt_presentation_paths == frozenset({exact_receipt})
    assert exact_pending not in candidates
    assert exact_receipt not in candidates
    assert {
        wrong_mode_same_request,
        replaced,
        wrong_revision,
        orphan,
    }.issubset(candidates)


def test_unprepared_request_has_no_artifact_to_protect(tmp_path):
    plugin_root = tmp_path / "plugin-images"
    instance = _snapshot(instance_uuid=VALID_UUID)
    retention = _retention(
        plugin_root,
        (instance,),
        {
            VALID_UUID: InstanceRuntimeState(
                presentation_request=_request(prepared=False)
            )
        },
    )

    assert retention.pending_presentation_paths == frozenset()


def test_expired_exact_pending_presentation_becomes_candidate(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    presentation_root = plugin_root / ".refresh-presentation"
    instance = _snapshot(instance_uuid=VALID_UUID)
    expired = _age(
        _write(
            prepared_presentation_path(
                presentation_root,
                VALID_UUID,
                2,
                5,
                "day",
                REQUEST_ID,
            )
        ),
        now=now,
        seconds=25 * 60 * 60,
    )
    retention = _retention(
        plugin_root,
        (instance,),
        {
            VALID_UUID: InstanceRuntimeState(
                presentation_request=_request(mode="day")
            )
        },
    )

    plan = CacheLifecycleManager(plugin_root).plan(
        retention,
        now_epoch=now,
        tier=DiskPressureTier.HEALTHY,
    )

    assert expired in _candidate_paths(plan)


def test_presentation_cleanup_rechecks_fresh_marker_before_unlink(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    presentation_root = plugin_root / ".refresh-presentation"
    instance = _snapshot(instance_uuid=VALID_UUID)
    stale = _age(
        _write(
            prepared_presentation_path(
                presentation_root,
                VALID_UUID,
                2,
                4,
                "day",
                "4" * 32,
            )
        ),
        now=now,
        seconds=2 * 24 * 60 * 60,
    )
    runtime = {VALID_UUID: InstanceRuntimeState()}
    retention = _retention(plugin_root, (instance,), runtime)
    changed_runtime = {
        VALID_UUID: InstanceRuntimeState(
            presentation_request=_request(request_id="5" * 32)
        )
    }
    manager = CacheLifecycleManager(
        plugin_root,
        clock=lambda: 10,
        presentation_marker_reader=lambda: changed_runtime,
    )

    blocked = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert stale.exists()
    assert blocked.deleted_entries == 0
    assert blocked.backlog_entries >= 1

    manager = CacheLifecycleManager(
        plugin_root,
        clock=lambda: 20,
        presentation_marker_reader=lambda: runtime,
    )
    deleted = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=20,
        tier=DiskPressureTier.HEALTHY,
    )

    assert not stale.exists(), deleted
    assert deleted.deleted_entries == 1


def test_presentation_marker_reader_error_fails_closed_without_leaking_text(
    tmp_path,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    presentation_root = plugin_root / ".refresh-presentation"
    stale = _age(
        _write(
            prepared_presentation_path(
                presentation_root,
                VALID_UUID,
                2,
                4,
                "day",
                "6" * 32,
            )
        ),
        now=now,
        seconds=2 * 24 * 60 * 60,
    )

    def fail_marker_read():
        raise OSError("secret marker path")

    manager = CacheLifecycleManager(
        plugin_root,
        clock=lambda: 10,
        presentation_marker_reader=fail_marker_read,
    )
    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert stale.exists()
    assert snapshot.deleted_entries == 0
    assert snapshot.error_count == 1
    assert "secret marker path" not in repr(asdict(snapshot))


def test_presentation_marker_covers_request_time_and_display_identity(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    presentation_root = plugin_root / ".refresh-presentation"
    instance = _snapshot(instance_uuid=VALID_UUID)
    stale = _age(
        _write(
            prepared_presentation_path(
                presentation_root,
                VALID_UUID,
                2,
                4,
                "day",
                "7" * 32,
            )
        ),
        now=now,
        seconds=2 * 24 * 60 * 60,
    )
    request = _request()
    runtime = {
        VALID_UUID: InstanceRuntimeState(presentation_request=request)
    }
    changed = {
        VALID_UUID: InstanceRuntimeState(
            presentation_request=replace(
                request,
                requested_at="2026-07-12T10:00:01+00:00",
                origin_display_commit_id="display-changed",
            )
        )
    }
    retention = _retention(plugin_root, (instance,), runtime)
    manager = CacheLifecycleManager(
        plugin_root,
        clock=lambda: 10,
        presentation_marker_reader=lambda: changed,
    )

    snapshot = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert stale.exists()
    assert snapshot.deleted_entries == 0
    assert snapshot.backlog_entries >= 1


def test_presentation_marker_change_after_quarantine_restores_artifact(
    tmp_path,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    presentation_root = plugin_root / ".refresh-presentation"
    stale = _age(
        _write(
            prepared_presentation_path(
                presentation_root,
                VALID_UUID,
                2,
                4,
                "day",
                "8" * 32,
            ),
            b"presentation-artifact",
        ),
        now=now,
        seconds=2 * 24 * 60 * 60,
    )
    original_runtime = {VALID_UUID: InstanceRuntimeState()}
    changed_runtime = {
        VALID_UUID: InstanceRuntimeState(
            presentation_request=_request(request_id="8" * 32, revision=4)
        )
    }
    reads = 0

    def marker_reader():
        nonlocal reads
        reads += 1
        return original_runtime if reads == 1 else changed_runtime

    retention = _retention(plugin_root, runtime_instances=original_runtime)
    manager = CacheLifecycleManager(
        plugin_root,
        clock=lambda: 10,
        presentation_marker_reader=marker_reader,
    )

    snapshot = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    retained = [
        path
        for path in (
            stale,
            *presentation_root.glob(".gc-lifecycle-*.hold"),
        )
        if path.exists()
    ]
    assert reads >= 2
    assert retained
    assert any(path.read_bytes() == b"presentation-artifact" for path in retained)
    assert snapshot.deleted_entries == 0
    assert snapshot.backlog_entries >= 1


def test_stat_change_between_plan_and_unlink_skips_candidate(
    tmp_path,
    monkeypatch,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    stale = _age(
        _write(plugin_root / "stale.png", b"old"),
        now=now,
        seconds=8 * 24 * 60 * 60,
    )
    manager = CacheLifecycleManager(plugin_root, clock=lambda: 10)
    original_unlink = manager._unlink_candidate
    swapped = False

    def swap_before_unlink(
        candidate,
        root_identity,
        aggregate,
        **kwargs,
    ):
        nonlocal swapped
        if not swapped:
            replacement = tmp_path / "replacement.png"
            replacement.write_bytes(b"replacement")
            os.replace(replacement, candidate.path)
            swapped = True
        return original_unlink(
            candidate,
            root_identity,
            aggregate,
            **kwargs,
        )

    monkeypatch.setattr(manager, "_unlink_candidate", swap_before_unlink)

    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert swapped is True
    assert stale.read_bytes() == b"replacement"
    assert snapshot.deleted_entries == 0
    assert snapshot.deleted_bytes == 0
    assert snapshot.skipped_unsafe >= 1


def test_replacement_between_final_stat_and_quarantine_is_never_deleted(
    tmp_path,
    monkeypatch,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    stale = _age(
        _write(plugin_root / "stale.png", b"planned"),
        now=now,
        seconds=8 * 24 * 60 * 60,
    )
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(b"unverified-replacement")
    manager = CacheLifecycleManager(plugin_root, clock=lambda: 10)
    real_rename = cache_lifecycle_module.os.rename
    swapped = False

    def swap_during_quarantine(source, destination, *args, **kwargs):
        nonlocal swapped
        source_path = Path(source)
        if not swapped and source_path.name == stale.name:
            os.replace(replacement, stale)
            swapped = True
        return real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        cache_lifecycle_module.os,
        "rename",
        swap_during_quarantine,
    )

    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    retained = [
        path
        for path in (stale, *plugin_root.glob(".gc-lifecycle-*.hold"))
        if path.exists()
    ]
    assert swapped is True
    assert retained
    assert any(path.read_bytes() == b"unverified-replacement" for path in retained)
    assert snapshot.deleted_entries == 0
    assert snapshot.deleted_bytes == 0
    assert snapshot.skipped_unsafe >= 1
    assert snapshot.backlog_entries >= 1


def test_interrupted_quarantine_recovers_on_next_run_idempotently(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    plugin_root.mkdir()
    quarantine_name = cache_lifecycle_module._lifecycle_quarantine_name(
        "stale.png",
        nonce="a" * 32,
    )
    assert quarantine_name is not None
    quarantine = _age(
        _write(
            plugin_root / quarantine_name,
            b"interrupted-artifact",
        ),
        now=now,
        seconds=0,
    )
    manager = CacheLifecycleManager(plugin_root, clock=lambda: 10)
    retention = _retention(plugin_root)

    recovered = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )
    second = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    original = plugin_root / "stale.png"
    assert original.read_bytes() == b"interrupted-artifact"
    assert not quarantine.exists()
    assert recovered.candidate_entries == 1
    assert recovered.deleted_entries == 0
    assert second.candidate_entries == 0
    assert second.deleted_entries == 0


def test_quarantine_recovery_collision_never_overwrites_original(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    original = _age(
        _write(plugin_root / "stale.png", b"new-original"),
        now=now,
        seconds=0,
    )
    quarantine_name = cache_lifecycle_module._lifecycle_quarantine_name(
        original.name,
        nonce="b" * 32,
    )
    assert quarantine_name is not None
    quarantine = _age(
        _write(
            plugin_root / quarantine_name,
            b"retained-quarantine",
        ),
        now=now,
        seconds=0,
    )
    manager = CacheLifecycleManager(plugin_root, clock=lambda: 10)

    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HARD,
    )

    assert original.read_bytes() == b"new-original"
    assert quarantine.read_bytes() == b"retained-quarantine"
    assert snapshot.deleted_entries == 0
    assert snapshot.skipped_unsafe >= 1
    assert snapshot.backlog_entries >= 1


def test_candidate_vanishing_before_unlink_counts_no_file_or_bytes(
    tmp_path,
    monkeypatch,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    stale = _age(
        _write(plugin_root / "stale.png"),
        now=now,
        seconds=8 * 24 * 60 * 60,
    )
    manager = CacheLifecycleManager(plugin_root, clock=lambda: 10)
    original_unlink = manager._unlink_candidate

    def vanish_before_unlink(
        candidate,
        root_identity,
        aggregate,
        **kwargs,
    ):
        candidate.path.unlink()
        return original_unlink(
            candidate,
            root_identity,
            aggregate,
            **kwargs,
        )

    monkeypatch.setattr(manager, "_unlink_candidate", vanish_before_unlink)

    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert not stale.exists()
    assert snapshot.deleted_entries == 0
    assert snapshot.deleted_bytes == 0
    assert snapshot.error_count == 0


def test_unlink_failure_increments_only_error_aggregate(
    tmp_path,
    monkeypatch,
):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    stale = _age(
        _write(plugin_root / "stale.png"),
        now=now,
        seconds=8 * 24 * 60 * 60,
    )
    manager = CacheLifecycleManager(plugin_root, clock=lambda: 10)

    def fail_unlink(*_args, **_kwargs):
        raise PermissionError("private path and error text")

    monkeypatch.setattr(cache_lifecycle_module.os, "unlink", fail_unlink)

    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert stale.exists()
    assert snapshot.deleted_entries == 0
    assert snapshot.deleted_bytes == 0
    assert snapshot.error_count == 1
    assert "private path" not in repr(asdict(snapshot))


def test_posix_unlink_success_is_not_reversed_by_descriptor_close_error(
    monkeypatch,
):
    class FakeStat:
        def __init__(self, mode, inode, size, mtime_ns):
            self.st_mode = mode
            self.st_dev = 1
            self.st_ino = inode
            self.st_size = size
            self.st_mtime_ns = mtime_ns
            self.st_mtime = mtime_ns / 1_000_000_000
            self.st_file_attributes = 0

    root_stat = FakeStat(stat.S_IFDIR | 0o700, 10, 0, 1)
    file_stat = FakeStat(stat.S_IFREG | 0o600, 20, 4, 2)
    candidate = cache_lifecycle_module.CleanupCandidate(
        artifact_class=ArtifactClass.UNOWNED_ALIAS,
        path=Path("/managed/stale.png"),
        stat_token=cache_lifecycle_module._file_stat_token(file_stat),
        size=4,
        age_seconds=999,
    )
    unlinked = []
    monkeypatch.setattr(cache_lifecycle_module.os, "open", lambda *_args: 99)
    monkeypatch.setattr(
        cache_lifecycle_module.os,
        "fstat",
        lambda _fd: root_stat,
    )
    monkeypatch.setattr(
        cache_lifecycle_module.os,
        "stat",
        lambda *_args, **_kwargs: file_stat,
    )
    monkeypatch.setattr(
        cache_lifecycle_module.os,
        "unlink",
        lambda *_args, **_kwargs: unlinked.append(True),
    )
    monkeypatch.setattr(
        cache_lifecycle_module.os,
        "rename",
        lambda *_args, **_kwargs: None,
    )

    def close_after_commit(_fd):
        raise OSError("descriptor close after committed unlink")

    monkeypatch.setattr(cache_lifecycle_module.os, "close", close_after_commit)

    assert (
        CacheLifecycleManager._unlink_candidate_posix(
            candidate,
            (root_stat.st_dev, root_stat.st_ino),
        )
        is True
    )
    assert unlinked == [True]


def test_scanner_close_error_degrades_to_aggregate_without_escaping(
    tmp_path,
    monkeypatch,
):
    plugin_root = tmp_path / "plugin-images"
    plugin_root.mkdir()

    class FailingCloseScanner:
        def __iter__(self):
            return iter(())

        def close(self):
            raise OSError("scanner close detail")

    monkeypatch.setattr(
        cache_lifecycle_module.os,
        "scandir",
        lambda _root: FailingCloseScanner(),
    )
    manager = CacheLifecycleManager(plugin_root, clock=lambda: 10)

    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=1_800_000_000,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert snapshot.error_count == 1
    assert "scanner close detail" not in repr(asdict(snapshot))


def test_scan_budget_stops_planning_with_backlog(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    for index in range(5):
        _age(
            _write(plugin_root / f"stale-{index}.png"),
            now=now,
            seconds=(8 + index) * 24 * 60 * 60,
        )
    budget = CleanupBudget(2, 10, 10 * MIB, 10)
    manager = CacheLifecycleManager(
        plugin_root,
        budgets={DiskPressureTier.HEALTHY: budget},
        clock=lambda: 10,
    )

    plan = manager.plan(
        _retention(plugin_root),
        now_epoch=now,
        tier=DiskPressureTier.HEALTHY,
    )

    assert plan.scanned_entries == 2
    assert len(plan.candidates) == 2
    assert plan.backlog_entries >= 1


def test_delete_count_budget_stops_immediately_with_backlog(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    paths = {
        _age(
            _write(plugin_root / f"stale-{index}.png"),
            now=now,
            seconds=(8 + index) * 24 * 60 * 60,
        )
        for index in range(3)
    }
    budget = CleanupBudget(20, 1, 10 * MIB, 10)
    manager = CacheLifecycleManager(
        plugin_root,
        budgets={DiskPressureTier.HEALTHY: budget},
        clock=lambda: 10,
    )

    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert snapshot.deleted_entries == 1
    assert sum(path.exists() for path in paths) == 2
    assert snapshot.backlog_entries >= 1


def test_delete_byte_budget_counts_only_successful_unlinks(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    oldest = _age(
        _write(plugin_root / "oldest.png", b"1234"),
        now=now,
        seconds=10 * 24 * 60 * 60,
    )
    newer = _age(
        _write(plugin_root / "newer.png", b"12345"),
        now=now,
        seconds=9 * 24 * 60 * 60,
    )
    budget = CleanupBudget(20, 10, 8, 10)
    manager = CacheLifecycleManager(
        plugin_root,
        budgets={DiskPressureTier.HEALTHY: budget},
        clock=lambda: 10,
    )

    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert not oldest.exists()
    assert newer.exists()
    assert snapshot.deleted_entries == 1
    assert snapshot.deleted_bytes == 4
    assert snapshot.backlog_entries >= 1


def test_elapsed_time_budget_stops_scan_with_backlog(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    for index in range(3):
        _age(
            _write(plugin_root / f"stale-{index}.png"),
            now=now,
            seconds=8 * 24 * 60 * 60,
        )
    readings = iter((10.0, 10.0, 12.0, 12.0))
    manager = CacheLifecycleManager(
        plugin_root,
        budgets={
            DiskPressureTier.HEALTHY: CleanupBudget(20, 20, 10 * MIB, 1)
        },
        clock=lambda: next(readings, 12.0),
    )

    plan = manager.plan(
        _retention(plugin_root),
        now_epoch=now,
        tier=DiskPressureTier.HEALTHY,
    )

    assert plan.scanned_entries == 1
    assert len(plan.candidates) == 1
    assert plan.backlog_entries >= 1


def test_dry_run_uses_same_plan_without_unlinking_and_real_is_subset(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    paths = {
        _age(
            _write(plugin_root / f"stale-{index}.png"),
            now=now,
            seconds=(8 + index) * 24 * 60 * 60,
        )
        for index in range(2)
    }
    retention = _retention(plugin_root)
    budget = CleanupBudget(20, 1, 10 * MIB, 10)
    manager = CacheLifecycleManager(
        plugin_root,
        budgets={DiskPressureTier.HEALTHY: budget},
        clock=lambda: 10,
    )
    planned = _candidate_paths(
        manager.plan(
            retention,
            now_epoch=now,
            tier=DiskPressureTier.HEALTHY,
        )
    )

    dry = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
        dry_run=True,
    )
    after_dry = {path for path in paths if not path.exists()}
    real = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )
    actually_deleted = {path for path in paths if not path.exists()}

    assert planned == paths
    assert dry.candidate_entries == len(planned)
    assert dry.deleted_entries == 0
    assert after_dry == set()
    assert actually_deleted <= planned
    assert len(actually_deleted) == real.deleted_entries == 1


def test_second_run_is_idempotent_after_partial_first_run(tmp_path):
    now = 1_800_000_000
    plugin_root = tmp_path / "plugin-images"
    for index in range(2):
        _age(
            _write(plugin_root / f"stale-{index}.png"),
            now=now,
            seconds=(8 + index) * 24 * 60 * 60,
        )
    manager = CacheLifecycleManager(
        plugin_root,
        budgets={
            DiskPressureTier.HEALTHY: CleanupBudget(20, 1, 10 * MIB, 10)
        },
        clock=lambda: 10,
    )
    retention = _retention(plugin_root)

    first = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )
    second = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )
    third = manager.maintain(
        retention,
        now_epoch=now,
        now_monotonic=10,
        tier=DiskPressureTier.HEALTHY,
    )

    assert first.deleted_entries == 1
    assert second.deleted_entries == 1
    assert third.deleted_entries == 0
    assert third.candidate_entries == 0


def test_health_snapshot_is_disabled_and_contains_aggregate_scalars_only(
    tmp_path,
):
    plugin_root = tmp_path / "private-root"
    manager = CacheLifecycleManager(plugin_root, enabled=False)

    snapshot = manager.maintain(
        _retention(plugin_root),
        now_epoch=1_800_000_000,
        now_monotonic=10,
        tier=DiskPressureTier.HARD,
    )
    payload = asdict(snapshot)

    assert payload == {
        "enabled": False,
        "disk_tier": DiskPressureTier.HARD,
        "ran_at": "2027-01-15T08:00:00+00:00",
        "dry_run": False,
        "scanned_entries": 0,
        "candidate_entries": 0,
        "deleted_entries": 0,
        "deleted_bytes": 0,
        "retained_current": 0,
        "retained_last_good": 0,
        "retained_recent": 0,
        "skipped_unsafe": 0,
        "error_count": 0,
        "backlog_entries": 0,
    }
    assert all(
        isinstance(value, (bool, int, str, DiskPressureTier))
        or value is None
        for value in payload.values()
    )
    assert "private-root" not in repr(payload)
    assert manager.due(10, DiskPressureTier.HARD) is False


def test_lifecycle_aggregate_is_scalar_only_and_has_no_sensitive_fields():
    payload = asdict(LifecycleAggregate())

    assert set(payload) == {
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
    assert all(isinstance(value, int) for value in payload.values())
