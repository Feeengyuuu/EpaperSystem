from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

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


def test_partial_upload_save_failure_leaves_no_pending_file(tmp_path, monkeypatch):
    saved = tmp_path / "saved"
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))

    class FailingUpload:
        filename = "broken.png"

        def save(self, path):
            Path(path).write_bytes(b"partial")
            raise OSError("save failed")

    request_files = MultiDict([("imageFile", FailingUpload())])

    with pytest.raises(OSError, match="save failed"):
        app_utils.prepare_request_files(request_files)

    assert saved.exists()
    assert list(saved.iterdir()) == []
