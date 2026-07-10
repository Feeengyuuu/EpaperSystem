"""Durable administrator credentials and one-time local pairing tokens."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash

from utils.atomic_file import atomic_write_bytes, atomic_write_json


logger = logging.getLogger(__name__)

CREDENTIAL_VERSION = 1
DEFAULT_TOKEN_TTL_SECONDS = 24 * 60 * 60
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024


class CredentialError(RuntimeError):
    pass


class InvalidToken(CredentialError):
    pass


class InvalidPassword(CredentialError):
    pass


class UnavailableCredentialStore:
    """Fail-closed stand-in that keeps public diagnostics available."""

    available = False

    def has_admin(self) -> bool:
        return True

    def verify_admin_password(self, _password) -> bool:
        return False

    def __getattr__(self, _name):
        raise CredentialError("administrator credential storage is unavailable")


def _credential_root(source) -> Path:
    if hasattr(source, "data_dir"):
        return Path(source.data_dir).expanduser() / "security"
    return Path(source).expanduser()


def _validate_password(password) -> str:
    if not isinstance(password, str):
        raise InvalidPassword("password must be text")
    if "\x00" in password:
        raise InvalidPassword("password contains an invalid character")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise InvalidPassword(
            f"password must contain {MIN_PASSWORD_LENGTH} to "
            f"{MAX_PASSWORD_LENGTH} characters"
        )
    return password


def _token_digest(token) -> str:
    if not isinstance(token, str):
        token = ""
    return hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()


class CredentialStore:
    """Persist scrypt password hashes and single-use pairing token hashes."""

    available = True

    def __init__(
        self,
        root_or_runtime_paths,
        *,
        clock=time.time,
        token_ttl_seconds=DEFAULT_TOKEN_TTL_SECONDS,
    ):
        self.root = _credential_root(root_or_runtime_paths)
        if self.root.exists() and self.root.is_symlink():
            raise CredentialError("credential directory cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            logger.warning("Could not restrict credential directory permissions: %s", self.root)
        self.credentials_path = self.root / "admin_credentials.json"
        self.bootstrap_plaintext_path = self.root / "bootstrap_admin.token"
        self.recovery_plaintext_path = self.root / "recovery_admin.token"
        self._clock = clock
        try:
            ttl = float(token_ttl_seconds)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("token_ttl_seconds must be positive") from error
        if not 1 <= ttl <= 7 * 24 * 60 * 60:
            raise ValueError("token_ttl_seconds must be between 1 second and 7 days")
        self.token_ttl_seconds = ttl
        self._lock = threading.RLock()

    def has_admin(self) -> bool:
        with self._lock:
            payload = self._load_locked()
            admin = payload.get("admin")
            return bool(
                isinstance(admin, dict)
                and admin.get("username") == "admin"
                and isinstance(admin.get("password_hash"), str)
                and admin["password_hash"].startswith("scrypt:")
            )

    def create_bootstrap_token(self) -> str:
        with self._lock:
            payload = self._load_locked()
            if isinstance(payload.get("admin"), dict):
                raise CredentialError("administrator is already configured")
            return self._create_token_locked(
                payload,
                field="bootstrap",
                plaintext_path=self.bootstrap_plaintext_path,
            )

    def verify_bootstrap_token(self, token) -> bool:
        with self._lock:
            return self._verify_token_locked(
                self._load_locked(),
                "bootstrap",
                token,
            )

    def consume_bootstrap_token(self, token, password) -> None:
        password = _validate_password(password)
        with self._lock:
            payload = self._load_locked()
            if isinstance(payload.get("admin"), dict):
                raise InvalidToken("bootstrap token is invalid or expired")
            if not self._verify_token_locked(payload, "bootstrap", token):
                raise InvalidToken("bootstrap token is invalid or expired")
            updated = dict(payload)
            updated.pop("bootstrap", None)
            updated["admin"] = self._admin_record(password)
            self._write_locked(updated)
            self._remove_plaintext_token(
                self.bootstrap_plaintext_path,
                missing_ok=True,
            )

    def verify_admin_password(self, password) -> bool:
        if not isinstance(password, str) or len(password) > MAX_PASSWORD_LENGTH:
            return False
        with self._lock:
            admin = self._load_locked().get("admin")
            password_hash = admin.get("password_hash") if isinstance(admin, dict) else None
        if not isinstance(password_hash, str):
            return False
        try:
            return bool(check_password_hash(password_hash, password))
        except (ValueError, TypeError):
            return False

    def rotate_admin_password(self, current_password, new_password) -> None:
        new_password = _validate_password(new_password)
        with self._lock:
            payload = self._load_locked()
            admin = payload.get("admin")
            if not isinstance(admin, dict):
                raise CredentialError("administrator is not configured")
            password_hash = admin.get("password_hash")
            try:
                valid = bool(
                    isinstance(password_hash, str)
                    and isinstance(current_password, str)
                    and check_password_hash(password_hash, current_password)
                )
            except (TypeError, ValueError):
                valid = False
            if not valid:
                raise InvalidPassword("current administrator password is incorrect")
            updated = dict(payload)
            updated["admin"] = self._admin_record(new_password)
            self._write_locked(updated)

    def create_recovery_token(self) -> str:
        with self._lock:
            payload = self._load_locked()
            if not isinstance(payload.get("admin"), dict):
                raise CredentialError("administrator is not configured")
            return self._create_token_locked(
                payload,
                field="recovery",
                plaintext_path=self.recovery_plaintext_path,
            )

    def verify_recovery_token(self, token) -> bool:
        with self._lock:
            return self._verify_token_locked(
                self._load_locked(),
                "recovery",
                token,
            )

    def consume_recovery_token(self, token, new_password) -> None:
        new_password = _validate_password(new_password)
        with self._lock:
            payload = self._load_locked()
            if not isinstance(payload.get("admin"), dict):
                raise CredentialError("administrator is not configured")
            if not self._verify_token_locked(payload, "recovery", token):
                raise InvalidToken("recovery token is invalid or expired")
            updated = dict(payload)
            updated.pop("recovery", None)
            updated["admin"] = self._admin_record(new_password)
            self._write_locked(updated)
            self._remove_plaintext_token(
                self.recovery_plaintext_path,
                missing_ok=True,
            )

    def _create_token_locked(self, payload, *, field, plaintext_path) -> str:
        token = secrets.token_urlsafe(32)
        now = float(self._clock())
        updated = dict(payload)
        updated[field] = {
            "token_hash": _token_digest(token),
            "created_at": now,
            "expires_at": now + self.token_ttl_seconds,
        }
        atomic_write_bytes(
            plaintext_path,
            (token + "\n").encode("utf-8"),
            mode=0o600,
        )
        self._set_private_mode(plaintext_path)
        try:
            self._write_locked(updated)
        except BaseException:
            self._remove_plaintext_token(plaintext_path, missing_ok=True)
            raise
        return token

    def _verify_token_locked(self, payload, field, token) -> bool:
        record = payload.get(field)
        expected = record.get("token_hash") if isinstance(record, dict) else ""
        try:
            expires_at = float(record.get("expires_at")) if isinstance(record, dict) else 0.0
        except (TypeError, ValueError, OverflowError):
            expires_at = 0.0
        supplied = _token_digest(token)
        hashes_match = hmac.compare_digest(str(expected), supplied)
        return bool(expected and hashes_match and float(self._clock()) <= expires_at)

    def _admin_record(self, password) -> dict:
        return {
            "username": "admin",
            "password_hash": generate_password_hash(password, method="scrypt"),
            "updated_at": float(self._clock()),
        }

    def _load_locked(self) -> dict:
        if not self.credentials_path.exists():
            return {"version": CREDENTIAL_VERSION}
        if self.credentials_path.is_symlink():
            raise CredentialError("administrator credential file cannot be a symlink")
        try:
            if self.credentials_path.stat().st_size > 64 * 1024:
                raise CredentialError("administrator credential file is too large")
            payload = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        except CredentialError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CredentialError("administrator credential file is unreadable") from error
        if not isinstance(payload, dict) or payload.get("version") != CREDENTIAL_VERSION:
            raise CredentialError("administrator credential file has an unsupported format")
        return payload

    def _write_locked(self, payload) -> None:
        normalized = dict(payload)
        normalized["version"] = CREDENTIAL_VERSION
        atomic_write_json(self.credentials_path, normalized, mode=0o600)
        self._set_private_mode(self.credentials_path)

    @staticmethod
    def _set_private_mode(path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError as error:
            raise CredentialError(
                f"could not restrict credential file permissions: {path}"
            ) from error

    @staticmethod
    def _remove_plaintext_token(path, *, missing_ok=False) -> None:
        try:
            Path(path).unlink(missing_ok=missing_ok)
        except OSError as error:
            raise CredentialError("could not remove one-time plaintext token") from error
