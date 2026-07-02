import builtins
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.secret_key import load_or_create_secret_key  # noqa: E402


def test_creates_file_with_strong_token_when_missing(tmp_path):
    secret_path = tmp_path / "config" / ".flask_secret"

    key = load_or_create_secret_key(str(secret_path))

    assert secret_path.is_file()
    assert secret_path.read_text(encoding="utf-8") == key
    assert len(key) == 64
    assert all(char in string.hexdigits for char in key)


def test_returns_same_value_on_second_call(tmp_path):
    secret_path = tmp_path / ".flask_secret"

    first = load_or_create_secret_key(str(secret_path))
    second = load_or_create_secret_key(str(secret_path))

    assert first == second


def test_returns_existing_content_stripped(tmp_path):
    secret_path = tmp_path / ".flask_secret"
    secret_path.write_text("  my-existing-secret\n", encoding="utf-8")

    assert load_or_create_secret_key(str(secret_path)) == "my-existing-secret"


def test_falls_back_to_ephemeral_token_when_path_unwritable(tmp_path, monkeypatch):
    secret_path = tmp_path / ".flask_secret"

    def broken_open(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(builtins, "open", broken_open)

    key = load_or_create_secret_key(str(secret_path))

    assert len(key) == 64
    assert all(char in string.hexdigits for char in key)
    assert not secret_path.exists()
