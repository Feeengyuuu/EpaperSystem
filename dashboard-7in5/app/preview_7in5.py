import argparse
import os
import sys
from collections import deque
from datetime import datetime, timedelta

sys.dont_write_bytecode = True

import main as dashboard
from epaper_7in5_adapter import CanvasEPD, adapt_for_7in5, load_fonts, save_preview_outputs
from render_7in5_layout import load_7in5_fonts, render_screen_7in5


def seed_preview_data():
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    times = [(now + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(8)]

    with dashboard.data_store.lock:
        dashboard.data_store.weather = {
            "current": {
                "temperature_2m": 22.4,
                "relative_humidity_2m": 48,
                "surface_pressure": 1014,
                "wind_speed_10m": 12,
                "wind_direction_10m": 260,
                "weather_code": 1,
                "is_day": 1,
                "uv_index": 5.2,
            },
            "hourly": {
                "time": times,
                "temperature_2m": [22.4, 23.1, 24.0, 24.5, 23.8, 22.9, 21.7, 20.9],
                "weather_code": [1, 2, 3, 61, 61, 2, 1, 0],
            },
        }
        dashboard.data_store.aqi = 42
        dashboard.data_store.sysload = {
            "cpu": 28,
            "ram_free": 412,
            "history": deque([12, 18, 16, 22, 31, 28, 35, 26, 21, 29, 33, 27], maxlen=30),
        }
        dashboard.data_store.crypto = {
            "btc": 68420,
            "eth": 3725,
            "btc_hist": [64500, 65100, 64600, 66000, 66800, 67100, 68420],
            "eth_hist": [3420, 3510, 3480, 3590, 3660, 3705, 3725],
        }
        dashboard.data_store.ping = {
            "current": 23,
            "history": deque([21, 22, 25, 28, 24, 23, 27, 20, 21, 23], maxlen=50),
        }
        dashboard.data_store.gmail_unread = 12


def main():
    parser = argparse.ArgumentParser(description="Render the dashboard into a 7.5-inch 800x480 preview image.")
    parser.add_argument(
        "--mode",
        default=os.environ.get("EPAPER_UI_MODE", "layout"),
        choices=["layout", "squash", "fit", "crop-left", "crop-center", "crop-right"],
        help="Use native 800x480 layout, or a legacy 1360x480 mapping mode.",
    )
    parser.add_argument("--output-dir", default=os.path.join(dashboard.BASE_DIR, "output"))
    args = parser.parse_args()

    seed_preview_data()
    source_image = dashboard.render_screen(CanvasEPD(), load_fonts(dashboard.FONT_DIR))
    if args.mode == "layout":
        target_image = render_screen_7in5(dashboard, load_7in5_fonts(dashboard.FONT_DIR))
    else:
        target_image = adapt_for_7in5(source_image, args.mode)
    paths = save_preview_outputs(source_image, target_image, args.output_dir)

    print("Preview rendered:")
    for key, path in paths.items():
        print(f"{key}: {path}")


if __name__ == "__main__":
    main()
