from pathlib import Path

from utils.atomic_file import atomic_write_json
from utils.plugin_cache import read_json


def read_json_file(path):
    return read_json(path, default={}, require_dict=True)


def write_json_file(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target, payload)
