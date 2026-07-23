"""
APOD Plugin for InkyPi
This plugin fetches the Astronomy Picture of the Day (APOD) from NASA's API
and displays it on the InkyPi device. It supports optional manual date selection or random dates.
For the API key, set `NASA_SECRET={API_KEY}` in your .env file.
"""

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.context_cache import write_context
from plugins.apod.apod_page import (
    ApodPageLayoutError,
    measure_apod_page,
    render_apod_page,
)
from plugins.apod.space_weather import SpaceWeatherRepository, refresh_space_weather
from runtime.long_task_executor import current_instance_identity, current_task_context
from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.atomic_file import atomic_write_json
from PIL import Image
from utils.http_client import HttpClient, get_http_client
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import hashlib
import http.client
import ipaddress
import json
import logging
import random
import re
import socket
import ssl
import threading
import time

logger = logging.getLogger(__name__)

RANDOM_APOD_MAX_ATTEMPTS = 5
APOD_ENDPOINT = "https://api.nasa.gov/planetary/apod"
OPENAI_TRANSLATION_ENDPOINT = "https://api.openai.com/v1/chat/completions"
GROQ_TRANSLATION_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
SELECTION_CACHE_SCHEMA = 1
APOD_STATE_SCHEMA = 1
TRANSLATION_CACHE_SCHEMA = 1
MAX_SELECTION_JSON_BYTES = 16 * 1024
MAX_APOD_JSON_BYTES = 512 * 1024
MAX_APOD_STATE_BYTES = 768 * 1024
MAX_TRANSLATION_JSON_BYTES = 256 * 1024
MAX_MEDIA_BYTES = 25 * 1024 * 1024
MAX_MEDIA_PIXELS = 80_000_000
MAX_DECODED_MEDIA_BYTES = 32 * 1024 * 1024
APOD_TIMEOUT_SECONDS = 20
TRANSLATION_TIMEOUT_SECONDS = 20
MEDIA_TIMEOUT_SECONDS = 40
DNS_WAIT_SLICE_SECONDS = 0.05
_SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "credential",
    "key",
    "key-pair-id",
    "policy",
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "access_token",
    "signature",
    "sig",
}
_AZURE_SAS_QUERY_KEYS = {
    "se",
    "sig",
    "sip",
    "si",
    "skoid",
    "sks",
    "skt",
    "sktid",
    "skv",
    "sp",
    "spr",
    "sr",
    "srt",
    "ss",
    "st",
    "sv",
}
_SENSITIVE_QUERY_FRAGMENTS = {
    "auth",
    "credential",
    "key",
    "password",
    "passwd",
    "pwd",
    "secret",
    "signature",
    "token",
}
_SENSITIVE_COMPACT_QUERY_KEYS = {
    "accesskey",
    "accesskeyid",
    "accesstoken",
    "apikey",
    "authkey",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "credentialkey",
    "idtoken",
    "keypairid",
    "password",
    "passwd",
    "pwd",
    "privatekey",
    "refreshtoken",
    "secretkey",
    "signingkey",
}
_DRAFT_SAFE_MEDIA_FORMATS = {"JPEG", "MPO"}
_ABORT_EXCEPTIONS = (TaskDeadlineExceeded, TaskCancelled)
_media_monotonic = time.monotonic
_DNS_WORKER_SLOT = threading.BoundedSemaphore(value=1)


def _wait_for_dns_completion(done: threading.Event, timeout: float) -> bool:
    return done.wait(timeout)


def _wait_for_dns_worker_slot(timeout: float) -> bool:
    return _DNS_WORKER_SLOT.acquire(timeout=timeout)


@dataclass(frozen=True)
class ApodRecord:
    selection_key: str
    requested_device_date: str
    date: str
    media_type: str
    title_en: str
    title_zh: str | None
    translation_state: Literal["pending", "live", "fresh_cache", "unavailable"]
    explanation: str
    copyright: str | None
    url: str | None
    hdurl: str | None
    image_url: str | None
    image_cache_key: str | None
    fetched_at_utc: datetime
    source_state: Literal["live", "fresh_cache", "stale_cache", "unavailable"]
    warning: str | None


@dataclass(frozen=True)
class InstancePaths:
    cache: Path
    data: Path
    media: Path
    identity_key: str


@dataclass(frozen=True)
class ApodSelection:
    device_day: str
    mode: Literal["today", "random", "custom"]
    requested_date: str
    fingerprint: str
    resolved_record_date: str | None = None
    record_cache_key: str | None = None
    provisional: bool = False
    candidate_dates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApodDisplayState:
    selection_fingerprint: str
    device_day: str
    requested_date: str
    requested_record: ApodRecord
    display_record: ApodRecord
    fallback_reason: Literal["video", "current_media_unavailable"] | None
    provisional_media: bool


class ApodMediaUnavailable(RuntimeError):
    """No validated image media can be admitted for the selected record."""


@dataclass(frozen=True)
class _ApprovedMediaTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[str, ...]

    @property
    def authority(self) -> str:
        default_port = 443 if self.scheme == "https" else 80
        return (
            self.hostname
            if self.port == default_port
            else f"{self.hostname}:{self.port}"
        )


class _MediaDeadline:
    """One monotonic media budget shared by DNS and every transport phase."""

    def __init__(self, context, timeout: float):
        seconds = float(timeout)
        if seconds <= 0:
            raise ValueError("APOD media timeout must be positive")
        self._context = context
        self._clock = _media_monotonic
        self._deadline = float(self._clock()) + seconds

    def raise_if_cancelled(self) -> None:
        _task_checkpoint(self._context)
        if float(self._clock()) >= self._deadline:
            raise TaskDeadlineExceeded("APOD media deadline expired")
        remaining = getattr(self._context, "remaining_seconds", None)
        if callable(remaining) and float(remaining()) <= 0:
            _task_checkpoint(self._context)
            raise TaskDeadlineExceeded("task deadline expired")

    def remaining_seconds(self) -> float:
        self.raise_if_cancelled()
        remaining = self._deadline - float(self._clock())
        external_remaining = getattr(self._context, "remaining_seconds", None)
        if callable(external_remaining):
            remaining = min(remaining, float(external_remaining()))
        if remaining <= 0:
            self.raise_if_cancelled()
            raise TaskDeadlineExceeded("APOD media deadline expired")
        return remaining


def _task_checkpoint(context) -> None:
    checkpoint = getattr(context, "raise_if_cancelled", None)
    if callable(checkpoint):
        checkpoint()


def _media_deadline(context, timeout: float) -> _MediaDeadline:
    if isinstance(context, _MediaDeadline):
        return context
    return _MediaDeadline(context, timeout)


_SELECTION_FILENAME = "selection.json"
_RANDOM_APOD_START = date(2015, 1, 1)


def _instance_paths(
    plugin: "Apod", *, preview_namespace: str | None = None
) -> InstancePaths:
    """Return trusted instance storage, or an isolated explicit preview namespace."""

    if preview_namespace is not None:
        namespace = str(preview_namespace).strip()
        if not namespace:
            raise ValueError("preview namespace must be non-empty")
        identity_key = "preview-" + hashlib.sha256(
            namespace.encode("utf-8")
        ).hexdigest()
    else:
        identity = current_instance_identity()
        instance_uuid = getattr(identity, "instance_uuid", None)
        generation = getattr(identity, "structural_generation", None)
        if not isinstance(instance_uuid, str) or not instance_uuid.strip() or generation is None:
            raise RuntimeError("APOD requires a trusted runtime instance identity")
        identity_key = hashlib.sha256(
            f"{instance_uuid}:{generation}".encode("utf-8")
        ).hexdigest()

    cache = plugin.cache_dir(leaf=Path("instances") / identity_key)
    data = plugin.data_dir(leaf=Path("instances") / identity_key)
    # Media is the sole plugin-global namespace: all JSON/state remains instance-safe.
    media = plugin.cache_dir(leaf=Path("media"))
    media.mkdir(parents=True, exist_ok=True)
    return InstancePaths(cache=cache, data=data, media=media, identity_key=identity_key)


def _selection_fingerprint(
    *, mode: str, device_day: date, requested_date: str, custom_date: str
) -> str:
    """Hash the complete user-visible daily selection contract."""

    contract = {
        "mode": str(mode),
        "device_day": device_day.isoformat(),
        "requested_date": str(requested_date),
        "custom_date": str(custom_date),
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _device_day(device_config) -> date:
    """Resolve the display's local calendar day without trusting the host zone."""

    configured = device_config.get_config("timezone", default="UTC")
    try:
        timezone_info = ZoneInfo(str(configured or "UTC"))
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        timezone_info = timezone.utc
    return datetime.now(timezone_info).date()


def _random_candidate_dates(device_day: date, rng: random.Random) -> tuple[str, ...]:
    """Choose a unique bounded compatibility sequence for one device day."""

    latest = max(_RANDOM_APOD_START, device_day)
    day_count = (latest - _RANDOM_APOD_START).days + 1
    if day_count < RANDOM_APOD_MAX_ATTEMPTS:
        raise ValueError("random APOD requires five unique eligible dates")
    attempts = RANDOM_APOD_MAX_ATTEMPTS
    used_offsets: set[int] = set()
    candidates: list[str] = []
    for _ in range(attempts):
        random_offset = rng.randint(0, day_count - 1)
        for step in range(day_count):
            offset = (random_offset + step) % day_count
            if offset in used_offsets:
                continue
            used_offsets.add(offset)
            candidates.append(
                (_RANDOM_APOD_START + timedelta(days=offset)).isoformat()
            )
            break
    return tuple(candidates)


def _resolve_selection(
    *,
    settings: Mapping[str, Any],
    device_day: date,
    paths: InstancePaths,
    rng: random.Random,
    context=None,
) -> ApodSelection:
    """Reuse one valid daily selection, or atomically persist a new one."""

    _task_checkpoint(context)
    custom_date = str(settings.get("customDate") or "").strip()
    if settings.get("randomizeApod") == "true":
        mode: Literal["today", "random", "custom"] = "random"
        requested_date = ""
    elif custom_date:
        mode = "custom"
        requested_date = custom_date
    else:
        mode = "today"
        requested_date = device_day.isoformat()

    if mode == "random":
        fingerprint = _selection_fingerprint(
            mode=mode,
            device_day=device_day,
            requested_date=device_day.isoformat(),
            custom_date=custom_date,
        )
    else:
        fingerprint = _selection_fingerprint(
            mode=mode,
            device_day=device_day,
            requested_date=requested_date,
            custom_date=custom_date,
        )

    persisted = _read_selection(paths.data / _SELECTION_FILENAME)
    _task_checkpoint(context)
    if persisted is not None and _selection_matches(
        persisted,
        device_day,
        mode,
        fingerprint,
        requested_date,
    ):
        return persisted

    if mode == "random":
        candidate_dates = _random_candidate_dates(device_day, rng)
        _task_checkpoint(context)
        selected_date = date.fromisoformat(candidate_dates[0])
        requested_date = selected_date.isoformat()
        provisional = True
    else:
        selected_date = date.fromisoformat(requested_date)
        provisional = False
        candidate_dates = ()

    record_cache_key = hashlib.sha256(
        f"{paths.identity_key}:{selected_date.isoformat()}".encode("utf-8")
    ).hexdigest()
    selection = ApodSelection(
        device_day=device_day.isoformat(),
        mode=mode,
        requested_date=requested_date,
        fingerprint=fingerprint,
        resolved_record_date=selected_date.isoformat(),
        record_cache_key=record_cache_key,
        provisional=provisional,
        candidate_dates=candidate_dates,
    )
    _persist_selection(paths, selection, context=context)
    return selection


def _read_selection(path: Path) -> ApodSelection | None:
    raw = _read_bounded_json(path, max_bytes=MAX_SELECTION_JSON_BYTES)
    if raw is None:
        return None
    try:
        if type(raw.get("schema")) is not int:
            return None
        if raw.get("schema") != SELECTION_CACHE_SCHEMA:
            return None
        mode = raw["mode"]
        if mode not in {"today", "random", "custom"}:
            return None
        device_day_text = raw["device_day"]
        requested_text = raw["requested_date"]
        selected = raw["selected_apod_date"]
        fingerprint = raw["selection_fingerprint"]
        record_key = raw["record_cache_key"]
        provisional = raw["provisional"]
        candidate_dates = raw.get("candidate_dates", [])
        if not all(
            type(value) is str
            for value in (
                mode,
                device_day_text,
                requested_text,
                selected,
                fingerprint,
                record_key,
            )
        ):
            return None
        if type(provisional) is not bool:
            return None
        if (
            len(fingerprint) != 64
            or len(record_key) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
            or any(character not in "0123456789abcdef" for character in record_key)
        ):
            return None
        if not isinstance(candidate_dates, list) or not all(
            type(candidate) is str for candidate in candidate_dates
        ):
            return None
        if (
            len(candidate_dates) > RANDOM_APOD_MAX_ATTEMPTS
            or len(set(candidate_dates)) != len(candidate_dates)
        ):
            return None
        device_day = date.fromisoformat(device_day_text)
        requested_day = date.fromisoformat(requested_text)
        selected_day = date.fromisoformat(selected)
        parsed_candidates = tuple(date.fromisoformat(value) for value in candidate_dates)
        if (
            device_day_text != device_day.isoformat()
            or requested_text != requested_day.isoformat()
            or selected != selected_day.isoformat()
            or any(
                raw_value != parsed_value.isoformat()
                for raw_value, parsed_value in zip(
                    candidate_dates,
                    parsed_candidates,
                )
            )
        ):
            return None
        if mode == "random":
            if (
                len(parsed_candidates) != RANDOM_APOD_MAX_ATTEMPTS
                or requested_day != parsed_candidates[0]
                or selected_day not in parsed_candidates
                or (
                    provisional
                    and selected_day != parsed_candidates[0]
                )
                or any(
                    candidate < _RANDOM_APOD_START or candidate > device_day
                    for candidate in parsed_candidates
                )
            ):
                return None
        elif (
            provisional
            or parsed_candidates
            or selected_day != requested_day
        ):
            return None
        return ApodSelection(
            device_day=device_day.isoformat(),
            mode=mode,
            requested_date=requested_day.isoformat(),
            fingerprint=fingerprint,
            resolved_record_date=selected_day.isoformat(),
            record_cache_key=record_key,
            provisional=provisional,
            candidate_dates=tuple(candidate_dates),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _selection_matches(
    selection: ApodSelection,
    device_day: date,
    mode: Literal["today", "random", "custom"],
    fingerprint: str,
    requested_date: str,
) -> bool:
    return (
        selection.device_day == device_day.isoformat()
        and selection.mode == mode
        and selection.fingerprint == fingerprint
        and selection.resolved_record_date is not None
        and selection.record_cache_key is not None
        and (
            mode == "random"
            or (
                selection.requested_date == requested_date
                and selection.resolved_record_date == requested_date
            )
        )
        and (
            mode != "random"
            or (
                bool(selection.candidate_dates)
                and (
                    not selection.provisional
                    or selection.candidate_dates[0] == selection.resolved_record_date
                )
                and len(selection.candidate_dates) == RANDOM_APOD_MAX_ATTEMPTS
                and len(set(selection.candidate_dates)) == len(selection.candidate_dates)
                and all(
                    _RANDOM_APOD_START
                    <= date.fromisoformat(candidate)
                    <= device_day
                    for candidate in selection.candidate_dates
                )
            )
        )
    )


def _persist_selection(
    paths: InstancePaths,
    selection: ApodSelection,
    *,
    context=None,
) -> None:
    document = {
        "schema": SELECTION_CACHE_SCHEMA,
        "device_day": selection.device_day,
        "mode": selection.mode,
        "requested_date": selection.requested_date,
        "selected_apod_date": selection.resolved_record_date,
        "selection_fingerprint": selection.fingerprint,
        "provisional": selection.provisional,
        "record_cache_key": selection.record_cache_key,
        "candidate_dates": list(selection.candidate_dates),
    }
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_SELECTION_JSON_BYTES:
        raise ValueError("APOD selection exceeds its bounded cache size")
    _task_checkpoint(context)
    atomic_write_json(paths.data / _SELECTION_FILENAME, document)


def _resolved_selection(
    paths: InstancePaths,
    selection: ApodSelection,
    record_date: str,
    *,
    context=None,
) -> ApodSelection:
    resolved = ApodSelection(
        device_day=selection.device_day,
        mode=selection.mode,
        requested_date=selection.requested_date,
        fingerprint=selection.fingerprint,
        resolved_record_date=record_date,
        record_cache_key=hashlib.sha256(
            f"{paths.identity_key}:{record_date}".encode("utf-8")
        ).hexdigest(),
        provisional=False,
        candidate_dates=selection.candidate_dates,
    )
    _persist_selection(paths, resolved, context=context)
    return resolved


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _fallback_warning(apod_date: str) -> str:
    return f"LATEST AVAILABLE \N{MIDDLE DOT} APOD {apod_date}"


def _canonical_date_text(value: Any, *, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"APOD {label} must be canonical date text")
    parsed = date.fromisoformat(value)
    canonical = parsed.isoformat()
    if value != canonical:
        raise ValueError(f"APOD {label} must be a canonical date")
    return canonical


def _parse_utc(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _required_text(value: Any, *, label: str, maximum: int = 2000) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise ValueError(f"APOD {label} is missing or invalid")
    return text


def _optional_text(value: Any, *, maximum: int = 4000) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise ValueError("APOD optional text is too long")
    return text


def _query_key_is_sensitive(key: str) -> bool:
    raw = str(key).strip()
    folded = raw.casefold()
    if not folded:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", folded)
    if folded.startswith(("x-amz-", "x-goog-")) or compact.startswith(
        ("xamz", "xgoog")
    ):
        return True
    if (
        folded in _SENSITIVE_QUERY_KEYS
        or folded in _AZURE_SAS_QUERY_KEYS
        or compact in _SENSITIVE_COMPACT_QUERY_KEYS
    ):
        return True
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    words = {
        part
        for part in re.split(r"[^a-z0-9]+", camel_separated.casefold())
        if part
    }
    return bool(words.intersection(_SENSITIVE_QUERY_FRAGMENTS))


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _trusted_apod_media_host(host: str) -> bool:
    normalized = str(host or "").rstrip(".").casefold()
    return normalized == "nasa.gov" or normalized.endswith(".nasa.gov")


def _require_public_hostname_resolution(
    host: str,
    port: int,
    *,
    context=None,
) -> tuple[str, ...]:
    """Validate a trusted host's current transport addresses immediately before I/O."""

    budget = _media_deadline(context, MEDIA_TIMEOUT_SECONDS)
    _task_checkpoint(budget)
    while True:
        wait_seconds = min(
            DNS_WAIT_SLICE_SECONDS,
            budget.remaining_seconds(),
        )
        if _wait_for_dns_worker_slot(wait_seconds):
            break
        _task_checkpoint(budget)
    try:
        _task_checkpoint(budget)
    except BaseException:
        _DNS_WORKER_SLOT.release()
        raise

    outcome: dict[str, Any] = {}
    done = threading.Event()

    def resolve() -> None:
        try:
            outcome["resolved"] = socket.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except Exception as error:
            outcome["error"] = error
        finally:
            try:
                _DNS_WORKER_SLOT.release()
            finally:
                done.set()

    try:
        worker = threading.Thread(
            target=resolve,
            name="apod-media-dns",
            daemon=True,
        )
        worker.start()
    except RuntimeError:
        _DNS_WORKER_SLOT.release()
        raise ValueError("APOD media DNS worker could not start") from None
    except BaseException:
        _DNS_WORKER_SLOT.release()
        raise
    while True:
        _task_checkpoint(budget)
        wait_seconds = min(
            DNS_WAIT_SLICE_SECONDS,
            budget.remaining_seconds(),
        )
        if _wait_for_dns_completion(done, wait_seconds):
            break
        _task_checkpoint(budget)
    _task_checkpoint(budget)

    error = outcome.get("error")
    if error is not None:
        if isinstance(error, _ABORT_EXCEPTIONS):
            raise error
        if isinstance(error, (OSError, TypeError, ValueError)):
            raise ValueError(
                "APOD media URL host did not resolve publicly"
            ) from None
        raise error
    resolved = outcome.get("resolved")
    _task_checkpoint(budget)
    if not resolved:
        raise ValueError("APOD media URL host did not resolve publicly")
    addresses: list[str] = []
    for item in resolved:
        _task_checkpoint(budget)
        try:
            address_text = str(item[4][0]).split("%", 1)[0]
            address = ipaddress.ip_address(address_text)
        except (IndexError, TypeError, ValueError):
            raise ValueError("APOD media URL resolved to an invalid address") from None
        if not _is_public_address(address):
            raise ValueError("APOD media URL resolved to a non-public address")
        normalized = address.compressed.casefold()
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise ValueError("APOD media URL host did not resolve publicly")
    return tuple(addresses)


def _public_http_url(value: Any) -> str | None:
    text = _optional_text(value, maximum=4096)
    if text is None:
        return None
    if (
        not text.isascii()
        or "\\" in text
        or any(character.isspace() or ord(character) < 32 for character in text)
    ):
        raise ValueError("APOD media URL contains invalid characters")
    parsed = urlsplit(text)
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.fragment
    ):
        raise ValueError("APOD media URL is invalid")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("APOD media URL contains credentials")
    try:
        host = str(parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except ValueError:
        raise ValueError("APOD media URL host or port is invalid") from None
    if not host or not _trusted_apod_media_host(host):
        raise ValueError("APOD media URL requires a trusted NASA host")
    expected_port = 443 if scheme == "https" else 80
    if port is not None and port != expected_port:
        raise ValueError("APOD media URL port is outside the NASA allowlist")
    if any(
        _query_key_is_sensitive(key)
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise ValueError("APOD media URL contains sensitive query credentials")
    return text


def _safe_media_candidate(value: Any) -> str | None:
    try:
        return _public_http_url(value)
    except _ABORT_EXCEPTIONS:
        raise
    except (TypeError, ValueError):
        return None


def _media_socket_timeout(context) -> float:
    budget = _media_deadline(context, MEDIA_TIMEOUT_SECONDS)
    _task_checkpoint(budget)
    return max(0.001, budget.remaining_seconds())


def _resolve_apod_media_target(
    media_url: str,
    *,
    context,
) -> _ApprovedMediaTarget:
    trusted_url = _public_http_url(media_url)
    if trusted_url is None:
        raise ValueError("APOD media URL is missing")
    parsed = urlsplit(trusted_url)
    scheme = parsed.scheme.lower()
    hostname = str(parsed.hostname or "").rstrip(".").casefold()
    port = parsed.port or (443 if scheme == "https" else 80)
    addresses = _require_public_hostname_resolution(
        hostname,
        port,
        context=context,
    )
    _task_checkpoint(context)
    return _ApprovedMediaTarget(
        url=trusted_url,
        scheme=scheme,
        hostname=hostname,
        port=port,
        addresses=addresses,
    )


def _download_apod_media_to_file(
    media_url: str,
    path: Path,
    *,
    context,
    timeout: float,
    max_bytes: int,
    mode: int = 0o600,
) -> None:
    """Download through one DNS-approved numeric peer while preserving TLS identity."""

    budget = _media_deadline(context, timeout)
    approved = _resolve_apod_media_target(media_url, context=budget)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    limit = int(max_bytes)
    if limit <= 0:
        raise ValueError("APOD media byte limit must be positive")
    parsed = urlsplit(approved.url)
    request_target = parsed.path or "/"
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"
    request_bytes = (
        f"GET {request_target} HTTP/1.1\r\n"
        f"Host: {approved.authority}\r\n"
        "Accept: image/*\r\n"
        "Accept-Encoding: identity\r\n"
        "Connection: close\r\n"
        "User-Agent: InkyPi-APOD/1\r\n"
        "\r\n"
    ).encode("ascii")

    last_error: Exception | None = None
    for address in approved.addresses:
        raw_socket = None
        connection = None
        response = None
        try:
            _task_checkpoint(budget)
            raw_socket = socket.create_connection(
                (address, approved.port),
                timeout=_media_socket_timeout(budget),
            )
            _task_checkpoint(budget)
            raw_socket.settimeout(_media_socket_timeout(budget))
            if approved.scheme == "https":
                connection = ssl.create_default_context().wrap_socket(
                    raw_socket,
                    server_hostname=approved.hostname,
                )
                raw_socket = None
                _task_checkpoint(budget)
            else:
                connection = raw_socket
                raw_socket = None
            connection.settimeout(_media_socket_timeout(budget))
            connection.sendall(request_bytes)
            _task_checkpoint(budget)
            connection.settimeout(_media_socket_timeout(budget))
            response = http.client.HTTPResponse(connection)
            response.begin()
            _task_checkpoint(budget)

            status = int(response.status)
            if not 200 <= status < 300:
                raise ApodMediaUnavailable(
                    f"APOD media returned disallowed HTTP status {status}"
                )
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > limit:
                        raise ApodMediaUnavailable(
                            "APOD media exceeds the byte limit"
                        )
                except ValueError:
                    pass

            written = 0
            with destination.open("wb") as handle:
                while True:
                    _task_checkpoint(budget)
                    connection.settimeout(_media_socket_timeout(budget))
                    chunk = response.read(64 * 1024)
                    _task_checkpoint(budget)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limit:
                        raise ApodMediaUnavailable(
                            "APOD media exceeds the byte limit"
                        )
                    handle.write(chunk)
            if written <= 0:
                raise ApodMediaUnavailable("APOD media response is empty")
            destination.chmod(int(mode))
            _task_checkpoint(budget)
            return
        except _ABORT_EXCEPTIONS:
            raise
        except (
            ApodMediaUnavailable,
            OSError,
            ssl.SSLError,
            http.client.HTTPException,
            TypeError,
            ValueError,
        ) as error:
            _task_checkpoint(budget)
            last_error = error
        finally:
            if response is not None:
                try:
                    response.close()
                except OSError:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
            elif raw_socket is not None:
                try:
                    raw_socket.close()
                except OSError:
                    pass
    _task_checkpoint(budget)
    raise ApodMediaUnavailable(
        "APOD approved media target could not be reached"
    ) from last_error


def _fetch_apod_record(
    *,
    http: HttpClient,
    api_key: str,
    requested_date: str,
    context,
) -> ApodRecord:
    """Fetch one bounded, validated APOD response without exposing credentials."""

    try:
        _task_checkpoint(context)
        requested = date.fromisoformat(str(requested_date)).isoformat()
        response = http.request_json(
            "GET",
            APOD_ENDPOINT,
            params={"api_key": str(api_key), "date": requested},
            context=context,
            timeout=APOD_TIMEOUT_SECONDS,
            max_bytes=MAX_APOD_JSON_BYTES,
        )
        _task_checkpoint(context)
        raw = response.data
        if not isinstance(raw, Mapping):
            raise ValueError("APOD response is not an object")
        record_date = date.fromisoformat(str(raw.get("date") or "")).isoformat()
        if record_date != requested:
            raise ValueError("APOD response date does not match the request")
        media_type = _required_text(
            raw.get("media_type"), label="media type", maximum=32
        ).casefold()
        title = _required_text(raw.get("title"), label="title", maximum=600)
        explanation = str(raw.get("explanation") or "").strip()
        if len(explanation) > 20_000:
            raise ValueError("APOD explanation is too long")
        url = _safe_media_candidate(raw.get("url"))
        hdurl = _safe_media_candidate(raw.get("hdurl"))
        if media_type == "image" and url is None and hdurl is None:
            raise ValueError("APOD image response has no safe media URL")
        copyright_text = _optional_text(raw.get("copyright"), maximum=500)
    except _ABORT_EXCEPTIONS:
        raise
    except Exception:
        _task_checkpoint(context)
        raise RuntimeError(
            f"NASA APOD request failed for {str(requested_date)}"
        ) from None

    return ApodRecord(
        selection_key=hashlib.sha256(requested.encode("utf-8")).hexdigest(),
        requested_device_date=requested,
        date=record_date,
        media_type=media_type,
        title_en=title,
        title_zh=None,
        translation_state="pending",
        explanation=explanation,
        copyright=copyright_text,
        url=url,
        hdurl=hdurl,
        image_url=None,
        image_cache_key=None,
        fetched_at_utc=_utc_now(),
        source_state="live",
        warning=None,
    )


def _resolve_image_record(
    *,
    requested: ApodRecord,
    fetch_for_date: Callable[[str], ApodRecord],
    max_prior_days: int = 7,
) -> tuple[ApodRecord, bool]:
    """Return the requested image or the newest prior image within the limit."""

    if requested.media_type == "image":
        return requested, False
    if not isinstance(max_prior_days, int) or max_prior_days <= 0:
        raise ValueError("APOD fallback day limit must be positive")
    requested_day = date.fromisoformat(requested.date)
    for offset in range(1, max_prior_days + 1):
        candidate_date = (requested_day - timedelta(days=offset)).isoformat()
        try:
            candidate = fetch_for_date(candidate_date)
        except _ABORT_EXCEPTIONS:
            raise
        except Exception:
            continue
        if candidate.media_type != "image":
            continue
        return (
            replace(
                candidate,
                selection_key=requested.selection_key,
                requested_device_date=requested.requested_device_date,
                warning=_fallback_warning(candidate.date),
            ),
            True,
        )
    raise RuntimeError(
        f"No image APOD was available within {max_prior_days} prior days"
    )


def _safe_media_dimensions(image: Image.Image) -> tuple[int, int]:
    width, height = (int(image.size[0]), int(image.size[1]))
    if width <= 0 or height <= 0 or width * height > MAX_MEDIA_PIXELS:
        raise ApodMediaUnavailable("APOD media dimensions are unsafe")
    media_format = str(getattr(image, "format", "") or "").upper()
    decoded_bands = max(4, len(image.getbands()))
    if (
        media_format not in _DRAFT_SAFE_MEDIA_FORMATS
        and width * height * decoded_bands > MAX_DECODED_MEDIA_BYTES
    ):
        raise ApodMediaUnavailable(
            "APOD decoded memory exceeds the safe limit"
        )
    try:
        orientation = int(image.getexif().get(274, 1))
    except _ABORT_EXCEPTIONS:
        raise
    except Exception:
        orientation = 1
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    return width, height


def _probe_media_blob(path: Path, minimum_size: tuple[int, int]) -> None:
    try:
        byte_count = path.stat().st_size
        if byte_count <= 0 or byte_count > MAX_MEDIA_BYTES:
            raise ApodMediaUnavailable("APOD media byte size is invalid")
        with Image.open(path) as image:
            width, height = _safe_media_dimensions(image)
            if width < int(minimum_size[0]) or height < int(minimum_size[1]):
                raise ApodMediaUnavailable("APOD media is smaller than the photo cell")
            image.verify()
    except ApodMediaUnavailable:
        raise
    except _ABORT_EXCEPTIONS:
        raise
    except Exception:
        raise ApodMediaUnavailable("APOD media validation failed") from None


def _media_url_candidates(record: ApodRecord) -> tuple[str, ...]:
    safe_standard = _safe_media_candidate(record.url)
    safe_hd = _safe_media_candidate(record.hdurl)
    safe_urls = tuple(
        candidate
        for candidate in (safe_standard, safe_hd)
        if candidate is not None
    )
    candidates: list[str] = []
    admitted = _safe_media_candidate(record.image_url)
    if (
        admitted in safe_urls
        and record.image_cache_key
        == hashlib.sha256(str(admitted).encode("utf-8")).hexdigest()
    ):
        candidates.append(str(admitted))
    for candidate in safe_urls:
        if candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def _remove_media_blob(namespace, digest: str) -> None:
    try:
        namespace.remove(digest, suffix=".img")
    except _ABORT_EXCEPTIONS:
        raise
    except (OSError, RuntimeError, ValueError):
        pass


def _resolve_media_url_blob(
    *,
    plugin: "Apod",
    media_url: str,
    paths: InstancePaths,
    minimum_size: tuple[int, int],
    context,
) -> Path:
    """Resolve one URL only, validating a cache hit before its managed LRU read."""

    trusted_media_url = _public_http_url(media_url)
    if trusted_media_url is None:
        raise ApodMediaUnavailable("APOD media URL is missing")
    media_url = trusted_media_url
    digest = hashlib.sha256(media_url.encode("utf-8")).hexdigest()
    namespace = plugin.managed_cache_namespace(paths.media)
    target = namespace.path(digest, suffix=".img")
    _task_checkpoint(context)
    if target.is_file() and not target.is_symlink():
        try:
            _probe_media_blob(target, minimum_size)
            _task_checkpoint(context)
            cached_payload = namespace.get_bytes(digest, suffix=".img")
            if cached_payload is None:
                raise ApodMediaUnavailable("APOD media cache miss")
            del cached_payload
            _probe_media_blob(target, minimum_size)
            _task_checkpoint(context)
            return target
        except _ABORT_EXCEPTIONS:
            raise
        except Exception:
            _remove_media_blob(namespace, digest)
    elif target.exists():
        _remove_media_blob(namespace, digest)

    candidate = paths.media / f".{digest}.{uuid4().hex}.tmp"
    try:
        _task_checkpoint(context)
        _download_apod_media_to_file(
            media_url,
            candidate,
            context=context,
            timeout=MEDIA_TIMEOUT_SECONDS,
            max_bytes=MAX_MEDIA_BYTES,
            mode=0o600,
        )
        _task_checkpoint(context)
        _probe_media_blob(candidate, minimum_size)
        _task_checkpoint(context)
        payload = candidate.read_bytes()
        _task_checkpoint(context)
        target = namespace.put_bytes(
            digest,
            payload,
            suffix=".img",
        )
        _probe_media_blob(target, minimum_size)
        _task_checkpoint(context)
        return target
    except _ABORT_EXCEPTIONS:
        raise
    except Exception:
        _remove_media_blob(namespace, digest)
        raise ApodMediaUnavailable(
            "APOD media candidate could not be validated"
        ) from None
    finally:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def _resolve_media_blob(
    *,
    plugin: "Apod",
    record: ApodRecord,
    paths: InstancePaths,
    minimum_size: tuple[int, int],
    context,
) -> tuple[Path, str]:
    """Reuse or atomically publish one full-SHA plugin-global media blob."""

    urls = _media_url_candidates(record)
    if not urls:
        raise ApodMediaUnavailable("APOD record does not provide image media")

    for media_url in urls:
        try:
            return (
                _resolve_media_url_blob(
                    plugin=plugin,
                    media_url=media_url,
                    paths=paths,
                    minimum_size=minimum_size,
                    context=context,
                ),
                media_url,
            )
        except _ABORT_EXCEPTIONS:
            raise
        except ApodMediaUnavailable:
            continue
    raise ApodMediaUnavailable("No validated APOD image media is available")


def _decode_media_blob(*, blob_path: Path, photo_size: tuple[int, int]) -> Image.Image:
    """Bound and decoder-draft one blob for the measured final photo rectangle."""

    try:
        byte_count = Path(blob_path).stat().st_size
        if byte_count <= 0 or byte_count > MAX_MEDIA_BYTES:
            raise ApodMediaUnavailable("APOD media byte size is invalid")
        with Image.open(blob_path) as image:
            _safe_media_dimensions(image)
            draft = getattr(image, "draft", None)
            if callable(draft):
                draft("RGB", tuple(photo_size))
            decoded_width, decoded_height = (
                int(image.size[0]),
                int(image.size[1]),
            )
            decoded_bands = max(4, len(image.getbands()))
            if (
                decoded_width
                * decoded_height
                * decoded_bands
                > MAX_DECODED_MEDIA_BYTES
            ):
                raise ApodMediaUnavailable(
                    "APOD decoded memory exceeds the safe limit"
                )
            image.load()
            decoded = image.copy()
            exif = image.getexif()
            if exif:
                decoded.info["exif"] = exif.tobytes()
        return decoded
    except ApodMediaUnavailable:
        raise
    except _ABORT_EXCEPTIONS:
        raise
    except Exception:
        raise ApodMediaUnavailable("APOD media decode failed") from None


def _read_bounded_json(path: Path, *, max_bytes: int) -> Mapping[str, Any] | None:
    try:
        with path.open("rb") as handle:
            payload = handle.read(max_bytes + 1)
        if not payload or len(payload) > max_bytes:
            return None
        decoded = json.loads(payload)
        return decoded if isinstance(decoded, Mapping) else None
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        return None


def _translation_cache_path(paths: InstancePaths, apod_date: str, title: str) -> Path:
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()
    return paths.cache / f"translation-{apod_date}-{digest}.json"


def _read_translation_cache(
    *, paths: InstancePaths, apod_date: str, title: str
) -> str | None:
    path = _translation_cache_path(paths, apod_date, title)
    raw = _read_bounded_json(path, max_bytes=MAX_TRANSLATION_JSON_BYTES)
    if raw is None:
        return None
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()
    try:
        if int(raw.get("schema")) != TRANSLATION_CACHE_SCHEMA:
            return None
        if date.fromisoformat(str(raw.get("apod_date"))).isoformat() != apod_date:
            return None
        if raw.get("title_sha256") != digest or raw.get("title_en") != title:
            return None
        translated = _required_text(
            raw.get("title_zh"), label="translated title", maximum=1000
        )
    except (TypeError, ValueError):
        return None
    return translated


def _translation_content(response: Any) -> str:
    raw = response.data
    if not isinstance(raw, Mapping):
        raise ValueError("translation response is not an object")
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("translation response has no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ValueError("translation choice is invalid")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("translation message is invalid")
    return _required_text(
        message.get("content"), label="translated title", maximum=1000
    )


def _translate_title(
    *,
    title: str,
    apod_date: str,
    paths: InstancePaths,
    device_config: Any,
    context,
) -> tuple[str | None, bool]:
    """Translate only the exact title, using an exact date+title cache key."""

    _task_checkpoint(context)
    english = _required_text(title, label="title", maximum=600)
    normalized_date = date.fromisoformat(apod_date).isoformat()
    cached = _read_translation_cache(
        paths=paths, apod_date=normalized_date, title=english
    )
    if cached is not None:
        _task_checkpoint(context)
        return cached, False

    def load_key(name: str) -> str:
        try:
            _task_checkpoint(context)
            return str(device_config.load_env_key(name) or "").strip()
        except _ABORT_EXCEPTIONS:
            raise
        except Exception:
            return ""

    openai_key = load_key("OPEN_AI_SECRET") or load_key("OPENAI_API_KEY")
    groq_key = load_key("GROQ_API_KEY")
    providers = []
    if openai_key:
        providers.append(
            (
                "OpenAI",
                OPENAI_TRANSLATION_ENDPOINT,
                openai_key,
                "gpt-4o-mini",
            )
        )
    if groq_key:
        providers.append(
            (
                "Groq",
                GROQ_TRANSLATION_ENDPOINT,
                groq_key,
                "llama-3.3-70b-versatile",
            )
        )

    http = get_http_client()
    for provider_name, endpoint, api_key, model in providers:
        try:
            _task_checkpoint(context)
            response = http.request_json(
                "POST",
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Translate the supplied astronomy title into concise "
                                "Simplified Chinese. Return only the translation."
                            ),
                        },
                        {"role": "user", "content": english},
                    ],
                },
                context=context,
                timeout=TRANSLATION_TIMEOUT_SECONDS,
                max_bytes=MAX_TRANSLATION_JSON_BYTES,
            )
            _task_checkpoint(context)
            translated = _translation_content(response)
            digest = hashlib.sha256(english.encode("utf-8")).hexdigest()
            _task_checkpoint(context)
            atomic_write_json(
                _translation_cache_path(paths, normalized_date, english),
                {
                    "schema": TRANSLATION_CACHE_SCHEMA,
                    "apod_date": normalized_date,
                    "title_sha256": digest,
                    "title_en": english,
                    "title_zh": translated,
                    "provider": provider_name.casefold(),
                    "fetched_at_utc": _format_utc(_utc_now()),
                },
            )
            _task_checkpoint(context)
            return translated, False
        except _ABORT_EXCEPTIONS:
            raise
        except Exception:
            logger.warning(
                "APOD title translation provider unavailable: %s", provider_name
            )
    return None, True


def _record_document(record: ApodRecord) -> dict[str, Any]:
    return {
        "selection_key": record.selection_key,
        "requested_device_date": record.requested_device_date,
        "date": record.date,
        "media_type": record.media_type,
        "title_en": record.title_en,
        "title_zh": record.title_zh,
        "translation_state": record.translation_state,
        "explanation": record.explanation,
        "copyright": record.copyright,
        "url": record.url,
        "hdurl": record.hdurl,
        "image_url": record.image_url,
        "image_cache_key": record.image_cache_key,
        "fetched_at_utc": _format_utc(record.fetched_at_utc),
        "source_state": record.source_state,
        "warning": record.warning,
    }


def _record_from_document(raw: Any) -> ApodRecord:
    if not isinstance(raw, Mapping):
        raise ValueError("APOD cached record is invalid")
    selection_key = _required_text(
        raw.get("selection_key"), label="selection key", maximum=128
    )
    requested_device_date = _canonical_date_text(
        raw.get("requested_device_date"),
        label="requested device date",
    )
    record_date = _canonical_date_text(raw.get("date"), label="record date")
    media_type = _required_text(
        raw.get("media_type"), label="media type", maximum=32
    ).casefold()
    title_en = _required_text(raw.get("title_en"), label="title", maximum=600)
    title_zh = _optional_text(raw.get("title_zh"), maximum=1000)
    translation_state = str(raw.get("translation_state") or "")
    if translation_state not in {"pending", "live", "fresh_cache", "unavailable"}:
        raise ValueError("APOD translation state is invalid")
    source_state = str(raw.get("source_state") or "")
    if source_state not in {"live", "fresh_cache", "stale_cache", "unavailable"}:
        raise ValueError("APOD source state is invalid")
    standard_url = _safe_media_candidate(raw.get("url"))
    hd_url = _safe_media_candidate(raw.get("hdurl"))
    image_url = _safe_media_candidate(raw.get("image_url"))
    image_cache_key = _optional_text(raw.get("image_cache_key"), maximum=128)
    if media_type == "image" and standard_url is None and hd_url is None:
        raise ValueError("APOD cached image record has no safe media URL")
    if image_url is None and image_cache_key is not None:
        raise ValueError("APOD cached image key has no URL")
    if image_url is not None:
        if image_url not in {standard_url, hd_url}:
            raise ValueError("APOD cached image URL is not a record candidate")
        expected_key = hashlib.sha256(image_url.encode("utf-8")).hexdigest()
        if image_cache_key != expected_key:
            raise ValueError("APOD cached image key does not match its URL")
    return ApodRecord(
        selection_key=selection_key,
        requested_device_date=requested_device_date,
        date=record_date,
        media_type=media_type,
        title_en=title_en,
        title_zh=title_zh,
        translation_state=translation_state,
        explanation=str(raw.get("explanation") or "")[:20_000],
        copyright=_optional_text(raw.get("copyright"), maximum=500),
        url=standard_url,
        hdurl=hd_url,
        image_url=image_url,
        image_cache_key=image_cache_key,
        fetched_at_utc=_parse_utc(raw.get("fetched_at_utc")),
        source_state=source_state,
        warning=_optional_text(raw.get("warning"), maximum=300),
    )


def _cached_record(record: ApodRecord) -> ApodRecord:
    translation_state = record.translation_state
    if record.title_zh:
        translation_state = "fresh_cache"
    return replace(
        record,
        source_state="fresh_cache",
        translation_state=translation_state,
    )


def _read_apod_state(
    path: Path, *, selection: ApodSelection
) -> ApodDisplayState | None:
    raw = _read_bounded_json(path, max_bytes=MAX_APOD_STATE_BYTES)
    if raw is None:
        return None
    try:
        if type(raw.get("schema")) is not int:
            return None
        if raw.get("schema") != APOD_STATE_SCHEMA:
            return None
        if raw.get("selection_fingerprint") != selection.fingerprint:
            return None
        if raw.get("device_day") != selection.device_day:
            return None
        requested_date = _canonical_date_text(
            raw.get("requested_date"),
            label="state requested date",
        )
        if requested_date != selection.resolved_record_date:
            return None
        requested = _record_from_document(raw.get("requested_record"))
        displayed = _record_from_document(raw.get("display_record"))
        if requested.date != requested_date or displayed.media_type != "image":
            return None
        if (
            requested.selection_key != selection.fingerprint
            or displayed.selection_key != selection.fingerprint
            or requested.requested_device_date != selection.device_day
            or displayed.requested_device_date != selection.device_day
            or requested.warning is not None
            or displayed.image_url is None
            or displayed.image_cache_key is None
        ):
            return None
        fallback_reason = raw.get("fallback_reason")
        if fallback_reason not in {None, "video", "current_media_unavailable"}:
            return None
        provisional_media = raw.get("provisional_media")
        if type(provisional_media) is not bool:
            return None
        if provisional_media != (fallback_reason == "current_media_unavailable"):
            return None
        if fallback_reason is None:
            if (
                displayed.date != requested.date
                or displayed.warning is not None
                or requested.media_type != "image"
                or requested.image_url != displayed.image_url
                or requested.image_cache_key != displayed.image_cache_key
            ):
                return None
        else:
            requested_day = date.fromisoformat(requested.date)
            displayed_day = date.fromisoformat(displayed.date)
            fallback_age = (requested_day - displayed_day).days
            if (
                fallback_age < 1
                or fallback_age > 7
                or displayed.warning != _fallback_warning(displayed.date)
            ):
                return None
        if fallback_reason == "video" and requested.media_type != "video":
            return None
        if fallback_reason == "current_media_unavailable" and requested.media_type != "image":
            return None
        return ApodDisplayState(
            selection_fingerprint=selection.fingerprint,
            device_day=selection.device_day,
            requested_date=requested_date,
            requested_record=_cached_record(requested),
            display_record=_cached_record(displayed),
            fallback_reason=fallback_reason,
            provisional_media=provisional_media,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _persist_apod_state(
    paths: InstancePaths,
    state: ApodDisplayState,
    *,
    context=None,
) -> None:
    document = {
        "schema": APOD_STATE_SCHEMA,
        "selection_fingerprint": state.selection_fingerprint,
        "device_day": state.device_day,
        "requested_date": state.requested_date,
        "requested_record": _record_document(state.requested_record),
        "display_record": _record_document(state.display_record),
        "fallback_reason": state.fallback_reason,
        "provisional_media": state.provisional_media,
    }
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_APOD_STATE_BYTES:
        raise ValueError("APOD state exceeds its bounded cache size")
    _task_checkpoint(context)
    atomic_write_json(paths.cache / "apod-state.json", document)


def _bind_record_to_selection(
    record: ApodRecord, *, selection: ApodSelection
) -> ApodRecord:
    return replace(
        record,
        selection_key=selection.fingerprint,
        requested_device_date=selection.device_day,
    )


def _load_or_fetch_apod_state(
    *,
    http: HttpClient,
    api_key: str,
    selection: ApodSelection,
    paths: InstancePaths,
    context,
) -> tuple[ApodDisplayState, ApodSelection]:
    cached = _read_apod_state(paths.cache / "apod-state.json", selection=selection)
    if cached is not None:
        _task_checkpoint(context)
        return cached, selection

    if selection.mode == "random" and selection.provisional:
        requested = None
        for candidate_date in selection.candidate_dates[:RANDOM_APOD_MAX_ATTEMPTS]:
            _task_checkpoint(context)
            try:
                candidate = _fetch_apod_record(
                    http=http,
                    api_key=api_key,
                    requested_date=candidate_date,
                    context=context,
                )
            except _ABORT_EXCEPTIONS:
                raise
            except RuntimeError:
                continue
            if candidate.media_type == "image":
                requested = candidate
                break
        if requested is None:
            raise RuntimeError(
                "No usable APOD image found after five random dates"
            )
    else:
        requested = _fetch_apod_record(
            http=http,
            api_key=api_key,
            requested_date=str(selection.resolved_record_date),
            context=context,
        )

    _task_checkpoint(context)
    requested = _bind_record_to_selection(requested, selection=selection)
    state = ApodDisplayState(
        selection_fingerprint=selection.fingerprint,
        device_day=selection.device_day,
        requested_date=requested.date,
        requested_record=requested,
        display_record=requested,
        fallback_reason=None,
        provisional_media=False,
    )
    return state, selection


class Apod(BasePlugin):
    NASA_LOGO_FILE = "nasa_logo.png"

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "NASA",
            "expected_key": "NASA_SECRET"
        }
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        logger.info("APOD data refresh started")
        api_key = str(device_config.load_env_key("NASA_SECRET") or "").strip()
        if not api_key:
            raise RuntimeError("NASA API Key not configured")

        dimensions = tuple(self.get_dimensions(device_config))
        if dimensions != (800, 480):
            raise ValueError("APOD page requires the approved 800x480 dimensions")
        context = current_task_context()
        _task_checkpoint(context)
        http = get_http_client()
        paths = _instance_paths(self)
        selection = _resolve_selection(
            settings=settings or {},
            device_day=_device_day(device_config),
            paths=paths,
            rng=random.Random(),
            context=context,
        )
        _task_checkpoint(context)
        state, selection = _load_or_fetch_apod_state(
            http=http,
            api_key=api_key,
            selection=selection,
            paths=paths,
            context=context,
        )
        _task_checkpoint(context)

        rendered_at = _utc_now()
        repository = SpaceWeatherRepository(
            cache_dir=paths.cache / "space-weather",
            http=http,
        )
        weather = refresh_space_weather(
            repository,
            nasa_api_key=api_key,
            now_utc=rendered_at,
            context=context,
        )
        _task_checkpoint(context)
        failed_core = [
            name
            for name, result in (("scales", weather.scales), ("kp", weather.kp))
            if result.state != "live" or getattr(result, "error", None) is not None
        ]
        if failed_core:
            raise RuntimeError(
                "APOD current-cycle core admission failed: "
                + ", ".join(failed_core)
            )

        if selection.mode == "random" and selection.provisional:
            (
                displayed,
                source_image,
                unavailable,
                measurement,
                state,
                selection,
            ) = self._resolve_provisional_random(
                state=state,
                http=http,
                api_key=api_key,
                selection=selection,
                paths=paths,
                device_config=device_config,
                context=context,
            )
        else:
            if (
                state.requested_record.media_type != "image"
                and state.display_record.date == state.requested_record.date
            ):
                displayed, source_image, unavailable, measurement, state = (
                    self._resolve_media_fallback(
                        state=state,
                        http=http,
                        api_key=api_key,
                        selection=selection,
                        paths=paths,
                        device_config=device_config,
                        context=context,
                    )
                )
            else:
                candidate = (
                    state.requested_record
                    if state.provisional_media
                    else state.display_record
                )
                try:
                    prepared, unavailable, measurement = self._prepare_record(
                        candidate,
                        paths=paths,
                        device_config=device_config,
                        context=context,
                    )
                    displayed, source_image = self._resolve_record_media(
                        prepared,
                        paths=paths,
                        photo_size=measurement.photo_size,
                        context=context,
                    )
                    if state.provisional_media:
                        state = replace(
                            state,
                            requested_record=displayed,
                            display_record=displayed,
                            fallback_reason=None,
                            provisional_media=False,
                        )
                    elif displayed.date == state.requested_record.date:
                        state = replace(
                            state,
                            requested_record=displayed,
                            display_record=displayed,
                        )
                    else:
                        state = replace(state, display_record=displayed)
                except _ABORT_EXCEPTIONS:
                    raise
                except ApodMediaUnavailable:
                    (
                        displayed,
                        source_image,
                        unavailable,
                        measurement,
                        state,
                    ) = self._resolve_media_fallback(
                        state=state,
                        http=http,
                        api_key=api_key,
                        selection=selection,
                        paths=paths,
                        device_config=device_config,
                        context=context,
                    )

        _task_checkpoint(context)
        image = render_apod_page(
            apod=displayed,
            title_zh=displayed.title_zh,
            translation_unavailable=unavailable,
            weather=weather,
            source_image=source_image,
            rendered_at_utc=rendered_at,
            dimensions=dimensions,
            measurement=measurement,
        )
        _task_checkpoint(context)
        provenance = weather.aggregate_state
        if provenance in {SourceProvenance.LIVE, SourceProvenance.FRESH_CACHE}:
            provenance = SourceProvenance.LIVE
        image = attach_source_provenance(image, provenance)
        if provenance in {
            SourceProvenance.STALE_CACHE,
            SourceProvenance.LOCAL_FALLBACK,
        }:
            image.info["inkypi_skip_cache"] = True
        _task_checkpoint(context)
        _persist_apod_state(paths, state, context=context)
        _task_checkpoint(context)
        self._write_apod_context(
            displayed,
            weather,
            provenance,
            rendered_at,
            context=context,
        )
        logger.info("APOD data refresh completed")
        return image

    def _prepare_record(self, record, *, paths, device_config, context):
        _task_checkpoint(context)
        had_cached_translation = _read_translation_cache(
            paths=paths,
            apod_date=record.date,
            title=record.title_en,
        ) is not None
        title_zh, unavailable = _translate_title(
            title=record.title_en,
            apod_date=record.date,
            paths=paths,
            device_config=device_config,
            context=context,
        )
        translation_state = (
            "unavailable"
            if unavailable
            else ("fresh_cache" if had_cached_translation else "live")
        )
        prepared = replace(
            record,
            title_zh=title_zh,
            translation_state=translation_state,
        )
        _task_checkpoint(context)
        try:
            measurement = measure_apod_page(
                apod=prepared,
                title_zh=title_zh,
                translation_unavailable=unavailable,
            )
        except ApodPageLayoutError:
            if title_zh is None:
                raise
            _task_checkpoint(context)
            try:
                _translation_cache_path(
                    paths, record.date, record.title_en
                ).unlink(missing_ok=True)
            except OSError:
                pass
            unavailable = True
            prepared = replace(
                record,
                title_zh=None,
                translation_state="unavailable",
            )
            measurement = measure_apod_page(
                apod=prepared,
                title_zh=None,
                translation_unavailable=True,
            )
        _task_checkpoint(context)
        return prepared, unavailable, measurement

    def _resolve_record_media(
        self, record, *, paths, photo_size, context
    ) -> tuple[ApodRecord, Image.Image]:
        urls = _media_url_candidates(record)
        if not urls:
            raise ApodMediaUnavailable("APOD record does not provide image media")
        namespace = self.managed_cache_namespace(paths.media)
        for media_url in urls:
            digest = hashlib.sha256(media_url.encode("utf-8")).hexdigest()
            try:
                _task_checkpoint(context)
                blob_path = _resolve_media_url_blob(
                    plugin=self,
                    media_url=media_url,
                    paths=paths,
                    minimum_size=photo_size,
                    context=context,
                )
                source_image = _decode_media_blob(
                    blob_path=blob_path,
                    photo_size=photo_size,
                )
                _task_checkpoint(context)
                return (
                    replace(
                        record,
                        image_url=media_url,
                        image_cache_key=digest,
                    ),
                    source_image,
                )
            except _ABORT_EXCEPTIONS:
                raise
            except ApodMediaUnavailable:
                _remove_media_blob(namespace, digest)
                continue
        raise ApodMediaUnavailable("No decodable APOD image media is available")

    def _resolve_provisional_random(
        self,
        *,
        state,
        http,
        api_key,
        selection,
        paths,
        device_config,
        context,
    ):
        initial_record = state.requested_record
        reached_initial = False
        for candidate_date in selection.candidate_dates[
            :RANDOM_APOD_MAX_ATTEMPTS
        ]:
            _task_checkpoint(context)
            if not reached_initial:
                if candidate_date != initial_record.date:
                    continue
                candidate = initial_record
                reached_initial = True
            else:
                try:
                    candidate = _fetch_apod_record(
                        http=http,
                        api_key=api_key,
                        requested_date=candidate_date,
                        context=context,
                    )
                except _ABORT_EXCEPTIONS:
                    raise
                except RuntimeError:
                    continue
                if candidate.media_type != "image":
                    continue
                candidate = _bind_record_to_selection(
                    candidate,
                    selection=selection,
                )

            try:
                prepared, unavailable, measurement = self._prepare_record(
                    candidate,
                    paths=paths,
                    device_config=device_config,
                    context=context,
                )
                displayed, source_image = self._resolve_record_media(
                    prepared,
                    paths=paths,
                    photo_size=measurement.photo_size,
                    context=context,
                )
            except _ABORT_EXCEPTIONS:
                raise
            except (ApodMediaUnavailable, ApodPageLayoutError):
                continue

            _task_checkpoint(context)
            selection = _resolved_selection(
                paths,
                selection,
                displayed.date,
                context=context,
            )
            _task_checkpoint(context)
            state = ApodDisplayState(
                selection_fingerprint=selection.fingerprint,
                device_day=selection.device_day,
                requested_date=displayed.date,
                requested_record=displayed,
                display_record=displayed,
                fallback_reason=None,
                provisional_media=False,
            )
            return (
                displayed,
                source_image,
                unavailable,
                measurement,
                state,
                selection,
            )

        raise RuntimeError("No usable APOD image found after five random dates")

    def _resolve_media_fallback(
        self,
        *,
        state,
        http,
        api_key,
        selection,
        paths,
        device_config,
        context,
    ):
        requested = state.requested_record
        requested_day = date.fromisoformat(requested.date)
        fallback_reason = (
            "current_media_unavailable"
            if requested.media_type == "image"
            else "video"
        )
        for offset in range(1, 8):
            _task_checkpoint(context)
            candidate_date = (requested_day - timedelta(days=offset)).isoformat()
            if (
                state.display_record.date == candidate_date
                and state.display_record.media_type == "image"
            ):
                candidate = state.display_record
            else:
                try:
                    candidate = _fetch_apod_record(
                        http=http,
                        api_key=api_key,
                        requested_date=candidate_date,
                        context=context,
                    )
                except _ABORT_EXCEPTIONS:
                    raise
                except RuntimeError:
                    continue
                candidate = _bind_record_to_selection(
                    candidate,
                    selection=selection,
                )
            if candidate.media_type != "image":
                continue
            candidate = replace(
                candidate,
                selection_key=selection.fingerprint,
                requested_device_date=selection.device_day,
                warning=_fallback_warning(candidate_date),
            )
            try:
                prepared, unavailable, measurement = self._prepare_record(
                    candidate,
                    paths=paths,
                    device_config=device_config,
                    context=context,
                )
                displayed, source_image = self._resolve_record_media(
                    prepared,
                    paths=paths,
                    photo_size=measurement.photo_size,
                    context=context,
                )
            except _ABORT_EXCEPTIONS:
                raise
            except (ApodMediaUnavailable, ApodPageLayoutError):
                continue
            new_state = replace(
                state,
                requested_record=requested,
                display_record=displayed,
                fallback_reason=fallback_reason,
                provisional_media=(
                    fallback_reason == "current_media_unavailable"
                ),
            )
            _task_checkpoint(context)
            return displayed, source_image, unavailable, measurement, new_state

        raise ApodMediaUnavailable(
            "No usable APOD image was available within seven prior days"
        )

    def _write_apod_context(
        self,
        record,
        weather,
        provenance,
        generated_at,
        *,
        context=None,
    ):
        summary = f"NASA APOD: {record.title_en} ({record.date})"
        facts = [
            {"label": "date", "value": record.date},
            {
                "label": "space_weather",
                "value": provenance.value,
            },
        ]
        if record.copyright:
            facts.append({"label": "credit", "value": record.copyright[:80]})
        if record.warning:
            facts.append({"label": "warning", "value": record.warning})
        current_kp = getattr(weather, "current_kp", None)
        if current_kp:
            facts.append(
                {"label": "kp", "value": str(current_kp.get("value"))}
            )
        _task_checkpoint(context)
        write_context(
            "apod",
            {
                "kind": "space_weather_photo",
                "source": "NASA APOD + NOAA SWPC",
                "summary": summary[:180],
                "facts": facts,
                "items": [
                    {
                        "title": record.title_en[:120],
                        "title_zh": (record.title_zh or "")[:120],
                        "date": record.date,
                        "summary": record.explanation[:160],
                    }
                ],
            },
            generated_at=generated_at,
            ttl_seconds=24 * 60 * 60,
        )
