from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image
import logging
import random
import os

logger = logging.getLogger(__name__)

IMAGE_UPLOAD_VERSION = "no-repeat-random-bag-v1"
PORTRAIT_COLUMN_COUNT = 3


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
        dimensions = self.get_dimensions(device_config)

        # The image upload display is automatic: portrait photos render as
        # three side-by-side columns, landscape photos render as one cover image.
        display_mode = self._get_display_mode(settings)

        logger.debug(f"Settings: display_mode={display_mode}")

        selected_index = img_index
        if display_mode == "random":
            selected_index = random.randrange(0, len(image_locations))
            logger.info(f"Random mode: Selected image index {selected_index}")
        elif display_mode == "no_repeat_random":
            selected_index = self._select_no_repeat_index(settings, image_locations)
            logger.info(f"No-repeat random mode: Selected image index {selected_index}")
        else:
            logger.info(f"Sequential mode: Loading image index {selected_index}")
            img_index = (selected_index + 1) % len(image_locations)
            logger.debug(f"Next index will be: {img_index}")

        if self._is_portrait_image(image_locations[selected_index]):
            portrait_indices = self._select_portrait_group(selected_index, image_locations, display_mode)
            logger.info(f"Portrait layout: rendering indices {portrait_indices}")
            image = self._render_portrait_columns(portrait_indices, image_locations, dimensions)
        else:
            logger.info("Landscape layout: rendering one full-screen image")
            image = self.open_image(selected_index, image_locations, dimensions, resize=True)

        # Write the new index back to the device json
        settings['image_index'] = selected_index if display_mode != "sequential" else img_index

        logger.info("=== Image Upload Plugin: Image generation complete ===")
        return image

    def _is_portrait_image(self, image_path):
        try:
            with Image.open(image_path) as image:
                width, height = image.size
                exif_orientation = image.getexif().get(274)
                if exif_orientation in {5, 6, 7, 8}:
                    width, height = height, width
                return height > width
        except Exception as e:
            logger.warning(f"Could not determine image orientation for {image_path}: {e}")
            return False

    def _select_portrait_group(self, selected_index, image_locations, display_mode):
        portrait_indices = [
            index
            for index, image_path in enumerate(image_locations)
            if self._is_portrait_image(image_path)
        ]

        if selected_index not in portrait_indices:
            return [selected_index]

        if display_mode == "sequential":
            selected = []
            for offset in range(len(image_locations)):
                candidate = (selected_index + offset) % len(image_locations)
                if candidate in portrait_indices:
                    selected.append(candidate)
                    if len(selected) == PORTRAIT_COLUMN_COUNT:
                        break
        else:
            remaining = [index for index in portrait_indices if index != selected_index]
            random.shuffle(remaining)
            selected = [selected_index] + remaining[:PORTRAIT_COLUMN_COUNT - 1]

        return self._repeat_to_column_count(selected)

    def _repeat_to_column_count(self, selected_indices):
        if not selected_indices:
            return []

        index = 0
        while len(selected_indices) < PORTRAIT_COLUMN_COUNT:
            selected_indices.append(selected_indices[index % len(selected_indices)])
            index += 1
        return selected_indices[:PORTRAIT_COLUMN_COUNT]

    def _render_portrait_columns(self, portrait_indices, image_locations, dimensions):
        width, height = dimensions
        canvas = Image.new("RGB", dimensions, "white")

        for column, image_index in enumerate(portrait_indices):
            left = round(column * width / PORTRAIT_COLUMN_COUNT)
            right = round((column + 1) * width / PORTRAIT_COLUMN_COUNT)
            column_size = (right - left, height)
            image = self.open_image(image_index, image_locations, column_size, resize=True)
            canvas.paste(image, (left, 0))

        return canvas

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
