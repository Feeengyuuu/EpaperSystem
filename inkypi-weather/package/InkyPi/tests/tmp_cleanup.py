"""Remove the scratch directories tests create under the project's .tmp folder."""

from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from pathlib import Path


PROJECT_TMP = Path(__file__).resolve().parents[4] / ".tmp"
# Test modules that keep scratch state outside tmp_path name their root with one of these.
STATE_ROOT_NAMES = ("TEST_STATE_ROOT", "TEST_TMP_ROOT", "TEST_CACHE_ROOT")
# Directories of failed tests are kept this long for debugging, then swept.
STALE_LEFTOVER_SECONDS = 24 * 60 * 60


def _location(path) -> Path | None:
    """Resolve the parent only, so a link is judged by where it sits, not its target."""

    path = Path(path)
    if path.name in {"", ".", ".."}:
        return None
    try:
        return path.parent.resolve() / path.name
    except OSError:
        return None


def is_scratch_path(path) -> bool:
    location = _location(path)
    return location is not None and location != PROJECT_TMP and location.is_relative_to(PROJECT_TMP)


def child_names(root) -> frozenset[str]:
    try:
        return frozenset(os.listdir(root))
    except (FileNotFoundError, NotADirectoryError):
        return frozenset()


def module_state_roots(module) -> tuple[Path, ...]:
    roots = []
    for name in STATE_ROOT_NAMES:
        value = getattr(module, name, None)
        if isinstance(value, Path) and is_scratch_path(value) and value not in roots:
            roots.append(value)
    return tuple(roots)


def _make_writable_and_retry(function, failed_path, _error):
    os.chmod(failed_path, stat.S_IWRITE)
    function(failed_path)


def remove_tree(path) -> bool:
    """Delete one scratch entry; a locked file leaves it for the stale sweep."""

    if not is_scratch_path(path):
        raise ValueError(f"refusing to delete outside {PROJECT_TMP}: {path}")
    target = str(_location(path))
    if os.name == "nt":
        # Release and venv fixtures nest deeply; the extended prefix lifts MAX_PATH.
        target = "\\\\?\\" + target
    if not os.path.lexists(target):
        return True
    try:
        if os.path.islink(target):
            try:
                os.unlink(target)
            except OSError:
                os.rmdir(target)  # Windows directory symlink
        elif not os.path.isdir(target):
            try:
                os.unlink(target)
            except PermissionError:
                os.chmod(target, stat.S_IWRITE)
                os.unlink(target)
        elif sys.version_info >= (3, 12):
            shutil.rmtree(target, onexc=_make_writable_and_retry)
        else:
            shutil.rmtree(target, onerror=_make_writable_and_retry)
    except OSError:
        return False
    return True


def settle_runtime_stores(stores, doomed_paths) -> None:
    """Flush stores inside paths about to be deleted, so no debounce timer writes there later."""

    locations = [location for location in map(_location, doomed_paths) if location is not None]
    for store in list(stores):
        where = _location(getattr(store, "path", ""))
        if where is None or not any(where.is_relative_to(location) for location in locations):
            continue
        try:
            store.flush()
        except Exception:
            pass  # A store whose directory the test already removed keeps its old behavior.


def sweep_stale(roots, *, max_age_seconds=STALE_LEFTOVER_SECONDS, now=None) -> int:
    """Remove leftovers older than the debugging window; fresh entries may be in use."""

    cutoff = (time.time() if now is None else now) - max_age_seconds
    removed = 0
    for root in roots:
        if not is_scratch_path(root):
            continue
        for name in child_names(root):
            child = Path(root) / name
            try:
                modified = child.lstat().st_mtime
            except OSError:
                continue
            if modified < cutoff and remove_tree(child):
                removed += 1
    return removed
