"""Bound Flask request parsing and validate uploads before publishing them."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import warnings

from flask import Flask, jsonify, request
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge


MIB = 1024 * 1024
MAX_REQUEST_BYTES = 8 * MIB
MAX_UPLOAD_BYTES = 5 * MIB
MAX_FORM_MEMORY_BYTES = 500_000
MAX_FORM_PARTS = 128
MAX_IMAGE_PIXELS = 8_000_000
MAX_IMAGE_DIMENSION = 8192
UPLOAD_CHUNK_BYTES = 64 * 1024
WAITRESS_MAX_REQUEST_BODY_BYTES = MAX_REQUEST_BYTES + UPLOAD_CHUNK_BYTES

GLOBAL_UPLOAD_EXTENSIONS = frozenset(
    {"avif", "csv", "gif", "heic", "heif", "jpeg", "jpg", "pdf", "png", "webp"}
)

_IMAGE_EXTENSIONS = frozenset(
    {"avif", "gif", "heic", "heif", "jpeg", "jpg", "png", "webp"}
)
_IMAGE_FORMAT_EXTENSIONS = {
    "AVIF": frozenset({"avif"}),
    "GIF": frozenset({"gif"}),
    "HEIC": frozenset({"heic", "heif"}),
    "HEIF": frozenset({"heic", "heif"}),
    "JPEG": frozenset({"jpeg", "jpg"}),
    "PNG": frozenset({"png"}),
    "WEBP": frozenset({"webp"}),
}
_IMAGE_FORMAT_MIME_TYPES = {
    "AVIF": frozenset({"image/avif"}),
    "GIF": frozenset({"image/gif"}),
    "HEIC": frozenset({"image/heic", "image/heif", "image/heic-sequence"}),
    "HEIF": frozenset({"image/heic", "image/heif", "image/heif-sequence"}),
    "JPEG": frozenset({"image/jpeg", "image/jpg", "image/pjpeg"}),
    "PNG": frozenset({"image/png", "image/x-png"}),
    "WEBP": frozenset({"image/webp"}),
}
_PDF_MIME_TYPES = frozenset({"application/pdf"})
_CSV_MIME_TYPES = frozenset(
    {"application/csv", "application/vnd.ms-excel", "text/csv", "text/plain"}
)
_GENERIC_MIME_TYPES = frozenset({"", "application/octet-stream"})


class UploadPolicyError(ValueError):
    """An upload policy attempted to exceed an application-wide ceiling."""


class UploadError(ValueError):
    """Base class for stable, user-facing upload validation failures."""

    error_code = "invalid_upload"
    status_code = 400


class UploadTooLarge(UploadError):
    error_code = "upload_too_large"
    status_code = 413


class UploadTotalTooLarge(UploadError):
    error_code = "upload_total_too_large"
    status_code = 413


class UploadExtensionNotAllowed(UploadError):
    error_code = "upload_extension_not_allowed"


class UploadContentMismatch(UploadError):
    error_code = "upload_content_mismatch"


class UploadFilenameRequired(UploadError):
    error_code = "upload_filename_required"


class UploadImageTooLarge(UploadError):
    error_code = "upload_image_too_large"
    status_code = 413


class UploadCommitUncertainError(OSError):
    """The upload was replaced but its directory entry may not be durable."""

    def __init__(self, target: Path) -> None:
        self.target = target
        self.target_replaced = True
        super().__init__(f"upload commit durability is uncertain for {target}")


@dataclass(frozen=True)
class UploadPolicy:
    """Per-plugin upload bounds that cannot exceed the global request policy."""

    max_file_bytes: int = MAX_UPLOAD_BYTES
    max_total_bytes: int = MAX_REQUEST_BYTES
    max_image_pixels: int = MAX_IMAGE_PIXELS
    allowed_extensions: frozenset[str] = GLOBAL_UPLOAD_EXTENSIONS

    def __post_init__(self) -> None:
        extensions = frozenset(
            str(extension).lower().lstrip(".") for extension in self.allowed_extensions
        )
        object.__setattr__(self, "allowed_extensions", extensions)

        numeric_limits = {
            "max_file_bytes": (self.max_file_bytes, MAX_UPLOAD_BYTES),
            "max_total_bytes": (self.max_total_bytes, MAX_REQUEST_BYTES),
            "max_image_pixels": (self.max_image_pixels, MAX_IMAGE_PIXELS),
        }
        for name, (value, ceiling) in numeric_limits.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise UploadPolicyError(f"{name} must be a positive integer")
            if value > ceiling:
                raise UploadPolicyError(f"{name} may not exceed the global ceiling")
        if not extensions:
            raise UploadPolicyError("allowed_extensions must not be empty")
        if not extensions.issubset(GLOBAL_UPLOAD_EXTENSIONS):
            raise UploadPolicyError("allowed_extensions may not expand the global allowlist")


def _error_payload(error_code: str, message: str) -> dict[str, object]:
    return {
        "success": False,
        "error": message,
        "error_code": error_code,
    }


def _upload_extension(upload: object, destination: Path | None, policy: UploadPolicy) -> str:
    filename = Path(str(getattr(upload, "filename", "") or "")).name
    suffix = Path(filename).suffix if filename else (destination.suffix if destination else "")
    extension = suffix.lower().lstrip(".")
    if not extension or extension not in policy.allowed_extensions:
        raise UploadExtensionNotAllowed("The uploaded file type is not allowed")
    return extension


def _normalized_mime(upload: object) -> str:
    raw_mime = getattr(upload, "mimetype", None) or getattr(upload, "content_type", None) or ""
    return str(raw_mime).split(";", 1)[0].strip().lower()


def is_empty_upload_placeholder(upload: object) -> bool:
    """Accept only the zero-byte, blank-name placeholder emitted by browsers."""

    if str(getattr(upload, "filename", "") or ""):
        return False
    stream = getattr(upload, "stream", None)
    try:
        stream.seek(0)
        has_content = bool(stream.read(1))
        stream.seek(0)
    except (AttributeError, OSError, ValueError) as error:
        raise UploadFilenameRequired(
            "An uploaded file without a filename could not be validated"
        ) from error
    if has_content:
        raise UploadFilenameRequired("Uploaded files must include a filename")
    return True


def _validate_declared_mime(actual_types: frozenset[str], declared_type: str) -> None:
    if declared_type not in _GENERIC_MIME_TYPES and declared_type not in actual_types:
        raise UploadContentMismatch("The uploaded content does not match its media type")


def _validate_image(source, extension: str, declared_type: str, policy: UploadPolicy) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                image_format = str(image.format or "").upper()
                width, height = image.size
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > policy.max_image_pixels
                ):
                    raise UploadImageTooLarge("The uploaded image exceeds the pixel limit")
                expected_extensions = _IMAGE_FORMAT_EXTENSIONS.get(image_format)
                if expected_extensions is None or extension not in expected_extensions:
                    raise UploadContentMismatch(
                        "The uploaded content does not match its file extension"
                    )
                _validate_declared_mime(
                    _IMAGE_FORMAT_MIME_TYPES[image_format],
                    declared_type,
                )
                image.verify()
    except UploadError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise UploadImageTooLarge("The uploaded image exceeds the pixel limit") from error
    except (KeyError, OSError, UnidentifiedImageError, ValueError) as error:
        raise UploadContentMismatch("The uploaded image is invalid") from error


def _validate_pdf(source, declared_type: str) -> None:
    source.seek(0)
    payload = source.read()
    try:
        import fitz

        with fitz.open(stream=payload, filetype="pdf") as document:
            if document.needs_pass or document.page_count < 1:
                raise UploadContentMismatch("The uploaded PDF is invalid")
            document.load_page(0)
    except UploadError:
        raise
    except (ImportError, RuntimeError, TypeError, ValueError) as error:
        raise UploadContentMismatch("The uploaded PDF is invalid") from error
    _validate_declared_mime(_PDF_MIME_TYPES, declared_type)


def _validate_csv(source, declared_type: str) -> None:
    source.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        while True:
            chunk = source.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            if b"\x00" in chunk:
                raise UploadContentMismatch("The uploaded CSV contains binary data")
            decoder.decode(chunk, final=False)
        decoder.decode(b"", final=True)
    except UploadError:
        raise
    except UnicodeDecodeError as error:
        raise UploadContentMismatch("The uploaded CSV must be UTF-8 text") from error
    _validate_declared_mime(_CSV_MIME_TYPES, declared_type)


def _validate_content(source, extension: str, declared_type: str, policy: UploadPolicy) -> None:
    source.seek(0)
    if extension in _IMAGE_EXTENSIONS:
        _validate_image(source, extension, declared_type, policy)
    elif extension == "pdf":
        _validate_pdf(source, declared_type)
    elif extension == "csv":
        _validate_csv(source, declared_type)
    else:  # UploadPolicy validation makes this defensive branch unreachable.
        raise UploadExtensionNotAllowed("The uploaded file type is not allowed")


def _read_limited(
    source,
    sink,
    policy: UploadPolicy,
    *,
    bytes_already_written: int,
) -> int:
    written = 0
    while True:
        chunk = source.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise UploadContentMismatch("The upload stream must contain bytes")
        written += len(chunk)
        if written > policy.max_file_bytes:
            raise UploadTooLarge("The uploaded file exceeds the per-file limit")
        if bytes_already_written + written > policy.max_total_bytes:
            raise UploadTotalTooLarge("The uploaded files exceed the total upload limit")
        if sink is not None:
            sink.write(chunk)
    return written


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copy_limited_upload(
    upload: object,
    destination: str | os.PathLike[str],
    policy: UploadPolicy,
    *,
    bytes_already_written: int = 0,
) -> int:
    """Stream one upload to a same-directory temp and atomically publish it.

    The byte counters intentionally ignore client-provided ``Content-Length``.
    Existing destinations remain untouched unless every validation succeeds.
    """

    if bytes_already_written < 0:
        raise ValueError("bytes_already_written must not be negative")
    if bytes_already_written >= policy.max_total_bytes:
        raise UploadTotalTooLarge("The uploaded files exceed the total upload limit")

    destination_path = Path(destination)
    extension = _upload_extension(upload, destination_path, policy)
    source = getattr(upload, "stream", upload)
    if not hasattr(source, "read"):
        raise UploadContentMismatch("The upload does not expose a readable stream")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{destination_path.name}.",
            suffix=".upload",
            dir=destination_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)

        with temporary_path.open("w+b") as temporary:
            written = _read_limited(
                source,
                temporary,
                policy,
                bytes_already_written=bytes_already_written,
            )
            temporary.flush()
            os.fsync(temporary.fileno())
            _validate_content(temporary, extension, _normalized_mime(upload), policy)

        os.replace(temporary_path, destination_path)
        temporary_path = None
        try:
            _fsync_directory(destination_path.parent)
        except OSError as error:
            raise UploadCommitUncertainError(destination_path) from error
        return written
    except BaseException:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        raise


def _validate_request_uploads(policy: UploadPolicy) -> None:
    total_written = 0
    for _field_name, upload in request.files.items(multi=True):
        if is_empty_upload_placeholder(upload):
            continue
        extension = _upload_extension(upload, None, policy)
        stream = upload.stream
        try:
            stream.seek(0)
            written = _read_limited(
                stream,
                None,
                policy,
                bytes_already_written=total_written,
            )
            stream.seek(0)
            _validate_content(stream, extension, _normalized_mime(upload), policy)
            total_written += written
        finally:
            stream.seek(0)


def configure_request_limits(app: Flask) -> None:
    """Install application-wide Flask parser limits and stable JSON errors."""

    app.config["MAX_CONTENT_LENGTH"] = min(
        app.config.get("MAX_CONTENT_LENGTH") or MAX_REQUEST_BYTES,
        MAX_REQUEST_BYTES,
    )
    app.config["MAX_FORM_MEMORY_SIZE"] = min(
        app.config.get("MAX_FORM_MEMORY_SIZE") or MAX_FORM_MEMORY_BYTES,
        MAX_FORM_MEMORY_BYTES,
    )
    app.config["MAX_FORM_PARTS"] = min(
        app.config.get("MAX_FORM_PARTS") or MAX_FORM_PARTS,
        MAX_FORM_PARTS,
    )
    configured_policy = app.config.get("UPLOAD_POLICY")
    if configured_policy is None:
        app.config["UPLOAD_POLICY"] = UploadPolicy()
    elif not isinstance(configured_policy, UploadPolicy):
        raise UploadPolicyError("UPLOAD_POLICY must be an UploadPolicy")

    @app.before_request
    def enforce_request_and_upload_limits():
        content_length = request.content_length
        if content_length is not None and content_length > app.config["MAX_CONTENT_LENGTH"]:
            raise RequestEntityTooLarge()
        if request.mimetype == "multipart/form-data":
            _validate_request_uploads(app.config["UPLOAD_POLICY"])

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify(
            _error_payload("request_too_large", "The request exceeds the allowed size")
        ), 413

    @app.errorhandler(UploadError)
    def invalid_upload(error: UploadError):
        return jsonify(_error_payload(error.error_code, str(error))), error.status_code
