"""Recurring team icons reuse disk assets across isolated render lifetimes."""

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from plugins.sports_dashboard.sports_dashboard import SportsDashboard, TEAM_LOGO_CACHE


@pytest.mark.parametrize("series", ["CS", "TI"])
def test_valve_icon_reuses_disk_cache_after_memory_cache_is_cleared(monkeypatch, tmp_path, series):
    url = "https://example.com/teams/reusable.png"
    event = {"series": series, "team_a": "Cache Team", "team_a_id": 123, "team_a_logo": url}
    source = Image.new("RGBA", (20, 20), (12, 34, 160, 220))
    output = BytesIO()
    source.save(output, format="PNG")
    source.close()
    downloads = []

    def fetch(logo_url, timeout):
        downloads.append(logo_url)
        return output.getvalue()

    monkeypatch.setattr(SportsDashboard, "_fetch_remote_image_bytes", fetch)
    monkeypatch.setattr(SportsDashboard, "_team_logo_disk_cache_dir", lambda self: tmp_path)
    TEAM_LOGO_CACHE.clear()
    try:
        pixels = []
        for _ in range(2):
            # A different renderer object and empty memory cache represent a new
            # isolated worker. Only the downloaded asset on disk survives.
            plugin = SportsDashboard({"id": "sports_dashboard"})
            with Image.new("RGB", (48, 48), "white") as canvas:
                plugin._draw_valve_team_icon(canvas, ImageDraw.Draw(canvas), event, "a", 8, 8, 32)
                pixels.append(canvas.tobytes())
            TEAM_LOGO_CACHE.clear()
        assert downloads == [url]
        assert pixels[0] == pixels[1]
        assert len(list(tmp_path.iterdir())) == 1
    finally:
        TEAM_LOGO_CACHE.clear()
