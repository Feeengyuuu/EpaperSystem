"""Narrow client for the root-owned InkyPi privileged action broker."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
from typing import Callable


DEFAULT_SOCKET_PATH = Path(
    os.environ.get("INKYPI_PRIVILEGED_SOCKET", "/run/inkypi-privileged.sock")
)
MAX_MESSAGE_BYTES = 4096
DEFAULT_TIMEOUT_SECONDS = 45.0
_INTERFACE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_ACTIONS = frozenset({"poweroff", "reboot", "wifi_powersave_off", "wifi_reconnect"})
_AF_UNIX = getattr(socket, "AF_UNIX", 1)


class PrivilegedActionError(RuntimeError):
    """Base class for a broker request that did not complete."""

    code = "privileged_action_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.code = code or self.code
        super().__init__(message)


class InvalidPrivilegedRequest(PrivilegedActionError, ValueError):
    code = "invalid_privileged_request"


class PrivilegedActionUnavailable(PrivilegedActionError):
    code = "privileged_action_unavailable"


class PrivilegedActionFailed(PrivilegedActionError):
    pass


@dataclass(frozen=True)
class PrivilegedActionResult:
    code: str


class PrivilegedActionClient:
    """Send a single fixed action over a bounded Unix-domain socket request."""

    def __init__(
        self,
        *,
        socket_path: str | os.PathLike[str] = DEFAULT_SOCKET_PATH,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.timeout_seconds = float(timeout_seconds)
        self._socket_factory = socket_factory

    def poweroff(self) -> PrivilegedActionResult:
        return self._request("poweroff")

    def reboot(self) -> PrivilegedActionResult:
        return self._request("reboot")

    def wifi_powersave_off(self, interface: str) -> PrivilegedActionResult:
        return self._request("wifi_powersave_off", interface=interface)

    def wifi_reconnect(self, interface: str) -> PrivilegedActionResult:
        return self._request("wifi_reconnect", interface=interface)

    def _request(
        self,
        action: str,
        *,
        interface: str | None = None,
    ) -> PrivilegedActionResult:
        if action not in _ACTIONS:
            raise InvalidPrivilegedRequest("unknown privileged action")
        request: dict[str, str] = {"action": action}
        if action.startswith("wifi_"):
            if (
                type(interface) is not str
                or interface in {".", ".."}
                or not _INTERFACE_NAME.fullmatch(interface)
            ):
                raise InvalidPrivilegedRequest("invalid network interface")
            request["interface"] = interface
        elif interface is not None:
            raise InvalidPrivilegedRequest("interface is not valid for this action")

        payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(payload) > MAX_MESSAGE_BYTES:
            raise InvalidPrivilegedRequest("privileged request is too large")

        try:
            with self._socket_factory(_AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(payload)
                response_bytes = self._read_line(connection)
        except (OSError, TimeoutError) as error:
            raise PrivilegedActionUnavailable("privileged action broker is unavailable") from error

        try:
            response = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PrivilegedActionUnavailable("privileged action broker returned invalid data") from error
        if type(response) is not dict or type(response.get("ok")) is not bool:
            raise PrivilegedActionUnavailable("privileged action broker returned invalid data")
        code = response.get("code")
        if type(code) is not str or not code:
            raise PrivilegedActionUnavailable("privileged action broker omitted its result code")
        if not response["ok"]:
            message = response.get("error")
            if type(message) is not str or not message:
                message = "privileged action failed"
            raise PrivilegedActionFailed(message, code=code)
        return PrivilegedActionResult(code=code)

    @staticmethod
    def _read_line(connection: socket.socket) -> bytes:
        response = bytearray()
        while len(response) <= MAX_MESSAGE_BYTES:
            chunk = connection.recv(min(1024, MAX_MESSAGE_BYTES + 1 - len(response)))
            if not chunk:
                break
            response.extend(chunk)
            newline = response.find(b"\n")
            if newline >= 0:
                if newline >= MAX_MESSAGE_BYTES:
                    raise PrivilegedActionUnavailable(
                        "privileged action response is too large"
                    )
                if response[newline + 1 :]:
                    raise PrivilegedActionUnavailable(
                        "privileged action broker returned trailing data"
                    )
                return bytes(response[:newline])
        if len(response) > MAX_MESSAGE_BYTES:
            raise PrivilegedActionUnavailable("privileged action response is too large")
        raise PrivilegedActionUnavailable("privileged action broker closed without a response")


def poweroff() -> PrivilegedActionResult:
    return PrivilegedActionClient().poweroff()


def reboot() -> PrivilegedActionResult:
    return PrivilegedActionClient().reboot()


def wifi_powersave_off(interface: str) -> PrivilegedActionResult:
    return PrivilegedActionClient().wifi_powersave_off(interface)


def wifi_reconnect(interface: str) -> PrivilegedActionResult:
    return PrivilegedActionClient().wifi_reconnect(interface)
