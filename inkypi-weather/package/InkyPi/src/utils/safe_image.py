"""Bounded image decoding for untrusted plugin inputs.

The returned image is fully loaded and detached from its input.  Callers retain
ownership of file-like inputs; seekable streams are restored to their original
position and non-seekable streams are left open.
"""

import base64
import binascii
from contextlib import ExitStack
from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import tempfile
import warnings

from PIL import Image, ImageOps


class ImageLimitError(ValueError):
    """An image exceeds a configured decode boundary."""


@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int = 25 * 1024 * 1024
    max_width: int = 8192
    max_height: int = 8192
    max_pixels: int = 8_000_000
    allowed_formats: frozenset[str] = frozenset({"JPEG", "PNG", "WEBP", "GIF"})

    def __post_init__(self):
        for field_name in ("max_bytes", "max_width", "max_height", "max_pixels"):
            value = getattr(self, field_name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        normalized_formats = frozenset(str(item).strip().upper() for item in self.allowed_formats)
        if not normalized_formats or "" in normalized_formats:
            raise ValueError("allowed_formats must contain non-empty format names")
        object.__setattr__(self, "allowed_formats", normalized_formats)


def _restore_position(source, position):
    try:
        source.seek(position)
    except (OSError, ValueError):
        pass


def _bounded_stream_copy(source, target, max_bytes):
    total = 0
    while True:
        chunk = source.read(64 * 1024)
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("image stream read() must return bytes")
        total += len(chunk)
        if total > max_bytes:
            raise ImageLimitError(f"image byte size exceeds limit of {max_bytes}")
        target.write(chunk)
    target.seek(0)


def _bounded_chunks_copy(chunks, target, max_bytes):
    total = 0
    for chunk in chunks:
        if not chunk:
            continue
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("image response chunks must be bytes")
        total += len(chunk)
        if total > max_bytes:
            raise ImageLimitError(f"image byte size exceeds limit of {max_bytes}")
        target.write(chunk)
    target.seek(0)


def _prepare_source(source, limits, stack):
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        if path.stat().st_size > limits.max_bytes:
            raise ImageLimitError(f"image byte size exceeds limit of {limits.max_bytes}")
        return path

    if isinstance(source, (bytes, bytearray, memoryview)):
        byte_size = source.nbytes if isinstance(source, memoryview) else len(source)
        if byte_size > limits.max_bytes:
            raise ImageLimitError(f"image byte size exceeds limit of {limits.max_bytes}")
        return stack.enter_context(BytesIO(source))

    if not callable(getattr(source, "read", None)):
        raise TypeError("image source must be bytes, a path, or a binary file-like object")

    try:
        original_position = source.tell()
        source.seek(0, os.SEEK_END)
        byte_size = source.tell()
        source.seek(original_position)
    except (AttributeError, OSError, TypeError, ValueError):
        spool = stack.enter_context(
            tempfile.SpooledTemporaryFile(
                max_size=min(limits.max_bytes, 1024 * 1024),
                mode="w+b",
            )
        )
        _bounded_stream_copy(source, spool, limits.max_bytes)
        return spool

    if byte_size > limits.max_bytes:
        raise ImageLimitError(f"image byte size exceeds limit of {limits.max_bytes}")
    stack.callback(_restore_position, source, original_position)
    source.seek(0)
    return source


def _validate_header(opened, limits):
    width, height = opened.size
    if width <= 0 or height <= 0:
        raise ImageLimitError("image dimension must be positive")
    if width > limits.max_width or height > limits.max_height:
        raise ImageLimitError("image dimension exceeds limit")
    if width * height > limits.max_pixels:
        raise ImageLimitError("image pixel count exceeds limit")
    image_format = str(opened.format or "").upper()
    if image_format not in limits.allowed_formats:
        raise ImageLimitError(f"image format is not allowed: {image_format or 'unknown'}")


def safe_open_image(
    source: object,
    *,
    limits: ImageLimits = ImageLimits(),
    first_frame: bool = True,
    draft_size: tuple[int, int] | None = None,
) -> Image.Image:
    """Decode one bounded image and return an independent, fully loaded copy."""

    if not isinstance(limits, ImageLimits):
        raise TypeError("limits must be an ImageLimits instance")
    if draft_size is not None:
        if (
            not isinstance(draft_size, tuple)
            or len(draft_size) != 2
            or any(type(value) is not int or value <= 0 for value in draft_size)
        ):
            raise ValueError("draft_size must be a pair of positive integers")

    try:
        with ExitStack() as stack:
            prepared_source = _prepare_source(source, limits, stack)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                opened = stack.enter_context(Image.open(prepared_source))
                _validate_header(opened, limits)
                if first_frame:
                    opened.seek(0)
                if draft_size is not None and str(opened.format or "").upper() == "JPEG":
                    opened.draft("RGB", draft_size)
                normalized = ImageOps.exif_transpose(opened)
                if normalized is not opened:
                    stack.callback(normalized.close)
                normalized.load()
                detached = normalized.copy()
                detached.load()
                return detached
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ImageLimitError("image decompression bomb protection triggered") from error


def safe_open_image_response(
    response: object,
    *,
    limits: ImageLimits = ImageLimits(),
    first_frame: bool = True,
    draft_size: tuple[int, int] | None = None,
) -> Image.Image:
    """Consume and close one owned streaming HTTP response as a bounded image."""

    if not isinstance(limits, ImageLimits):
        raise TypeError("limits must be an ImageLimits instance")

    close = getattr(response, "close", None)
    try:
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()

        headers = getattr(response, "headers", {}) or {}
        content_length = headers.get("Content-Length") or headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError):
                declared_size = None
            if declared_size is not None and declared_size > limits.max_bytes:
                raise ImageLimitError(f"image byte size exceeds limit of {limits.max_bytes}")

        iter_content = getattr(response, "iter_content", None)
        if not callable(iter_content):
            raise TypeError("image response must provide iter_content()")

        with tempfile.SpooledTemporaryFile(
            max_size=min(limits.max_bytes, 1024 * 1024),
            mode="w+b",
        ) as spool:
            _bounded_chunks_copy(iter_content(chunk_size=64 * 1024), spool, limits.max_bytes)
            return safe_open_image(
                spool,
                limits=limits,
                first_frame=first_frame,
                draft_size=draft_size,
            )
    finally:
        if callable(close):
            close()


def read_limited_response_bytes(response: object, *, max_bytes: int) -> bytes:
    """Consume and close one owned streaming response after enforcing a byte cap."""

    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")

    close = getattr(response, "close", None)
    try:
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()

        headers = getattr(response, "headers", {}) or {}
        content_length = headers.get("Content-Length") or headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError):
                declared_size = None
            if declared_size is not None and declared_size > max_bytes:
                raise ImageLimitError(f"image byte size exceeds limit of {max_bytes}")

        iter_content = getattr(response, "iter_content", None)
        if not callable(iter_content):
            raise TypeError("image response must provide iter_content()")

        target = BytesIO()
        _bounded_chunks_copy(iter_content(chunk_size=64 * 1024), target, max_bytes)
        return target.getvalue()
    finally:
        if callable(close):
            close()


def safe_open_base64_image(
    encoded: str | bytes | bytearray | memoryview,
    *,
    limits: ImageLimits = ImageLimits(),
    first_frame: bool = True,
    draft_size: tuple[int, int] | None = None,
) -> Image.Image:
    """Reject oversized base64 before decoding, then safely decode the image."""

    if not isinstance(limits, ImageLimits):
        raise TypeError("limits must be an ImageLimits instance")
    if not isinstance(encoded, (str, bytes, bytearray, memoryview)):
        raise TypeError("encoded image must be base64 text or bytes")

    payload = encoded
    marker = "base64," if isinstance(payload, str) else b"base64,"
    if isinstance(payload, memoryview):
        if not payload.contiguous:
            raise TypeError("encoded image memoryview must be contiguous")
        if payload.ndim != 1 or payload.itemsize != 1:
            payload = payload.cast("B")
        marker_index = bytes(payload[:256]).find(marker)
    else:
        marker_index = payload.find(marker)
    if marker_index >= 0:
        payload = payload[marker_index + len(marker):]

    whitespace = {" ", "\t", "\r", "\n"} if isinstance(payload, str) else {9, 10, 13, 32}
    encoded_length = sum(value not in whitespace for value in payload)
    padding = 0
    for value in reversed(payload):
        if value in whitespace:
            continue
        equals = value == "=" if isinstance(payload, str) else value == ord("=")
        if equals and padding < 2:
            padding += 1
            continue
        break
    decoded_upper_bound = ((encoded_length + 3) // 4) * 3 - padding
    if decoded_upper_bound > limits.max_bytes:
        raise ImageLimitError(f"base64 image exceeds decoded byte limit of {limits.max_bytes}")

    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ImageLimitError("invalid base64 image data") from error
    return safe_open_image(
        decoded,
        limits=limits,
        first_frame=first_frame,
        draft_size=draft_size,
    )
