import re
from pathlib import Path

import pytest
from flask import Flask


SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def _css_rule(source, selector):
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>[^}}]+)\}}", source)
    assert match is not None
    return match.group("body")


@pytest.mark.parametrize(
    ("asset_path", "expected_mimetypes"),
    (
        ("styles/tokens.css", {"text/css"}),
        ("icons/logo.svg", {"image/svg+xml"}),
        ("icons/favicon-32.png", {"image/png"}),
        ("icons/apple-touch-icon.png", {"image/png"}),
        ("icons/icon-192.png", {"image/png"}),
        ("icons/icon-512.png", {"image/png"}),
        ("styles/components.css", {"text/css"}),
        ("styles/now_playing.css", {"text/css"}),
        ("styles/plugins.css", {"text/css"}),
        ("styles/plugin_page.css", {"text/css"}),
        ("styles/apikeys.css", {"text/css"}),
        ("styles/device.css", {"text/css"}),
        ("styles/runtime_status.css", {"text/css"}),
        ("scripts/app.js", {"application/javascript", "text/javascript"}),
        ("scripts/i18n.js", {"application/javascript", "text/javascript"}),
        ("scripts/now_playing.js", {"application/javascript", "text/javascript"}),
        ("scripts/plugins.js", {"application/javascript", "text/javascript"}),
        ("scripts/apikeys.js", {"application/javascript", "text/javascript"}),
        ("scripts/device.js", {"application/javascript", "text/javascript"}),
        (
            "scripts/refresh_settings_manager.js",
            {"application/javascript", "text/javascript"},
        ),
    ),
)
def test_application_owned_static_asset_is_packaged_and_served(
    asset_path,
    expected_mimetypes,
):
    app = Flask(
        __name__,
        static_folder=str(SRC_DIR / "static"),
        static_url_path="/static",
    )

    response = app.test_client().get(f"/static/{asset_path}")

    assert response.status_code == 200
    assert response.mimetype in expected_mimetypes
    assert response.data


def test_tokens_define_dark_default_and_light_theme():
    tokens = (SRC_DIR / "static" / "styles" / "tokens.css").read_text(
        encoding="utf-8"
    )

    assert ":root,\nhtml[data-theme=\"dark\"] {" in tokens
    assert 'html[data-theme="light"] {' in tokens
    # Plugin settings partials still reference the legacy variable names.
    for legacy in ("--text-primary", "--border-color", "--bg-secondary", "--accent-warn"):
        assert f"{legacy}:" in tokens


def test_shell_pages_switch_layout_at_the_tab_bar_breakpoint():
    components = (SRC_DIR / "static" / "styles" / "components.css").read_text(
        encoding="utf-8"
    )

    assert "@media (min-width: 900px)" in components
    assert "@media (max-width: 899px)" in components
    assert ".app-main { margin-left: var(--nav-width); }" in components
    assert "bottom: calc(var(--tabbar-height)" in components


def test_i18n_pauses_mutation_observation_during_translation_writes():
    script = (SRC_DIR / "static" / "scripts" / "i18n.js").read_text(
        encoding="utf-8"
    )

    assert "translationObserver?.disconnect()" in script
    assert "observeTranslationMutations()" in script


def test_home_screen_icons_are_opaque_squares_of_the_declared_size():
    from PIL import Image

    for name, size in (("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)):
        with Image.open(SRC_DIR / "static" / "icons" / name) as image:
            assert image.size == (size, size), name
            # iOS fills transparency with black, so the icon must be opaque.
            assert image.mode == "RGB", name
