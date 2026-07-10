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
