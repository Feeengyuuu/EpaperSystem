import inspect
import importlib
import logging
import sys
import time
from contextlib import contextmanager

from display.abstract_display import AbstractDisplay
from PIL import Image
from pathlib import Path
from plugins.plugin_registry import get_plugin_instance
from runtime.refresh_contracts import TaskContext

logger = logging.getLogger(__name__)


def split_image_for_bi_color_epd(image):
    """
    Convert image into two 1-bit layers for bi-color (black and red) e-paper displays.
    """
    black = (0, 0, 0)
    white = (255, 255, 255)
    red = (255, 0, 0)

    palette_data = [*black, *white, *red]
    palette_img = Image.new('P', (1, 1))
    palette_img.putpalette(palette_data)

    indexed_img = image.quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)
    black_layer = indexed_img.point(lambda p: 0 if p == 0 else 1, mode='1')
    red_layer = indexed_img.point(lambda p: 0 if p == 2 else 1, mode='1')
    return black_layer, red_layer


class WaveshareDisplay(AbstractDisplay):
    """
    Handles Waveshare e-paper display dynamically based on device type.

    This class loads the appropriate display driver dynamically based on the 
    `display_type` specified in the device configuration, allowing support for 
    multiple Waveshare EPD models.  

    The module drivers are in display.waveshare_epd.
    """

    def _hardware_context(self, task_context=None):
        if task_context is not None:
            return task_context
        try:
            timeout = float(
                self.device_config.get_config(
                    "display_busy_timeout_seconds",
                    default=90,
                )
            )
        except (TypeError, ValueError, OverflowError):
            timeout = 90.0
        timeout = max(1.0, min(600.0, timeout))
        return TaskContext.never_cancelled(
            deadline_monotonic=time.monotonic() + timeout,
        )

    @contextmanager
    def _driver_context(self, task_context, stage):
        missing = object()
        previous_context = getattr(
            self.epd_display,
            "_inkypi_task_context",
            missing,
        )
        previous_stage = getattr(
            self.epd_display,
            "_inkypi_busy_stage",
            missing,
        )
        self.epd_display._inkypi_task_context = task_context
        self.epd_display._inkypi_busy_stage = stage
        try:
            yield
        finally:
            if previous_context is missing:
                delattr(self.epd_display, "_inkypi_task_context")
            else:
                self.epd_display._inkypi_task_context = previous_context
            if previous_stage is missing:
                delattr(self.epd_display, "_inkypi_busy_stage")
            else:
                self.epd_display._inkypi_busy_stage = previous_stage

    def initialize_display(self):
        
        """
        Initializes the Waveshare display device.

        Retrieves the display type from the device configuration and dynamically 
        loads the corresponding Waveshare EPD driver from display.waveshare_epd.

        Raises:
            ValueError: If `display_type` is missing or the specified module is 
                        not found.
        """
        
        logger.info("Initializing Waveshare display")

        # get the device type which should be the model number of the device.
        display_type = self.device_config.get_config("display_type")  
        logger.info(f"Loading EPD display for {display_type} display")

        if not display_type:
            raise ValueError("Waveshare driver but 'display_type' not specified in configuration.")

        # Construct module path dynamically - e.g. "display.waveshare_epd.epd7in3e"
        module_name = f"display.waveshare_epd.{display_type}" 

        # Workaround for some Waveshare drivers using 'import epdconfig' causing import errors
        epd_dir = Path(__file__).parent / "waveshare_epd"
        if str(epd_dir) not in sys.path:
            sys.path.insert(0, str(epd_dir))

        try:
            # Dynamically load module
            epd_module = importlib.import_module(module_name)  
            self.epd_display = epd_module.EPD()
            # Workaround for init functions with inconsistent casing
            self.epd_display_init = getattr(self.epd_display, "Init", getattr(self.epd_display, "init", None))

            if not callable(self.epd_display_init):
                raise AttributeError("No Init/init method found")

            task_context = self._hardware_context()
            with self._driver_context(
                task_context,
                f"{display_type}.init",
            ):
                task_context.raise_if_cancelled()
                init_result = self.epd_display_init()
            if init_result not in {None, 0}:
                raise OSError(
                    f"Waveshare display initialization failed: {display_type}"
                )

            display_args_spec = inspect.getfullargspec(self.epd_display.display)
        except ModuleNotFoundError:
            raise ValueError(f"Unsupported Waveshare display type: {display_type}")
        except AttributeError:
            raise ValueError(f"Display does not support required methods: {display_type}")

        self.bi_color_display = len(display_args_spec.args) > 2

        # update the resolution directly from the loaded device context
        if not self.device_config.get_config("resolution"):
            w, h = int(self.epd_display.width), int(self.epd_display.height)
            resolution = [w, h] if w >= h else [h, w]
            self.device_config.update_value(
                "resolution",
                resolution,
                write=True)


    def display_image(self, image, image_settings=()):
        return self.display_image_with_context(image, image_settings)

    def display_image_with_context(
        self,
        image,
        image_settings=(),
        *,
        task_context=None,
    ):
        
        """
        Displays an image on the Waveshare display.

        The image has been processed by adjusting orientation, resizing, and converting it
        into the buffer format required for e-paper rendering.

        Args:
            image (PIL.Image): The image to be displayed.
            image_settings (list, optional): Additional settings to modify image rendering.

        Raises:
            ValueError: If no image is provided.
        """

        logger.info("Displaying image to Waveshare display.")
        if not image:
            raise ValueError(f"No image provided.")

        task_context = self._hardware_context(task_context)
        display_type = getattr(self, "device_config", None)
        display_type = (
            display_type.get_config("display_type")
            if display_type is not None
            else "waveshare"
        )
        with self._driver_context(
            task_context,
            f"{display_type}.display",
        ):
            task_context.raise_if_cancelled()

            # Assume device was in sleep mode.
            self.epd_display_init()
            task_context.raise_if_cancelled()

            # Clear residual pixels before updating the image.
            self.epd_display.Clear()
            task_context.raise_if_cancelled()

            # Display the image on the WS display.
            if not self.bi_color_display:
                self.epd_display.display(self.epd_display.getbuffer(image))
            else:
                black_layer, red_layer = split_image_for_bi_color_epd(image)

                self.epd_display.display(
                    self.epd_display.getbuffer(black_layer),
                    self.epd_display.getbuffer(red_layer),
                )

            # Put device into low power mode (EPD displays maintain image when powered off)
            logger.info("Putting Waveshare display into sleep mode for power saving.")
            self.epd_display.sleep()
