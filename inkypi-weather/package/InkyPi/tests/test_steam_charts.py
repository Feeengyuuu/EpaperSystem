import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.steam_charts.steam_charts import Image, SteamCharts


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", timezone="America/Los_Angeles"):
        self.resolution = resolution
        self.orientation = orientation
        self.timezone = timezone

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "timezone": self.timezone}
        if key is None:
            return values
        return values.get(key, default)


def _plugin():
    return SteamCharts({"id": "steam_charts"})


def test_scrape_trending_extracts_games_and_formats_players():
    plugin = _plugin()
    plugin._fetch_homepage = lambda message: """
        <table id="trending-recent">
            <tr>
                <td><a href="/app/730">Counter-Strike 2</a></td>
                <td>+12.5%</td>
                <td>ignored</td>
                <td>1,234,567</td>
            </tr>
        </table>
    """

    games = plugin._scrape_steamcharts_trending(1)

    assert games == [
        {
            "rank": 1,
            "app_id": 730,
            "name": "Counter-Strike 2",
            "image": "https://cdn.akamai.steamstatic.com/steam/apps/730/capsule_sm_120.jpg",
            "change_24h_fmt": "+12.5%",
            "current_players_fmt": "1,234,567",
        }
    ]


def test_scrape_top_records_formats_record_month():
    plugin = _plugin()
    plugin._fetch_homepage = lambda message: """
        <table id="toppeaks">
            <tr>
                <td><a href="/app/570">Dota 2</a></td>
                <td>1,295,114</td>
                <td>2016-03-06T12:00:00Z</td>
                <td>ignored</td>
            </tr>
        </table>
    """

    games = plugin._scrape_steamcharts_top_records(1)

    assert games[0]["app_id"] == 570
    assert games[0]["peak_players_fmt"] == "1,295,114"
    assert games[0]["peak_time_fmt"] == "Mar 2016"


def test_fetch_games_enriches_missing_trending_fields_without_images():
    plugin = _plugin()
    plugin._scrape_steamcharts_trending = lambda count: [
        {"rank": 1, "app_id": 730, "name": "Counter-Strike 2", "image": ""}
    ]
    plugin._fetch_chart_data_batch = lambda app_ids, sparkline_hours, include_change=False: {
        730: {
            "current_players": 1234,
            "change_24h": -5.25,
            "sparkline_svg": '<polyline points="0,10 120,20" />',
        }
    }

    games = plugin._fetch_games("steamcharts_trending", 1)

    assert games[0]["current_players_fmt"] == "1,234"
    assert games[0]["change_24h_fmt"] == "-5.2%"
    assert "polyline" in games[0]["sparkline_svg"]


def test_generate_image_maps_legacy_mode_and_clamps_item_count():
    plugin = _plugin()
    calls = {}

    def fake_fetch(source, count):
        calls["source"] = source
        calls["count"] = count
        return [{"rank": 1, "app_id": 730, "name": "Counter-Strike 2", "image": ""}]

    def fake_render(dimensions, html_file, css_file, template_params):
        calls["dimensions"] = dimensions
        calls["template"] = html_file
        calls["css"] = css_file
        calls["template_params"] = template_params
        return "rendered"

    plugin._fetch_games = fake_fetch
    plugin._apply_store_metadata = lambda games, include_images: calls.setdefault(
        "images", include_images
    )
    plugin.render_image = fake_render

    result = plugin.generate_image(
        {"mode": "top_sellers", "itemsCount": "99", "showImages": "false", "themeMode": "day"},
        FakeDeviceConfig(),
    )

    assert result == "rendered"
    assert calls["source"] == "steamcharts_top_games"
    assert calls["count"] == 5
    assert calls["template"] == "steam_charts.html"
    assert calls["css"] == "steam_charts.css"
    assert calls["template_params"]["show_images"] is False
    assert calls["template_params"]["theme_mode"] == "day"
    assert calls["template_params"]["theme_ink"] == "#000000"
    assert calls["template_params"]["theme_paper"] == "#ffffff"
    assert calls["template_params"]["steam_logo_uri"].startswith("data:image/png;base64,")
    assert calls["template_params"]["updated_at_text"].startswith("刷新时间 ")
    assert calls["template_params"]["plugin_settings"]["backgroundColor"] == "#ffffff"
    assert calls["template_params"]["plugin_settings"]["textColor"] == "#000000"
    assert calls["images"] is False


def test_generate_image_uses_pil_fallback_when_html_render_fails():
    plugin = _plugin()
    plugin._fetch_games = lambda source, count: [
        {
            "rank": 1,
            "app_id": 730,
            "name": "Counter-Strike 2",
            "image": "",
            "change_24h_fmt": "+12.5%",
            "current_players_fmt": "1,234,567",
        }
    ]
    plugin._apply_store_metadata = lambda games, include_images: None
    plugin.render_image = lambda dimensions, html_file, css_file, template_params: None

    image = plugin.generate_image(
        {"mode": "new_trending", "itemsCount": "1", "showImages": "true", "themeMode": "night"},
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_font_match_rejects_non_cjk_sans_fallbacks():
    assert SteamCharts._is_accepted_sans_match("Microsoft YaHei", "DejaVu Sans") is False
    assert SteamCharts._is_accepted_sans_match("Noto Sans SC", "Noto Sans SC") is True


def test_updated_at_uses_device_timezone():
    utc_now = datetime(2026, 5, 28, 2, 15, tzinfo=ZoneInfo("UTC"))

    assert SteamCharts._format_updated_at(FakeDeviceConfig(), utc_now) == "刷新时间 05/27 19:15"


def test_steam_logo_renders_theme_colors():
    icon = SteamCharts._theme_steam_logo(
        48,
        {"ink": (0, 0, 0), "paper": (255, 255, 255)},
    )
    night_icon = SteamCharts._theme_steam_logo(
        48,
        {"ink": (255, 255, 255), "paper": (0, 0, 0)},
    )

    assert icon.size == (48, 48)
    assert icon.mode == "RGBA"
    opaque_pixels = [
        pixel[:3]
        for count, pixel in icon.getcolors(maxcolors=48 * 48)
        if pixel[3] > 0
    ]
    assert (0, 0, 0) in opaque_pixels
    assert (255, 255, 255) in opaque_pixels
    night_pixels = [
        pixel[:3]
        for count, pixel in night_icon.getcolors(maxcolors=48 * 48)
        if pixel[3] > 0
    ]
    assert (255, 255, 255) in night_pixels
    assert (0, 0, 0) in night_pixels


def test_header_pixel_gradient_uses_theme_ink():
    day = Image.new("RGB", (800, 480), "white")
    night = Image.new("RGB", (800, 480), "black")

    SteamCharts._draw_header_pixel_gradient(
        day,
        22,
        65,
        {"ink": (0, 0, 0), "paper": (255, 255, 255)},
    )
    SteamCharts._draw_header_pixel_gradient(
        night,
        22,
        65,
        {"ink": (255, 255, 255), "paper": (0, 0, 0)},
    )

    assert day.crop((620, 16, 780, 68)).getbbox() is not None
    assert night.crop((620, 16, 780, 68)).getbbox() is not None


def test_apply_store_metadata_prefers_schinese_name_and_cover_image():
    plugin = _plugin()
    calls = []

    def fake_details(app_id, language):
        calls.append((app_id, language))
        if language == "schinese":
            return {
                "name": "反恐精英2",
                "capsule_image": "https://example.test/capsule.jpg",
            }
        return {"name": "Counter-Strike 2"}

    plugin._fetch_store_appdetails = fake_details
    plugin._image_url_to_data_uri = lambda image_url: f"data:image/jpeg;base64,{image_url}"
    games = [{"rank": 1, "app_id": 730, "name": "Counter-Strike 2", "image": ""}]

    plugin._apply_store_metadata(games, include_images=True)

    assert games[0]["name"] == "反恐精英2"
    assert games[0]["secondary_name"] == "Counter-Strike 2"
    assert games[0]["image"] == "data:image/jpeg;base64,https://example.test/capsule.jpg"
    assert calls == [(730, "schinese"), (730, "english")]
