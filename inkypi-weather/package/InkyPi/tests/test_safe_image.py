import ast
import base64
import io
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image, JpegImagePlugin

from src.utils import safe_image
from src.utils.safe_image import ImageLimitError, ImageLimits, safe_open_image


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _png_chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _header_only_png(width, height):
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IEND", b"")


def _encoded_image(format_name="PNG", *, size=(3, 2), color="red", exif=None):
    output = io.BytesIO()
    image = Image.new("RGB", size, color)
    save_kwargs = {"exif": exif} if exif is not None else {}
    image.save(output, format=format_name, **save_kwargs)
    return output.getvalue()


def _write_two_frame_gif(path):
    first = Image.new("RGB", (2, 2), "red")
    second = Image.new("RGB", (2, 2), "blue")
    first.save(path, save_all=True, append_images=[second], duration=10, loop=0)


def test_image_limits_use_task_four_defaults():
    limits = ImageLimits()

    assert limits.max_bytes == 25 * 1024 * 1024
    assert limits.max_width == 8192
    assert limits.max_height == 8192
    assert limits.max_pixels == 8_000_000
    assert limits.allowed_formats == frozenset({"JPEG", "PNG", "WEBP", "GIF"})


def test_safe_open_rejects_byte_limit_before_pillow_open(monkeypatch):
    monkeypatch.setattr(Image, "open", lambda _source: pytest.fail("Pillow open called"))

    with pytest.raises(ImageLimitError, match="byte"):
        safe_open_image(b"12345", limits=ImageLimits(max_bytes=4))


def test_safe_open_rejects_oversized_bytearray_before_copying():
    class CopyGuardBytearray(bytearray):
        def __bytes__(self):
            pytest.fail("oversized bytearray copied before its size was checked")

    with pytest.raises(ImageLimitError, match="byte"):
        safe_open_image(CopyGuardBytearray(b"12345"), limits=ImageLimits(max_bytes=4))


def test_safe_open_uses_memoryview_nbytes_before_copying():
    source = memoryview(bytearray(b"123456")).cast("B", shape=(2, 3))

    with pytest.raises(ImageLimitError, match="byte"):
        safe_open_image(source, limits=ImageLimits(max_bytes=4))


def test_safe_open_rejects_large_dimensions_before_load(monkeypatch):
    monkeypatch.setattr(Image.Image, "load", lambda _self: pytest.fail("load called"))

    with pytest.raises(ImageLimitError, match="dimension"):
        safe_open_image(io.BytesIO(_header_only_png(width=9000, height=10)))


def test_safe_open_rejects_large_pixel_count_before_load(monkeypatch):
    monkeypatch.setattr(Image.Image, "load", lambda _self: pytest.fail("load called"))

    with pytest.raises(ImageLimitError, match="pixel"):
        safe_open_image(io.BytesIO(_header_only_png(width=4000, height=3000)))


def test_safe_open_rejects_disallowed_format_before_load(monkeypatch):
    payload = _encoded_image("BMP")
    monkeypatch.setattr(Image.Image, "load", lambda _self: pytest.fail("load called"))

    with pytest.raises(ImageLimitError, match="format"):
        safe_open_image(payload)


def test_decompression_bomb_warning_becomes_image_limit_error(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 3)

    with pytest.raises(ImageLimitError, match="decompression"):
        safe_open_image(_encoded_image("PNG", size=(2, 2)))


def test_safe_open_returns_detached_first_frame(tmp_path):
    path = tmp_path / "animated.gif"
    _write_two_frame_gif(path)

    result = safe_open_image(path)
    path.unlink()

    assert result.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
    assert getattr(result, "n_frames", 1) == 1


def test_safe_open_applies_exif_orientation_and_detaches_from_caller_stream():
    exif = Image.Exif()
    exif[274] = 6
    source = io.BytesIO(_encoded_image("JPEG", size=(3, 2), exif=exif))

    result = safe_open_image(source)

    assert source.closed is False
    assert source.tell() == 0
    source.close()
    assert result.size == (2, 3)
    assert result.getpixel((0, 0)) is not None


class _NonSeekableStream:
    def __init__(self, payload):
        self._payload = io.BytesIO(payload)
        self.closed = False

    def read(self, size=-1):
        return self._payload.read(size)

    def close(self):
        self.closed = True
        self._payload.close()


def test_safe_open_spools_non_seekable_stream_without_taking_ownership():
    source = _NonSeekableStream(_encoded_image("PNG"))

    result = safe_open_image(source)

    assert result.size == (3, 2)
    assert source.closed is False


class _StreamingResponse:
    def __init__(self, chunks, *, content_length=None):
        self._chunks = tuple(chunks)
        self.closed = False
        self.status_checked = False
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    @property
    def content(self):
        pytest.fail("response.content must not be materialized for image downloads")

    def raise_for_status(self):
        self.status_checked = True

    def iter_content(self, chunk_size=64 * 1024):
        assert chunk_size > 0
        yield from self._chunks

    def close(self):
        self.closed = True


def test_safe_open_response_streams_with_limit_and_closes_response():
    payload = _encoded_image("PNG")
    response = _StreamingResponse((payload[:7], payload[7:]))

    result = safe_image.safe_open_image_response(response)

    assert result.size == (3, 2)
    assert response.status_checked is True
    assert response.closed is True


def test_safe_open_response_rejects_oversized_stream_before_pillow(monkeypatch):
    response = _StreamingResponse((b"123", b"456"))
    monkeypatch.setattr(Image, "open", lambda _source: pytest.fail("Pillow open called"))

    with pytest.raises(ImageLimitError, match="byte"):
        safe_image.safe_open_image_response(response, limits=ImageLimits(max_bytes=4))

    assert response.closed is True


def test_read_limited_response_bytes_streams_and_closes_without_content_access():
    response = _StreamingResponse((b"123", b"456"))

    payload = safe_image.read_limited_response_bytes(response, max_bytes=6)

    assert payload == b"123456"
    assert response.status_checked is True
    assert response.closed is True


def test_safe_open_base64_rejects_encoded_oversize_before_decoding(monkeypatch):
    monkeypatch.setattr(
        base64,
        "b64decode",
        lambda _value, **_kwargs: pytest.fail("base64 decoded before size check"),
    )

    with pytest.raises(ImageLimitError, match="base64"):
        safe_image.safe_open_base64_image("A" * 9, limits=ImageLimits(max_bytes=4))


def test_safe_open_base64_accepts_memoryview_data_uri():
    encoded = base64.b64encode(_encoded_image("PNG"))
    source = memoryview(b"data:image/png;base64," + encoded)

    result = safe_image.safe_open_base64_image(source)

    assert result.size == (3, 2)


def test_safe_open_base64_rejects_invalid_alphabet_with_clear_error():
    with pytest.raises(ImageLimitError, match="invalid base64"):
        safe_image.safe_open_base64_image("not-valid-***")


def test_safe_open_applies_jpeg_draft_before_full_load(monkeypatch):
    calls = []
    original_draft = JpegImagePlugin.JpegImageFile.draft

    def recording_draft(self, mode, size):
        calls.append((mode, size))
        return original_draft(self, mode, size)

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", recording_draft)

    result = safe_open_image(
        _encoded_image("JPEG", size=(64, 48)),
        draft_size=(16, 12),
    )

    assert calls == [("RGB", (16, 12))]
    assert result.size[0] <= 64
    assert result.getpixel((0, 0)) is not None


def test_safe_open_validates_header_before_jpeg_draft(monkeypatch):
    monkeypatch.setattr(
        JpegImagePlugin.JpegImageFile,
        "draft",
        lambda *_args, **_kwargs: pytest.fail("draft called before header validation"),
    )

    with pytest.raises(ImageLimitError, match="dimension"):
        safe_open_image(
            _encoded_image("JPEG", size=(32, 16)),
            limits=ImageLimits(max_width=16),
            draft_size=(8, 4),
        )


def _call_name(node):
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _contains_response_materialization(node):
    return any(
        isinstance(candidate, ast.Attribute) and candidate.attr in {"content", "raw"}
        for candidate in ast.walk(node)
    )


def _contains_call(node, names):
    return any(
        isinstance(candidate, ast.Call) and _call_name(candidate.func) in names
        for candidate in ast.walk(node)
    )


def _is_risky_path_expression(node, function_name):
    risky_names = {"tmp_path", "cached_image", "stale_image", "data_path"}
    if any(isinstance(candidate, ast.Name) and candidate.id in risky_names for candidate in ast.walk(node)):
        return True
    if any(
        isinstance(candidate, ast.Subscript)
        and isinstance(candidate.value, ast.Name)
        and candidate.value.id == "cache"
        for candidate in ast.walk(node)
    ):
        return True
    return "cache" in function_name.lower() and not isinstance(node, ast.Constant)


def test_all_plugins_route_untrusted_images_without_materializing_responses():
    violations = []
    plugin_root = PROJECT_ROOT / "src" / "plugins"
    for path in plugin_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for function in (
            node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            if path.parent.name == "newspaper" and function.name == "_render_pdf_first_page":
                # Newspaper PDF rendering has its own bounded PDF pipeline.
                continue
            streamed_response_names = set()
            for assignment in ast.walk(function):
                if not isinstance(assignment, ast.Assign) or not isinstance(assignment.value, ast.Call):
                    continue
                request_call = assignment.value
                if not isinstance(request_call.func, ast.Attribute) or request_call.func.attr != "get":
                    continue
                has_stream = any(
                    keyword.arg == "stream"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in request_call.keywords
                )
                if has_stream:
                    streamed_response_names.update(
                        target.id for target in assignment.targets if isinstance(target, ast.Name)
                    )
            image_function = any(
                token in function.name.lower()
                for token in ("image", "logo", "avatar", "cover", "photo", "art", "thumbnail", "icon", "map")
            )
            for node in ast.walk(function):
                if isinstance(node, ast.Return) and node.value is not None:
                    if image_function and _contains_response_materialization(node.value):
                        violations.append((path.relative_to(PROJECT_ROOT), node.lineno, "returned response body"))
                    continue
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                call_name = _call_name(node.func)
                image_source = node.args[0]
                if call_name == "Image.open" and (
                    _contains_call(image_source, {"BytesIO", "base64.b64decode"})
                    or _contains_response_materialization(image_source)
                    or _is_risky_path_expression(image_source, function.name)
                ):
                    violations.append((path.relative_to(PROJECT_ROOT), node.lineno, "direct Pillow decode"))
                if call_name == "safe_open_image" and (
                    _contains_response_materialization(image_source)
                    or _contains_call(image_source, {"base64.b64decode"})
                ):
                    violations.append((path.relative_to(PROJECT_ROOT), node.lineno, "pre-materialized safe decode"))
                if call_name in {"safe_open_image_response", "read_limited_response_bytes"}:
                    if not isinstance(image_source, ast.Name) or image_source.id not in streamed_response_names:
                        violations.append((path.relative_to(PROJECT_ROOT), node.lineno, "non-streaming response decode"))

    assert violations == [], "\n".join(f"{path}:{line}: {reason}" for path, line, reason in violations)
