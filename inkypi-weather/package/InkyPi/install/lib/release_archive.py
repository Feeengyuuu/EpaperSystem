"""Build release ZIPs while excluding device-owned runtime font files."""

from __future__ import annotations

from pathlib import Path, PurePath
import sys
import zipfile


EXCLUDED_NAMES = {
    ".git",
    ".pytest_cache",
    ".tmp",
    ".venv",
    ".venv-test",
    ".venv-codex",
    ".venv-local",
    "__pycache__",
    "tmp",
}
YAHEI_SUFFIXES = {".ttc", ".ttf"}


def is_device_owned_yahei_font(path: PurePath) -> bool:
    """Return whether a release member is a device-owned YaHei binary."""

    return (
        path.name.casefold().startswith("msyh")
        and path.suffix.casefold() in YAHEI_SUFFIXES
    )


def build_release_archive(source_root: Path, artifact: Path) -> Path:
    root = Path(source_root).resolve()
    output = Path(artifact)
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if any(part in EXCLUDED_NAMES for part in relative.parts):
                continue
            if is_device_owned_yahei_font(relative):
                continue
            if path.is_symlink() or not path.is_file() or path.suffix == ".pyc":
                continue
            archive.write(path, relative.as_posix())
    return output


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit("usage: release_archive.py SOURCE_ROOT ARTIFACT")
    build_release_archive(Path(args[0]), Path(args[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
