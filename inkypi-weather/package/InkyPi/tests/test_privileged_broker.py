import importlib.util
import json
from pathlib import Path
import struct
import subprocess

import pytest


BROKER_PATH = (
    Path(__file__).resolve().parents[1]
    / "install"
    / "privileged"
    / "inkypi_privileged.py"
)


def _load_broker():
    spec = importlib.util.spec_from_file_location("inkypi_privileged_test", BROKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_broker_rejects_unknown_action_and_interface_injection(tmp_path):
    broker = _load_broker()
    net_root = tmp_path / "net"
    net_root.mkdir()
    (net_root / "wlan0").mkdir()

    with pytest.raises(broker.BrokerProtocolError) as unknown:
        broker.validate_request({"action": "shell", "args": ["id"]}, net_root=net_root)
    assert unknown.value.code == "unknown_action"

    with pytest.raises(broker.BrokerProtocolError) as injected:
        broker.validate_request(
            {"action": "wifi_reconnect", "interface": "wlan0;reboot"},
            net_root=net_root,
        )
    assert injected.value.code == "invalid_interface"

    for interface in (".", ".."):
        with pytest.raises(broker.BrokerProtocolError) as relative:
            broker.validate_request(
                {"action": "wifi_reconnect", "interface": interface},
                net_root=net_root,
            )
        assert relative.value.code == "invalid_interface"


def test_broker_uses_argv_only_fixed_commands(tmp_path):
    broker = _load_broker()
    net_root = tmp_path / "net"
    net_root.mkdir()
    (net_root / "wlan0").mkdir()
    request = broker.validate_request(
        {"action": "wifi_powersave_off", "interface": "wlan0"},
        net_root=net_root,
    )
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    result = broker.execute_request(request, runner=runner)

    assert result == {"ok": True, "code": "ok"}
    assert calls == [
        (
            ["/usr/sbin/iw", "dev", "wlan0", "set", "power_save", "off"],
            {
                "capture_output": True,
                "text": True,
                "timeout": 8,
                "check": False,
            },
        )
    ]


class FakeConnection:
    def __init__(self, *, uid, request=b'{"action":"reboot"}\n'):
        self.credentials = struct.pack("3i", 123, uid, 456)
        self.request = request
        self.sent = b""

    def getsockopt(self, *_args):
        return self.credentials

    def recv(self, _size):
        request, self.request = self.request, b""
        return request

    def sendall(self, payload):
        self.sent += payload


def test_broker_rejects_peer_uid_before_parsing_or_execution(monkeypatch):
    broker = _load_broker()
    connection = FakeConnection(uid=2000, request=b"not json\n")
    monkeypatch.setattr(
        broker,
        "execute_request",
        lambda _request: pytest.fail("unauthorized peer reached command execution"),
    )

    broker.handle_connection(connection, allowed_uid=1000)

    response = json.loads(connection.sent)
    assert response["ok"] is False
    assert response["code"] == "peer_not_allowed"


def test_broker_rejects_request_over_four_kib_before_execution(monkeypatch):
    broker = _load_broker()
    connection = FakeConnection(uid=1000, request=b"x" * 4097)
    monkeypatch.setattr(
        broker,
        "execute_request",
        lambda _request: pytest.fail("oversize request reached command execution"),
    )

    broker.handle_connection(connection, allowed_uid=1000)

    response = json.loads(connection.sent)
    assert response["ok"] is False
    assert response["code"] == "request_too_large"


def test_broker_rejects_valid_json_frame_whose_newline_exceeds_four_kib(monkeypatch):
    broker = _load_broker()
    padded = b'{"action":"reboot"}' + (b" " * 4077) + b"\n"
    assert len(padded) == 4097
    connection = FakeConnection(uid=1000, request=padded)
    monkeypatch.setattr(
        broker,
        "execute_request",
        lambda _request: pytest.fail("oversize JSON reached command execution"),
    )

    broker.handle_connection(connection, allowed_uid=1000)

    response = json.loads(connection.sent)
    assert response["ok"] is False
    assert response["code"] == "request_too_large"


def test_broken_client_during_response_does_not_escape_connection_handler():
    broker = _load_broker()
    connection = FakeConnection(uid=2000)

    def broken_send(_payload):
        raise BrokenPipeError("client timed out")

    connection.sendall = broken_send

    assert broker.handle_connection(connection, allowed_uid=1000) is None
