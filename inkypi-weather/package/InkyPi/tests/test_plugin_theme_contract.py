from pathlib import Path

from plugins.plugin_manifest import PluginManifest
from utils.theme_utils import resolve_palette_roles


PLUGIN_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "plugins"


def load_all_builtin_manifests():
    return [
        PluginManifest.from_path(path)
        for path in sorted(PLUGIN_SOURCE_ROOT.glob("*/plugin-info.json"))
    ]


def _relative_luminance(rgb):
    channels = []
    for channel in rgb:
        value = channel / 255
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return (
        0.2126 * channels[0]
        + 0.7152 * channels[1]
        + 0.0722 * channels[2]
    )


def contrast_ratio(first, second):
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_every_builtin_renderer_owns_two_valid_palettes():
    manifests = load_all_builtin_manifests()

    assert len(manifests) == 59
    for manifest in manifests:
        assert manifest.capabilities.supports_day_night_theme, manifest.id
        assert manifest.theme.presentation in {"ui", "media"}, manifest.id
        assert manifest.theme.day != manifest.theme.night, manifest.id

        day = resolve_palette_roles({"day": manifest.theme.day}, "day")
        night = resolve_palette_roles({"night": manifest.theme.night}, "night")

        assert contrast_ratio(day["background"], day["ink"]) >= 4.5, manifest.id
        assert (
            contrast_ratio(night["background"], night["ink"]) >= 4.5
        ), manifest.id
