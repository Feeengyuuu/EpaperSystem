"""Validate the resolved Compose model used by isolated preflight and restore drills."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys


WEB_ENV_KEYS = {
    "PUBLICATION_ROOT",
    "WEB_PORTAL_CREDENTIAL_ROOT",
    "WEB_PORTAL_PUBLICATION_HEALTH_CACHE_SECONDS",
    "WEB_PORTAL_PUBLICATIONS_FACTORY",
    "WEB_PORTAL_SECRET_KEY_FILE",
    "WEB_PORTAL_TIMEZONE",
    "WEB_PORTAL_TRUSTED_HOSTS",
    "WEB_PORTAL_TRUST_PROXY_HEADERS",
    "WEB_PORTAL_WORKER_STATE_FILE",
    "WEB_PORTAL_WORKER_STATE_MAX_AGE_SECONDS",
}
WORKER_ENV_KEYS = {
    "PUBLICATION_ROOT",
    "WEB_CATALOG_RETENTION",
    "WEB_CONFIG",
    "WEB_DATA_ROOT",
    "WEB_EDITION_RETENTION_PER_INSTANCE",
    "WEB_PORTAL_WORKER_FACTORY",
    "WEB_PORTAL_WORKER_HEARTBEAT_FILE",
    "WEB_PORTAL_WORKER_HEARTBEAT_MAX_AGE_SECONDS",
    "WEB_SECRET_DIR",
    "WEB_WORKER_DEADLINE_SECONDS",
    "XDG_CACHE_HOME",
}
CADDY_IMAGE = "caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
ADMIN_COMMAND = "\n".join(
    (
        "from getpass import getpass",
        "from security.credentials import CredentialStore",
        "import os",
        'store = CredentialStore(os.environ["WEB_PORTAL_CREDENTIAL_ROOT"])',
        "if store.has_admin():",
        '    raise SystemExit("admin is already configured")',
        'first = getpass("New admin password: ")',
        'second = getpass("Repeat password: ")',
        "if first != second or len(first) < 12:",
        '    raise SystemExit("passwords differ or are shorter than 12 characters")',
        "token = store.create_bootstrap_token()",
        "store.consume_bootstrap_token(token, first)",
        'print("Administrator password configured.")',
        "",
    )
)
CADDY_HEALTHCHECK_TEST = [
    "CMD-SHELL",
    "wget -q -O /dev/null http://127.0.0.1:2019/config/",
]
WEB_HEALTHCHECK_TEST = [
    "CMD",
    "python",
    "-c",
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/livez', timeout=3).read()",
]
WORKER_HEALTHCHECK_TEST = [
    "CMD",
    "python",
    "-c",
    "import os; from web_portal.worker import heartbeat_is_fresh; "
    "assert heartbeat_is_fresh(os.environ['WEB_PORTAL_WORKER_HEARTBEAT_FILE'], "
    "max_age_seconds=float(os.environ['WEB_PORTAL_WORKER_HEARTBEAT_MAX_AGE_SECONDS']))",
]
SERVICE_KEYS = {
    "admin": {
        "profiles",
        "build",
        "cap_drop",
        "command",
        "entrypoint",
        "environment",
        "image",
        "init",
        "network_mode",
        "read_only",
        "restart",
        "security_opt",
        "stdin_open",
        "tmpfs",
        "tty",
        "user",
        "volumes",
    },
    "caddy": {
        "cap_add",
        "cap_drop",
        "cpus",
        "command",
        "depends_on",
        "entrypoint",
        "environment",
        "healthcheck",
        "image",
        "logging",
        "mem_limit",
        "networks",
        "pids_limit",
        "ports",
        "read_only",
        "restart",
        "security_opt",
        "tmpfs",
        "volumes",
    },
    "web": {
        "build",
        "cap_drop",
        "cpus",
        "command",
        "entrypoint",
        "environment",
        "expose",
        "healthcheck",
        "image",
        "init",
        "logging",
        "mem_limit",
        "networks",
        "pids_limit",
        "read_only",
        "restart",
        "security_opt",
        "tmpfs",
        "user",
        "volumes",
    },
    "worker": {
        "build",
        "cap_drop",
        "cpus",
        "command",
        "entrypoint",
        "environment",
        "healthcheck",
        "image",
        "init",
        "logging",
        "mem_limit",
        "networks",
        "pids_limit",
        "read_only",
        "restart",
        "security_opt",
        "stop_grace_period",
        "tmpfs",
        "user",
        "volumes",
    },
}


def _source(root: Path, relative: str) -> str:
    return os.path.normpath(os.path.abspath(root / relative))


def _environment(service: dict, expected_keys: set[str]) -> dict[str, str]:
    environment = service.get("environment")
    if not isinstance(environment, dict) or set(environment) != expected_keys:
        raise ValueError("resolved service environment does not match the supported contract")
    result = {}
    for key, value in environment.items():
        if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("resolved service environment contains an unsafe value")
        result[key] = value
    return result


def _safe_mount_text(value: object) -> str:
    if not isinstance(value, str) or not value or any(character in value for character in "\x00\r\n"):
        raise ValueError("resolved mount contains an unsafe path")
    return value


def _mounts(service: dict) -> dict[str, tuple[str, str, bool]]:
    result = {}
    for volume in service.get("volumes", []):
        if not isinstance(volume, dict) or volume.get("type") not in {"bind", "volume"}:
            raise ValueError("resolved mount type is unsupported")
        mount_type = volume["type"]
        expected_keys = {"type", "source", "target", "read_only", mount_type}
        if set(volume) - expected_keys:
            raise ValueError("resolved mount contains unsupported options")
        options = volume.get(mount_type, {})
        if not isinstance(options, dict):
            raise ValueError("resolved mount options are invalid")
        if mount_type == "bind":
            if set(options) - {"create_host_path"} or options.get("create_host_path") not in {
                None,
                False,
            }:
                raise ValueError("resolved bind options are invalid")
        elif options:
            raise ValueError("resolved volume options are invalid")
        target = _safe_mount_text(volume.get("target"))
        source = _safe_mount_text(volume.get("source"))
        if target in result:
            raise ValueError("resolved bind mount is invalid or duplicated")
        normalized_source = os.path.normpath(source) if mount_type == "bind" else source
        result[target] = (mount_type, normalized_source, bool(volume.get("read_only", False)))
    return result


def _size_bytes(value: object) -> int:
    if isinstance(value, int) and value > 0:
        return value
    if not isinstance(value, str):
        raise ValueError("resolved tmpfs size is invalid")
    match = re.fullmatch(r"([1-9][0-9]*)([kmgt]?)(?:i?b)?", value.strip().casefold())
    if not match:
        raise ValueError("resolved tmpfs size is invalid")
    exponent = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4}[match.group(2)]
    return int(match.group(1)) * (1024**exponent)


def _mode_bits(value: object) -> int:
    if isinstance(value, int) and value == 0o1777:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-7]{3,4}", value):
        return int(value, 8)
    raise ValueError("resolved tmpfs mode is invalid")


def _tmpfs(service: dict) -> dict[str, tuple[int, int]]:
    result = {}
    entries = service.get("tmpfs", [])
    if not isinstance(entries, list):
        raise ValueError("resolved tmpfs contract is invalid")
    for entry in entries:
        if isinstance(entry, str):
            target, separator, raw_options = entry.partition(":")
            options = {}
            if separator:
                for item in raw_options.split(","):
                    key, equals, value = item.partition("=")
                    if not equals or key in options:
                        raise ValueError("resolved tmpfs options are invalid")
                    options[key] = value
            if set(options) != {"size", "mode"}:
                raise ValueError("resolved tmpfs options are incomplete")
            size = _size_bytes(options["size"])
            mode = _mode_bits(options["mode"])
        elif isinstance(entry, dict):
            target = entry.get("target")
            if set(entry) - {"target", "size", "mode"} or not {"target", "size", "mode"}.issubset(entry):
                raise ValueError("resolved tmpfs options are invalid")
            size = _size_bytes(entry["size"])
            mode = _mode_bits(entry["mode"])
        else:
            raise ValueError("resolved tmpfs entry is invalid")
        target = _safe_mount_text(target)
        if target in result:
            raise ValueError("resolved tmpfs target is duplicated")
        result[target] = (size, mode)
    return result


def _service_networks(service: dict) -> set[str]:
    networks = service.get("networks", {})
    if isinstance(networks, dict):
        if any(value not in (None, {}) for value in networks.values()):
            raise ValueError("resolved service network options are invalid")
        return set(networks)
    if isinstance(networks, list) and all(isinstance(item, str) for item in networks):
        return set(networks)
    raise ValueError("resolved service network contract is invalid")


def _validate_build(service: dict, expected_root: Path, revision: str) -> None:
    build = service.get("build")
    if not isinstance(build, dict) or set(build) != {"context", "dockerfile", "args"}:
        raise ValueError("resolved Python image build contract is invalid")
    args = build.get("args")
    if (
        os.path.normpath(str(build.get("context")))
        != os.path.normpath(os.path.abspath(expected_root.parent.parent))
        or build.get("dockerfile") != "deploy/web/Dockerfile"
        or args != {"WEB_PORTAL_GIT_REVISION": revision}
    ):
        raise ValueError("resolved Python image build contract is invalid")


def _validate_healthcheck(service: dict, test: list[str], start_period: str) -> None:
    expected = {
        "test": test,
        "timeout": "5s",
        "interval": "30s",
        "retries": 3,
        "start_period": start_period,
    }
    if service.get("healthcheck") != expected:
        raise ValueError("resolved service healthcheck contract is invalid")


def _validate_logging(service: dict) -> None:
    if service.get("logging") != {
        "driver": "json-file",
        "options": {"max-file": "3", "max-size": "10m"},
    }:
        raise ValueError("resolved service logging contract is invalid")


def _validate_service_contract(services: dict, expected_root: Path) -> None:
    for service_name, expected_keys in SERVICE_KEYS.items():
        service = services[service_name]
        if not isinstance(service, dict) or set(service) != expected_keys:
            raise ValueError(f"resolved {service_name} service contract contains unsupported fields")

    caddy = services["caddy"]
    web = services["web"]
    worker = services["worker"]
    admin = services["admin"]
    web_image = web.get("image")
    if not isinstance(web_image, str) or ":" not in web_image:
        raise ValueError("resolved web image contract is invalid")
    revision = web_image.rsplit(":", 1)[1]
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise ValueError("resolved web image revision is invalid")
    if worker.get("image") != web_image or admin.get("image") != web_image:
        raise ValueError("resolved Python service images do not match")
    for service in (web, worker, admin):
        _validate_build(service, expected_root, revision)

    if caddy.get("image") != CADDY_IMAGE or caddy.get("command") is not None \
            or caddy.get("entrypoint") is not None:
        raise ValueError("resolved caddy image execution contract is invalid")
    if web.get("command") != ["python", "-m", "web_portal"] or web.get("entrypoint") is not None:
        raise ValueError("resolved web execution contract is invalid")
    if worker.get("command") != ["python", "-m", "web_portal.worker"] \
            or worker.get("entrypoint") is not None:
        raise ValueError("resolved worker execution contract is invalid")
    if admin.get("command") != ["python", "-c", ADMIN_COMMAND] or admin.get("entrypoint") is not None:
        raise ValueError("resolved admin execution contract is invalid")

    for service_name in ("web", "worker", "admin"):
        service = services[service_name]
        if (
            service.get("user") != "10001:10001"
            or service.get("init") is not True
            or service.get("cap_drop") != ["ALL"]
            or service.get("security_opt") != ["no-new-privileges:true"]
            or service.get("read_only") is not True
        ):
            raise ValueError(f"resolved {service_name} privilege contract is invalid")
    if (
        caddy.get("cap_add") != ["NET_BIND_SERVICE"]
        or caddy.get("cap_drop") != ["ALL"]
        or caddy.get("security_opt") != ["no-new-privileges:true"]
        or caddy.get("read_only") is not True
    ):
        raise ValueError("resolved caddy privilege contract is invalid")

    expected_ports = {
        (80, "80", "tcp", "ingress"),
        (443, "443", "tcp", "ingress"),
        (443, "443", "udp", "ingress"),
    }
    ports = caddy.get("ports")
    if not isinstance(ports, list) or any(
        not isinstance(port, dict)
        or set(port) != {"target", "published", "protocol", "mode"}
        for port in ports
    ):
        raise ValueError("resolved caddy port contract is invalid")
    actual_ports = {
        (port["target"], str(port["published"]), port["protocol"], port["mode"])
        for port in ports
    }
    if actual_ports != expected_ports or web.get("expose") != ["8080"]:
        raise ValueError("resolved public port contract is invalid")

    if (
        caddy.get("restart") != "unless-stopped"
        or web.get("restart") != "unless-stopped"
        or worker.get("restart") != "unless-stopped"
        or admin.get("restart") != "no"
        or worker.get("stop_grace_period") != "3m0s"
    ):
        raise ValueError("resolved lifecycle contract is invalid")
    if admin.get("profiles") != ["admin"] or admin.get("stdin_open") is not True \
            or admin.get("tty") is not True:
        raise ValueError("resolved admin profile contract is invalid")
    if caddy.get("depends_on") != {"web": {"condition": "service_healthy", "required": True}}:
        raise ValueError("resolved caddy dependency contract is invalid")

    _validate_healthcheck(caddy, CADDY_HEALTHCHECK_TEST, "10s")
    _validate_healthcheck(web, WEB_HEALTHCHECK_TEST, "20s")
    _validate_healthcheck(worker, WORKER_HEALTHCHECK_TEST, "3m0s")
    for service in (caddy, web, worker):
        _validate_logging(service)
    resources = {
        "caddy": (0.25, 268435456, 128),
        "web": (0.75, 805306368, 256),
        "worker": (1.25, 2147483648, 256),
    }
    for service_name, (cpus, memory, pids) in resources.items():
        service = services[service_name]
        try:
            actual_memory = int(service.get("mem_limit"))
            actual_cpus = float(service.get("cpus"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"resolved {service_name} resource contract is invalid") from error
        if actual_cpus != cpus or actual_memory != memory or service.get("pids_limit") != pids:
            raise ValueError(f"resolved {service_name} resource contract is invalid")


def validate_contract(document: object, expected_root: Path) -> dict[str, dict[str, str]]:
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise ValueError("resolved Compose document is invalid")
    if set(document) != {
        "name",
        "networks",
        "services",
        "volumes",
        "x-default-logging",
        "x-python-service",
    } \
            or document.get("name") != "epapersystem-web":
        raise ValueError("resolved Compose top-level contract is not exact")
    services = document["services"]
    if set(services) != {"admin", "caddy", "web", "worker"}:
        raise ValueError("resolved Compose service set is not exact")
    volumes = document.get("volumes")
    if not isinstance(volumes, dict) or set(volumes) != {"caddy_config", "caddy_data"}:
        raise ValueError("resolved Compose volume set is not exact")
    for volume_name, volume in volumes.items():
        if (
            not isinstance(volume, dict)
            or set(volume) != {"name"}
            or volume.get("name") != f"epapersystem-web_{volume_name}"
        ):
            raise ValueError("resolved Compose volume contract is invalid")
    networks = document.get("networks")
    if not isinstance(networks, dict) or set(networks) != {"edge", "portal", "worker_egress"}:
        raise ValueError("resolved Compose network set is not exact")
    for network_name, network in networks.items():
        expected_network_keys = {"name", "ipam"}
        if network_name == "portal":
            expected_network_keys.add("internal")
        if not isinstance(network, dict) or set(network) != expected_network_keys:
            raise ValueError("resolved Compose network contract is invalid")
        expected_name = f"epapersystem-web_{network_name}"
        if network.get("name") != expected_name or network.get("ipam") != {}:
            raise ValueError("resolved Compose network contract is invalid")
        if bool(network.get("internal", False)) != (network_name == "portal"):
            raise ValueError("resolved Compose network isolation is invalid")
    expected_service_networks = {
        "caddy": {"edge", "portal"},
        "web": {"portal"},
        "worker": {"worker_egress"},
    }
    for service_name, expected in expected_service_networks.items():
        if _service_networks(services[service_name]) != expected or services[service_name].get(
            "network_mode"
        ) not in {None, ""}:
            raise ValueError(f"resolved {service_name} network contract is invalid")
    if services["admin"].get("network_mode") != "none" or services["admin"].get("networks"):
        raise ValueError("resolved admin network contract is invalid")
    _validate_service_contract(services, expected_root)
    web_image = services["web"]["image"]
    revision = web_image.rsplit(":", 1)[1]
    if document.get("x-default-logging") != {
        "driver": "json-file",
        "options": {"max-file": "3", "max-size": "10m"},
    } or document.get("x-python-service") != {
        "build": {
            "args": {"WEB_PORTAL_GIT_REVISION": revision},
            "context": "../..",
            "dockerfile": "deploy/web/Dockerfile",
        },
        "cap_drop": ["ALL"],
        "image": web_image,
        "init": True,
        "read_only": True,
        "restart": "unless-stopped",
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:size=128m,mode=1777"],
        "user": "10001:10001",
    }:
        raise ValueError("resolved Compose extension contract is invalid")

    expected_mounts = {
        "caddy": {
            "/etc/caddy/Caddyfile": ("bind", _source(expected_root, "Caddyfile"), True),
            "/data": ("volume", "caddy_data", False),
            "/config": ("volume", "caddy_config", False),
        },
        "web": {
            "/app/publication": ("bind", _source(expected_root, "publication"), True),
            "/app/security": ("bind", _source(expected_root, "security"), True),
            "/app/worker-health": ("bind", _source(expected_root, "worker-data/health"), True),
            "/run/session-key/web_portal_secret_key": (
                "bind",
                _source(expected_root, "session-key/web_portal_secret_key"),
                True,
            ),
        },
        "worker": {
            "/app/config": ("bind", _source(expected_root, "config"), True),
            "/app/publication": ("bind", _source(expected_root, "publication"), False),
            "/app/worker-data": ("bind", _source(expected_root, "worker-data"), False),
            "/run/provider-secrets": ("bind", _source(expected_root, "provider-secrets"), True),
        },
        "admin": {
            "/app/security": ("bind", _source(expected_root, "security"), False),
        },
    }
    for service_name, expected in expected_mounts.items():
        actual = _mounts(services[service_name])
        if actual != expected:
            raise ValueError(f"resolved {service_name} mount violates the canonical layout")

    expected_tmpfs = {
        "caddy": {"/tmp": (64 * 1024 * 1024, 0o1777)},
        "web": {"/tmp": (128 * 1024 * 1024, 0o1777)},
        "worker": {"/tmp": (128 * 1024 * 1024, 0o1777)},
        "admin": {"/tmp": (128 * 1024 * 1024, 0o1777)},
    }
    for service_name, expected in expected_tmpfs.items():
        if _tmpfs(services[service_name]) != expected:
            raise ValueError(f"resolved {service_name} tmpfs violates the canonical layout")

    caddy = _environment(services["caddy"], {"WEB_PORTAL_HOSTNAME"})
    web = _environment(services["web"], WEB_ENV_KEYS)
    worker = _environment(services["worker"], WORKER_ENV_KEYS)
    admin = _environment(services["admin"], {"WEB_PORTAL_CREDENTIAL_ROOT"})

    if not re.fullmatch(r"[A-Za-z0-9.-]+", caddy["WEB_PORTAL_HOSTNAME"]):
        raise ValueError("resolved hostname is invalid")
    fixed_web = {
        "PUBLICATION_ROOT": "/app/publication",
        "WEB_PORTAL_CREDENTIAL_ROOT": "/app/security",
        "WEB_PORTAL_PUBLICATIONS_FACTORY": "web_portal.factories:create_reader",
        "WEB_PORTAL_SECRET_KEY_FILE": "/run/session-key/web_portal_secret_key",
        "WEB_PORTAL_TRUST_PROXY_HEADERS": "true",
        "WEB_PORTAL_WORKER_STATE_FILE": "/app/worker-health/worker-state.json",
        "WEB_PORTAL_WORKER_STATE_MAX_AGE_SECONDS": "300",
    }
    fixed_worker = {
        "PUBLICATION_ROOT": "/app/publication",
        "WEB_CONFIG": "/app/config/web_config.json",
        "WEB_DATA_ROOT": "/app/worker-data",
        "WEB_PORTAL_WORKER_FACTORY": "web_portal.factories:create_worker",
        "WEB_PORTAL_WORKER_HEARTBEAT_FILE": "/app/worker-data/health/worker-state.json",
        "WEB_PORTAL_WORKER_HEARTBEAT_MAX_AGE_SECONDS": "300",
        "WEB_SECRET_DIR": "/run/provider-secrets",
        "XDG_CACHE_HOME": "/app/worker-data/cache/xdg",
    }
    if any(web.get(key) != value for key, value in fixed_web.items()):
        raise ValueError("resolved web environment overrides a fixed runtime seam")
    if any(worker.get(key) != value for key, value in fixed_worker.items()):
        raise ValueError("resolved worker environment overrides a fixed runtime seam")
    if admin != {"WEB_PORTAL_CREDENTIAL_ROOT": "/app/security"}:
        raise ValueError("resolved admin environment is invalid")
    return {"admin": admin, "caddy": caddy, "web": web, "worker": worker}


def _environment_map(entries: object, context: str) -> dict[str, str]:
    if not isinstance(entries, list):
        raise ValueError(f"{context} environment is invalid")
    result = {}
    for entry in entries:
        if not isinstance(entry, str) or "=" not in entry or any(
            character in entry for character in "\x00\r\n"
        ):
            raise ValueError(f"{context} environment is invalid")
        key, value = entry.split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in result:
            raise ValueError(f"{context} environment key is invalid: {key}")
        result[key] = value
    return result


def _environment_drift(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    return sorted(
        key
        for key in set(expected) | set(actual)
        if key not in expected or key not in actual or expected[key] != actual[key]
    )


def _runtime_mounts(
    service_name: str,
    container: dict,
    expected: dict[str, tuple[str, str, bool]],
    expected_tmpfs: dict[str, tuple[int, int]],
) -> None:
    mounts = container.get("Mounts")
    if not isinstance(mounts, list):
        raise ValueError(f"running {service_name} mount contract is invalid")
    actual = {}
    actual_tmpfs = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            raise ValueError(f"running {service_name} mount contract is invalid")
        mount_type = mount.get("Type")
        destination = mount.get("Destination")
        if not isinstance(destination, str) or not destination or destination in actual:
            raise ValueError(f"running {service_name} mount contract is invalid")
        if mount_type == "tmpfs":
            if destination not in expected_tmpfs or mount.get("RW") is not True:
                raise ValueError(f"running {service_name} mount contract does not match")
            actual_tmpfs.add(destination)
            continue
        if mount_type not in {"bind", "volume"}:
            raise ValueError(f"running {service_name} mount contract does not match")
        source = mount.get("Source")
        if not isinstance(source, str) or any(character in source for character in "\x00\r\n"):
            raise ValueError(f"running {service_name} mount contract is invalid")
        actual[destination] = mount

    if set(actual) != set(expected) or not actual_tmpfs.issubset(expected_tmpfs):
        raise ValueError(f"running {service_name} mount contract does not match")
    for destination, (mount_type, source, read_only) in expected.items():
        mount = actual[destination]
        if mount.get("Type") != mount_type or mount.get("RW") is not (not read_only):
            raise ValueError(f"running {service_name} mount contract does not match")
        if mount_type == "bind":
            if (
                os.path.normpath(mount["Source"]) != source
                or mount.get("Propagation") != "rprivate"
            ):
                raise ValueError(f"running {service_name} mount contract does not match")
        else:
            expected_name = f"epapersystem-web_{source}"
            if mount.get("Name") != expected_name or mount.get("Propagation", "") not in {"", None}:
                raise ValueError(f"running {service_name} mount contract does not match")


def _runtime_tmpfs(host_config: dict, service_name: str) -> dict[str, tuple[int, int]]:
    entries = host_config.get("Tmpfs")
    if not isinstance(entries, dict):
        raise ValueError(f"running {service_name} tmpfs contract is invalid")
    result = {}
    for target, raw_options in entries.items():
        if not isinstance(target, str) or not isinstance(raw_options, str):
            raise ValueError(f"running {service_name} tmpfs contract is invalid")
        options = {}
        for item in raw_options.split(","):
            if item == "rw":
                continue
            key, equals, value = item.partition("=")
            if not equals or key in options:
                raise ValueError(f"running {service_name} tmpfs contract is invalid")
            options[key] = value
        if set(options) != {"size", "mode"}:
            raise ValueError(f"running {service_name} tmpfs contract is invalid")
        result[target] = (_size_bytes(options["size"]), _mode_bits(options["mode"]))
    return result


def _validate_runtime_host(service_name: str, service: dict, container: dict) -> None:
    state = container.get("State")
    if not isinstance(state, dict) or (
        state.get("Running") is not True
        or state.get("Paused") is not False
        or state.get("Restarting") is not False
        or state.get("Dead") is not False
        or not isinstance(state.get("Health"), dict)
        or state["Health"].get("Status") != "healthy"
    ):
        raise ValueError(f"running {service_name} state contract does not match")
    host_config = container.get("HostConfig")
    if not isinstance(host_config, dict):
        raise ValueError(f"running {service_name} host contract is invalid")
    actual_cap_add = host_config.get("CapAdd")
    actual_cap_drop = host_config.get("CapDrop")
    if actual_cap_add is None:
        actual_cap_add = []
    if actual_cap_drop is None:
        actual_cap_drop = []
    logging = service.get("logging")
    expected_log_config = {
        "Type": logging.get("driver"),
        "Config": logging.get("options"),
    } if isinstance(logging, dict) else None
    if (
        host_config.get("ReadonlyRootfs") is not service.get("read_only", False)
        or host_config.get("Privileged") is not False
        or not isinstance(actual_cap_add, list)
        or actual_cap_add != service.get("cap_add", [])
        or not isinstance(actual_cap_drop, list)
        or actual_cap_drop != service.get("cap_drop", [])
        or host_config.get("SecurityOpt") != service.get("security_opt", [])
        or bool(host_config.get("Init", False)) is not bool(service.get("init", False))
        or host_config.get("LogConfig") != expected_log_config
    ):
        raise ValueError(f"running {service_name} host security contract does not match")
    expected_port_bindings: dict[str, list[dict[str, str]]] = {}
    for port in service.get("ports", []):
        port_key = f"{port['target']}/{port['protocol']}"
        expected_port_bindings.setdefault(port_key, []).append(
            {"HostIp": "", "HostPort": str(port["published"])}
        )
    if host_config.get("PortBindings") != expected_port_bindings:
        raise ValueError(f"running {service_name} port binding contract does not match")
    if host_config.get("RestartPolicy") != {
        "Name": "unless-stopped",
        "MaximumRetryCount": 0,
    }:
        raise ValueError(f"running {service_name} restart contract does not match")
    try:
        expected_memory = int(service["mem_limit"])
        expected_nano_cpus = int(float(service["cpus"]) * 1_000_000_000)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"resolved {service_name} resource contract is invalid") from error
    if (
        host_config.get("Memory") != expected_memory
        or host_config.get("NanoCpus") != expected_nano_cpus
        or host_config.get("PidsLimit") != service.get("pids_limit")
    ):
        raise ValueError(f"running {service_name} resource contract does not match")
    if _runtime_tmpfs(host_config, service_name) != _tmpfs(service):
        raise ValueError(f"running {service_name} tmpfs contract does not match")


def _runtime_argv(value: object) -> bool:
    return value is None or (
        isinstance(value, list) and all(isinstance(argument, str) for argument in value)
    )


def _validate_runtime_execution(
    service_name: str,
    service: dict,
    config: dict,
    image_config: dict,
) -> None:
    expected_user = service.get("user", image_config.get("User"))
    expected_command = (
        service["command"] if service.get("command") is not None else image_config.get("Cmd")
    )
    expected_entrypoint = (
        service["entrypoint"]
        if service.get("entrypoint") is not None
        else image_config.get("Entrypoint")
    )
    if (
        not isinstance(expected_user, str)
        or not _runtime_argv(expected_command)
        or not _runtime_argv(expected_entrypoint)
        or not isinstance(config.get("User"), str)
        or not _runtime_argv(config.get("Cmd"))
        or not _runtime_argv(config.get("Entrypoint"))
        or config.get("User") != expected_user
        or config.get("Cmd") != expected_command
        or config.get("Entrypoint") != expected_entrypoint
    ):
        raise ValueError(f"running {service_name} execution contract does not match")


def _runtime_duration_nanoseconds(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("resolved healthcheck duration is invalid")
    match = re.fullmatch(r"(?:(\d+)m)?(\d+)s", value)
    if match is None:
        raise ValueError("resolved healthcheck duration is invalid")
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2))
    return (minutes * 60 + seconds) * 1_000_000_000


def _validate_runtime_healthcheck(
    service_name: str,
    service: dict,
    config: dict,
) -> None:
    healthcheck = service.get("healthcheck")
    if not isinstance(healthcheck, dict):
        raise ValueError(f"resolved {service_name} healthcheck contract is invalid")
    try:
        expected = {
            "Test": healthcheck["test"],
            "Interval": _runtime_duration_nanoseconds(healthcheck["interval"]),
            "Timeout": _runtime_duration_nanoseconds(healthcheck["timeout"]),
            "Retries": healthcheck["retries"],
            "StartPeriod": _runtime_duration_nanoseconds(healthcheck["start_period"]),
        }
    except KeyError as error:
        raise ValueError(
            f"resolved {service_name} healthcheck contract is invalid"
        ) from error
    actual = config.get("Healthcheck")
    if not isinstance(actual, dict):
        raise ValueError(f"running {service_name} healthcheck contract does not match")
    if actual != expected:
        raise ValueError(f"running {service_name} healthcheck contract does not match")


def validate_runtime_contract(
    document: object,
    expected_root: Path,
    runtime: dict[str, dict[str, dict]],
) -> None:
    environments = validate_contract(document, expected_root)
    if set(runtime) != {"caddy", "web", "worker"}:
        raise ValueError("running service inspect set is not exact")
    services = document["services"]
    for service_name in ("caddy", "web", "worker"):
        entry = runtime.get(service_name)
        if not isinstance(entry, dict):
            raise ValueError(f"running {service_name} inspect data is invalid")
        container = entry.get("container")
        image = entry.get("image")
        if not isinstance(container, dict) or not isinstance(image, dict):
            raise ValueError(f"running {service_name} inspect data is invalid")
        image_id = image.get("Id")
        if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError(f"running {service_name} image identity is invalid")
        if container.get("Image") != image_id:
            raise ValueError(f"running {service_name} image identity does not match")
        _validate_runtime_host(service_name, services[service_name], container)

        config = container.get("Config")
        image_config = image.get("Config")
        if not isinstance(config, dict) or not isinstance(image_config, dict):
            raise ValueError(f"running {service_name} configuration is invalid")
        _validate_runtime_execution(
            service_name,
            services[service_name],
            config,
            image_config,
        )
        _validate_runtime_healthcheck(service_name, services[service_name], config)
        labels = config.get("Labels")
        if not isinstance(labels, dict) or labels.get("com.docker.compose.project") != "epapersystem-web" \
                or labels.get("com.docker.compose.service") != service_name:
            raise ValueError(f"running {service_name} Compose identity does not match")

        expected_environment = _environment_map(
            image_config.get("Env"), f"running {service_name} image"
        )
        expected_environment.update(environments[service_name])
        actual_environment = _environment_map(
            config.get("Env"), f"running {service_name} container"
        )
        for dynamic_key in ("HOME", "HOSTNAME", "PATH"):
            if dynamic_key not in expected_environment and dynamic_key in actual_environment:
                expected_environment[dynamic_key] = actual_environment[dynamic_key]
        drift = _environment_drift(expected_environment, actual_environment)
        if drift:
            raise ValueError(
                f"running {service_name} environment does not match resolved Compose contract: "
                + ",".join(drift)
            )
        _runtime_mounts(
            service_name,
            container,
            _mounts(services[service_name]),
            _tmpfs(services[service_name]),
        )
        network_settings = container.get("NetworkSettings")
        actual_networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
        if not isinstance(actual_networks, dict):
            raise ValueError(f"running {service_name} network contract is invalid")
        expected_networks = {
            document["networks"][network_name]["name"]
            for network_name in _service_networks(services[service_name])
        }
        if set(actual_networks) != expected_networks:
            raise ValueError(f"running {service_name} network contract does not match")


def _load_single_inspect(path: Path) -> dict:
    try:
        details = path.stat(follow_symlinks=False)
    except (OSError, TypeError) as error:
        raise ValueError("runtime inspect file is unavailable") from error
    if not stat.S_ISREG(details.st_mode) or details.st_size <= 0 or details.st_size > 4 * 1024 * 1024:
        raise ValueError("runtime inspect file is not a bounded regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("runtime inspect file is unreadable") from error
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        raise ValueError("runtime inspect document is invalid")
    return document[0]


def load_runtime_inspects(root: Path) -> dict[str, dict[str, dict]]:
    return {
        service_name: {
            kind: _load_single_inspect(root / f"{service_name}.{kind}.json")
            for kind in ("container", "image")
        }
        for service_name in ("caddy", "web", "worker")
    }


def write_env_files(environments: dict[str, dict[str, str]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for service_name, environment in environments.items():
        target = output_dir / f"{service_name}.env"
        body = "".join(f"{key}={environment[key]}\n" for key in sorted(environment))
        target.write_text(body, encoding="utf-8", newline="\n")
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--runtime-inspect-dir")
    options = parser.parse_args(argv)
    document = json.load(sys.stdin)
    environments = validate_contract(document, Path(options.expected_root))
    if options.runtime_inspect_dir:
        validate_runtime_contract(
            document,
            Path(options.expected_root),
            load_runtime_inspects(Path(options.runtime_inspect_dir)),
        )
    write_env_files(environments, Path(options.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
