import os
import sys
from typing import Dict, Optional, Tuple

from PIL import Image, ImageFont


SOURCE_SIZE: Tuple[int, int] = (1360, 480)
TARGET_SIZE: Tuple[int, int] = (800, 480)
DEFAULT_ADAPT_MODE = os.environ.get("EPAPER_ADAPT_MODE", "squash").strip().lower()


class CanvasEPD:
    width = SOURCE_SIZE[0]
    height = SOURCE_SIZE[1]


def _resample_filter():
    if hasattr(Image, "Resampling"):
        return Image.Resampling.LANCZOS
    return Image.LANCZOS


def load_fonts(font_dir: str):
    def load_font(name, size):
        return ImageFont.truetype(os.path.join(font_dir, name), size)

    return {
        "20": load_font("Aldrich-Regular.ttc", 20),
        "24": load_font("Aldrich-Regular.ttc", 24),
        "28": load_font("Aldrich-Regular.ttc", 28),
        "32": load_font("Aldrich-Regular.ttc", 32),
        "35": load_font("Aldrich-Regular.ttc", 35),
        "40": load_font("Aldrich-Regular.ttc", 40),
        "60": load_font("Aldrich-Regular.ttc", 60),
        "80": load_font("Aldrich-Regular.ttc", 80),
        "clock": load_font("advanced_led_board-7.ttc", 180),
    }


def adapt_for_7in5(image: Image.Image, mode: Optional[str] = None) -> Image.Image:
    mode = (mode or DEFAULT_ADAPT_MODE).strip().lower()
    source = image.convert("L")

    if mode == "squash":
        # Keeps every 10.85-inch widget visible at 800x480, but compresses width.
        adapted = source.resize(TARGET_SIZE, _resample_filter())
    elif mode == "fit":
        # Keeps aspect ratio and all content, with vertical whitespace.
        scale = min(TARGET_SIZE[0] / source.width, TARGET_SIZE[1] / source.height)
        resized_size = (max(1, int(source.width * scale)), max(1, int(source.height * scale)))
        resized = source.resize(resized_size, _resample_filter())
        adapted = Image.new("L", TARGET_SIZE, 255)
        adapted.paste(resized, ((TARGET_SIZE[0] - resized.width) // 2, (TARGET_SIZE[1] - resized.height) // 2))
    elif mode in {"crop-left", "crop-center", "crop-right"}:
        # Keeps original scale, but only one 800px window is visible.
        if source.height != TARGET_SIZE[1]:
            source = source.resize((source.width, TARGET_SIZE[1]), _resample_filter())
        if mode == "crop-left":
            left = 0
        elif mode == "crop-right":
            left = max(0, source.width - TARGET_SIZE[0])
        else:
            left = max(0, (source.width - TARGET_SIZE[0]) // 2)
        adapted = source.crop((left, 0, left + TARGET_SIZE[0], TARGET_SIZE[1]))
    else:
        raise ValueError("EPAPER_ADAPT_MODE must be squash, fit, crop-left, crop-center, or crop-right")

    return adapted.point(lambda pixel: 0 if pixel < 160 else 255, "1")


def save_preview_outputs(full_image: Image.Image, target_image: Image.Image, output_dir: str) -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    paths = {
        "full_png": os.path.abspath(os.path.join(output_dir, "dashboard-10in85-source.png")),
        "target_png": os.path.abspath(os.path.join(output_dir, "dashboard-7in5-preview.png")),
        "target_bmp": os.path.abspath(os.path.join(output_dir, "dashboard-7in5-display.bmp")),
    }
    full_image.save(paths["full_png"])
    target_image.save(paths["target_png"])
    target_image.save(paths["target_bmp"])
    return paths


def import_epd7in5_v2(base_dir: str):
    local_lib = os.path.abspath(os.path.join(base_dir, "lib"))
    original_path = list(sys.path)
    existing_package = sys.modules.pop("waveshare_epd", None)

    try:
        sys.path = [
            path for path in sys.path
            if os.path.abspath(path or os.getcwd()) != local_lib
        ]
        from waveshare_epd import epd7in5_V2
        return epd7in5_V2
    except Exception as exc:
        raise RuntimeError(
            "Cannot import waveshare_epd.epd7in5_V2. On Raspberry Pi, install it with: "
            "python3 -m pip install waveshare-epaper"
        ) from exc
    finally:
        sys.path = original_path
        if existing_package is not None and "waveshare_epd" not in sys.modules:
            sys.modules["waveshare_epd"] = existing_package
