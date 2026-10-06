import os
import shutil
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.runtime_state import RuntimeStateStore
from tests.tmp_cleanup import (
    PROJECT_TMP,
    is_scratch_path,
    module_state_roots,
    remove_tree,
    settle_runtime_stores,
    sweep_stale,
)


TEST_STATE_ROOT = PROJECT_TMP / "tmp_cleanup_tests"
_CREATED = {}


def test_remove_tree_refuses_paths_outside_project_scratch():
    tests_dir = Path(__file__).resolve().parent

    with pytest.raises(ValueError):
        remove_tree(tests_dir)
    with pytest.raises(ValueError):
        remove_tree(PROJECT_TMP)
    with pytest.raises(ValueError):
        remove_tree(PROJECT_TMP / "child" / "..")
    assert tests_dir.is_dir()


def test_remove_tree_deletes_nested_read_only_files(tmp_path):
    victim = tmp_path / "victim"
    nested = victim / "a" / "b"
    nested.mkdir(parents=True)
    locked = nested / "locked.txt"
    locked.write_text("x", encoding="utf-8")
    os.chmod(locked, stat.S_IREAD)

    assert remove_tree(victim) is True
    assert not victim.exists()
    assert remove_tree(victim) is True


def test_remove_tree_deletes_a_plain_file_entry(tmp_path):
    entry = tmp_path / "state.json"
    entry.write_text("{}", encoding="utf-8")
    os.chmod(entry, stat.S_IREAD)

    assert remove_tree(entry) is True
    assert not entry.exists()


def test_module_state_roots_only_accepts_paths_inside_project_scratch():
    module = SimpleNamespace(
        TEST_STATE_ROOT=PROJECT_TMP / "example_tests",
        TEST_TMP_ROOT=Path(__file__).resolve().parent,
        TEST_CACHE_ROOT=str(PROJECT_TMP / "as_text_tests"),
    )

    assert module_state_roots(module) == (PROJECT_TMP / "example_tests",)
    assert module_state_roots(None) == ()


def test_sweep_stale_removes_only_entries_older_than_the_window(tmp_path):
    root = tmp_path / "root"
    old = root / "old-run"
    fresh = root / "fresh-run"
    old.mkdir(parents=True)
    fresh.mkdir()
    (old / "leftover.png").write_bytes(b"png")
    now = time.time()
    os.utime(old, (now - 3 * 24 * 3600, now - 3 * 24 * 3600))

    assert sweep_stale([root, Path(__file__).resolve().parent], now=now) == 1
    assert not old.exists()
    assert fresh.is_dir()


def test_settle_runtime_stores_flushes_only_stores_inside_doomed_paths(tmp_path):
    class FakeStore:
        def __init__(self, path, error=None):
            self.path = path
            self.error = error
            self.flushed = False

        def flush(self):
            self.flushed = True
            if self.error:
                raise self.error

    inside = FakeStore(tmp_path / "state" / "runtime.json")
    broken = FakeStore(tmp_path / "gone" / "runtime.json", OSError("directory removed"))
    outside = FakeStore(PROJECT_TMP / "elsewhere" / "runtime.json")

    settle_runtime_stores([inside, broken, outside, object()], [tmp_path])

    assert inside.flushed and broken.flushed
    assert not outside.flushed


def test_cleanup_runs_after_monkeypatch_restores_rmtree(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "rmtree", lambda *_args, **_kwargs: pytest.fail("cleanup used a patched rmtree"))
    (tmp_path / "nested").mkdir()
    _CREATED["patched_tmp_path"] = tmp_path


def test_dirty_runtime_store_is_flushed_before_its_directory_is_deleted(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime.json")
    store.record_attempt("one", "2026-10-06T10:00:00+00:00")
    store.record_attempt("one", "2026-10-06T10:00:01+00:00")
    assert store._pending_timer is not None
    _CREATED["dirty_store"] = store


def test_tmp_path_and_module_scratch_are_created_inside_project_scratch(tmp_path):
    module_dir = TEST_STATE_ROOT / "created-by-passing-test"
    module_dir.mkdir(parents=True)
    (module_dir / "state.json").write_text("{}", encoding="utf-8")
    _CREATED.update(tmp_path=tmp_path, module_dir=module_dir)

    assert is_scratch_path(tmp_path)
    assert is_scratch_path(module_dir)


def test_passing_test_scratch_was_removed_at_teardown():
    if not {"tmp_path", "module_dir", "patched_tmp_path", "dirty_store"} <= set(_CREATED):
        pytest.skip("checks the earlier tests in file order")

    assert not _CREATED["tmp_path"].exists()
    assert not _CREATED["module_dir"].exists()
    assert TEST_STATE_ROOT.is_dir()
    assert not _CREATED["patched_tmp_path"].exists()
    assert _CREATED["dirty_store"]._pending_timer is None
    assert not _CREATED["dirty_store"].path.parent.exists()
