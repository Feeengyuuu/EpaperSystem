from PIL import Image, ImageEnhance, ImageOps, ImageFilter
import os
import logging
import hashlib
from pathlib import Path
from urllib.parse import unquote, urlsplit

from utils.browser_renderer import find_browser_binary, get_browser_renderer
from utils.http_client import get_http_client
from utils.safe_image import safe_open_image

logger = logging.getLogger(__name__)
def get_image(image_url):
    response = get_http_client().request_bytes("GET", image_url)
    return safe_open_image(response.data)

def change_orientation(image, orientation, inverted=False):
    if orientation == 'horizontal':
        angle = 0
    elif orientation == 'vertical':
        angle = 90

    if inverted:
        angle = (angle + 180) % 360

    return image.rotate(angle, expand=1)

def resize_image(image, desired_size, image_settings=()):
    img_width, img_height = image.size
    desired_width, desired_height = desired_size
    desired_width, desired_height = int(desired_width), int(desired_height)

    img_ratio = img_width / img_height
    desired_ratio = desired_width / desired_height

    keep_width = "keep-width" in image_settings

    x_offset, y_offset = 0,0
    new_width, new_height = img_width,img_height
    # Step 1: Determine crop dimensions
    desired_ratio = desired_width / desired_height
    if img_ratio > desired_ratio:
        # Image is wider than desired aspect ratio
        new_width = int(img_height * desired_ratio)
        if not keep_width:
            x_offset = (img_width - new_width) // 2
    else:
        # Image is taller than desired aspect ratio
        new_height = int(img_width / desired_ratio)
        if not keep_width:
            y_offset = (img_height - new_height) // 2

    # Step 2: Crop the image
    image = image.crop((x_offset, y_offset, x_offset + new_width, y_offset + new_height))

    # Step 3: Resize to the exact desired dimensions (if necessary)
    return image.resize((desired_width, desired_height), Image.LANCZOS)

def apply_image_enhancement(img, image_settings=None):
    image_settings = image_settings or {}
    # Convert image to RGB mode if necessary for enhancement operations
    # ImageEnhance requires RGB mode for operations like blend
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
        

    # Apply Brightness
    img = ImageEnhance.Brightness(img).enhance(image_settings.get("brightness", 1.0))

    # Apply Contrast
    img = ImageEnhance.Contrast(img).enhance(image_settings.get("contrast", 1.0))

    # Apply Saturation (Color)
    img = ImageEnhance.Color(img).enhance(image_settings.get("saturation", 1.0))

    # Apply Sharpness
    img = ImageEnhance.Sharpness(img).enhance(image_settings.get("sharpness", 1.0))

    return img

def compute_image_hash(image):
    """Compute SHA-256 hash of an image."""
    image = image.convert("RGB")
    img_bytes = image.tobytes()
    return hashlib.sha256(img_bytes).hexdigest()

def text_width(draw, text, font):
    """Return the rendered pixel width of text drawn with the given font."""
    bbox = draw.textbbox((0, 0), str(text), font=font)
    return bbox[2] - bbox[0]

def take_screenshot_html(html_str, dimensions, timeout_ms=None, timezone_name=None):
    try:
        timeout_seconds = ((timeout_ms or 45000) / 1000) + 15
        return get_browser_renderer().render_html(
            html_str,
            viewport=dimensions,
            timeout_seconds=timeout_seconds,
            timezone_name=timezone_name,
        )
    except Exception as e:
        logger.error(f"Failed to take screenshot: {str(e)}")
        return None

def _find_chromium_binary():
    """Compatibility alias for callers that only probe browser availability."""

    return find_browser_binary()


def take_screenshot(
    target,
    dimensions,
    timeout_ms=None,
    timezone_name=None,
    *,
    validator=None,
    task_context=None,
):
    try:
        renderer = get_browser_renderer()
        timeout_seconds = ((timeout_ms or 45000) / 1000) + 15
        parsed = urlsplit(str(target))
        if parsed.scheme.lower() in {"http", "https"}:
            return renderer.render_url(
                str(target),
                viewport=dimensions,
                context=task_context,
                validator=validator,
                timeout_seconds=timeout_seconds,
                timezone_name=timezone_name,
            )
        if parsed.scheme.lower() == "file":
            local_path = Path(unquote(parsed.path))
            if os.name == "nt" and local_path.as_posix().startswith("/"):
                local_path = Path(local_path.as_posix().lstrip("/"))
        else:
            local_path = Path(target)
        html = local_path.read_text(encoding="utf-8")
        return renderer.render_html(
            html,
            viewport=dimensions,
            context=task_context,
            timeout_seconds=timeout_seconds,
            timezone_name=timezone_name,
        )
    except Exception as e:
        logger.error(f"Failed to take screenshot: {str(e)}")
        return None


def pad_image_blur(img: Image, dimensions: tuple[int, int]) -> Image:
    bkg = ImageOps.fit(img, dimensions)
    bkg = bkg.filter(ImageFilter.BoxBlur(8))
    img = ImageOps.contain(img, dimensions)

    img_size = img.size
    bkg.paste(img, ((dimensions[0] - img_size[0]) // 2, (dimensions[1] - img_size[1]) // 2))
    return bkg
