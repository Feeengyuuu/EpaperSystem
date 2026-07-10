import json
from pathlib import Path
import sys

import pytest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class FakeSocket:
    def __init__(self, response):
        self.response = response
        self.sent = b""
        self.connected_to = None
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, path):
        self.connected_to = path

    def sendall(self, payload):
        self.sent += payload

    def recv(self, _size):
        response, self.response = self.response, b""
        return response


def test_client_sends_one_bounded_json_line_with_fixed_action(tmp_path):
    from utils.privileged_actions import PrivilegedActionClient

    fake = FakeSocket(b'{"ok":true,"code":"ok"}\n')
    socket_path = tmp_path / "broker.sock"
    client = PrivilegedActionClient(
        socket_path=socket_path,
        socket_factory=lambda *_args: fake,
    )

    result = client.wifi_reconnect("wlan0")

    assert result.code == "ok"
    assert fake.connected_to == str(socket_path)
    assert fake.timeout == 45.0
    assert fake.sent.endswith(b"\n")
    assert json.loads(fake.sent) == {
        "action": "wifi_reconnect",
        "interface": "wlan0",
    }


@pytest.mark.parametrize(
    "interface",
    ["wlan0;reboot", "../../lo", "", ".", "..", "x" * 16],
)
def test_client_rejects_interface_injection_before_socket_use(interface):
    from utils.privileged_actions import InvalidPrivilegedRequest, PrivilegedActionClient

    client = PrivilegedActionClient(
        socket_factory=lambda *_args: pytest.fail("socket should not be opened")
    )

    with pytest.raises(InvalidPrivilegedRequest):
        client.wifi_reconnect(interface)


def test_broker_failure_is_never_reported_as_success():
    from utils.privileged_actions import PrivilegedActionClient, PrivilegedActionFailed

    fake = FakeSocket(b'{"ok":false,"code":"command_failed","error":"no"}\n')
    client = PrivilegedActionClient(socket_factory=lambda *_args: fake)

    with pytest.raises(PrivilegedActionFailed) as caught:
        client.reboot()

    assert caught.value.code == "command_failed"


def test_shutdown_route_returns_503_when_broker_is_unavailable(monkeypatch):
    from flask import Flask
    from blueprints import settings
    from utils.privileged_actions import PrivilegedActionUnavailable

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(settings.settings_bp)
    monkeypatch.setattr(
        settings.privileged_actions,
        "poweroff",
        lambda: (_ for _ in ()).throw(PrivilegedActionUnavailable("offline")),
    )

    response = app.test_client().post("/shutdown", json={})

    assert response.status_code == 503
    assert response.get_json()["error_code"] == "privileged_action_unavailable"
