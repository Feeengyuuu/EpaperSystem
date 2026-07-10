import importlib.util
import io
import subprocess
import tarfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = REPO_ROOT / "tools" / "verify_clean_archive.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("verify_clean_archive", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_validator_rejects_non_311_interpreter(monkeypatch):
    tool = _load_tool()

    monkeypatch.setattr(tool, "query_python_version", lambda _python: (3, 12, 7))

    with pytest.raises(tool.CleanArchiveError, match="Python 3.11"):
        tool.require_python_311(Path("python"))


@pytest.mark.parametrize(
    "member_name",
    ("../outside.txt", "..\\outside.txt", "/outside.txt", "C:\\outside.txt"),
)
def test_archive_extractor_rejects_parent_traversal(tmp_path, member_name):
    tool = _load_tool()
    archive = tmp_path / "archive.tar"
    payload = b"escape"
    with tarfile.open(archive, "w") as handle:
        member = tarfile.TarInfo(member_name)
        member.size = len(payload)
        handle.addfile(member, io.BytesIO(payload))

    with pytest.raises(tool.CleanArchiveError, match="unsafe archive member"):
        tool.extract_archive(archive, tmp_path / "extract")

    assert not (tmp_path / "outside.txt").exists()


def test_verifier_cleans_only_its_owned_temp_tree_on_failure(tmp_path, monkeypatch):
    tool = _load_tool()
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    def fake_archive(_repo_root, archive_path):
        archive_path.write_bytes(b"archive")

    def fake_extract(_archive_path, destination):
        (destination / tool.PROJECT_RELATIVE).mkdir(parents=True)

    monkeypatch.setattr(tool, "require_python_311", lambda _python: None)
    monkeypatch.setattr(tool, "create_head_archive", fake_archive)
    monkeypatch.setattr(tool, "extract_archive", fake_extract)
    monkeypatch.setattr(
        tool,
        "run_archive_tests",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "missing dependency", ""),
    )
    monkeypatch.setattr(tool, "collect_ignored_paths", lambda _repo: ["ignored/font.ttf"])

    with pytest.raises(tool.CleanArchiveError, match="ignored/font.ttf"):
        tool.verify_clean_archive(
            REPO_ROOT,
            Path("python"),
            ["-q"],
            temp_parent=tmp_path,
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.iterdir()) == [sentinel]


def test_verifier_absolutizes_external_interpreter_before_archive_cwd(tmp_path, monkeypatch):
    tool = _load_tool()
    python = tmp_path / "python.exe"
    python.touch()
    seen = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tool, "require_python_311", lambda candidate: seen.append(candidate))
    monkeypatch.setattr(
        tool,
        "create_head_archive",
        lambda _repo, archive_path: archive_path.write_bytes(b"archive"),
    )
    monkeypatch.setattr(
        tool,
        "extract_archive",
        lambda _archive, destination: (destination / tool.PROJECT_RELATIVE).mkdir(parents=True),
    )
    monkeypatch.setattr(
        tool,
        "run_archive_tests",
        lambda _root, candidate, _args: (
            seen.append(candidate) or subprocess.CompletedProcess([], 0, "", "")
        ),
    )

    tool.verify_clean_archive(REPO_ROOT, Path("python.exe"), [], temp_parent=tmp_path)

    assert seen == [python.resolve(), python.resolve()]
