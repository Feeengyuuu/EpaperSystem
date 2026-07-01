import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blueprints.apikeys import parse_env_file, write_env_file


def test_write_env_file_uses_atomic_file_and_escapes_values(tmp_path):
    env_path = tmp_path / ".env"

    assert write_env_file(
        str(env_path),
        [
            ("PLAIN", "abc123"),
            ("WITH_SPACE", "hello world"),
            ("WITH_QUOTE", 'hello "world"'),
            ("EMPTY", ""),
        ],
    )

    assert not list(tmp_path.glob(".env.*.tmp"))
    values = dict(parse_env_file(str(env_path)))
    assert values["PLAIN"] == "abc123"
    assert values["WITH_SPACE"] == "hello world"
    assert values["WITH_QUOTE"] == 'hello "world"'
    assert values["EMPTY"] == ""