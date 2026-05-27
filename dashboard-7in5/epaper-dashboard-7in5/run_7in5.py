import argparse
import gc
import logging
import os
import sys
import threading
import time

sys.dont_write_bytecode = True

import main as dashboard
from epaper_7in5_adapter import (
    CanvasEPD,
    adapt_for_7in5,
    import_epd7in5_v2,
    load_fonts,
    save_preview_outputs,
)
from render_7in5_layout import load_7in5_fonts, render_screen_7in5


def start_data_threads():
    dashboard.auth_strava()
    dashboard.auth_claude()
    dashboard.auth_antigravity()
    roborock_user_data = dashboard.auth_roborock(dashboard.ROBOROCK_CONF["EMAIL"])

    data_thread = threading.Thread(target=dashboard.update_data_thread, daemon=True)
    data_thread.start()

    if dashboard.ENABLE_ROBOROCK:
        roborock_thread = threading.Thread(
            target=dashboard.roborock_update_thread,
            args=(roborock_user_data, dashboard.ROBOROCK_CONF["EMAIL"]),
            daemon=True,
        )
        roborock_thread.start()


def main():
    parser = argparse.ArgumentParser(description="Run the dashboard on a Waveshare 7.5-inch 800x480 e-Paper HAT.")
    parser.add_argument("--once", action="store_true", help="Render and display one frame, then exit.")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between e-paper refreshes.")
    parser.add_argument(
        "--mode",
        default=os.environ.get("EPAPER_UI_MODE", "layout"),
        choices=["layout", "squash", "fit", "crop-left", "crop-center", "crop-right"],
        help="Use native 800x480 layout, or a legacy 1360x480 mapping mode.",
    )
    parser.add_argument("--no-save", action="store_true", help="Do not write latest preview images to output/.")
    args = parser.parse_args()

    epd_module = import_epd7in5_v2(dashboard.BASE_DIR)
    epd = epd_module.EPD()
    source_fonts = load_fonts(dashboard.FONT_DIR)
    target_fonts = load_7in5_fonts(dashboard.FONT_DIR)
    canvas_epd = CanvasEPD()
    output_dir = os.path.join(dashboard.BASE_DIR, "output")

    try:
        logging.info("Initializing 7.5-inch e-paper display")
        epd.init()
        epd.Clear()
        time.sleep(1)

        start_data_threads()

        while True:
            start = time.time()
            source_image = dashboard.render_screen(canvas_epd, source_fonts)
            if args.mode == "layout":
                target_image = render_screen_7in5(dashboard, target_fonts)
            else:
                target_image = adapt_for_7in5(source_image, args.mode)

            if not args.no_save:
                save_preview_outputs(source_image, target_image, output_dir)

            logging.info("Refreshing 7.5-inch display")
            epd.display(epd.getbuffer(target_image))

            del source_image
            del target_image
            gc.collect()

            if args.once:
                break

            elapsed = time.time() - start
            time.sleep(max(5, args.interval - elapsed))
    except KeyboardInterrupt:
        logging.info("Stopped by user")
    finally:
        try:
            epd.sleep()
        except Exception:
            pass
        try:
            epd_module.epdconfig.module_exit(cleanup=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
