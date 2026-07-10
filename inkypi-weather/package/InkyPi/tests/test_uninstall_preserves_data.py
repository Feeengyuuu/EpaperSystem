from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "install" / "uninstall.sh"


def test_default_uninstall_preserves_mutable_state_and_purge_is_explicit():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "--purge" in source
    assert 'PURGE=false' in source
    assert 'if [[ "$PURGE" == "true" ]]' in source
    assert 'read -r -p' in source
    assert 'PURGE' in source
    assert "Preserving /etc/inkypi, /var/lib/inkypi, and /var/cache/inkypi" in source
    assert 'rm -rf "$ETC_ROOT" "$STATE_ROOT" "$CACHE_ROOT"' in source


def test_uninstall_removes_release_binaries_but_not_arbitrary_paths():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'INSTALL_ROOT="/opt/$APPNAME"' in source
    assert 'UPDATE_BIN="/usr/local/sbin/inkypi-update"' in source
    assert 'rm -rf "$INSTALL_ROOT"' in source
    assert "rm -rf /" not in source
