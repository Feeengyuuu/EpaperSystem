"""Exercise the beginner shell flow with host mutations replaced by test doubles."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[2]
INSTALL = PROJECT / "install"
SPEC = importlib.util.spec_from_file_location("check_install", INSTALL / "check_install.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)
BASH = shutil.which("bash")


def quote(path):
    return shlex.quote(str(path).replace("\\", "/"))


def shell(script, *, input_text=None):
    if not BASH:
        pytest.skip("Bash is required for installer integration tests")
    # Use this test environment's Python, including Git Bash on Windows.
    python = f"python3() {{ {quote(sys.executable)} \"$@\" | tr -d '\\r'; }}; export -f python3\n"
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", "-c", python + script],
        input=input_text.encode("utf-8") if input_text is not None else None,
        stdin=None if input_text is not None else subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        start_new_session=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    result.stdout = result.stdout.decode("utf-8").replace("\r\n", "\n")
    result.stderr = result.stderr.decode("utf-8").replace("\r\n", "\n")
    return result


def prerequisites(tmp_path, **overrides):
    (tmp_path / "proc/device-tree").mkdir(parents=True)
    (tmp_path / "proc/device-tree/model").write_text("Raspberry Pi Zero 2 W\0")
    (tmp_path / "run/systemd/system").mkdir(parents=True)
    arguments = dict(
        root=tmp_path,
        system="Linux",
        version=(3, 11),
        which=lambda name: f"/usr/bin/{name}",
        disk_usage=lambda path: SimpleNamespace(free=4 * preflight.GIB),
        waveshare="epd7in3e",
    )
    arguments.update(overrides)
    return preflight.check_install(**arguments)


def test_fresh_pi_prerequisites_do_not_require_provider_credentials(tmp_path):
    assert all(row[0] for row in prerequisites(tmp_path))
    assert not (tmp_path / "etc/inkypi").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"system": "Windows"},
        {"version": (3, 9)},
        {"which": lambda name: None},
        {"disk_usage": lambda path: SimpleNamespace(free=1)},
        {"waveshare": "../../outside"},
        {"waveshare": "epdNotPackaged"},
    ],
)
def test_unsupported_prerequisites_fail_before_installation(tmp_path, overrides):
    assert not all(row[0] for row in prerequisites(tmp_path, **overrides))


def test_non_pi_and_missing_systemd_are_reported(tmp_path):
    prerequisites(tmp_path)
    (tmp_path / "proc/device-tree/model").write_text("Ordinary PC")
    (tmp_path / "run/systemd/system").rmdir()
    checks = preflight.check_install(root=tmp_path, system="Linux")
    errors = [row[1] for row in checks if not row[0]]
    assert "Raspberry Pi hardware must be detected." in errors
    assert "systemd must be running." in errors


@pytest.mark.parametrize(
    "entry", [REPO / "install.sh", INSTALL / "bootstrap.sh", REPO / "inkypi-weather/package/install_on_pi.sh"]
)
def test_help_and_display_list_need_no_sudo(entry):
    result = shell(f"bash {quote(entry)} --help")
    assert result.returncode == 0, result.stderr
    assert "--non-interactive" in result.stdout
    result = shell(f"bash {quote(entry)} --list-displays")
    assert result.returncode == 0, result.stderr
    assert "epd7in3e" in result.stdout
    assert "epd7in5_V2" in result.stdout
    assert "epdconfig" not in result.stdout


def test_local_check_runs_without_elevation():
    result = shell(
        f"sudo() {{ echo UNEXPECTED_SUDO; return 99; }}; export -f sudo; bash {quote(REPO / 'install.sh')} --check"
    )
    assert "UNEXPECTED_SUDO" not in result.stdout
    # CI is Linux without a Pi, and the developer workstation is Windows.
    assert result.returncode == 1
    assert "FAIL" in result.stdout


def test_noninteractive_mode_never_reads_stdin():
    result = shell(
        f"source {quote(INSTALL / 'bootstrap.sh')} --non-interactive; prepare_input; choose_display; echo selected:$WS_TYPE"
    )
    assert result.returncode == 0, result.stderr
    assert "selected:epd7in3e" in result.stdout


def test_interactive_mode_without_a_terminal_gives_actionable_error():
    result = shell(f"source {quote(INSTALL / 'bootstrap.sh')}; prepare_input")
    assert result.returncode == 1
    assert "--non-interactive" in result.stderr


def test_explicit_default_display_skips_the_menu():
    result = shell(f"source {quote(INSTALL / 'bootstrap.sh')} -W epd7in3e; choose_display; echo selected:$WS_TYPE")
    assert result.returncode == 0, result.stderr
    assert "selected:epd7in3e" in result.stdout
    assert "Choose 1/2/3" not in result.stdout


def test_all_keys_option_reaches_the_configuration_tool(tmp_path):
    result = shell(
        f"""
source {quote(INSTALL / "bootstrap.sh")} --all-keys
RUNTIME_ENV_FILE={quote(tmp_path / "runtime.env")}
python3() {{ printf '%s\\n' "$*"; }}
chown() {{ :; }}
chmod() {{ :; }}
configure_keys
echo changed:$KEYS_CHANGED
""",
        input_text="y\n",
    )
    assert result.returncode == 0, result.stderr
    assert "--all --lang" in result.stdout
    assert "changed:true" in result.stdout


@pytest.mark.parametrize("preflight_status, health_status", [(0, 0), (1, 0), (0, 1)])
def test_bootstrap_orchestrates_install_and_reports_failure(tmp_path, preflight_status, health_status):
    # Replace the host boundary while exercising the real main sequence.
    (tmp_path / "healthcheck.sh").write_text(f"exit {health_status}\n", newline="\n")
    result = shell(f"""
source {quote(INSTALL / "bootstrap.sh")} --non-interactive --skip-keys
SCRIPT_DIR={quote(tmp_path)}
require_root() {{ :; }}
choose_display() {{ :; }}
check_prerequisites() {{ return {preflight_status}; }}
run_install() {{ echo INSTALLED; }}
ensure_env_file() {{ :; }}
restart_service() {{ echo UNEXPECTED_RESTART; return 99; }}
show_access_info() {{ echo ACCESS_INSTRUCTIONS; }}
main
""")
    assert result.returncode == (preflight_status or health_status), result.stderr
    assert "UNEXPECTED_RESTART" not in result.stdout
    if preflight_status:
        assert "INSTALLED" not in result.stdout
        assert "preflight" in result.stderr
    else:
        assert "INSTALLED" in result.stdout
        assert "ACCESS_INSTRUCTIONS" in result.stdout
        if health_status:
            assert "not confirmed ready" in result.stderr


def test_downloaded_script_help_works_through_a_real_pipe():
    result = shell(f"cat {quote(REPO / 'install.sh')} | bash -s -- --help")
    assert result.returncode == 0, result.stderr
    assert "--check" in result.stdout


@pytest.mark.parametrize(
    "model, expected_resolution",
    [
        ("epd7in3e", [800, 480]),
        ("epd7in5_V2", [800, 480]),
        ("epd2in13_V4", None),
        ("", None),
    ],
)
def test_first_install_writes_selected_driver_and_correct_dimensions(tmp_path, model, expected_resolution):
    config = tmp_path / "config"
    config.mkdir()
    result = shell(f"""
source {quote(INSTALL / "install.sh")}
CONFIG_DIR={quote(config)}
SRC_PATH={quote(tmp_path / "empty-source")}
WS_TYPE={quote(model)}
install() {{ command cp "${{@: -2:1}}" "${{@: -1}}"; }}
chown() {{ :; }}
chmod() {{ :; }}
install_config
""")
    assert result.returncode == 0, result.stderr
    document = json.loads((config / "device.json").read_text())
    assert document["display_type"] == (model or "inky")
    assert document.get("resolution") == expected_resolution


def test_reinstall_preserves_personal_config_bytes(tmp_path):
    device = tmp_path / "device.json"
    original = '{"display_type":"inky","name":"My Frame","playlist_config":{"playlists":[]}}\n'
    device.write_text(original)
    result = shell(f"""
source {quote(INSTALL / "install.sh")}
CONFIG_DIR={quote(tmp_path)}
WS_TYPE=epd7in3e
chown() {{ :; }}
chmod() {{ :; }}
install_config
""")
    assert result.returncode == 0, result.stderr
    assert device.read_text() == original


def test_service_account_can_access_pimoroni_i2c(tmp_path):
    result = shell(f"""
source {quote(INSTALL / "install.sh")}
PROJECT_DIR={quote(tmp_path)}
id() {{ return 0; }}
getent() {{ return 0; }}
usermod() {{ echo "$*"; }}
install() {{ :; }}
chown() {{ :; }}
chmod() {{ :; }}
normalize_durable_font_permissions() {{ :; }}
ensure_service_user
""")
    assert result.returncode == 0, result.stderr
    assert "-a -G i2c inkypi" in result.stdout


def test_autodetected_dimensions_work_with_the_isolated_release_probe(tmp_path):
    spec = importlib.util.spec_from_file_location("beginner_release_probe", INSTALL / "preflight.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    source = tmp_path / "device.json"
    source.write_text('{"display_type": "inky", "startup": true}', encoding="utf-8")
    original = source.read_bytes()
    target = probe.prepare_config_copy(source, tmp_path / "probe/device_dev.json")
    document = json.loads(target.read_text())
    assert document["display_type"] == "mock"
    assert document["resolution"] == [800, 480]
    assert source.read_bytes() == original


@pytest.mark.parametrize("dirty, wrong_origin", [(False, False), (True, False), (False, True)])
def test_online_checkout_update_preserves_local_changes(tmp_path, dirty, wrong_origin):
    entry = tmp_path / "download.sh"
    shutil.copyfile(REPO / "install.sh", entry)
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    bootstrap = checkout / "inkypi-weather/package/InkyPi/install/bootstrap.sh"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_text('printf "BOOTSTRAP:%s\\n" "$*"\n')
    calls = tmp_path / "git-calls"
    origin = "https://example.test/other.git" if wrong_origin else "https://example.test/personal.git"
    result = shell(f"""
export EPAPERSYSTEM_REPO_URL=https://example.test/personal.git
export EPAPERSYSTEM_CHECKOUT_DIR={quote(checkout)}
source {quote(entry)}
uname() {{ echo Linux; }}
apt-get() {{ echo UNEXPECTED_APT; return 99; }}
check_permissions() {{ return 0; }}
git() {{
  printf '%s\\n' "$*" >> {quote(calls)}
  case "$*" in
    *'remote get-url origin') echo {quote(origin)} ;;
    *'status --porcelain') {'echo " M personal.py"' if dirty else ":"} ;;
    *'pull --ff-only') : ;;
    *) return 99 ;;
  esac
}}
main --non-interactive --lang zh-CN
""")
    if dirty or wrong_origin:
        assert result.returncode != 0
        assert "pull --ff-only" not in calls.read_text()
        assert "BOOTSTRAP" not in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert "pull --ff-only" in calls.read_text()
        assert "BOOTSTRAP:--non-interactive --lang zh-CN" in result.stdout


@pytest.mark.parametrize(
    "responses, expected",
    [
        ([None, {"release_id": "candidate", "status": "ready"}], 0),
        ([{"release_id": "old", "status": "ready"}], 1),
        ([{"release_id": "candidate", "status": "starting"}], 1),
        ([{"release_id": "candidate", "status": "degraded"}], 0),
        ([None], 1),
    ],
)
def test_healthcheck_waits_for_expected_release(tmp_path, responses, expected):
    release = tmp_path / "release"
    release.mkdir()
    (release / ".release-id").write_text("candidate\n", newline="\n")
    count = tmp_path / "count"
    count.write_text("0")
    cases = []
    for index, response in enumerate(responses):
        result = "return 22" if response is None else f"echo {quote(json.dumps(response))}"
        cases.append(f"{index}) {result} ;;")
    last = "return 22" if responses[-1] is None else f"echo {quote(json.dumps(responses[-1]))}"
    result = shell(f"""
source {quote(INSTALL / "healthcheck.sh")} --wait 5
INSTALL_DIR={quote(release)}
RUNTIME_ENV_FILE={quote(tmp_path / "absent.env")}
check_path() {{ :; }}
systemctl() {{ return 0; }}
sleep() {{ SECONDS=$((SECONDS + 2)); }}
curl() {{
  if [[ "$*" == *api/current_image* ]]; then return 22; fi
  local count
  count=$(cat {quote(count)})
  echo $((count + 1)) > {quote(count)}
  case "$count" in
    {" ".join(cases)}
    *) {last} ;;
  esac
}}
main
""")
    assert result.returncode == expected, result.stdout + result.stderr
    if responses[0] is None:
        assert int(count.read_text()) > 1
