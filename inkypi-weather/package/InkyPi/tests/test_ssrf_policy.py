import socket

import pytest

from src.security.ssrf import SSRFPolicy, UnsafeTarget


class Resolver:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, host, port, **_kwargs):
        self.calls.append((host, port))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        values = answer if isinstance(answer, (list, tuple)) else [answer]
        return [
            (
                socket.AF_INET6 if ":" in value else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (value, port, 0, 0) if ":" in value else (value, port),
            )
            for value in values
        ]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://224.0.0.1/",
        "http://[ff02::1]/",
        "http://240.0.0.1/",
        "http://0.0.0.0/",
        "http://user:pass@example.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://example.com\\@127.0.0.1/",
    ],
)
def test_unsafe_targets_are_rejected(url):
    policy = SSRFPolicy(resolver=Resolver("93.184.216.34"))

    with pytest.raises(UnsafeTarget):
        policy.resolve_and_validate(url)


def test_public_target_is_normalized_and_pinned_to_every_dns_answer():
    resolver = Resolver(["93.184.216.34", "1.1.1.1"])
    policy = SSRFPolicy(resolver=resolver)

    approved = policy.resolve_and_validate(
        "HTTPS://Example.COM:443/path?q=1#not-sent"
    )

    assert approved.normalized_url == "https://example.com/path?q=1"
    assert approved.hostname == "example.com"
    assert approved.port == 443
    assert approved.addresses == ("93.184.216.34", "1.1.1.1")
    assert resolver.calls == [("example.com", 443)]


def test_mixed_public_and_private_dns_answers_fail_closed():
    policy = SSRFPolicy(
        resolver=Resolver(["93.184.216.34", "127.0.0.1"]),
    )

    with pytest.raises(UnsafeTarget, match="non-public"):
        policy.resolve_and_validate("https://mixed.example/")


def test_every_resolution_is_revalidated_against_dns_rebinding():
    resolver = Resolver("93.184.216.34", "127.0.0.1")
    policy = SSRFPolicy(resolver=resolver)

    assert policy.resolve_and_validate("https://rebind.example/").addresses == (
        "93.184.216.34",
    )
    with pytest.raises(UnsafeTarget):
        policy.resolve_and_validate("https://rebind.example/")


def test_explicit_host_or_cidr_allowlist_can_reach_private_service():
    host_policy = SSRFPolicy(
        resolver=Resolver("192.168.20.5"),
        allowed_private_hosts={"panel.local"},
    )
    cidr_policy = SSRFPolicy(
        resolver=Resolver("10.20.30.40"),
        allowed_private_cidrs={"10.20.30.0/24"},
    )

    assert host_policy.resolve_and_validate("http://panel.local/").addresses == (
        "192.168.20.5",
    )
    assert cidr_policy.resolve_and_validate("http://internal.example/").addresses == (
        "10.20.30.40",
    )


def test_cloud_metadata_is_never_allowlisted():
    policy = SSRFPolicy(
        resolver=Resolver("169.254.169.254"),
        allowed_private_hosts={"metadata.local"},
        allowed_private_cidrs={"169.254.0.0/16"},
    )

    with pytest.raises(UnsafeTarget, match="metadata"):
        policy.resolve_and_validate("http://metadata.local/latest/meta-data/")


def test_private_cidr_allowlist_rejects_public_or_overbroad_networks():
    with pytest.raises(ValueError, match="private network"):
        SSRFPolicy(allowed_private_cidrs={"0.0.0.0/0"})


def test_operator_environment_builds_exact_private_allowlist(monkeypatch):
    monkeypatch.setenv("INKYPI_BROWSER_ALLOWED_HOSTS", "panel.home.arpa")
    monkeypatch.setenv("INKYPI_BROWSER_ALLOWED_CIDRS", "10.44.0.0/16")
    policy = SSRFPolicy.from_environment(resolver=Resolver("10.44.2.9"))

    approved = policy.resolve_and_validate("http://service.home.arpa/status")

    assert approved.addresses == ("10.44.2.9",)
    assert policy.allowed_private_hosts == frozenset({"panel.home.arpa"})
