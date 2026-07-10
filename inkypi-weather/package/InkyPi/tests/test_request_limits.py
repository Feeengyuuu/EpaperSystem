from io import BytesIO
from pathlib import Path
import sys
from types import SimpleNamespace

from flask import Flask, request
from PIL import Image, ImageOps
import pytest
from werkzeug.datastructures import FileStorage
from werkzeug.datastructures import MultiDict


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security.request_limits import (
    MAX_FORM_MEMORY_BYTES,
    MAX_FORM_PARTS,
    MAX_REQUEST_BYTES,
    MAX_UPLOAD_BYTES,
    UploadContentMismatch,
    UploadImageTooLarge,
    UploadPolicy,
    UploadPolicyError,
    UploadTooLarge,
    UploadTotalTooLarge,
    copy_limited_upload,
    configure_request_limits,
)
import utils.app_utils as app_utils
from runtime_paths import RuntimePaths


def _image_bytes(format_name="PNG", size=(2, 2)):
    buffer = BytesIO()
    Image.new("RGB", size, "white").save(buffer, format=format_name)
    return buffer.getvalue()


def _upload(content, filename, content_type=None):
    return FileStorage(
        stream=BytesIO(content),
        filename=filename,
        content_type=content_type,
    )


def _oriented_jpeg_bytes():
    buffer = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (3, 2), "white").save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _valid_pdf_bytes():
    import fitz

    with fitz.open() as document:
        document.new_page()
        return document.tobytes()


def _expanding_jpeg_bytes():
    image = Image.effect_noise((512, 512), 100).convert("RGB")
    exif = Image.Exif()
    exif[274] = 6
    source = BytesIO()
    image.save(source, format="JPEG", quality=5, exif=exif)
    original = source.getvalue()

    normalized = BytesIO()
    with Image.open(BytesIO(original)) as opened:
        ImageOps.exif_transpose(opened).save(normalized, format="JPEG")
    assert len(normalized.getvalue()) > len(original)
    return original, len(normalized.getvalue())


@pytest.fixture
def limited_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    configure_request_limits(app)

    @app.post("/consume")
    def consume():
        request.get_data()
        return {"success": True}

    @app.post("/form")
    def form():
        return {"parts": len(request.form) + len(request.files)}

    @app.post("/upload")
    def upload():
        return {"filename": request.files["image"].filename}

    return app


def test_configure_request_limits_sets_global_caps(limited_app):
    assert limited_app.config["MAX_CONTENT_LENGTH"] == MAX_REQUEST_BYTES == 8 * 1024 * 1024
    assert limited_app.config["MAX_FORM_MEMORY_SIZE"] == MAX_FORM_MEMORY_BYTES
    assert limited_app.config["MAX_FORM_PARTS"] == MAX_FORM_PARTS == 128
    assert limited_app.config["UPLOAD_POLICY"].max_file_bytes == MAX_UPLOAD_BYTES


def test_request_larger_than_eight_mib_returns_json_413(limited_app):
    response = limited_app.test_client().post(
        "/consume",
        data=b"x" * (8 * 1024 * 1024 + 1),
        content_type="application/octet-stream",
    )

    assert response.status_code == 413
    assert response.get_json()["error_code"] == "request_too_large"


def test_more_than_128_multipart_parts_returns_json_413(limited_app):
    response = limited_app.test_client().post(
        "/form",
        data={f"field-{index}": str(index) for index in range(129)},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert response.get_json()["error_code"] == "request_too_large"


def test_empty_browser_file_placeholder_does_not_block_form(limited_app):
    response = limited_app.test_client().post(
        "/upload",
        data={"image": (BytesIO(b""), "", "application/octet-stream")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200


def test_empty_browser_placeholder_does_not_hide_valid_upload(limited_app):
    response = limited_app.test_client().post(
        "/upload",
        data=MultiDict(
            [
                ("image", (BytesIO(b""), "", "application/octet-stream")),
                ("image", (BytesIO(_image_bytes()), "image.png", "image/png")),
            ]
        ),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200


def test_nonempty_upload_with_blank_filename_is_rejected(limited_app):
    limited_app.config["UPLOAD_POLICY"] = UploadPolicy(
        max_file_bytes=1,
        max_total_bytes=1,
    )

    response = limited_app.test_client().post(
        "/upload",
        data={"image": (BytesIO(b"malicious"), "", "application/octet-stream")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["error_code"] == "upload_filename_required"


def test_partial_oversize_upload_is_removed(tmp_path):
    upload = _upload(
        b"x" * (5 * 1024 * 1024 + 1),
        "image.png",
        "image/png",
    )

    with pytest.raises(UploadTooLarge) as error:
        copy_limited_upload(upload, tmp_path / "image.png", UploadPolicy())

    assert error.value.error_code == "upload_too_large"
    assert list(tmp_path.iterdir()) == []


def test_copy_counts_total_independently_of_file_content_length(tmp_path):
    upload = FileStorage(
        stream=BytesIO(_image_bytes()),
        filename="image.png",
        content_type="image/png",
        content_length=1,
    )
    policy = UploadPolicy(max_total_bytes=32)

    with pytest.raises(UploadTotalTooLarge):
        copy_limited_upload(
            upload,
            tmp_path / "image.png",
            policy,
            bytes_already_written=24,
        )

    assert list(tmp_path.iterdir()) == []


def test_extension_and_detected_content_must_match(tmp_path):
    upload = _upload(_image_bytes("PNG"), "photo.jpg", "image/jpeg")

    with pytest.raises(UploadContentMismatch) as error:
        copy_limited_upload(upload, tmp_path / "photo.jpg", UploadPolicy())

    assert error.value.error_code == "upload_content_mismatch"
    assert list(tmp_path.iterdir()) == []


def test_client_mime_must_match_detected_content(tmp_path):
    upload = _upload(_image_bytes("PNG"), "photo.png", "text/plain")

    with pytest.raises(UploadContentMismatch):
        copy_limited_upload(upload, tmp_path / "photo.png", UploadPolicy())

    assert list(tmp_path.iterdir()) == []


def test_image_pixel_budget_is_enforced_without_partial_file(tmp_path):
    upload = _upload(_image_bytes(size=(11, 11)), "large.png", "image/png")

    with pytest.raises(UploadImageTooLarge) as error:
        copy_limited_upload(
            upload,
            tmp_path / "large.png",
            UploadPolicy(max_image_pixels=100),
        )

    assert error.value.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_default_image_policy_is_eight_megapixels():
    assert UploadPolicy().max_image_pixels == 8_000_000


def test_image_dimension_limit_rejects_before_pixel_load(tmp_path, monkeypatch):
    content = _image_bytes(size=(8193, 1))
    monkeypatch.setattr(
        Image.Image,
        "load",
        lambda _self: pytest.fail("oversize image was decoded"),
    )

    with pytest.raises(UploadImageTooLarge):
        copy_limited_upload(
            _upload(content, "wide.png", "image/png"),
            tmp_path / "wide.png",
            UploadPolicy(),
        )

    assert list(tmp_path.iterdir()) == []


def test_pdf_requires_parseable_document_structure(tmp_path):
    invalid = _upload(b"%PDF-not-a-real-document", "fake.pdf", "application/pdf")

    with pytest.raises(UploadContentMismatch):
        copy_limited_upload(invalid, tmp_path / "fake.pdf", UploadPolicy())

    valid = _valid_pdf_bytes()
    written = copy_limited_upload(
        _upload(valid, "valid.pdf", "application/pdf"),
        tmp_path / "valid.pdf",
        UploadPolicy(),
    )
    assert written == len(valid)


def test_save_only_upload_adapter_is_rejected_before_unbounded_write(tmp_path):
    content = _image_bytes()

    class SaveOnlyUpload:
        filename = "image.png"
        mimetype = "image/png"

        def save(self, destination):
            Path(destination).write_bytes(content)

    with pytest.raises(UploadContentMismatch, match="readable stream"):
        copy_limited_upload(
            SaveOnlyUpload(),
            tmp_path / "image.png",
            UploadPolicy(),
        )

    assert list(tmp_path.iterdir()) == []


def test_failed_validation_preserves_existing_destination(tmp_path):
    destination = tmp_path / "image.png"
    destination.write_bytes(b"existing")

    with pytest.raises(UploadContentMismatch):
        copy_limited_upload(
            _upload(b"not-an-image", "image.png", "image/png"),
            destination,
            UploadPolicy(),
        )

    assert destination.read_bytes() == b"existing"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["image.png"]


def test_valid_upload_is_atomically_published_and_fsynced(tmp_path, monkeypatch):
    from security import request_limits

    fsync_calls = []
    real_fsync = request_limits.os.fsync
    monkeypatch.setattr(
        request_limits.os,
        "fsync",
        lambda descriptor: (fsync_calls.append(descriptor), real_fsync(descriptor))[1],
    )
    content = _image_bytes()

    written = copy_limited_upload(
        _upload(content, "image.png", "image/png"),
        tmp_path / "image.png",
        UploadPolicy(),
    )

    assert written == len(content)
    assert (tmp_path / "image.png").read_bytes() == content
    assert fsync_calls
    assert sorted(path.name for path in tmp_path.iterdir()) == ["image.png"]


def test_directory_fsync_failure_reports_explicit_uncertain_commit(
    tmp_path,
    monkeypatch,
):
    from security import request_limits

    destination = tmp_path / "image.png"
    monkeypatch.setattr(
        request_limits,
        "_fsync_directory",
        lambda _directory: (_ for _ in ()).throw(OSError("fsync failed")),
    )

    with pytest.raises(OSError) as caught:
        copy_limited_upload(
            _upload(_image_bytes(), "image.png", "image/png"),
            destination,
            UploadPolicy(),
        )

    assert caught.value.target == destination
    assert caught.value.target_replaced is True
    assert destination.exists()


def test_plugin_policy_may_lower_but_not_raise_global_caps():
    lowered = UploadPolicy(
        max_file_bytes=1024,
        max_total_bytes=2048,
        max_image_pixels=1000,
        allowed_extensions=frozenset({"png"}),
    )
    assert lowered.max_file_bytes == 1024

    with pytest.raises(UploadPolicyError):
        UploadPolicy(max_file_bytes=MAX_UPLOAD_BYTES + 1)
    with pytest.raises(UploadPolicyError):
        UploadPolicy(allowed_extensions=frozenset({"png", "exe"}))


def test_configure_request_limits_preserves_stricter_application_caps():
    app = Flask(__name__)
    policy = UploadPolicy(max_file_bytes=1024, max_total_bytes=2048)
    app.config.update(
        MAX_CONTENT_LENGTH=4096,
        MAX_FORM_MEMORY_SIZE=2048,
        MAX_FORM_PARTS=4,
        UPLOAD_POLICY=policy,
    )

    configure_request_limits(app)

    assert app.config["MAX_CONTENT_LENGTH"] == 4096
    assert app.config["MAX_FORM_MEMORY_SIZE"] == 2048
    assert app.config["MAX_FORM_PARTS"] == 4
    assert app.config["UPLOAD_POLICY"] is policy


@pytest.mark.parametrize(
    ("content", "filename", "content_type", "status", "error_code"),
    [
        (b"not-an-image", "image.png", "image/png", 400, "upload_content_mismatch"),
        (_image_bytes(), "image.exe", "application/octet-stream", 400, "upload_extension_not_allowed"),
        (
            _image_bytes(size=(101, 101)),
            "image.png",
            "image/png",
            413,
            "upload_image_too_large",
        ),
    ],
)
def test_upload_policy_failures_have_stable_json_errors(
    limited_app,
    content,
    filename,
    content_type,
    status,
    error_code,
):
    if error_code == "upload_image_too_large":
        limited_app.config["UPLOAD_POLICY"] = UploadPolicy(max_image_pixels=10_000)

    response = limited_app.test_client().post(
        "/upload",
        data={"image": (BytesIO(content), filename, content_type)},
        content_type="multipart/form-data",
    )

    assert response.status_code == status
    assert response.get_json()["error_code"] == error_code


def test_prepare_request_files_uses_runtime_data_and_policy(tmp_path):
    app = Flask(__name__)
    configure_request_limits(app)
    app.config["RUNTIME_PATHS"] = SimpleNamespace(data_dir=tmp_path / "data")
    content = _image_bytes()
    files = MultiDict([("imageFile", _upload(content, "cover.png", "image/png"))])

    with app.app_context():
        prepared = app_utils.prepare_request_files(files)

    destination = Path(prepared.locations["imageFile"])
    assert destination.parent == tmp_path / "data" / "uploads"
    assert not destination.exists()
    assert len(prepared.pending) == 1
    pending = Path(prepared.pending[0][0])
    assert pending.read_bytes() == content

    prepared.promote()
    prepared.accept()
    assert destination.read_bytes() == content
    assert sorted(path.name for path in destination.parent.iterdir()) == [destination.name]


def test_prepare_request_files_preserves_jpeg_exif_orientation_behavior(tmp_path):
    app = Flask(__name__)
    configure_request_limits(app)
    app.config["RUNTIME_PATHS"] = SimpleNamespace(data_dir=tmp_path / "data")
    files = MultiDict(
        [("imageFile", _upload(_oriented_jpeg_bytes(), "photo.jpg", "image/jpeg"))]
    )

    with app.app_context():
        prepared = app_utils.prepare_request_files(files)

    pending = Path(prepared.pending[0][0])
    with Image.open(pending) as image:
        assert image.size == (2, 3)
        assert image.getexif().get(274) in {None, 1}

    prepared.rollback()


def test_jpeg_normalization_cannot_expand_past_per_file_limit(tmp_path):
    app = Flask(__name__)
    configure_request_limits(app)
    app.config["RUNTIME_PATHS"] = SimpleNamespace(data_dir=tmp_path / "data")
    content, normalized_size = _expanding_jpeg_bytes()
    limit = (len(content) + normalized_size) // 2
    app.config["UPLOAD_POLICY"] = UploadPolicy(
        max_file_bytes=limit,
        max_total_bytes=limit,
    )
    files = MultiDict(
        [("imageFile", _upload(content, "photo.jpg", "image/jpeg"))]
    )

    with app.app_context(), pytest.raises(UploadTooLarge):
        app_utils.prepare_request_files(files)

    assert list((tmp_path / "data" / "uploads").iterdir()) == []


def test_jpeg_normalization_counts_expansion_toward_total_limit(tmp_path):
    app = Flask(__name__)
    configure_request_limits(app)
    app.config["RUNTIME_PATHS"] = SimpleNamespace(data_dir=tmp_path / "data")
    content, normalized_size = _expanding_jpeg_bytes()
    total_limit = max(normalized_size + 1, len(content) * 2 + 1)
    assert total_limit < normalized_size * 2
    app.config["UPLOAD_POLICY"] = UploadPolicy(
        max_file_bytes=normalized_size + 1,
        max_total_bytes=total_limit,
    )
    files = MultiDict(
        [
            ("imageFiles[]", _upload(content, "first.jpg", "image/jpeg")),
            ("imageFiles[]", _upload(content, "second.jpg", "image/jpeg")),
        ]
    )

    with app.app_context(), pytest.raises(UploadTotalTooLarge):
        app_utils.prepare_request_files(files)

    assert list((tmp_path / "data" / "uploads").iterdir()) == []


def test_server_generated_upload_names_ignore_oversize_client_basename(tmp_path):
    app = Flask(__name__)
    configure_request_limits(app)
    app.config["RUNTIME_PATHS"] = SimpleNamespace(data_dir=tmp_path / "data")
    long_name = f"{'a' * 300}.png"
    files = MultiDict(
        [("imageFile", _upload(_image_bytes(), long_name, "image/png"))]
    )

    with app.app_context():
        prepared = app_utils.prepare_request_files(files)

    final_path = Path(prepared.locations["imageFile"])
    assert final_path.suffix == ".png"
    assert len(final_path.name) < 64
    prepared.rollback()


def test_prepare_request_files_removes_all_staged_files_on_total_failure(tmp_path):
    app = Flask(__name__)
    configure_request_limits(app)
    app.config["RUNTIME_PATHS"] = SimpleNamespace(data_dir=tmp_path / "data")
    content = _image_bytes()
    app.config["UPLOAD_POLICY"] = UploadPolicy(
        max_file_bytes=len(content),
        max_total_bytes=len(content) * 2 - 1,
    )
    files = MultiDict(
        [
            ("imageFiles[]", _upload(content, "first.png", "image/png")),
            ("imageFiles[]", _upload(content, "second.png", "image/png")),
        ]
    )

    with app.app_context(), pytest.raises(UploadTotalTooLarge):
        app_utils.prepare_request_files(files)

    upload_dir = tmp_path / "data" / "uploads"
    assert upload_dir.is_dir()
    assert list(upload_dir.iterdir()) == []


def test_application_factory_installs_request_limits(tmp_path, monkeypatch):
    import inkypi

    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    paths = RuntimePaths.from_environment(dev_mode=True)

    class FakeConfig:
        def __init__(self, *, runtime_paths):
            self.runtime_paths = runtime_paths

        def get_plugins(self):
            return []

    class FakeDisplayManager:
        def __init__(self, _device_config):
            pass

    class FakeRefreshTask:
        def __init__(self, _device_config, _display_manager):
            pass

    monkeypatch.setattr(inkypi, "Config", FakeConfig)
    monkeypatch.setattr(inkypi, "DisplayManager", FakeDisplayManager)
    monkeypatch.setattr(inkypi, "RefreshTask", FakeRefreshTask)
    monkeypatch.setattr(inkypi, "load_plugins", lambda _plugins: None)
    monkeypatch.setattr(inkypi, "register_plugin_blueprints", lambda _app: None)
    monkeypatch.setattr(inkypi, "load_or_create_secret_key", lambda _path: "secret")

    app = inkypi.build_application(dev_mode=True, runtime_paths=paths)
    app.config["TESTING"] = True
    response = app.test_client().post(
        "/update_now",
        data=b"x" * (MAX_REQUEST_BYTES + 1),
        content_type="application/octet-stream",
    )

    assert app.config["MAX_FORM_PARTS"] == MAX_FORM_PARTS
    assert app.config["MAX_CONTENT_LENGTH"] == MAX_REQUEST_BYTES
    assert response.status_code == 413
    assert response.get_json()["error_code"] == "request_too_large"
