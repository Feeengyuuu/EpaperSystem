"""Optional nearby Caltrans stills, sharing the camera-wall cache when possible.

The directory is cached for twelve hours; only the three selected closures can request
stills. Coordinates, road number and direction must agree within two kilometres. These
are nearby road views, never a claim that a camera points at the closure itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.source_book import Source, SourceBook
from plugins.bay_traffic_cams import sources as cameras
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded
from utils.cache_manager import CacheBudget, CacheError, cache_namespace_for_directory
from utils.http_client import get_http_client
from utils.safe_image import ImageLimits, safe_open_image

from .sources import parse_time, rank_roads, select_roads, valid_coordinates


logger = logging.getLogger(__name__)
DIRECTORY_AGE = 12 * 3600
FRAME_FRESH = 15 * 60
FRAME_USABLE = 6 * 3600
DIRECTORY_LIMIT = 512 * 1024
FRAME_LIMITS = ImageLimits(max_bytes=512 * 1024, max_width=1280, max_height=1024, max_pixels=1280 * 1024)
FRAME_BUDGET = CacheBudget(7 * 86400, 36, 8 * 1024 * 1024)
CATALOG_BUDGET = CacheBudget(14 * 86400, 6, 2 * 1024 * 1024)


def route_number(value):
    match = re.fullmatch(r"(?:I|SR|US)?[- ]?(\d{1,3})", str(value or "").strip().upper())
    return int(match.group(1)) if match else None


def matching_cameras(row, catalog):
    """Nearest first, using provider coordinates rather than home-distance estimates."""
    point = (row.get("begin_lat"), row.get("begin_lon"))
    if not valid_coordinates(*point):
        return []
    directions = {part.strip().casefold() for part in str(row.get("direction") or "").split("/")}
    route = route_number(row.get("route"))
    if route is None or not directions <= {"north", "south", "east", "west"}:
        return []
    matched = []
    for camera in catalog:
        if not isinstance(camera, dict):
            continue
        coords = (camera.get("latitude"), camera.get("longitude"))
        if (not valid_coordinates(*coords) or route_number(camera.get("route")) != route
                or str(camera.get("direction") or "").casefold() not in directions
                or not cameras.safe_image_url(camera.get("image"))):
            continue
        distance = cameras.distance_km(point, coords)
        if distance <= 2:
            matched.append({**camera, "closure_distance_km": distance})
    return sorted(matched, key=lambda item: (item["closure_distance_km"], item.get("id", "")))[:3]


def _read_json(path, limit):
    try:
        if path.is_symlink() or path.stat().st_size > limit:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _fresh(stamp, now, limit):
    parsed = parse_time(stamp)
    return parsed is not None and -300 <= (now - parsed).total_seconds() <= limit


def _shared_catalog(root, now):
    candidates = []
    for path in sorted((root / "sources").glob("cameras-*.json"))[:8]:
        entry = _read_json(path, DIRECTORY_LIMIT)
        if not _fresh(entry.get("fetched_at"), now, DIRECTORY_AGE):
            continue
        data = entry.get("data") or {}
        if not isinstance(data, dict):
            continue
        rows = [*(data.get("cameras") or []), *(data.get("patrol") or [])]
        rows = [row for row in rows if isinstance(row, dict)
                and valid_coordinates(row.get("latitude"), row.get("longitude"))]
        if rows:
            candidates.append((entry["fetched_at"], rows))
    return max(candidates, default=("", []), key=lambda item: item[0])[1]


def _fetch_catalog(routes, context):
    response = get_http_client().request_json("GET", cameras.CAMERAS_URL, context=context,
                                             timeout=(3, 6), max_bytes=6 * 1024 * 1024,
                                             headers={"User-Agent": cameras.USER_AGENT})
    raw = response.data.get("data") if isinstance(response.data, dict) else None
    if not isinstance(raw, list) or len(raw) > 2000:
        raise ValueError("Invalid camera directory")
    wanted = {route_number(route) for route in routes}
    result = []
    for entry in raw:
        item = (entry or {}).get("cctv") or {}
        location = item.get("location") or {}
        image = cameras.safe_image_url(((item.get("imageData") or {}).get("static") or {}).get("currentImageURL"))
        try:
            lat, lon = float(location.get("latitude")), float(location.get("longitude"))
        except (ValueError, TypeError):
            continue
        if (str(item.get("inService")).lower() != "true" or not image
                or route_number(location.get("route")) not in wanted or not valid_coordinates(lat, lon)):
            continue
        result.append({"id": str(location.get("locationName") or "").split(" ", 1)[0][:12],
                       "route": str(location.get("route") or "")[:10],
                       "direction": str(location.get("direction") or "")[:20],
                       "latitude": lat, "longitude": lon, "image": image})
    return {"cameras": result}


def _catalog(root, shared_root, routes, now, context, allow_network):
    shared = _shared_catalog(shared_root, now)
    if shared:
        return shared
    book = SourceBook(root / "camera_sources", CATALOG_BUDGET, schema=1, max_bytes=DIRECTORY_LIMIT)
    source = Source("nearby_cameras", {"routes": sorted(routes)}, lambda ctx: _fetch_catalog(routes, ctx),
                    fresh_seconds=DIRECTORY_AGE, usable_seconds=14 * 86400,
                    validate=lambda data: isinstance(data, dict) and isinstance(data.get("cameras"), list)
                    and len(data["cameras"]) <= 2000,
                    timeout_seconds=7, min_remaining_seconds=4)
    result = book.load(source, now, context=context, allow_network=allow_network)
    return result["data"].get("cameras", [])


def _read_frame(directory, name, now):
    metadata = _read_json(directory / "frames.json", 256 * 1024).get(name) or {}
    if not isinstance(metadata, dict) or not _fresh(metadata.get("fetched"), now, FRAME_USABLE):
        return None
    path = directory / f"{name}.jpg"
    try:
        if not path.is_file() or path.is_symlink():
            return None
        with safe_open_image(path, limits=FRAME_LIMITS) as source:
            image = source.convert("RGB")
        if cameras.is_placeholder(image):
            image.close()
            return None
        return {"image": image, "fetched": metadata["fetched"],
                "stale": not _fresh(metadata["fetched"], now, FRAME_FRESH)}
    except (OSError, ValueError):
        return None


def _frame(camera, root, shared_root, now, context, allow_network, request_budget=None):
    url = cameras.safe_image_url(camera.get("image"))
    if not url:
        return None
    name = hashlib.sha256(url.encode()).hexdigest()[:24]
    directory = root / "camera_frames"
    candidates = [frame for folder in (shared_root / "frames", directory)
                  if (frame := _read_frame(folder, name, now)) is not None]
    candidates.sort(key=lambda item: parse_time(item["fetched"]), reverse=True)
    cached = candidates[0] if candidates else None
    for duplicate in candidates[1:]:
        duplicate["image"].close()
    if cached and not cached["stale"]:
        return cached
    if (allow_network and context.remaining_seconds() >= 2.5
            and (request_budget is None or request_budget[0] > 0)):
        try:
            context.raise_if_cancelled()
            if request_budget is not None:
                request_budget[0] -= 1
            response = get_http_client().request_bytes("GET", url, context=context, timeout=(2, 4),
                                                      max_bytes=FRAME_LIMITS.max_bytes, allow_redirects=False,
                                                      headers={"User-Agent": cameras.USER_AGENT})
            with safe_open_image(response.data, limits=FRAME_LIMITS) as source:
                image = source.convert("RGB")
            if cameras.is_placeholder(image):
                image.close()
            else:
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    namespace = cache_namespace_for_directory(directory, FRAME_BUDGET)
                    namespace.put_bytes(name + ".jpg", response.data)
                    metadata = _read_json(directory / "frames.json", 256 * 1024)
                    # Bound the small index independently of file cleanup.
                    metadata = {key: value for key, value in metadata.items()
                                if isinstance(value, dict) and _fresh(value.get("fetched"), now, 7 * 86400)}
                    metadata[name] = {"fetched": now.isoformat()}
                    namespace.put_bytes("frames.json", json.dumps(metadata).encode())
                except (OSError, CacheError):
                    logger.debug("Optional closure camera cache unavailable")
                if cached:
                    cached["image"].close()
                return {"image": image, "fetched": now.isoformat(), "stale": False}
        except TaskDeadlineExceeded:
            pass
        except TaskCancelled:
            if cached:
                cached["image"].close()
            raise
        except Exception as exc:
            logger.debug("Optional closure camera failed (%s)", type(exc).__name__)
    return cached


def load_nearby(plugin, payload, now, *, context, allow_network):
    """Return owned images keyed by closure id; caller closes them after rendering."""
    rows = select_roads(rank_roads(payload["roads"].get("data", {}).get("items", []), now), now, 3)
    if not rows:
        return {}
    root = plugin._cache_dir(create=False)
    shared = BasePlugin({"id": "bay_traffic_cams"}).cache_dir(
        env_var="BAY_TRAFFIC_CAMS_CACHE_DIR", leaf="cache", create=False, strip=True)
    child = TaskContext(context.cancel_event, min(context.deadline_monotonic - 1,
                                                context.clock() + 10), context.clock)
    try:
        catalog = _catalog(root, shared, payload["routes"], now, child,
                           allow_network and child.remaining_seconds() >= 4)
    except TaskDeadlineExceeded:
        # The optional camera allowance must never blank valid closures and tides.
        context.raise_if_cancelled()
        catalog = []
    result = {}
    # At most five still requests for the whole page, and ten seconds including directory.
    # A fresh farther candidate wins over repeatedly retrying a nearer unavailable camera.
    request_budget = [5]
    try:
        for row in rows:
            matches = matching_cameras(row, catalog)
            entry = {"status": "no_match", "image": None}
            chosen = None
            for camera in matches:
                frame = _frame(camera, root, shared, now, child, False)
                if frame and not frame["stale"]:
                    chosen = camera, frame
                    break
                if frame:
                    frame["image"].close()
            if chosen is None:
                for camera in matches:
                    frame = _frame(camera, root, shared, now, child, allow_network, request_budget)
                    if frame:
                        chosen = camera, frame
                        break
            if chosen:
                camera, frame = chosen
                entry = {**frame, "status": "available", "camera_id": camera["id"],
                         "distance_km": camera["closure_distance_km"]}
            if matches and entry["image"] is None:
                entry["status"] = "unavailable"
            result[row["id"]] = entry
        return result
    except BaseException:
        for entry in result.values():
            if entry.get("image") is not None:
                entry["image"].close()
        raise
