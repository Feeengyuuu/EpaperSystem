"""
Adaptive Image Loader for InkyPi
Centralized image loading and processing with device-aware optimizations.

Automatically uses memory-efficient strategies on low-RAM devices (Pi Zero)
and high-performance strategies on capable devices (Pi 3/4).
"""

from PIL import Image, ImageFilter, ImageOps, ImageStat
from utils.http_client import get_http_session
from utils.safe_image import ImageLimits, safe_open_image
import logging
import gc
import psutil
import tempfile
import os
import requests

logger = logging.getLogger(__name__)
DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES = 25 * 1024 * 1024
SPOOL_MEMORY_BYTES = 1024 * 1024


def _is_low_resource_device():
    """
    Detect if running on a low-resource device (e.g., Raspberry Pi Zero).
    Returns True if device has less than 1GB RAM, False otherwise.
    """
    try:
        total_memory_gb = psutil.virtual_memory().total / (1024 ** 3)
        is_low_resource = total_memory_gb < 1.0
        logger.debug(f"Device RAM: {total_memory_gb:.2f}GB - Low resource mode: {is_low_resource}")
        return is_low_resource
    except Exception as e:
        # If we can't detect, assume low resource to be safe
        logger.warning(f"Could not detect device memory: {e}. Defaulting to low-resource mode.")
        return True


class AdaptiveImageLoader:
    """
    Centralized image loading with device-adaptive optimizations.

    Features:
    - Automatic device detection (low-resource vs high-performance)
    - Memory-efficient loading using temp files + PIL draft mode on Pi Zero
    - Fast in-memory loading on powerful devices
    - Automatic resizing with quality-appropriate filters
    - RGB conversion for e-ink compatibility
    - Comprehensive error handling and logging

    Usage:
        loader = AdaptiveImageLoader()
        image = loader.from_url("https://...", (800, 480))
        image = loader.from_file("/path/to/image.jpg", (800, 480))
    """

    # Default headers to avoid 403 errors from sites that block requests without User-Agent
    DEFAULT_HEADERS = {
        'User-Agent': 'InkyPi/1.0 (https://github.com/fatihak/InkyPi/) Python-requests'
    }

    def __init__(self):
        self.is_low_resource = _is_low_resource_device()

    def from_url(self, url, dimensions, timeout_ms=40000, resize=True, headers=None, focus_crop=False, max_bytes=None):
        """
        Load an image from a URL and optionally resize it.

        Args:
            url: Image URL to download
            dimensions: Target dimensions as (width, height)
            timeout_ms: Request timeout in milliseconds
            resize: Whether to resize the image (default True)
            headers: Optional dict of HTTP headers to include in request
            focus_crop: Bias cover-crop toward the most detailed area
            max_bytes: Optional positive response body limit. Invalid or non-positive
                values fall back to the safe default.

        Returns:
            PIL Image object resized to dimensions, or None on error
        """
        logger.debug(f"Loading image from URL: {url}")
        max_bytes = self._max_download_bytes(max_bytes)

        if self.is_low_resource:
            return self._load_from_url_lowmem(url, dimensions, timeout_ms, resize, headers, focus_crop, max_bytes)
        else:
            return self._load_from_url_fast(url, dimensions, timeout_ms, resize, headers, focus_crop, max_bytes)

    def from_file(self, path, dimensions, resize=True, focus_crop=False):
        """
        Load an image from a local file and optionally resize it.

        Args:
            path: Path to local image file
            dimensions: Target dimensions as (width, height)
            resize: Whether to resize the image (default True)
            focus_crop: Bias cover-crop toward the most detailed area

        Returns:
            PIL Image object resized to dimensions, or None on error
        """
        logger.debug(f"Loading image from file: {path}")

        if not os.path.exists(path):
            logger.error(f"File not found: {path}")
            return None

        try:
            if self.is_low_resource:
                return self._load_from_file_lowmem(path, dimensions, resize, focus_crop)
            else:
                return self._load_from_file_fast(path, dimensions, resize, focus_crop)
        except Exception as e:
            logger.error(f"Error loading image from {path}: {e}")
            return None

    def from_bytesio(self, data, dimensions, resize=True, focus_crop=False):
        """
        Load an image from BytesIO object and optionally resize it.

        Args:
            data: BytesIO object containing image data
            dimensions: Target dimensions as (width, height)
            resize: Whether to resize the image (default True)
            focus_crop: Bias cover-crop toward the most detailed area

        Returns:
            PIL Image object resized to dimensions, or None on error
        """
        logger.debug("Loading image from BytesIO")

        try:
            return self._decode_and_process(
                data,
                dimensions,
                resize,
                focus_crop,
                low_memory=self.is_low_resource,
            )
        except Exception as e:
            logger.error(f"Error loading image from BytesIO: {e}")
            return None

    # ========== LOW-RESOURCE IMPLEMENTATIONS ==========

    def _load_from_url_lowmem(self, url, dimensions, timeout_ms, resize, headers=None, focus_crop=False, max_bytes=None):
        """Low-memory URL loading through one bounded spooled stream."""
        return self._load_from_url_streamed(
            url,
            dimensions,
            timeout_ms,
            resize,
            headers,
            focus_crop,
            max_bytes,
            low_memory=True,
        )

    def _load_from_file_lowmem(self, path, dimensions, resize, focus_crop=False):
        """Low-memory file loading through the shared bounded decoder."""
        try:
            return self._decode_and_process(
                path,
                dimensions,
                resize,
                focus_crop,
                low_memory=True,
            )

        except MemoryError as e:
            logger.error(f"Out of memory while loading {path}: {e}")
            logger.error("Try using a smaller image or enabling more swap space")
            gc.collect()
            return None
        except Exception as e:
            logger.error(f"Error loading image from {path}: {e}")
            return None

    # ========== HIGH-PERFORMANCE IMPLEMENTATIONS ==========

    def _load_from_url_fast(self, url, dimensions, timeout_ms, resize, headers=None, focus_crop=False, max_bytes=None):
        """High-performance URL loading through one bounded spooled stream."""
        return self._load_from_url_streamed(
            url,
            dimensions,
            timeout_ms,
            resize,
            headers,
            focus_crop,
            max_bytes,
            low_memory=False,
        )

    def _load_from_url_streamed(
        self,
        url,
        dimensions,
        timeout_ms,
        resize,
        headers,
        focus_crop,
        max_bytes,
        *,
        low_memory,
    ):
        try:
            logger.debug("Using bounded spooled image download")
            request_headers = {**self.DEFAULT_HEADERS, **(headers or {})}
            session = get_http_session()
            with session.get(
                url,
                timeout=timeout_ms / 1000,
                stream=True,
                headers=request_headers,
            ) as response:
                response.raise_for_status()
                with tempfile.SpooledTemporaryFile(
                    max_size=SPOOL_MEMORY_BYTES,
                    mode="w+b",
                ) as spool:
                    downloaded_bytes = self._stream_response(
                        response,
                        spool,
                        url,
                        max_bytes,
                    )
                    logger.debug(
                        "Downloaded %.1fKB to bounded spooled file",
                        downloaded_bytes / 1024,
                    )
                    spool.seek(0)
                    return self._decode_and_process(
                        spool,
                        dimensions,
                        resize,
                        focus_crop,
                        max_bytes=max_bytes,
                        low_memory=low_memory,
                    )
        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading image from {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error processing image from {url}: {e}")
            return None

    def _max_download_bytes(self, max_bytes):
        if max_bytes is None:
            max_bytes = os.getenv("INKYPI_MAX_IMAGE_DOWNLOAD_BYTES", DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES)
        try:
            max_bytes = int(max_bytes)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid INKYPI_MAX_IMAGE_DOWNLOAD_BYTES value '%s'; using %s",
                max_bytes,
                DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES,
            )
            max_bytes = DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES
        if max_bytes <= 0:
            logger.warning(
                "Non-positive image download limit '%s'; using %s",
                max_bytes,
                DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES,
            )
            return DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES
        return max_bytes

    def _stream_response(self, response, target, url, max_bytes):
        downloaded_bytes = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            downloaded_bytes += len(chunk)
            self._raise_if_download_too_large(url, downloaded_bytes, max_bytes)
            target.write(chunk)
        return downloaded_bytes

    def _raise_if_download_too_large(self, url, downloaded_bytes, max_bytes):
        if max_bytes is not None and downloaded_bytes > max_bytes:
            raise ValueError(
                f"Image download exceeded {max_bytes} bytes. | "
                f"url: {url} | downloaded_bytes: {downloaded_bytes}"
            )

    def _load_from_file_fast(self, path, dimensions, resize, focus_crop=False):
        """High-performance file loading through the shared bounded decoder."""
        try:
            return self._decode_and_process(path, dimensions, resize, focus_crop)

        except Exception as e:
            logger.error(f"Error loading image from {path}: {e}")
            return None

    def _decode_and_process(
        self,
        source,
        dimensions,
        resize,
        focus_crop=False,
        max_bytes=None,
        *,
        low_memory=False,
    ):
        limits = ImageLimits(
            max_bytes=max_bytes or DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES,
        )
        draft_size = None
        if low_memory and resize:
            draft_size = (
                max(1, int(dimensions[0]) * 2),
                max(1, int(dimensions[1]) * 2),
            )
        img = safe_open_image(source, limits=limits, draft_size=draft_size)
        original_size = img.size
        original_pixels = original_size[0] * original_size[1]
        logger.info(
            "Loaded image: %sx%s (%s mode, %.1fMP)",
            original_size[0],
            original_size[1],
            img.mode,
            original_pixels / 1_000_000,
        )
        if resize:
            return self._process_and_resize(img, dimensions, original_size, focus_crop)
        return img

    # ========== SHARED PROCESSING LOGIC ==========

    def _process_and_resize(self, img, dimensions, original_size, focus_crop=False):
        """
        Process and resize image with device-appropriate optimizations.

        Args:
            img: PIL Image object
            dimensions: Target dimensions (width, height)
            original_size: Original image size for logging
            focus_crop: Bias cover-crop toward the most detailed area

        Returns:
            Processed and resized PIL Image
        """
        # Apply EXIF orientation correction first (before any processing)
        # This handles images from cameras/phones that store rotation in EXIF metadata
        # Safe to call on any image - returns unchanged if no EXIF data present
        img = ImageOps.exif_transpose(img)
        if img.size != original_size:
            logger.debug(f"EXIF orientation applied: {original_size[0]}x{original_size[1]} -> {img.size[0]}x{img.size[1]}")
        
        # Convert to RGB if necessary (removes alpha channel, saves memory)
        # E-ink displays don't need alpha channel anyway
        if img.mode in ('RGBA', 'LA', 'P'):
            logger.debug(f"Converting image from {img.mode} to RGB")
            img = img.convert('RGB')

        # Choose processing strategy based on device capabilities
        if self.is_low_resource:
            img = self._resize_low_resource(img, dimensions, focus_crop)
        else:
            img = self._resize_high_performance(img, dimensions, focus_crop)

        logger.info(f"Image processing complete: {dimensions[0]}x{dimensions[1]}")
        return img

    def _resize_low_resource(self, img, dimensions, focus_crop=False):
        """Memory-efficient resize for low-resource devices."""
        logger.debug("Using memory-efficient processing (BICUBIC filter)")

        # For very large images, use two-stage resize
        if img.size[0] > dimensions[0] * 2 or img.size[1] > dimensions[1] * 2:
            logger.debug(f"Image is {img.size[0]}x{img.size[1]}, using two-stage resize")

            # Stage 1: Aggressive downsample using thumbnail (in-place, very memory efficient)
            aspect = img.size[0] / img.size[1]
            if aspect > 1:  # Landscape
                intermediate_size = (dimensions[0] * 2, int(dimensions[0] * 2 / aspect))
            else:  # Portrait
                intermediate_size = (int(dimensions[1] * 2 * aspect), dimensions[1] * 2)

            logger.debug(f"Stage 1: Downsampling to ~{intermediate_size[0]}x{intermediate_size[1]} using NEAREST")
            img.thumbnail(intermediate_size, Image.NEAREST)
            logger.debug(f"Stage 1 complete: {img.size[0]}x{img.size[1]}")
            gc.collect()

            # Stage 2: High-quality resize to exact dimensions
            logger.debug(f"Stage 2: Final resize to {dimensions[0]}x{dimensions[1]} using LANCZOS")
            img = self._fit_image(img, dimensions, Image.LANCZOS, focus_crop)
            logger.debug(f"Stage 2 complete: {dimensions[0]}x{dimensions[1]}")
        else:
            # Direct resize with BICUBIC (fast, sufficient quality for e-ink)
            logger.debug(f"Resizing directly from {img.size[0]}x{img.size[1]} to {dimensions[0]}x{dimensions[1]}")
            img = self._fit_image(img, dimensions, Image.BICUBIC, focus_crop)

        # Explicit garbage collection
        gc.collect()
        logger.debug("Garbage collection completed")

        return img

    def _resize_high_performance(self, img, dimensions, focus_crop=False):
        """High-quality resize for powerful devices."""
        logger.debug("Using high-quality processing (LANCZOS filter)")
        logger.debug(f"Resizing from {img.size[0]}x{img.size[1]} to {dimensions[0]}x{dimensions[1]}")

        return self._fit_image(img, dimensions, Image.LANCZOS, focus_crop)

    def _fit_image(self, img, dimensions, method, focus_crop=False):
        if not focus_crop:
            return ImageOps.fit(img, dimensions, method=method)

        logger.debug("Using focus-aware cover crop")
        return self._focus_crop_fit(img, dimensions, method)

    def _focus_crop_fit(self, img, dimensions, method):
        target_width, target_height = int(dimensions[0]), int(dimensions[1])
        target_ratio = target_width / target_height
        image_ratio = img.width / img.height
        face_focus = self._face_focus_point(img)

        if abs(image_ratio - target_ratio) < 0.01:
            cropped = img
        elif image_ratio > target_ratio:
            crop_width = max(1, min(img.width, int(round(img.height * target_ratio))))
            if face_focus:
                x = self._crop_offset_for_focus(face_focus[0], img.width, crop_width)
                logger.debug(f"Using face-aware horizontal crop at x={x}")
            else:
                x = self._focus_crop_offset(img, crop_width, horizontal=True)
            cropped = img.crop((x, 0, x + crop_width, img.height))
        else:
            crop_height = max(1, min(img.height, int(round(img.width / target_ratio))))
            if face_focus:
                y = self._crop_offset_for_focus(face_focus[1], img.height, crop_height)
                logger.debug(f"Using face-aware vertical crop at y={y}")
            else:
                y = self._focus_crop_offset(img, crop_height, horizontal=False)
            cropped = img.crop((0, y, img.width, y + crop_height))

        return cropped.resize((target_width, target_height), method)

    def _crop_offset_for_focus(self, focus_coord, full_size, crop_size):
        max_offset = full_size - crop_size
        if max_offset <= 0:
            return 0
        offset = int(round(focus_coord - crop_size / 2))
        return max(0, min(max_offset, offset))

    def _face_focus_point(self, image):
        sample = image.convert("RGB")
        sample.thumbnail((220, 220), Image.BILINEAR)
        if sample.width < 16 or sample.height < 16:
            return None

        mask = self._skin_pixel_mask(sample)
        components = self._skin_components(mask, sample.width, sample.height)
        best = None
        best_score = 0

        for component in components:
            score = self._face_component_score(sample, component)
            if score > best_score:
                best = component
                best_score = score

        if not best:
            return None

        min_x, min_y, max_x, max_y = best["bbox"]
        sample_x = (min_x + max_x) / 2
        sample_y = min_y + (max_y - min_y) * 0.42
        focus = (
            sample_x * image.width / sample.width,
            sample_y * image.height / sample.height,
        )
        logger.debug(f"Detected face-like focus at {focus[0]:.1f},{focus[1]:.1f}")
        return focus

    def _skin_pixel_mask(self, image):
        rgb_bytes = image.tobytes()
        ycbcr_bytes = image.convert("YCbCr").tobytes()
        mask = []

        for index in range(0, len(rgb_bytes), 3):
            r, g, b = rgb_bytes[index], rgb_bytes[index + 1], rgb_bytes[index + 2]
            cb = ycbcr_bytes[index + 1]
            cr = ycbcr_bytes[index + 2]
            luma = (r * 299 + g * 587 + b * 114) / 1000
            max_rgb = max(r, g, b)
            min_rgb = min(r, g, b)
            chroma_range = max_rgb - min_rgb
            ycbcr_skin = 70 <= cb <= 145 and 122 <= cr <= 190 and luma > 32
            rgb_skin = (
                r > 45
                and g > 25
                and b > 15
                and chroma_range > 10
                and r >= b * 0.85
                and r >= g * 0.72
            )
            mask.append(ycbcr_skin and rgb_skin)

        return mask

    def _skin_components(self, mask, width, height):
        visited = bytearray(width * height)
        components = []

        for start, is_skin in enumerate(mask):
            if not is_skin or visited[start]:
                continue

            stack = [start]
            visited[start] = 1
            area = 0
            min_x = max_x = start % width
            min_y = max_y = start // width

            while stack:
                index = stack.pop()
                x = index % width
                y = index // width
                area += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)

                if x > 0:
                    self._push_skin_neighbor(mask, visited, stack, index - 1)
                if x < width - 1:
                    self._push_skin_neighbor(mask, visited, stack, index + 1)
                if y > 0:
                    self._push_skin_neighbor(mask, visited, stack, index - width)
                if y < height - 1:
                    self._push_skin_neighbor(mask, visited, stack, index + width)

            components.append({"area": area, "bbox": (min_x, min_y, max_x, max_y)})

        return components

    def _push_skin_neighbor(self, mask, visited, stack, index):
        if mask[index] and not visited[index]:
            visited[index] = 1
            stack.append(index)

    def _face_component_score(self, image, component):
        area = component["area"]
        image_area = image.width * image.height
        area_ratio = area / image_area
        min_x, min_y, max_x, max_y = component["bbox"]
        width = max_x - min_x + 1
        height = max_y - min_y + 1
        bbox_area = width * height

        if area < max(24, int(image_area * 0.0015)) or area_ratio > 0.34:
            return 0
        if width < 6 or height < 6:
            return 0

        aspect = width / height
        fill = area / bbox_area
        if aspect < 0.35 or aspect > 2.2 or fill < 0.16:
            return 0

        crop = image.crop((min_x, min_y, max_x + 1, max_y + 1)).convert("L")
        edges = crop.filter(ImageFilter.FIND_EDGES)
        edge_mean = ImageStat.Stat(edges).mean[0]
        dark_ratio = sum(crop.histogram()[:80]) / bbox_area

        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        center_bias = 1 - abs(center_x - image.width / 2) / (image.width / 2)
        upper_bias = 1 - min(1, center_y / image.height)
        aspect_score = 1 - min(1, abs(aspect - 0.82) / 1.4)
        fill_score = min(1.0, fill / 0.48)
        detail_score = min(1.0, edge_mean / 28) + min(0.45, dark_ratio * 2.2)

        return area * (
            0.7
            + aspect_score * 0.7
            + fill_score * 0.35
            + detail_score * 0.5
            + center_bias * 0.12
            + upper_bias * 0.25
        )

    def _focus_crop_offset(self, image, crop_size, horizontal=True):
        full_size = image.width if horizontal else image.height
        max_offset = full_size - crop_size
        if max_offset <= 0:
            return 0

        sample = image.convert("L")
        sample.thumbnail((180, 180), Image.BILINEAR)
        edges = sample.filter(ImageFilter.FIND_EDGES)

        sample_full = sample.width if horizontal else sample.height
        sample_crop = max(1, min(sample_full, int(round(crop_size * sample_full / full_size))))
        sample_max = sample_full - sample_crop
        if sample_max <= 0:
            return max_offset // 2

        steps = min(32, sample_max + 1)
        best_score = None
        best_sample_offset = sample_max // 2

        for index in range(steps):
            offset = round(index * sample_max / max(1, steps - 1))
            if horizontal:
                box = (offset, 0, offset + sample_crop, sample.height)
            else:
                box = (0, offset, sample.width, offset + sample_crop)

            edge_region = edges.crop(box)
            gray_region = sample.crop(box)
            edge_mean = ImageStat.Stat(edge_region).mean[0]
            luminance_std = ImageStat.Stat(gray_region).stddev[0]

            crop_center = offset + sample_crop / 2
            sample_center = sample_full / 2
            center_bias = 1 - abs(crop_center - sample_center) / sample_center
            score = edge_mean * 1.8 + luminance_std * 0.75 + center_bias * 10

            if best_score is None or score > best_score:
                best_score = score
                best_sample_offset = offset

        return int(round(best_sample_offset * max_offset / sample_max))
