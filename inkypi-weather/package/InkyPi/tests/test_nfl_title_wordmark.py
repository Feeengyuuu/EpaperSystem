from PIL import Image, ImageDraw

import plugins.sports_dashboard.common as common
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


def test_nfl_header_uses_wordmark_and_preserves_shield_and_status(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    calls, shields, statuses, texts = [], [], [], []
    canvas = Image.new("RGB", (552, 60), "black")
    draw = ImageDraw.Draw(canvas)
    monkeypatch.setattr(plugin, "_draw_nfl_title_wordmark",
                        lambda _image, *bounds: calls.append(bounds) or True, raising=False)
    monkeypatch.setattr(plugin, "_draw_sport_logo", lambda *args: shields.append(args[2:]))
    monkeypatch.setattr(plugin, "_draw_standalone_sport_header_cutout", lambda *args: True)
    monkeypatch.setattr(plugin, "_draw_status_pill", lambda *args: statuses.append(args[3]))
    original_text = draw.text

    def text(xy, value, *args, **kwargs):
        texts.append(value)
        return original_text(xy, value, *args, **kwargs)

    monkeypatch.setattr(draw, "text", text)
    plugin._draw_standalone_sport_header(canvas, draw, 0, 0, 551, "NFL", {"status": "NEXT"}, "HUB LIVE")
    assert calls == [(66, 7, 154, 24)]
    assert shields == [("NFL", 14, 7, 42, 34)]
    assert statuses == ["NEXT"]
    assert "NFL" not in texts and "KICKOFF" in texts


def test_nfl_header_retains_text_when_wordmark_cannot_load(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    canvas = Image.new("RGB", (552, 60), "black")
    draw = ImageDraw.Draw(canvas)
    texts = []
    monkeypatch.setattr(plugin, "_load_local_logo", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin, "_draw_sport_logo", lambda *args: None)
    monkeypatch.setattr(plugin, "_draw_standalone_sport_header_cutout", lambda *args: True)
    monkeypatch.setattr(draw, "text", lambda _xy, value, *args, **kwargs: texts.append(value))
    plugin._draw_standalone_sport_header(canvas, draw, 0, 0, 551, "NFL", {"status": "NEXT"}, "HUB LIVE")
    assert "NFL" in texts and "KICKOFF" in texts


def test_nfl_wordmark_asset_is_transparent_and_reuses_decoded_cache(monkeypatch):
    from pathlib import Path

    path = Path(common.LOCAL_NFL_TITLE_WORDMARK_PATH)
    assert path.stat().st_size <= 2 * 1024 * 1024
    with Image.open(path) as source:
        assert source.mode == "RGBA"
        assert source.getchannel("A").getextrema() == (0, 255)
        assert all(source.getpixel(corner)[3] == 0 for corner in
                   ((0, 0), (source.width - 1, 0), (0, source.height - 1),
                    (source.width - 1, source.height - 1)))
    calls = []
    original_open = common.safe_open_image

    def open_asset(*args, **kwargs):
        calls.append(args[0])
        return original_open(*args, **kwargs)

    monkeypatch.setattr(common, "safe_open_image", open_asset)
    common.TEAM_LOGO_CACHE.clear()
    try:
        plugin = SportsDashboard({"id": "sports_dashboard"})
        with Image.new("RGB", (180, 40), "black") as canvas:
            assert plugin._draw_nfl_title_wordmark(canvas, 5, 5, 154, 24)
            first = canvas.tobytes()
            assert canvas.getbbox() is not None
            assert plugin._draw_nfl_title_wordmark(canvas, 5, 5, 154, 24)
            assert first != bytes(len(first))
        assert len(calls) == 1
    finally:
        common.TEAM_LOGO_CACHE.clear()
