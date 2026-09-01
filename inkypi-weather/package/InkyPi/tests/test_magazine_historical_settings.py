from pathlib import Path


SETTINGS_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "plugins"
    / "magazine_covers"
    / "settings.html"
)


def _settings_html():
    return SETTINGS_PATH.read_text(encoding="utf-8")


def test_historical_magazine_settings_expose_locked_defaults():
    html = _settings_html()

    expected_defaults = {
        "contentMode": "comprehensive",
        "selectionHoldHours": "3",
        "historicalPercent": "80",
        "categories": (
            "art_design,sports,news_politics,fashion_culture,science_nature,"
            "entertainment_music,adult,general_history"
        ),
        "includeAdult": "true",
        "historyStartYear": "",
        "overlayMode": "none",
        "catalogRefreshHours": "24",
        "latestRefreshHours": "6",
    }
    for field, value in expected_defaults.items():
        assert f'id="{field}"' in html
        assert f'name="{field}"' in html
        assert f'value="{value}"' in html


def test_historical_magazine_settings_preserve_legacy_fields_and_saved_values():
    html = _settings_html()

    for field in (
        "sources",
        "rotationMode",
        "fitMode",
        "refreshOnDisplay",
        "dailyLibraryMode",
        "libraryRefreshHours",
    ):
        assert f'id="{field}"' in html
        assert f'name="{field}"' in html

    for field in (
        "contentMode",
        "selectionHoldHours",
        "historicalPercent",
        "categories",
        "includeAdult",
        "historyStartYear",
        "overlayMode",
        "catalogRefreshHours",
        "latestRefreshHours",
    ):
        assert f"pluginSettings.{field}" in html


def test_historical_magazine_settings_bound_numeric_values_in_browser():
    html = _settings_html()

    assert "clampNumber" in html
    assert "selectionHoldHours, 3, 1, 168" in html
    assert "historicalPercent, 80, 0, 100" in html
    assert "catalogRefreshHours, 24, 1, 168" in html
    assert "latestRefreshHours, 6, 1, 72" in html
