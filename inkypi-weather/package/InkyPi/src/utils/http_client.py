"""
HTTP Client with Connection Pooling for InkyPi

Provides a shared requests.Session() instance for all plugins to use.
Benefits:
- Connection reuse (20-30% faster requests)
- Reduced TCP handshake overhead
- Automatic keep-alive handling
- Consistent headers across all requests

Usage:
    from utils.http_client import get_http_session

    session = get_http_session()
    response = session.get(url)
"""

import logging
import os
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# Global session instance (singleton)
_HTTP_SESSION: Optional[requests.Session] = None
_PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_DEAD_LOCAL_PROXY_HOSTS = {"127.0.0.1", "localhost", "::1"}
_DEAD_LOCAL_PROXY_PORTS = {0, 9}


def _is_dead_local_proxy(value: str) -> bool:
    proxy = str(value or "").strip()
    if not proxy:
        return False
    candidate = proxy if "://" in proxy else f"http://{proxy}"
    try:
        parsed = urlparse(candidate)
        port = parsed.port
    except ValueError:
        return False
    host = str(parsed.hostname or "").strip().lower()
    return host in _DEAD_LOCAL_PROXY_HOSTS and port in _DEAD_LOCAL_PROXY_PORTS


def sanitize_dead_local_proxy_environment() -> dict[str, str]:
    """Remove known-dead local proxy variables from the process environment."""
    removed = {}
    for name in _PROXY_ENV_NAMES:
        value = os.environ.get(name)
        if _is_dead_local_proxy(value):
            removed[name] = value
            os.environ.pop(name, None)
    if removed:
        logger.warning(
            "Removed dead local proxy environment variables: %s",
            ", ".join(sorted(removed)),
        )
    return removed


def _dead_local_proxy_configured() -> bool:
    return any(_is_dead_local_proxy(os.environ.get(name)) for name in _PROXY_ENV_NAMES)


def _disable_dead_local_proxy(session: requests.Session) -> None:
    removed = sanitize_dead_local_proxy_environment()
    if removed and session.trust_env:
        logger.warning("Ignoring dead local proxy environment for shared HTTP session")
        session.trust_env = False


def get_http_session() -> requests.Session:
    """
    Get the shared HTTP session instance.
    Creates it on first call (lazy initialization).

    Returns:
        requests.Session: Shared session with connection pooling
    """
    global _HTTP_SESSION

    if _HTTP_SESSION is None:
        logger.debug("Initializing shared HTTP session with connection pooling")
        _HTTP_SESSION = requests.Session()
        _disable_dead_local_proxy(_HTTP_SESSION)

        # Set common headers for all InkyPi requests
        _HTTP_SESSION.headers.update({
            'User-Agent': 'InkyPi/1.0 (https://github.com/fatihak/InkyPi/)'
        })

        # Configure connection pool
        # Max 10 connections per host (reasonable for e-ink device)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=3,
            pool_block=False
        )
        _HTTP_SESSION.mount('http://', adapter)
        _HTTP_SESSION.mount('https://', adapter)

        logger.debug("HTTP session initialized successfully")

    _disable_dead_local_proxy(_HTTP_SESSION)
    return _HTTP_SESSION


def close_http_session():
    """
    Close the shared HTTP session.
    Should be called on application shutdown.
    """
    global _HTTP_SESSION

    if _HTTP_SESSION is not None:
        logger.debug("Closing shared HTTP session")
        _HTTP_SESSION.close()
        _HTTP_SESSION = None

