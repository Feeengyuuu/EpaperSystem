import configparser
import subprocess
import sys
import zipfile
from pathlib import Path


INSTALL_ROOT = Path(__file__).resolve().parents[1] / "install"


def _parse_unit(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


def _release_archive_python():
    source = (INSTALL_ROOT / "install.sh").read_text(encoding="utf-8")
    marker = 'python3 - "$PROJECT_DIR" "$artifact" <<\'PY\'\n'
    _prefix, heredoc = source.split(marker, maxsplit=1)
    archive_python, _suffix = heredoc.split("\nPY\n", maxsplit=1)
    return archive_python


def test_main_unit_is_unprivileged_and_hardened():
    unit = _parse_unit(INSTALL_ROOT / "inkypi.service")

    assert unit["Service"]["User"] == "inkypi"
    assert unit["Service"]["Group"] == "inkypi"
    assert unit["Service"]["NoNewPrivileges"].lower() == "true"
    assert unit["Service"]["WorkingDirectory"] == "/run/inkypi"
    assert unit["Service"]["RuntimeDirectory"] == "inkypi"
    assert unit["Service"]["EnvironmentFile"] == "-/etc/inkypi/inkypi.env"
    assert "PYTHONDONTWRITEBYTECODE=1" in unit["Service"]["Environment"]
    assert "INKYPI_CACHE_DIR=/var/cache/inkypi" in unit["Service"]["Environment"]
    assert "INKYPI_DATA_DIR=/var/lib/inkypi/data" in unit["Service"]["Environment"]
    assert "LG_WD=/run/inkypi" in unit["Service"]["Environment"]
    assert "/run/inkypi" in unit["Service"]["ReadWritePaths"].split()
    assert unit["Unit"]["Requires"] == "inkypi-privileged.socket"
    assert unit["Service"]["AmbientCapabilities"] == "CAP_NET_BIND_SERVICE"
    assert unit["Service"]["CapabilityBoundingSet"] == "CAP_NET_BIND_SERVICE"


def test_privileged_socket_is_root_owned_and_group_bounded():
    socket_unit = _parse_unit(
        INSTALL_ROOT / "privileged" / "inkypi-privileged.socket"
    )
    service_unit = _parse_unit(
        INSTALL_ROOT / "privileged" / "inkypi-privileged.service"
    )

    assert socket_unit["Socket"]["ListenStream"] == "/run/inkypi-privileged.sock"
    assert socket_unit["Socket"]["SocketUser"] == "root"
    assert socket_unit["Socket"]["SocketGroup"] == "inkypi"
    assert socket_unit["Socket"]["SocketMode"] == "0660"
    assert service_unit["Service"]["User"] == "root"
    assert service_unit["Service"]["Environment"] == "PYTHONDONTWRITEBYTECODE=1"
    assert service_unit["Service"]["NoNewPrivileges"].lower() == "true"
    assert service_unit["Service"]["ProtectSystem"] == "strict"
    assert service_unit["Service"]["PrivateDevices"].lower() == "true"
    assert service_unit["Service"]["ProtectKernelLogs"].lower() == "true"
    assert service_unit["Service"]["RestrictSUIDSGID"].lower() == "true"
    assert service_unit["Service"]["UMask"] == "0077"
    assert service_unit["Service"]["Restart"] == "on-failure"
    assert set(service_unit["Service"]["CapabilityBoundingSet"].split()) == {
        "CAP_NET_ADMIN",
        "CAP_SYS_BOOT",
    }


def test_install_manages_service_user_runtime_ownership_and_broker_units():
    script = (INSTALL_ROOT / "install.sh").read_text(encoding="utf-8")

    assert "useradd --system" in script
    assert "/var/lib/inkypi" in script
    assert "/var/cache/inkypi" in script
    assert "inkypi-privileged.socket" in script
    assert "inkypi-privileged.service" in script
    assert 'INSTALL_ROOT="/opt/$APPNAME"' in script
    assert 'INSTALL_PATH="$INSTALL_ROOT/current"' in script
    assert "/var/lib/inkypi/config" in script
    assert "/var/lib/inkypi/display" in script
    assert "/var/lib/inkypi/plugins" in script
    assert "/etc/inkypi/inkypi.env" in script
    assert '"/usr/local/inkypi/src/config/device.json"' in script
    assert '"/usr/local/inkypi/.env"' in script
    assert 'ln -sf "$SRC_PATH"' not in script
    assert "set -Eeuo pipefail" in script
    assert "chown -R -h inkypi:inkypi" in script


def test_install_creates_root_owned_durable_font_directory():
    script = (INSTALL_ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'install -d -o root -g inkypi -m 0750 "$DATA_DIR/fonts"' in script


def test_release_archive_excludes_yahei_binaries_from_any_directory(tmp_path):
    project = tmp_path / "project"
    files = {
        "src/app.py": b"print('included')\n",
        "src/static/fonts/NotoSansSC-VF.ttf": b"tracked fallback",
        "src/static/fonts/msyh.ttf": b"proprietary regular",
        "src/static/fonts/msyh.ttc": b"proprietary regular collection",
        "src/plugins/sports_dashboard/fonts/msyhbd.ttc": b"proprietary bold collection",
        "vendor/fonts/MSYHL.TTC": b"proprietary light collection",
    }
    for relative, content in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    artifact = tmp_path / "release.zip"
    subprocess.run(
        [sys.executable, "-", str(project), str(artifact)],
        input=_release_archive_python(),
        text=True,
        check=True,
    )

    with zipfile.ZipFile(artifact) as archive:
        members = set(archive.namelist())

    assert "src/app.py" in members
    assert "src/static/fonts/NotoSansSC-VF.ttf" in members
    assert {
        "src/static/fonts/msyh.ttf",
        "src/static/fonts/msyh.ttc",
        "src/plugins/sports_dashboard/fonts/msyhbd.ttc",
        "vendor/fonts/MSYHL.TTC",
    }.isdisjoint(members)


def test_installed_launcher_exports_mutable_runtime_roots():
    launcher = (INSTALL_ROOT / "inkypi").read_text(encoding="utf-8")

    assert "INKYPI_CACHE_DIR=/var/cache/inkypi" in launcher
    assert "INKYPI_DATA_DIR=/var/lib/inkypi/data" in launcher


def test_install_loader_only_waits_for_a_new_background_process():
    script_lines = (INSTALL_ROOT / "install.sh").read_text(encoding="utf-8").splitlines()

    for index, line in enumerate(script_lines):
        if "show_loader " not in line or line.lstrip().startswith("show_loader()"):
            continue
        previous_command = script_lines[index - 1].rstrip()
        assert previous_command.endswith("&"), (
            f"show_loader at line {index + 1} would wait for a stale process id"
        )


def test_bootstrap_writes_keys_to_runtime_environment_file():
    script = (INSTALL_ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert 'RUNTIME_ENV_FILE="/etc/inkypi/inkypi.env"' in script
    assert '--env-file "$RUNTIME_ENV_FILE"' in script
    assert '--env-file "$PROJECT_DIR/.env"' not in script


def test_uninstall_stops_broker_service_and_preserves_runtime_data():
    script = (INSTALL_ROOT / "uninstall.sh").read_text(encoding="utf-8")

    assert "systemctl stop inkypi-privileged.service" in script
    assert "systemctl stop inkypi-privileged.socket" in script
    assert "Preserving /etc/inkypi, /var/lib/inkypi, and /var/cache/inkypi" in script
    assert 'rm -rf "/var/lib/inkypi"' not in script


def test_web_and_network_modules_have_no_privileged_shell_fallback():
    settings_source = (
        Path(__file__).resolve().parents[1] / "src" / "blueprints" / "settings.py"
    ).read_text(encoding="utf-8")
    network_source = (
        Path(__file__).resolve().parents[1] / "src" / "utils" / "network_utils.py"
    ).read_text(encoding="utf-8")

    assert "os.system" not in settings_source
    assert "sudo " not in settings_source
    assert '"power_save", "off"' not in network_source
    assert '"device", "connect"' not in network_source
