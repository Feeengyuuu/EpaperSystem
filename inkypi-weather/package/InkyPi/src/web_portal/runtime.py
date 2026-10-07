"""Environment-backed production runtime for ``python -m web_portal``."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import stat

from security.credentials import CredentialStore

from .app import create_web_app


DEFAULT_CREDENTIAL_ROOT = "/app/data/security"
DEFAULT_SECRET_FILE = "/run/secrets/web_portal_secret_key"
DEFAULT_PUBLICATIONS_FACTORY = "web_portal.factories:create_reader"
MAX_SESSION_KEY_BYTES = 4096


class RuntimeConfigurationError(RuntimeError):
    """Raised before binding a port when the deployment contract is incomplete."""


def _integer(environment, name, default, *, minimum=1, maximum=65535):
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise RuntimeConfigurationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise RuntimeConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _enabled(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _waitress_proxy_options(environment):
    """Configure Waitress itself to trust the single private Caddy hop."""

    if not _enabled(
        environment.get("WEB_PORTAL_TRUST_PROXY_HEADERS"),
        default=True,
    ):
        return {}
    trusted_proxy = str(environment.get("WEB_PORTAL_WAITRESS_TRUSTED_PROXY", "*")).strip()
    if not trusted_proxy:
        raise RuntimeConfigurationError("WEB_PORTAL_WAITRESS_TRUSTED_PROXY cannot be empty")
    return {
        "trusted_proxy": trusted_proxy,
        "trusted_proxy_count": _integer(
            environment,
            "WEB_PORTAL_TRUSTED_PROXY_COUNT",
            1,
            minimum=1,
            maximum=8,
        ),
        "trusted_proxy_headers": {
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
        },
        "clear_untrusted_proxy_headers": True,
    }


def _secret_file_is_private(path):
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if os.name != "posix":
            return True
        return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    except OSError:
        return False


def _load_secret(environment):
    secret_file = environment.get("WEB_PORTAL_SECRET_KEY_FILE")
    direct_secret = environment.get("WEB_PORTAL_SECRET_KEY")
    if secret_file:
        path = Path(secret_file)
    else:
        default_path = Path(DEFAULT_SECRET_FILE)
        path = default_path if default_path.is_file() else None
    if path is not None:
        if not _secret_file_is_private(path):
            raise RuntimeConfigurationError(
                "WEB_PORTAL_SECRET_KEY_FILE must be a regular file with private permissions"
            )
        try:
            with path.open("rb") as handle:
                encoded = handle.read(MAX_SESSION_KEY_BYTES + 1)
            if len(encoded) > MAX_SESSION_KEY_BYTES:
                raise RuntimeConfigurationError("WEB_PORTAL_SECRET_KEY_FILE is too large")
            secret = encoded.decode("utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise RuntimeConfigurationError("WEB_PORTAL_SECRET_KEY_FILE is unreadable") from error
    else:
        secret = direct_secret or ""
    if len(secret) < 32:
        raise RuntimeConfigurationError(
            "WEB_PORTAL_SECRET_KEY or WEB_PORTAL_SECRET_KEY_FILE must contain at least 32 characters"
        )
    return secret


def load_injected_component(spec):
    """Load and call a no-argument ``module:factory`` deployment seam."""

    if not isinstance(spec, str) or spec.count(":") != 1:
        raise RuntimeConfigurationError("factory must use the module.path:callable format")
    module_name, attribute_name = (part.strip() for part in spec.split(":", 1))
    if not module_name or not attribute_name:
        raise RuntimeConfigurationError("factory must use the module.path:callable format")
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute_name)
    except (ImportError, AttributeError) as error:
        raise RuntimeConfigurationError("configured factory could not be imported") from error
    if not callable(factory):
        raise RuntimeConfigurationError("configured factory is not callable")
    try:
        return factory()
    except Exception as error:
        raise RuntimeConfigurationError("configured factory failed during startup") from error


def build_runtime_app(environment=None):
    """Build a production app entirely from an explicit environment mapping."""

    environment = os.environ if environment is None else environment
    factory_spec = environment.get("WEB_PORTAL_PUBLICATIONS_FACTORY", DEFAULT_PUBLICATIONS_FACTORY)
    secret_key = _load_secret(environment)
    publications = load_injected_component(factory_spec)
    missing = [
        method
        for method in ("list_publications", "get_publication", "get_asset")
        if not callable(getattr(publications, method, None))
    ]
    if missing:
        raise RuntimeConfigurationError(
            "publication factory result is missing read-only methods: " + ", ".join(missing)
        )

    credential_root = environment.get("WEB_PORTAL_CREDENTIAL_ROOT", DEFAULT_CREDENTIAL_ROOT)
    credentials = CredentialStore(credential_root)
    config = {
        "PORTAL_TIMEZONE": environment.get("WEB_PORTAL_TIMEZONE", "America/Los_Angeles"),
        "LOGIN_MAX_ATTEMPTS": _integer(
            environment,
            "WEB_PORTAL_LOGIN_MAX_ATTEMPTS",
            5,
            minimum=1,
            maximum=100,
        ),
        "LOGIN_GLOBAL_MAX_ATTEMPTS": _integer(
            environment,
            "WEB_PORTAL_LOGIN_GLOBAL_MAX_ATTEMPTS",
            100,
            minimum=1,
            maximum=100000,
        ),
        "LOGIN_MAX_TRACKED_CLIENTS": _integer(
            environment,
            "WEB_PORTAL_LOGIN_MAX_TRACKED_CLIENTS",
            4096,
            minimum=1,
            maximum=100000,
        ),
        "LOGIN_WINDOW_SECONDS": _integer(
            environment,
            "WEB_PORTAL_LOGIN_WINDOW_SECONDS",
            900,
            minimum=10,
            maximum=86400,
        ),
        "WORKER_STATE_MAX_AGE_SECONDS": _integer(
            environment,
            "WEB_PORTAL_WORKER_STATE_MAX_AGE_SECONDS",
            300,
            minimum=1,
            maximum=86400,
        ),
        "PUBLICATION_HEALTH_CACHE_SECONDS": _integer(
            environment,
            "WEB_PORTAL_PUBLICATION_HEALTH_CACHE_SECONDS",
            10,
            minimum=1,
            maximum=300,
        ),
        "WORKER_STATE_PATH": environment.get("WEB_PORTAL_WORKER_STATE_FILE"),
    }
    trusted_hosts = environment.get("WEB_PORTAL_TRUSTED_HOSTS")
    if trusted_hosts:
        config["TRUSTED_HOSTS"] = [host.strip() for host in trusted_hosts.split(",") if host.strip()]
    app = create_web_app(publications, credentials, secret_key, config)
    return app


def main(environment=None):
    """Serve the portal with Waitress; configuration errors abort before bind."""

    environment = os.environ if environment is None else environment
    app = build_runtime_app(environment)
    host = environment.get("WEB_PORTAL_HOST", "0.0.0.0")
    port = _integer(environment, "WEB_PORTAL_PORT", 8080)
    threads = _integer(environment, "WEB_PORTAL_THREADS", 8, minimum=1, maximum=64)
    try:
        from waitress import serve
    except ImportError as error:
        raise RuntimeConfigurationError("waitress is required to serve web_portal") from error
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        max_request_body_size=16 * 1024,
        max_request_header_size=32 * 1024,
        **_waitress_proxy_options(environment),
    )


__all__ = [
    "RuntimeConfigurationError",
    "build_runtime_app",
    "load_injected_component",
    "main",
]
