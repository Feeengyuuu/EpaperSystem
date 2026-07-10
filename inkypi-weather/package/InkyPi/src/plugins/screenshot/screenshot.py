from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image
from security.ssrf import validate_browser_target
from utils.image_utils import take_screenshot
import logging

logger = logging.getLogger(__name__)

class Screenshot(BasePlugin):
    def generate_image(self, settings, device_config):

        url = settings.get('url')
        if not url:
            raise RuntimeError("URL is required.")

        dimensions = self.get_dimensions(device_config)
        capture_dimensions = self._capture_dimensions(settings, dimensions)
        timezone_name = str(settings.get("timezone") or device_config.get_config("timezone", "") or "").strip()

        logger.info(f"Taking screenshot of url: {url}")

        image = take_screenshot(
            url,
            capture_dimensions,
            timeout_ms=40000,
            timezone_name=timezone_name,
            validator=validate_browser_target,
        )

        if not image:
            raise RuntimeError("Failed to take screenshot, please check logs.")

        if image.size != dimensions:
            image = image.resize(dimensions, Image.LANCZOS)

        return image

    @staticmethod
    def _capture_dimensions(settings, display_dimensions):
        width, height = display_dimensions
        try:
            capture_width = int(settings.get("captureWidth") or width)
        except Exception:
            capture_width = width
        try:
            capture_height = int(settings.get("captureHeight") or height)
        except Exception:
            capture_height = height
        capture_width = max(200, min(2400, capture_width))
        capture_height = max(150, min(2400, capture_height))
        return capture_width, capture_height
