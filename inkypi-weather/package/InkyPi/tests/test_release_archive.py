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


def test_release_archive_excludes_plugin_runtime_caches(tmp_path):
    source = tmp_path / "source"
    plugin = source / "src" / "plugins" / "sports_dashboard"
    cache = plugin / "cache"
    assets = plugin / "assets"
    cache.mkdir(parents=True)
    assets.mkdir()
    (plugin / "sports_dashboard.py").write_text("PLUGIN = True\n", encoding="utf-8")
    (cache / "valve_csapi_matches.json").write_text(
        '{"url":"https://api.csapi.de"}\n',
        encoding="utf-8",
    )
    (assets / "league-logo.png").write_bytes(b"asset")
    artifact = tmp_path / "release.zip"

    build_release_archive(source, artifact)

    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
    assert "src/plugins/sports_dashboard/sports_dashboard.py" in names
    assert "src/plugins/sports_dashboard/assets/league-logo.png" in names
    assert not any(
        name.startswith("src/plugins/sports_dashboard/cache/")
        for name in names
    )


def test_release_archive_excludes_hidden_plugin_caches_and_current_display(
    tmp_path,
):
    source = tmp_path / "source"
    context_cache = source / "src" / "plugins" / ".context_cache"
    plugin_cache = (
        source
        / "src"
        / "plugins"
        / "lol_info"
        / ".lol_info_cache"
    )
    static_images = source / "src" / "static" / "images"
    context_cache.mkdir(parents=True)
    plugin_cache.mkdir(parents=True)
    static_images.mkdir(parents=True)
    (context_cache / "plugin.json").write_text(
        '{"runtime":true}\n',
        encoding="utf-8",
    )
    (plugin_cache / "rotation.json.tmp").write_text(
        '{"runtime":true}\n',
        encoding="utf-8",
    )
    (static_images / "current_image.png").write_bytes(b"runtime-display")
    (static_images / "inkypi.png").write_bytes(b"packaged-logo")
    artifact = tmp_path / "release.zip"

    build_release_archive(source, artifact)

    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
    assert "src/static/images/inkypi.png" in names
    assert "src/static/images/current_image.png" not in names
    assert not any(name.startswith("src/plugins/.context_cache/") for name in names)
    assert not any(
        name.startswith("src/plugins/lol_info/.lol_info_cache/")
        for name in names
    )
