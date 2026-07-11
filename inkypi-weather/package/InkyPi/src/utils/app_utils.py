import logging
import os
import socket
import subprocess
from dataclasses import dataclass, field
from uuid import uuid4

from pathlib import Path
from flask import current_app, has_app_context
from PIL import Image, ImageDraw, ImageFont, ImageOps

from security.request_limits import (
    UploadPolicy,
    UploadTooLarge,
    UploadTotalTooLarge,
    copy_limited_upload,
    is_empty_upload_placeholder,
)

logger = logging.getLogger(__name__)

DEFAULT_FONT_FAMILY = "Microsoft YaHei"

YAHEI_REGULAR_FILES = ("msyh.ttf", "msyh.ttc")
YAHEI_BOLD_FILES = ("msyhbd.ttf", "msyhbd.ttc")
BASE_FALLBACK_FILES = ("NotoSansSC-VF.ttf", "LXGWWenKai-Regular.ttf")
BASE_FALLBACK_VARIABLE_WEIGHTS = {
    "notosanssc-vf.ttf": {False: 400, True: 700},
}

FONT_FAMILIES = {
    "Microsoft YaHei": [{
        "font-weight": "normal",
        "file": "msyh.ttf"
    },{
        "font-weight": "bold",
        "file": "msyhbd.ttf"
    }],
    "\u5fae\u8f6f\u96c5\u9ed1": [{
        "font-weight": "normal",
        "file": "msyh.ttf"
    },{
        "font-weight": "bold",
        "file": "msyhbd.ttf"
    }],
    "Dogica": [{
        "font-weight": "normal",
        "file": "dogicapixel.ttf"
    },{
        "font-weight": "bold",
        "file": "dogicapixelbold.ttf"
    }],
    "Jost": [{
        "font-weight": "normal",
        "file": "Jost.ttf"
    },{
        "font-weight": "bold",
        "file": "Jost-SemiBold.ttf"
    }],
    "LXGW WenKai": [{
        "font-weight": "normal",
        "file": "LXGWWenKai-Regular.ttf"
    }],
    "方正新楷近似": [{
        "font-weight": "normal",
        "file": os.path.join("plugins", "chinese_literature_clock", "fonts", "FandolKai-Regular.otf")
    }],
    "FandolKai": [{
        "font-weight": "normal",
        "file": os.path.join("plugins", "chinese_literature_clock", "fonts", "FandolKai-Regular.otf")
    }],
    "康熙字典体": [{
        "font-weight": "normal",
        "file": os.path.join("plugins", "chinese_literature_clock", "fonts", "I.Ming-8.10.ttf")
    }],
    "I.Ming": [{
        "font-weight": "normal",
        "file": os.path.join("plugins", "chinese_literature_clock", "fonts", "I.Ming-8.10.ttf")
    }],
    "Napoli": [{
        "font-weight": "normal",
        "file": "Napoli.ttf"
    }],
    "DS-Digital": [{
        "font-weight": "normal",
        "file": os.path.join("DS-DIGI", "DS-DIGI.TTF")
    }]
}

FONTS = {
    "microsoft-yahei": "msyh.ttf",
    "microsoft-yahei-bold": "msyhbd.ttf",
    "yahei": "msyh.ttf",
    "yahei-bold": "msyhbd.ttf",
    "ds-gigi": "DS-DIGI.TTF",
    "napoli": "Napoli.ttf",
    "jost": "Jost.ttf",
    "jost-semibold": "Jost-SemiBold.ttf",
    "lxgw-wenkai": "LXGWWenKai-Regular.ttf",
    "fangzheng-xinkai-near": os.path.join("plugins", "chinese_literature_clock", "fonts", "FandolKai-Regular.otf"),
    "fandol-kai": os.path.join("plugins", "chinese_literature_clock", "fonts", "FandolKai-Regular.otf"),
    "kangxi": os.path.join("plugins", "chinese_literature_clock", "fonts", "I.Ming-8.10.ttf"),
    "iming": os.path.join("plugins", "chinese_literature_clock", "fonts", "I.Ming-8.10.ttf")
}

def resolve_path(file_path):
    src_dir = os.getenv("SRC_DIR")
    if src_dir is None:
        # Default to the src directory
        src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    src_path = Path(src_dir)
    return str(src_path / file_path)

def resolve_font_path(file_path):
    if os.path.isabs(file_path):
        return file_path
    if file_path.startswith(f"plugins{os.sep}") or file_path.startswith("plugins/"):
        return resolve_path(file_path)
    return resolve_path(os.path.join("static", "fonts", file_path))

def base_ui_font_candidates(bold: bool = False) -> tuple[str, ...]:
    names = YAHEI_BOLD_FILES if bold else YAHEI_REGULAR_FILES
    data_dir = os.getenv("INKYPI_DATA_DIR")
    candidates = []
    if data_dir:
        candidates.extend(str(Path(data_dir) / "fonts" / name) for name in names)
    candidates.extend(resolve_path(os.path.join("static", "fonts", name)) for name in names)
    candidates.extend(
        resolve_path(os.path.join("static", "fonts", name))
        for name in BASE_FALLBACK_FILES
    )
    return tuple(dict.fromkeys(candidates))


def _apply_base_ui_variable_weight(font, candidate, bold):
    weights = BASE_FALLBACK_VARIABLE_WEIGHTS.get(Path(candidate).name.casefold())
    if weights is None:
        return font
    try:
        axes = font.get_variation_axes()
    except (AttributeError, OSError):
        return font

    values = []
    changed = False
    for axis in axes:
        if not isinstance(axis, dict):
            return font
        name = axis.get("name", axis.get(b"name", b""))
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="ignore")
        minimum = axis.get("minimum", axis.get(b"minimum"))
        maximum = axis.get("maximum", axis.get(b"maximum"))
        default = axis.get("default", axis.get(b"default", minimum))
        value = default
        if "weight" in str(name).casefold() or "wght" in str(name).casefold():
            target = weights[bool(bold)]
            value = max(minimum, min(maximum, target))
            changed = True
        values.append(value)

    if changed:
        try:
            font.set_variation_by_axes(values)
        except (AttributeError, OSError, TypeError, ValueError):
            return font
    return font


def get_base_ui_font(
    font_size: int, bold: bool = False
) -> ImageFont.FreeTypeFont:
    for candidate in base_ui_font_candidates(bold=bold):
        if not Path(candidate).is_file():
            continue
        try:
            font = ImageFont.truetype(candidate, int(font_size))
        except OSError:
            continue
        if Path(font.path).resolve() == Path(candidate).resolve():
            return _apply_base_ui_variable_weight(font, candidate, bold)
    return ImageFont.load_default()

def resolve_base_ui_font_path(bold: bool = False) -> str:
    for candidate in base_ui_font_candidates(bold=bold):
        if not Path(candidate).is_file():
            continue
        try:
            font = ImageFont.truetype(candidate, 10)
        except OSError:
            continue
        if Path(font.path).resolve() == Path(candidate).resolve():
            return candidate
    raise OSError("No loadable base UI font is available")

def font_file_uri(path: str) -> str:
    return Path(path).resolve().as_uri()

def get_ip_address(default="Unknown"):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        logger.warning("Could not determine a routable local IP address")
        return default

def get_wifi_name():
    try:
        output = subprocess.check_output(['iwgetid', '-r']).decode('utf-8').strip()
        return output
    except subprocess.CalledProcessError:
        return None

def is_connected():
    """Check if the Raspberry Pi has an internet connection."""
    try:
        # Try to connect to Google's public DNS server
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except OSError:
        return False

def get_font(font_name, font_size=50, font_weight="normal"):
    if font_name in FONT_FAMILIES:
        font_variants = FONT_FAMILIES[font_name]

        font_entry = next((entry for entry in font_variants if entry["font-weight"] == font_weight), None)
        if font_entry is None:
            font_entry = font_variants[0]  # Default to first available variant

        if font_entry:
            font_file = font_entry["file"]
            if Path(font_file).name.casefold() in {
                name.casefold() for name in YAHEI_REGULAR_FILES + YAHEI_BOLD_FILES
            }:
                return get_base_ui_font(
                    font_size,
                    bold=Path(font_file).name.casefold()
                    in {name.casefold() for name in YAHEI_BOLD_FILES},
                )
            font_path = resolve_font_path(font_file)
            return ImageFont.truetype(font_path, font_size)
        else:
            logger.warning(f"Requested font weight not found: font_name={font_name}, font_weight={font_weight}")
    else:
        logger.warning(f"Requested font not found: font_name={font_name}")

    return None

def get_fonts():
    fonts_list = []
    for font_family, variants in FONT_FAMILIES.items():
        for variant in variants:
            font_file = variant["file"]
            if Path(font_file).name.casefold() in {
                name.casefold() for name in YAHEI_REGULAR_FILES + YAHEI_BOLD_FILES
            }:
                font_path = resolve_base_ui_font_path(
                    bold=Path(font_file).name.casefold()
                    in {name.casefold() for name in YAHEI_BOLD_FILES}
                )
            else:
                font_path = resolve_font_path(font_file)
            fonts_list.append({
                "font_family": font_family,
                "url": font_file_uri(font_path),
                "font_weight": variant.get("font-weight", "normal"),
                "font_style": variant.get("font-style", "normal"),
            })
    return fonts_list

def get_font_path(font_name):
    font_file = FONTS[font_name]
    if Path(font_file).name.casefold() in {
        name.casefold() for name in YAHEI_REGULAR_FILES + YAHEI_BOLD_FILES
    }:
        return resolve_base_ui_font_path(
            bold=Path(font_file).name.casefold()
            in {name.casefold() for name in YAHEI_BOLD_FILES}
        )
    return resolve_font_path(font_file)

def resolve_dimensions(device_config):
    """Return the device resolution as (width, height), reversed for vertical orientation."""
    dimensions = device_config.get_resolution()
    if device_config.get_config("orientation") == "vertical":
        dimensions = dimensions[::-1]
    return dimensions

def coerce_bool(value, default=False, truthy=("1", "true", "yes", "on")):
    """Coerce a setting value to bool: None -> default, bool passthrough, else membership in truthy."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in truthy

def bounded_int(value, default, minimum, maximum):
    """Parse value as int (falling back to default), then clamp to [minimum, maximum]."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))

def get_available_font_names(default=None):
    """Sorted unique font display names from get_fonts(), with an optional default appended if missing."""
    names = sorted({
        f.get("name") or f.get("font_family")
        for f in get_fonts()
        if f.get("name") or f.get("font_family")
    })
    if default and default not in names:
        names.append(default)
    return names

def generate_startup_image(dimensions=(800,480)):
    bg_color = (255,255,255)
    text_color = (0,0,0)
    width, height = dimensions

    hostname = socket.gethostname()
    ip = get_ip_address()

    image = Image.new("RGBA", dimensions, bg_color)
    image_draw = ImageDraw.Draw(image)

    title_font_size = width * 0.145
    image_draw.text((width/2, height/2), "inkypi", anchor="mm", fill=text_color, font=get_font("Jost", title_font_size))

    text = f"To get started, visit http://{hostname}.local"
    text_font_size = width * 0.032

    # Draw the instructions
    y_text = height * 3 / 4
    image_draw.text((width/2, y_text), text, anchor="mm", fill=text_color, font=get_base_ui_font(text_font_size))

    # Draw the IP on a line below
    ip_text = f"or http://{ip}"
    ip_text_font_size = width * 0.032
    bbox = image_draw.textbbox((0, 0), text, font=get_base_ui_font(text_font_size))
    text_height = bbox[3] - bbox[1]
    ip_y = y_text + text_height * 1.35
    image_draw.text((width/2, ip_y), ip_text, anchor="mm", fill=text_color, font=get_base_ui_font(ip_text_font_size))

    return image

def parse_form(request_form):
    request_dict = request_form.to_dict()
    for key in request_form.keys():
        if key.endswith('[]'):
            request_dict[key] = request_form.getlist(key)
    return request_dict


class RequestFileReferenceError(ValueError):
    """A submitted local upload reference disappeared before admission."""


def validate_request_file_references(settings):
    """Reject missing absolute paths carried by file-valued form settings.

    Existing upload paths can wait behind instance cleanup. Validation must run
    inside the instance lifecycle guard so cleanup cannot remove a path between
    this check and the model mutation that takes ownership of it.
    """
    for key, raw_value in (settings or {}).items():
        if "file" not in str(key).casefold():
            continue
        values = raw_value if isinstance(raw_value, (list, tuple)) else (raw_value,)
        for value in values:
            if not isinstance(value, (str, os.PathLike)):
                continue
            path = os.fspath(value)
            if path and os.path.isabs(path) and not os.path.isfile(path):
                raise RequestFileReferenceError(
                    "Referenced upload no longer exists; reload and upload it again"
                )

@dataclass
class PreparedRequestFiles:
    """Unique request uploads that can be rolled back before admission commits."""

    locations: dict
    pending: list[tuple[str, str]] = field(default_factory=list, repr=False)
    promoted: list[str] = field(default_factory=list, repr=False)
    _accepted: bool = field(default=False, init=False, repr=False)

    def promote(self):
        """Publish unique final files without overwriting an existing resource."""
        if self._accepted:
            return dict(self.locations)
        try:
            while self.pending:
                temporary, final = self.pending[0]
                if os.path.exists(final):
                    raise FileExistsError(f"Prepared upload target already exists: {final}")
                os.replace(temporary, final)
                self.pending.pop(0)
                self.promoted.append(final)
                _fsync_upload_directory(Path(final).parent)
        except BaseException:
            self.rollback()
            raise
        return dict(self.locations)

    def accept(self):
        """Transfer ownership of promoted files to the committed configuration."""
        self.pending.clear()
        self.promoted.clear()
        self._accepted = True

    def rollback(self):
        """Remove all temporary and uniquely promoted files for a failed request."""
        if self._accepted:
            return
        for path in [temporary for temporary, _final in self.pending] + list(self.promoted):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as error:
                logger.warning("Could not roll back prepared upload %s: %s", path, error)
        self.pending.clear()
        self.promoted.clear()


def _fsync_upload_directory(directory):
    if os.name == "nt":
        return
    descriptor = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _request_upload_directory():
    if has_app_context():
        runtime_paths = current_app.config.get("RUNTIME_PATHS")
        if runtime_paths is not None:
            return Path(runtime_paths.data_dir) / "uploads"
    return Path(resolve_path(os.path.join("static", "images", "saved")))


def _request_upload_policy(policy):
    if policy is not None:
        if not isinstance(policy, UploadPolicy):
            raise TypeError("policy must be an UploadPolicy")
        return policy
    if has_app_context():
        configured = current_app.config.get("UPLOAD_POLICY")
        if configured is not None:
            if not isinstance(configured, UploadPolicy):
                raise TypeError("UPLOAD_POLICY must be an UploadPolicy")
            return configured
    return UploadPolicy()


def _normalize_jpeg_upload(path):
    """Preserve the established EXIF-orientation normalization atomically."""
    normalized_path = path.with_name(f".{uuid4().hex}.normalized-{path.name}")
    try:
        with Image.open(path) as image:
            ImageOps.exif_transpose(image, in_place=True)
            image.save(normalized_path, format="JPEG")
        with normalized_path.open("r+b") as normalized:
            os.fsync(normalized.fileno())
        os.replace(normalized_path, path)
        _fsync_upload_directory(path.parent)
    except Exception as error:
        logger.warning("EXIF processing error for %s: %s", path.name, error)
    finally:
        try:
            normalized_path.unlink()
        except FileNotFoundError:
            pass


def prepare_request_files(request_files, form_data=None, policy=None):
    """Stage uploads under unique names without touching existing user files."""
    form_data = form_data or {}
    policy = _request_upload_policy(policy)
    file_location_map = {}
    prepared = PreparedRequestFiles(file_location_map)
    total_written = 0

    for key in set(request_files.keys()):
        is_list = key.endswith('[]')
        if key in form_data:
            file_location_map[key] = form_data.getlist(key) if is_list else form_data.get(key)

    try:
        for key, file in request_files.items(multi=True):
            is_list = key.endswith('[]')
            if is_empty_upload_placeholder(file):
                continue
            original_name = os.path.basename(file.filename or "")

            file_save_dir = _request_upload_directory()
            file_save_dir.mkdir(parents=True, exist_ok=True)
            token = uuid4().hex
            extension = Path(original_name).suffix.lower()
            final_name = f"{token}{extension}"
            temporary_name = f".{token}.pending{extension}"
            final_path = file_save_dir / final_name
            temporary_path = file_save_dir / temporary_name

            # Register ownership before writing so even a partial save is
            # discoverable and removable by the outer rollback path.
            prepared.pending.append((str(temporary_path), str(final_path)))
            written = copy_limited_upload(
                file,
                temporary_path,
                policy,
                bytes_already_written=total_written,
            )
            if Path(original_name).suffix.lower() in {".jpg", ".jpeg"}:
                _normalize_jpeg_upload(temporary_path)
                written = max(written, temporary_path.stat().st_size)
                if written > policy.max_file_bytes:
                    raise UploadTooLarge(
                        "The normalized image exceeds the per-file limit"
                    )
                if total_written + written > policy.max_total_bytes:
                    raise UploadTotalTooLarge(
                        "The normalized images exceed the total upload limit"
                    )
            total_written += written

            if is_list:
                file_location_map.setdefault(key, [])
                file_location_map[key].append(str(final_path))
            else:
                file_location_map[key] = str(final_path)
    except BaseException:
        prepared.rollback()
        raise

    return prepared


def handle_request_files(request_files, form_data=None, policy=None):
    """Compatibility wrapper that publishes uploads under unique names."""
    prepared = prepare_request_files(request_files, form_data, policy)
    try:
        locations = prepared.promote()
        prepared.accept()
        return locations
    except BaseException:
        prepared.rollback()
        raise
