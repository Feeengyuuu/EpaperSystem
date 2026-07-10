from __future__ import annotations

import html
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from plugins.pixiv_r18_ranking.pixiv_r18_ranking import (
    DOWNLOAD_CHUNK_SIZE,
    PixivR18Ranking,
    _setting_enabled,
)
from utils.http_client import get_http_session

logger = logging.getLogger(__name__)

PLUGIN_ID = "reddit_rule34_hot"
STATE_VERSION = "reddit-rule34-hot-v1"
DEFAULT_POOL_SIZE = 20
MAX_POOL_SIZE = 50
DEFAULT_FIT_MODE = "auto_layout"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_BASE = "https://oauth.reddit.com"
REDDIT_POST_LIMIT = 100
TOKEN_REFRESH_SKEW_SECONDS = 60

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
REDDIT_USER_AGENT = "InkyPi:reddit_rule34_hot:v1 (local private display)"

RISK_TERMS = {
    "underage",
    "minor",
    "minors",
    "teen",
    "teens",
    "loli",
    "lolicon",
    "shota",
    "shotacon",
    "child",
    "children",
    "kid",
    "kids",
    "gore",
    "guro",
    "grotesque",
    "rape",
    "raped",
    "noncon",
    "non-con",
    "non consensual",
    "non-consensual",
    "bestiality",
    "zoophilia",
    "scat",
    "incest",
}


class RedditRule34Hot(PixivR18Ranking):
    """Daily Reddit hot-image pool using Pixiv's proven display pipeline."""

    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self._token_value = ""
        self._token_expires_at = 0.0

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        try:
            pool = self._daily_pool(settings, device_config, dimensions)
            if not pool:
                logger.warning("Reddit Rule34 hot daily pool is empty after filtering.")
                return self._fallback_image(dimensions, "Reddit Rule34", "No filtered image available")

            group = self._select_display_group(pool, settings)
            if not group:
                return self._fallback_image(dimensions, "Reddit Rule34", "No cached image available")

            images = []
            for item in group:
                image = self._load_cached_item_image(item, dimensions)
                if image:
                    images.append(image)
            if not images:
                logger.warning("Cached Reddit Rule34 image missing for %s", group[0].get("post_id"))
                return self._fallback_image(dimensions, "Reddit Rule34", "Cached image missing")

            logger.info(
                "Selected Reddit Rule34 hot image. | count: %s | post_ids: %s",
                len(images),
                [item.get("post_id") for item in group],
            )
            if len(images) >= 2:
                return self._compose_strip(images, dimensions, settings)
            return self._fit_image(images[0], dimensions, settings, group[0])
        except Exception as exc:
            logger.exception("Reddit Rule34 hot plugin failed: %s", exc)
            return self._fallback_image(dimensions, "Reddit Rule34", "Reddit unavailable")

    def _daily_pool_needs_refresh(self, settings):
        if not _setting_enabled(settings.get("dailyPoolMode", "true")):
            return True

        state = self._read_state()
        expected = {
            "state_version": STATE_VERSION,
            "day_key": self._day_key(),
            "subreddits": self._subreddits(settings),
            "pool_size": self._pool_size(settings),
        }
        for key, value in expected.items():
            if state.get(key) != value:
                return True

        return self._read_daily_pool_payload() is None

    def _refresh_daily_pool(self, settings, device_config, dimensions):
        subreddits = self._subreddits(settings)
        pool_size = self._pool_size(settings)
        usable = []
        candidates = []
        errors = []
        seen = set()

        if not subreddits:
            state = self._write_current_day_pool([], settings)
            state["last_refresh_errors"] = ["No subreddits configured"]
            self._write_state(state)
            return []

        for source_index, subreddit in enumerate(subreddits):
            try:
                posts = self._fetch_hot_posts(subreddit, device_config)
            except Exception as exc:
                errors.append(f"r/{subreddit}: {exc}")
                logger.warning("Could not fetch Reddit hot posts for r/%s: %s", subreddit, exc)
                continue

            for source_rank, post in enumerate(posts, start=1):
                if not self._is_usable_post(post):
                    continue
                try:
                    item = self._post_item_metadata(post, subreddit, source_index, source_rank)
                except Exception as exc:
                    errors.append(f"r/{subreddit} item: {exc}")
                    logger.warning("Could not parse Reddit hot post from r/%s: %s", subreddit, exc)
                    continue

                dedupe_key = item.get("post_id") or item.get("image_url")
                if not dedupe_key or dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                candidates.append(item)

        candidates.sort(
            key=lambda item: (
                -int(item.get("score") or 0),
                int(item.get("source_index") or 0),
                int(item.get("source_rank") or 0),
            )
        )

        for item in candidates:
            if len(usable) >= pool_size:
                break
            try:
                image_path = self._download_ranking_item_image(item, dimensions)
                if image_path:
                    item["image_path"] = str(image_path)
                    item["rank"] = len(usable) + 1
                    usable.append(item)
            except Exception as exc:
                errors.append(f"{item.get('post_id')}: {exc}")
                logger.warning("Could not cache Reddit hot post %s: %s", item.get("post_id"), exc)

        state = self._write_current_day_pool(usable, settings)
        state["last_refresh_errors"] = errors[-8:]
        self._write_state(state)
        if len(usable) < pool_size:
            logger.warning(
                "Reddit Rule34 pool under target. | subreddits: %s | got: %s | target: %s",
                subreddits,
                len(usable),
                pool_size,
            )
        else:
            logger.info(
                "Reddit Rule34 daily pool refreshed. | subreddits: %s | count: %s",
                subreddits,
                len(usable),
            )
        return usable

    def _fetch_hot_posts(self, subreddit, device_config):
        token = self._access_token(device_config)
        response = get_http_session().get(
            f"{REDDIT_API_BASE}/r/{subreddit}/hot",
            params={"limit": REDDIT_POST_LIMIT, "raw_json": 1},
            headers={
                "Authorization": f"bearer {token}",
                "Accept": "application/json",
                "User-Agent": REDDIT_USER_AGENT,
            },
            timeout=40,
        )
        response.raise_for_status()
        payload = response.json()
        children = ((payload or {}).get("data") or {}).get("children") or []
        posts = []
        for child in children:
            if not isinstance(child, dict):
                continue
            data = child.get("data")
            if isinstance(data, dict):
                posts.append(data)
        return posts

    def _access_token(self, device_config):
        now = time.time()
        if self._token_value and now < self._token_expires_at - TOKEN_REFRESH_SKEW_SECONDS:
            return self._token_value

        client_id = self._load_secret(device_config, "REDDIT_CLIENT_ID")
        client_secret = self._load_secret(device_config, "REDDIT_CLIENT_SECRET")
        refresh_token = self._load_secret(device_config, "REDDIT_REFRESH_TOKEN")
        missing = [
            name
            for name, value in (
                ("REDDIT_CLIENT_ID", client_id),
                ("REDDIT_CLIENT_SECRET", client_secret),
                ("REDDIT_REFRESH_TOKEN", refresh_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing Reddit API credentials: {', '.join(missing)}")

        response = get_http_session().post(
            REDDIT_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(client_id, client_secret),
            headers={"Accept": "application/json", "User-Agent": REDDIT_USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
        token = str((payload or {}).get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Reddit OAuth did not return access_token")
        try:
            expires_in = int((payload or {}).get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600
        self._token_value = token
        self._token_expires_at = now + max(60, expires_in)
        return token

    def _post_item_metadata(self, post, subreddit, source_index, source_rank):
        post_id = str(post.get("id") or "").strip()
        title = str(post.get("title") or "").strip()
        image_url, width, height = self._post_image(post)
        if not post_id or not image_url:
            raise RuntimeError("post missing id or image URL")

        author = str(post.get("author") or "").strip()
        permalink = str(post.get("permalink") or "").strip()
        if permalink and permalink.startswith("/"):
            page_url = f"https://www.reddit.com{permalink}"
        else:
            page_url = str(post.get("url") or "")
        try:
            score = int(post.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        try:
            comments = int(post.get("num_comments") or 0)
        except (TypeError, ValueError):
            comments = 0

        return {
            "illust_id": post_id,
            "post_id": post_id,
            "rank": int(source_rank),
            "source_index": int(source_index),
            "source_rank": int(source_rank),
            "title": title,
            "artist": f"r/{subreddit}" + (f" - u/{author}" if author else ""),
            "subreddit": subreddit,
            "author": author,
            "score": score,
            "num_comments": comments,
            "width": width,
            "height": height,
            "page_url": page_url,
            "image_url": image_url,
            "cached_at": self._now_utc().isoformat(),
        }

    def _is_usable_post(self, post):
        if not isinstance(post, dict):
            return False
        if not post.get("over_18"):
            return False
        if post.get("stickied") or post.get("spoiler"):
            return False
        if post.get("is_video") or str(post.get("post_hint") or "").lower() in {"hosted:video", "rich:video"}:
            return False
        if str(post.get("removed_by_category") or "").strip():
            return False
        if str(post.get("selftext") or "").strip() and not post.get("url"):
            return False
        if self._risk_text_present(post):
            return False
        image_url, _width, _height = self._post_image(post)
        return bool(image_url)

    def _risk_text_present(self, post):
        pieces = [
            post.get("title"),
            post.get("subreddit"),
            post.get("link_flair_text"),
            post.get("author_flair_text"),
        ]
        text = " ".join(str(piece or "") for piece in pieces).casefold()
        collapsed = re.sub(r"[^a-z0-9]+", " ", text)
        for term in RISK_TERMS:
            normalized = re.sub(r"[^a-z0-9]+", " ", term.casefold()).strip()
            if not normalized:
                continue
            if re.search(rf"(^|\s){re.escape(normalized)}($|\s)", collapsed):
                return True
        return False

    def _post_image(self, post):
        url = str(post.get("url_overridden_by_dest") or post.get("url") or "").strip()
        if self._is_static_image_url(url):
            width, height = self._preview_dimensions(post)
            return html.unescape(url), width, height

        preview = post.get("preview") or {}
        images = preview.get("images") or []
        if images and isinstance(images[0], dict):
            image_info = images[0]
            source = image_info.get("source") or {}
            candidates = list(image_info.get("resolutions") or []) + [source]
            for candidate in reversed(candidates):
                candidate_url = html.unescape(str(candidate.get("url") or "").strip())
                if self._is_static_image_url(candidate_url):
                    width = _safe_int(candidate.get("width"))
                    height = _safe_int(candidate.get("height"))
                    return candidate_url, width, height
        return "", 0, 0

    def _preview_dimensions(self, post):
        preview = post.get("preview") or {}
        images = preview.get("images") or []
        if images and isinstance(images[0], dict):
            source = images[0].get("source") or {}
            return _safe_int(source.get("width")), _safe_int(source.get("height"))
        return 0, 0

    def _is_static_image_url(self, url):
        if not url:
            return False
        parsed = urlparse(html.unescape(str(url)))
        suffix = Path(parsed.path).suffix.casefold()
        if suffix not in IMAGE_SUFFIXES:
            return False
        host = (parsed.netloc or "").casefold()
        return "v.redd.it" not in host and "redgifs.com" not in host

    def _download_to_temp(self, url):
        response = get_http_session().get(
            url,
            timeout=40,
            stream=True,
            headers={"User-Agent": REDDIT_USER_AGENT, "Accept": "image/*,*/*;q=0.8"},
        )
        response.raise_for_status()

        suffix = Path(urlparse(url).path).suffix or ".img"
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = Path(temp_file.name)
        try:
            with temp_file:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        temp_file.write(chunk)
            return tmp_path
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _write_current_day_pool(self, items, settings):
        state = self._read_state()
        state.update({
            "state_version": STATE_VERSION,
            "day_key": self._day_key(),
            "subreddits": self._subreddits(settings),
            "pool_size": self._pool_size(settings),
            "refreshed_at": self._now_utc().isoformat(),
            "queue": [],
        })
        self._write_daily_pool(items)
        self._write_state(state)
        return state

    def _read_daily_pool_payload(self):
        try:
            path = self._daily_pool_path()
            if not path.is_file():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("state_version") != STATE_VERSION:
                return None
            if payload.get("day_key") != self._day_key():
                return None
            return payload
        except Exception as exc:
            logger.warning("Could not read Reddit Rule34 daily pool: %s", exc)
            return None

    def _write_daily_pool(self, items):
        payload = {
            "state_version": STATE_VERSION,
            "day_key": self._day_key(),
            "items": list(items or []),
        }
        path = self._daily_pool_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(path, payload)

    def _load_secret(self, device_config, key):
        value = ""
        if device_config is not None and hasattr(device_config, "load_env_key"):
            try:
                value = device_config.load_env_key(key) or ""
            except Exception as exc:
                logger.warning("Could not read %s from device config: %s", key, exc)
        return str(value or os.getenv(key, "") or "").strip()

    def _subreddits(self, settings):
        raw = settings.get("subreddits", "")
        if isinstance(raw, (list, tuple)):
            parts = [str(value) for value in raw]
        else:
            parts = re.split(r"[\s,;]+", str(raw or ""))

        result = []
        seen = set()
        for part in parts:
            name = self._normalize_subreddit(part)
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(name)
        return result

    def _normalize_subreddit(self, value):
        value = str(value or "").strip()
        if not value:
            return ""
        value = re.sub(r"^https?://(www\.)?reddit\.com/r/", "", value, flags=re.IGNORECASE)
        value = re.sub(r"^/?r/", "", value, flags=re.IGNORECASE)
        value = value.strip().strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", value):
            return ""
        return value

    def _pool_size(self, settings):
        try:
            size = int(settings.get("poolSize") or DEFAULT_POOL_SIZE)
        except (TypeError, ValueError):
            size = DEFAULT_POOL_SIZE
        return max(1, min(MAX_POOL_SIZE, size))

    def _fit_mode(self, settings):
        return str(settings.get("fitMode") or DEFAULT_FIT_MODE).strip().lower()

    def _day_key(self):
        return self._now_utc().astimezone().date().isoformat()

    def _cache_dir(self):
        return self.cache_dir(
            env_var="INKYPI_REDDIT_RULE34_CACHE",
            leaf=".reddit_rule34_hot_cache",
            create=False,
        )


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
