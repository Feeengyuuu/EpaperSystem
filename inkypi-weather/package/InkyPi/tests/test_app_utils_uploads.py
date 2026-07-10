from io import BytesIO
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage, MultiDict

from security.request_limits import UploadFilenameRequired
import utils.app_utils as app_utils
from utils.app_utils import PreparedRequestFiles


def test_promote_failure_rolls_back_current_pending_file(tmp_path, monkeypatch):
    temporary = tmp_path / ".token.pending-image.png"
    final = tmp_path / "token-image.png"
    temporary.write_bytes(b"pending")
    prepared = PreparedRequestFiles(
        {"imageFile": str(final)},
        pending=[(str(temporary), str(final))],
    )

    monkeypatch.setattr(
        app_utils.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        prepared.promote()

    assert not temporary.exists()
    assert not final.exists()


def test_partial_upload_stream_failure_leaves_no_pending_file(tmp_path, monkeypatch):
    saved = tmp_path / "saved"
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))

    class FailingStream:
        def __init__(self):
            self.calls = 0

        def read(self, _size):
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("read failed")

    class FailingUpload:
        filename = "broken.png"
        mimetype = "image/png"
        stream = FailingStream()

    request_files = MultiDict([("imageFile", FailingUpload())])

    with pytest.raises(OSError, match="read failed"):
        app_utils.prepare_request_files(request_files)

    assert saved.exists()
    assert list(saved.iterdir()) == []


def test_prepare_request_files_rejects_nonempty_blank_filename(tmp_path, monkeypatch):
    saved = tmp_path / "saved"
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))
    request_files = MultiDict(
        [
            (
                "imageFile",
                FileStorage(
                    stream=BytesIO(b"malicious"),
                    filename="",
                    content_type="application/octet-stream",
                ),
            )
        ]
    )

    with pytest.raises(UploadFilenameRequired):
        app_utils.prepare_request_files(request_files)

    assert not saved.exists()
