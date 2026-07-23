import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from plugins.plugin_settings import (
    PluginSettingError,
    parse_strict_bool,
    resolve_refresh_on_display,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (False, False),
        (True, True),
        ("false", False),
        ("true", True),
        ("  FALSE\t", False),
        ("\nTrUe ", True),
    ],
)
def test_parse_strict_bool_accepts_only_booleans_and_boolean_strings(
    value,
    expected,
):
    assert parse_strict_bool(value, field="refreshOnDisplay") is expected


@pytest.mark.parametrize(
    "value",
    [None, 0, 1, 1.0, "", "yes", "sometimes", [], {}, object()],
)
def test_parse_strict_bool_rejects_coerced_or_ambiguous_values(value):
    with pytest.raises(
        PluginSettingError,
        match="refreshOnDisplay must be true or false",
    ):
        parse_strict_bool(value, field="refreshOnDisplay")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(False, False), ("false", False), (True, True), ("true", True)],
)
def test_instance_value_overrides_manifest_default(value, expected):
    assert resolve_refresh_on_display(
        {"refreshOnDisplay": value},
        {"refresh_on_display": not expected},
    ) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(False, False), (" FALSE ", False), (True, True), (" TrUe ", True)],
)
def test_manifest_value_is_used_when_instance_value_is_missing(value, expected):
    assert resolve_refresh_on_display({}, {"refresh_on_display": value}) is expected


@pytest.mark.parametrize("source", ["settings", "manifest"])
def test_invalid_explicit_boolean_is_rejected(source):
    settings = {"refreshOnDisplay": "sometimes"} if source == "settings" else {}
    manifest = {"refresh_on_display": "sometimes"} if source == "manifest" else {}

    with pytest.raises(PluginSettingError):
        resolve_refresh_on_display(settings, manifest)


@pytest.mark.parametrize("base_default", [False, True])
def test_base_default_is_used_only_when_instance_and_manifest_are_missing(
    base_default,
):
    assert (
        resolve_refresh_on_display(None, None, base_default=base_default)
        is base_default
    )


APOD_SETTINGS_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "plugins"
    / "apod"
    / "settings.html"
)


class _NASAPicsSettingsInputParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "input":
            return
        attributes = dict(attrs)
        self.fields.append(
            {
                "id": attributes.get("id"),
                "name": attributes.get("name"),
                "type": str(attributes.get("type") or "text").lower(),
                "value": str(attributes.get("value") or ""),
                "checked": "checked" in attributes,
                "disabled": "disabled" in attributes,
            }
        )


def _nasapics_settings_form_fields(html):
    parser = _NASAPicsSettingsInputParser()
    parser.feed(html)
    fields = parser.fields
    assert fields
    assert all(field["id"] for field in fields)
    assert len({field["id"] for field in fields}) == len(fields)
    by_id = {field["id"]: field for field in fields}

    assert "customDate" in by_id, "customDate input is missing"
    assert by_id["customDate"] == {
        "id": "customDate",
        "name": "customDate",
        "type": "date",
        "value": "",
        "checked": False,
        "disabled": False,
    }
    assert by_id["refreshOnDisplay"] == {
        "id": "refreshOnDisplay",
        "name": "refreshOnDisplay",
        "type": "hidden",
        "value": "false",
        "checked": False,
        "disabled": False,
    }
    assert by_id["randomizeApod"]["name"] == "randomizeApod"
    assert by_id["randomizeApod"]["type"] == "checkbox"
    return fields


def _run_nasapics_settings_roundtrip(plugin_settings, *, html=None):
    if html is None:
        html = APOD_SETTINGS_PATH.read_text(encoding="utf-8")
    fields = _nasapics_settings_form_fields(html)
    scripts = re.findall(
        r"<script(?:\s[^>]*)?>(.*?)</script>",
        html,
        flags=re.DOTALL,
    )
    assert scripts
    payload = json.dumps(
        {
            "scripts": scripts,
            "pluginSettings": plugin_settings,
            "fields": fields,
        }
    )
    harness = r"""
const vm = require('node:vm');
const payload = JSON.parse(process.argv[1]);
const listeners = new Map();

const formFields = payload.fields.map((field) => ({...field}));
const elements = Object.fromEntries(
  formFields.map((field) => [field.id, field])
);
const form = {elements: formFields};
const document = {
  addEventListener(type, callback) {
    const callbacks = listeners.get(type) || [];
    callbacks.push(callback);
    listeners.set(type, callbacks);
  },
  getElementById(id) {
    return elements[id] || null;
  },
};
class FormData {
  constructor(source) {
    this.fields = [];
    for (const field of source.elements) {
      if (!field.name || field.disabled) continue;
      if (field.type === 'checkbox' && !field.checked) continue;
      this.fields.push([field.name, field.value]);
    }
  }
  entries() { return this.fields[Symbol.iterator](); }
}
class ForbiddenDate {
  constructor() { throw new Error('NASAPics settings synthesized a date'); }
}
const context = vm.createContext({
  console,
  document,
  FormData,
  Date: ForbiddenDate,
  loadPluginSettings: true,
  pluginSettings: payload.pluginSettings,
});
for (const script of payload.scripts) vm.runInContext(script, context);
for (const callback of listeners.get('DOMContentLoaded') || []) callback();
process.stdout.write(JSON.stringify({
  dateValue: elements.customDate.value,
  fields: [...new FormData(form).entries()],
}));
"""
    completed = subprocess.run(
        ["node", "-e", harness, payload],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("plugin_settings", [{}, {"customDate": ""}])
def test_nasapics_settings_roundtrip_keeps_absent_or_empty_date_empty(
    plugin_settings,
):
    result = _run_nasapics_settings_roundtrip(plugin_settings)

    assert result["dateValue"] == ""
    assert ["customDate", ""] in result["fields"]
    assert [
        value
        for name, value in result["fields"]
        if name == "refreshOnDisplay"
    ] == ["false"]


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    [
        ("id", "renamedDate"),
        ("name", "renamedDate"),
    ],
    ids=["renamed-id", "renamed-name"],
)
def test_nasapics_settings_roundtrip_rejects_missing_or_renamed_date_field(
    attribute,
    replacement,
):
    html = APOD_SETTINGS_PATH.read_text(encoding="utf-8")
    mutated_html = html.replace(
        f'{attribute}="customDate"',
        f'{attribute}="{replacement}"',
        1,
    )
    assert mutated_html != html

    with pytest.raises(AssertionError):
        _run_nasapics_settings_roundtrip({}, html=mutated_html)


def test_nasapics_instance_false_wins_even_if_a_stale_manifest_was_true():
    assert (
        resolve_refresh_on_display(
            {"refreshOnDisplay": "false"},
            {"refresh_on_display": True},
        )
        is False
    )
