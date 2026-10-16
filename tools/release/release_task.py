"""Task-folder rules shared by every release step.

A deployment round keeps all of its evidence in one folder directly under
``outputs/``, which is git-ignored because the repository is public. Steps that
run before the deployment refuse a folder that already holds ``deployment.json``
so a new round never overwrites the evidence of a finished one.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "inkypi-weather/package/InkyPi"
DEPLOYMENT_RECORD = "deployment.json"
TASK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
EVIDENCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
COMMIT = re.compile(r"[0-9a-f]{40}")


class ReleaseError(RuntimeError):
    """A release step refused to continue; nothing after the refusal ran."""


def require(condition: object, message: str) -> None:
    """Refuse with ``message`` unless ``condition`` holds (unlike assert, never optimized away)."""
    if not condition:
        raise ReleaseError(message)


def require_commit(value: object, what: str) -> str:
    require(isinstance(value, str) and COMMIT.fullmatch(value), f"{what} is not a full commit id")
    return value


def is_reparse_point(path: Path) -> bool:
    """Return whether ``path`` is a symlink, junction or other reparse point."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & reparse)


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def is_within(path: Path, root: Path) -> bool:
    """Return whether the absolute ``path`` lies inside ``root`` (or is ``root``)."""
    path_text = os.path.normcase(os.path.abspath(path))
    root_text = os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([path_text, root_text]) == root_text
    except ValueError:  # different drives
        return False


def resolve_task_dir(value: str | os.PathLike[str], repo: Path = REPO_ROOT) -> Path:
    """Resolve a task folder, which must be a direct child of ``<repo>/outputs``.

    Relative values are taken from the repository root, not the current
    directory, so the same command line always names the same folder.
    """
    outputs = Path(os.path.abspath(Path(repo) / "outputs"))
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path(repo) / candidate
    candidate = Path(os.path.abspath(candidate))
    require(same_path(candidate.parent, outputs), f"Task folder must be a direct child of {outputs}: {candidate}")
    require(TASK_NAME.fullmatch(candidate.name), f"Unsafe task folder name: {candidate.name!r}")
    require(not is_reparse_point(outputs), "Refusing an outputs folder that is a reparse point")
    require(not is_reparse_point(candidate), "Refusing a task folder that is a reparse point")
    return candidate


@dataclass(frozen=True)
class TaskContext:
    """One round's evidence folder plus the repository it belongs to."""

    repo: Path
    task_dir: Path

    @classmethod
    def open(cls, value: str | os.PathLike[str], repo: Path = REPO_ROOT, create: bool = False) -> TaskContext:
        task_dir = resolve_task_dir(value, repo)
        if create:
            task_dir.mkdir(exist_ok=True)
        require(task_dir.is_dir(), f"Task folder does not exist: {task_dir}")
        return cls(Path(os.path.abspath(repo)), task_dir)

    @property
    def temp_dir(self) -> Path:
        return self.repo / ".tmp" / "release" / self.task_dir.name

    def path(self, name: str) -> Path:
        require(EVIDENCE_NAME.fullmatch(name), f"Unsafe evidence file name: {name!r}")
        return self.task_dir / name

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    def load(self, name: str) -> dict:
        path = self.path(name)
        require(path.is_file(), f"Missing task evidence: {name}")
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def save(self, name: str, document: object) -> Path:
        path = self.path(name)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def require_not_deployed(self) -> None:
        require(
            not self.has(DEPLOYMENT_RECORD),
            f"{self.task_dir.name} already has {DEPLOYMENT_RECORD}; inspect or resume that deployment "
            "and start the next round in a new task folder",
        )

    def require_deployed(self) -> dict:
        deployment = self.load(DEPLOYMENT_RECORD)
        require(deployment.get("status") == "passed", "Deployment must be terminal and passed first")
        return deployment

    def isolate_temp(self) -> Path:
        """Keep this process's (and its children's) temporary files inside the project."""
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        for name in ("TEMP", "TMP", "TMPDIR"):
            os.environ[name] = str(self.temp_dir)
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        sys.dont_write_bytecode = True
        tempfile.tempdir = None
        return self.temp_dir
