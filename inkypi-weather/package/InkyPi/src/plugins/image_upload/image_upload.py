from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageOps, ImageColor
import logging
import random
import os

from utils.image_utils import pad_image_blur

logger = logging.getLogger(__name__)

IMAGE_UPLOAD_VERSION = "no-repeat-random-bag-v1"


class ImageUpload(BasePlugin):
    NO_REPEAT_QUEUE_KEY = "image_no_repeat_queue"
    NO_REPEAT_POOL_KEY = "image_no_repeat_pool"
    NO_REPEAT_LAST_KEY = "image_no_repeat_last"

    def open_image(self, img_index: int, image_locations: list, dimensions: tuple, resize: bool = True) -> Image:
        """
        Open image with adaptive loader for memory efficiency.

        Args:
            img_index: Index of image to load
            image_locations: List of image paths
            dimensions: Target dimensions
            resize: Whether to auto-resize (set False if manual padding needed)
        """
        if not image_locations:
            raise RuntimeError("No images provided.")

        try:
            # Use adaptive loader for memory-efficient processing
            image = self.image_loader.from_file(image_locations[img_index], dimensions, resize=resize)
            if not image:
                raise RuntimeError("Failed to load image from file")
            return image
        except Exception as e:
            logger.error(f"Failed to read image file: {str(e)}")
            raise RuntimeError("Failed to read image file.")


    def generate_image(self, settings, device_config) -> Image:
        logger.info("=== Image Upload Plugin: Starting image generation ===")

        # Get the current index from the device json
        img_index = self._safe_int(settings.get("image_index", 0))
        image_locations = self._normalize_image_locations(settings.get("imageFiles[]"))

        if not image_locations:
            logger.error("No images uploaded")
            raise RuntimeError("No images provided.")

        logger.debug(f"Total uploaded images: {len(image_locations)}")
        logger.debug(f"Current index: {img_index}")

        if img_index >= len(image_locations):
            # Prevent Index out of range issues when file list has changed
            logger.warning(f"Index {img_index} out of range, resetting to 0")
            img_index = 0

        # Get dimensions
        dimensions = device_config.get_resolution()
        orientation = device_config.get_config("orientation")
        if orientation == "vertical":
            dimensions = dimensions[::-1]
            logger.debug(f"Vertical orientation detected, dimensions: {dimensions[0]}x{dimensions[1]}")

        # Determine if we need manual padding
        needs_padding = settings.get('padImage') == "true"
        display_mode = self._get_display_mode(settings)
        background_option = settings.get('backgroundOption', 'blur')

        logger.debug(f"Settings: display_mode={display_mode}, pad_image={needs_padding}, background_option={background_option}")

        # Load image (without auto-resize if padding needed)
        if display_mode == "random":
            img_index = random.randrange(0, len(image_locations))
            logger.info(f"Random mode: Selected image index {img_index}")
            image = self.open_image(img_index, image_locations, dimensions, resize=not needs_padding)
        elif display_mode == "no_repeat_random":
            img_index = self._select_no_repeat_index(settings, image_locations)
            logger.info(f"No-repeat random mode: Selected image index {img_index}")
            image = self.open_image(img_index, image_locations, dimensions, resize=not needs_padding)
        else:
            logger.info(f"Sequential mode: Loading image index {img_index}")
            image = self.open_image(img_index, image_locations, dimensions, resize=not needs_padding)
            img_index = (img_index + 1) % len(image_locations)
            logger.debug(f"Next index will be: {img_index}")

        # Write the new index back to the device json
        settings['image_index'] = img_index

        # Apply padding if requested
        if needs_padding:
            logger.debug(f"Applying padding with {background_option} background")
            if background_option == "blur":
                image = pad_image_blur(image, dimensions)
            else:
                background_color = ImageColor.getcolor(settings.get('backgroundColor') or "white", image.mode)
                image = ImageOps.pad(image, dimensions, color=background_color, method=Image.Resampling.LANCZOS)

        logger.info("=== Image Upload Plugin: Image generation complete ===")
        return image

    def _get_display_mode(self, settings):
        display_mode = settings.get("displayMode")
        if display_mode in {"no_repeat_random", "random", "sequential"}:
            return display_mode

        # Backward compatibility for existing saved plugin instances.
        if settings.get("randomize") == "true":
            return "no_repeat_random"
        return "sequential"

    def _normalize_image_locations(self, image_locations):
        if not image_locations:
            return []
        if isinstance(image_locations, list):
            return image_locations
        return [image_locations]

    def _safe_int(self, value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _select_no_repeat_index(self, settings, image_locations):
        pool = list(image_locations)
        previous_pool = self._normalize_image_locations(settings.get(self.NO_REPEAT_POOL_KEY))
        queue = self._normalize_image_locations(settings.get(self.NO_REPEAT_QUEUE_KEY))
        last_selected = settings.get(self.NO_REPEAT_LAST_KEY)

        pool_changed = previous_pool != pool
        queue = [image_path for image_path in queue if image_path in pool]

        if pool_changed:
            queue = []

        if not queue:
            queue = list(pool)
            random.shuffle(queue)
            self._avoid_immediate_repeat(queue, last_selected)

        selected_path = queue.pop(0)
        settings[self.NO_REPEAT_POOL_KEY] = pool
        settings[self.NO_REPEAT_QUEUE_KEY] = queue
        settings[self.NO_REPEAT_LAST_KEY] = selected_path

        return pool.index(selected_path)

    def _avoid_immediate_repeat(self, queue, last_selected):
        if not last_selected or len(queue) < 2 or queue[0] != last_selected:
            return

        for index, image_path in enumerate(queue[1:], start=1):
            if image_path != last_selected:
                queue[0], queue[index] = queue[index], queue[0]
                return

    def cleanup(self, settings):
        """Delete all uploaded image files associated with this plugin instance."""
        image_locations = settings.get("imageFiles[]", [])
        if not image_locations:
            return

        for image_path in image_locations:
            if os.path.exists(image_path):
                try:
                    os.remove(image_path)
                    logger.info(f"Deleted uploaded image: {image_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete uploaded image {image_path}: {e}")
