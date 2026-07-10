import logging
import os
import socket
import subprocess
from dataclasses import dataclass, field
from uuid import uuid4

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)

DEFAULT_FONT_FAMILY = "Microsoft YaHei"

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

def get_ip_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
    return ip_address

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
            font_path = resolve_font_path(font_entry["file"])
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
            fonts_list.append({
                "font_family": font_family,
                "url": resolve_font_path(variant["file"]),
                "font_weight": variant.get("font-weight", "normal"),
                "font_style": variant.get("font-style", "normal"),
            })
    return fonts_list

def get_font_path(font_name):
    return resolve_font_path(FONTS[font_name])

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
    image_draw.text((width/2, y_text), text, anchor="mm", fill=text_color, font=get_font("Jost", text_font_size))

    # Draw the IP on a line below
    ip_text = f"or http://{ip}"
    ip_text_font_size = width * 0.032
    bbox = image_draw.textbbox((0, 0), text, font=get_font("Jost", text_font_size))
    text_height = bbox[3] - bbox[1]
    ip_y = y_text + text_height * 1.35
    image_draw.text((width/2, ip_y), ip_text, anchor="mm", fill=text_color, font=get_font("Jost", ip_text_font_size))

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


def prepare_request_files(request_files, form_data=None):
    """Stage uploads under unique names without touching existing user files."""
    form_data = form_data or {}
    allowed_file_extensions = {
        'pdf', 'png', 'avif', 'jpg', 'jpeg', 'gif', 'webp', 'heif', 'heic', 'csv'
    }
    file_location_map = {}
    prepared = PreparedRequestFiles(file_location_map)

    for key in set(request_files.keys()):
        is_list = key.endswith('[]')
        if key in form_data:
            file_location_map[key] = form_data.getlist(key) if is_list else form_data.get(key)

    try:
        for key, file in request_files.items(multi=True):
            is_list = key.endswith('[]')
            original_name = os.path.basename(file.filename or "")
            if not original_name:
                continue
            extension = os.path.splitext(original_name)[1].lstrip('.').lower()
            if extension not in allowed_file_extensions:
                continue

            file_save_dir = resolve_path(os.path.join("static", "images", "saved"))
            os.makedirs(file_save_dir, exist_ok=True)
            token = uuid4().hex
            final_name = f"{token}-{original_name}"
            temporary_name = f".{token}.pending-{original_name}"
            final_path = os.path.join(file_save_dir, final_name)
            temporary_path = os.path.join(file_save_dir, temporary_name)

            # Register ownership before writing so even a partial save is
            # discoverable and removable by the outer rollback path.
            prepared.pending.append((temporary_path, final_path))

            if extension in {'jpg', 'jpeg'}:
                try:
                    with Image.open(file) as image:
                        ImageOps.exif_transpose(image).save(temporary_path)
                except Exception as error:
                    logger.warning("EXIF processing error for %s: %s", original_name, error)
                    try:
                        file.stream.seek(0)
                    except (AttributeError, OSError):
                        pass
                    file.save(temporary_path)
            else:
                file.save(temporary_path)

            if is_list:
                file_location_map.setdefault(key, [])
                file_location_map[key].append(final_path)
            else:
                file_location_map[key] = final_path
    except BaseException:
        prepared.rollback()
        raise

    return prepared


def handle_request_files(request_files, form_data={}):
    allowed_file_extensions = {'pdf', 'png', 'avif', 'jpg', 'jpeg', 'gif', 'webp', 'heif', 'heic', 'csv'}
    file_location_map = {}
    # handle existing file locations being provided as part of the form data
    for key in set(request_files.keys()):
        is_list = key.endswith('[]')
        if key in form_data:
            file_location_map[key] = form_data.getlist(key) if is_list else form_data.get(key)
    # add new files in the request
    for key, file in request_files.items(multi=True):
        is_list = key.endswith('[]')
        file_name = file.filename
        if not file_name:
            continue

        extension = os.path.splitext(file_name)[1].replace('.', '')
        if not extension or extension.lower() not in allowed_file_extensions:
            continue

        file_name = os.path.basename(file_name)

        file_save_dir = resolve_path(os.path.join("static", "images", "saved"))
        file_path = os.path.join(file_save_dir, file_name)

        # Open the image and apply EXIF transformation before saving
        if extension in {'jpg', 'jpeg'}:
            try:
                with Image.open(file) as img:
                    img = ImageOps.exif_transpose(img)
                    img.save(file_path)
            except Exception as e:
                logger.warning(f"EXIF processing error for {file_name}: {e}")
                file.save(file_path)
        else:
            # Directly save non-JPEG files
            file.save(file_path)

        if is_list:
            file_location_map.setdefault(key, [])
            file_location_map[key].append(file_path)
        else:
            file_location_map[key] = file_path
    return file_location_map
