#!/usr/bin/env python3
"""Root broker for the four privileged operations InkyPi is allowed to request."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
from typing import Any, Callable

try:
    import pwd
except ImportError:  # Allows protocol tests on Windows; the broker only runs on Linux.
    pwd = None


MAX_MESSAGE_BYTES = 4096
COMMAND_TIMEOUT_SECONDS = 8
DEFAULT_NET_ROOT = Path("/sys/class/net")
_INTERFACE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_ACTIONS = frozenset({"poweroff", "reboot", "wifi_powersave_off", "wifi_reconnect"})
_SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)


class BrokerProtocolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class BrokerRequest:
    __slots__ = ("action", "interface")

    def __init__(self, action: str, interface: str | None = None) -> None:
        self.action = action
        self.interface = interface


def validate_request(
    value: object,
    *,
    net_root: str | os.PathLike[str] = DEFAULT_NET_ROOT,
) -> BrokerRequest:
    if type(value) is not dict:
        raise BrokerProtocolError("invalid_request", "request must be a JSON object")
    action = value.get("action")
    if type(action) is not str or action not in _ACTIONS:
        raise BrokerProtocolError("unknown_action", "unknown privileged action")
    expected_keys = {"action"}
    interface = None
    if action.startswith("wifi_"):
        expected_keys.add("interface")
        interface = value.get("interface")
        if (
            type(interface) is not str
            or interface in {".", ".."}
            or not _INTERFACE_NAME.fullmatch(interface)
        ):
            raise BrokerProtocolError("invalid_interface", "invalid network interface")
        interface_path = Path(net_root) / interface
        if not interface_path.exists():
            raise BrokerProtocolError("invalid_interface", "network interface does not exist")
    if set(value) != expected_keys:
        raise BrokerProtocolError("invalid_request", "request contains unsupported fields")
    return BrokerRequest(action, interface)


def _commands_for(request: BrokerRequest) -> tuple[list[str], ...]:
    if request.action == "poweroff":
        return (["/usr/bin/systemctl", "poweroff"],)
    if request.action == "reboot":
        return (["/usr/bin/systemctl", "reboot"],)
    if request.action == "wifi_powersave_off":
        return ([
            "/usr/sbin/iw",
            "dev",
            request.interface,
            "set",
            "power_save",
            "off",
        ],)
    return (
        ["/usr/sbin/iw", "dev", request.interface, "set", "power_save", "off"],
        ["/usr/bin/nmcli", "radio", "wifi", "on"],
        ["/usr/bin/nmcli", "device", "set", request.interface, "managed", "yes"],
        ["/usr/bin/nmcli", "device", "wifi", "rescan", "ifname", request.interface],
        ["/usr/bin/nmcli", "device", "connect", request.interface],
    )


def execute_request(
    request: BrokerRequest,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    for command in _commands_for(request):
        try:
            result = runner(
                command,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"ok": False, "code": "command_failed", "error": str(error)[:512]}
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
            return {"ok": False, "code": "command_failed", "error": detail[:512]}
    return {"ok": True, "code": "ok"}


def _send_response(connection: socket.socket, response: dict[str, Any]) -> bool:
    payload = json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
    try:
        connection.sendall(payload[:MAX_MESSAGE_BYTES])
    except OSError:
        return False
    return True


def _read_request(connection: socket.socket) -> object:
    payload = bytearray()
    while len(payload) <= MAX_MESSAGE_BYTES:
        chunk = connection.recv(min(1024, MAX_MESSAGE_BYTES + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        newline = payload.find(b"\n")
        if newline >= 0:
            if newline >= MAX_MESSAGE_BYTES:
                raise BrokerProtocolError("request_too_large", "request exceeds 4 KiB")
            if payload[newline + 1 :]:
                raise BrokerProtocolError("invalid_request", "trailing request data")
            try:
                return json.loads(bytes(payload[:newline]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise BrokerProtocolError("invalid_json", "request is not valid JSON") from error
    if len(payload) > MAX_MESSAGE_BYTES:
        raise BrokerProtocolError("request_too_large", "request exceeds 4 KiB")
    raise BrokerProtocolError("invalid_request", "request must end with a newline")


def handle_connection(
    connection: socket.socket,
    *,
    allowed_uid: int,
    net_root: str | os.PathLike[str] = DEFAULT_NET_ROOT,
) -> None:
    try:
        credentials = connection.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, 12)
        _pid, uid, _gid = struct.unpack("3i", credentials)
        if uid != allowed_uid:
            raise BrokerProtocolError("peer_not_allowed", "peer uid is not authorized")
        request = validate_request(_read_request(connection), net_root=net_root)
        response = execute_request(request)
    except BrokerProtocolError as error:
        response = {"ok": False, "code": error.code, "error": str(error)}
    except Exception:
        response = {"ok": False, "code": "internal_error", "error": "broker error"}
    _send_response(connection, response)


def _activation_socket() -> socket.socket:
    listen_pid = int(os.environ.get("LISTEN_PID", "0") or 0)
    listen_fds = int(os.environ.get("LISTEN_FDS", "0") or 0)
    if listen_pid != os.getpid() or listen_fds != 1:
        raise RuntimeError("broker must be started by systemd socket activation")
    return socket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)


def main() -> int:
    try:
        if pwd is None:
            raise RuntimeError("POSIX account database is unavailable")
        allowed_uid = pwd.getpwnam("inkypi").pw_uid
        listener = _activation_socket()
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        print(f"inkypi privileged broker startup failed: {error}", file=sys.stderr)
        return 1
    with listener:
        while True:
            connection, _address = listener.accept()
            with connection:
                connection.settimeout(5)
                handle_connection(connection, allowed_uid=allowed_uid)


if __name__ == "__main__":
    raise SystemExit(main())
