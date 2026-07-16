import configparser
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


INSTALL_ROOT = Path(__file__).resolve().parents[1] / "install"
RELEASE_ARCHIVE_HELPER = INSTALL_ROOT / "lib" / "release_archive.py"
FONT_PERMISSIONS_HELPER = INSTALL_ROOT / "lib" / "font_permissions.py"


def _parse_unit(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser


def _release_archive_builder(entrypoint, artifact_variable):
    source = (INSTALL_ROOT / entrypoint).read_text(encoding="utf-8")
    match = re.search(
        rf'python3 "\$SCRIPT_DIR/(?P<helper>[^\"]+)" '
        rf'"\$PROJECT_DIR" "{re.escape(artifact_variable)}"',
        source,
    )
    assert match is not None, f"{entrypoint} does not invoke the shared archive builder"
    return INSTALL_ROOT / match.group("helper")


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


def test_updates_repair_runtime_env_without_persistent_root_service_hook():
    source = (INSTALL_ROOT / "inkypi.service").read_text(encoding="utf-8")
    helper_path = INSTALL_ROOT / "repair_env_permissions.py"
    helper = helper_path.read_text(encoding="utf-8")
    bootstrap = (INSTALL_ROOT / "bootstrap_admin.py").read_text(encoding="utf-8")

    assert "ExecStartPre=+" not in source
    assert "repair_env_permissions.py" not in source
    assert "from repair_env_permissions import repair_runtime_env_permissions" in bootstrap
    assert 'if args.command == "ensure-bootstrap" and os.name != "nt":' in bootstrap
    assert "repair_runtime_env_permissions()" in bootstrap
    assert "os.O_NOFOLLOW" in helper
    assert "os.O_DIRECTORY" in helper
    assert "dir_fd=directory_fd" in helper
    assert "stat.S_ISREG" in helper
    assert "os.fchown(" in helper
    assert "os.fchmod(" in helper
    assert "os.chown(" not in helper
    assert "os.chmod(" not in helper


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


def test_install_merges_missing_keys_from_resolved_legacy_checkout():
    script = (INSTALL_ROOT / "install.sh").read_text(encoding="utf-8")

    assert 'legacy_src="$(readlink -f -- "/usr/local/inkypi/src")"' in script
    assert 'legacy_env_candidates+=("$(dirname "$legacy_src")/.env")' in script
    assert 'merge_args+=(--merge-from "$env_candidate")' in script
    assert 'configure_api_keys.py" --env-file "$RUNTIME_ENV_FILE"' in script


def test_install_delegates_durable_font_permissions_to_fd_helper():
    script = (INSTALL_ROOT / "install.sh").read_text(encoding="utf-8")

    directory_command = 'install -d -o root -g inkypi -m 0750 "$DATA_DIR/fonts"'
    helper_command = 'python3 "$SCRIPT_DIR/lib/font_permissions.py" "$DATA_DIR"'
    assert directory_command not in script
    assert helper_command in script
    assert 'find -P "$DATA_DIR/fonts"' not in script


def test_font_permission_helper_uses_no_follow_file_descriptors_only():
    assert FONT_PERMISSIONS_HELPER.is_file()
    source = FONT_PERMISSIONS_HELPER.read_text(encoding="utf-8")

    assert "os.O_DIRECTORY" in source
    assert "os.O_NOFOLLOW" in source
    assert "def _open_absolute_directory" in source
    assert "dir_fd=directory_fd" in source
    assert "os.mkdir(" in source
    assert "dir_fd=data_fd" in source
    assert source.count("_open_absolute_directory(") >= 3
    assert "os.fchown(" in source
    assert "os.fchmod(" in source
    assert '"fonts", dir_fd=data_fd, follow_symlinks=False' in " ".join(
        source.split()
    )
    assert source.count("os.fstat(") >= 3
    assert "os.chmod(" not in source
    assert "os.makedirs(" not in source


def test_shared_release_archive_builder_excludes_yahei_binaries_from_any_directory(
    tmp_path,
):
    project = tmp_path / "project"
    files = {
        "src/app.py": b"print('included')\n",
        "src/static/fonts/NotoSansSC-VF.ttf": b"tracked fallback",
        "src/static/fonts/msyh.ttf": b"proprietary regular",
        "src/static/fonts/msyh.ttc": b"proprietary regular collection",
        "src/plugins/sports_dashboard/fonts/msyhbd.ttc": b"proprietary bold collection",
        "vendor/deep/fonts/MSYHL.TTC": b"proprietary light collection",
    }
    for relative, content in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    artifact = tmp_path / "release.zip"
    subprocess.run(
        [
            sys.executable,
            str(RELEASE_ARCHIVE_HELPER),
            str(project),
            str(artifact),
        ],
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
        "vendor/deep/fonts/MSYHL.TTC",
    }.isdisjoint(members)


@pytest.mark.parametrize(
    ("entrypoint", "artifact_variable"),
    (("install.sh", "$artifact"), ("update.sh", "$ARTIFACT")),
)
def test_release_entrypoints_delegate_once_to_the_shared_builder_on_normal_path(
    entrypoint, artifact_variable
):
    source = (INSTALL_ROOT / entrypoint).read_text(encoding="utf-8")
    builder = _release_archive_builder(entrypoint, artifact_variable)

    assert builder == RELEASE_ARCHIVE_HELPER
    assert source.count("lib/release_archive.py") == 1
    assert "import zipfile" not in source
    assert "root.rglob" not in source


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
