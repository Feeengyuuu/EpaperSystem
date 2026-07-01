from utils.plugin_cache import read_json, write_json


def read_json_file(path):
    return read_json(path, default={}, require_dict=True)


def write_json_file(path, payload):
    write_json(path, payload, ensure_ascii=True, indent=None, separators=(",", ":"))