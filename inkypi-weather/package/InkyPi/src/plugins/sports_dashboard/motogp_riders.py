"""MotoGP riders' official profiles, for the faces and team colours beside their names.

The Pulselive riders API answers a rider (by the results' ``riders_api_uuid``) with their name, the
current season's team (its colour) and the official profile picture: a full-length cut-out on a
transparent ground, about 2000x3000 px. The picture is fetched once, cut down to its head and
shoulders and kept as a small PNG in the dashboard's cache with the rider's answer (a found rider
for 30 days, a miss for 3). A refresh looks up at most LOOKUPS_PER_REFRESH riders; a cache-only
redraw only reads what is kept.
"""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from PIL import Image

from runtime.refresh_contracts import TaskCancelled

from . import extra_feeds

logger = logging.getLogger(__name__)
API = "https://api.motogp.pulselive.com/motogp/v1/riders/{uuid}"
CACHE_FILE = "extra_motogp_riders.json"
PHOTO_DIR = "motogp_riders"
VERSION = 1
FOUND_TTL, MISSED_TTL = timedelta(days=30), timedelta(days=3)
LOOKUPS_PER_REFRESH = 4
DEADLINE_SECONDS = 25
JSON_MAX_BYTES = 512 * 1024
PHOTO_MAX_BYTES = 8 * 1024 * 1024
# The kept face: a square of the figure's head and shoulders, FACE_SHARE of its height tall.
FACE_SIZE = 72
FACE_SHARE = 0.2
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
_PARTICLES = {"di", "da", "de", "del", "della", "van", "von", "der", "dos", "le", "la"}


def surname(full_name):
    """"Fabio Di Giannantonio" -> "Di Giannantonio": the last word with the particles before it."""
    words = str(full_name or "").split()
    if not words:
        return ""
    start = len(words) - 1
    while start > 1 and words[start - 1].lower() in _PARTICLES:
        start -= 1
    return " ".join(words[start:])


def _current_step(profile):
    career = [step for step in profile.get("career") or [] if isinstance(step, dict)]
    return next((step for step in career if step.get("current")), career[0] if career else {})


def head_and_shoulders(picture, size=FACE_SIZE):
    """The figure's head and shoulders from a full-length cut-out, as a ``size`` square RGBA image;
    None when the picture has no figure."""
    figure = picture.convert("RGBA")
    # Work on a quarter-size copy: the cut is the same, the arithmetic far lighter.
    if figure.height > 4 * size * 3:
        figure = figure.reduce(max(1, figure.height // (size * 12)))
    alpha = figure.getchannel("A").point(lambda value: 255 if value >= 96 else 0)
    box = alpha.getbbox()
    if not box:
        return None
    height = box[3] - box[1]
    side = max(8, round(height * FACE_SHARE))
    # The head's middle: the opaque columns of the figure's top tenth.
    crown = alpha.crop((box[0], box[1], box[2], box[1] + max(2, height // 10)))
    columns = crown.resize((crown.width, 1), Image.Resampling.BOX).get_flattened_data()
    weight = sum(columns)
    middle = box[0] + (sum(index * value for index, value in enumerate(columns)) / weight if weight else crown.width / 2)
    top = box[1] - round(side * 0.04)
    left = round(middle - side / 2)
    face = figure.crop((left, top, left + side, top + side))
    return face.resize((size, size), Image.Resampling.LANCZOS)


def _lookup(client, uuid, photo_path, deadline):
    response = client.request_json("GET", API.format(uuid=uuid), timeout=extra_feeds.remaining(deadline),
                                   max_bytes=JSON_MAX_BYTES)
    profile = response.data if isinstance(response.data, dict) else {}
    step = _current_step(profile)
    team = step.get("team") if isinstance(step.get("team"), dict) else {}
    colour = str(team.get("color") or "")
    entry = {"name": str(profile.get("name") or "")[:24], "surname": str(profile.get("surname") or "")[:32],
             "team": str(team.get("name") or "")[:40], "color": colour if _COLOUR.fullmatch(colour) else "",
             "photo": ""}
    pictures = step.get("pictures") if isinstance(step.get("pictures"), dict) else {}
    url = str(((pictures.get("profile") or {}).get("main")) or "")
    if url.startswith("https://photos.motogp.com/"):
        data = client.request_bytes("GET", url, timeout=extra_feeds.remaining(deadline), max_bytes=PHOTO_MAX_BYTES).data
        with Image.open(io.BytesIO(data)) as picture:
            face = head_and_shoulders(picture)
        if face is not None:
            photo_path.parent.mkdir(parents=True, exist_ok=True)
            face.save(photo_path, "PNG", optimize=True)
            entry["photo"] = photo_path.name
    return entry


def rider_profiles(plugin, settings, rows, now):
    """{riders_api_uuid: {"name", "surname", "team", "color", "photo" (a path or "")}} for the riders of
    ``rows``: the kept answers, with up to LOOKUPS_PER_REFRESH new lookups when the network is allowed."""
    now = now.astimezone(timezone.utc)
    folder = plugin._sports_dashboard_cache_dir()
    path = folder / CACHE_FILE
    cache = plugin._read_json_file(path)
    kept = cache.get("riders") if isinstance(cache, dict) and cache.get("version") == VERSION else None
    kept = kept if isinstance(kept, dict) else {}

    def current(entry):
        try:
            at = datetime.fromisoformat(entry["at"])
        except (KeyError, TypeError, ValueError):
            return False
        return now - at <= (FOUND_TTL if entry.get("photo") else MISSED_TTL)

    uuids = list(dict.fromkeys(str(row.get("uuid") or "") for row in rows if _UUID.fullmatch(str(row.get("uuid") or ""))))
    wanted = [uuid for uuid in uuids if not (isinstance(kept.get(uuid), dict) and current(kept[uuid]))]
    if wanted and not plugin._provider_cache_only(settings):
        changed = False
        try:
            with extra_feeds.create_single_attempt_http_client() as client:
                deadline = time.monotonic() + DEADLINE_SECONDS
                for uuid in wanted[:LOOKUPS_PER_REFRESH]:
                    try:
                        entry = _lookup(client, uuid, folder / PHOTO_DIR / f"{uuid}.png", deadline)
                    except TaskCancelled:
                        raise
                    except Exception as exc:  # noqa: BLE001 - this rider is looked up again next time.
                        logger.info("No MotoGP profile for a rider: %s", type(exc).__name__)
                        if isinstance(exc, TimeoutError):
                            break
                        continue
                    kept[uuid] = {**entry, "at": now.isoformat()}
                    changed = True
        except TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - the panel keeps its names and marks alone.
            logger.info("MotoGP rider lookups unavailable: %s", type(exc).__name__)
        if changed:
            try:
                plugin._write_json_file(path, {"version": VERSION, "riders": kept})
            except OSError as exc:
                logger.warning("Failed to keep the MotoGP riders: %s", exc)
    profiles = {}
    for uuid in uuids:
        entry = kept.get(uuid)
        if isinstance(entry, dict):
            photo = folder / PHOTO_DIR / str(entry.get("photo") or "") if entry.get("photo") else None
            profiles[uuid] = {**{key: entry.get(key, "") for key in ("name", "surname", "team", "color")},
                              "photo": str(photo) if photo is not None and photo.is_file() else ""}
    return profiles
