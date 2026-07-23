from pathlib import Path
import sys
import zipfile


INSTALL_LIB = Path(__file__).resolve().parents[1] / "install" / "lib"
sys.path.insert(0, str(INSTALL_LIB))

from release_archive import build_release_archive  # noqa: E402


def test_release_archive_excludes_runtime_env_secrets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (source / ".env").write_text("SECRET=do-not-ship\n", encoding="utf-8")
    (source / ".env.example").write_text("SECRET=\n", encoding="utf-8")
    package_cache = source / ".pc-packages"
    package_cache.mkdir()
    (package_cache / "local-only.py").write_text("LOCAL = True\n", encoding="utf-8")
    artifact = tmp_path / "release.zip"

    build_release_archive(source, artifact)

    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
    assert "app.py" in names
    assert ".env.example" in names
    assert ".env" not in names
    assert not any(name.startswith(".pc-packages/") for name in names)
