"""Strict parsing and precedence rules for plugin instance settings."""


class PluginSettingError(ValueError):
    """Raised when a plugin setting has an unsupported explicit value."""


def parse_strict_bool(value, *, field):
    """Parse a boolean without accepting Python's broader truthy values."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "false"}:
            return normalized == "true"
    raise PluginSettingError(f"{field} must be true or false")


def resolve_refresh_on_display(settings, manifest, *, base_default=False):
    """Resolve instance, manifest, then base refresh-on-display precedence."""

    settings = settings or {}
    manifest = manifest or {}
    if "refreshOnDisplay" in settings:
        return parse_strict_bool(
            settings["refreshOnDisplay"],
            field="refreshOnDisplay",
        )
    if "refresh_on_display" in manifest:
        return parse_strict_bool(
            manifest["refresh_on_display"],
            field="refresh_on_display",
        )
    return bool(base_default)
