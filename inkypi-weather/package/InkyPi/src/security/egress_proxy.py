"""Loopback-only HTTP proxy that pins every browser request to SSRF-safe IPs."""

from __future__ import annotations

from email.parser import BytesHeaderParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import select
import socket
import threading
from urllib.parse import urljoin, urlsplit

from security.ssrf import SSRFPolicy, UnsafeTarget, get_ssrf_policy


logger = logging.getLogger(__name__)

MAX_REQUEST_BODY_BYTES = 1024 * 1024
MAX_RELAY_CHUNK_BYTES = 64 * 1024
MAX_RESPONSE_HEADER_BYTES = 64 * 1024
CONNECT_TIMEOUT_SECONDS = 10.0
HTTP_RESPONSE_TIMEOUT_SECONDS = 30.0
TUNNEL_IDLE_TIMEOUT_SECONDS = 185.0
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


class _LoopbackProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class EgressProxy:
    """A small fail-closed HTTP/CONNECT proxy for one Chromium renderer."""

    def __init__(
        self,
        *,
        policy: SSRFPolicy | None = None,
        connector=socket.create_connection,
        server_factory=_LoopbackProxyServer,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        response_timeout=HTTP_RESPONSE_TIMEOUT_SECONDS,
        tunnel_idle_timeout=TUNNEL_IDLE_TIMEOUT_SECONDS,
    ):
        self.policy = policy or get_ssrf_policy()
        self._connector = connector
        self._server_factory = server_factory
        self.connect_timeout = float(connect_timeout)
        self.response_timeout = float(response_timeout)
        self.tunnel_idle_timeout = float(tunnel_idle_timeout)
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self._connections = set()
        self._connections_lock = threading.Lock()
        self._closed = False

    @property
    def available(self) -> bool:
        thread = self._thread
        return bool(
            not self._closed
            and self._server is not None
            and thread is not None
            and thread.is_alive()
        )

    @property
    def address(self):
        server = self._server
        if server is None:
            return None
        host, port = server.server_address[:2]
        return str(host), int(port)

    @property
    def proxy_url(self):
        address = self.address
        if not self.available or address is None:
            return None
        return f"http://{address[0]}:{address[1]}"

    def start(self) -> bool:
        with self._lock:
            if self.available:
                return True
            if self._closed:
                return False
            try:
                server = self._server_factory(
                    ("127.0.0.1", 0),
                    _EgressProxyHandler,
                )
                server.egress_proxy = self
                thread = threading.Thread(
                    target=server.serve_forever,
                    name="inkypi-browser-egress",
                    kwargs={"poll_interval": 0.1},
                    daemon=True,
                )
                self._server = server
                self._thread = thread
                thread.start()
            except (OSError, RuntimeError, TypeError):
                logger.exception("Could not start browser egress proxy")
                server = self._server
                self._server = None
                self._thread = None
                if server is not None:
                    try:
                        server.server_close()
                    except OSError:
                        pass
                return False
            return self.available

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is not None:
            try:
                server.shutdown()
            except OSError:
                pass
            try:
                server.server_close()
            except OSError:
                pass
        with self._connections_lock:
            connections = tuple(self._connections)
            self._connections.clear()
        for connection in connections:
            _close_socket(connection)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def connect(self, approved):
        last_error = None
        for address in approved.addresses:
            connection = None
            try:
                connection = self._connector(
                    (address, approved.port),
                    timeout=self.connect_timeout,
                )
                connection.settimeout(self.response_timeout)
                with self._connections_lock:
                    if self._closed:
                        _close_socket(connection)
                        raise OSError("browser egress proxy is closed")
                    self._connections.add(connection)
                return connection
            except OSError as error:
                if connection is not None:
                    _close_socket(connection)
                last_error = error
        raise OSError("no approved target address was reachable") from last_error

    def release(self, connection) -> None:
        with self._connections_lock:
            self._connections.discard(connection)
        _close_socket(connection)


class _EgressProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "InkyPiEgress/1"
    sys_version = ""

    def do_CONNECT(self):
        owner = self.server.egress_proxy
        upstream = None
        established = False
        try:
            approved = owner.policy.resolve_and_validate(f"https://{self.path}/")
            upstream = owner.connect(approved)
            upstream.settimeout(None)
            self.send_response(200, "Connection Established")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            established = True
            self._tunnel(upstream, owner.tunnel_idle_timeout)
        except (UnsafeTarget, ValueError):
            if not established:
                self._reject(403, "Forbidden")
        except OSError:
            if not established:
                self._reject(502, "Bad Gateway")
        finally:
            if upstream is not None:
                owner.release(upstream)
            self.close_connection = True

    def _forward_http(self):
        owner = self.server.egress_proxy
        upstream = None
        try:
            parsed_request = urlsplit(self.path)
            if parsed_request.scheme.lower() != "http":
                self._reject(400, "Absolute HTTP target required")
                return
            approved = owner.policy.resolve_and_validate(self.path)
            body = self._request_body()
            if body is None:
                return
            upstream = owner.connect(approved)
            request_bytes = self._upstream_request(approved, body)
            upstream.sendall(request_bytes)
            self._relay_response(owner, upstream, approved)
        except (UnsafeTarget, ValueError):
            self._reject(403, "Forbidden")
        except OSError:
            self._reject(502, "Bad Gateway")
        finally:
            if upstream is not None:
                owner.release(upstream)
            self.close_connection = True

    do_DELETE = _forward_http
    do_GET = _forward_http
    do_HEAD = _forward_http
    do_OPTIONS = _forward_http
    do_PATCH = _forward_http
    do_POST = _forward_http
    do_PUT = _forward_http

    def _request_body(self):
        if self.headers.get("Transfer-Encoding"):
            self._reject(400, "Chunked proxy requests are not supported")
            return None
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            self._reject(400, "Invalid Content-Length")
            return None
        if not 0 <= length <= MAX_REQUEST_BODY_BYTES:
            self._reject(413, "Request body too large")
            return None
        body = self.rfile.read(length) if length else b""
        if len(body) != length:
            self._reject(400, "Incomplete request body")
            return None
        return body

    def _upstream_request(self, approved, body) -> bytes:
        parsed = urlsplit(approved.normalized_url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        connection_tokens = {
            item.strip().lower()
            for item in self.headers.get("Connection", "").split(",")
            if item.strip()
        }
        blocked = _HOP_BY_HOP_HEADERS | connection_tokens | {"host"}
        lines = [f"{self.command} {path} HTTP/1.1", f"Host: {approved.authority}"]
        for name, value in self.headers.items():
            if name.lower() in blocked:
                continue
            if "\r" in value or "\n" in value:
                raise ValueError("invalid proxy request header")
            lines.append(f"{name}: {value}")
        lines.append("Connection: close")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body

    def _relay_response(self, owner, upstream, approved) -> None:
        header_buffer = bytearray()
        while b"\r\n\r\n" not in header_buffer:
            chunk = upstream.recv(MAX_RELAY_CHUNK_BYTES)
            if not chunk:
                raise OSError("upstream closed before sending response headers")
            header_buffer.extend(chunk)
            if len(header_buffer) > MAX_RESPONSE_HEADER_BYTES:
                raise OSError("upstream response headers are too large")

        header_end = header_buffer.index(b"\r\n\r\n") + 4
        response_head = bytes(header_buffer[:header_end])
        self._validate_redirect(owner, approved, response_head)
        self.connection.sendall(header_buffer)
        while True:
            try:
                chunk = upstream.recv(MAX_RELAY_CHUNK_BYTES)
            except OSError:
                return
            if not chunk:
                return
            try:
                self.connection.sendall(chunk)
            except OSError:
                return

    @staticmethod
    def _validate_redirect(owner, approved, response_head) -> None:
        status_line, separator, header_bytes = response_head.partition(b"\r\n")
        if not separator:
            raise OSError("upstream response has no status line")
        try:
            status = int(status_line.split(b" ", 2)[1])
        except (IndexError, TypeError, ValueError) as error:
            raise OSError("upstream response status is malformed") from error
        if not 300 <= status < 400:
            return
        headers = BytesHeaderParser().parsebytes(header_bytes)
        for location in headers.get_all("Location", ()):
            redirect_url = urljoin(approved.normalized_url, location)
            owner.policy.resolve_and_validate(redirect_url)

    def _tunnel(self, upstream, idle_timeout) -> None:
        sockets = (self.connection, upstream)
        while True:
            readable, _, exceptional = select.select(
                sockets,
                (),
                sockets,
                idle_timeout,
            )
            if exceptional or not readable:
                return
            for source in readable:
                data = source.recv(MAX_RELAY_CHUNK_BYTES)
                if not data:
                    return
                destination = upstream if source is self.connection else self.connection
                destination.sendall(data)

    def _reject(self, status, message):
        if self.wfile.closed:
            return
        try:
            self.send_error(status, message)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.close_connection = True

    def log_message(self, _format, *_args):
        return


def _close_socket(connection) -> None:
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        connection.close()
    except OSError:
        pass
