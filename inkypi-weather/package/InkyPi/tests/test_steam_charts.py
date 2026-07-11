import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.steam_charts.steam_charts as steam_charts_module
from plugins.steam_charts.steam_charts import Image, SANS_FONT_PATHS, STATIC_YAHEI_FONT_PATH, STEAM_PIXEL_KAIJU_PATH, STEAM_TITLE_WORDMARK_PATH, SteamCharts


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


def test_fetch_games_uses_30_day_bar_sparkline_for_top_games():
    plugin = _plugin()
    calls = {}
    plugin._scrape_steamcharts_top_games = lambda count: [
        {"rank": 1, "app_id": 570, "name": "Dota 2", "image": ""}
    ]

    def fake_chart_batch(app_ids, sparkline_hours, include_change=False, sparkline_style="line"):
        calls["args"] = (app_ids, sparkline_hours, include_change, sparkline_style)
        return {
            570: {
                "current_players": 789588,
                "sparkline_svg": '<rect x="0" y="4" width="3" height="26" />',
            }
        }

    plugin._fetch_chart_data_batch = fake_chart_batch

    games = plugin._fetch_games("steamcharts_top_games", 1)

    assert calls["args"] == ([570], 30 * 24, False, "bars")
    assert games[0]["current_players_fmt"] == "789,588"
    assert games[0]["sparkline_svg"].startswith("<rect")

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
    plugin._font_file_uri = lambda weight="normal": (
        "file:///fonts/msyhbd.ttc" if weight == "bold" else "file:///fonts/msyh.ttc"
    )

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
    assert calls["template_params"]["theme_chart_ink"] == "#064e3b"
    assert calls["template_params"]["steam_logo_uri"].startswith("data:image/png;base64,")
    assert calls["template_params"]["title_wordmark_uri"].startswith("data:image/png;base64,")
    assert calls["template_params"]["pixel_kaiju_uri"].startswith("data:image/png;base64,")
    assert "header_bar_uri" not in calls["template_params"]
    assert "header_scene_uri" not in calls["template_params"]
    assert calls["template_params"]["yahei_font_uri"] == "file:///fonts/msyh.ttc"
    assert calls["template_params"]["yahei_bold_font_uri"] == "file:///fonts/msyhbd.ttc"
    assert calls["template_params"]["updated_at_text"].startswith("\u5237\u65b0\u65f6\u95f4 ")
    assert calls["template_params"]["plugin_settings"]["backgroundColor"] == "#ffffff"
    assert calls["template_params"]["plugin_settings"]["textColor"] == "#000000"
    assert calls["template_params"]["plugin_settings"]["selectedFrame"] == "None"
    for margin_key in ("margin", "topMargin", "bottomMargin", "leftMargin", "rightMargin"):
        assert calls["template_params"]["plugin_settings"][margin_key] == 0
    assert calls["images"] is False


def test_generate_image_combined_mode_renders_two_top_five_live_groups():
    plugin = _plugin()
    calls = {"sources": [], "metadata_batches": []}

    def fake_fetch(source, count):
        calls["sources"].append((source, count))
        if source == "steamcharts_trending":
            return [
                {
                    "rank": 1,
                    "app_id": 730,
                    "name": "Counter-Strike 2",
                    "image": "",
                    "change_24h_fmt": "+12.5%",
                    "current_players_fmt": "1,234,567",
                }
            ]
        return [
            {
                "rank": 1,
                "app_id": 570,
                "name": "Dota 2",
                "image": "",
                "current_players_fmt": "555,555",
                "peak_players_fmt": "777,777",
            }
        ]

    def fake_apply(games, include_images):
        calls["metadata_batches"].append(([game["app_id"] for game in games], include_images))

    def fake_render(dimensions, html_file, css_file, template_params):
        calls["template_params"] = template_params
        return "combined-rendered"

    plugin._fetch_games = fake_fetch
    plugin._apply_store_metadata = fake_apply
    plugin.render_image = fake_render
    plugin._font_file_uri = lambda weight="normal": (
        "file:///fonts/msyhbd.ttc" if weight == "bold" else "file:///fonts/msyh.ttc"
    )

    result = plugin.generate_image(
        {"mode": "live_overview", "itemsCount": "4", "showImages": "false", "themeMode": "day"},
        FakeDeviceConfig(),
    )

    assert result == "combined-rendered"
    assert calls["sources"] == [
        ("steamcharts_trending", 5),
        ("steamcharts_top_games", 5),
    ]
    assert calls["metadata_batches"] == [([730], False), ([570], False)]
    params = calls["template_params"]
    assert params["layout_variant"] == "combined"
    assert params["table_variant"] == "combined"
    assert params["subtitle"] == "Live Overview"
    assert [group["key"] for group in params["chart_groups"]] == [
        "trending",
        "player_count",
    ]
    assert params["chart_groups"][0]["games"][0]["primary_metric"] == "1,234,567"
    assert params["chart_groups"][0]["games"][0]["secondary_metric"] == "24h +12.5%"
    assert params["chart_groups"][1]["games"][0]["primary_metric"] == "555,555"
    assert params["chart_groups"][1]["games"][0]["secondary_metric"] == "Peak 777,777"
    assert params["chart_groups"][0]["games"][0]["name_font_scale"]
    left_metric_scale = params["chart_groups"][0]["games"][0]["metric_font_scale"]
    right_metric_scale = params["chart_groups"][1]["games"][0]["metric_font_scale"]
    assert left_metric_scale
    assert left_metric_scale == right_metric_scale


def _polyline_y_span(svg):
    points = svg.split('points="', 1)[1].split('"', 1)[0].split()
    y_values = [float(pair.split(",", 1)[1]) for pair in points]
    return max(y_values) - min(y_values)


def test_sparkline_generator_supports_line_and_bar_shapes():
    points = [[index * 1000, 100 + index * index] for index in range(40)]

    line_svg = SteamCharts._generate_sparkline_svg(points, chart_style="line")
    bar_svg = SteamCharts._generate_sparkline_svg(points, chart_style="bars")

    assert line_svg.startswith("<polyline")
    assert "<rect" not in line_svg
    assert bar_svg.count("<rect") == 30
    assert "<polyline" not in bar_svg


def test_line_sparkline_exaggerates_vertical_movement_without_touching_edges():
    points = [[index * 1000, 100 + index * index] for index in range(40)]
    svg = SteamCharts._generate_sparkline_svg(points, chart_style="line")
    point_pairs = svg.split('points="', 1)[1].split('"', 1)[0].split()
    y_values = [float(pair.split(",", 1)[1]) for pair in point_pairs]

    assert _polyline_y_span(svg) >= 25.0
    assert min(y_values) >= 2.0
    assert max(y_values) <= 28.0

def test_pil_fallback_draws_line_and_bar_sparklines():
    image = Image.new("RGB", (240, 40), "white")
    draw = ImageDraw.Draw(image)
    points = [[index * 1000, 100 + index * index] for index in range(40)]
    line_svg = SteamCharts._generate_sparkline_svg(points, chart_style="line")
    bar_svg = SteamCharts._generate_sparkline_svg(points, chart_style="bars")

    assert SteamCharts._draw_sparkline_svg(draw, bar_svg, (0, 0, 110, 30), (0, 0, 0))
    assert SteamCharts._draw_sparkline_svg(draw, line_svg, (130, 0, 240, 30), (0, 0, 0))
    colors = dict((color, count) for count, color in image.getcolors(maxcolors=image.width * image.height))
    assert colors[(0, 0, 0)] > 50

def test_compact_sparkline_widths_match_group_shape():
    assert SteamCharts._compact_sparkline_width_ratio("trending", '<polyline points="0,10 120,20" />') == 0.64
    assert SteamCharts._compact_sparkline_width_ratio("top_records", '<polyline points="0,10 120,20" />') == 0.64
    assert SteamCharts._compact_sparkline_width_ratio("top_games", '<rect x="0" y="4" width="3" height="26" />') == 0.64
    assert SteamCharts._compact_sparkline_y_offset("trending") == 0
    assert SteamCharts._compact_sparkline_y_offset("top_records") == 0
    assert SteamCharts._compact_sparkline_y_offset("top_games") == 5

def test_metric_font_scale_prioritizes_full_player_counts():
    short = SteamCharts._metric_font_scale("821")
    current_count = SteamCharts._metric_font_scale("1,146,000")
    peak_count = SteamCharts._metric_font_scale("Peak 1,754,724")

    assert short > current_count > peak_count
    assert current_count <= 1.04
    assert peak_count >= 0.9
    assert peak_count < 1


def test_compact_font_scales_expand_short_text_and_shrink_long_text():
    short_game, long_game = SteamCharts._prepare_compact_games(
        "top_games",
        [
            {
                "rank": 1,
                "app_id": 570,
                "name": "Dota 2",
                "image": "",
                "current_players_fmt": "123",
                "peak_players_fmt": "777",
            },
            {
                "rank": 2,
                "app_id": 306130,
                "name": "The Elder Scrolls Online: Tamriel Unlimited",
                "image": "",
                "current_players_fmt": "1,234,567,890",
                "peak_players_fmt": "9,876,543,210",
            },
        ],
    )

    assert float(short_game["name_font_scale"]) > float(long_game["name_font_scale"])
    assert float(short_game["metric_font_scale"]) > float(long_game["metric_font_scale"])
    assert float(short_game["name_font_scale"]) > 1
    assert float(long_game["name_font_scale"]) < 1


def test_prepared_games_use_bold_safe_cjk_middle_dot_without_control_chars():
    name = "TBH: \u5854\u65af\u514b\u5df4\u00b7\u82f1\u96c4"
    expected_display = "TBH: \u5854\u65af\u514b\u5df4\u2027\u82f1\u96c4"

    compact = SteamCharts._prepare_compact_games(
        "top_games",
        [
            {
                "rank": 1,
                "app_id": 3678970,
                "name": name,
                "image": "",
                "current_players_fmt": "470,984",
                "peak_players_fmt": "545,349",
            }
        ],
    )[0]
    table = SteamCharts._prepare_table_games(
        "top_games",
        [
            {
                "rank": 1,
                "app_id": 3678970,
                "name": name,
                "image": "",
                "current_players_fmt": "470,984",
                "peak_players_fmt": "545,349",
            }
        ],
    )[0]

    assert compact["name"] == name
    assert table["name"] == name
    assert compact["display_name"] == expected_display
    assert table["display_name"] == expected_display
    assert "\u00b7" not in compact["display_name"]
    assert "\u00b7" not in table["display_name"]
    assert "\u2060" not in compact["display_name"]
    assert "\u2060" not in table["display_name"]


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
    assert image.info["inkypi_skip_cache"] is True


def test_steam_charts_css_prefers_embedded_yahei_font():
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.css"
    ).read_text(encoding="utf-8")

    assert 'font-family: "InkySteamYaHei", "Microsoft YaHei", Arial, sans-serif;' in css


def test_steam_charts_css_overrides_base_plugin_page_shell():
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.css"
    ).read_text(encoding="utf-8")

    assert "margin: 0 !important;" in css
    assert "padding: 0 !important;" in css
    assert "background-image: none !important;" in css



def test_steam_charts_cover_images_are_scaled_up():
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.css"
    ).read_text(encoding="utf-8")
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "steam_charts.py"
    ).read_text(encoding="utf-8")

    assert "--image-width: 18.85vw;" in css
    assert "grid-template-columns: minmax(104px, 14.4vw) minmax(0, 1fr);" in css
    assert "cover_width = int(width * 0.1885)" in source
    assert "cover_width = max(104, int(col_width * 0.31))" in source


def test_steam_charts_compact_metric_divider_favors_title_space():
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.css"
    ).read_text(encoding="utf-8")
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "steam_charts.py"
    ).read_text(encoding="utf-8")

    assert "minmax(6.75rem, 7.65rem)" in css
    assert "metric_max_width = max(108, int(col_width * 0.30))" in source


def test_steam_charts_primary_game_title_minimum_is_scaled_up():
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.css"
    ).read_text(encoding="utf-8")
    template = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.html"
    ).read_text(encoding="utf-8")
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "steam_charts.py"
    ).read_text(encoding="utf-8")

    assert "--single-name-size: clamp(18.8px, calc(19.2px * var(--name-font-scale, 1)), 22.2px);" in css
    assert "--compact-title-size: clamp(13.8px, calc(15.4px * var(--name-font-scale, 1)), 17.4px);" in css
    assert "--compact-primary-size: clamp(12.4px, calc(16.4px * var(--metric-font-scale, 1)), 19px);" in css
    assert "--compact-metric-secondary-size: clamp(9.6px, calc(10.8px * var(--metric-font-scale, 1)), 12.6px);" in css
    assert "max-height: 2.26em;" in css
    assert 'class="compact-title" data-fit-text data-fit-min="13.8"' in template
    assert 'class="game-name-primary" data-fit-text data-fit-min="15"' in template
    assert "self._scaled_font_size(name_font_size, name_scale, 13)" in source
    assert "self._scaled_font_size(name_font_size, name_scale, 14)" in source


def test_steam_charts_combined_template_draws_compact_sparklines():
    base = Path(__file__).resolve().parents[1]
    template = (
        base
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.html"
    ).read_text(encoding="utf-8")
    css = (
        base
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.css"
    ).read_text(encoding="utf-8")
    source = (
        base
        / "src"
        / "plugins"
        / "steam_charts"
        / "steam_charts.py"
    ).read_text(encoding="utf-8")

    assert "class=\"compact-sparkline compact-sparkline--{{ group.table_variant | replace('_', '-') }}\"" in template
    assert "{{ game.sparkline_svg | safe }}" in template
    assert "--chart-ink: {{ theme_chart_ink }};" in template
    assert "--chart-ink: #064e3b;" in css
    assert "width: var(--compact-sparkline-width, 64%);" in css
    assert "margin-left: auto;" in css
    assert "overflow: visible;" in css
    assert "overflow-wrap: normal;" in css
    assert "word-break: keep-all;" in css
    assert ".compact-sparkline--top-games" in css
    assert "transform: translateY(5px);" in css
    assert "--compact-sparkline-width: 96%;" not in css
    assert "stroke: var(--chart-ink);" in css
    assert "fill: var(--chart-ink);" in css
    assert ".sparkline-cell svg rect" in css
    assert ".compact-sparkline polyline" in css
    assert ".compact-sparkline rect" in css
    assert "SPARKLINE_INK = (6, 78, 59)" in source
    assert "LINE_SPARKLINE_AMPLIFICATION = 1.55" in source
    assert "LINE_SPARKLINE_EDGE_PADDING = 2.0" in source
    assert "chart_ink = SPARKLINE_INK" in source
    assert "sparkline_y_offset = self._compact_sparkline_y_offset" in source
    assert "sparkline_bottom_gap = max(3, int(height * 0.006))" in source
    assert "BOLD_SAFE_MIDDLE_DOT = \"\\u2027\"" in source
    assert "MIDDLE_DOT_DISPLAY_TRANSLATION" in source
    assert "WORD_JOINER" not in source
    assert "CJK_MIDDLE_DOT_RE" not in source
    assert "def _draw_sparkline_svg" in source
    assert "def _compact_sparkline_width_ratio" in source
    assert "def _compact_sparkline_y_offset" in source

def test_steam_charts_template_embeds_yahei_font_faces():
    template = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.html"
    ).read_text(encoding="utf-8")

    assert 'font-family: "InkySteamYaHei"' in template
    assert '{{ yahei_font_uri }}' in template
    assert '{{ yahei_bold_font_uri or yahei_font_uri }}' in template


def test_steam_charts_template_marks_overflow_text_for_dynamic_fit():
    template = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.html"
    ).read_text(encoding="utf-8")

    assert 'data-fit-text data-fit-min="13.8">{{ game.display_name | default(game.name) }}' in template
    assert 'data-fit-text data-fit-min="12.4">{{ game.primary_metric }}' in template
    assert 'data-fit-text data-fit-min="9.6">{{ game.secondary_metric }}' in template
    assert "function fitAllText()" in template
    assert "document.fonts.ready.then(fitAllText)" in template


def test_steam_charts_removes_right_header_pixel_bar():
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.css"
    ).read_text(encoding="utf-8")

    assert ".header::after" not in css
    assert "--header-bar-uri" not in css
    assert "--header-scene-uri" not in css
    assert "header-scene-strip" not in css
    template = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.html"
    ).read_text(encoding="utf-8")
    assert "header-scene-strip" not in template
    assert "--header-scene-uri" not in template


def test_steam_charts_uses_transparent_title_wordmark():
    template = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.html"
    ).read_text(encoding="utf-8")
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "render"
        / "steam_charts.css"
    ).read_text(encoding="utf-8")
    wordmark = Image.open(STEAM_TITLE_WORDMARK_PATH).convert("RGBA")

    assert "title_wordmark_uri" in template
    assert "steam_logo_uri" in template
    assert "class=\"steam-logo\"" in template
    assert "--logo-size: min(11.31dvh, 7.15vw);" in css
    assert "--logo-size: min(8.84dvh, 4.03rem);" in css
    assert "class=\"header-kaiju\"" in template
    assert ".header-kaiju" in css
    assert "display: none;" in css
    assert ".layout-combined .header-kaiju" not in css
    assert "transform: translate(0.8vw, 9dvh);" in css
    assert template.index("steam_logo_uri") < template.index("title_wordmark_uri")
    assert "class=\"title-wordmark\"" in template
    assert ".title-wordmark" in css
    assert "width: min(60vw, 30rem);" in css
    assert "height: min(22.2dvh, 6.75rem);" in css
    assert "margin-bottom: -0.72dvh;" in css
    assert "margin-left: auto;" in css
    assert "transform: translateY(-4.2dvh);" in css
    assert "height: calc(100% + 4.2dvh);" in css
    assert wordmark.size == (320, 72)
    assert wordmark.getbbox() is not None
    assert wordmark.getchannel("A").getextrema()[0] == 0


def test_steam_charts_title_wordmark_tints_for_night_theme():
    wordmark = SteamCharts._theme_title_wordmark(
        {"ink": (255, 255, 255), "paper": (0, 0, 0)}
    )

    assert wordmark is not None
    opaque_colors = [
        pixel[:3]
        for count, pixel in wordmark.getcolors(maxcolors=wordmark.width * wordmark.height)
        if pixel[3] > 220
    ]
    assert (255, 255, 255) in opaque_colors


def test_steam_charts_combined_fallback_places_logo_in_title_gap(monkeypatch):
    logo_calls = []
    wordmark_calls = []

    def fail_paste_kaiju(*_args):
        raise AssertionError("Steam Charts fallback should hide the header kaiju")

    monkeypatch.setattr(SteamCharts, "_paste_pixel_kaiju", staticmethod(fail_paste_kaiju))

    def fake_paste_wordmark(_target, x, y, max_size, _theme_colors):
        wordmark_calls.append((x, y, max_size))
        return True

    monkeypatch.setattr(SteamCharts, "_paste_title_wordmark", staticmethod(fake_paste_wordmark))

    def fake_paste_logo(_target, x, y, size, _theme_colors):
        logo_calls.append((x, y, size))

    monkeypatch.setattr(SteamCharts, "_paste_steam_logo", staticmethod(fake_paste_logo))

    plugin = _plugin()
    plugin._render_combined_fallback_image(
        (800, 480),
        "Live Overview",
        [{"title": "Trending Top 5", "subtitle": "24h movers", "games": []}],
    )

    assert logo_calls == [(26, 22, 42)]
    assert wordmark_calls == [(71, 18, (288, 51))]
    logo_y, logo_size = logo_calls[0][1], logo_calls[0][2]
    wordmark_y, wordmark_height = wordmark_calls[0][1], wordmark_calls[0][2][1]
    assert abs((logo_y + logo_size / 2) - (wordmark_y + wordmark_height / 2)) <= 1


def test_steam_charts_single_fallback_aligns_logo_with_title_wordmark(monkeypatch):
    logo_calls = []
    wordmark_calls = []

    def fake_paste_wordmark(_target, x, y, max_size, _theme_colors):
        wordmark_calls.append((x, y, max_size))
        return True

    monkeypatch.setattr(SteamCharts, "_paste_title_wordmark", staticmethod(fake_paste_wordmark))

    def fake_paste_logo(_target, x, y, size, _theme_colors):
        logo_calls.append((x, y, size))

    monkeypatch.setattr(SteamCharts, "_paste_steam_logo", staticmethod(fake_paste_logo))

    plugin = _plugin()
    plugin._render_fallback_image(
        (800, 480),
        "Player Count",
        "top_games",
        [],
        show_images=False,
    )

    assert logo_calls == [(28, 22, 54)]
    assert wordmark_calls == [(85, 20, (312, 57))]
    logo_y, logo_size = logo_calls[0][1], logo_calls[0][2]
    wordmark_y, wordmark_height = wordmark_calls[0][1], wordmark_calls[0][2][1]
    assert abs((logo_y + logo_size / 2) - (wordmark_y + wordmark_height / 2)) <= 1


def test_generate_image_skips_html_render_when_pil_first_configured():
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
    plugin.render_image = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("HTML renderer should be skipped")
    )

    image = plugin.generate_image(
        {
            "mode": "new_trending",
            "itemsCount": "1",
            "showImages": "false",
            "themeMode": "night",
            "preferPilFallback": "true",
        },
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert "inkypi_skip_cache" not in image.info


def test_prefer_pil_fallback_first_honors_setting_and_env(monkeypatch):
    assert SteamCharts._prefer_pil_fallback_first({}) is False
    assert SteamCharts._prefer_pil_fallback_first({"preferPilFallback": "true"}) is True
    assert SteamCharts._prefer_pil_fallback_first({"preferPilFallback": "false"}) is False

    monkeypatch.setenv("INKYPI_STEAM_CHARTS_PIL_FIRST", "1")
    assert SteamCharts._prefer_pil_fallback_first({}) is True

    monkeypatch.setenv("INKYPI_STEAM_CHARTS_PIL_FIRST", "0")
    assert SteamCharts._prefer_pil_fallback_first({}) is False

def test_steam_charts_pixel_kaiju_asset_is_transparent_header_cutout():
    kaiju = Image.open(STEAM_PIXEL_KAIJU_PATH).convert("RGBA")
    alpha = kaiju.getchannel("A")

    assert kaiju.size == (168, 92)
    assert alpha.getbbox() is not None
    assert alpha.getextrema() == (0, 255)
    assert kaiju.getpixel((0, 0))[3] == 0
    assert kaiju.getpixel((kaiju.width - 1, 0))[3] == 0


def test_sans_font_paths_prefer_static_yahei_before_fallbacks():
    assert SANS_FONT_PATHS["normal"][0] == STATIC_YAHEI_FONT_PATH
    assert SANS_FONT_PATHS["bold"][1] == STATIC_YAHEI_FONT_PATH
    assert "NotoSansSC" not in SANS_FONT_PATHS["normal"][0]


def test_font_file_uri_exports_yahei_file_uri(monkeypatch, tmp_path):
    noto = tmp_path / "NotoSansSC-VF.ttf"
    yahei = tmp_path / "msyh.ttc"
    noto.write_bytes(b"noto")
    yahei.write_bytes(b"yahei")
    monkeypatch.setattr(
        SteamCharts,
        "_preferred_sans_font_paths",
        staticmethod(lambda weight="normal": (str(noto), str(yahei))),
    )
    monkeypatch.setattr(
        steam_charts_module,
        "resolve_base_ui_font_path",
        lambda bold=False: (_ for _ in ()).throw(OSError("shared font unavailable")),
    )

    assert SteamCharts._font_file_uri("normal") == yahei.resolve().as_uri()


def test_font_file_uri_uses_shared_durable_font_path(monkeypatch, tmp_path):
    yahei = tmp_path / "msyhbd.ttc"
    yahei.write_bytes(b"yahei")
    calls = []
    monkeypatch.setattr(
        steam_charts_module,
        "resolve_base_ui_font_path",
        lambda bold=False: calls.append(bold) or str(yahei),
        raising=False,
    )
    monkeypatch.setattr(
        steam_charts_module,
        "font_file_uri",
        lambda path: Path(path).resolve().as_uri(),
        raising=False,
    )
    monkeypatch.setattr(
        SteamCharts,
        "_preferred_sans_font_paths",
        staticmethod(lambda weight="normal": ()),
    )

    assert SteamCharts._font_file_uri("bold") == yahei.resolve().as_uri()
    assert calls == [True]


def test_font_file_uri_exports_shared_tracked_fallback(monkeypatch, tmp_path):
    noto = tmp_path / "NotoSansSC-VF.ttf"
    noto.write_bytes(b"noto")
    monkeypatch.setattr(
        steam_charts_module,
        "resolve_base_ui_font_path",
        lambda bold=False: str(noto),
    )
    monkeypatch.setattr(
        steam_charts_module,
        "font_file_uri",
        lambda path: Path(path).resolve().as_uri(),
    )

    assert SteamCharts._font_file_uri("normal") == noto.resolve().as_uri()

def test_font_match_rejects_non_cjk_sans_fallbacks():
    assert SteamCharts._is_accepted_sans_match("Microsoft YaHei", "DejaVu Sans") is False
    assert SteamCharts._is_accepted_sans_match("Noto Sans SC", "Noto Sans SC") is True


def test_updated_at_uses_device_timezone():
    utc_now = datetime(2026, 5, 28, 2, 15, tzinfo=ZoneInfo("UTC"))

    assert SteamCharts._format_updated_at(FakeDeviceConfig(), utc_now) == "\u5237\u65b0\u65f6\u95f4 05/27 19:15"


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

def test_header_bar_asset_is_exact_transparent_slot():
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "assets"
        / "steam_header_pixel_bar.png"
    )

    image = Image.open(path).convert("RGBA")

    assert image.size == (67, 48)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((66, 47))[3] == 0
    assert image.getchannel("A").getbbox() is not None

def test_header_scene_asset_is_exact_transparent_pixel_level_strip():
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "steam_charts"
        / "assets"
        / "steam_header_pixel_level.png"
    )

    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")

    assert image.size == (320, 44)
    assert alpha.getbbox() is not None
    assert alpha.getextrema() == (0, 255)


def test_steam_charts_base_font_uses_shared_resolver(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        steam_charts_module,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
        raising=False,
    )

    assert SteamCharts._font(20, "bold") is sentinel
    assert calls == [(20, True)]
