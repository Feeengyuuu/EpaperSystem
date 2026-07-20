from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
INKYPI_ROOT = ROOT / "inkypi-weather" / "package" / "InkyPi"
SRC = INKYPI_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugins.sports_dashboard.sports_dashboard import SportsDashboard  # noqa: E402


class PreviewDeviceConfig:
    def __init__(self):
        self.resolution = (800, 480)

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
            "theme_mode": "day",
        }
        if key is None:
            return values
        return values.get(key, default)

    @staticmethod
    def load_env_key(key):
        return os.environ.get(key)


def preview_settings():
    return {
        "id": "sports_dashboard",
        "footballPanelMode": "club",
        "clubFootballEnabledLeagues": "PL,PD,BL1,SA,FL1",
        "clubFootballLiveRefreshEnabled": "true",
        "clubFootballLiveRefreshIntervalSeconds": "60",
        "worldCupLeftWidth": "536",
        "worldCupTopHeight": "240",
        "overlayWorldCupLocalTimes": "false",
        "nbaOffseasonPanelMode": "off",
        "ewcSidebarEnabled": "false",
        "valveEsportsEnabled": "false",
        "forceRefresh": "true",
    }


def _enable_sample_odds(plugin):
    original_attach = plugin._attach_club_api_football_odds
    samples = (
        (1.72, 3.80, 4.60),
        (2.05, 3.35, 3.55),
        (1.48, 4.40, 6.20),
        (2.30, 3.20, 3.05),
        (1.91, 3.45, 4.10),
    )

    def attach(selected, settings, device_config, timezone_info, now):
        selected = original_attach(
            selected, settings, device_config, timezone_info, now
        )
        for index, event in enumerate((selected or {}).get("rail") or []):
            if event.get("no_schedule") or plugin._club_event_has_complete_odds(event):
                continue
            home, draw, away = samples[index % len(samples)]
            api_fallback = index % 2 == 1
            event.update(
                {
                    "odds_home_decimal": home,
                    "odds_draw_decimal": draw,
                    "odds_away_decimal": away,
                    "odds_provider": "Bet365" if api_fallback else "DraftKings",
                    "odds_provider_short": "365" if api_fallback else "DK",
                    "odds_source": "API-Football" if api_fallback else "ESPN",
                }
            )
        return selected

    plugin._attach_club_api_football_odds = attach


def render_preview(output, sample_odds=False):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cache_dir = ROOT / ".tmp" / "club-football-preview-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: cache_dir
    if sample_odds:
        _enable_sample_odds(plugin)
    image = plugin.generate_image(preview_settings(), PreviewDeviceConfig()).convert("RGB")
    if image.size != (800, 480):
        raise RuntimeError(f"unexpected rendered size: {image.size}")
    image.save(output, format="PNG", optimize=True)

    with Image.open(output) as rendered:
        rendered.load()
        if rendered.size != (800, 480):
            raise RuntimeError(f"saved PNG has unexpected size: {rendered.size}")
        if rendered.mode != "RGB":
            raise RuntimeError(f"saved PNG has unexpected mode: {rendered.mode}")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render the real club-football SportsDashboard PNG")
    parser.add_argument(
        "--output",
        default=str(ROOT / "output" / "playwright" / "club-football-final.png"),
    )
    parser.add_argument(
        "--sample-odds",
        action="store_true",
        help="Fill missing live odds with labeled sample values for UI inspection.",
    )
    args = parser.parse_args(argv)
    output = render_preview(args.output, sample_odds=args.sample_odds)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())