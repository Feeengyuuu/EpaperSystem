import socket
import threading

from src.security.egress_proxy import EgressProxy
from src.security.ssrf import ApprovedTarget, UnsafeTarget


class SequencePolicy:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.urls = []

    def resolve_and_validate(self, url):
        self.urls.append(url)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _approved(url="http://safe.example/resource?q=1"):
    return ApprovedTarget(
        normalized_url=url,
        scheme="https" if url.startswith("https:") else "http",
        hostname="safe.example",
        port=443 if url.startswith("https:") else 80,
        addresses=("93.184.216.34",),
    )


def _read_all(sock):
    chunks = []
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def test_http_proxy_connects_to_pinned_ip_and_revalidates_next_request():
    policy = SequencePolicy(
        _approved(),
        UnsafeTarget("DNS answer became non-public"),
    )
    connector_calls = []
    upstream_requests = []

    def connector(address, timeout=None):
        connector_calls.append((address, timeout))
        client, server = socket.socketpair()

        def respond():
            with server:
                upstream_requests.append(server.recv(65536))
                server.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
                )

        threading.Thread(target=respond, daemon=True).start()
        return client

    proxy = EgressProxy(policy=policy, connector=connector)
    try:
        assert proxy.start()
        assert proxy.address[0] == "127.0.0.1"
        assert proxy.address[1] > 0

        with socket.create_connection(proxy.address, timeout=2) as client:
            client.sendall(
                b"GET http://safe.example/resource?q=1 HTTP/1.1\r\n"
                b"Host: safe.example\r\nProxy-Connection: keep-alive\r\n\r\n"
            )
            first = _read_all(client)

        with socket.create_connection(proxy.address, timeout=2) as client:
            client.sendall(
                b"GET http://safe.example/again HTTP/1.1\r\n"
                b"Host: safe.example\r\n\r\n"
            )
            second = _read_all(client)

        assert b"200 OK" in first and first.endswith(b"ok")
        assert b"403 Forbidden" in second
        assert connector_calls[0][0] == ("93.184.216.34", 80)
        assert b"GET /resource?q=1 HTTP/1.1" in upstream_requests[0]
        assert b"Host: safe.example" in upstream_requests[0]
        assert b"Proxy-Connection" not in upstream_requests[0]
        assert len(connector_calls) == 1
        assert policy.urls == [
            "http://safe.example/resource?q=1",
            "http://safe.example/again",
        ]
    finally:
        proxy.close()


def test_connect_tunnel_uses_validated_ip_and_preserves_client_tls_bytes():
    policy = SequencePolicy(_approved("https://safe.example/"))
    connector_calls = []
    received = []

    def connector(address, timeout=None):
        connector_calls.append((address, timeout))
        client, server = socket.socketpair()

        def echo():
            with server:
                received.append(server.recv(4))
                server.sendall(b"pong")

        threading.Thread(target=echo, daemon=True).start()
        return client

    proxy = EgressProxy(policy=policy, connector=connector)
    try:
        assert proxy.start()
        with socket.create_connection(proxy.address, timeout=2) as client:
            client.sendall(
                b"CONNECT safe.example:443 HTTP/1.1\r\nHost: safe.example:443\r\n\r\n"
            )
            established = client.recv(4096)
            assert b"200 Connection Established" in established
            client.sendall(b"ping")
            assert client.recv(4) == b"pong"

        assert connector_calls[0][0] == ("93.184.216.34", 443)
        assert received == [b"ping"]
        assert policy.urls == ["https://safe.example:443/"]
    finally:
        proxy.close()


def test_plain_http_redirect_is_validated_before_browser_receives_it():
    policy = SequencePolicy(
        _approved("http://safe.example/start"),
        UnsafeTarget("redirect became private"),
    )

    def connector(_address, timeout=None):
        client, server = socket.socketpair()

        def redirect():
            with server:
                server.recv(65536)
                server.sendall(
                    b"HTTP/1.1 302 Found\r\n"
                    b"Location: http://127.0.0.1/admin\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )

        threading.Thread(target=redirect, daemon=True).start()
        return client

    proxy = EgressProxy(policy=policy, connector=connector)
    try:
        assert proxy.start()
        with socket.create_connection(proxy.address, timeout=2) as client:
            client.sendall(
                b"GET http://safe.example/start HTTP/1.1\r\n"
                b"Host: safe.example\r\n\r\n"
            )
            response = _read_all(client)

        assert b"403 Forbidden" in response
        assert b"Location: http://127.0.0.1" not in response
        assert policy.urls == [
            "http://safe.example/start",
            "http://127.0.0.1/admin",
        ]
    finally:
        proxy.close()


def test_proxy_start_failure_is_fail_closed():
    def broken_server(*_args, **_kwargs):
        raise OSError("loopback bind unavailable")

    proxy = EgressProxy(policy=SequencePolicy(_approved()), server_factory=broken_server)

    assert proxy.start() is False
    assert proxy.available is False
    assert proxy.proxy_url is None
