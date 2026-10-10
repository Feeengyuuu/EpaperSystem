"""Rotating artwork frames around the console rail avatar.

The bundled frames (built by tools/build_steam_avatar_frames.py) are dealt in
shuffled rounds: every frame appears once per round, and a new round never
opens with the frame that closed the previous one, so two consecutive renders
never share a frame. Each frame ships with a mask of its opening, which is
where the avatar photo shows.

The round survives restarts in a small JSON file. Without a writable state
file the rotation continues in memory for the life of the process.
"""

from dataclasses import dataclass
from functools import lru_cache
import json
import logging
from pathlib import Path
import random
import re
import threading

from PIL import Image

from utils.atomic_file import atomic_write_json
from utils.safe_image import ImageLimits, safe_open_image


logger = logging.getLogger(__name__)

FRAME_ROOT = Path(__file__).resolve().parent / "assets" / "avatar_frames"
FRAME_SIZE = (178, 178)
# Inside the 182 px rail and above the persona name.
FRAME_ORIGIN = (1, 0)
STATE_VERSION = 1
_ID_PATTERN = re.compile(r"\d{2}\Z")
_MANIFEST_MAX_BYTES = 64 * 1024
_STATE_MAX_BYTES = 16 * 1024
_LIMITS = ImageLimits(max_bytes=512 * 1024, max_width=FRAME_SIZE[0],
                      max_height=FRAME_SIZE[1], max_pixels=FRAME_SIZE[0] * FRAME_SIZE[1],
                      allowed_formats=frozenset({"PNG"}))

_lock = threading.Lock()
_memory_states = {}
_write_failure_logged = set()


@dataclass(frozen=True)
class AvatarFrame:
    frame_id: str
    title: str
    image: Image.Image
    mask: Image.Image

    @property
    def opening(self):
        """Bounding box of the avatar opening, in frame coordinates."""
        return self.mask.getbbox()


@lru_cache(maxsize=1)
def frame_catalog():
    """Return ((id, title), ...) for every bundled frame with both images present."""
    try:
        path = FRAME_ROOT / "frames.json"
        if path.stat().st_size > _MANIFEST_MAX_BYTES:
            raise ValueError("avatar frame manifest is too large")
        entries = json.loads(path.read_text(encoding="utf-8")).get("frames")
    except (OSError, ValueError, AttributeError) as error:
        logger.warning("Steam avatar frame manifest unavailable: %s", error)
        return ()
    catalog = []
    for entry in entries if isinstance(entries, list) else ():
        frame_id = entry.get("id") if isinstance(entry, dict) else None
        title = entry.get("title") if isinstance(entry, dict) else None
        if (not isinstance(frame_id, str) or _ID_PATTERN.match(frame_id) is None
                or not isinstance(title, str) or any(frame_id == known for known, _ in catalog)):
            continue
        if (FRAME_ROOT / f"frame_{frame_id}.png").is_file() and (FRAME_ROOT / f"mask_{frame_id}.png").is_file():
            catalog.append((frame_id, title))
    return tuple(catalog)


def _open(name, mode):
    with safe_open_image(FRAME_ROOT / name, limits=_LIMITS) as opened:
        if opened.size != FRAME_SIZE:
            raise ValueError(f"{name} is not {FRAME_SIZE}")
        return opened.convert(mode)


def load_avatar_frame(frame_id):
    """Load one frame and its opening mask, or None when either is unusable."""
    title = dict(frame_catalog()).get(frame_id)
    if title is None:
        return None
    try:
        image = _open(f"frame_{frame_id}.png", "RGBA")
        mask = _open(f"mask_{frame_id}.png", "L")
    except (OSError, ValueError) as error:
        logger.warning("Steam avatar frame %s unavailable: %s", frame_id, error)
        return None
    if mask.getbbox() is None:
        return None
    return AvatarFrame(frame_id, title, image, mask)


def _valid_state(raw, known):
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return None
    queue, last = raw.get("queue"), raw.get("last")
    if not isinstance(queue, list) or not all(isinstance(item, str) for item in queue):
        return None
    if len(set(queue)) != len(queue):
        return None
    return {"queue": [item for item in queue if item in known],
            "last": last if isinstance(last, str) and last in known else None}


def _read_state(state_path, known):
    remembered = _memory_states.get(state_path)
    if remembered is not None:
        return _valid_state(remembered, known)
    if state_path is None:
        return None
    try:
        if state_path.stat().st_size > _STATE_MAX_BYTES:
            return None
        return _valid_state(json.loads(state_path.read_text(encoding="utf-8")), known)
    except (OSError, ValueError):
        return None


def _write_state(state_path, state):
    _memory_states[state_path] = state
    if state_path is None:
        return
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(state_path, state)
    except OSError as error:
        if state_path not in _write_failure_logged:
            _write_failure_logged.add(state_path)
            logger.warning("Steam avatar frame order kept in memory only: %s", error)


def next_frame_id(state_path=None, rng=None):
    """Deal the next frame id from the shuffled round persisted at ``state_path``."""
    known = [frame_id for frame_id, _ in frame_catalog()]
    if not known:
        return None
    rng = rng or random
    with _lock:
        state = _read_state(state_path, set(known)) or {"queue": [], "last": None}
        queue, last = [item for item in state["queue"] if item != state["last"]], state["last"]
        if not queue:
            queue = list(known)
            rng.shuffle(queue)
            if len(queue) > 1 and queue[0] == last:
                swap = rng.randrange(1, len(queue))
                queue[0], queue[swap] = queue[swap], queue[0]
        chosen = queue.pop(0)
        _write_state(state_path, {"version": STATE_VERSION, "queue": queue, "last": chosen})
        return chosen


def next_avatar_frame(state_path=None, rng=None):
    """Return the next AvatarFrame in the rotation, or None if none can be loaded."""
    for _attempt in range(len(frame_catalog())):
        frame_id = next_frame_id(state_path, rng)
        if frame_id is None:
            return None
        frame = load_avatar_frame(frame_id)
        if frame is not None:
            return frame
    return None
