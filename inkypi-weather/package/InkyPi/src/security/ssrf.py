"""DNS-pinned SSRF policy for browser and proxy HTTP(S) egress."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
import re
import socket
import threading
from urllib.parse import urlsplit, urlunsplit


MAX_URL_LENGTH = 8192
MAX_ALLOWLIST_ENTRIES = 128
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_METADATA_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "169.254.169.254/32",
        "169.254.170.2/32",
        "100.100.100.200/32",
        "192.0.0.192/32",
        "fd00:ec2::254/128",
    )
)
_ALLOWLISTABLE_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


class UnsafeTarget(ValueError):
    """Raised when a URL or any resolved address violates egress policy."""


@dataclass(frozen=True)
class ApprovedTarget:
    normalized_url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]

    @property
    def authority(self) -> str:
        host = self.hostname
        try:
            if ipaddress.ip_address(host).version == 6:
                host = f"[{host}]"
        except ValueError:
            pass
        default_port = 443 if self.scheme == "https" else 80
        return host if self.port == default_port else f"{host}:{self.port}"


class SSRFPolicy:
    """Normalize an HTTP(S) URL and pin it to a fully validated DNS answer set."""

    def __init__(
        self,
        *,
        resolver=socket.getaddrinfo,
        allowed_private_hosts=(),
        allowed_private_cidrs=(),
    ):
        self._resolver = resolver
        self.allowed_private_hosts = frozenset(
            self._allowlisted_hosts(allowed_private_hosts)
        )
        self.allowed_private_networks = tuple(
            self._allowlisted_networks(allowed_private_cidrs)
        )

    @classmethod
    def from_environment(cls, *, resolver=socket.getaddrinfo):
        return cls(
            resolver=resolver,
            allowed_private_hosts=_environment_items(
                "INKYPI_BROWSER_ALLOWED_HOSTS"
            ),
            allowed_private_cidrs=_environment_items(
                "INKYPI_BROWSER_ALLOWED_CIDRS"
            ),
        )

    def resolve_and_validate(self, url) -> ApprovedTarget:
        raw = self._raw_url(url)
        try:
            parsed = urlsplit(raw)
            scheme = parsed.scheme.lower()
            port = parsed.port
        except (TypeError, ValueError) as error:
            raise UnsafeTarget("target URL is malformed") from error
        if scheme not in {"http", "https"}:
            raise UnsafeTarget("only HTTP(S) targets are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeTarget("target URL userinfo is not allowed")
        if not parsed.netloc or parsed.hostname is None:
            raise UnsafeTarget("target URL has no hostname")

        hostname = _normalize_hostname(parsed.hostname)
        port = port or (443 if scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise UnsafeTarget("target URL port is out of range")
        addresses = self._resolve(hostname, port)
        for address in addresses:
            self._validate_address(hostname, address)

        normalized_host = hostname
        try:
            if ipaddress.ip_address(hostname).version == 6:
                normalized_host = f"[{hostname}]"
        except ValueError:
            pass
        default_port = 443 if scheme == "https" else 80
        authority = (
            normalized_host
            if port == default_port
            else f"{normalized_host}:{port}"
        )
        normalized_url = urlunsplit(
            (scheme, authority, parsed.path or "/", parsed.query, "")
        )
        return ApprovedTarget(
            normalized_url=normalized_url,
            scheme=scheme,
            hostname=hostname,
            port=port,
            addresses=addresses,
        )

    @staticmethod
    def _raw_url(url) -> str:
        if not isinstance(url, str):
            raise UnsafeTarget("target URL must be text")
        raw = url.strip()
        if (
            not raw
            or len(raw) > MAX_URL_LENGTH
            or "\\" in raw
            or any(character.isspace() or ord(character) < 32 for character in raw)
        ):
            raise UnsafeTarget("target URL contains invalid characters")
        return raw

    def _resolve(self, hostname, port) -> tuple[str, ...]:
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            return (literal.compressed.lower(),)
        try:
            records = self._resolver(
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except (OSError, TypeError, ValueError) as error:
            raise UnsafeTarget("target hostname could not be resolved") from error

        addresses = []
        for record in records or ():
            try:
                address = ipaddress.ip_address(record[4][0])
            except (IndexError, TypeError, ValueError):
                continue
            normalized = address.compressed.lower()
            if normalized not in addresses:
                addresses.append(normalized)
        if not addresses:
            raise UnsafeTarget("target hostname returned no usable addresses")
        return tuple(addresses)

    def _validate_address(self, hostname, address) -> None:
        try:
            candidate = ipaddress.ip_address(address)
        except ValueError as error:
            raise UnsafeTarget("target resolved to an invalid address") from error
        if isinstance(candidate, ipaddress.IPv6Address) and candidate.ipv4_mapped:
            raise UnsafeTarget("IPv4-mapped IPv6 targets are not allowed")
        if any(candidate in network for network in _METADATA_NETWORKS):
            raise UnsafeTarget("cloud metadata targets are not allowed")
        if candidate.is_unspecified or candidate.is_multicast or candidate.is_reserved:
            raise UnsafeTarget(f"target resolved to non-public address {candidate}")
        if candidate.is_global:
            return
        explicitly_allowed = (
            hostname in self.allowed_private_hosts
            or any(candidate in network for network in self.allowed_private_networks)
        )
        if (
            explicitly_allowed
            and not candidate.is_unspecified
            and not candidate.is_multicast
        ):
            return
        raise UnsafeTarget(f"target resolved to non-public address {candidate}")

    @staticmethod
    def _allowlisted_hosts(values):
        if isinstance(values, str):
            values = (values,)
        values = tuple(values or ())
        if len(values) > MAX_ALLOWLIST_ENTRIES:
            raise ValueError("too many browser host allowlist entries")
        for value in values:
            yield _normalize_hostname(str(value))

    @staticmethod
    def _allowlisted_networks(values):
        if isinstance(values, str):
            values = (values,)
        values = tuple(values or ())
        if len(values) > MAX_ALLOWLIST_ENTRIES:
            raise ValueError("too many browser CIDR allowlist entries")
        for value in values:
            try:
                network = ipaddress.ip_network(str(value).strip(), strict=True)
            except ValueError as error:
                raise ValueError(f"invalid browser CIDR allowlist entry: {value}") from error
            if not any(
                network.version == private.version and network.subnet_of(private)
                for private in _ALLOWLISTABLE_PRIVATE_NETWORKS
            ):
                raise ValueError(
                    f"browser CIDR allowlist must be a private network: {value}"
                )
            yield network


def _normalize_hostname(value) -> str:
    raw = str(value or "").strip().rstrip(".").lower()
    if not raw or "%" in raw or len(raw) > 253:
        raise UnsafeTarget("target hostname is malformed")
    try:
        return ipaddress.ip_address(raw).compressed.lower()
    except ValueError:
        pass
    try:
        normalized = raw.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as error:
        raise UnsafeTarget("target hostname is malformed") from error
    labels = normalized.split(".")
    if any(not _DNS_LABEL.fullmatch(label) for label in labels):
        raise UnsafeTarget("target hostname is malformed")
    return normalized


def _environment_items(name):
    raw = os.getenv(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


_DEFAULT_POLICY = None
_DEFAULT_POLICY_LOCK = threading.Lock()


def get_ssrf_policy() -> SSRFPolicy:
    global _DEFAULT_POLICY

    policy = _DEFAULT_POLICY
    if policy is None:
        with _DEFAULT_POLICY_LOCK:
            if _DEFAULT_POLICY is None:
                _DEFAULT_POLICY = SSRFPolicy.from_environment()
            policy = _DEFAULT_POLICY
    return policy


def validate_browser_target(url) -> ApprovedTarget:
    return get_ssrf_policy().resolve_and_validate(url)
