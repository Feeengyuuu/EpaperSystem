"""Opt-in bridge from prepared media banks to the bounded image stage.

The bridge is intentionally inert unless the refresh owner bound all runtime
authority needed to reject stale results.  Plugin bank state, selection, and
receipts stay in the parent; workers only read immutable PNG paths and write
new PNG artifacts inside a short-lived plugin-owned staging directory.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Iterable

from PIL import Image

from runtime.bounded_parallel_stage import (
    DEFAULT_MAX_HEIGHT,
    DEFAULT_MAX_PIXELS,
    DEFAULT_MAX_WIDTH,
    ImmutableImageWorkset,
    ParallelStageProcessLeak,
)
from runtime.execution_policy import ExecutionClass, plugin_execution_class
from runtime.long_task_executor import (
    InstanceIdentity,
    current_instance_identity,
    current_instance_identity_validator,
    current_parallel_image_runner,
    current_task_context,
)
from runtime.refresh_contracts import TaskContext


_STAGING_PREFIX = ".parallel-image-stage-"


def prepare_local_bank_images(
    *,
    plugin_id: str,
    media_root: str | os.PathLike[str],
    source_paths: Iterable[str | os.PathLike[str]],
    target_sizes: Iterable[tuple[int, int] | None],
    expected_instance_uuid: str,
) -> tuple[Image.Image, ...] | None:
    """Return detached staged images, or ``None`` when no owner bound a stage.

    ``source_paths`` may repeat when a composition needs both a resized cell
    and an original-resolution backdrop.  The only admitted source root is the
    exact prepared-bank media directory supplied by the plugin.
    """

    if plugin_execution_class(plugin_id) is not ExecutionClass.PARALLEL_IMAGE:
        return None

    runner = current_parallel_image_runner()
    context = current_task_context()
    identity = current_instance_identity()
    identity_validator = current_instance_identity_validator()
    if (
        runner is None
        or not callable(getattr(runner, "run_parallel_only", None))
        or not callable(getattr(runner, "acquire_parallel_lease", None))
        or not isinstance(context, TaskContext)
        or not isinstance(identity, InstanceIdentity)
        or not callable(identity_validator)
        or not isinstance(expected_instance_uuid, str)
        or not expected_instance_uuid
        or identity.instance_uuid != expected_instance_uuid
    ):
        return None

    admission = runner.acquire_parallel_lease(context)
    if admission is None:
        return None

    staging_dir: Path | None = None
    staging_parent: Path | None = None
    detached: list[Image.Image] = []
    safe_to_cleanup = True
    try:
        paths = tuple(_absolute_path(value) for value in source_paths)
        sizes = tuple(target_sizes)
        if not paths or len(paths) != len(sizes):
            raise ValueError("prepared bank image requests must be non-empty and aligned")

        root = _ordinary_directory(media_root, label="prepared bank media root")
        staging_parent = _ordinary_directory(
            root.parent,
            label="prepared bank runtime root",
        )
        descriptors = []
        for ordinal, (path, target_size) in enumerate(zip(paths, sizes)):
            _ordinary_bank_file(path, root)
            if not _parallel_safe_image_header(path):
                # A legitimate bank image may exceed the tightly bounded
                # parallel decoder budget.  The original bank loader remains
                # responsible for its larger validation envelope.
                return None
            descriptor = {
                "ordinal": ordinal,
                "source_path": str(path),
                "source_sha256": _sha256_file(path),
            }
            if target_size is not None:
                if (
                    not isinstance(target_size, tuple)
                    or len(target_size) != 2
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or value <= 0
                        for value in target_size
                    )
                ):
                    raise ValueError("prepared bank target sizes must be positive pairs")
                descriptor["target_width"], descriptor["target_height"] = target_size
            descriptors.append(descriptor)

        staging_dir = Path(
            tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=staging_parent)
        )
        os.chmod(staging_dir, 0o700)
        workset = ImmutableImageWorkset(
            descriptors=tuple(descriptors),
            staging_dir=str(staging_dir),
            instance_identity=identity,
            source_roots=(str(root),),
        )
        artifacts = runner.run_parallel_only(
            workset,
            context,
            identity_validator,
            lease=admission,
        )
        if artifacts is None:
            return None
        if len(artifacts) != len(descriptors):
            raise RuntimeError("parallel image stage returned an incomplete bank batch")
        for artifact in artifacts:
            with Image.open(artifact.path) as opened:
                if opened.format != "PNG":
                    raise RuntimeError("parallel image stage returned a non-PNG artifact")
                opened.load()
                detached.append(opened.copy())
        return tuple(detached)
    except BaseException as error:
        if isinstance(error, ParallelStageProcessLeak):
            safe_to_cleanup = False
        for image in detached:
            image.close()
        raise
    finally:
        admission.release()
        if safe_to_cleanup and staging_dir is not None and staging_parent is not None:
            _remove_staging_directory(staging_dir, staging_parent)


def _absolute_path(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _ordinary_directory(value, *, label: str) -> Path:
    path = _absolute_path(value)
    try:
        info = path.lstat()
    except OSError as error:
        raise RuntimeError(f"{label} is unavailable") from error
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError(f"{label} is not an ordinary directory")
    if path.resolve(strict=True) != path:
        raise RuntimeError(f"{label} contains a symbolic-link path component")
    return path


def _ordinary_bank_file(path: Path, root: Path) -> None:
    if path.parent != root:
        raise RuntimeError("prepared bank image is outside the exact media root")
    try:
        info = path.lstat()
    except OSError as error:
        raise RuntimeError("prepared bank image is unavailable") from error
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise RuntimeError("prepared bank image is not an ordinary file")
    if path.resolve(strict=True).parent != root:
        raise RuntimeError("prepared bank image contains a symbolic-link path component")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _parallel_safe_image_header(path: Path) -> bool:
    """Probe only enough Pillow metadata to enforce the child decode budget."""

    try:
        with Image.open(path) as opened:
            width, height = opened.size
            return (
                opened.format == "PNG"
                and isinstance(width, int)
                and isinstance(height, int)
                and 0 < width <= DEFAULT_MAX_WIDTH
                and 0 < height <= DEFAULT_MAX_HEIGHT
                and width * height <= DEFAULT_MAX_PIXELS
            )
    except (OSError, SyntaxError, ValueError):
        return False


def _remove_staging_directory(path: Path, expected_parent: Path) -> None:
    if path.parent != expected_parent or not path.name.startswith(_STAGING_PREFIX):
        raise RuntimeError("parallel image staging path changed unexpectedly")
    try:
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    except OSError as error:
        raise RuntimeError("parallel image staging could not be cleaned") from error
