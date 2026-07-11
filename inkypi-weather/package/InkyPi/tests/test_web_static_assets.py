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
        ("styles/main.css", {"text/css"}),
        ("scripts/dark_mode.js", {"application/javascript", "text/javascript"}),
        ("scripts/i18n.js", {"application/javascript", "text/javascript"}),
        ("scripts/image_modal.js", {"application/javascript", "text/javascript"}),
        (
            "scripts/refresh_settings_manager.js",
            {"application/javascript", "text/javascript"},
        ),
        ("scripts/response_modal.js", {"application/javascript", "text/javascript"}),
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


def test_administration_frame_centers_independently_of_body_siblings():
    stylesheet = (SRC_DIR / "static" / "styles" / "main.css").read_text(
        encoding="utf-8"
    )
    body_rule = _css_rule(stylesheet, "body")
    frame_rule = _css_rule(stylesheet, ".frame")

    assert "display: block" in body_rule
    assert "margin: 0 auto" in frame_rule


def test_i18n_pauses_mutation_observation_during_translation_writes():
    script = (SRC_DIR / "static" / "scripts" / "i18n.js").read_text(
        encoding="utf-8"
    )

    assert "translationObserver?.disconnect()" in script
    assert "observeTranslationMutations()" in script
