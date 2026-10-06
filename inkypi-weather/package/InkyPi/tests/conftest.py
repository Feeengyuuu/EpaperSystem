import functools
import re
import sys
import uuid
import weakref
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.runtime_state import RuntimeStateStore  # noqa: E402
from tests.tmp_cleanup import (  # noqa: E402
    PROJECT_TMP,
    child_names,
    is_scratch_path,
    module_state_roots,
    remove_tree,
    settle_runtime_stores,
    sweep_stale,
)


TEST_TMP_ROOT = PROJECT_TMP / "pytest-fixtures"
TMP_PATH_SLUG_MAX_CHARS = 32
WINDOWS_SAFE_PATH_CHARS = 240
NESTED_VENV_SUFFIX = Path(
    "opt/releases/current-release/venv_inkypi/Scripts/python.exe"
)
_PHASE_REPORTS = pytest.StashKey[dict]()
_SCRATCH_PATHS = pytest.StashKey[list]()
_KNOWN_ROOTS = pytest.StashKey[tuple]()

# Runtime stores debounce writes on daemon timers. Track them so a store a test leaves
# dirty is flushed before its directory is deleted, instead of failing seconds later.
_LIVE_RUNTIME_STORES = weakref.WeakSet()
_runtime_store_init = RuntimeStateStore.__init__


@functools.wraps(_runtime_store_init)
def _tracked_runtime_store_init(self, *args, **kwargs):
    _runtime_store_init(self, *args, **kwargs)
    _LIVE_RUNTIME_STORES.add(self)


RuntimeStateStore.__init__ = _tracked_runtime_store_init


def _tmp_root_with_windows_headroom(root: Path) -> Path:
    """Keep enough room for release/venv fixtures under a nested basetemp."""

    longest_leaf = "x" * (TMP_PATH_SLUG_MAX_CHARS + 1 + 32)
    nested = root / longest_leaf / NESTED_VENV_SUFFIX
    if len(str(nested)) <= WINDOWS_SAFE_PATH_CHARS:
        return root
    return TEST_TMP_ROOT


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    item.stash.setdefault(_PHASE_REPORTS, {})[report.when] = report
    return report


def _keep_for_debugging(node) -> bool:
    return any(report.failed for report in node.stash.get(_PHASE_REPORTS, {}).values())


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    result = yield
    # Runs after every fixture finalizer, including monkeypatch undo, so the real os/shutil apply.
    paths = item.stash.get(_SCRATCH_PATHS, [])
    if paths and not _keep_for_debugging(item):
        settle_runtime_stores(_LIVE_RUNTIME_STORES, paths)
        for path in paths:
            remove_tree(path)
    return result


def pytest_collection_finish(session):
    # Helper modules imported by other tests (e.g. tests.test_refresh_task) own roots too.
    roots = {TEST_TMP_ROOT}
    for name, module in list(sys.modules.items()):
        if name.startswith("tests."):
            roots.update(module_state_roots(module))
    session.config.stash[_KNOWN_ROOTS] = tuple(sorted(roots))


@pytest.fixture(autouse=True)
def _collect_new_scratch_entries(request):
    """Queue whatever a test adds under the known scratch roots for teardown cleanup."""

    roots = request.config.stash.get(_KNOWN_ROOTS, (TEST_TMP_ROOT,))
    before = {root: child_names(root) for root in roots}
    yield
    created = request.node.stash.setdefault(_SCRATCH_PATHS, [])
    for root, existing in before.items():
        created.extend(root / name for name in sorted(child_names(root) - existing))


@pytest.fixture
def tmp_path(request, tmp_path_factory):
    configured_root = request.config.getoption("basetemp")
    root = tmp_path_factory.getbasetemp() if configured_root else TEST_TMP_ROOT
    root = _tmp_root_with_windows_headroom(root)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)
    slug = slug[-TMP_PATH_SLUG_MAX_CHARS:]
    path = root / f"{slug}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    if is_scratch_path(path):
        request.node.stash.setdefault(_SCRATCH_PATHS, []).append(path)
    return path


def pytest_sessionfinish(session):
    sweep_stale(session.config.stash.get(_KNOWN_ROOTS, (TEST_TMP_ROOT,)))
