"""Application-wide authentication, Host, same-origin, and CSRF guards."""

from __future__ import annotations

from datetime import timedelta
import hmac
import ipaddress
import math
import os
import secrets
import socket
import time
from urllib.parse import urlsplit

from flask import current_app, jsonify, request, session
from flask.sessions import SecureCookieSessionInterface

from security.rate_limit import BoundedRateLimiter


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MACHINE_HOSTS_REFRESH_SECONDS = 60.0
PUBLIC_MUTATION_ENDPOINTS = frozenset(
    {"auth.login", "auth.setup", "auth.recover"}
)
ADMIN_SESSION_KEY = "admin_identity"
CSRF_SESSION_KEY = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"


class ForwardedSecureCookieSessionInterface(SecureCookieSessionInterface):
    """Set Secure dynamically when direct or supported proxy TLS is detected."""

    def get_cookie_secure(self, app):
        return bool(super().get_cookie_secure(app) or request_is_secure())


def request_is_secure() -> bool:
    if request.is_secure:
        return True
    forwarded = request.headers.get("X-Forwarded-Proto", "")
    return forwarded.split(",", 1)[0].strip().lower() == "https"


def ensure_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def rotate_csrf_token() -> str:
    token = secrets.token_urlsafe(32)
    session[CSRF_SESSION_KEY] = token
    return token


def current_admin_authenticated() -> bool:
    if session.get(ADMIN_SESSION_KEY) != "admin":
        return False
    store = current_app.config.get("CREDENTIAL_STORE")
    if store is None or not getattr(store, "available", True):
        return False
    try:
        return bool(store.has_admin())
    except Exception:
        return False


def install_request_guards(
    app,
    credential_store,
    device_config,
    *,
    rate_limiter=None,
):
    if app.extensions.get("inkypi_request_guard"):
        return app.extensions["inkypi_request_guard"]

    limiter = rate_limiter or BoundedRateLimiter()
    allowed_hosts = _initial_allowed_hosts(device_config)
    state = {
        "credential_store": credential_store,
        "device_config": device_config,
        "rate_limiter": limiter,
        "allowed_hosts": allowed_hosts,
        "machine_hosts": _machine_hosts(),
        "machine_hosts_refreshed_at": None,
    }
    app.extensions["inkypi_request_guard"] = state
    app.config["CREDENTIAL_STORE"] = credential_store
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if not app.config.get("SESSION_COOKIE_NAME"):
        app.config["SESSION_COOKIE_NAME"] = "inkypi_session"
    app.permanent_session_lifetime = timedelta(hours=8)
    if isinstance(app.session_interface, SecureCookieSessionInterface):
        app.session_interface = ForwardedSecureCookieSessionInterface()

    @app.before_request
    def inkypi_request_guard():
        if request.method in SAFE_METHODS:
            return None

        host = _authority_hostname(request.host)
        if host is None:
            return _failure("host_not_allowed", 400)
        current_hosts = (
            allowed_hosts
            | state["machine_hosts"]
            | _device_allowed_hosts(device_config)
        )
        if host not in current_hosts:
            # DHCP leases and mDNS names can change after startup; refresh the
            # machine-derived set at most once per window before rejecting.
            refreshed_at = state["machine_hosts_refreshed_at"]
            now = time.monotonic()
            if (
                refreshed_at is None
                or now - refreshed_at >= MACHINE_HOSTS_REFRESH_SECONDS
            ):
                state["machine_hosts_refreshed_at"] = now
                state["machine_hosts"] = _machine_hosts()
                current_hosts |= state["machine_hosts"]
            if host not in current_hosts:
                return _failure("host_not_allowed", 400)

        action = request.endpoint or request.path
        limit, window = _rate_policy(action)
        decision = limiter.check(
            request.remote_addr or "unknown",
            action,
            limit=limit,
            window_seconds=window,
        )
        if not decision.allowed:
            return _failure(
                "rate_limited",
                429,
                retry_after=decision.retry_after_seconds,
            )

        public_mutation = request.endpoint in PUBLIC_MUTATION_ENDPOINTS
        if not public_mutation and not current_admin_authenticated():
            return _failure("authentication_required", 401)

        if not _same_origin_when_present():
            return _failure("csrf_failed", 403)

        expected = ensure_csrf_token()
        supplied = request.headers.get(CSRF_HEADER)
        if supplied is None and request.mimetype in {
            "application/x-www-form-urlencoded",
            "multipart/form-data",
        }:
            supplied = request.form.get("_csrf_token")
        if not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
            return _failure("csrf_failed", 403)
        return None

    @app.context_processor
    def inkypi_security_context():
        return {
            "inkypi_csrf_token": ensure_csrf_token(),
            "inkypi_admin_authenticated": current_admin_authenticated(),
            "inkypi_plain_http_warning": not request_is_secure(),
        }

    @app.after_request
    def inkypi_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.config["HEALTH_DETAIL_AUTHORIZER"] = (
        lambda _request: current_admin_authenticated()
    )
    return state


def _failure(code, status, *, retry_after=None):
    messages = {
        "authentication_required": "Administrator authentication is required.",
        "csrf_failed": "Request origin or CSRF validation failed.",
        "host_not_allowed": "Request Host is not allowed.",
        "rate_limited": "Too many requests. Please try again later.",
    }
    response = jsonify(
        {
            "success": False,
            "error": messages[code],
            "error_code": code,
        }
    )
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    if retry_after is not None:
        response.headers["Retry-After"] = str(max(1, math.ceil(retry_after)))
    return response


def _rate_policy(action):
    if action == "auth.login":
        return 10, 5 * 60.0
    if action in {"auth.setup", "auth.recover"}:
        return 10, 10 * 60.0
    if action in {"settings.shutdown", "auth.password"}:
        return 10, 60.0
    return 120, 60.0


def _same_origin_when_present() -> bool:
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return True
    try:
        parsed = urlsplit(source)
        source_host = _authority_hostname(parsed.netloc)
        source_port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except (ValueError, TypeError):
        return False
    if parsed.scheme.lower() not in {"http", "https"} or source_host is None:
        return False
    request_host = _authority_hostname(request.host)
    if request_host is None:
        return False
    request_scheme = "https" if request_is_secure() else "http"
    try:
        request_port = urlsplit(f"//{request.host}").port
    except ValueError:
        return False
    request_port = request_port or (443 if request_scheme == "https" else 80)
    return (
        parsed.scheme.lower() == request_scheme
        and source_host == request_host
        and source_port == request_port
    )


def _initial_allowed_hosts(device_config) -> set[str]:
    hosts = {"localhost", "127.0.0.1", "::1"}
    hosts.update(_device_allowed_hosts(device_config))
    for candidate in os.getenv("INKYPI_ALLOWED_HOSTS", "").split(","):
        normalized = _authority_hostname(candidate)
        if normalized:
            hosts.add(normalized)
    return hosts


def _machine_hosts() -> set[str]:
    """Names and addresses this machine is reachable as, right now."""

    hosts: set[str] = set()
    for candidate in (socket.gethostname(), socket.getfqdn()):
        normalized = _authority_hostname(candidate)
        if normalized:
            hosts.add(normalized)
            if "." not in normalized and ":" not in normalized:
                hosts.add(f"{normalized}.local")
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None):
            normalized = _authority_hostname(item[4][0])
            if normalized:
                hosts.add(normalized)
    except OSError:
        pass
    # A connected UDP socket never transmits; it only asks the kernel which
    # source address routes toward the (documentation-range) target.  This is
    # the address DHCP actually handed out, which getaddrinfo(hostname) hides
    # behind 127.0.1.1 on Debian-family systems.
    for family, probe_target in (
        (socket.AF_INET, ("192.0.2.1", 9)),
        (socket.AF_INET6, ("2001:db8::1", 9)),
    ):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.connect(probe_target)
                normalized = _authority_hostname(probe.getsockname()[0])
                if normalized:
                    hosts.add(normalized)
        except OSError:
            pass
    return hosts


def _device_allowed_hosts(device_config) -> set[str]:
    hosts = set()
    try:
        name = device_config.get_config("name", default=None)
    except Exception:
        name = None
    normalized_name = _authority_hostname(name)
    if normalized_name:
        hosts.add(normalized_name)
        if "." not in normalized_name and ":" not in normalized_name:
            hosts.add(f"{normalized_name}.local")
    try:
        configured = device_config.get_config("allowed_hosts", default=[])
    except Exception:
        configured = []
    if isinstance(configured, str):
        configured = configured.split(",")
    if isinstance(configured, (list, tuple, set, frozenset)):
        for candidate in configured:
            normalized = _authority_hostname(candidate)
            if normalized:
                hosts.add(normalized)
    return hosts


def _authority_hostname(authority) -> str | None:
    if not isinstance(authority, str):
        return None
    raw = authority.strip()
    if (
        not raw
        or any(character.isspace() or ord(character) < 32 for character in raw)
        or any(character in raw for character in "/\\@")
    ):
        return None
    try:
        if raw.count(":") > 1 and not raw.startswith("["):
            return ipaddress.ip_address(raw).compressed.lower()
        parsed = urlsplit(f"//{raw}")
        hostname = parsed.hostname
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
    except (ValueError, TypeError):
        return None
    if hostname is None:
        return None
    return hostname.rstrip(".").lower()
