"""Font DLC: the optional fonts the pages use when present (install/font_dlc.json).

    inkypi fonts list                      each DLC font, what it is for, its licence, whether installed
    sudo inkypi fonts install --open       every open-licensed font, from its pinned source
    sudo inkypi fonts install <key> ...    named open-licensed fonts
    sudo inkypi fonts add <key> <file>     a font you hold a licence for, from a local file
    sudo inkypi fonts remove <key>         take an installed DLC font out again

Fonts are checked and published by the same fixed helper the updater uses
(private_score_fonts): one fixed file name per key, at most 2 MiB, readable
distinct digits, root:inkypi 0640 in /var/lib/inkypi/data/fonts. Downloads are
https from the manifest's pinned URL only and must match its SHA256. Pages pick
an installed font up at their next refresh.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import urlparse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_score_fonts as store  # noqa: E402

MANIFEST = Path(__file__).resolve().parents[1] / "font_dlc.json"
UPDATE_LOCK = Path("/var/lib/inkypi/update/update.lock")
DOWNLOAD_HOSTS = frozenset({"cdn.jsdelivr.net"})
KINDS = {
    "open": "open licence, installs with --open",
    "commercial": "commercial, buy a licence, then add your file",
    "brand": "league/brand typeface, not offered for licence",
    "unverified": "rights holder not confirmed",
}
EXTENSION_BY_MAGIC = {magic: suffix for suffix, magic in store.MAGIC.items()}


class FontDlcError(RuntimeError):
    """A DLC font could not be fetched, checked or installed."""


def load_manifest(path=MANIFEST):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    fonts = {}
    for entry in data["fonts"]:
        key = entry["key"]
        if key in fonts or entry["kind"] not in KINDS or not file_names(key):
            raise FontDlcError(f"manifest entry is invalid: {key}")
        if entry["kind"] == "open":
            url = urlparse(entry["url"])
            if url.scheme != "https" or url.hostname not in DOWNLOAD_HOSTS:
                raise FontDlcError(f"open font must come from a pinned https source: {key}")
            if Path(url.path).suffix not in store.MAGIC:
                raise FontDlcError(f"open font has no known format: {key}")
        fonts[key] = entry
    return fonts


def file_names(key):
    """Every file name the pages read for ``key``: one per format, or the purpose's one name."""
    purpose = store.PURPOSES.get(key)
    if purpose:
        return (purpose["basename"],)
    if key not in store.LEAGUES:
        return ()
    return tuple(f"sports-{key}-score.{extension}" for extension in store.EXTENSIONS)


def installed(key, directory=None):
    """The installed file name for ``key``, None when absent, "?" when the folder is unreadable."""
    directory = Path(directory or store.FONT_DIRECTORY)
    try:
        present = set(os.listdir(directory))
    except FileNotFoundError:
        return None
    except PermissionError:
        return "?"
    return next((name for name in file_names(key) if name in present), None)


def download(entry, opener=urllib.request.urlopen):
    """The pinned bytes of an open font, refused unless their size and SHA256 match."""
    request = urllib.request.Request(entry["url"], headers={"User-Agent": "EpaperSystem font-dlc"})
    with opener(request, timeout=60) as response:
        payload = response.read(store.MAX_BYTES + 1)
    if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise FontDlcError(f"{entry['key']}: the download does not match its pinned SHA256")
    return payload


def target_name(key, payload):
    """The fixed file name ``payload`` installs under: its own format's extension."""
    suffix = EXTENSION_BY_MAGIC.get(payload[:4])
    if suffix is None:
        raise FontDlcError(f"{key}: not a TTF, OTF, WOFF or WOFF2 font")
    name = next((name for name in file_names(key) if name.endswith(suffix)), None)
    if name is None:
        raise FontDlcError(f"{key}: this font must be a {Path(file_names(key)[0]).suffix[1:].upper()} file")
    return name


def read_local(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= store.MAX_BYTES:
        raise FontDlcError(f"{path}: must be a regular font file of at most 2 MiB")
    return path.read_bytes()


def install_payload(key, payload, install=store.install_font):
    name = target_name(key, payload)
    present = installed(key)
    if present not in (None, "?", name):
        raise FontDlcError(f"{key}: {present} is already installed; remove it first")
    return install(payload, name, hashlib.sha256(payload).hexdigest(), len(payload))


def remove(key):
    """Unlink ``key``'s installed files from the font folder, opened without following links."""
    import grp

    gid = grp.getgrnam("inkypi").gr_gid
    descriptor = store._open_directory(store.FONT_DIRECTORY)
    removed = []
    try:
        store._verify_directory(descriptor, gid)
        for name in file_names(key):
            try:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise FontDlcError(f"{name} is not a regular file; review it by hand")
            os.unlink(name, dir_fd=descriptor)
            removed.append(name)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return removed


def update_lock():
    """The updater's own lock, so no font changes while a release is swapped."""
    from update_engine import UpdateLock

    return UpdateLock(UPDATE_LOCK)


def print_list(fonts, out=None):
    out = out or sys.stdout
    width = max(len(key) for key in fonts)
    for kind, label in KINDS.items():
        entries = [entry for entry in fonts.values() if entry["kind"] == kind]
        if not entries:
            continue
        print(f"\n{label}:", file=out)
        for entry in entries:
            state = installed(entry["key"])
            mark = "installed" if state not in (None, "?") else "?" if state == "?" else "-"
            print(f"  {entry['key']:<{width}}  {mark:<9}  {entry['font']} - {entry['use']}", file=out)
            print(f"  {'':<{width}}  {'':<9}  {entry['license']}" +
                  (f" - {entry['source']}" if entry.get("source") else ""), file=out)
    if any(installed(key) == "?" for key in fonts):
        print("\n? = run with sudo to see what is installed.", file=out)


def build_parser():
    parser = argparse.ArgumentParser(prog="inkypi fonts", description="Optional fonts (font DLC).")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="each DLC font, its licence and whether it is installed")
    install = commands.add_parser("install", help="download and install open-licensed fonts")
    install.add_argument("--open", action="store_true", help="every open-licensed font")
    install.add_argument("keys", nargs="*", help="open-licensed font keys")
    add = commands.add_parser("add", help="install a font you hold a licence for, from a local file")
    add.add_argument("key")
    add.add_argument("file", type=Path)
    drop = commands.add_parser("remove", help="remove an installed DLC font")
    drop.add_argument("key")
    return parser


def main(argv=None, *, opener=urllib.request.urlopen, install=store.install_font):
    arguments = build_parser().parse_args(argv)
    fonts = load_manifest()
    if arguments.command == "list":
        print_list(fonts)
        return 0
    if os.geteuid() != 0:
        raise FontDlcError("run this with sudo")
    if arguments.command == "install":
        keys = [key for key, entry in fonts.items() if entry["kind"] == "open"] if arguments.open else arguments.keys
        if not keys:
            raise FontDlcError("name open font keys, or pass --open for all of them")
        for key in keys:
            entry = fonts.get(key)
            if entry is None or entry["kind"] != "open":
                raise FontDlcError(f"{key}: not an open-licensed DLC font; use `add` with your own licensed file")
        failed = 0
        with update_lock():
            for key in keys:
                try:
                    result = install_payload(key, download(fonts[key], opener), install)
                    print(f"{key}: {result['status']} ({fonts[key]['font']})")
                except (FontDlcError, store.PrivateFontError, OSError) as error:
                    failed += 1
                    print(f"{key}: failed - {error}", file=sys.stderr)
        return 1 if failed else 0
    key = arguments.key
    if key not in fonts:
        raise FontDlcError(f"unknown font key: {key} (see `inkypi fonts list`)")
    with update_lock():
        if arguments.command == "add":
            result = install_payload(key, read_local(arguments.file), install)
            print(f"{key}: {result['status']} ({result['font']['family']} {result['font']['style']})".rstrip())
        else:
            removed = remove(key)
            print(f"{key}: removed {', '.join(removed)}" if removed else f"{key}: nothing installed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FontDlcError, store.PrivateFontError, OSError, ValueError) as error:
        print(f"inkypi fonts: {error}", file=sys.stderr)
        raise SystemExit(2)
