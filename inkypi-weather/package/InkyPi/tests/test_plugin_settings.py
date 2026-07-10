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
