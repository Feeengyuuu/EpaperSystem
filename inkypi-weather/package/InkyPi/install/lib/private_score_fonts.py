"""Validate and atomically install one owner-supplied, device-only font.

The installed updater invokes this fixed helper with the current release's
Python. Font bytes arrive on stdin, never in the release archive or a transport
file. There are no configurable destination paths or replacement operations.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

MAX_BYTES = 2 * 1024 * 1024
FONT_DIRECTORY = Path("/var/lib/inkypi/data/fonts")
LEAGUES = (
    "nba", "wnba", "mls", "epl", "laliga", "bundesliga", "seriea", "ligue1", "f1",
    "esports-cs2", "esports-cs2-regular", "esports-dota", "esports-dota-regular",
    "esports-riot-heading", "esports-riot", "esports-riot-regular",
    "esports-blast-heading", "esports-blast", "esports-esl-heading", "esports-iem-heading",
    "esports-pgl-event", "esports-tesla-event", "esports-roboto-event",
    "esports-industry-event", "esports-gear-event", "esports-cct-event",
    "esports-dust2-event", "esports-montserrat-event",
)
EXTENSIONS = ("ttf", "otf", "woff", "woff2")
PURPOSES = {
    "game_deals_price": {"basename": "game-deals-price.ttf", "family": "Banknote", "style": "Regular",
                         "glyphs": "0123456789$.,-"},
    # The LoL pro page's type in the game client's own Beaufort for LOL, one file per weight.
    "lol_pro_heavy": {"basename": "lol-beaufort-heavy.ttf", "family": "Beaufort for LOL", "style": "Heavy",
                      "glyphs": "0123456789/.%"},
    "lol_pro_bold": {"basename": "lol-beaufort-bold.ttf", "family": "Beaufort for LOL", "style": "Bold",
                     "glyphs": "0123456789/.%"},
    "lol_pro_medium": {"basename": "lol-beaufort-medium.ttf", "family": "Beaufort for LOL", "style": "Medium",
                       "glyphs": "0123456789/.%"},
}
FONT_NAMES = frozenset(
    f"sports-{league}-score.{extension}"
    for league in LEAGUES for extension in EXTENSIONS
) | frozenset(item["basename"] for item in PURPOSES.values())
MAGIC = {".ttf": b"\x00\x01\x00\x00", ".otf": b"OTTO", ".woff": b"wOFF", ".woff2": b"wOF2"}


class PrivateFontError(RuntimeError):
    """A private font could not be verified or safely installed."""


def require(condition, message):
    if not condition:
        raise PrivateFontError(message)


def validate_request(name, sha256, size):
    require(name in FONT_NAMES, "only fixed private font names are allowed")
    require(isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256), "invalid font SHA256")
    require(isinstance(size, int) and not isinstance(size, bool) and 0 < size <= MAX_BYTES,
            "font must contain 1 through 2097152 bytes")


def validate_font(payload: bytes, name: str, sha256: str, size: int) -> dict:
    """Inspect original font metadata and distinct visible digits with Pillow."""
    from PIL import ImageFont

    validate_request(name, sha256, size)
    require(len(payload) == size, "font byte count differs from the declared size")
    require(hashlib.sha256(payload).hexdigest() == sha256, "font SHA256 mismatch")
    require(payload[:4] == MAGIC[Path(name).suffix], "font format does not match its extension")
    try:
        font = ImageFont.truetype(io.BytesIO(payload), 32)
        family, style = font.getname()
        family = family.strip() if isinstance(family, str) else ""
        style = style.strip() if isinstance(style, str) else ""
        require(bool(family or style), "font metadata is missing")
        purpose = next((item for item in PURPOSES.values() if item["basename"] == name), None)
        if purpose:
            require((family, style) == (purpose["family"], purpose["style"]),
                    "font family and style do not match the registered purpose")
        required = purpose["glyphs"] if purpose else "0123456789-"
        for char in required:
            box = font.getbbox(char)
            require(0 < box[2] - box[0] <= 256 and 0 < box[3] - box[1] <= 256,
                    "score glyph has invalid or excessive bounds")
        missing = bytes(font.getmask(chr(0x10FFFF)))
        digits = [bytes(font.getmask(char)) for char in "0123456789"]
        require(all(mask and any(mask) and mask != missing for mask in digits),
                "font does not contain readable digits 0123456789")
        require(len(set(digits)) == 10, "font digit glyphs are not distinct")
        for char in required[10:]:
            mark = bytes(font.getmask(char))
            require(mark and any(mark) and mark != missing, "font has no readable required separator or currency glyph")
        bounds = font.getbbox("0123456789")
        require(bounds[2] > bounds[0] and bounds[3] > bounds[1], "font digits have no visible bounds")
    except (OSError, ValueError) as error:
        raise PrivateFontError("Pillow cannot read this raw font") from error
    result = {"family": family, "style": style, "digits": "0123456789", "digit_bbox": list(bounds)}
    if purpose:
        result["required_glyphs"] = required
    return result


def _open_directory(path):
    """Walk every component using O_NOFOLLOW, including the mutable data parent."""
    require(path.is_absolute(), "font directory must be absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(os.sep, flags)
    try:
        for component in path.parts[1:]:
            require(component not in {"", ".", ".."}, "unsafe directory component")
            following = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _directory_identity(descriptor):
    info = os.fstat(descriptor)
    return info.st_dev, info.st_ino


def _verify_directory(descriptor, gid):
    info = os.fstat(descriptor)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == gid
            and stat.S_IMODE(info.st_mode) == 0o750,
            "font directory must already be root:inkypi with mode 0750")
    reopened = _open_directory(FONT_DIRECTORY)
    try:
        require(_directory_identity(reopened) == _directory_identity(descriptor),
                "font directory changed during installation")
    finally:
        os.close(reopened)


def _existing_font(descriptor, name, sha256, gid):
    stem = name.rsplit(".", 1)[0]
    alternatives = {f"{stem}.{extension}" for extension in EXTENSIONS} - {name}
    for alternative in sorted(alternatives):
        if alternative.rsplit(".", 1)[0] != stem:
            continue
        try:
            os.stat(alternative, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise PrivateFontError("another format already exists for this font; owner review required")
    try:
        member = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    except FileNotFoundError:
        return False
    try:
        info = os.fstat(member)
        require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES,
                "existing target is not a regular bounded font")
        require(info.st_uid == 0 and info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o640,
                "existing font permissions differ; owner review required")
        with os.fdopen(os.dup(member), "rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        require(actual == sha256, "existing owner font differs; replacement is forbidden")
        return True
    finally:
        os.close(member)


def _publish(descriptor, name, payload, gid):
    temporary = ".private-score-font-" + secrets.token_hex(12)
    member = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=descriptor)
    try:
        with os.fdopen(member, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fchown(stream.fileno(), 0, gid)
            os.fchmod(stream.fileno(), 0o640)
            os.fsync(stream.fileno())
        _verify_directory(descriptor, gid)
        # link is an atomic no-replace publication on the same filesystem.
        # replace/rename could overwrite an owner file created concurrently.
        os.link(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor,
                follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=descriptor)
        os.fsync(descriptor)


def install_font(payload, name, sha256, size):
    """Called only under the updater's shared update lock; no service restart."""
    metadata = validate_font(payload, name, sha256, size)
    import grp

    require(os.geteuid() == 0, "private font installation requires the installed updater")
    gid = grp.getgrnam("inkypi").gr_gid
    descriptor = _open_directory(FONT_DIRECTORY)
    try:
        _verify_directory(descriptor, gid)
        existing = _existing_font(descriptor, name, sha256, gid)
        if not existing:
            _publish(descriptor, name, payload, gid)
        _verify_directory(descriptor, gid)
        return {"status": "unchanged" if existing else "installed", "destination": str(FONT_DIRECTORY / name),
                "sha256": sha256, "bytes": size, "uid": 0, "gid": gid, "mode": "0640", "font": metadata}
    finally:
        os.close(descriptor)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    require(len(arguments) == 3, "expected fixed font name, SHA256 and byte count")
    name, sha256, size = arguments
    size = int(size)
    validate_request(name, sha256, size)
    payload = sys.stdin.buffer.read(MAX_BYTES + 1)
    result = install_font(payload, name, sha256, size)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PrivateFontError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "refused", "reason": str(error)}), file=sys.stderr)
        raise SystemExit(2)
