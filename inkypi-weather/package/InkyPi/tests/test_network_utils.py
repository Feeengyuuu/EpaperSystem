import subprocess

from src.utils import network_utils


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


def test_disable_wifi_powersave_runs_iw_for_wireless_interfaces(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(network_utils.subprocess, "run", fake_run)

    assert network_utils.disable_wifi_powersave(
        interface_names=["wlan0"],
        iw_path="/usr/sbin/iw",
    ) is True

    assert calls == [
        (
            ["/usr/sbin/iw", "dev", "wlan0", "set", "power_save", "off"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 5,
                "check": False,
            },
        )
    ]


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


def test_reconnect_wifi_rescans_and_activates_known_connections(monkeypatch):
    calls = []

    def fake_disable(interface_names=None, iw_path=None):
        calls.append(["disable_wifi_powersave", interface_names, iw_path])
        return True

    def fake_connections(nmcli_path=None):
        assert nmcli_path == "/usr/bin/nmcli"
        return ["netplan-wlan0-Void"]

    def fake_run(command, timeout=8):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(network_utils, "disable_wifi_powersave", fake_disable)
    monkeypatch.setattr(network_utils, "_known_wifi_connections", fake_connections)
    monkeypatch.setattr(network_utils, "_run_command", fake_run)

    assert network_utils.reconnect_wifi("wlan0", nmcli_path="/usr/bin/nmcli") is True

    assert calls == [
        ["disable_wifi_powersave", ["wlan0"], None],
        ["/usr/bin/nmcli", "radio", "wifi", "on"],
        ["/usr/bin/nmcli", "device", "set", "wlan0", "managed", "yes"],
        ["/usr/bin/nmcli", "device", "wifi", "rescan", "ifname", "wlan0"],
        ["/usr/bin/nmcli", "device", "connect", "wlan0"],
        ["/usr/bin/nmcli", "connection", "up", "netplan-wlan0-Void"],
    ]
