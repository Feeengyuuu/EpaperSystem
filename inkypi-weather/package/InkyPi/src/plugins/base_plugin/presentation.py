"""Cache-safe presentation refresh contracts shared by plugin implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from PIL import Image

from runtime.runtime_state import PresentationCommitReceipt


_LOWERCASE_HEX = frozenset("0123456789abcdef")


def _validated_request_id(value) -> str:
    if not isinstance(value, str):
        raise TypeError("request_id must be a string")
    if len(value) != 32 or any(character not in _LOWERCASE_HEX for character in value):
        raise ValueError("request_id must be 32 lowercase hexadecimal characters")
    return value


def _validated_iso_timestamp(value, field_name) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    return normalized


def _validated_non_empty_text(value, field_name) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


class PresentationMode(str, Enum):
    NO_CHANGE = "no_change"
    PREPARED_BANK = "prepared_bank"
    LEGACY_ASYNC = "legacy_async"


@dataclass(frozen=True)
class PresentationRequestContext:
    request_id: str
    requested_at: str
    origin_display_commit_id: str
    last_receipt: PresentationCommitReceipt | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _validated_request_id(self.request_id))
        object.__setattr__(
            self,
            "requested_at",
            _validated_iso_timestamp(self.requested_at, "requested_at"),
        )
        object.__setattr__(
            self,
            "origin_display_commit_id",
            _validated_non_empty_text(
                self.origin_display_commit_id,
                "origin_display_commit_id",
            ),
        )
        if self.last_receipt is not None and not isinstance(
            self.last_receipt,
            PresentationCommitReceipt,
        ):
            raise TypeError("last_receipt must be a PresentationCommitReceipt or None")


@dataclass(frozen=True)
class PresentationPreparation:
    request_id: str
    image: Image.Image | None = field(repr=False, compare=False, hash=False)
    changed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _validated_request_id(self.request_id))
        if type(self.changed) is not bool:
            raise TypeError("changed must be a boolean")
        if self.image is not None and not isinstance(self.image, Image.Image):
            raise TypeError("image must be a PIL Image or None")
        if self.changed and self.image is None:
            raise ValueError("image is required when changed is true")
        if not self.changed and self.image is not None:
            raise ValueError("image must be None when changed is false")
        if self.image is not None:
            object.__setattr__(self, "image", self.image.copy())
