from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "inkypi-weather" / "package" / "InkyPi" / "src"
LOCAL_PACKAGES = ROOT / "inkypi-weather" / "package" / "InkyPi" / ".pc-packages"

if LOCAL_PACKAGES.exists():
    sys.path.insert(0, str(LOCAL_PACKAGES))
sys.path.insert(0, str(SRC))

from plugins.magazine_covers.magazine_covers import DEFAULT_SOURCES, MagazineCovers  # noqa: E402
from plugins.plugin_registry import get_plugin_instance, load_plugins  # noqa: E402


class DeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
        }
        if key is None:
            return values
        return values.get(key, default)


def main() -> int:
    preview_dir = ROOT / ".tmp" / "magazine_covers_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    os.environ["INKYPI_MAGAZINE_COVERS_CACHE"] = str(preview_dir / "cache")

    plugin = MagazineCovers({"id": "magazine_covers"})
    sources = plugin._parse_sources(DEFAULT_SOURCES)
    if not sources:
        raise AssertionError("No default magazine sources parsed.")

    load_plugins([{"id": "magazine_covers", "class": "MagazineCovers"}])
    registered = get_plugin_instance({"id": "magazine_covers"})
    if not isinstance(registered, MagazineCovers):
        raise AssertionError("Magazine Covers plugin did not register.")

    results = []
    for source in sources:
        settings = {
            "rotationMode": "single",
            "sources": f"{source['name']}|{source['url']}",
            "fitMode": "rotate_full",
            "backgroundStyle": "blur",
            "backgroundColor": "white",
        }
        try:
            image = plugin.generate_image(settings, DeviceConfig())
            if image.size != (800, 480):
                raise AssertionError(f"Unexpected image size for {source['name']}: {image.size}")
            colors = image.convert("RGB").getcolors(maxcolors=1_000_000) or []
            if len(colors) < 2:
                raise AssertionError(f"Preview appears blank for {source['name']}.")
            out_path = preview_dir / f"{slug(source['name'])}.png"
            image.save(out_path)
            results.append((source["name"], "ok", out_path))
        except Exception as exc:
            results.append((source["name"], f"failed: {exc}", None))

    for name, status, path in results:
        if path:
            print(f"{name}: {status} -> {path}")
        else:
            print(f"{name}: {status}")

    if not any(status == "ok" for _name, status, _path in results):
        raise AssertionError("No live magazine cover previews succeeded.")
    return 0


def slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


if __name__ == "__main__":
    raise SystemExit(main())
