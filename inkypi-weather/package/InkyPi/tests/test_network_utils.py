import subprocess
import logging

from utils import network_utils


def run_probes(monkeypatch, samples):
    """Run the actual watchdog with deterministic probe results and elapsed time."""
    samples = iter(samples)
    clock = [0.0]
    attempts, waits = [], []

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
            return next(samples)
        except StopIteration:
            stop.stopped = True
            return None

    monkeypatch.setattr(network_utils, "_wifi_is_connected", connected)
    monkeypatch.setattr(network_utils, "_default_gateway", lambda *_: "192.168.1.1")
    monkeypatch.setattr(network_utils, "_gateway_is_reachable", lambda *_: True)
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
