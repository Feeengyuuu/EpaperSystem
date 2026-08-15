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
    static_display = source / "src" / "static" / "display"
    context_cache.mkdir(parents=True)
    plugin_cache.mkdir(parents=True)
    static_images.mkdir(parents=True)
    (static_display / "objects").mkdir(parents=True)
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
    (static_display / "display_revision").write_text("commit-id\n", encoding="ascii")
    (static_display / "display_manifest.json").write_text("{}\n", encoding="utf-8")
    (static_display / "objects" / "commit-id.png").write_bytes(b"runtime-object")
    artifact = tmp_path / "release.zip"

    build_release_archive(source, artifact)

    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
    assert "src/static/images/inkypi.png" in names
    assert "src/static/images/current_image.png" not in names
    assert not any(name.startswith("src/static/display/") for name in names)
    assert not any(name.startswith("src/plugins/.context_cache/") for name in names)
    assert not any(
        name.startswith("src/plugins/lol_info/.lol_info_cache/")
        for name in names
    )


def test_release_archive_normalizes_linux_entrypoints_to_lf(tmp_path):
    source = tmp_path / "source"
    (source / "install").mkdir(parents=True)
    (source / "src").mkdir()
    (source / "assets").mkdir()
    (source / "install" / "inkypi-update").write_bytes(
        b"#!/usr/bin/env python3\r\nprint('ok')\r\n"
    )
    (source / "install" / "update.sh").write_bytes(
        b"#!/bin/bash\r\nexit 0\r\n"
    )
    (source / "install" / "inkypi.service").write_bytes(
        b"[Service]\r\nExecStart=/usr/local/bin/inkypi\r\n"
    )
    (source / ".envrc").write_bytes(
        b"#!/usr/bin/env bash\r\nexport INKYPI_DEV=1\r\n"
    )
    (source / "src" / "app.py").write_bytes(b"VALUE = 1\r\n")
    binary = b"\x89PNG\r\n\x1a\n\x00\r\n"
    (source / "assets" / "pixel.png").write_bytes(binary)
    artifact = tmp_path / "release.zip"

    build_release_archive(source, artifact)

    with zipfile.ZipFile(artifact) as archive:
        assert archive.read("install/inkypi-update") == (
            b"#!/usr/bin/env python3\nprint('ok')\n"
        )
        assert archive.read("install/update.sh") == b"#!/bin/bash\nexit 0\n"
        assert archive.read("install/inkypi.service") == (
            b"[Service]\nExecStart=/usr/local/bin/inkypi\n"
        )
        assert archive.read(".envrc") == (
            b"#!/usr/bin/env bash\nexport INKYPI_DEV=1\n"
        )
        assert archive.read("src/app.py") == b"VALUE = 1\n"
        assert archive.read("assets/pixel.png") == binary
