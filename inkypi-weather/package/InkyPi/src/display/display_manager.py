import fnmatch
import hashlib
import json
import logging
from pathlib import Path
import time

from utils.image_utils import resize_image, change_orientation, apply_image_enhancement
from utils.safe_image import safe_open_image
from display.mock_display import MockDisplay
from display.display_transaction import DisplayTransaction
from runtime.refresh_contracts import TaskContext
from runtime.runtime_state import RuntimeStateStore

logger = logging.getLogger(__name__)

# Try to import hardware displays, but don't fail if they're not available
try:
    from display.inky_display import InkyDisplay
except ImportError:
    logger.info("Inky display not available, hardware support disabled")

try:
    from display.waveshare_display import WaveshareDisplay
except ImportError:
    logger.info("Waveshare display not available, hardware support disabled")

class DisplayManager:

    """Manages the display and rendering of images."""

    def __init__(self, device_config, runtime_state_store=None):

        """
        Initializes the display manager and selects the correct display type 
        based on the configuration.

        Args:
            device_config (object): Configuration object containing display settings.

        Raises:
            ValueError: If an unsupported display type is specified.
        """
        
        self.device_config = device_config
        self.runtime_state_store = None
        self.transaction = None
     
        display_type = device_config.get_config("display_type", default="inky")

        if display_type == "mock":
            self.display = MockDisplay(device_config)
        elif display_type == "inky":
            self.display = InkyDisplay(device_config)
        elif fnmatch.fnmatch(display_type, "epd*in*"):  
            # derived from waveshare epd - we assume here that will be consistent
            # otherwise we will have to enshring the manufacturer in the 
            # display_type and then have a display_model parameter.  Will leave
            # that for future use if the need arises.
            #
            # see https://github.com/waveshareteam/e-Paper
            self.display = WaveshareDisplay(device_config)
        else:
            raise ValueError(f"Unsupported display type: {display_type}")

        if runtime_state_store is not None:
            self.bind_runtime_state(runtime_state_store)

    def bind_runtime_state(self, runtime_state_store):
        """Bind the one shared RuntimeStateStore and create the transaction layer."""

        if runtime_state_store is None:
            raise TypeError("runtime_state_store is required")
        if (
            getattr(self, "runtime_state_store", None) is runtime_state_store
            and getattr(self, "transaction", None) is not None
        ):
            return self.transaction
        self.runtime_state_store = runtime_state_store
        display_dir = Path(
            getattr(
                self.device_config,
                "display_dir",
                Path(self.device_config.current_image_file).parent,
            )
        )
        self.transaction = DisplayTransaction(
            self,
            display_dir=display_dir,
            compatibility_image_path=self.device_config.current_image_file,
            runtime_state_store=runtime_state_store,
        )
        return self.transaction

    def _ensure_transaction(self):
        if self.transaction is not None:
            return self.transaction
        data_dir = Path(
            getattr(
                self.device_config,
                "data_dir",
                Path(self.device_config.current_image_file).parent,
            )
        )
        data_dir.mkdir(parents=True, exist_ok=True)
        return self.bind_runtime_state(RuntimeStateStore(data_dir / "runtime_state.json"))

    def prepare_image(self, image, *, image_settings=()):
        """Apply the complete pixel pipeline without touching hardware or manifests."""

        if image is None:
            raise ValueError("No image provided.")
        prepared = image.copy()
        prepared = change_orientation(
            prepared,
            self.device_config.get_config("orientation"),
        )
        prepared = resize_image(
            prepared,
            self.device_config.get_resolution(),
            image_settings,
        )
        if self.device_config.get_config("inverted_image"):
            prepared = prepared.rotate(180)
        return apply_image_enhancement(
            prepared,
            self.device_config.get_config("image_settings"),
        )

    def hardware_fingerprint(self, image_settings=()):
        """Hash driver-affecting settings so metadata-only commits stay safe."""

        payload = {
            "driver": type(self.display).__name__,
            "display_type": self.device_config.get_config("display_type"),
            "resolution": list(self.device_config.get_resolution()),
            "image_settings": list(image_settings or ()),
            "device_image_settings": self.device_config.get_config(
                "image_settings",
                default={},
            ),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def write_hardware(self, image, *, image_settings=(), task_context):
        """Perform the only driver call in the display pipeline."""

        task_context.raise_if_cancelled()
        if not hasattr(self, "display"):
            raise ValueError("No valid display instance initialized.")
        self.display.display_image(image, image_settings)

    def write_hardware_path(self, image_path, *, image_settings=(), task_context):
        image = safe_open_image(image_path)
        self.write_hardware(
            image,
            image_settings=image_settings,
            task_context=task_context,
        )

    def display_image(
        self,
        image,
        image_settings=(),
        *,
        task_context=None,
        logical_target=None,
        instance_revision=None,
    ):
        
        """
        Delegates image rendering to the appropriate display instance.

        Args:
            image (PIL.Image): The image to be displayed.
            image_settings (list, optional): List of settings to modify image rendering.

        Raises:
            ValueError: If no valid display instance is found.
        """

        transaction = self._ensure_transaction()
        if task_context is None:
            try:
                timeout = float(
                    self.device_config.get_config(
                        "display_timeout_seconds",
                        default=120,
                    )
                )
            except (TypeError, ValueError):
                timeout = 120.0
            timeout = max(1.0, min(900.0, timeout))
            task_context = TaskContext.never_cancelled(
                deadline_monotonic=time.monotonic() + timeout,
            )
        prepared = transaction.prepare(
            image,
            image_settings=image_settings,
            logical_target=logical_target,
            instance_revision=instance_revision,
        )
        return transaction.commit(prepared, task_context=task_context)

    def recover_display(self, *, task_context=None):
        transaction = self._ensure_transaction()
        if task_context is None:
            task_context = TaskContext.never_cancelled(
                deadline_monotonic=time.monotonic() + 120,
            )
        return transaction.recover(task_context=task_context)
