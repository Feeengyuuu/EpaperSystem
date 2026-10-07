from pathlib import Path
import importlib.util
import re

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
DEPLOY_ROOT = REPO_ROOT / "deploy" / "web"


def _load_compose_contract():
    path = DEPLOY_ROOT / "ops" / "compose_contract.py"
    spec = importlib.util.spec_from_file_location("web_compose_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolved_contract_document(module, root):
    source = lambda relative: str(root / relative)
    revision = "c" * 40
    web_image = f"epapersystem-web:{revision}"
    build = {
        "context": str(root.parent.parent),
        "dockerfile": "deploy/web/Dockerfile",
        "args": {"WEB_PORTAL_GIT_REVISION": revision},
    }
    logging = {"driver": "json-file", "options": {"max-file": "3", "max-size": "10m"}}
    web_environment = {key: "value" for key in module.WEB_ENV_KEYS}
    web_environment.update(
        {
            "PUBLICATION_ROOT": "/app/publication",
            "WEB_PORTAL_CREDENTIAL_ROOT": "/app/security",
            "WEB_PORTAL_PUBLICATION_HEALTH_CACHE_SECONDS": "10",
            "WEB_PORTAL_PUBLICATIONS_FACTORY": "web_portal.factories:create_reader",
            "WEB_PORTAL_SECRET_KEY_FILE": "/run/session-key/web_portal_secret_key",
            "WEB_PORTAL_TIMEZONE": "America/Los_Angeles",
            "WEB_PORTAL_TRUSTED_HOSTS": "portal.example,127.0.0.1,localhost",
            "WEB_PORTAL_TRUST_PROXY_HEADERS": "true",
            "WEB_PORTAL_WORKER_STATE_FILE": "/app/worker-health/worker-state.json",
            "WEB_PORTAL_WORKER_STATE_MAX_AGE_SECONDS": "300",
        }
    )
    worker_environment = {key: "1" for key in module.WORKER_ENV_KEYS}
    worker_environment.update(
        {
            "PUBLICATION_ROOT": "/app/publication",
            "WEB_CONFIG": "/app/config/web_config.json",
            "WEB_DATA_ROOT": "/app/worker-data",
            "WEB_PORTAL_WORKER_FACTORY": "web_portal.factories:create_worker",
            "WEB_PORTAL_WORKER_HEARTBEAT_FILE": "/app/worker-data/health/worker-state.json",
            "WEB_PORTAL_WORKER_HEARTBEAT_MAX_AGE_SECONDS": "300",
            "WEB_SECRET_DIR": "/run/provider-secrets",
            "XDG_CACHE_HOME": "/app/worker-data/cache/xdg",
        }
    )

    def bind(relative, target, read_only):
        return {
            "type": "bind",
            "source": source(relative),
            "target": target,
            "read_only": read_only,
            "bind": {},
        }

    def volume(name, target):
        return {
            "type": "volume",
            "source": name,
            "target": target,
            "read_only": False,
            "volume": {},
        }

    return {
        "name": "epapersystem-web",
        "services": {
            "caddy": {
                "cap_add": ["NET_BIND_SERVICE"],
                "cap_drop": ["ALL"],
                "cpus": 0.25,
                "command": None,
                "depends_on": {"web": {"condition": "service_healthy", "required": True}},
                "entrypoint": None,
                "environment": {"WEB_PORTAL_HOSTNAME": "portal.example"},
                "healthcheck": {
                    "test": module.CADDY_HEALTHCHECK_TEST,
                    "timeout": "5s",
                    "interval": "30s",
                    "retries": 3,
                    "start_period": "10s",
                },
                "image": module.CADDY_IMAGE,
                "logging": logging,
                "mem_limit": "268435456",
                "networks": {"edge": None, "portal": None},
                "pids_limit": 128,
                "ports": [
                    {"mode": "ingress", "target": 80, "published": "80", "protocol": "tcp"},
                    {"mode": "ingress", "target": 443, "published": "443", "protocol": "tcp"},
                    {"mode": "ingress", "target": 443, "published": "443", "protocol": "udp"},
                ],
                "read_only": True,
                "restart": "unless-stopped",
                "security_opt": ["no-new-privileges:true"],
                "volumes": [
                    bind("Caddyfile", "/etc/caddy/Caddyfile", True),
                    volume("caddy_data", "/data"),
                    volume("caddy_config", "/config"),
                ],
                "tmpfs": ["/tmp:size=64m,mode=1777"],
            },
            "web": {
                "build": build,
                "cap_drop": ["ALL"],
                "cpus": 0.75,
                "command": ["python", "-m", "web_portal"],
                "entrypoint": None,
                "environment": web_environment,
                "expose": ["8080"],
                "healthcheck": {
                    "test": module.WEB_HEALTHCHECK_TEST,
                    "timeout": "5s",
                    "interval": "30s",
                    "retries": 3,
                    "start_period": "20s",
                },
                "image": web_image,
                "init": True,
                "logging": logging,
                "mem_limit": "805306368",
                "networks": {"portal": None},
                "pids_limit": 256,
                "read_only": True,
                "restart": "unless-stopped",
                "security_opt": ["no-new-privileges:true"],
                "volumes": [
                    bind("publication", "/app/publication", True),
                    bind("security", "/app/security", True),
                    bind("worker-data/health", "/app/worker-health", True),
                    bind(
                        "session-key/web_portal_secret_key",
                        "/run/session-key/web_portal_secret_key",
                        True,
                    ),
                ],
                "tmpfs": ["/tmp:size=128m,mode=1777"],
                "user": "10001:10001",
            },
            "worker": {
                "build": build,
                "cap_drop": ["ALL"],
                "cpus": 1.25,
                "command": ["python", "-m", "web_portal.worker"],
                "entrypoint": None,
                "environment": worker_environment,
                "healthcheck": {
                    "test": module.WORKER_HEALTHCHECK_TEST,
                    "timeout": "5s",
                    "interval": "30s",
                    "retries": 3,
                    "start_period": "3m0s",
                },
                "image": web_image,
                "init": True,
                "logging": logging,
                "mem_limit": "2147483648",
                "networks": {"worker_egress": None},
                "pids_limit": 256,
                "read_only": True,
                "restart": "unless-stopped",
                "security_opt": ["no-new-privileges:true"],
                "stop_grace_period": "3m0s",
                "volumes": [
                    bind("config", "/app/config", True),
                    bind("publication", "/app/publication", False),
                    bind("worker-data", "/app/worker-data", False),
                    bind("provider-secrets", "/run/provider-secrets", True),
                ],
                "tmpfs": ["/tmp:size=128m,mode=1777"],
                "user": "10001:10001",
            },
            "admin": {
                "profiles": ["admin"],
                "build": build,
                "cap_drop": ["ALL"],
                "command": ["python", "-c", module.ADMIN_COMMAND],
                "entrypoint": None,
                "environment": {"WEB_PORTAL_CREDENTIAL_ROOT": "/app/security"},
                "image": web_image,
                "init": True,
                "network_mode": "none",
                "read_only": True,
                "restart": "no",
                "security_opt": ["no-new-privileges:true"],
                "stdin_open": True,
                "volumes": [bind("security", "/app/security", False)],
                "tmpfs": ["/tmp:size=128m,mode=1777"],
                "tty": True,
                "user": "10001:10001",
            },
        },
        "volumes": {
            "caddy_data": {"name": "epapersystem-web_caddy_data"},
            "caddy_config": {"name": "epapersystem-web_caddy_config"},
        },
        "networks": {
            "edge": {"name": "epapersystem-web_edge", "ipam": {}},
            "portal": {"name": "epapersystem-web_portal", "ipam": {}, "internal": True},
            "worker_egress": {"name": "epapersystem-web_worker_egress", "ipam": {}},
        },
        "x-default-logging": logging,
        "x-python-service": {
            "build": {
                "context": "../..",
                "dockerfile": "deploy/web/Dockerfile",
                "args": {"WEB_PORTAL_GIT_REVISION": revision},
            },
            "cap_drop": ["ALL"],
            "image": web_image,
            "init": True,
            "read_only": True,
            "restart": "unless-stopped",
            "security_opt": ["no-new-privileges:true"],
            "tmpfs": ["/tmp:size=128m,mode=1777"],
            "user": "10001:10001",
        },
    }


def test_compose_contract_rejects_an_extra_bind_mount(tmp_path):
    contract = _load_compose_contract()
    document = _resolved_contract_document(contract, tmp_path)
    document["services"]["web"]["volumes"].append(
        {
            "type": "bind",
            "source": str(tmp_path / "unexpected-host-data"),
            "target": "/unexpected",
            "read_only": True,
        }
    )

    with pytest.raises(ValueError, match="canonical layout"):
        contract.validate_contract(document, tmp_path)


def test_compose_contract_rejects_wrong_mount_mode_factory_and_extra_environment(tmp_path):
    contract = _load_compose_contract()

    wrong_mode = _resolved_contract_document(contract, tmp_path)
    wrong_mode["services"]["worker"]["volumes"][1]["read_only"] = True
    with pytest.raises(ValueError, match="canonical layout"):
        contract.validate_contract(wrong_mode, tmp_path)

    wrong_factory = _resolved_contract_document(contract, tmp_path)
    wrong_factory["services"]["worker"]["environment"][
        "WEB_PORTAL_WORKER_FACTORY"
    ] = "untrusted.module:create_worker"
    with pytest.raises(ValueError, match="fixed runtime seam"):
        contract.validate_contract(wrong_factory, tmp_path)

    extra_environment = _resolved_contract_document(contract, tmp_path)
    extra_environment["services"]["web"]["environment"]["UNEXPECTED_SECRET"] = "value"
    with pytest.raises(ValueError, match="supported contract"):
        contract.validate_contract(extra_environment, tmp_path)


def test_compose_contract_rejects_extra_services_nonbind_mounts_and_tmpfs_drift(tmp_path):
    contract = _load_compose_contract()

    extra_service = _resolved_contract_document(contract, tmp_path)
    extra_service["services"]["debug"] = {"environment": {}, "volumes": []}
    with pytest.raises(ValueError, match="service set"):
        contract.validate_contract(extra_service, tmp_path)

    extra_volume = _resolved_contract_document(contract, tmp_path)
    extra_volume["services"]["web"]["volumes"].append(
        {
            "type": "volume",
            "source": "unexpected",
            "target": "/debug",
            "read_only": False,
        }
    )
    with pytest.raises(ValueError, match="canonical layout"):
        contract.validate_contract(extra_volume, tmp_path)

    wrong_tmpfs = _resolved_contract_document(contract, tmp_path)
    wrong_tmpfs["services"]["worker"]["tmpfs"] = ["/tmp:size=1g,mode=1777"]
    with pytest.raises(ValueError, match="tmpfs"):
        contract.validate_contract(wrong_tmpfs, tmp_path)

    extra_network = _resolved_contract_document(contract, tmp_path)
    extra_network["services"]["worker"]["networks"]["portal"] = None
    with pytest.raises(ValueError, match="network"):
        contract.validate_contract(extra_network, tmp_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document["services"]["caddy"]["ports"].append(
            {"mode": "ingress", "target": 2019, "published": "2019", "protocol": "tcp"}
        ),
        lambda document: document["services"]["web"].__setitem__("read_only", False),
        lambda document: document["services"]["worker"]["healthcheck"].__setitem__(
            "test", ["CMD", "true"]
        ),
        lambda document: document["services"]["worker"].__setitem__("mem_limit", "0"),
        lambda document: document["volumes"]["caddy_data"].__setitem__(
            "driver_opts", {"type": "none", "o": "bind", "device": "/tmp"}
        ),
    ],
)
def test_compose_contract_rejects_entrypoint_privilege_health_resource_and_storage_drift(
    tmp_path, mutate
):
    contract = _load_compose_contract()
    document = _resolved_contract_document(contract, tmp_path)
    mutate(document)

    with pytest.raises(ValueError, match="contract|security|volume"):
        contract.validate_contract(document, tmp_path)


def _runtime_contract_document(contract, compose_document):
    image_id = "sha256:" + ("a" * 64)
    image_environment = ["PATH=/usr/local/bin", "LANG=C.UTF-8"]
    runtime = {}
    for service_name in ("caddy", "web", "worker"):
        service = compose_document["services"][service_name]
        if service_name == "caddy":
            image_user = ""
            image_command = [
                "caddy",
                "run",
                "--config",
                "/etc/caddy/Caddyfile",
                "--adapter",
                "caddyfile",
            ]
            image_entrypoint = None
        else:
            image_user = "10001:10001"
            image_command = ["python", "-m", "web_portal"]
            image_entrypoint = None
        expected_environment = dict(
            item.split("=", 1) for item in image_environment
        )
        expected_environment.update(service["environment"])
        healthcheck = service["healthcheck"]
        healthcheck_duration_nanoseconds = {
            "5s": 5_000_000_000,
            "10s": 10_000_000_000,
            "20s": 20_000_000_000,
            "30s": 30_000_000_000,
            "3m0s": 180_000_000_000,
        }
        mounts = []
        for volume in service["volumes"]:
            if volume["type"] == "bind":
                mounts.append(
                    {
                        "Type": "bind",
                        "Source": volume["source"],
                        "Destination": volume["target"],
                        "RW": not volume.get("read_only", False),
                        "Propagation": "rprivate",
                    }
                )
            else:
                mounts.append(
                    {
                        "Type": "volume",
                        "Name": f"epapersystem-web_{volume['source']}",
                        "Source": f"/var/lib/docker/volumes/epapersystem-web_{volume['source']}/_data",
                        "Destination": volume["target"],
                        "RW": True,
                        "Propagation": "",
                    }
                )
        runtime[service_name] = {
            "image": {
                "Id": image_id,
                "Config": {
                    "Env": image_environment,
                    "User": image_user,
                    "Cmd": image_command,
                    "Entrypoint": image_entrypoint,
                },
            },
            "container": {
                "Image": image_id,
                "State": {
                    "Running": True,
                    "Paused": False,
                    "Restarting": False,
                    "Dead": False,
                    "Health": {"Status": "healthy"},
                },
                "Config": {
                    "Env": [f"{key}={value}" for key, value in expected_environment.items()],
                    "User": service.get("user", image_user),
                    "Cmd": service["command"] if service["command"] is not None else image_command,
                    "Entrypoint": (
                        service["entrypoint"]
                        if service["entrypoint"] is not None
                        else image_entrypoint
                    ),
                    "Healthcheck": {
                        "Test": list(healthcheck["test"]),
                        "Interval": healthcheck_duration_nanoseconds[
                            healthcheck["interval"]
                        ],
                        "Timeout": healthcheck_duration_nanoseconds[
                            healthcheck["timeout"]
                        ],
                        "Retries": healthcheck["retries"],
                        "StartPeriod": healthcheck_duration_nanoseconds[
                            healthcheck["start_period"]
                        ],
                    },
                    "Labels": {
                        "com.docker.compose.project": "epapersystem-web",
                        "com.docker.compose.service": service_name,
                    },
                },
                "HostConfig": {
                    "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                    "Memory": int(service["mem_limit"]),
                    "NanoCpus": int(float(service["cpus"]) * 1_000_000_000),
                    "PidsLimit": service["pids_limit"],
                    "ReadonlyRootfs": True,
                    "Privileged": False,
                    "CapDrop": ["ALL"],
                    "CapAdd": ["NET_BIND_SERVICE"] if service_name == "caddy" else None,
                    "SecurityOpt": ["no-new-privileges:true"],
                    "Init": True if service_name in {"web", "worker"} else None,
                    "LogConfig": {
                        "Type": "json-file",
                        "Config": {"max-file": "3", "max-size": "10m"},
                    },
                    "PortBindings": (
                        {
                            "80/tcp": [{"HostIp": "", "HostPort": "80"}],
                            "443/tcp": [{"HostIp": "", "HostPort": "443"}],
                            "443/udp": [{"HostIp": "", "HostPort": "443"}],
                        }
                        if service_name == "caddy"
                        else {}
                    ),
                    "Tmpfs": {
                        "/tmp": (
                            "rw,size=67108864,mode=1777"
                            if service_name == "caddy"
                            else "rw,size=134217728,mode=1777"
                        )
                    },
                },
                "Mounts": mounts,
                "NetworkSettings": {
                    "Networks": {
                        compose_document["networks"][name]["name"]: {}
                        for name in service.get("networks", {})
                    }
                },
            },
        }
    return runtime


def test_runtime_contract_rejects_writable_root_filesystem(tmp_path):
    contract = _load_compose_contract()
    compose_document = _resolved_contract_document(contract, tmp_path)
    runtime = _runtime_contract_document(contract, compose_document)
    runtime["web"]["container"]["HostConfig"]["ReadonlyRootfs"] = False

    with pytest.raises(ValueError, match="host security contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, runtime)


@pytest.mark.parametrize(
    ("service_name", "field", "drifted_value"),
    [
        ("worker", "Privileged", True),
        ("worker", "CapDrop", None),
        ("caddy", "CapAdd", None),
        ("web", "CapAdd", ["SYS_ADMIN"]),
        ("worker", "SecurityOpt", []),
        ("worker", "Init", False),
        ("caddy", "LogConfig", {"Type": "none", "Config": {}}),
    ],
)
def test_runtime_contract_rejects_host_security_drift(
    tmp_path, service_name, field, drifted_value
):
    contract = _load_compose_contract()
    compose_document = _resolved_contract_document(contract, tmp_path)
    runtime = _runtime_contract_document(contract, compose_document)
    runtime[service_name]["container"]["HostConfig"][field] = drifted_value

    with pytest.raises(ValueError, match="host security contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, runtime)


@pytest.mark.parametrize(
    ("service_name", "field", "drifted_value"),
    [
        ("web", "User", "0:0"),
        ("worker", "Cmd", ["sh", "-c", "sleep infinity"]),
        ("caddy", "Entrypoint", ["/bin/sh"]),
    ],
)
def test_runtime_contract_rejects_effective_execution_drift(
    tmp_path, service_name, field, drifted_value
):
    contract = _load_compose_contract()
    compose_document = _resolved_contract_document(contract, tmp_path)
    runtime = _runtime_contract_document(contract, compose_document)
    runtime[service_name]["container"]["Config"][field] = drifted_value

    with pytest.raises(ValueError, match="execution contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, runtime)


@pytest.mark.parametrize(
    ("service_name", "field", "drifted_value"),
    [
        ("worker", "Test", ["CMD", "true"]),
        ("web", "Timeout", 1_000_000_000),
        ("caddy", "StartPeriod", 0),
        ("caddy", "StartInterval", 0),
    ],
)
def test_runtime_contract_rejects_healthcheck_drift(
    tmp_path, service_name, field, drifted_value
):
    contract = _load_compose_contract()
    compose_document = _resolved_contract_document(contract, tmp_path)
    runtime = _runtime_contract_document(contract, compose_document)
    runtime[service_name]["container"]["Config"]["Healthcheck"][field] = drifted_value

    with pytest.raises(ValueError, match="healthcheck contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, runtime)


@pytest.mark.parametrize(
    ("service_name", "port_key", "drifted_value"),
    [
        ("caddy", "2019/tcp", [{"HostIp": "", "HostPort": "2019"}]),
        ("caddy", "443/tcp", [{"HostIp": "", "HostPort": "8443"}]),
        ("web", "8080/tcp", [{"HostIp": "", "HostPort": "8080"}]),
    ],
)
def test_runtime_contract_rejects_port_binding_drift(
    tmp_path, service_name, port_key, drifted_value
):
    contract = _load_compose_contract()
    compose_document = _resolved_contract_document(contract, tmp_path)
    runtime = _runtime_contract_document(contract, compose_document)
    runtime[service_name]["container"]["HostConfig"]["PortBindings"][
        port_key
    ] = drifted_value

    with pytest.raises(ValueError, match="port binding contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, runtime)


def test_runtime_contract_rejects_mount_mode_and_environment_drift_without_values(tmp_path):
    contract = _load_compose_contract()
    compose_document = _resolved_contract_document(contract, tmp_path)
    runtime = _runtime_contract_document(contract, compose_document)
    contract.validate_runtime_contract(compose_document, tmp_path, runtime)

    wrong_mode = _runtime_contract_document(contract, compose_document)
    next(
        mount
        for mount in wrong_mode["web"]["container"]["Mounts"]
        if mount["Destination"] == "/app/security"
    )["RW"] = True
    with pytest.raises(ValueError, match="mount contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, wrong_mode)

    extra_environment = _runtime_contract_document(contract, compose_document)
    extra_environment["worker"]["container"]["Config"]["Env"].append(
        "WEB_PROVIDER_SECRET=leak-me"
    )
    with pytest.raises(ValueError, match="environment.*WEB_PROVIDER_SECRET") as error:
        contract.validate_runtime_contract(compose_document, tmp_path, extra_environment)
    assert "leak-me" not in str(error.value)

    extra_network = _runtime_contract_document(contract, compose_document)
    extra_network["worker"]["container"]["NetworkSettings"]["Networks"][
        "epapersystem-web_portal"
    ] = {}
    with pytest.raises(ValueError, match="network contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, extra_network)

    paused = _runtime_contract_document(contract, compose_document)
    paused["web"]["container"]["State"]["Paused"] = True
    with pytest.raises(ValueError, match="state contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, paused)

    resource_drift = _runtime_contract_document(contract, compose_document)
    resource_drift["worker"]["container"]["HostConfig"]["Memory"] = 0
    with pytest.raises(ValueError, match="resource contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, resource_drift)

    missing_tmpfs = _runtime_contract_document(contract, compose_document)
    missing_tmpfs["caddy"]["container"]["HostConfig"]["Tmpfs"] = {}
    with pytest.raises(ValueError, match="tmpfs contract"):
        contract.validate_runtime_contract(compose_document, tmp_path, missing_tmpfs)


def _service_block(compose, service):
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-z][a-z0-9_-]*:\n|^volumes:\n|\Z)",
        compose,
    )
    assert match is not None, f"service {service!r} is missing"
    return match.group(1)


def test_compose_uses_one_nonroot_python_image_for_web_and_worker():
    compose = (DEPLOY_ROOT / "compose.yaml").read_text(encoding="utf-8")
    dockerfile = (DEPLOY_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "\nservices:\n" in compose
    assert "\n  caddy:\n" in compose
    assert "\n  web:\n" in compose
    assert "\n  worker:\n" in compose
    assert "\n  admin:\n" in compose
    assert "x-python-service: &python-service" in compose
    assert compose.count("<<: *python-service") == 3
    assert 'command: ["python", "-m", "web_portal"]' in compose
    assert 'command: ["python", "-m", "web_portal.worker"]' in compose
    assert "dockerfile: deploy/web/Dockerfile" in compose
    assert "USER 10001:10001" in dockerfile
    assert "PYTHONPATH=/app/inkypi/src" in dockerfile
    assert "LABEL org.opencontainers.image.revision=$WEB_PORTAL_GIT_REVISION" in dockerfile
    assert "WEB_PORTAL_GIT_REVISION: ${WEB_PORTAL_GIT_REVISION:-unknown}" in compose
    assert re.search(r"^FROM python:3\.11-slim-bookworm@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)
    assert re.search(r"image: caddy:2-alpine@sha256:[0-9a-f]{64}", compose)


def test_image_includes_timezone_data_and_uses_a_writable_default_cache():
    dockerfile = (DEPLOY_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "        tzdata" in dockerfile
    assert "XDG_CACHE_HOME=/tmp/.cache" in dockerfile
    assert "XDG_CACHE_HOME=/app/data" not in dockerfile


def test_compose_exposes_only_caddy_and_separates_private_trust_domains():
    compose = (DEPLOY_ROOT / "compose.yaml").read_text(encoding="utf-8")
    caddyfile = (DEPLOY_ROOT / "Caddyfile").read_text(encoding="utf-8")
    web = _service_block(compose, "web")
    worker = _service_block(compose, "worker")
    admin = _service_block(compose, "admin")

    assert compose.count("\n    ports:\n") == 1
    assert '"80:80"' in compose
    assert '"443:443"' in compose
    assert '"443:443/udp"' in compose
    assert "${WEB_PORTAL_PUBLICATION_DIR:-./publication}:/app/publication:ro" in web
    assert "${WEB_PORTAL_SECURITY_DIR:-./security}:/app/security:ro" in web
    assert (
        "${WEB_PORTAL_SESSION_KEY_FILE:-./session-key/web_portal_secret_key}:/run/session-key/web_portal_secret_key:ro"
        in web
    )
    assert "PUBLICATION_ROOT: /app/publication" in web
    assert "WEB_PORTAL_CREDENTIAL_ROOT: /app/security" in web
    assert "WEB_PORTAL_SECRET_KEY_FILE: /run/session-key/web_portal_secret_key" in web
    assert "provider-secrets" not in web
    assert "WEB_CONFIG" not in web
    assert "source: ${WEB_PORTAL_WORKER_DATA_DIR:-./worker-data}/health" in web
    assert "target: /app/worker-health" in web
    assert "read_only: true" in web
    assert "create_host_path: false" in web
    assert "WEB_PORTAL_WORKER_STATE_FILE: /app/worker-health/worker-state.json" in web
    assert "WEB_PORTAL_WORKER_STATE_MAX_AGE_SECONDS: \"300\"" in web

    assert "${WEB_PORTAL_CONFIG_DIR:-./config}:/app/config:ro" in worker
    assert "${WEB_PORTAL_PUBLICATION_DIR:-./publication}:/app/publication" in worker
    assert "${WEB_PORTAL_WORKER_DATA_DIR:-./worker-data}:/app/worker-data" in worker
    assert "${WEB_PORTAL_PROVIDER_SECRETS_DIR:-./provider-secrets}:/run/provider-secrets:ro" in worker
    assert "WEB_CONFIG: /app/config/web_config.json" in worker
    assert "PUBLICATION_ROOT: /app/publication" in worker
    assert "WEB_DATA_ROOT: /app/worker-data" in worker
    assert "WEB_SECRET_DIR: /run/provider-secrets" in worker
    assert "WEB_EDITION_RETENTION_PER_INSTANCE: ${WEB_EDITION_RETENTION_PER_INSTANCE:-32}" in worker
    assert "WEB_CATALOG_RETENTION: ${WEB_CATALOG_RETENTION:-64}" in worker
    assert "WEB_WORKER_DEADLINE_SECONDS: ${WEB_WORKER_DEADLINE_SECONDS:-120}" in worker
    assert "stop_grace_period: 3m" in worker
    assert "session-key" not in worker
    assert "WEB_PORTAL_CREDENTIAL_ROOT" not in worker

    assert 'profiles: ["admin"]' in admin
    assert "network_mode: none" in admin
    assert "${WEB_PORTAL_SECURITY_DIR:-./security}:/app/security" in admin
    assert "WEB_PORTAL_CREDENTIAL_ROOT: /app/security" in admin
    assert ":/app/security:ro" not in admin
    assert "publication" not in admin
    assert "provider-secrets" not in admin
    assert "session-key" not in admin
    assert "WEB_CONFIG" not in admin
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:\n      - ALL" in compose
    assert "caddy_data:/data" in compose
    assert "caddy_config:/config" in compose
    assert "{$WEB_PORTAL_HOSTNAME}" in caddyfile
    assert "reverse_proxy web:8080" in caddyfile
    assert "/api/current_image" not in caddyfile
    assert "/display_plugin_instance" not in caddyfile
    assert "request>remote_ip ip_mask 16 32" in caddyfile
    assert "request>client_ip ip_mask 16 32" in caddyfile
    assert "request>headers>Cookie delete" in caddyfile
    assert "request>headers>Authorization delete" in caddyfile
    assert "request>uri query" in caddyfile
    assert "replace next REDACTED" in caddyfile
    assert "replace playlist REDACTED" in caddyfile
    assert "read_body 15s" in caddyfile
    assert "read_header 10s" in caddyfile
    assert "write 2m" in caddyfile
    assert "idle 2m" in caddyfile
    assert "max_header_size 32KB" in caddyfile
    assert "request_body" in caddyfile
    assert "max_size 16KB" in caddyfile
    assert "0rtt off" in caddyfile


def test_compose_isolates_edge_portal_and_secret_bearing_worker_networks():
    compose = (DEPLOY_ROOT / "compose.yaml").read_text(encoding="utf-8")
    caddy = _service_block(compose, "caddy")
    web = _service_block(compose, "web")
    worker = _service_block(compose, "worker")

    assert "networks:\n      - edge\n      - portal" in caddy
    assert "networks:\n      - portal" in web
    assert "networks:\n      - worker_egress" in worker
    assert "\nnetworks:\n  edge:\n  portal:\n    internal: true\n  worker_egress:\n" in compose


def test_web_trusted_hosts_keep_public_domain_and_allow_loopback_healthcheck():
    compose = (DEPLOY_ROOT / "compose.yaml").read_text(encoding="utf-8")
    web = _service_block(compose, "web")

    assert "WEB_PORTAL_TRUSTED_HOSTS:" in web
    assert "${WEB_PORTAL_HOSTNAME" in web
    assert "127.0.0.1" in web
    assert "localhost" in web


def test_compose_has_healthchecks_resource_limits_and_log_rotation():
    compose = (DEPLOY_ROOT / "compose.yaml").read_text(encoding="utf-8")
    worker = _service_block(compose, "worker")

    assert compose.count("\n    healthcheck:\n") == 3
    assert "http://127.0.0.1:8080/livez" in compose
    assert "http://127.0.0.1:2019/config/" in compose
    assert "WEB_PORTAL_WORKER_HEARTBEAT_FILE: /app/worker-data/health/worker-state.json" in worker
    assert "heartbeat_is_fresh" in worker
    assert "/proc" not in worker
    assert "cmdline" not in worker
    assert "condition: service_healthy" in compose
    assert 'cpus: "0.25"' in compose
    assert 'cpus: "0.75"' in compose
    assert 'cpus: "1.25"' in compose
    assert "mem_limit: 256m" in compose
    assert "mem_limit: 768m" in compose
    assert "mem_limit: 2g" in compose
    assert compose.count("pids_limit:") == 3
    assert 'max-size: "10m"' in compose
    assert 'max-file: "3"' in compose
    assert compose.count("logging: *default-logging") == 3


def test_deployment_example_contains_no_secret_values_or_runtime_state():
    example = (DEPLOY_ROOT / ".env.example").read_text(encoding="utf-8")
    ignored = (DEPLOY_ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (DEPLOY_ROOT / "Dockerfile.dockerignore").read_text(encoding="utf-8")

    assert "WEB_PORTAL_HOSTNAME=epaper.example.com" in example
    assert "WEB_PORTAL_IMAGE=epapersystem-web:REPLACE_WITH_GIT_SHA" in example
    assert "WEB_PORTAL_GIT_REVISION=REPLACE_WITH_GIT_SHA" in example
    assert "WEB_PORTAL_CONFIG_DIR=./config" in example
    assert "WEB_PORTAL_PUBLICATION_DIR=./publication" in example
    assert "WEB_PORTAL_WORKER_DATA_DIR=./worker-data" in example
    assert "WEB_PORTAL_SECURITY_DIR=./security" in example
    assert "WEB_PORTAL_PROVIDER_SECRETS_DIR=./provider-secrets" in example
    assert "WEB_PORTAL_SESSION_KEY_FILE=./session-key/web_portal_secret_key" in example
    assert "WEB_EDITION_RETENTION_PER_INSTANCE=32" in example
    assert "WEB_CATALOG_RETENTION=64" in example
    assert "PASSWORD=" not in example
    assert "SECRET_KEY=" not in example
    assert "TOKEN=" not in example
    assert ".env" in ignored
    assert "publication/" in ignored
    assert "worker-data/" in ignored
    assert "security/" in ignored
    assert "provider-secrets/" in ignored
    assert "session-key/" in ignored
    assert "**/__pycache__" in dockerignore
    assert "**/tests" in dockerignore


def test_deployment_guide_covers_bootstrap_import_backup_and_rollback():
    guide = (REPO_ROOT / "docs" / "web_portal_deployment.md").read_text(encoding="utf-8")

    assert "2 vCPU" in guide
    assert "4 GB" in guide
    assert "40 GB" in guide
    assert "DNS A/AAAA" in guide
    assert "tools/export_web_config.py" in guide
    assert "required_secrets.json" in guide
    assert "web_portal_secret_key" in guide
    assert "bootstrap" in guide.casefold()
    assert "config publication worker-data worker-data/health provider-secrets session-key" in guide
    assert "-m 0700 security" in guide
    assert "sudo install -d -o root -g root -m 0700 backups" in guide
    assert "worker-data/health" in guide
    assert "180 秒" in guide
    assert "root:root" in guide
    assert "0700" in guide
    assert "./provider-secrets/TICKETMASTER_API_KEY" in guide
    assert "./session-key/web_portal_secret_key" in guide
    assert "--profile admin run --rm admin" in guide
    assert "每个实例保留 32" in guide
    assert "目录版本保留 64" in guide
    assert "web_portal_go_live_gate.md" in guide
    assert "run --rm --no-deps web python -c" not in guide
    assert "备份" in guide
    assert "回滚" in guide
    assert "Model Y" in guide
    assert "不会暴露 InkyPi 管理站" in guide
    assert "8080" in guide
    assert "不要" in guide


def test_deployment_guide_covers_device_cache_bootstrap_and_encrypted_backup_restore():
    guide = (REPO_ROOT / "docs" / "web_portal_deployment.md").read_text(encoding="utf-8")

    assert "tools/capture_device_portal.py" in guide
    assert "tools/bootstrap_web_rasters.py" in guide
    assert "不会保存原始管理页 HTML" in guide
    assert "全新且不存在的 publication 目录" in guide
    assert "合成实例 UUID" in guide
    assert "age -R" in guide
    assert ".tar.gz.age" in guide
    assert "sha256sum -c" in guide
    assert 'tar --numeric-owner -czf "backups/web-${STAMP}.tar.gz"' not in guide


def test_go_live_gate_orders_all_preflight_material_before_the_first_build():
    gate = (REPO_ROOT / "docs" / "web_portal_go_live_gate.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "web_portal_deployment.md").read_text(encoding="utf-8")

    build = 'WEB_PORTAL_GIT_REVISION="$GIT_SHA" docker compose'
    for required_material in (
        "/tmp/epaper-web-export/ ./config/",
        "config/required_secrets.json",
        "./provider-secrets/TICKETMASTER_API_KEY",
        "./session-key/web_portal_secret_key",
    ):
        assert required_material in gate
        assert gate.index(required_material) < gate.index(build)
    assert build in guide
    assert "admin 完成后" in gate
    assert "staging hostname" in gate
    assert "docker compose config --help" in gate
    assert "docker compose ps --help" in gate
    assert "docker compose version --short" in gate
    assert '= "5.3.1"' in gate
    for capability in ("--format", "--images", "--hash"):
        assert f"config --help | grep -q -- '{capability}'" in gate
    assert "pull caddy" in gate


def test_ops_scripts_make_backup_restore_and_preflight_fail_closed():
    backup = (DEPLOY_ROOT / "ops" / "backup.sh").read_text(encoding="utf-8")
    restore = (DEPLOY_ROOT / "ops" / "restore-drill.sh").read_text(encoding="utf-8")
    preflight = (DEPLOY_ROOT / "ops" / "preflight.sh").read_text(encoding="utf-8")
    contract = (DEPLOY_ROOT / "ops" / "compose_contract.py").read_text(encoding="utf-8")

    for script in (backup, restore, preflight):
        assert script.startswith("#!/usr/bin/env bash\n")
        assert "set -Eeuo pipefail" in script
        assert "require_command" in script
        assert "--profile admin config --format json" in script
        assert 'expected_compose_version="5.3.1"' in script

    assert ".partial" in backup
    assert "trap backup_cleanup EXIT" in backup
    assert "sha256sum" in backup
    assert "rev-parse HEAD" in backup
    assert "docker compose" in backup
    assert 'git -C "$repo_root" diff --quiet' in backup
    assert 'git -C "$repo_root" diff --cached --quiet' in backup
    assert "ls-files --others --exclude-standard" in backup
    assert "org.opencontainers.image.revision" in backup
    assert 'stop --timeout "$worker_stop_timeout" worker' in backup
    assert "worker did not stop cleanly" in backup
    assert "tar --numeric-owner -czf -" in backup
    assert "age -R" in backup
    assert "mv -- \"$stage_dir\" \"$final_dir\"" in backup
    assert "backup root must be owned by" in backup
    assert "release.env" in backup
    assert 'sync -f "$archive"' in backup
    assert 'sync -f "$stage_dir"' in backup
    assert 'sync -f "$backup_root"' in backup
    assert backup.index('sync -f "$stage_dir"') < backup.index('mv -- "$stage_dir" "$final_dir"')
    assert backup.index('mv -- "$stage_dir" "$final_dir"') < backup.index('sync -f "$backup_root"')
    assert "set +e" in backup
    assert "EPAPER_WORKER_RESTART_TIMEOUT_SECONDS" in backup
    assert "worker restart was not confirmed" in backup
    assert backup.index("set +e") < backup.index('"${compose[@]}" start worker')
    assert backup.index("trap backup_cleanup EXIT") < backup.index('mkdir -- "$stage_dir"')
    assert "required service is not running and healthy" in backup
    assert "running worker image does not match" in backup
    assert "--runtime-inspect-dir /scratch" in backup
    assert "docker inspect --type container" in backup
    assert "config --hash" in backup
    assert 'export WEB_PORTAL_GIT_REVISION="$git_revision"' in backup
    assert "release .env must pin WEB_PORTAL_GIT_REVISION" in backup
    assert "true:none" not in backup
    assert "config --format json" in backup
    assert "configured web image reference does not match" in backup
    assert "compose_contract.py" in backup
    assert "configured web image is not tagged with the clean Git revision" in backup
    assert "backup root must not be nested under archived state" in backup
    assert 'container.get("Mounts")' in contract
    assert 'mount.get("Type")' in contract
    assert 'mount.get("RW")' in contract
    assert 'config.get("Env")' in contract
    assert "running {service_name} mount contract does not match" in contract
    assert "environment does not match resolved Compose contract" in contract

    assert "sha256sum -c" in restore
    assert restore.count("age --decrypt") == 1
    assert "checksum file has an invalid format" in restore
    assert "tar -tvzf" in restore
    assert "archive contains a non-regular entry" in restore
    assert "cmp" in restore
    assert "PRAGMA integrity_check;" in restore
    assert "cat-file -e" in restore
    assert 'git -C "$repo_root" archive "$git_revision"' in restore
    assert "docker image inspect" in restore
    assert "org.opencontainers.image.revision" in restore
    assert "build_runtime_app" in restore
    assert "build_worker" in restore
    assert "required_secrets.json" in restore
    assert "admin_credentials.json" in restore
    assert "path.stat().st_size <= 65536" in restore
    assert 'if [[ ! -e "$restore_parent" ]]' in restore
    assert "restore cleanup failed; decrypted state may remain" in restore
    assert restore.index("set +e") < restore.index('rm -rf -- "$restore_root"')
    assert restore.index("remove_restore_root") < restore.index("restore drill passed")
    assert "does not resolve to the recorded image" in restore
    assert "compose_contract.py" in restore
    assert "WEB_PORTAL_WORKER_STATE_FILE=" in restore
    assert "docker compose" in restore
    assert "docker compose" not in restore or " down" not in restore
    assert "mv config publication" not in restore

    assert "docker compose" in preflight
    assert 'git -C "$repo_root" diff --quiet' in preflight
    assert 'git -C "$repo_root" diff --cached --quiet' in preflight
    assert "ls-files --others --exclude-standard" in preflight
    assert "org.opencontainers.image.revision" in preflight
    assert "config --quiet" in preflight
    assert "caddy adapt --config /etc/caddy/Caddyfile --validate" in preflight
    assert "build_runtime_app" in preflight
    assert "build_worker" in preflight
    assert 'worker_health_dir="$worker_data_dir/health"' in preflight
    assert '10001:10001' in preflight
    assert "session key must be owned by 10001:10001" in preflight
    assert "8#$session_key_mode & 0077" in preflight
    assert "/livez" in preflight
    assert "/readyz" in preflight
    assert "required_secrets.json" in preflight
    assert "(ready|degraded)" not in preflight
    assert '"status"[[:space:]]*:[[:space:]]*"ready"' in preflight
    assert '"${compose[@]}" run' not in preflight
    assert "epaper-preflight." in preflight
    assert "$preflight_scratch/publication:/app/publication:rw" in preflight
    assert "$preflight_scratch/worker-data:/app/worker-data:rw" in preflight
    assert "preflight scratch cleanup failed" in preflight
    assert "compose_contract.py" in preflight
    assert "immutable current Git revision" in preflight
    assert "release .env must pin WEB_PORTAL_GIT_REVISION" in preflight
    assert "worker state directory must be owned by 10001:10001" in preflight
    assert "PUBLIC_BASE_URL" in preflight
    assert "--proto '=https' --tlsv1.2" in preflight
    assert 'expected_compose_version="5.3.1"' in preflight
    assert "for command_name in awk " in preflight
    assert '"${compose[@]}" build' not in preflight
    assert '"${compose[@]}" pull' not in preflight
