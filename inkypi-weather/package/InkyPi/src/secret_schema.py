"""Validated, standard-library-only registry for InkyPi plugin secrets."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "config" / "secret_schema.json"
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VALUE_TYPES = frozenset({"secret", "path"})
REGISTRY_DESCRIPTION = (
    "Optional credentials and secret paths used by InkyPi plugins. Missing "
    "values never block installation; they only disable or degrade the "
    "matching plugin features."
)
RUNTIME_DEFAULTS = (
    ("OPENWEATHER_ONECALL_DAILY_LIMIT", "900", "Weather safety throttle."),
    ("OPENWEATHER_ONECALL_MIN_SECONDS", "1800", "Weather safety throttle."),
    ("OPENWEATHER_AUX_MIN_SECONDS", "1800", "Weather safety throttle."),
    ("OPENWEATHER_LOCATION_MIN_SECONDS", "86400", "Weather safety throttle."),
)


class SecretSchemaError(ValueError):
    """The secret registry is malformed or ambiguous."""


@dataclass(frozen=True)
class SecretEntry:
    """One canonical secret name and its accepted legacy aliases."""

    canonical: str
    aliases: tuple[str, ...]
    label: str
    features: tuple[str, ...]
    value_type: str
    help_url: str = ""
    notes: str = ""


class SecretSchema:
    """Immutable, validated lookup over canonical names and aliases."""

    def __init__(self, version: int, entries: tuple[SecretEntry, ...]):
        self.version = version
        self.entries = entries
        self._entries_by_name = {
            name: entry
            for entry in entries
            for name in (entry.canonical, *entry.aliases)
        }

    @classmethod
    def load(cls, path: str | Path = DEFAULT_SCHEMA_PATH) -> "SecretSchema":
        schema_path = Path(path)
        try:
            document = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SecretSchemaError(f"could not load secret schema {schema_path}: {error}") from error
        return cls.from_document(document)

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "SecretSchema":
        if not isinstance(document, Mapping):
            raise SecretSchemaError("secret schema document must be an object")
        version = document.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise SecretSchemaError("secret schema version must be a positive integer")
        raw_entries = document.get("entries")
        if not isinstance(raw_entries, list):
            raise SecretSchemaError("secret schema entries must be a list")

        entries: list[SecretEntry] = []
        name_owners: dict[str, str] = {}
        canonical_names: set[str] = set()
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, Mapping):
                raise SecretSchemaError(f"entry {index} must be an object")
            canonical = cls._required_env_name(raw.get("canonical"), index, "canonical")
            if canonical in canonical_names:
                raise SecretSchemaError(f"duplicate canonical name: {canonical}")
            canonical_names.add(canonical)
            prior_owner = name_owners.get(canonical)
            if prior_owner is not None:
                raise SecretSchemaError(
                    f"canonical name collision: {canonical} is already owned by {prior_owner}"
                )
            name_owners[canonical] = canonical

            raw_aliases = raw.get("aliases", [])
            if not isinstance(raw_aliases, list):
                raise SecretSchemaError(f"aliases for {canonical} must be a list")
            aliases: list[str] = []
            for alias_index, raw_alias in enumerate(raw_aliases):
                alias = cls._required_env_name(raw_alias, index, f"alias {alias_index}")
                prior_owner = name_owners.get(alias)
                if prior_owner is not None:
                    raise SecretSchemaError(
                        f"alias collision: {alias} belongs to both {prior_owner} and {canonical}"
                    )
                name_owners[alias] = canonical
                aliases.append(alias)

            label = raw.get("label")
            if not isinstance(label, str) or not label.strip():
                raise SecretSchemaError(f"label for {canonical} must be a non-empty string")
            raw_features = raw.get("features")
            if (
                not isinstance(raw_features, list)
                or not raw_features
                or any(not isinstance(feature, str) or not feature.strip() for feature in raw_features)
            ):
                raise SecretSchemaError(
                    f"features for {canonical} must be a non-empty string list"
                )
            value_type = raw.get("value_type")
            if value_type not in VALUE_TYPES:
                raise SecretSchemaError(
                    f"value_type for {canonical} must be one of {sorted(VALUE_TYPES)}"
                )
            help_url = raw.get("help_url", "")
            notes = raw.get("notes", "")
            if not isinstance(help_url, str) or not isinstance(notes, str):
                raise SecretSchemaError(f"help_url and notes for {canonical} must be strings")
            entries.append(
                SecretEntry(
                    canonical=canonical,
                    aliases=tuple(aliases),
                    label=label.strip(),
                    features=tuple(feature.strip() for feature in raw_features),
                    value_type=value_type,
                    help_url=help_url.strip(),
                    notes=notes.strip(),
                )
            )
        return cls(version=version, entries=tuple(entries))

    @staticmethod
    def _required_env_name(value: Any, index: int, field: str) -> str:
        if not isinstance(value, str) or not ENV_NAME_RE.fullmatch(value):
            raise SecretSchemaError(
                f"invalid environment name for entry {index} {field}: {value!r}"
            )
        return value

    def contains_name(self, name: str) -> bool:
        return name in self._entries_by_name

    def validate(self) -> "SecretSchema":
        """Revalidate this immutable schema and return it for fluent callers."""

        self.from_document(
            {
                "version": self.version,
                "entries": [
                    {
                        "canonical": entry.canonical,
                        "aliases": list(entry.aliases),
                        "label": entry.label,
                        "features": list(entry.features),
                        "value_type": entry.value_type,
                        "help_url": entry.help_url,
                        "notes": entry.notes,
                    }
                    for entry in self.entries
                ],
            }
        )
        return self

    def entry_for(self, name: str) -> SecretEntry:
        try:
            return self._entries_by_name[name]
        except KeyError as error:
            raise KeyError(f"unknown secret name: {name}") from error

    def resolve_names(self, name: str) -> tuple[str, ...]:
        """Return canonical-first candidates for either a canonical name or alias."""

        entry = self.entry_for(name)
        return (entry.canonical, *entry.aliases)

    def public_registry(self) -> list[dict[str, Any]]:
        """Return the metadata exposed by the Web UI and installer."""

        return [
            {
                "key": entry.canonical,
                "service": entry.label,
                "features": list(entry.features),
                "required": False,
                "aliases": list(entry.aliases),
                "signup_url": entry.help_url,
                "notes": entry.notes,
                "value_type": entry.value_type,
            }
            for entry in self.entries
        ]

    def registry_document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "description": REGISTRY_DESCRIPTION,
            "keys": self.public_registry(),
        }

    def env_example(self) -> str:
        lines = [
            "# InkyPi optional credentials and secret paths",
            "# Copy this file to the configured runtime env path, or run:",
            "#   python3 install/configure_api_keys.py --env-file /etc/inkypi/inkypi.env",
            "# Do not commit real credentials.",
            "",
        ]
        for entry in self.entries:
            lines.append(f"# {entry.label} - {', '.join(entry.features)}")
            if entry.help_url:
                lines.append(f"# Help: {entry.help_url}")
            if entry.value_type == "path":
                lines.append("# Type: path")
            if entry.aliases:
                lines.append(f"# Legacy aliases accepted: {', '.join(entry.aliases)}")
            if entry.notes:
                lines.append(f"# Note: {entry.notes}")
            lines.append(f"# {entry.canonical}=")
            lines.append("")

        lines.append("# Non-secret runtime defaults")
        for key, default, note in RUNTIME_DEFAULTS:
            lines.append(f"# {note}")
            lines.append(f"{key}={default}")
        return "\n".join(lines) + "\n"


def json_document(document: Mapping[str, Any]) -> str:
    """Serialize a generated artifact deterministically."""

    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
