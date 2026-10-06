"""Identity-bound Steam artwork with a bounded, per-render fetch budget.

Construct one provider per render. Source images are read/downloaded once, then
resized in memory. Disk cache identity is independent of dashboard style/version.
"""

from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps

from plugins.steam_profile_dashboard.focal_crop import focused_cover_fit

from runtime.long_task_executor import current_task_context
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded
from utils.atomic_file import atomic_write_image, atomic_write_json
from utils.http_client import HttpClient, HttpStatusError, create_single_attempt_http_client
from utils.safe_image import ImageLimits, safe_open_image


APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
ICON_URL = "https://shared.fastly.steamstatic.com/community_assets/images/apps/{appid}/{icon_hash}.jpg"
METADATA_TTL = 7 * 24 * 60 * 60
IMAGE_TTL = 30 * 24 * 60 * 60
NEGATIVE_TTL = 6 * 60 * 60
TRANSIENT_TTL = 15 * 60
ASSET_LIMITS = ImageLimits(max_bytes=3 * 1024 * 1024, max_width=4096, max_height=4096, max_pixels=4_000_000)
_CACHE_VERSION = 1
CACHE_MAX_GAMES = 64
CACHE_MAX_BYTES = 32 * 1024 * 1024


def _appid(value):
    text = str(value or "")
    return text if re.fullmatch(r"[1-9][0-9]{0,9}", text) else None


def _asset_url(value, appid, kind):
    """Accept only an official CDN URL whose path identifies this exact app."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        if not (host.endswith(".steamstatic.com") or host == "steamcdn-a.akamaihd.net"):
            return None
        path = unquote(parsed.path)
        if ".." in path or "\\" in path or "\x00" in path:
            return None
        if kind == "icon":
            pattern = rf"/(?:community_assets|steamcommunity/public)/images/apps/{appid}/[a-fA-F0-9]{{40}}\.(?:jpg|png)"
        else:
            pattern = rf"/(?:store_item_assets/)?steam/apps/{appid}/(?:[a-fA-F0-9]+/)?[^/]+\.(?:jpg|jpeg|png|webp)"
        return value if re.fullmatch(pattern, path) else None
    except ValueError:
        return None


class _AppHubIcon(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.urls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "apphub_AppIcon" in str(values.get("class", "")).split():
                self.depth = 1
        if tag == "img" and self.depth:
            self.urls.append(values.get("src"))

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1


class SteamGameAssets:
    """Public get_background/get_icon return a new PIL image or None.

    ``record`` may contain the exact AppID's ``img_icon_url`` hash from Steam's
    owned/recent games API. Otherwise the official AppHub page supplies the icon.
    Diagnostics distinguish downloaded, disk, memory, missing and budget cases.
    A zero network budget skips HTTP. ``read_only=True`` additionally prohibits
    metadata writes, directory creation and eviction, even for new API hashes.
    """

    def __init__(self, cache_dir, session=None, *, budget_seconds=12, max_games=7, context=None, read_only=False):
        self.cache_dir = Path(cache_dir)
        self.read_only = bool(read_only)
        self.client = None if self.read_only else (
            HttpClient(session=session, max_attempts=1) if session is not None else create_single_attempt_http_client()
        )
        context = context if context is not None else current_task_context()
        self._deadline = time.monotonic() + max(0, float(budget_seconds))
        self.context = TaskContext(
            cancel_event=context.cancel_event if context else threading.Event(),
            deadline_monotonic=min(self._deadline, context.deadline_monotonic) if context else self._deadline,
        )
        self.max_games = max(1, int(max_games))
        self._metadata = {}
        self._sources = {}
        self._lock = threading.RLock()
        self._cache_writes = False
        self.diagnostics = {name: 0 for name in (
            "requests", "downloaded", "disk_hits", "memory_hits", "missing", "budget_skips", "rejected", "errors"
        )}
        self.sources = {}

    def close(self):
        try:
            if self._cache_writes and not self.read_only:
                self._prune_cache()
                self._cache_writes = False
        finally:
            if self.client is not None:
                self.client.close()
            for source in self._sources.values():
                if source is not None:
                    source.close()
            self._sources.clear()

    def _prune_cache(self):
        """Bound only this module's validated groups, protecting visible games.

        Run after a cache publication, not on every cached render. Unknown files,
        symlinks and recently accessed groups from another instance are retained.
        """
        groups = []
        try:
            with os.scandir(self.cache_dir) as entries:
                for index, entry in enumerate(entries):
                    if index >= 4096:
                        break
                    match = re.fullmatch(r"([1-9][0-9]{0,9})\.json", entry.name)
                    if not match or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                    appid = match.group(1)
                    path = Path(entry.path)
                    # Path.stat supplies a real file identity on Windows;
                    # DirEntry.stat can report st_ino=0 and break the recheck.
                    stat = path.stat()
                    if stat.st_size > 32 * 1024:
                        continue
                    try:
                        meta = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if not isinstance(meta, dict) or meta.get("appid") != appid or meta.get("version") != _CACHE_VERSION:
                        continue
                    files = [(path, stat)]
                    for kind in ("background", "icon"):
                        image = self.cache_dir / f"{appid}-{kind}.png"
                        if _asset_url(meta.get(f"{kind}_image_url"), appid, kind) and not image.is_symlink():
                            try:
                                if image.is_file():
                                    files.append((image, image.stat()))
                            except OSError:
                                pass
                    # Reading manifests while pruning must not make every old
                    # group look recently used. Only actual image reads count.
                    accessed = max([stat.st_mtime] +
                                   [max(item.st_atime, item.st_mtime) for _, item in files[1:]])
                    groups.append((accessed, appid, files))
            count = len(groups)
            total = sum(stat.st_size for _, _, files in groups for _, stat in files)
            for accessed, appid, files in sorted(groups):
                if count <= CACHE_MAX_GAMES and total <= CACHE_MAX_BYTES:
                    break
                if appid in self._metadata or time.time() - accessed < 300:
                    continue
                # Recheck every owned file before deleting the group, in case a
                # different instance just published a new version after scanning.
                try:
                    if any(path.is_symlink() or
                           (path.stat().st_mtime_ns, path.stat().st_size, path.stat().st_ino) !=
                           (stat.st_mtime_ns, stat.st_size, stat.st_ino) for path, stat in files):
                        continue
                    for path, _ in reversed(files):
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
                count -= 1
                total -= sum(stat.st_size for _, stat in files)
        except OSError:
            self.diagnostics["errors"] += 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def get_background(self, appid, size, record=None):
        return self._get(appid, size, "background", record)

    def get_icon(self, appid, size, record=None):
        return self._get(appid, size, "icon", record)

    def _get(self, appid, size, kind, record):
        appid = _appid(appid)
        if not appid or len(size) != 2 or any(type(n) is not int or n <= 0 or n > 4096 for n in size):
            self.diagnostics["rejected"] += 1
            return None
        # Serialize a shared instance so concurrent requests cannot duplicate a
        # source fetch, race metadata publication, or spend the budget twice.
        with self._lock:
            key = (appid, kind)
            if key in self._sources:
                self.diagnostics["memory_hits"] += 1
                source = self._sources[key]
            else:
                if appid not in self._metadata and len(self._metadata) >= self.max_games:
                    self.diagnostics["budget_skips"] += 1
                    return None
                meta = self._load_metadata(appid)
                source = self._source(appid, kind, record, meta)
                self._sources[key] = source
                if source is None:
                    self.diagnostics["missing"] += 1
            if source is None:
                return None
            if kind == "background":
                fitted, crop = focused_cover_fit(source, size, appid)
                self.sources.setdefault(f"{appid}:{kind}", {})["crop"] = crop
                return fitted
            # Independent square icon; never crop a header into a fake logo.
            return ImageOps.pad(source, size, method=Image.Resampling.LANCZOS, color=(16, 30, 43, 255))

    def _load_metadata(self, appid):
        if appid in self._metadata:
            return self._metadata[appid]
        meta = {}
        path = self.cache_dir / f"{appid}.json"
        try:
            if path.stat().st_size <= 32 * 1024:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded.get("appid") == appid and loaded.get("version") == _CACHE_VERSION:
                    meta = loaded
        except (OSError, ValueError):
            pass
        meta.update(appid=appid, version=_CACHE_VERSION)
        self._metadata[appid] = meta
        return meta

    def _save_metadata(self, meta):
        if self.read_only:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.cache_dir / f"{meta['appid']}.json", meta)
            self._cache_writes = True
        except (OSError, ValueError):
            self.diagnostics["errors"] += 1

    @staticmethod
    def _stamp(meta, key):
        value = meta.get(key)
        return float(value) if type(value) in (int, float) else 0

    def _available(self):
        if self.read_only:
            return False
        if self.context.remaining_seconds() <= 0:
            self.diagnostics["budget_skips"] += 1
            return False
        self.context.raise_if_cancelled()
        return True

    def _request(self, url, max_bytes, **kwargs):
        if self.read_only:
            raise RuntimeError("Steam artwork is read-only during cached display")
        self.diagnostics["requests"] += 1
        return self.client.request_bytes(
            "GET", url, context=self.context, timeout=3, max_bytes=max_bytes,
            allow_redirects=False, **kwargs,
        ).data

    def _failed(self, meta, key, error):
        if isinstance(error, TaskDeadlineExceeded):
            self.diagnostics["budget_skips"] += 1
            return
        if isinstance(error, TaskCancelled):
            raise error
        self.diagnostics["errors"] += 1
        ttl = NEGATIVE_TTL if isinstance(error, (ValueError, HttpStatusError)) else TRANSIENT_TTL
        if isinstance(error, HttpStatusError) and error.status != 404:
            ttl = TRANSIENT_TTL
        meta[f"{key}_retry_after"] = time.time() + ttl
        self._save_metadata(meta)

    def _source(self, appid, kind, record, meta, allow_hub_fallback=True):
        now = time.time()
        source = None
        old_url = _asset_url(meta.get(f"{kind}_image_url"), appid, kind)
        path = self.cache_dir / f"{appid}-{kind}.png"
        if old_url:
            try:
                source = safe_open_image(path, limits=ASSET_LIMITS).convert("RGBA")
                self.diagnostics["disk_hits"] += 1
                self.sources[f"{appid}:{kind}"] = {"source": "disk", "url": old_url}
            except (OSError, ValueError):
                pass
        if self.read_only:
            return source
        url = self._resolve_url(appid, kind, record, meta, now)
        if not url:
            return source
        if source is not None and old_url == url and now - self._stamp(meta, f"{kind}_image_at") < IMAGE_TTL:
            return source
        if self._stamp(meta, f"{kind}_image_retry_after") > now or not self._available():
            return source
        try:
            payload = self._request(url, ASSET_LIMITS.max_bytes)
            downloaded = safe_open_image(payload, limits=ASSET_LIMITS).convert("RGBA")
            if kind == "icon" and (downloaded.width > downloaded.height * 1.3 or downloaded.height > downloaded.width * 1.3):
                raise ValueError("Steam icon is not a standalone square asset")
            # Cap originals on the Pi while retaining enough pixels for the
            # 800x480 display and avoiding resize/download for every card size.
            downloaded.thumbnail((1200, 600) if kind == "background" else (256, 256), Image.Resampling.LANCZOS)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_image(path, downloaded)
            meta.update({f"{kind}_image_url": url, f"{kind}_image_at": now, f"{kind}_image_retry_after": 0})
            self._save_metadata(meta)
            self.diagnostics["downloaded"] += 1
            self.sources[f"{appid}:{kind}"] = {"source": "download", "url": url}
            return downloaded
        except (OSError, ValueError, RuntimeError) as error:
            self._failed(meta, f"{kind}_image", error)
            # API icon hashes occasionally lag behind the AppHub. Resolve its
            # current independent icon once after a missing/invalid hash image.
            # A transport outage does not justify more requests to another host.
            if kind == "icon" and allow_hub_fallback and isinstance(record, dict):
                icon_hash = str(record.get("img_icon_url") or "")
                record_url = ICON_URL.format(appid=appid, icon_hash=icon_hash)
                missing = isinstance(error, ValueError) or (isinstance(error, HttpStatusError) and error.status == 404)
                if missing and url == record_url and _appid(record.get("appid")) == appid:
                    meta.update(icon_record_hash_bad=icon_hash, icon_record_hash_bad_until=now + METADATA_TTL,
                                icon_checked_at=0)
                    self._save_metadata(meta)
                    return self._source(appid, kind, None, meta, allow_hub_fallback=False)
            return source

    def _resolve_url(self, appid, kind, record, meta, now):
        url = _asset_url(meta.get(f"{kind}_url"), appid, kind)
        if kind == "icon" and isinstance(record, dict) and _appid(record.get("appid")) == appid:
            icon_hash = str(record.get("img_icon_url") or "")
            known_bad = meta.get("icon_record_hash_bad") == icon_hash and self._stamp(meta, "icon_record_hash_bad_until") > now
            if re.fullmatch(r"[a-fA-F0-9]{40}", icon_hash) and not known_bad:
                candidate = ICON_URL.format(appid=appid, icon_hash=icon_hash)
                if candidate != url:
                    meta.update(icon_url=candidate, icon_checked_at=now, icon_image_retry_after=0)
                    self._save_metadata(meta)
                return candidate
        if url and now - self._stamp(meta, f"{kind}_checked_at") < METADATA_TTL:
            return url
        if self._stamp(meta, f"{kind}_retry_after") > now or not self._available():
            return url
        try:
            if kind == "background":
                payload = json.loads(self._request(
                    APPDETAILS_URL, 512 * 1024,
                    params={"appids": appid, "l": "english", "cc": "us"},
                ))
                item = payload.get(appid) if isinstance(payload, dict) else None
                data = item.get("data") if isinstance(item, dict) and item.get("success") is True else None
                if not isinstance(data, dict) or _appid(data.get("steam_appid")) != appid:
                    raise ValueError("Steam appdetails identity mismatch or unavailable app")
                candidate = next((valid for field in ("header_image", "capsule_image", "capsule_imagev5")
                                  if (valid := _asset_url(data.get(field), appid, kind))), None)
            else:
                body = self._request(f"https://steamcommunity.com/app/{appid}/", 1024 * 1024)
                parser = _AppHubIcon()
                parser.feed(body.decode("utf-8", errors="replace"))
                candidate = next((valid for value in parser.urls if (valid := _asset_url(value, appid, kind))), None)
            if not candidate:
                raise ValueError("Steam did not return identity-bound official artwork")
            if candidate != url:
                meta[f"{kind}_image_retry_after"] = 0
            meta.update({f"{kind}_url": candidate, f"{kind}_checked_at": now, f"{kind}_retry_after": 0})
            self._save_metadata(meta)
            return candidate
        except (OSError, ValueError, RuntimeError) as error:
            self._failed(meta, kind, error)
            return url
