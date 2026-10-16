"""Pinned SSH and LAN HTTP access to the e-paper device for release steps.

The device address is resolved from its mDNS name at run time and is never
written to tracked files. Host-key verification stays pinned to the device
alias in the project-local known_hosts file, so a changed host key stops every
step instead of being accepted. SSH errors are reported by exit code only:
helper stderr can quote configuration that contains secrets.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import socket
import subprocess
import sys

from release_task import ReleaseError, require

DEVICE_HOST = "ColoredEpaperFrame.local"
DEVICE_HOSTNAME = "ColoredEpaperFrame"
HOST_KEY_ALIAS = "coloredepaperframe.local"
SSH_USER = "feeengyuuu"
SSH_KEY = ".ssh/epaperpod_codex_20260525"
KNOWN_HOSTS = ".tmp/epaperpod_known_hosts"
WINDOWS_OPENSSH = r"C:\Windows\System32\OpenSSH"

CURRENT_LINK = "/opt/inkypi/current"
PREVIOUS_LINK = "/opt/inkypi/previous"
RELEASES_DIR = "/opt/inkypi/releases"
HELPER = "sudo -n /opt/inkypi/current/venv_inkypi/bin/python /var/tmp/live_all_instances_acceptance.py "
CONFIG = "/var/lib/inkypi/config/device.json"
RUNTIME = "/var/lib/inkypi/data/runtime_state.json"
DISPLAY = "/var/lib/inkypi/display/display_manifest.json"
IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
SECRET_KEY_WORDS = ("token", "secret", "password", "cookie", "api_key", "apikey")


def safe(value):
    """Drop secret-looking keys and provider URLs before anything reaches evidence."""
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()
                if not any(word in str(key).lower() for word in SECRET_KEY_WORDS)}
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"https?://[^\s]+", "[provider-url]", value)
    return value


def digest(document) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def openssh_program(name: str) -> str:
    """Windows' bundled OpenSSH (the identity used for every round), or PATH elsewhere."""
    if sys.platform == "win32":
        return WINDOWS_OPENSSH + "\\" + name + ".exe"
    return name


def ssh_options(repo: Path) -> list[str]:
    return [
        "-4", "-i", str(Path(repo) / SSH_KEY),
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=12",
        "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
        "-o", "HostKeyAlias=" + HOST_KEY_ALIAS, "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=" + str(Path(repo) / KNOWN_HOSTS),
    ]


def is_ipv4(value: object) -> bool:
    return (isinstance(value, str) and IPV4.fullmatch(value) is not None
            and all(int(part) <= 255 for part in value.split(".")))


def resolve_address(address: str | None = None, resolver=socket.getaddrinfo) -> str:
    """Resolve the device by name first: Windows OpenSSH cannot always resolve ``.local``."""
    if not address:
        try:
            address = resolver(DEVICE_HOST, 80, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        except (OSError, IndexError):
            address = None
    require(is_ipv4(address), "Device address could not be resolved.")
    return address


def release_name(path: str) -> str:
    """Return the release id of an ``/opt/inkypi/releases/<id>`` path."""
    release = PurePosixPath(path)
    require(str(release.parent) == RELEASES_DIR and release.name, f"Unexpected release path: {path!r}")
    return release.name


class Device:
    """SSH/SCP/HTTP access to one resolved device address."""

    def __init__(self, repo: Path, address: str, runner=subprocess.run):
        require(is_ipv4(address), "Device address must be an IPv4 address")
        self.repo = Path(repo)
        self.address = address
        self.runner = runner
        self.options = ssh_options(self.repo)
        self.target = SSH_USER + "@" + address
        self._client = None

    @classmethod
    def connect(cls, repo: Path, address: str | None = None) -> Device:
        for required in (Path(repo) / SSH_KEY, Path(repo) / KNOWN_HOSTS):
            require(required.is_file(), f"Missing pinned SSH material: {required.name}")
        device = cls(repo, resolve_address(address))
        require(device.remote("hostname").decode().strip() == DEVICE_HOSTNAME, "Pinned host identity mismatch")
        return device

    @property
    def base_url(self) -> str:
        return "http://" + self.address

    @property
    def client(self):
        if self._client is None:
            import requests

            self._client = requests.Session()
            self._client.trust_env = False
        return self._client

    def run(self, command: str, *, timeout: float | None = None, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, input: bytes | None = None) -> subprocess.CompletedProcess:
        """Run one remote command and return the completed process without raising."""
        return self.runner([openssh_program("ssh"), *self.options, self.target, command],
                           stdout=stdout, stderr=stderr, input=input, timeout=timeout)

    def copy(self, local: Path, remote_path: str) -> int:
        """Copy one file with the legacy SCP protocol, pinned like every SSH call."""
        result = self.runner([openssh_program("scp"), "-O", *self.options, str(local), self.target + ":" + remote_path])
        return result.returncode

    def remote(self, command: str, timeout: float = 45, input: bytes | None = None) -> bytes:
        result = self.run(command, timeout=timeout, input=input)
        if result.returncode:
            raise ReleaseError("read-only SSH operation returned " + str(result.returncode))
        return result.stdout

    def document(self, path: str) -> dict:
        return json.loads(self.remote(HELPER + "--print-runtime-state --runtime-state " + shlex.quote(path)))

    def resolved_link(self, link: str) -> str:
        return self.remote("readlink -f " + shlex.quote(link)).decode().strip()

    def http_json(self, route: str) -> dict:
        response = self.client.get(self.base_url + route, timeout=(3, 15))
        return {"http_status": response.status_code, "payload": safe(response.json())}
