from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

from plugins.base_plugin.base_plugin import BasePlugin
from utils.app_utils import bounded_int, coerce_bool, get_base_ui_font
from utils.http_client import get_http_session
from utils.safe_image import ImageLimits, safe_open_image, safe_open_image_response

logger = logging.getLogger(__name__)

PLUGIN_ID = "telegram_digest"
STATE_VERSION = "telegram-digest-v1"
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_FILE_BASE = "https://api.telegram.org/file/bot{token}/{file_path}"
DEFAULT_CHANNEL_LABEL = "@daily_signal"
DEFAULT_MAX_MESSAGES = 18
DEFAULT_UPDATE_LIMIT = 50
DEFAULT_DIALOG_LIMIT = 30
DEFAULT_MESSAGES_PER_DIALOG = 4
MAX_MESSAGE_CACHE = 30
CHAT_FEED_MAX_ROWS = 14
ACCOUNT_MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 7
DEFAULT_ACCOUNT_MEDIA_DOWNLOAD_LIMIT = 12
MAX_ACCOUNT_MEDIA_DOWNLOAD_LIMIT = MAX_MESSAGE_CACHE
DISPLAY_READ_KEY_LIMIT = 1000
ACCOUNT_SCAN_LIMIT_CAP = 100
DISPLAY_RENDER_SETTING = "_inkypiDisplayRender"
REQUEST_TIMEOUT = (4, 18)
TELEGRAM_MEDIA_IMAGE_LIMITS = ImageLimits(max_bytes=25 * 1024 * 1024)
MAX_MEDIA_PIXELS = 1_200_000
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS

class TelegramDigest(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = dict(settings or {})
        injected_theme = settings.get("_inkypi_theme")
        if not isinstance(injected_theme, dict):
            settings["_inkypi_theme"] = self.resolve_theme(
                settings,
                device_config,
            )
        theme_render_only = self._enabled(
            settings.get("_theme_render_only"),
            default=False,
        )
        dimensions = self.get_dimensions(device_config)
        now = self._now_utc()
        payload = self._payload(settings, device_config, now)
        image = self._render_page(dimensions, payload, settings, now)
        if not theme_render_only:
            self._remember_displayed_messages(payload, settings, now)
        return image

    def _payload(self, settings, device_config, now):
        cache = self._read_state()
        max_messages = bounded_int(settings.get("maxMessages"), DEFAULT_MAX_MESSAGES, 4, MAX_MESSAGE_CACHE)
        if self._enabled(settings.get("_theme_render_only"), default=False):
            cached = self._stale_payload(cache, now, "")
            if cached:
                return cached
            return self._sample_payload(settings, now, "sample")
        access_mode = self._access_mode(settings, device_config)

        if access_mode == "account":
            try:
                payload = self._fetch_account_payload(settings, device_config, cache, now, max_messages)
                self._write_state(payload)
                return payload
            except Exception as exc:
                logger.warning("Telegram Digest account refresh failed: %s", exc)
                stale = self._stale_payload(cache, now, str(exc))
                if stale:
                    return stale
                return self._sample_payload(settings, now, "missing_account", str(exc))

        token = self._bot_token(settings, device_config)
        if token:
            try:
                payload = self._fetch_live_payload(settings, device_config, cache, now, token, max_messages)
                self._attach_display_read_state(payload, cache)
                self._write_state(payload)
                return payload
            except Exception as exc:
                logger.warning("Telegram Digest live refresh failed: %s", exc)
                stale = self._stale_payload(cache, now, str(exc))
                if stale:
                    return stale

        if self._valid_state(cache) and cache.get("messages"):
            cached = self._stale_payload(cache, now, "" if token else "Missing Telegram bot token")
            if cached:
                return cached

        return self._sample_payload(settings, now, "missing_token" if not token else "sample")

    def _stale_payload(self, cache, now, live_error):
        if not self._valid_state(cache):
            return None
        stale = dict(cache)
        stale["status"] = dict(stale.get("status") or {})
        stale["status"]["source_state"] = "cache"
        stale["status"]["generated_at"] = now.isoformat()
        if live_error:
            stale["status"]["live_error"] = live_error
        return stale

    def _fetch_live_payload(self, settings, device_config, cache, now, token, max_messages):
        update_limit = bounded_int(settings.get("updateLimit"), DEFAULT_UPDATE_LIMIT, 5, 100)
        params = {
            "limit": update_limit,
            "timeout": 0,
            "allowed_updates": json.dumps(["message", "channel_post", "edited_channel_post"]),
        }
        last_update_id = self._optional_int(cache.get("last_update_id"))
        if last_update_id is not None and not self._enabled(settings.get("forceRefresh"), default=False):
            params["offset"] = last_update_id + 1

        response = get_http_session().get(
            self._api_url(token, "getUpdates"),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not data.get("ok"):
            description = data.get("description") if isinstance(data, dict) else "invalid response"
            raise RuntimeError(f"Telegram getUpdates failed: {description}")

        updates = data.get("result") or []
        if not isinstance(updates, list):
            raise RuntimeError("Telegram getUpdates returned an invalid result")

        chat_filter = self._chat_filter(settings)
        existing = {
            str(message.get("key")): dict(message)
            for message in cache.get("messages", [])
            if isinstance(message, dict) and message.get("key")
        }
        messages = dict(existing)
        max_update_id = last_update_id
        matched_updates = 0

        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = self._optional_int(update.get("update_id"))
            if update_id is not None:
                max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)
            message = self._message_from_update(update)
            if not message or not self._chat_matches(message.get("chat") or {}, chat_filter):
                continue
            item = self._message_item(message, update_id, token, existing)
            if item:
                messages[item["key"]] = item
                matched_updates += 1

        ordered = sorted(messages.values(), key=lambda item: (int(item.get("date") or 0), int(item.get("message_id") or 0)), reverse=True)
        ordered = ordered[:max_messages]
        return self._build_payload(settings, ordered, now, "live", max_update_id, matched_updates)

    def _fetch_account_payload(self, settings, device_config, cache, now, max_messages):
        config = self._account_config(settings, device_config)
        if not config["api_id"] or not config["api_hash"]:
            raise RuntimeError("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH")
        if not config["session_ready"]:
            raise RuntimeError(f"Telegram account session is not authorized yet: {config['session_file']}")
        return asyncio.run(self._fetch_account_payload_async(settings, cache, now, max_messages, config))

    async def _fetch_account_payload_async(self, settings, cache, now, max_messages, config):
        client_class = self._telethon_client_class()
        dialog_filter = self._dialog_filter(settings)
        dialog_limit = bounded_int(settings.get("dialogLimit"), DEFAULT_DIALOG_LIMIT, 1, 100)
        messages_per_dialog = bounded_int(settings.get("messagesPerDialog"), DEFAULT_MESSAGES_PER_DIALOG, 1, 10)
        media_download_limit = bounded_int(
            settings.get("mediaDownloadLimit"),
            DEFAULT_ACCOUNT_MEDIA_DOWNLOAD_LIMIT,
            1,
            MAX_ACCOUNT_MEDIA_DOWNLOAD_LIMIT,
        )
        unread_only = self._enabled(settings.get("unreadOnly"), default=True)
        include_outgoing = self._enabled(settings.get("includeOutgoing"), default=False)
        display_read_keys = self._display_read_key_set(cache)
        existing = {
            str(message.get("key")): dict(message)
            for message in cache.get("messages", [])
            if isinstance(message, dict) and message.get("key")
        }
        messages = {}
        message_objects = {}
        matched_dialogs = 0
        unread_total = 0

        async with client_class(config["session_path"], config["api_id"], config["api_hash"]) as client:
            authorized = await self._maybe_await(client.is_user_authorized())
            if not authorized:
                raise RuntimeError("Telegram account session is not authorized yet")

            async for dialog in self._aiter(client.iter_dialogs(limit=dialog_limit)):
                if not self._account_dialog_matches(dialog, dialog_filter):
                    continue
                unread_count = max(0, self._optional_int(getattr(dialog, "unread_count", 0)) or 0)
                unread_total += unread_count
                if unread_only and unread_count <= 0:
                    continue
                matched_dialogs += 1
                entity = getattr(dialog, "entity", None)
                if entity is None:
                    continue
                fetch_limit = self._account_scan_limit(dialog, messages_per_dialog, unread_count, unread_only, display_read_keys)
                if fetch_limit <= 0:
                    continue
                kept_for_dialog = 0
                async for message in self._aiter(client.iter_messages(entity, limit=fetch_limit, reverse=False)):
                    if message is None:
                        continue
                    if not include_outgoing and bool(getattr(message, "out", False)):
                        continue
                    key = self._account_message_key(dialog, message)
                    if key in display_read_keys:
                        continue
                    item = await self._account_message_item(client, dialog, message, existing)
                    if item:
                        messages[item["key"]] = item
                        message_objects[item["key"]] = message
                        kept_for_dialog += 1
                        if kept_for_dialog >= messages_per_dialog:
                            break

            ordered = sorted(
                messages.values(),
                key=lambda item: (int(item.get("date") or 0), int(item.get("message_id") or 0)),
                reverse=True,
            )[:max_messages]
            await self._hydrate_account_media(client, ordered, message_objects, existing, media_download_limit)

        ordered = sorted(
            messages.values(),
            key=lambda item: (int(item.get("date") or 0), int(item.get("message_id") or 0)),
            reverse=True,
        )[:max_messages]
        payload = self._build_payload(
            settings,
            ordered,
            now,
            "live",
            None,
            len(ordered),
            auth_mode="account",
            unread_total=unread_total,
            matched_dialogs=matched_dialogs,
        )
        self._attach_display_read_state(payload, cache)
        return payload

    def _account_message_key(self, dialog, message):
        chat = self._dialog_chat(dialog)
        message_id = self._optional_int(getattr(message, "id", None)) or 0
        return f"{chat.get('id', '')}:{message_id}"

    async def _account_message_item(self, client, dialog, message, existing_messages):
        chat = self._dialog_chat(dialog)
        message_id = self._optional_int(getattr(message, "id", None)) or 0
        key = f"{chat.get('id', '')}:{message_id}"
        raw_text = getattr(message, "raw_text", None) or getattr(message, "message", None) or ""
        text = self._clean_text(raw_text)
        title, summary = self._split_title_summary(raw_text)
        media = self._account_media_candidate(message, chat)
        cached_media_path = self._existing_media_path(existing_messages.get(key), media)

        if not title:
            title = self._fallback_title(media)
        if not summary:
            summary = self._fallback_summary(media, {"text": text})

        return {
            "key": key,
            "update_id": None,
            "message_id": message_id,
            "date": self._message_timestamp(getattr(message, "date", None)),
            "chat_id": str(chat.get("id") or ""),
            "chat_title": self._clean_text(chat.get("title") or ""),
            "chat_username": self._clean_text(chat.get("username") or ""),
            "title": title,
            "summary": summary,
            "raw_text": text,
            "media_kind": (media or {}).get("kind") or ("link" if self._first_url(text) else "text"),
            "media_file_id": (media or {}).get("file_id") or "",
            "media_unique_id": (media or {}).get("file_unique_id") or "",
            "media_width": (media or {}).get("width") or 0,
            "media_height": (media or {}).get("height") or 0,
            "duration": (media or {}).get("duration") or 0,
            "media_path": str(cached_media_path or ""),
            "url": self._first_url(text),
            "unread": True,
        }

    async def _hydrate_account_media(self, client, messages, message_objects, existing_messages, media_download_limit):
        downloaded = 0
        for item in self._account_media_download_queue(messages):
            if not isinstance(item, dict) or item.get("media_path"):
                continue
            kind = str(item.get("media_kind") or "").lower()
            key = str(item.get("key") or "")
            message = (message_objects or {}).get(key)
            if message is None:
                continue
            media = {
                "kind": kind,
                "file_id": item.get("media_file_id") or key,
                "file_unique_id": item.get("media_unique_id") or key,
                "width": item.get("media_width") or 0,
                "height": item.get("media_height") or 0,
                "duration": item.get("duration") or 0,
            }
            cached_media_path = self._existing_media_path((existing_messages or {}).get(key), media)
            if cached_media_path:
                item["media_path"] = str(cached_media_path)
                continue
            if downloaded >= media_download_limit:
                return
            try:
                cached_media_path = await asyncio.wait_for(
                    self._download_account_media(client, message, media),
                    timeout=ACCOUNT_MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
                )
                item["media_path"] = str(cached_media_path or "")
            except Exception as exc:
                logger.warning("Could not cache Telegram account media for %s: %s", key, exc)
            finally:
                downloaded += 1

    def _account_media_download_queue(self, messages):
        queue = []
        seen = set()
        prioritized = list(self._visible_message_items(messages)) + list(messages or [])
        for item in prioritized:
            if not isinstance(item, dict) or not item:
                continue
            key = str(item.get("key") or "")
            if key in seen:
                continue
            seen.add(key)
            if str(item.get("media_kind") or "").lower() in {"photo", "video"}:
                queue.append(item)
        return queue

    def _account_config(self, settings, device_config):
        api_id = self._setting_or_env(settings, ("telegramApiId", "apiId"), device_config, "TELEGRAM_API_ID")
        api_hash = self._setting_or_env(settings, ("telegramApiHash", "apiHash"), device_config, "TELEGRAM_API_HASH")
        session_path = self._setting_or_env(settings, ("telegramSessionPath", "sessionPath"), device_config, "TELEGRAM_SESSION_PATH")
        session_path = self._resolve_session_path(session_path)
        session_file = self._session_file_for(session_path)
        if session_file.is_file():
            try:
                session_file.chmod(0o600)
            except OSError as exc:
                logger.warning("Could not restrict Telegram session permissions: %s", exc)
        return {
            "api_id": self._optional_int(api_id),
            "api_hash": str(api_hash or "").strip(),
            "session_path": str(Path(session_path).expanduser()),
            "session_file": str(session_file),
            "session_ready": session_file.is_file(),
        }

    def _resolve_session_path(self, configured_path):
        if configured_path:
            path = Path(str(configured_path)).expanduser()
            if not path.is_absolute() and os.getenv("INKYPI_DATA_DIR", "").strip():
                path = self.data_dir() / path
        else:
            path = self.data_dir(
                leaf="telegram_account",
                legacy_leaf=Path("cache") / "telegram_account",
                create=False,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _access_mode(self, settings, device_config):
        explicit = str(settings.get("accessMode") or settings.get("authMode") or "").strip().casefold()
        if explicit in {"account", "user", "mtproto"}:
            return "account"
        if explicit in {"bot", "botapi", "bot_api"}:
            return "bot"
        config = self._account_config(settings, device_config)
        if config["api_id"] and config["api_hash"] and config["session_ready"]:
            return "account"
        return "bot"

    def _setting_or_env(self, settings, names, device_config, env_key):
        for name in names:
            value = settings.get(name)
            if value not in (None, ""):
                return str(value).strip()
        return self._load_env_key(env_key, device_config)

    def _load_env_key(self, key, device_config):
        if hasattr(device_config, "load_env_key"):
            return str(device_config.load_env_key(key) or "").strip()
        return os.getenv(key, "").strip()

    def _session_file_for(self, session_path):
        path = Path(str(session_path or "")).expanduser()
        if path.suffix == ".session":
            return path
        return Path(str(path) + ".session")

    def _telethon_client_class(self):
        vendor_dir = Path(__file__).resolve().parent / "vendor"
        if vendor_dir.is_dir() and str(vendor_dir) not in sys.path:
            sys.path.insert(0, str(vendor_dir))
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError("Telethon is required for Telegram account mode") from exc
        return TelegramClient

    async def _aiter(self, values):
        if values is None:
            return
        if hasattr(values, "__aiter__"):
            async for value in values:
                yield value
            return
        for value in values:
            yield value

    async def _maybe_await(self, value):
        if hasattr(value, "__await__"):
            return await value
        return value

    def _dialog_filter(self, settings):
        value = str(settings.get("dialogFilter") or settings.get("chatFilter") or settings.get("chatId") or settings.get("channel") or "").strip()
        return value.casefold()

    def _account_dialog_matches(self, dialog, dialog_filter):
        if not dialog_filter:
            return True
        chat = self._dialog_chat(dialog)
        username = str(chat.get("username") or "").lstrip("@")
        candidates = {
            str(chat.get("id") or "").casefold(),
            str(chat.get("title") or "").casefold(),
            username.casefold(),
            ("@" + username).casefold() if username else "",
        }
        return dialog_filter in candidates

    def _dialog_chat(self, dialog):
        entity = getattr(dialog, "entity", None)
        username = getattr(entity, "username", "") if entity is not None else ""
        title = getattr(dialog, "title", "") or getattr(entity, "title", "") or getattr(entity, "first_name", "") or username
        chat_id = getattr(entity, "id", None) if entity is not None else None
        if chat_id is None:
            chat_id = getattr(dialog, "id", "")
        return {"id": chat_id, "title": title, "username": username}

    def _account_media_candidate(self, message, chat):
        message_id = self._optional_int(getattr(message, "id", None)) or 0
        chat_id = chat.get("id") or ""
        video = getattr(message, "video", None) or getattr(message, "gif", None)
        photo = getattr(message, "photo", None)
        document = getattr(message, "document", None)
        kind = "video" if video else "photo" if photo else "file" if document else ""
        if not kind:
            return None
        media_obj = video or photo or document
        unique = f"account:{chat_id}:{message_id}:{kind}"
        return {
            "kind": kind,
            "file_id": unique,
            "file_unique_id": unique,
            "width": getattr(media_obj, "w", None) or getattr(media_obj, "width", 0) or 0,
            "height": getattr(media_obj, "h", None) or getattr(media_obj, "height", 0) or 0,
            "duration": getattr(media_obj, "duration", 0) or 0,
        }

    async def _download_account_media(self, client, message, media):
        media_dir = self._cache_dir() / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        target = media_dir / f"{self._safe_media_id(media)}.jpg"
        if target.is_file():
            return target

        kwargs = {"file": bytes}
        if media.get("kind") in {"video", "file"}:
            kwargs["thumb"] = -1
        data = await self._maybe_await(client.download_media(message, **kwargs))
        if not data:
            return ""
        if isinstance(data, str):
            data_path = Path(data)
            if data_path.is_file():
                image = safe_open_image(data_path, limits=TELEGRAM_MEDIA_IMAGE_LIMITS).convert("RGB")
                image.thumbnail(self._media_thumbnail_size(image.size), RESAMPLE)
                image.save(target, format="JPEG", quality=88)
                return target
            return ""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            return ""
        self._cache_image_bytes(data, target)
        return target

    def _cache_image_bytes(self, data, target):
        image = safe_open_image(data, limits=TELEGRAM_MEDIA_IMAGE_LIMITS).convert("RGB")
        image.thumbnail(self._media_thumbnail_size(image.size), RESAMPLE)
        image.save(target, format="JPEG", quality=88)

    def _message_timestamp(self, value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return int(value.timestamp())
        return self._optional_int(value) or 0
    def _message_from_update(self, update):
        for key in ("channel_post", "message", "edited_channel_post"):
            message = update.get(key)
            if isinstance(message, dict):
                return message
        return None

    def _message_item(self, message, update_id, token, existing_messages):
        chat = message.get("chat") or {}
        chat_id = chat.get("id", "")
        message_id = self._optional_int(message.get("message_id")) or 0
        key = f"{chat_id}:{message_id}"
        raw_text = message.get("text") or message.get("caption") or ""
        text = self._clean_text(raw_text)
        title, summary = self._split_title_summary(raw_text)
        media = self._media_candidate(message)
        cached_media_path = self._existing_media_path(existing_messages.get(key), media)
        if media and not cached_media_path:
            try:
                cached_media_path = self._download_media(media, token)
            except Exception as exc:
                logger.warning("Could not cache Telegram media for %s: %s", key, exc)

        if not title:
            title = self._fallback_title(media)
        if not summary:
            summary = self._fallback_summary(media, message)

        return {
            "key": key,
            "update_id": update_id,
            "message_id": message_id,
            "date": self._optional_int(message.get("date")) or 0,
            "chat_id": str(chat_id),
            "chat_title": self._clean_text(chat.get("title") or chat.get("first_name") or chat.get("username") or ""),
            "chat_username": self._clean_text(chat.get("username") or ""),
            "title": title,
            "summary": summary,
            "raw_text": text,
            "media_kind": (media or {}).get("kind") or ("link" if self._first_url(text) else "text"),
            "media_file_id": (media or {}).get("file_id") or "",
            "media_unique_id": (media or {}).get("file_unique_id") or "",
            "media_width": (media or {}).get("width") or 0,
            "media_height": (media or {}).get("height") or 0,
            "duration": (media or {}).get("duration") or 0,
            "media_path": str(cached_media_path or ""),
            "url": self._first_url(text),
        }

    def _media_candidate(self, message):
        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            photo = self._largest_photo(photos)
            if photo:
                return {"kind": "photo", **photo}

        video = message.get("video")
        if isinstance(video, dict):
            cover = self._largest_photo(video.get("cover") or [])
            if cover:
                return {"kind": "video", "duration": video.get("duration") or 0, **cover}
            thumbnail = video.get("thumbnail")
            if isinstance(thumbnail, dict) and thumbnail.get("file_id"):
                return {
                    "kind": "video",
                    "file_id": thumbnail.get("file_id"),
                    "file_unique_id": thumbnail.get("file_unique_id") or video.get("file_unique_id") or "",
                    "width": thumbnail.get("width") or video.get("width") or 0,
                    "height": thumbnail.get("height") or video.get("height") or 0,
                    "duration": video.get("duration") or 0,
                }

        animation = message.get("animation")
        if isinstance(animation, dict):
            thumbnail = animation.get("thumbnail")
            if isinstance(thumbnail, dict) and thumbnail.get("file_id"):
                return {
                    "kind": "video",
                    "file_id": thumbnail.get("file_id"),
                    "file_unique_id": thumbnail.get("file_unique_id") or animation.get("file_unique_id") or "",
                    "width": thumbnail.get("width") or animation.get("width") or 0,
                    "height": thumbnail.get("height") or animation.get("height") or 0,
                    "duration": animation.get("duration") or 0,
                }

        document = message.get("document")
        if isinstance(document, dict):
            thumbnail = document.get("thumbnail")
            if isinstance(thumbnail, dict) and thumbnail.get("file_id"):
                return {
                    "kind": "file",
                    "file_id": thumbnail.get("file_id"),
                    "file_unique_id": thumbnail.get("file_unique_id") or document.get("file_unique_id") or "",
                    "width": thumbnail.get("width") or 0,
                    "height": thumbnail.get("height") or 0,
                    "duration": 0,
                }

        return None

    def _largest_photo(self, photos):
        candidates = [item for item in photos if isinstance(item, dict) and item.get("file_id")]
        if not candidates:
            return None
        best = max(candidates, key=lambda item: int(item.get("width") or 0) * int(item.get("height") or 0))
        return {
            "file_id": best.get("file_id"),
            "file_unique_id": best.get("file_unique_id") or best.get("file_id"),
            "width": best.get("width") or 0,
            "height": best.get("height") or 0,
        }

    def _download_media(self, media, token):
        file_id = media.get("file_id")
        if not file_id:
            return ""
        media_dir = self._cache_dir() / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        safe_id = self._safe_media_id(media)
        target = media_dir / f"{safe_id}.jpg"
        if target.is_file():
            return target

        file_response = get_http_session().get(
            self._api_url(token, "getFile"),
            params={"file_id": file_id},
            timeout=REQUEST_TIMEOUT,
        )
        file_response.raise_for_status()
        file_payload = file_response.json()
        if not isinstance(file_payload, dict) or not file_payload.get("ok"):
            description = file_payload.get("description") if isinstance(file_payload, dict) else "invalid response"
            raise RuntimeError(f"Telegram getFile failed: {description}")
        file_path = ((file_payload.get("result") or {}).get("file_path") or "").strip()
        if not file_path:
            raise RuntimeError("Telegram getFile did not return file_path")

        response = get_http_session().get(
            self._file_url(token, file_path),
            timeout=REQUEST_TIMEOUT,
            stream=True,
        )
        image = safe_open_image_response(response, limits=TELEGRAM_MEDIA_IMAGE_LIMITS).convert("RGB")
        image.thumbnail(self._media_thumbnail_size(image.size), RESAMPLE)
        image.save(target, format="JPEG", quality=88)
        return target

    def _build_payload(
        self,
        settings,
        messages,
        now,
        source_state,
        last_update_id=None,
        matched_updates=0,
        auth_mode="bot",
        unread_total=0,
        matched_dialogs=0,
    ):
        photo_count = sum(1 for item in messages if item.get("media_kind") == "photo")
        video_count = sum(1 for item in messages if item.get("media_kind") == "video")
        media_messages = [
            item for item in messages
            if str((item or {}).get("media_kind") or "").lower() in {"photo", "video"}
        ]
        cached_media_count = sum(1 for item in media_messages if self._media_path_exists(item))
        missing_media_count = max(0, len(media_messages) - cached_media_count)
        return {
            "schema": STATE_VERSION,
            "channel_label": self._channel_label(settings, messages),
            "last_update_id": last_update_id,
            "messages": list(messages or []),
            "stats": {
                "message_count": len(messages or []),
                "photo_count": photo_count,
                "video_count": video_count,
                "media_cached_count": cached_media_count,
                "media_missing_count": missing_media_count,
                "new_count": matched_updates,
                "unread_count": unread_total,
                "dialog_count": matched_dialogs,
            },
            "status": {
                "source_state": source_state,
                "generated_at": now.isoformat(),
                "bot_api": auth_mode == "bot",
                "account_api": auth_mode == "account",
                "media_cache": "partial" if missing_media_count else "ok",
            },
        }

    def _sample_payload(self, settings, now, source_state, error_message=""):
        messages = [
            {
                "key": "sample:1",
                "message_id": 101,
                "date": int(now.timestamp()),
                "chat_title": "Daily Signal",
                "chat_username": "daily_signal",
                "title": "模型发布更新",
                "summary": "频道中的图片与视频封面优先展示。摘要只保留最有信息量的两三行。失败时使用本地缓存。",
                "media_kind": "video",
                "duration": 138,
                "media_path": "",
                "url": "",
            },
            {
                "key": "sample:2",
                "message_id": 100,
                "date": int(now.timestamp()) - 900,
                "chat_title": "Daily Signal",
                "chat_username": "daily_signal",
                "title": "市场异动观察",
                "summary": "主要指数快速回撤，关注成交量与后续流动性。",
                "media_kind": "photo",
                "media_path": "",
                "url": "",
            },
            {
                "key": "sample:3",
                "message_id": 99,
                "date": int(now.timestamp()) - 1800,
                "chat_title": "Daily Signal",
                "chat_username": "daily_signal",
                "title": "项目部署完成",
                "summary": "生产环境已通过健康检查，截图和日志均已归档。",
                "media_kind": "photo",
                "media_path": "",
                "url": "",
            },
            {
                "key": "sample:4",
                "message_id": 98,
                "date": int(now.timestamp()) - 2700,
                "chat_title": "Daily Signal",
                "chat_username": "daily_signal",
                "title": "会议纪要",
                "summary": "下轮重点是媒体缓存、权限边界和失败兜底。",
                "media_kind": "file",
                "media_path": "",
                "url": "",
            },
            {
                "key": "sample:5",
                "message_id": 97,
                "date": int(now.timestamp()) - 3600,
                "chat_title": "Daily Signal",
                "chat_username": "daily_signal",
                "title": "链接收藏",
                "summary": "Bot API getFile 路径和频道更新说明。",
                "media_kind": "link",
                "media_path": "",
                "url": "https://core.telegram.org/bots/api",
            },
        ]
        auth_mode = "account" if source_state == "missing_account" else "bot"
        payload = self._build_payload(settings, messages, now, source_state, None, 0, auth_mode=auth_mode)
        payload["status"]["bot_api"] = False
        payload["status"]["account_api"] = source_state == "missing_account"
        if source_state == "missing_token":
            payload["status"]["live_error"] = "Missing Telegram bot token"
        elif source_state == "missing_account":
            payload["status"]["live_error"] = error_message or "Missing Telegram account session"
        else:
            payload["status"]["live_error"] = ""
        return payload

    def _render_page(self, dimensions, payload, settings, now):
        width, height = dimensions
        sx = width / 800
        sy = height / 480
        scale = max(0.72, min(sx, sy))
        p = self._palette((settings or {}).get("_inkypi_theme"))
        image = Image.new("RGB", dimensions, p["background"])
        draw = ImageDraw.Draw(image)
        fonts = {
            "title": self._font(int(25 * scale), "bold"),
            "headline": self._font(int(24 * scale), "bold"),
            "row_title": self._font(int(16 * scale), "bold"),
            "body": self._font(int(15 * scale), "normal"),
            "small": self._font(int(12 * scale), "normal"),
            "label": self._font(int(11 * scale), "bold"),
            "chat": self._font(int(12 * scale), "normal"),
            "chat_channel": self._font(int(11 * scale), "bold"),
            "chat_meta": self._font(int(10 * scale), "normal"),
            "chat_badge": self._font(int(9 * scale), "bold"),
            "footer": self._font(int(12 * scale), "bold"),
        }
        self._draw_header(image, draw, payload, width, sx, sy, fonts, p, now)
        messages = payload.get("messages") or []
        lead, secondary_messages = self._prioritize_featured_messages(messages)
        self._draw_featured_post(image, draw, lead, (int(14 * sx), int(44 * sy), int(486 * sx), int(449 * sy)), fonts, p, scale)
        visible_chat_messages = self._draw_secondary_posts(image, draw, secondary_messages, (int(498 * sx), int(44 * sy), int(786 * sx), int(449 * sy)), fonts, p, scale)
        payload["_rendered_visible_keys"] = self._message_keys_for_items(([lead] if isinstance(lead, dict) and lead else []) + list(visible_chat_messages or []))
        self._draw_footer(draw, payload, settings, (int(14 * sx), int(452 * sy), int(786 * sx), int(477 * sy)), fonts, p)
        return image

    def _prioritize_featured_messages(self, messages):
        ordered = list(messages or [])
        if not ordered:
            return {}, []
        for index, item in enumerate(ordered):
            if self._is_photo_message(item) and self._media_path_exists(item):
                return item, ordered[:index] + ordered[index + 1:]
        for index, item in enumerate(ordered):
            if self._is_cached_media_message(item):
                return item, ordered[:index] + ordered[index + 1:]
        return ordered[0], ordered[1:]

    def _is_photo_message(self, item):
        if not isinstance(item, dict):
            return False
        title = str(item.get("title") or "").strip()
        return str(item.get("media_kind") or "").lower() == "photo" or title == "图片更新"

    def _is_cached_media_message(self, item):
        kind = str((item or {}).get("media_kind") or "").lower()
        return kind in {"photo", "video"} and self._media_path_exists(item)

    def _media_path_exists(self, item):
        try:
            return Path(str((item or {}).get("media_path") or "")).is_file()
        except Exception:
            return False

    def _displayed_message_keys(self, messages):
        visible = self._visible_message_items(messages)
        keys = []
        seen = set()
        for item in visible:
            key = str(item.get("key") or "").strip()
            if key and key not in seen:
                keys.append(key)
                seen.add(key)
        return keys

    def _visible_message_items(self, messages):
        lead, secondary = self._prioritize_featured_messages(messages)
        visible = []
        if isinstance(lead, dict) and lead:
            visible.append(lead)
        visible.extend(self._chat_feed_messages(secondary))
        return visible

    def _chat_feed_messages(self, messages, max_rows=CHAT_FEED_MAX_ROWS):
        feed = []
        used_rows = 0
        for item in messages or []:
            if not isinstance(item, dict) or not item:
                continue
            weight = self._chat_item_row_weight(item)
            if feed and used_rows + weight > max_rows:
                break
            if not feed and weight > max_rows:
                weight = max_rows
            feed.append(item)
            used_rows += weight
            if used_rows >= max_rows:
                break
        return feed

    def _chat_item_row_weight(self, item):
        kind = str((item or {}).get("media_kind") or "text").lower()
        if kind in {"photo", "video"}:
            return 8 if self._chat_line_text(item) else 6
        text = self._chat_line_text(item)
        if not text:
            return 1
        return min(5, max(2, (len(text) + 32) // 33))

    def _remember_displayed_messages(self, payload, settings, now):
        if not self._mark_displayed_read_enabled(settings, payload):
            return
        keys = self._rendered_message_keys(payload)
        if not keys:
            return
        try:
            state = self._read_state()
            current = self._display_read_key_list(state)
            seen = set(current)
            for key in keys:
                if key not in seen:
                    current.append(key)
                    seen.add(key)
            current = current[-DISPLAY_READ_KEY_LIMIT:]
            next_state = dict(state) if self._valid_state(state) else dict(payload or {})
            display_read = next_state.get("display_read") if isinstance(next_state.get("display_read"), dict) else {}
            display_read = dict(display_read)
            display_read["keys"] = current
            display_read["updated_at"] = now.isoformat()
            display_read["last_marked_count"] = len(keys)
            next_state["display_read"] = display_read
            self._write_state(next_state)
        except Exception as exc:
            logger.warning("Could not remember Telegram Digest displayed messages: %s", exc)
    def _rendered_message_keys(self, payload):
        rendered = (payload or {}).get("_rendered_visible_keys")
        if isinstance(rendered, list):
            keys = []
            seen = set()
            for value in rendered:
                key = str(value or "").strip()
                if key and key not in seen:
                    keys.append(key)
                    seen.add(key)
            return keys
        return self._displayed_message_keys((payload or {}).get("messages") or [])

    def _message_keys_for_items(self, items):
        keys = []
        seen = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key and key not in seen:
                keys.append(key)
                seen.add(key)
        return keys

    def _mark_displayed_read_enabled(self, settings, payload):
        if not self._enabled((settings or {}).get(DISPLAY_RENDER_SETTING), default=False):
            return False
        if not self._enabled((settings or {}).get("markDisplayedRead"), default=True):
            return False
        status = (payload or {}).get("status") or {}
        if not status.get("account_api"):
            return False
        return status.get("source_state") != "missing_account"

    def _display_read_key_list(self, state):
        display_read = (state or {}).get("display_read")
        if isinstance(display_read, dict):
            raw_keys = display_read.get("keys") or []
        elif isinstance(display_read, list):
            raw_keys = display_read
        else:
            raw_keys = []
        keys = []
        seen = set()
        for value in raw_keys:
            key = str(value or "").strip()
            if key and key not in seen:
                keys.append(key)
                seen.add(key)
        return keys[-DISPLAY_READ_KEY_LIMIT:]

    def _display_read_key_set(self, state):
        return set(self._display_read_key_list(state))

    def _attach_display_read_state(self, payload, cache):
        if not isinstance(payload, dict):
            return payload
        keys = self._display_read_key_list(cache)
        if not keys:
            return payload
        cached_display_read = (cache or {}).get("display_read")
        display_read = dict(cached_display_read) if isinstance(cached_display_read, dict) else {}
        display_read["keys"] = keys
        payload["display_read"] = display_read
        return payload

    def _account_scan_limit(self, dialog, messages_per_dialog, unread_count, unread_only, display_read_keys):
        if unread_only and unread_count <= 0:
            return 0
        base = max(1, int(messages_per_dialog or DEFAULT_MESSAGES_PER_DIALOG))
        chat = self._dialog_chat(dialog)
        chat_id = str(chat.get("id") or "")
        displayed_for_dialog = 0
        if chat_id and display_read_keys:
            prefix = f"{chat_id}:"
            displayed_for_dialog = sum(1 for key in display_read_keys if key.startswith(prefix))
        limit = base + displayed_for_dialog
        if unread_only:
            limit = min(limit, max(0, int(unread_count or 0)))
        return min(max(0, limit), ACCOUNT_SCAN_LIMIT_CAP)

    def _draw_header(self, image, draw, payload, width, sx, sy, fonts, p, now):
        y0, y1 = int(0 * sy), int(39 * sy)
        draw.rectangle((0, y0, width, y1), fill=p["background"])
        draw.line((int(14 * sx), y1, int(786 * sx), y1), fill=p["rule"], width=1)
        title_box = (int(14 * sx), int(2 * sy), int(344 * sx), int(38 * sy))
        if not self._draw_title_asset(image, title_box):
            icon_box = (int(16 * sx), int(9 * sy), int(38 * sx), int(31 * sy))
            draw.rounded_rectangle(icon_box, radius=6, fill=p["cyan"])
            draw.polygon(
                [
                    (int(22 * sx), int(20 * sy)),
                    (int(33 * sx), int(14 * sy)),
                    (int(29 * sx), int(26 * sy)),
                ],
                fill=(255, 255, 255),
            )
            self._draw_text(draw, (int(48 * sx), int(8 * sy)), self._fit_text(draw, payload.get("channel_label") or DEFAULT_CHANNEL_LABEL, fonts["title"], int(245 * sx)), fonts["title"], p["ink"])
        center = self._format_clock((payload.get("status") or {}).get("generated_at"), now)
        self._draw_centered(draw, center, int(400 * sx), int(20 * sy), fonts["footer"], p["muted"])
        status_info = payload.get("status") or {}
        source_state = status_info.get("source_state")
        status = "Bot API"
        if source_state == "cache":
            status = "cache"
        elif source_state == "missing_account":
            status = "account setup"
        elif status_info.get("account_api"):
            status = "Account"
        elif source_state in {"missing_token", "sample"}:
            status = "sample"
        chip = f"{status} / media ok"
        chip_w = self._text_width(draw, chip, fonts["label"]) + int(20 * sx)
        chip_box = (int(786 * sx) - chip_w, int(9 * sy), int(786 * sx), int(31 * sy))
        draw.rounded_rectangle(chip_box, radius=7, fill=p["chip"], outline=p["rule"])
        self._draw_text(draw, (chip_box[0] + int(10 * sx), int(14 * sy)), chip, fonts["label"], p["muted"])

    def _draw_title_asset(self, image, box):
        asset_path = self._title_asset_path()
        if not asset_path.is_file():
            return False
        x0, y0, x1, y1 = [int(value) for value in box]
        target_size = (max(1, x1 - x0), max(1, y1 - y0))
        try:
            with Image.open(asset_path) as source:
                title = source.convert("RGBA")
            if title.size != target_size:
                title = title.resize(target_size, RESAMPLE)
            image.paste(title, (x0, y0), title.getchannel("A"))
            return True
        except Exception as exc:
            logger.debug("Could not draw Telegram Digest title asset %s: %s", asset_path, exc)
            return False

    def _title_asset_path(self):
        return Path(__file__).resolve().parent / "assets" / "telegram_digest_title.png"

    def _draw_featured_post(self, image, draw, item, box, fonts, p, scale):
        x0, y0, x1, y1 = box
        self._panel(draw, box, p)
        pad = int(12 * scale)
        media_only = self._featured_media_without_caption(item)
        media_bottom = y1 - pad if media_only else y0 + int(236 * scale)
        media_box = (x0 + pad, y0 + pad, x1 - pad, media_bottom)
        self._draw_media(image, draw, item, media_box, p, featured=True)
        badge = self._media_badge(item)
        self._badge(draw, (media_box[0] + int(10 * scale), media_box[1] + int(10 * scale)), badge, fonts["label"], p)
        if item.get("duration"):
            duration = self._duration_label(item.get("duration"))
            duration_w = self._text_width(draw, duration, fonts["label"]) + int(18 * scale)
            self._pill(draw, (media_box[2] - duration_w - int(9 * scale), media_box[1] + int(9 * scale), media_box[2] - int(9 * scale), media_box[1] + int(31 * scale)), (25, 29, 34), None, 7)
            self._draw_text(draw, (media_box[2] - duration_w, media_box[1] + int(14 * scale)), duration, fonts["label"], (255, 255, 255))
        if media_only:
            return

        text_x = x0 + pad
        title_y = media_box[3] + int(12 * scale)
        self._draw_text(draw, (text_x, title_y), "今日重点", fonts["label"], p["cyan"])
        title_lines = self._wrap_text(draw, item.get("title") or "Telegram channel digest", fonts["headline"], x1 - x0 - pad * 2, 2)
        line_y = title_y + int(20 * scale)
        for line in title_lines:
            self._draw_text(draw, (text_x, line_y), line, fonts["headline"], p["ink"])
            line_y += int(28 * scale)
        summary_lines = self._wrap_text(draw, item.get("summary") or "", fonts["body"], x1 - x0 - pad * 2, 3)
        body_y = line_y + int(4 * scale)
        for line in summary_lines:
            self._draw_text(draw, (text_x, body_y), line, fonts["body"], p["muted"])
            body_y += int(21 * scale)

        meta = f"{self._relative_time(item.get('date'))}  {self._source_label(item)}"
        self._draw_text(draw, (text_x, y1 - int(28 * scale)), self._fit_text(draw, meta, fonts["small"], x1 - x0 - pad * 2), fonts["small"], p["dim"])

    def _featured_media_without_caption(self, item):
        kind = str((item or {}).get("media_kind") or "").lower()
        if kind not in {"photo", "video"}:
            return False
        if not self._media_path_exists(item):
            return False
        return not self._clean_text((item or {}).get("raw_text") or "")
    def _draw_secondary_posts(self, image, draw, messages, box, fonts, p, scale):
        return self._draw_chat_feed_panel(image, draw, messages, box, fonts, p, scale)
    def _draw_chat_feed_panel(self, image, draw, messages, box, fonts, p, scale):
        x0, y0, x1, y1 = box
        chat_bg = p["chat_background"]
        header_bg = p["chat_header"]
        row_bg = p["chat_row"]
        row_alt = p["chat_row_alt"]
        rule = p["rule"]
        ink = p["ink"]
        dim = p["dim"]
        amber = p["amber"]
        draw.rounded_rectangle(box, radius=7, fill=chat_bg, outline=rule, width=1)
        header_h = int(29 * scale)
        header_box = (x0 + 1, y0 + 1, x1 - 1, y0 + header_h)
        draw.rounded_rectangle(header_box, radius=6, fill=header_bg, outline=None)
        draw.rectangle((x0 + 1, y0 + header_h - int(5 * scale), x1 - 1, y0 + header_h), fill=header_bg)

        top = y0 + header_h + int(4 * scale)
        bottom = y1 - int(5 * scale)
        inner_x0 = x0 + int(6 * scale)
        inner_x1 = x1 - int(6 * scale)
        display = self._chat_feed_messages_for_box(draw, messages, fonts, scale, (inner_x0, top, inner_x1, bottom))
        channel_count = len({self._chat_channel_label(item) for item in display})
        self._draw_text(draw, (x0 + int(10 * scale), y0 + int(8 * scale)), "UNREAD CHAT", fonts["label"], amber)
        meta = f"{len(display)} items / {channel_count} ch"
        self._draw_text(
            draw,
            (x1 - int(10 * scale) - self._text_width(draw, meta, fonts["chat_meta"]), y0 + int(10 * scale)),
            meta,
            fonts["chat_meta"],
            dim,
        )

        if not display:
            self._draw_empty_chat_state(draw, (x0, top, x1, bottom), fonts, scale, ink, dim)
            return []

        cursor_y = top
        row_index = 0
        drawn = []
        for item in display:
            item_box_x0 = x0 + int(6 * scale)
            item_box_x1 = x1 - int(6 * scale)
            item_h = self._chat_item_height(draw, item, fonts, scale, item_box_x1 - item_box_x0)
            if cursor_y + item_h > bottom + 1:
                continue
            fill = row_alt if row_index % 2 else row_bg
            item_box = (item_box_x0, cursor_y, item_box_x1, cursor_y + item_h)
            if self._chat_item_is_media(item):
                self._draw_chat_media_item(image, draw, item, item_box, fonts, p, scale, fill, rule, ink, dim)
            else:
                self._draw_chat_text_item(
                    draw,
                    item,
                    item_box,
                    fonts,
                    scale,
                    fill,
                    rule,
                    ink,
                    dim,
                    palette=p,
                )
            drawn.append(item)
            cursor_y += item_h + int(2 * scale)
            row_index += 1
        return drawn

    def _chat_feed_messages_for_box(self, draw, messages, fonts, scale, content_box):
        x0, y0, x1, y1 = content_box
        remaining = y1 - y0
        gap = int(2 * scale)
        display = []
        for item in messages or []:
            if not isinstance(item, dict) or not item:
                continue
            item_h = self._chat_item_height(draw, item, fonts, scale, x1 - x0)
            extra_gap = gap if display else 0
            if item_h + extra_gap <= remaining:
                display.append(item)
                remaining -= item_h + extra_gap
            # Keep scanning: a later short message can use the leftover space.
        return display

    def _chat_item_is_media(self, item):
        return str((item or {}).get("media_kind") or "").lower() in {"photo", "video"}

    def _chat_item_height(self, draw, item, fonts, scale, panel_width):
        if not self._chat_item_is_media(item):
            lines = self._chat_text_lines(draw, item, fonts, scale, panel_width)
            return int(18 * scale) + len(lines) * int(15 * scale) + int(5 * scale)
        caption = self._chat_line_text(item)
        caption_lines = self._wrap_text(draw, caption, fonts["chat"], int(panel_width - 20 * scale), 3) if caption else []
        return int(22 * scale) + int(86 * scale) + len(caption_lines) * int(15 * scale) + int(6 * scale)

    def _chat_text_lines(self, draw, item, fonts, scale, panel_width):
        text = self._chat_line_text(item)
        if not text:
            return []
        max_width = max(24, int(panel_width - 22 * scale))
        max_lines = max(1, min(5, self._chat_item_row_weight(item)))
        return self._wrap_text(draw, text, fonts["chat"], max_width, max_lines)

    def _draw_chat_text_item(
        self,
        draw,
        item,
        box,
        fonts,
        scale,
        fill,
        rule,
        ink,
        dim,
        palette=None,
    ):
        x0, y0, x1, y1 = box
        draw.rectangle((x0, y0, x1, y1 - 1), fill=fill)
        draw.line((x0, y1 - 1, x1, y1 - 1), fill=rule, width=1)
        color = self._chat_channel_color(item, palette)
        draw.rectangle((x0 + int(2 * scale), y0 + int(4 * scale), x0 + int(5 * scale), y1 - int(4 * scale)), fill=color)
        prefix_y = y0 + int(4 * scale)
        self._draw_chat_prefix(draw, item, x0, prefix_y, fonts, scale, color, dim)
        text_x = x0 + int(12 * scale)
        text_y = y0 + int(18 * scale)
        max_width = x1 - text_x - int(8 * scale)
        for line in self._chat_text_lines(draw, item, fonts, scale, x1 - x0):
            if text_y + int(13 * scale) > y1:
                break
            self._draw_text(draw, (text_x, text_y), self._clip_text(draw, line, fonts["chat"], max_width), fonts["chat"], ink)
            text_y += int(15 * scale)

    def _draw_chat_media_item(self, image, draw, item, box, fonts, p, scale, fill, rule, ink, dim):
        x0, y0, x1, y1 = box
        draw.rectangle((x0, y0, x1, y1 - 1), fill=fill)
        draw.line((x0, y1 - 1, x1, y1 - 1), fill=rule, width=1)
        color = self._chat_channel_color(item, p)
        draw.rectangle((x0 + int(2 * scale), y0 + int(4 * scale), x0 + int(5 * scale), y1 - int(4 * scale)), fill=color)
        cursor = self._draw_chat_prefix(draw, item, x0, y0 + int(5 * scale), fonts, scale, color, dim)
        badge = self._chat_media_label(item)
        if badge:
            badge_width = max(0, x1 - cursor - int(8 * scale))
            badge_text = self._fit_text(draw, badge, fonts["chat_badge"], badge_width)
            if badge_text:
                self._draw_text(
                    draw,
                    (cursor, y0 + int(5 * scale)),
                    badge_text,
                    fonts["chat_badge"],
                    ink,
                )

        media_top = y0 + int(22 * scale)
        media_box = (x0 + int(10 * scale), media_top, x1 - int(10 * scale), media_top + int(86 * scale))
        self._draw_media(image, draw, item, media_box, p, featured=False)
        caption = self._chat_line_text(item)
        if caption:
            caption_y = media_box[3] + int(3 * scale)
            caption_width = media_box[2] - media_box[0]
            for line in self._wrap_text(draw, caption, fonts["chat"], caption_width, 3):
                if caption_y + int(13 * scale) > y1:
                    break
                self._draw_text(draw, (media_box[0], caption_y), self._clip_text(draw, line, fonts["chat"], caption_width), fonts["chat"], ink)
                caption_y += int(15 * scale)

    def _draw_chat_prefix(self, draw, item, x0, y, fonts, scale, color, dim):
        time_text = self._relative_time(item.get("date"))
        time_x = x0 + int(10 * scale)
        time_w = int(34 * scale)
        self._draw_text(draw, (time_x, y + int(1 * scale)), self._fit_text(draw, time_text, fonts["chat_meta"], time_w), fonts["chat_meta"], dim)
        channel_x = time_x + time_w + int(4 * scale)
        channel_max = int(94 * scale)
        channel_text = "[" + self._chat_channel_label(item) + "]"
        channel = self._fit_text(draw, channel_text, fonts["chat_channel"], channel_max)
        self._draw_text(draw, (channel_x, y), channel, fonts["chat_channel"], color)
        return channel_x + min(self._text_width(draw, channel, fonts["chat_channel"]), channel_max) + int(6 * scale)

    def _draw_empty_chat_state(self, draw, box, fonts, scale, ink, dim):
        x0, y0, x1, y1 = box
        center_y = y0 + int((y1 - y0) * 0.42)
        self._draw_centered(draw, "No unread lines", (x0 + x1) // 2, center_y, fonts["row_title"], ink)
        self._draw_centered(draw, "next display refresh will refill", (x0 + x1) // 2, center_y + int(22 * scale), fonts["small"], dim)

    def _chat_channel_label(self, item):
        title = self._clean_text((item or {}).get("chat_title") or "")
        username = self._clean_text((item or {}).get("chat_username") or "").lstrip("@")
        label = title or username or "channel"
        return label[:28]

    def _chat_channel_color(self, item, palette=None):
        colors = (palette or {}).get("channel_colors") or (
            (132, 202, 255),
            (129, 219, 150),
            (255, 197, 103),
            (219, 168, 255),
            (255, 148, 123),
            (130, 224, 215),
            (231, 228, 126),
        )
        label = self._chat_channel_label(item).casefold().encode("utf-8", errors="ignore")
        return colors[hashlib.sha1(label).digest()[0] % len(colors)]

    def _chat_media_label(self, item):
        kind = str((item or {}).get("media_kind") or "text").lower()
        return {
            "photo": "IMG",
            "video": "VID",
            "file": "FILE",
            "link": "LINK",
        }.get(kind, "")

    def _chat_line_text(self, item):
        raw_text = self._clean_text((item or {}).get("raw_text") or "")
        if raw_text:
            return raw_text
        kind = str((item or {}).get("media_kind") or "text").lower()
        if kind in {"photo", "video"}:
            return ""
        return self._clean_text((item or {}).get("title") or "")

    def _draw_post_row(self, image, draw, item, box, fonts, p, scale):
        x0, y0, x1, y1 = box
        draw.rounded_rectangle(box, radius=7, fill=p["panel"], outline=p["rule"], width=1)
        pad = int(9 * scale)
        thumb_box = (x0 + pad, y0 + pad, x0 + int(86 * scale), y1 - pad)
        self._draw_media(image, draw, item, thumb_box, p, featured=False)
        text_x = thumb_box[2] + int(9 * scale)
        title = item.get("title") or "等待频道更新"
        self._draw_text(draw, (text_x, y0 + int(9 * scale)), self._fit_text(draw, title, fonts["row_title"], x1 - text_x - pad), fonts["row_title"], p["ink"])
        for line_index, line in enumerate(self._wrap_text(draw, item.get("summary") or "刷新后显示最新内容", fonts["small"], x1 - text_x - pad, 2)):
            self._draw_text(draw, (text_x, y0 + int(32 * scale) + line_index * int(16 * scale)), line, fonts["small"], p["muted"])
        meta = f"{self._media_badge(item)}  {self._relative_time(item.get('date'))}"
        self._draw_text(draw, (text_x, y1 - int(19 * scale)), self._fit_text(draw, meta, fonts["label"], x1 - text_x - pad), fonts["label"], p["dim"])

    def _draw_media(self, image, draw, item, box, p, featured=False):
        x0, y0, x1, y1 = [int(v) for v in box]
        path = Path(str(item.get("media_path") or ""))
        if self._media_path_exists(item):
            try:
                with Image.open(path) as source:
                    source_image = ImageOps.exif_transpose(source).convert("RGB")
                    media = self._fit_media_image(source_image, (max(1, x1 - x0), max(1, y1 - y0)), featured=featured)
                image.paste(media, (x0, y0))
                draw.rounded_rectangle(box, radius=7, outline=p["rule"], width=1)
                return
            except Exception as exc:
                logger.debug("Could not draw cached Telegram media %s: %s", path, exc)

        kind = item.get("media_kind") or "text"
        draw.rounded_rectangle(box, radius=7, fill=self._placeholder_fill(kind), outline=p["rule"], width=1)
        if kind == "video":
            self._draw_video_placeholder(draw, box, p, featured)
        elif kind == "photo":
            self._draw_photo_placeholder(draw, box, p, featured)
        elif kind == "link":
            self._draw_link_placeholder(draw, box, p, featured)
        elif kind == "file":
            self._draw_file_placeholder(draw, box, p, featured)
        else:
            self._draw_text_placeholder(draw, box, p, featured)
        if kind in {"photo", "video"}:
            label = "NO IMAGE" if kind == "photo" else "NO COVER"
            self._draw_missing_media_badge(draw, box, label, p, featured)

    def _fit_media_image(self, source, target_size, featured=False):
        target_w, target_h = [max(1, int(value)) for value in target_size]
        if not featured:
            return ImageOps.fit(source, (target_w, target_h), method=RESAMPLE)
        crop_box = self._smart_crop_box(source, target_w / max(1, target_h))
        return source.crop(crop_box).resize((target_w, target_h), RESAMPLE)

    def _smart_crop_box(self, source, target_ratio):
        src_w, src_h = source.size
        if src_w <= 0 or src_h <= 0 or target_ratio <= 0:
            return (0, 0, max(1, src_w), max(1, src_h))
        src_ratio = src_w / src_h
        if abs(src_ratio - target_ratio) < 0.03:
            return (0, 0, src_w, src_h)

        if src_ratio > target_ratio:
            crop_w = max(1, min(src_w, int(round(src_h * target_ratio))))
            crop_h = src_h
            max_offset = src_w - crop_w
            axis = "x"
        else:
            crop_w = src_w
            crop_h = max(1, min(src_h, int(round(src_w / target_ratio))))
            max_offset = src_h - crop_h
            axis = "y"
        if max_offset <= 0:
            return (0, 0, src_w, src_h)

        thumb = source.copy()
        thumb.thumbnail((180, 180), RESAMPLE)
        scale_x = thumb.size[0] / src_w
        scale_y = thumb.size[1] / src_h
        gray = thumb.convert("L")
        edges = gray.filter(ImageFilter.FIND_EDGES)
        saturation = thumb.convert("HSV").split()[1]

        offsets = {0, max_offset, max_offset // 2}
        steps = 10
        for step in range(steps + 1):
            offsets.add(int(round(max_offset * step / steps)))

        best_score = None
        best_offset = max_offset // 2
        for offset in sorted(offsets):
            if axis == "x":
                box = (offset, 0, offset + crop_w, crop_h)
                center_distance = abs((offset + crop_w / 2) - src_w / 2) / max(1, src_w / 2)
            else:
                box = (0, offset, crop_w, offset + crop_h)
                center_distance = abs((offset + crop_h / 2) - src_h / 2) / max(1, src_h / 2)
            tbox = self._scale_crop_box(box, scale_x, scale_y, thumb.size)
            edge_stat = ImageStat.Stat(edges.crop(tbox))
            gray_stat = ImageStat.Stat(gray.crop(tbox))
            sat_stat = ImageStat.Stat(saturation.crop(tbox))
            score = edge_stat.mean[0] * 1.35 + gray_stat.stddev[0] * 0.55 + sat_stat.mean[0] * 0.18 - center_distance * 4.0
            if best_score is None or score > best_score:
                best_score = score
                best_offset = offset

        if axis == "x":
            return (best_offset, 0, best_offset + crop_w, crop_h)
        return (0, best_offset, crop_w, best_offset + crop_h)

    def _scale_crop_box(self, box, scale_x, scale_y, thumb_size):
        x0, y0, x1, y1 = box
        tw, th = thumb_size
        return (
            max(0, min(tw - 1, int(math.floor(x0 * scale_x)))),
            max(0, min(th - 1, int(math.floor(y0 * scale_y)))),
            max(1, min(tw, int(math.ceil(x1 * scale_x)))),
            max(1, min(th, int(math.ceil(y1 * scale_y)))),
        )

    def _draw_video_placeholder(self, draw, box, p, featured):
        x0, y0, x1, y1 = box
        draw.rectangle((x0, int(y1 - (y1 - y0) * 0.33), x1, y1), fill=(33, 80, 91))
        center = ((x0 + x1) // 2, (y0 + y1) // 2)
        size = max(10, (y1 - y0) // (5 if featured else 6))
        draw.ellipse((center[0] - size, center[1] - size, center[0] + size, center[1] + size), fill=(245, 251, 250))
        draw.polygon([(center[0] - size // 3, center[1] - size // 2), (center[0] - size // 3, center[1] + size // 2), (center[0] + size // 2, center[1])], fill=p["cyan"])

    def _draw_missing_media_badge(self, draw, box, label, p, featured):
        x0, y0, x1, y1 = box
        if x1 - x0 < 48 or y1 - y0 < 24:
            return
        font = self._font(12 if featured else 9, "bold")
        padding_x = 7 if featured else 5
        padding_y = 4 if featured else 3
        text_w = self._text_width(draw, label, font)
        box_w = min(x1 - x0 - 12, text_w + padding_x * 2)
        if box_w <= padding_x * 2:
            return
        badge = (x0 + 8, y1 - (24 if featured else 18), x0 + 8 + box_w, y1 - 6)
        draw.rounded_rectangle(badge, radius=5, fill=(28, 33, 36), outline=p["rule"])
        self._draw_text(draw, (badge[0] + padding_x, badge[1] + padding_y), label, font, (247, 244, 232))

    def _draw_photo_placeholder(self, draw, box, p, featured):
        x0, y0, x1, y1 = box
        draw.rectangle((x0, y0, x1, y1), fill=(218, 229, 224))
        draw.polygon([(x0, y1), (x0 + (x1 - x0) // 3, y0 + (y1 - y0) // 2), (x0 + (x1 - x0) // 2, y1)], fill=(106, 144, 130))
        draw.polygon([(x0 + (x1 - x0) // 3, y1), (x0 + (x1 - x0) * 2 // 3, y0 + (y1 - y0) // 3), (x1, y1)], fill=(66, 122, 134))
        draw.ellipse((x1 - 34, y0 + 12, x1 - 18, y0 + 28), fill=(246, 188, 87))

    def _draw_link_placeholder(self, draw, box, p, featured):
        x0, y0, x1, y1 = box
        draw.rectangle((x0, y0, x1, y1), fill=(239, 236, 226))
        for offset in (12, 26, 40):
            y = y0 + offset if featured else y0 + max(8, offset // 2)
            if y < y1 - 8:
                draw.line((x0 + 10, y, x1 - 10, y), fill=(159, 168, 170), width=2)
        draw.rounded_rectangle((x0 + 10, y1 - 25, x0 + 54, y1 - 10), radius=4, fill=p["cyan"])

    def _draw_file_placeholder(self, draw, box, p, featured):
        x0, y0, x1, y1 = box
        draw.rectangle((x0, y0, x1, y1), fill=(241, 236, 222))
        page = (x0 + 16, y0 + 10, x1 - 16, y1 - 10)
        draw.rounded_rectangle(page, radius=4, fill=(255, 253, 246), outline=(193, 183, 164))
        for i in range(4):
            y = page[1] + 14 + i * 10
            if y < page[3] - 6:
                draw.line((page[0] + 8, y, page[2] - 8, y), fill=(151, 151, 142), width=1)

    def _draw_text_placeholder(self, draw, box, p, featured):
        x0, y0, x1, y1 = box
        draw.rectangle((x0, y0, x1, y1), fill=(242, 239, 230))
        draw.line((x0 + 12, y0 + 18, x1 - 12, y0 + 18), fill=p["cyan"], width=3)
        draw.line((x0 + 12, y0 + 34, x1 - 28, y0 + 34), fill=(154, 160, 160), width=2)

    def _draw_footer(self, draw, payload, settings, box, fonts, p):
        x0, y0, x1, y1 = box
        draw.rounded_rectangle(box, radius=7, fill=p["chip"], outline=p["rule"], width=1)
        stats = payload.get("stats") or {}
        status_info = payload.get("status") or {}
        parts = [f"{int(stats.get('message_count') or 0)} 条消息"]
        if status_info.get("account_api"):
            parts.append(f"{int(stats.get('unread_count') or 0)} 未读")
        if self._enabled(settings.get("showMediaStats"), default=True):
            parts.append(f"{int(stats.get('photo_count') or 0)} 张图片")
            parts.append(f"{int(stats.get('video_count') or 0)} 个视频封面")
        parts.append(settings.get("footerRefreshLabel") or "下一次刷新 轮到展示时")
        text = "   ".join(parts)
        self._draw_text(draw, (x0 + 12, y0 + 6), self._fit_text(draw, text, fonts["footer"], x1 - x0 - 24), fonts["footer"], p["muted"])
    def _bot_token(self, settings, device_config):
        return self._setting_or_env(settings, ("botToken", "bot_token"), device_config, "TELEGRAM_BOT_TOKEN")

    def _channel_label(self, settings, messages=None):
        label = str(settings.get("channelLabel") or settings.get("dialogFilter") or settings.get("chatFilter") or "").strip()
        if label:
            return label
        for message in messages or []:
            username = str(message.get("chat_username") or "").strip()
            if username:
                return "@" + username.lstrip("@")
            title = str(message.get("chat_title") or "").strip()
            if title:
                return title
        return DEFAULT_CHANNEL_LABEL

    def _chat_filter(self, settings):
        value = str(settings.get("chatFilter") or settings.get("chatId") or settings.get("channel") or "").strip()
        return value.casefold()

    def _chat_matches(self, chat, chat_filter):
        if not chat_filter:
            return True
        candidates = {
            str(chat.get("id") or "").casefold(),
            str(chat.get("title") or "").casefold(),
            str(chat.get("username") or "").casefold(),
            ("@" + str(chat.get("username") or "").lstrip("@")).casefold(),
        }
        return chat_filter in candidates

    def _split_title_summary(self, text):
        raw_text = str(text or "")
        text = self._clean_text(raw_text)
        if not text:
            return "", ""
        parts = [self._clean_text(part) for part in re.split(r"[\r\n]+", raw_text) if self._clean_text(part)]
        title = parts[0] if parts else text
        if len(title) > 60:
            title = title[:60].rstrip()
        rest = " ".join(parts[1:]).strip()
        if not rest and len(text) > len(title):
            rest = text[len(title):].strip()
        if not rest:
            rest = title
        return title, rest[:220].rstrip()

    def _fallback_title(self, media):
        kind = (media or {}).get("kind")
        if kind == "video":
            return "视频无配字"
        if kind == "photo":
            return "图片无配字"
        if kind == "file":
            return "文件预览"
        return "频道更新"

    def _fallback_summary(self, media, message):
        kind = (media or {}).get("kind")
        if kind == "video":
            return "这条视频消息没有配字，仅展示视频封面。"
        if kind == "photo":
            return "这条图片消息没有配字，仅展示图片内容。"
        if kind == "file":
            return "这条 Telegram 消息包含文件缩略图。"
        return self._first_url(self._clean_text(message.get("text") or "")) or "无正文内容"

    def _existing_media_path(self, existing, media):
        if not existing or not media:
            return ""
        if existing.get("media_unique_id") and existing.get("media_unique_id") == media.get("file_unique_id"):
            path = Path(str(existing.get("media_path") or ""))
            if path.is_file():
                return path
        return ""

    def _safe_media_id(self, media):
        unique = str(media.get("file_unique_id") or "").strip()
        if unique:
            return re.sub(r"[^A-Za-z0-9_.-]+", "_", unique)[:80]
        digest = hashlib.sha1(str(media.get("file_id") or "").encode("utf-8")).hexdigest()
        return digest[:24]

    def _media_thumbnail_size(self, size):
        width, height = max(1, int(size[0])), max(1, int(size[1]))
        pixels = width * height
        if pixels <= MAX_MEDIA_PIXELS:
            return width, height
        scale = (MAX_MEDIA_PIXELS / pixels) ** 0.5
        return max(1, int(width * scale)), max(1, int(height * scale))

    def _cache_dir(self):
        return self.cache_dir(env_var="TELEGRAM_DIGEST_CACHE_DIR", leaf="cache", strip=True)

    def _state_path(self):
        return self._cache_dir() / "state.json"

    def _read_state(self):
        path = self._state_path()
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("Could not read Telegram Digest state %s: %s", path, exc)
        return {}

    def _write_state(self, payload):
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=True, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            path.write_text(text, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _valid_state(self, state):
        return isinstance(state, dict) and state.get("schema") == STATE_VERSION

    def _api_url(self, token, method):
        return TELEGRAM_API_BASE.format(token=token, method=method)

    def _file_url(self, token, file_path):
        return TELEGRAM_FILE_BASE.format(token=token, file_path=file_path.lstrip("/"))

    @staticmethod
    def _theme_role(theme_context, name, fallback):
        palette = (
            theme_context.get("palette")
            if isinstance(theme_context, dict)
            else None
        )
        value = palette.get(name) if isinstance(palette, dict) else None
        try:
            result = tuple(int(channel) for channel in value)
        except (TypeError, ValueError):
            return fallback
        return result if len(result) == 3 else fallback

    @staticmethod
    def _blend(foreground, background, amount):
        amount = max(0.0, min(1.0, float(amount)))
        return tuple(
            int(background[index] + (foreground[index] - background[index]) * amount)
            for index in range(3)
        )

    @staticmethod
    def _contrast_ratio(first, second):
        def relative_luminance(color):
            channels = []
            for value in color:
                normalized = value / 255
                channels.append(
                    normalized / 12.92
                    if normalized <= 0.04045
                    else ((normalized + 0.055) / 1.055) ** 2.4
                )
            return (
                0.2126 * channels[0]
                + 0.7152 * channels[1]
                + 0.0722 * channels[2]
            )

        lighter, darker = sorted(
            (relative_luminance(first), relative_luminance(second)),
            reverse=True,
        )
        return (lighter + 0.05) / (darker + 0.05)

    def _day_dim(self, initial, ink, surfaces):
        for step in range(21):
            candidate = self._blend(ink, initial, step / 20)
            if all(
                self._contrast_ratio(candidate, surface) >= 4.5
                for surface in surfaces
            ):
                return candidate
        return ink

    def _palette(self, theme_context=None):
        mode = str((theme_context or {}).get("mode") or "day").lower()
        night = mode == "night"
        background = self._theme_role(
            theme_context,
            "background",
            (246, 242, 232),
        )
        panel = self._theme_role(theme_context, "panel", (255, 252, 242))
        ink = self._theme_role(theme_context, "ink", (29, 33, 38))
        muted = self._theme_role(theme_context, "muted", (78, 85, 91))
        rule = self._theme_role(theme_context, "rule", (206, 198, 181))
        accent = self._theme_role(theme_context, "accent", (0, 135, 170))
        dim = self._blend(muted, background, 0.72)
        if not night:
            dim = self._day_dim(dim, ink, (background, panel))
        return {
            "background": background,
            "panel": panel,
            "chip": self._blend(accent, background, 0.08),
            "rule": rule,
            "ink": ink,
            "muted": muted,
            "dim": dim,
            "cyan": accent,
            "amber": (236, 177, 82) if night else (153, 93, 23),
            "chat_background": self._blend(accent, background, 0.08),
            "chat_header": self._blend(accent, background, 0.14),
            "chat_row": self._blend(accent, background, 0.05),
            "chat_row_alt": self._blend(accent, background, 0.1),
            "channel_colors": (
                (
                    (132, 202, 255),
                    (129, 219, 150),
                    (255, 197, 103),
                    (219, 168, 255),
                    (255, 148, 123),
                    (130, 224, 215),
                    (231, 228, 126),
                )
                if night
                else (
                    (17, 82, 130),
                    (31, 111, 70),
                    (139, 86, 18),
                    (100, 62, 137),
                    (147, 55, 42),
                    (17, 112, 107),
                    (116, 105, 21),
                )
            ),
        }

    def _panel(self, draw, box, p):
        draw.rounded_rectangle(box, radius=8, fill=p["panel"], outline=p["rule"], width=1)

    def _pill(self, draw, box, fill, outline, radius):
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)

    def _badge(self, draw, xy, text, font, p):
        x, y = xy
        w = self._text_width(draw, text, font) + 18
        box = (x, y, x + w, y + 22)
        draw.rounded_rectangle(box, radius=7, fill=(23, 29, 34), outline=None)
        self._draw_text(draw, (x + 9, y + 5), text, font, (255, 255, 255))

    def _placeholder_fill(self, kind):
        if kind == "video":
            return (103, 181, 194)
        if kind == "photo":
            return (220, 232, 226)
        if kind == "file":
            return (238, 230, 211)
        if kind == "link":
            return (235, 235, 226)
        return (242, 239, 230)

    def _media_badge(self, item):
        kind = str((item or {}).get("media_kind") or "text").lower()
        return {
            "video": "VIDEO",
            "photo": "PHOTO",
            "file": "FILE",
            "link": "LINK",
        }.get(kind, "TEXT")

    def _source_label(self, item):
        username = str(item.get("chat_username") or "").strip()
        if username:
            return "@" + username.lstrip("@")
        return str(item.get("chat_title") or DEFAULT_CHANNEL_LABEL).strip() or DEFAULT_CHANNEL_LABEL

    def _format_clock(self, iso_value, now):
        dt = self._parse_datetime(iso_value) or now
        return dt.astimezone().strftime("%H:%M")

    def _relative_time(self, timestamp):
        value = self._optional_int(timestamp)
        if not value:
            return "--"
        delta = max(0, int(self._now_utc().timestamp()) - value)
        minutes = delta // 60
        if minutes < 1:
            return "now"
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h"
        return f"{hours // 24}d"

    def _duration_label(self, seconds):
        value = max(0, self._optional_int(seconds) or 0)
        return f"{value // 60}:{value % 60:02d}"

    def _first_url(self, text):
        match = re.search(r"https?://[^\s]+", str(text or ""))
        if not match:
            return ""
        return match.group(0).rstrip(".,;)")

    def _clean_text(self, value):
        text = html.unescape(str(value or ""))
        text = re.sub(r"\s+", " ", text.replace("\u200b", " ")).strip()
        cleaned = []
        for ch in text:
            if ord(ch) > 0xFFFF:
                if cleaned and cleaned[-1] != " ":
                    cleaned.append(" ")
                continue
            cleaned.append(ch)
        return re.sub(r"\s+", " ", "".join(cleaned)).strip()

    def _wrap_text(self, draw, text, font, max_width, max_lines):
        max_width = int(max_width or 0)
        max_lines = int(max_lines or 0)
        if max_width <= 0 or max_lines <= 0:
            return []
        text = self._clean_text(text)
        if not text:
            return []

        lines = []
        current = ""
        words = [word for word in text.split(" ") if word]

        def finish(truncated):
            if truncated and lines:
                lines[-1] = self._ellipsize_text(draw, lines[-1], font, max_width)
            return lines[:max_lines]

        for word_index, word in enumerate(words):
            pending = word
            while pending:
                if current:
                    candidate = current + " " + pending
                    if self._text_width(draw, candidate, font) <= max_width:
                        current = candidate
                        pending = ""
                        break
                    lines.append(current)
                    current = ""
                    if len(lines) >= max_lines:
                        return finish(True)
                    continue

                if self._text_width(draw, pending, font) <= max_width:
                    current = pending
                    pending = ""
                    break

                chunk = ""
                consumed = 0
                for index, char in enumerate(pending):
                    candidate = chunk + char
                    if self._text_width(draw, candidate, font) <= max_width:
                        chunk = candidate
                        consumed = index + 1
                    else:
                        break
                if not chunk:
                    chunk = self._clip_text(draw, pending[:1], font, max_width)
                    consumed = 1
                lines.append(chunk)
                pending = pending[consumed:]
                if len(lines) >= max_lines:
                    return finish(bool(pending) or word_index < len(words) - 1)

        if current and len(lines) < max_lines:
            lines.append(current)
        return lines[:max_lines]

    def _ellipsize_text(self, draw, text, font, max_width):
        text = str(text or "").rstrip()
        max_width = int(max_width or 0)
        if max_width <= 0:
            return ""
        suffix = "..."
        if self._text_width(draw, suffix, font) > max_width:
            suffix = "."
            if self._text_width(draw, suffix, font) > max_width:
                return ""
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return text + suffix if text else suffix

    def _fit_text(self, draw, text, font, max_width):
        text = str(text or "")
        max_width = int(max_width or 0)
        if max_width <= 0:
            return ""
        if self._text_width(draw, text, font) <= max_width:
            return text
        suffix = "..."
        if self._text_width(draw, suffix, font) > max_width:
            suffix = "."
            if self._text_width(draw, suffix, font) > max_width:
                return ""
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return text + suffix if text else suffix

    def _clip_text(self, draw, text, font, max_width):
        text = str(text or "")
        max_width = int(max_width or 0)
        if max_width <= 0:
            return ""
        while text and self._text_width(draw, text, font) > max_width:
            text = text[:-1]
        return text

    def _draw_text(self, draw, xy, text, font, fill):
        draw.text(xy, str(text or ""), font=font, fill=fill)

    def _draw_centered(self, draw, text, x, y, font, fill):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        draw.text((x - (bbox[2] - bbox[0]) / 2, y - (bbox[3] - bbox[1]) / 2), str(text), font=font, fill=fill)

    def _text_width(self, draw, text, font):
        try:
            return draw.textlength(str(text or ""), font=font)
        except Exception:
            bbox = draw.textbbox((0, 0), str(text or ""), font=font)
            return bbox[2] - bbox[0]

    def _font(self, size, weight="normal"):
        return get_base_ui_font(int(size), bold=weight == "bold")

    def _enabled(self, value, default=False):
        return coerce_bool(value, default=default, truthy=("1", "true", "yes", "on"))

    def _optional_int(self, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _parse_datetime(self, value):
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None

    def _now_utc(self):
        return datetime.now(timezone.utc)
