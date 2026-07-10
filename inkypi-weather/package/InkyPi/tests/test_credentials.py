import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security.credentials import (
    CredentialError,
    CredentialStore,
    InvalidPassword,
    InvalidToken,
)


class Clock:
    def __init__(self, value=1_800_000_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


def test_bootstrap_token_is_one_time_and_plaintext_file_is_removed(tmp_path):
    store = CredentialStore(tmp_path)

    token = store.create_bootstrap_token()

    assert store.bootstrap_plaintext_path.read_text(encoding="utf-8").strip() == token
    if os.name != "nt":
        assert stat.S_IMODE(store.bootstrap_plaintext_path.stat().st_mode) == 0o600
    assert store.verify_bootstrap_token(token)
    store.consume_bootstrap_token(token, "strong-password")
    assert not store.bootstrap_plaintext_path.exists()
    assert not store.verify_bootstrap_token(token)
    assert store.verify_admin_password("strong-password")
    with pytest.raises(InvalidToken):
        store.consume_bootstrap_token(token, "another-strong-password")


def test_persisted_credentials_never_contain_plaintext_secrets(tmp_path):
    store = CredentialStore(tmp_path)
    token = store.create_bootstrap_token()
    store.consume_bootstrap_token(token, "correct horse battery staple")

    raw = store.credentials_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert token not in raw
    assert "correct horse battery staple" not in raw
    assert payload["version"] == 1
    assert payload["admin"]["password_hash"].startswith("scrypt:")
    if os.name != "nt":
        assert stat.S_IMODE(store.credentials_path.stat().st_mode) == 0o600


def test_bootstrap_token_expires_and_wrong_token_is_constant_failure(tmp_path):
    clock = Clock()
    store = CredentialStore(tmp_path, clock=clock, token_ttl_seconds=60)
    token = store.create_bootstrap_token()

    assert not store.verify_bootstrap_token("wrong-token")
    clock.advance(61)
    assert not store.verify_bootstrap_token(token)
    with pytest.raises(InvalidToken):
        store.consume_bootstrap_token(token, "strong-password")


def test_password_policy_and_authenticated_rotation(tmp_path):
    store = CredentialStore(tmp_path)
    token = store.create_bootstrap_token()
    with pytest.raises(InvalidPassword):
        store.consume_bootstrap_token(token, "too-short")
    store.consume_bootstrap_token(token, "first-strong-password")

    with pytest.raises(InvalidPassword):
        store.rotate_admin_password("wrong-current", "second-strong-password")
    store.rotate_admin_password(
        "first-strong-password",
        "second-strong-password",
    )

    assert not store.verify_admin_password("first-strong-password")
    assert store.verify_admin_password("second-strong-password")


def test_recovery_token_rotates_password_once_without_revealing_hash(tmp_path):
    store = CredentialStore(tmp_path)
    bootstrap = store.create_bootstrap_token()
    store.consume_bootstrap_token(bootstrap, "first-strong-password")

    recovery = store.create_recovery_token()
    assert store.recovery_plaintext_path.read_text(encoding="utf-8").strip() == recovery
    store.consume_recovery_token(recovery, "recovered-strong-password")

    assert not store.recovery_plaintext_path.exists()
    assert store.verify_admin_password("recovered-strong-password")
    with pytest.raises(InvalidToken):
        store.consume_recovery_token(recovery, "third-strong-password")


def test_bootstrap_cannot_replace_an_existing_admin(tmp_path):
    store = CredentialStore(tmp_path)
    token = store.create_bootstrap_token()
    store.consume_bootstrap_token(token, "first-strong-password")

    with pytest.raises(CredentialError, match="already configured"):
        store.create_bootstrap_token()


def test_concurrent_bootstrap_consumption_has_exactly_one_winner(tmp_path):
    store = CredentialStore(tmp_path)
    token = store.create_bootstrap_token()

    def consume(password):
        try:
            store.consume_bootstrap_token(token, password)
            return "success"
        except InvalidToken:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                consume,
                ("first-strong-password", "second-strong-password"),
            )
        )

    assert sorted(results) == ["rejected", "success"]


def test_corrupt_credential_file_fails_closed(tmp_path):
    store = CredentialStore(tmp_path)
    store.credentials_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(CredentialError, match="unreadable"):
        store.has_admin()
    with pytest.raises(CredentialError, match="unreadable"):
        store.verify_admin_password("strong-password")
