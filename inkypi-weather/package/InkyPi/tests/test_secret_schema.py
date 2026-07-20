import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from secret_schema import DEFAULT_SCHEMA_PATH, SecretSchema, SecretSchemaError


REGISTRY = ROOT / "install" / "api_key_registry.json"
EXAMPLE = ROOT / ".env.example"


def test_generated_registry_and_example_match_schema():
    schema = SecretSchema.load(DEFAULT_SCHEMA_PATH)

    assert json.loads(REGISTRY.read_text(encoding="utf-8")) == schema.registry_document()
    assert EXAMPLE.read_text(encoding="utf-8") == schema.env_example()


def test_example_documents_browser_private_target_allowlist():
    example = SecretSchema.load(DEFAULT_SCHEMA_PATH).env_example()

    assert "# INKYPI_BROWSER_ALLOWED_HOSTS=panel.home.arpa" in example
    assert "# INKYPI_BROWSER_ALLOWED_CIDRS=192.168.1.0/24" in example
    assert "Cloud metadata addresses remain denied" in example


@pytest.mark.parametrize(
    ("canonical", "alias"),
    [
        ("OPENAI_API_KEY", "OPEN_AI_SECRET"),
        ("TICKETMASTER_API_KEY", "TICKETMASTER_CONSUMER_KEY"),
        ("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN"),
        ("BAMBU_ACCESS_CODE", "BAMBU_LAB_ACCESS_CODE"),
    ],
)
def test_schema_resolves_declared_alias_to_canonical_first(canonical, alias):
    schema = SecretSchema.load(DEFAULT_SCHEMA_PATH)

    assert schema.resolve_names(canonical)[0] == canonical
    assert schema.resolve_names(alias)[0] == canonical
    assert alias in schema.resolve_names(canonical)


def test_public_registry_contains_only_supported_ui_fields():
    schema = SecretSchema.load(DEFAULT_SCHEMA_PATH)

    registry = schema.public_registry()

    assert registry
    assert registry[0].keys() == {
        "key",
        "service",
        "features",
        "required",
        "aliases",
        "signup_url",
        "notes",
        "value_type",
    }
    assert all(entry["required"] is False for entry in registry)


@pytest.mark.parametrize(
    "document, message",
    [
        (
            {
                "version": 1,
                "entries": [
                    {"canonical": "DUPLICATE_KEY", "label": "One", "features": ["one"], "value_type": "secret"},
                    {"canonical": "DUPLICATE_KEY", "label": "Two", "features": ["two"], "value_type": "secret"},
                ],
            },
            "duplicate canonical",
        ),
        (
            {
                "version": 1,
                "entries": [
                    {
                        "canonical": "FIRST_KEY",
                        "aliases": ["SHARED_ALIAS"],
                        "label": "One",
                        "features": ["one"],
                        "value_type": "secret",
                    },
                    {
                        "canonical": "SECOND_KEY",
                        "aliases": ["SHARED_ALIAS"],
                        "label": "Two",
                        "features": ["two"],
                        "value_type": "secret",
                    },
                ],
            },
            "alias collision",
        ),
        (
            {
                "version": 1,
                "entries": [
                    {"canonical": "NOT-AN-ENV-NAME", "label": "Bad", "features": ["bad"], "value_type": "secret"}
                ],
            },
            "invalid environment name",
        ),
        (
            {
                "version": 1,
                "entries": [
                    {"canonical": "VALID_KEY", "label": "Bad", "features": ["bad"], "value_type": "number"}
                ],
            },
            "value_type",
        ),
    ],
)
def test_schema_validation_rejects_ambiguous_or_invalid_documents(document, message):
    with pytest.raises(SecretSchemaError, match=message):
        SecretSchema.from_document(document)


def test_default_schema_has_one_owner_for_every_name():
    schema = SecretSchema.load(DEFAULT_SCHEMA_PATH)
    names = [name for entry in schema.entries for name in (entry.canonical, *entry.aliases)]

    assert len(names) == len(set(names))
    assert schema.validate() is schema


def test_tmdb_bearer_and_api_key_remain_distinct_credential_types():
    schema = SecretSchema.load(DEFAULT_SCHEMA_PATH)

    assert schema.resolve_names("TMDB_BEARER_TOKEN")[0] == "TMDB_BEARER_TOKEN"
    assert schema.resolve_names("TMDB_API_KEY")[0] == "TMDB_API_KEY"
    assert "TMDB_API_KEY" not in schema.resolve_names("TMDB_BEARER_TOKEN")


def test_liveradar_twitch_credentials_are_canonical_schema_entries():
    schema = SecretSchema.load(DEFAULT_SCHEMA_PATH)
    entries = {entry.canonical: entry for entry in schema.entries}

    assert entries["TWITCH_CLIENT_ID"].features == ("LiveRadar",)
    assert entries["TWITCH_CLIENT_SECRET"].features == ("LiveRadar",)
    assert entries["TWITCH_CLIENT_ID"].value_type == "secret"
    assert entries["TWITCH_CLIENT_SECRET"].value_type == "secret"


def test_installer_can_read_schema_without_site_packages():
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "install" / "configure_api_keys.py"),
            "--list",
            "--common",
            "--lang",
            "en",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "OPENAI_API_KEY" in result.stdout
    assert "OPEN_AI_SECRET" in result.stdout
