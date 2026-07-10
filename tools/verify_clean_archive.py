#!/usr/bin/env python3
"""Run the InkyPi test suite from a tracked-only ``git archive`` snapshot."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath


PROJECT_RELATIVE = Path("inkypi-weather/package/InkyPi")
PYTHON_VERSION_CODE = (
    "import json, sys; "
    "print(json.dumps([sys.version_info.major, sys.version_info.minor, "
    "sys.version_info.micro]))"
)


class CleanArchiveError(RuntimeError):
    """Raised when the tracked-only release gate cannot complete safely."""


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=False, text=True, **kwargs)
    except OSError as exc:
        raise CleanArchiveError(f"could not run {command[0]!r}: {exc}") from exc


def query_python_version(python: Path) -> tuple[int, int, int]:
    result = _run(
        [str(python), "-c", PYTHON_VERSION_CODE],
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CleanArchiveError(f"could not query Python interpreter {python}: {detail}")
    try:
        major, minor, micro = json.loads(result.stdout)
        return int(major), int(minor), int(micro)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CleanArchiveError(
            f"unexpected version response from Python interpreter {python}: {result.stdout!r}"
        ) from exc


def require_python_311(python: Path) -> None:
    version = query_python_version(python)
    if version[:2] != (3, 11):
        rendered = ".".join(str(part) for part in version)
        raise CleanArchiveError(
            f"clean archive verification requires Python 3.11; {python} is Python {rendered}"
        )


def resolve_python(python: Path) -> Path:
    if python.exists():
        return python.resolve()
    resolved = shutil.which(str(python))
    return Path(resolved).resolve() if resolved else python


def create_head_archive(repo_root: Path, archive_path: Path) -> None:
    result = _run(
        [
            "git",
            "-C",
            str(repo_root),
            "archive",
            "--format=tar",
            "--output",
            str(archive_path),
            "HEAD",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise CleanArchiveError(f"git archive HEAD failed: {detail}")


def _safe_member_name(member: tarfile.TarInfo) -> bool:
    posix_path = PurePosixPath(member.name.replace("\\", "/"))
    windows_path = PureWindowsPath(member.name)
    return (
        bool(posix_path.parts)
        and not posix_path.is_absolute()
        and not windows_path.is_absolute()
        and not windows_path.drive
        and ".." not in posix_path.parts
        and ".." not in windows_path.parts
        and not any(":" in part for part in posix_path.parts)
    )


def extract_archive(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
            for member in members:
                if not _safe_member_name(member) or not (member.isdir() or member.isfile()):
                    raise CleanArchiveError(f"unsafe archive member: {member.name!r}")
            archive.extractall(destination, members=members)
    except (tarfile.TarError, OSError) as exc:
        if isinstance(exc, CleanArchiveError):
            raise
        raise CleanArchiveError(f"could not extract clean archive: {exc}") from exc


def collect_ignored_paths(repo_root: Path, *, limit: int = 50) -> list[str]:
    result = _run(
        [
            "git",
            "-C",
            str(repo_root),
            "status",
            "--short",
            "--ignored",
            "--untracked-files=normal",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    paths = [line[3:] for line in result.stdout.splitlines() if line.startswith("!! ")]
    if len(paths) > limit:
        return [*paths[:limit], f"... and {len(paths) - limit} more ignored paths"]
    return paths


def run_archive_tests(
    archive_root: Path,
    python: Path,
    pytest_args: list[str],
) -> subprocess.CompletedProcess[str]:
    project_root = archive_root / PROJECT_RELATIVE
    process_temp = archive_root / ".pytest-temp"
    process_temp.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(
                (str(project_root / "src"), str(project_root))
            ),
            "TEMP": str(process_temp),
            "TMP": str(process_temp),
            "TMPDIR": str(process_temp),
        }
    )
    return _run(
        [
            str(python),
            "-m",
            "pytest",
            "tests",
            "--no-header",
            "-p",
            "no:cacheprovider",
            *pytest_args,
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
    )


def verify_clean_archive(
    repo_root: Path,
    python: Path,
    pytest_args: list[str],
    *,
    temp_parent: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    repo_root = repo_root.resolve()
    python = resolve_python(python)
    require_python_311(python)
    parent = str(temp_parent.resolve()) if temp_parent is not None else None
    with tempfile.TemporaryDirectory(prefix="inkypi-clean-archive-", dir=parent) as owned_temp:
        owned_root = Path(owned_temp)
        archive_path = owned_root / "source.tar"
        extracted_root = owned_root / "source"
        create_head_archive(repo_root, archive_path)
        extract_archive(archive_path, extracted_root)
        project_root = extracted_root / PROJECT_RELATIVE
        if not project_root.is_dir():
            raise CleanArchiveError(
                f"archive does not contain expected project directory: {PROJECT_RELATIVE}"
            )

        result = run_archive_tests(extracted_root, python, pytest_args)
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(
                result.stderr,
                file=sys.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
            )
        if result.returncode != 0:
            ignored = collect_ignored_paths(repo_root)
            ignored_note = (
                "\nIgnored workspace paths absent from the archive:\n  - "
                + "\n  - ".join(ignored)
                if ignored
                else "\nNo ignored workspace paths were reported by git."
            )
            raise CleanArchiveError(
                "clean archive tests failed; the workspace may rely on ignored or untracked "
                f"dependencies.{ignored_note}"
            )
        return result


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        default=os.environ.get("INKYPI_PYTHON311", sys.executable),
        help="external Python 3.11 interpreter with development dependencies installed",
    )
    parser.add_argument(
        "--pytest-args",
        default="-q",
        help="quoted arguments appended to pytest (default: -q)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    pytest_args = shlex.split(args.pytest_args, posix=os.name != "nt")
    try:
        verify_clean_archive(
            args.repo_root,
            Path(args.python).expanduser(),
            pytest_args,
        )
    except CleanArchiveError as exc:
        print(f"clean archive verification failed: {exc}", file=sys.stderr)
        return 1
    print("clean archive verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
