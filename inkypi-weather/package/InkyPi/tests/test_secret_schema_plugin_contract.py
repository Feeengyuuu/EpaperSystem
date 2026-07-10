import ast
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PLUGINS = SRC / "plugins"
sys.path.insert(0, str(SRC))

from secret_schema import DEFAULT_SCHEMA_PATH, SecretSchema


ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_PARTS = {
    "SECRET",
    "KEY",
    "TOKEN",
    "HASH",
    "SESSION",
    "ACCESS_CODE",
    "CLIENT_ID",
    "CLIENT_SECRET",
    "API_ID",
    "COOKIE",
    "PASSWORD",
    "USERNAME",
    "PHPSESSID",
}


def _looks_like_secret_name(value):
    if not isinstance(value, str) or not ENV_NAME.fullmatch(value) or not value[0].isupper():
        return False
    upper = value.upper()
    if "_" in value and any(part in upper for part in SECRET_PARTS):
        return True
    return value.endswith(("ApiKey", "APIKey", "Key", "Token", "Secret", "AccessCode"))


def _literal_secret_names(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and _looks_like_secret_name(node.value):
            yield node.value, node.lineno


def test_every_builtin_plugin_literal_secret_name_resolves_to_schema_entry():
    schema = SecretSchema.load(DEFAULT_SCHEMA_PATH)
    missing = []
    for path in sorted(PLUGINS.rglob("*.py")):
        for name, line in _literal_secret_names(path):
            if not schema.contains_name(name):
                missing.append(f"{path.relative_to(ROOT)}:{line}: {name}")

    assert not missing, "Plugin secret names missing from SecretSchema:\n" + "\n".join(missing)


def test_required_provider_contracts_are_present_even_without_a_plugin_snapshot():
    schema = SecretSchema.load(DEFAULT_SCHEMA_PATH)
    required = {
        "OPENAI_API_KEY",
        "TICKETMASTER_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION_PATH",
        "BAMBU_ACCESS_CODE",
        "BLIZZARD_CLIENT_ID",
        "BLIZZARD_CLIENT_SECRET",
        "PIXIV_PHPSESSID",
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_REFRESH_TOKEN",
    }

    assert not {name for name in required if not schema.contains_name(name)}
