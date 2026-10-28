"""Font DLC: the manifest of optional fonts and the `inkypi fonts` command, with local fixtures."""

from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import sys

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "install" / "lib"))

import font_dlc  # noqa: E402
import private_score_fonts as store  # noqa: E402

INTER = (PACKAGE / "src/plugins/flight_radar/assets/fonts/Inter-Bold.ttf").read_bytes()
WOFF2 = b"wOF2" + bytes(range(256)) * 4


def _open_entry(key, payload):
    return {"key": key, "font": "Test", "use": "test", "kind": "open", "license": "SIL OFL 1.1",
            "url": f"https://cdn.jsdelivr.net/npm/test@1.0.0/files/{key}.woff2",
            "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def test_the_manifest_names_every_optional_font_the_pages_read():
    fonts = font_dlc.load_manifest()

    assert set(fonts) == set(store.LEAGUES) | set(store.PURPOSES)
    for key, entry in fonts.items():
        assert entry["font"] and entry["use"] and entry["license"], key
        if entry["kind"] == "open":
            assert entry["url"].startswith("https://cdn.jsdelivr.net/npm/") and "@" in entry["url"], key
            assert len(entry["sha256"]) == 64 and 0 < entry["bytes"] <= store.MAX_BYTES, key
            assert "OFL" in entry["license"] or "Apache" in entry["license"], key
        else:
            assert "url" not in entry and "sha256" not in entry, key
        if entry["kind"] == "commercial":
            assert entry["source"].startswith("https://"), key
    # Brand and unconfirmed fonts never carry a download or purchase link.
    assert all("source" not in entry for entry in fonts.values() if entry["kind"] in {"brand", "unverified"})


def test_a_manifest_entry_outside_a_pinned_https_source_is_refused(tmp_path):
    entry = {**_open_entry("seriea", WOFF2), "url": "http://example.com/seriea.woff2"}
    manifest = tmp_path / "font_dlc.json"
    manifest.write_text(json.dumps({"schema": 1, "fonts": [entry]}), encoding="utf-8")

    with pytest.raises(font_dlc.FontDlcError, match="pinned https"):
        font_dlc.load_manifest(manifest)


def test_a_download_must_match_its_pinned_size_and_hash():
    entry = _open_entry("seriea", WOFF2)

    assert font_dlc.download(entry, lambda request, timeout: _Response(WOFF2)) == WOFF2
    with pytest.raises(font_dlc.FontDlcError, match="SHA256"):
        font_dlc.download(entry, lambda request, timeout: _Response(WOFF2[:-1] + b"x"))


def test_a_font_installs_under_its_own_formats_fixed_name():
    assert font_dlc.target_name("seriea", WOFF2) == "sports-seriea-score.woff2"
    assert font_dlc.target_name("nba", INTER) == "sports-nba-score.ttf"
    assert font_dlc.target_name("game_deals_price", INTER) == "game-deals-price.ttf"
    with pytest.raises(font_dlc.FontDlcError, match="TTF file"):
        font_dlc.target_name("game_deals_price", WOFF2)
    with pytest.raises(font_dlc.FontDlcError, match="not a TTF"):
        font_dlc.target_name("nba", b"%PDF" + WOFF2)


def test_installed_reports_the_present_file_and_another_format_blocks_install(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "FONT_DIRECTORY", tmp_path)
    (tmp_path / "sports-seriea-score.woff").write_bytes(b"wOFF")

    assert font_dlc.installed("seriea") == "sports-seriea-score.woff"
    assert font_dlc.installed("nba") is None
    with pytest.raises(font_dlc.FontDlcError, match="remove it first"):
        font_dlc.install_payload("seriea", WOFF2, install=lambda *args: pytest.fail("installed"))


def test_install_open_downloads_each_pinned_font_into_the_shared_helper(tmp_path, monkeypatch, capsys):
    manifest = {"seriea": _open_entry("seriea", WOFF2), "nba": {
        "key": "nba", "font": "Knockout", "use": "NBA", "kind": "commercial", "license": "licence",
        "source": "https://example.com/knockout"}}
    monkeypatch.setattr(font_dlc, "load_manifest", lambda: manifest)
    monkeypatch.setattr(font_dlc.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(font_dlc, "update_lock", nullcontext)
    monkeypatch.setattr(store, "FONT_DIRECTORY", tmp_path)
    calls = []

    def install(payload, name, sha256, size):
        calls.append((name, sha256, size))
        return {"status": "installed"}

    assert font_dlc.main(["install", "--open"], opener=lambda request, timeout: _Response(WOFF2),
                         install=install) == 0
    assert calls == [("sports-seriea-score.woff2", hashlib.sha256(WOFF2).hexdigest(), len(WOFF2))]
    assert "seriea: installed" in capsys.readouterr().out
    # A commercial font is never downloaded; it only installs from the owner's licensed file.
    with pytest.raises(font_dlc.FontDlcError, match="add"):
        font_dlc.main(["install", "nba"], install=install)


def test_add_installs_a_licensed_local_file_through_the_shared_helper(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(font_dlc.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(font_dlc, "update_lock", nullcontext)
    monkeypatch.setattr(store, "FONT_DIRECTORY", tmp_path)
    font = tmp_path / "Knockout.ttf"
    font.write_bytes(INTER)
    seen = []

    def install(payload, name, sha256, size):
        seen.append(store.validate_font(payload, name, sha256, size))
        return {"status": "installed", "font": seen[-1]}

    assert font_dlc.main(["add", "nba", str(font)], install=install) == 0
    assert "Inter" in seen[0]["family"]
    assert capsys.readouterr().out.startswith("nba: installed")


def test_changes_need_root_but_the_list_does_not(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(store, "FONT_DIRECTORY", tmp_path / "missing")
    monkeypatch.setattr(font_dlc.os, "geteuid", lambda: 1000, raising=False)

    assert font_dlc.main(["list"]) == 0
    listing = capsys.readouterr().out
    assert "open licence" in listing and "Bai Jamjuree Bold" in listing and "Knockout" in listing
    with pytest.raises(font_dlc.FontDlcError, match="sudo"):
        font_dlc.main(["install", "--open"])


def test_the_device_cli_routes_fonts_to_the_dlc_helper():
    script = (PACKAGE / "install" / "inkypi").read_text(encoding="utf-8")
    assert 'fonts)\n        run_fonts_cli "$@"' in script
    assert '"$PROGRAM_PATH/install/lib/font_dlc.py"' in script
