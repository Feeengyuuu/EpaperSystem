import subprocess
import logging

from utils import network_utils


def run_probes(monkeypatch, samples, *, reboot_allowed=lambda _wall: False, reboots=None, gateway=True):
    """Run the actual watchdog with deterministic probe results and elapsed time.

    A sample of "activating" means iw reports no link while NetworkManager is
    still connecting, which the plain probe reports as unknown (None).
    """
    samples = iter(samples)
    clock = [0.0]
    attempts, waits = [], []
    current = [None]
    reboots = reboots if reboots is not None else []

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            waits.append(seconds)
            clock[0] += seconds
            return self.stopped

    stop = Stop()

    def connected(_interface):
        try:
            current[0] = next(samples)
        except StopIteration:
            stop.stopped = True
            current[0] = None
        return None if current[0] == "activating" else current[0]

    def reboot(_interface, outage, tries):
        reboots.append((clock[0], outage, tries))
        return True

    monkeypatch.setattr(network_utils, "_wifi_is_connected", connected)
    monkeypatch.setattr(network_utils, "_wifi_link_activating", lambda *_: current[0] == "activating")
    monkeypatch.setattr(network_utils, "_wifi_reboot_allowed", reboot_allowed)
    monkeypatch.setattr(network_utils, "reboot_for_wifi_recovery", reboot)
    monkeypatch.setattr(network_utils, "_default_gateway", lambda *_: "192.168.1.1")
    monkeypatch.setattr(network_utils, "_gateway_is_reachable", lambda *_: gateway)
    monkeypatch.setattr(network_utils, "disable_wifi_powersave", lambda *_: True)
    monkeypatch.setattr(network_utils, "reconnect_wifi", lambda *_: attempts.append(clock[0]) or True)
    monkeypatch.setattr(network_utils.time, "monotonic", lambda: clock[0])
    network_utils.wifi_reconnect_watchdog_loop(stop_event=stop)
    return attempts, waits


def test_transient_disconnect_recovers_without_reconnecting(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        attempts, waits = run_probes(monkeypatch, [True, False, False, True])
    assert attempts == []
    assert waits[:4] == [60, 15, 15, 60]
    assert "without intervention" in caplog.text


def test_confirmed_disconnect_reconnects_then_checks_the_result(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        attempts, _ = run_probes(monkeypatch, [False, False, False, True])
    assert attempts == [30]
    assert "after reconnect" in caplog.text


def test_unknown_probe_breaks_failure_streak_without_claiming_recovery(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        attempts, _ = run_probes(monkeypatch, [False, False, None, False, True])
    assert attempts == []
    assert caplog.text.count("watchdog recovered") == 1


def test_sustained_outage_backs_off_reconnect_attempts(monkeypatch):
    attempts, _ = run_probes(monkeypatch, [False] * 150)
    assert len(attempts) >= 4
    assert attempts[1] - attempts[0] >= 180
    assert attempts[2] - attempts[1] >= 360
    assert attempts[3] - attempts[2] >= 720


def test_missing_probe_tools_are_unknown_not_disconnected(monkeypatch):
    monkeypatch.setattr(network_utils, "_find_iw", lambda: None)
    monkeypatch.setattr(network_utils, "_find_command", lambda *_: None)
    assert network_utils._wifi_is_connected() is None
    assert network_utils._gateway_is_reachable(gateway="192.168.1.1") is None


def test_ping_tool_error_is_not_gateway_packet_loss(monkeypatch):
    monkeypatch.setattr(network_utils, "_run_command", lambda *a, **k: subprocess.CompletedProcess([], 2, "", "permission denied"))
    assert network_utils._gateway_is_reachable(gateway="192.168.1.1", ping_path="/bin/ping") is None


def test_networkmanager_activation_is_allowed_to_finish(monkeypatch):
    def run(command, **kwargs):
        output = "Not connected.\n" if command[0] == "/bin/iw" else "wlan0:wifi:connecting (getting IP configuration)\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(network_utils, "_run_command", run)
    assert network_utils._wifi_is_connected(iw_path="/bin/iw", nmcli_path="/bin/nmcli") is None


def test_wireless_interfaces_returns_only_wireless_names(monkeypatch):
    class FakeEntry:
        def __init__(self, name):
            self.name = name

    class FakePath:
        def __init__(self, path):
            self.path = path

        def iterdir(self):
            return [
                FakeEntry("lo"),
                FakeEntry("eth0"),
                FakeEntry("wlan0"),
                FakeEntry("wlx001122"),
            ]

    monkeypatch.setattr(network_utils, "Path", FakePath)

    assert network_utils._wireless_interfaces("/sys/class/net") == ["wlan0", "wlx001122"]


def test_disable_wifi_powersave_uses_privileged_broker_for_wireless_interfaces(monkeypatch):
    calls = []

    def fake_disable(interface):
        calls.append(interface)

    monkeypatch.setattr(
        network_utils.privileged_actions,
        "wifi_powersave_off",
        fake_disable,
    )

    assert network_utils.disable_wifi_powersave(
        interface_names=["wlan0"],
        iw_path="/usr/sbin/iw",
    ) is True

    assert calls == ["wlan0"]


def test_default_gateway_parses_ip_route_output(monkeypatch):
    def fake_run(command, timeout=8):
        assert command == ["/usr/bin/ip", "route", "show", "default", "dev", "wlan0"]
        return subprocess.CompletedProcess(
            command,
            0,
            "default via 192.168.1.254 dev wlan0 proto dhcp src 192.168.1.183 metric 600\n",
            "",
        )

    monkeypatch.setattr(network_utils, "_run_command", fake_run)

    assert network_utils._default_gateway("wlan0", ip_path="/usr/bin/ip") == "192.168.1.254"


def test_wifi_is_connected_uses_iw_link(monkeypatch):
    def fake_run(command, timeout=8):
        assert command == ["/usr/sbin/iw", "dev", "wlan0", "link"]
        return subprocess.CompletedProcess(command, 0, "Connected to aa:bb:cc:dd:ee:ff\n", "")

    monkeypatch.setattr(network_utils, "_run_command", fake_run)

    assert network_utils._wifi_is_connected("wlan0", iw_path="/usr/sbin/iw") is True


def test_reconnect_wifi_uses_single_fixed_privileged_action(monkeypatch):
    calls = []

    monkeypatch.setattr(
        network_utils.privileged_actions,
        "wifi_reconnect",
        lambda interface: calls.append(interface),
    )

    assert network_utils.reconnect_wifi("wlan0", nmcli_path="/usr/bin/nmcli") is True

    assert calls == ["wlan0"]


def test_stalled_activation_counts_as_outage_after_grace(monkeypatch, caplog):
    # 10-10: the driver refused every association while NetworkManager kept
    # "connecting", so the probe stayed unknown and no reconnect ever ran.
    with caplog.at_level(logging.INFO):
        attempts, waits = run_probes(monkeypatch, ["activating"] * 20)
    assert waits[0] == 15
    assert attempts and network_utils.WIFI_ACTIVATION_GRACE_SECONDS <= attempts[0] <= 180
    assert "activation has not completed" in caplog.text


def test_activation_finishing_within_grace_does_not_reconnect(monkeypatch):
    attempts, _ = run_probes(monkeypatch, ["activating"] * 4 + [True])
    assert attempts == []


def test_interleaved_disconnects_and_activation_keep_the_failure_streak(monkeypatch):
    attempts, _ = run_probes(monkeypatch, ["activating", False] * 10)
    assert attempts and attempts[0] <= 90


class _RebootRecorder(list):
    """Records reboot requests and closes the guard, like the persisted record."""

    def __init__(self, allowed):
        super().__init__()
        self.allowed = allowed

    def append(self, entry):
        super().append(entry)
        self.allowed[0] = False


def test_unrecoverable_outage_reboots_once_after_reconnects_fail(monkeypatch):
    allowed = [True]
    reboots = _RebootRecorder(allowed)
    attempts, _ = run_probes(
        monkeypatch,
        [False] * 400,
        reboot_allowed=lambda _wall: allowed[0],
        reboots=reboots,
    )
    assert len(reboots) == 1
    _at, outage, tries = reboots[0]
    assert outage >= network_utils.WIFI_REBOOT_AFTER_OUTAGE_SECONDS
    assert tries >= network_utils.WIFI_REBOOT_AFTER_RECONNECT_ATTEMPTS
    assert len(attempts) >= network_utils.WIFI_REBOOT_AFTER_RECONNECT_ATTEMPTS


def test_outage_without_reboot_permission_keeps_reconnecting(monkeypatch):
    reboots = []
    attempts, _ = run_probes(monkeypatch, [False] * 400, reboots=reboots)
    assert reboots == []
    assert len(attempts) >= 4


def test_recovery_before_the_reboot_threshold_does_not_reboot(monkeypatch):
    reboots = []
    attempts, _ = run_probes(
        monkeypatch,
        [False] * 40 + [True] + [False] * 40,
        reboot_allowed=lambda _wall: True,
        reboots=reboots,
    )
    assert attempts
    assert reboots == []


def test_reboot_guard_requires_uptime_and_cooldown(monkeypatch):
    monkeypatch.delenv("INKYPI_DATA_DIR", raising=False)
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", None)
    cooldown = network_utils.WIFI_REBOOT_COOLDOWN_SECONDS
    monkeypatch.setattr(network_utils, "_system_uptime_seconds", lambda: 60.0)
    assert network_utils._wifi_reboot_allowed(1_000_000.0) is False
    monkeypatch.setattr(network_utils, "_system_uptime_seconds", lambda: None)
    assert network_utils._wifi_reboot_allowed(1_000_000.0) is False
    monkeypatch.setattr(network_utils, "_system_uptime_seconds", lambda: 7200.0)
    assert network_utils._wifi_reboot_allowed(1_000_000.0) is True
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", 1_000_000.0 - cooldown + 60)
    assert network_utils._wifi_reboot_allowed(1_000_000.0) is False
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", 1_000_000.0 - cooldown - 60)
    assert network_utils._wifi_reboot_allowed(1_000_000.0) is True
    # A record in the future means the clock moved: it cannot date the last
    # reboot, so only a boot older than the cooldown may reboot again.
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", 1_000_000.0 + cooldown + 60)
    assert network_utils._wifi_reboot_allowed(1_000_000.0) is False
    monkeypatch.setattr(network_utils, "_system_uptime_seconds", lambda: float(cooldown + 60))
    assert network_utils._wifi_reboot_allowed(1_000_000.0) is True


def test_reboot_request_is_recorded_before_the_broker_call(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", None)
    seen = []

    def reboot():
        seen.append(network_utils._last_wifi_reboot())

    monkeypatch.setattr(network_utils.privileged_actions, "reboot", reboot)
    with caplog.at_level(logging.INFO):
        assert network_utils.reboot_for_wifi_recovery("wlan0", 1300.0, 3) is True
    assert seen and seen[0] is not None
    assert (tmp_path / "wifi_watchdog_reboot.json").exists()
    assert "rebooting after an unrecoverable outage" in caplog.text
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", None)
    monkeypatch.setattr(network_utils, "_system_uptime_seconds", lambda: 7200.0)
    # The persisted record survives a restart of the process (in-memory reset).
    assert network_utils._wifi_reboot_allowed(seen[0] + 60) is False


def test_failed_reboot_request_is_reported_and_still_bounded(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", None)

    def reboot():
        raise network_utils.privileged_actions.PrivilegedActionUnavailable("broker down")

    monkeypatch.setattr(network_utils.privileged_actions, "reboot", reboot)
    with caplog.at_level(logging.WARNING):
        assert network_utils.reboot_for_wifi_recovery("wlan0", 1300.0, 3) is False
    assert "reboot request failed" in caplog.text
    assert network_utils._last_wifi_reboot() is not None


def test_link_activating_needs_no_link_and_networkmanager_connecting(monkeypatch):
    outputs = {}

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, outputs[command[0]], "")

    monkeypatch.setattr(network_utils, "_run_command", run)
    outputs.update({"/bin/iw": "Not connected.\n", "/bin/nmcli": "wlan0:wifi:connecting (prepare)\n"})
    assert network_utils._wifi_link_activating(iw_path="/bin/iw", nmcli_path="/bin/nmcli") is True
    outputs.update({"/bin/iw": "Connected to aa:bb:cc:dd:ee:ff (on wlan0)\n"})
    assert network_utils._wifi_link_activating(iw_path="/bin/iw", nmcli_path="/bin/nmcli") is False
    outputs.update({"/bin/iw": "Not connected.\n", "/bin/nmcli": "wlan0:wifi:disconnected\n"})
    assert network_utils._wifi_link_activating(iw_path="/bin/iw", nmcli_path="/bin/nmcli") is False
    monkeypatch.setattr(network_utils, "_find_iw", lambda: None)
    monkeypatch.setattr(network_utils, "_find_command", lambda *_: None)
    assert network_utils._wifi_link_activating() is False


def test_reboot_waits_for_the_last_reconnect_to_settle(monkeypatch):
    allowed = [True]
    reboots = _RebootRecorder(allowed)
    attempts, _ = run_probes(
        monkeypatch, [False] * 400, reboot_allowed=lambda _wall: allowed[0], reboots=reboots,
    )
    at = reboots[0][0]
    last_attempt = max(attempt for attempt in attempts if attempt <= at)
    assert at - last_attempt >= network_utils.WIFI_REBOOT_RECONNECT_SETTLE_SECONDS


def test_stalled_activation_outage_counts_from_when_it_started(monkeypatch):
    allowed = [True]
    reboots = _RebootRecorder(allowed)
    run_probes(monkeypatch, ["activating"] * 400, reboot_allowed=lambda _wall: allowed[0], reboots=reboots)
    at, outage, _tries = reboots[0]
    # The first activating sample is at t=0, so the outage spans the whole run.
    assert abs(at - outage) < 1


def test_guard_deferral_is_logged_once_per_outage(monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        run_probes(monkeypatch, [False] * 400)
    assert caplog.text.count("reboot deferred by its uptime or cooldown guard") == 1


def test_unsaved_reboot_record_blocks_reboot_until_this_boot_is_old(monkeypatch, tmp_path, caplog):
    # A full SD card or read-only root must not become a reboot every 30 minutes.
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", None)
    calls = []
    monkeypatch.setattr(network_utils.privileged_actions, "reboot", lambda: calls.append("reboot"))

    def refuse(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(network_utils, "atomic_write_json", refuse)
    monkeypatch.setattr(network_utils, "_system_uptime_seconds", lambda: 7200.0)
    with caplog.at_level(logging.WARNING):
        assert network_utils.reboot_for_wifi_recovery("wlan0", 1300.0, 3) is False
    assert calls == []
    assert "reboot record could not be saved" in caplog.text
    # The in-memory record still stops this process from asking again.
    assert network_utils._wifi_reboot_allowed(network_utils._last_wifi_reboot() + 60) is False

    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", None)
    monkeypatch.setattr(
        network_utils, "_system_uptime_seconds",
        lambda: float(network_utils.WIFI_REBOOT_COOLDOWN_SECONDS + 60),
    )
    assert network_utils.reboot_for_wifi_recovery("wlan0", 1300.0, 3) is True
    assert calls == ["reboot"]


def test_reboot_without_data_dir_is_bounded_by_this_boot_age(monkeypatch):
    monkeypatch.delenv("INKYPI_DATA_DIR", raising=False)
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", None)
    calls = []
    monkeypatch.setattr(network_utils.privileged_actions, "reboot", lambda: calls.append("reboot"))
    monkeypatch.setattr(network_utils, "_system_uptime_seconds", lambda: 7200.0)
    assert network_utils.reboot_for_wifi_recovery("wlan0", 1300.0, 3) is False
    assert calls == []


def test_last_reboot_prefers_the_newest_of_disk_and_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    (tmp_path / "wifi_watchdog_reboot.json").write_text('{"requested_at": 1000.0}', encoding="utf-8")
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", 5000.0)
    assert network_utils._last_wifi_reboot() == 5000.0
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", 10.0)
    assert network_utils._last_wifi_reboot() == 1000.0


def test_corrupt_reboot_records_are_ignored_without_crashing(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(network_utils, "_last_wifi_reboot_wall", None)
    record = tmp_path / "wifi_watchdog_reboot.json"
    for text in ('{"requested_at": true}', '{"requested_at": NaN}', '{"requested_at": 1' + '0' * 400 + '}',
                 '{"requested_at": "soon"}', '[1, 2]', 'not json'):
        record.write_text(text, encoding="utf-8")
        assert network_utils._last_wifi_reboot() is None


def test_link_up_with_inconclusive_gateway_check_clears_old_outage_state(monkeypatch):
    # Without a conclusive "reachable" sample (ping broken), short outages days
    # apart must not add up to the reconnect count that permits a reboot.
    allowed = [True]
    reboots = _RebootRecorder(allowed)
    samples = ([False] * 4 + [True] * 3) * 6 + [False] * 4
    attempts, _ = run_probes(
        monkeypatch, samples, reboot_allowed=lambda _wall: allowed[0], reboots=reboots, gateway=None,
    )
    assert len(attempts) >= 3
    assert reboots == []
