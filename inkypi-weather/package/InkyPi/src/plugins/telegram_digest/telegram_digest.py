from __future__ import annotations
from utils.resource_cache import record_resource_event, measured_image_response

import asyncio
import hashlib
import html
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.refresh_on_display_presentation import RefreshOnDisplayPresentationMixin
from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from plugins.telegram_digest import account_worker
from plugins.telegram_digest.briefing_page import DAY_TAG_COLOURS, NIGHT_TAG_COLOURS, BriefingPageMixin
from plugins.telegram_digest.curation import VISIBLE_COUNT, Curator, split_lead
from runtime.long_task_executor import current_task_context
from runtime.refresh_contracts import TaskCancelled
from utils.app_utils import bounded_int, coerce_bool, get_base_ui_font
from utils.http_client import get_http_session, provider_io_lease
from utils.page_canvas import NO_LINE_START, re_tokens
from utils.safe_image import ImageLimits, safe_open_image

logger = logging.getLogger(__name__)

PLUGIN_ID = "telegram_digest"
STATE_VERSION = "telegram-digest-v1"
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_FILE_BASE = "https://api.telegram.org/file/bot{token}/{file_path}"
TELEGRAM_PROVIDER_ORIGIN = "https://api.telegram.org"
DEFAULT_CHANNEL_LABEL = "@daily_signal"
DEFAULT_MAX_MESSAGES = 18
DEFAULT_UPDATE_LIMIT = 50
DEFAULT_DIALOG_LIMIT = 30
DEFAULT_MESSAGES_PER_DIALOG = 4
MAX_MESSAGE_CACHE = 30
ACCOUNT_MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 7
# A time-limited account fetch stops opening more chats once less than this is left, so
# curation (with an AI call) and the media still fit.
ACCOUNT_SCAN_RESERVE_SECONDS = 70
# Every visible picture: the lead and the list thumbnails. Thumbnails download a
# small Telegram photo size, so seven cost less than four full photos did.
DEFAULT_ACCOUNT_MEDIA_DOWNLOAD_LIMIT = 7
MAX_ACCOUNT_MEDIA_DOWNLOAD_LIMIT = 7
ACCOUNT_MEDIA_PREFETCH_LIMIT = 1
LEAD_PHOTO_MIN_SIDE = 800
THUMB_PHOTO_MIN_SIDE = 320
DISPLAY_READ_KEY_LIMIT = 1000
ACCOUNT_SCAN_LIMIT_CAP = 100
DISPLAY_RENDER_SETTING = "_inkypiDisplayRender"
PENDING_PRESENTATION_DISPLAY_STATE_KEY = "pending_presentation_display"
RENDERED_VISIBLE_KEYS_IMAGE_INFO_KEY = "inkypi_rendered_visible_message_keys"
REQUEST_TIMEOUT = (4, 18)
TELEGRAM_MEDIA_IMAGE_LIMITS = ImageLimits(max_bytes=25 * 1024 * 1024)
MAX_MEDIA_PIXELS = 1_200_000
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS


def _json_safe_settings(settings):
    """Drop runtime-injected objects (trusted identities); the account fetch never reads them."""
    safe = {}
    for key, value in (settings or {}).items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        safe[key] = value
    return safe


class TelegramDigest(BriefingPageMixin, RefreshOnDisplayPresentationMixin, BasePlugin):
    # Telethon stays out of the long-lived service; see account_worker.
    ACCOUNT_FETCH_IN_CHILD_PROCESS = True

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
        image.info[RENDERED_VISIBLE_KEYS_IMAGE_INFO_KEY] = tuple(
            self._rendered_message_keys(payload)
        )
        source_state = str((payload.get("status") or {}).get("source_state") or "")
        provenance = {
            "live": SourceProvenance.LIVE,
            "cache": SourceProvenance.STALE_CACHE,
        }.get(source_state, SourceProvenance.LOCAL_FALLBACK)
        attach_source_provenance(image, provenance)
        if provenance is SourceProvenance.LOCAL_FALLBACK:
            image.info["inkypi_skip_cache"] = True
        if not theme_render_only:
            self._remember_displayed_messages(payload, settings, now)
        return image

    def prepare_presentation(
        self,
        settings,
        device_config,
        *,
        request,
        resolved_theme_context,
    ):
        preparation = super().prepare_presentation(
            settings,
            device_config,
            request=request,
            resolved_theme_context=resolved_theme_context,
        )
        if not preparation.changed:
            return preparation
        state = self._read_state()
        if not self._valid_state(state):
            return preparation
        status = state.get("status") if isinstance(state.get("status"), dict) else {}
        rendered_key_values = preparation.image.info.get(
            RENDERED_VISIBLE_KEYS_IMAGE_INFO_KEY,
        )
        if not isinstance(rendered_key_values, (list, tuple)):
            raise RuntimeError("Telegram prepared render did not report visible messages")
        rendered_keys = self._rendered_message_keys(
            {
                "_rendered_visible_keys": list(rendered_key_values)
            }
        )
        state[PENDING_PRESENTATION_DISPLAY_STATE_KEY] = {
            "request_id": request.request_id,
            "origin_display_commit_id": request.origin_display_commit_id,
            "keys": rendered_keys,
            "source_state": str(status.get("source_state") or ""),
            "account_api": bool(status.get("account_api")),
        }
        self._write_state(state)
        return preparation

    def reconcile_presentation_receipt(self, settings, receipt):
        """Mark only physically committed account messages as plugin-read."""
        if receipt is None:
            return None
        try:
            state = self._read_state()
            if not self._valid_state(state):
                return None
            pending = state.get(PENDING_PRESENTATION_DISPLAY_STATE_KEY)
            if not isinstance(pending, dict):
                return None
            if str(pending.get("request_id") or "") != receipt.request_id:
                return None
            # A same-request theme reprepare replays the origin receipt. It
            # does not prove these pending keys reached the display. Legacy
            # pending state without an origin must also fail closed.
            origin_display_commit_id = str(
                pending.get("origin_display_commit_id") or ""
            )
            if (
                not origin_display_commit_id
                or origin_display_commit_id == receipt.display_commit_id
            ):
                return None
            display_read = state.get("display_read")
            display_read = dict(display_read) if isinstance(display_read, dict) else {}
            if display_read.get("last_display_commit_id") == receipt.display_commit_id:
                return None
            displayed_settings = dict(settings or {})
            displayed_settings[DISPLAY_RENDER_SETTING] = True
            committed_at = datetime.fromisoformat(
                str(receipt.committed_at).replace("Z", "+00:00")
            )
            committed_payload = {
                "_rendered_visible_keys": list(pending.get("keys") or []),
                "status": {
                    "source_state": str(pending.get("source_state") or ""),
                    "account_api": bool(pending.get("account_api")),
                },
            }
            self._remember_displayed_messages(
                committed_payload,
                displayed_settings,
                committed_at,
            )
            next_state = self._read_state()
            if not self._valid_state(next_state):
                next_state = dict(state)
            next_display_read = next_state.get("display_read")
            next_display_read = (
                dict(next_display_read) if isinstance(next_display_read, dict) else {}
            )
            next_display_read["last_display_commit_id"] = receipt.display_commit_id
            next_display_read["last_receipt_request_id"] = receipt.request_id
            next_state["display_read"] = next_display_read
            current_pending = next_state.get(PENDING_PRESENTATION_DISPLAY_STATE_KEY)
            if (
                isinstance(current_pending, dict)
                and str(current_pending.get("request_id") or "") == receipt.request_id
            ):
                next_state.pop(PENDING_PRESENTATION_DISPLAY_STATE_KEY, None)
            self._write_state(next_state)
        except Exception as exc:
            logger.warning("Could not reconcile Telegram display receipt: %s", exc)
        return None

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
        messages = self._page_messages(stale.get("messages"))
        stale["messages"] = messages
        stats = dict(stale.get("stats") or {})
        photo_count = sum(1 for item in messages if item.get("media_kind") == "photo")
        video_count = sum(1 for item in messages if item.get("media_kind") == "video")
        cached_media_count = sum(1 for item in messages if self._media_path_exists(item))
        stats.update(
            {
                "message_count": len(messages),
                "photo_count": photo_count,
                "video_count": video_count,
                "media_cached_count": cached_media_count,
                "media_missing_count": max(0, len(messages) - cached_media_count),
            }
        )
        stale["stats"] = stats
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
        if last_update_id is not None:
            params["offset"] = last_update_id + 1

        with provider_io_lease(
            TELEGRAM_PROVIDER_ORIGIN,
            context=current_task_context(),
            timeout=REQUEST_TIMEOUT,
        ):
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

        candidates = sorted(messages.values(), key=lambda item: (int(item.get("date") or 0), int(item.get("message_id") or 0)), reverse=True)
        curator = Curator(settings, cache.get("curation"), now, api_key=self._openai_key(settings, device_config))
        ordered = curator.curate(candidates)[:max_messages]
        payload = self._build_payload(settings, ordered, now, "live", max_update_id, matched_updates)
        payload["stats"].update(curator.stats(), scanned_count=len(candidates))
        payload["curation"] = curator.state(candidates)
        payload["status"]["ai_error"] = curator.ai_error
        return payload

    def _fetch_account_payload(self, settings, device_config, cache, now, max_messages):
        config = self._account_config(settings, device_config)
        if not config["api_id"] or not config["api_hash"]:
            raise RuntimeError("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH")
        if not config["session_ready"]:
            raise RuntimeError(f"Telegram account session is not authorized yet: {config['session_file']}")
        errors = []
        for session_path in config.get("session_candidates") or [config["session_path"]]:
            session_file = self._session_file_for(session_path)
            if not session_file.is_file():
                continue
            candidate = dict(config)
            candidate["session_path"] = str(session_path)
            candidate["session_file"] = str(session_file)
            try:
                with provider_io_lease(
                    TELEGRAM_PROVIDER_ORIGIN,
                    context=current_task_context(),
                    timeout=REQUEST_TIMEOUT,
                ):
                    return self._run_account_fetch(settings, cache, now, max_messages, candidate)
            except TaskCancelled:
                raise
            except Exception as exc:
                errors.append(str(exc))
        detail = errors[-1] if errors else "no usable session file"
        raise RuntimeError(f"Telegram account session is not authorized yet: {detail}")

    def _run_account_fetch(self, settings, cache, now, max_messages, config):
        if not self.ACCOUNT_FETCH_IN_CHILD_PROCESS:
            return asyncio.run(self._fetch_account_payload_async(settings, cache, now, max_messages, config))
        request = {
            "settings": _json_safe_settings(settings),
            "cache": cache,
            "now": now.isoformat(),
            "max_messages": max_messages,
            "config": {key: config.get(key) for key in ("session_path", "api_id", "api_hash", "openai_key")},
            "cache_dir": str(self._cache_dir()),
        }
        return account_worker.run_account_fetch_in_child(request, context=current_task_context())

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
        include_text = self._enabled(settings.get("includeText"), default=True)
        display_read_keys = self._display_read_key_set(cache)
        display_cursors = self._display_cursors(display_read_keys)
        curator = Curator(settings, cache.get("curation"), now, api_key=config.get("openai_key"))
        budget = config.get("budget_seconds")
        deadline = time.monotonic() + float(budget) if budget else None
        curator.deadline = deadline

        def time_left():
            return float("inf") if deadline is None else deadline - time.monotonic()

        existing = {
            str(message.get("key")): dict(message)
            for message in cache.get("messages", [])
            if isinstance(message, dict) and message.get("key")
        }
        messages = {}
        message_objects = {}
        matched_dialogs = 0
        unread_total = 0
        scanned = 0

        logger.info("Telegram Digest connecting to Telegram")
        async with client_class(config["session_path"], config["api_id"], config["api_hash"]) as client:
            authorized = await self._maybe_await(client.is_user_authorized())
            if not authorized:
                raise RuntimeError("Telegram account session is not authorized yet")
            logger.info("Telegram Digest signed in; reading chats")

            scan_started = time.monotonic()
            async for dialog in self._aiter(client.iter_dialogs(limit=dialog_limit)):
                if time_left() < ACCOUNT_SCAN_RESERVE_SECONDS:
                    # Chats come newest first; the rest wait for the next refresh.
                    logger.info("Telegram Digest stopped scanning after %d chats to stay within time.", matched_dialogs)
                    break
                if not self._account_dialog_matches(dialog, dialog_filter):
                    continue
                unread_count = max(0, self._optional_int(getattr(dialog, "unread_count", 0)) or 0)
                if unread_only and unread_count <= 0:
                    continue
                unread_total += unread_count
                matched_dialogs += 1
                if matched_dialogs % 10 == 0:
                    logger.info("Telegram Digest reading chat %d (%.0fs)", matched_dialogs,
                                time.monotonic() - scan_started)
                entity = getattr(dialog, "entity", None)
                if entity is None:
                    continue
                chat = self._dialog_chat(dialog)
                # Focus chats offer twice the candidates, so they can fill their extra row.
                focus = curator.rules.focus_weight(
                    {"chat_title": chat.get("title"), "chat_username": chat.get("username"), "chat_id": chat.get("id")})
                per_dialog = messages_per_dialog * (2 if focus else 1)
                cursor = display_cursors.get(str(chat.get("id") or ""))
                # With a cursor the queries start past what was shown, so no overfetch for it.
                fetch_limit = self._account_scan_limit(dialog, per_dialog, unread_count, unread_only,
                                                       set() if cursor else display_read_keys)
                if fetch_limit <= 0:
                    continue
                kept_for_dialog = 0
                base_query = {
                    # Outgoing messages and known ads are skipped locally, so a
                    # bounded overfetch keeps them from hiding later unread ones.
                    "limit": (
                        fetch_limit
                        if include_outgoing
                        else min(
                            ACCOUNT_SCAN_LIMIT_CAP,
                            max(fetch_limit, per_dialog * 4),
                        )
                    ),
                    "reverse": False,
                }
                if not include_text:
                    base_query["filter"] = self._telethon_media_filter()
                floor = self._account_read_inbox_max_id(dialog) if unread_only else None
                for message_query in self._account_message_queries(base_query, floor, cursor):
                    if kept_for_dialog >= per_dialog:
                        break
                    async for message in self._aiter(
                        client.iter_messages(entity, **message_query)
                    ):
                        if message is None:
                            continue
                        if not include_outgoing and bool(getattr(message, "out", False)):
                            continue
                        key = self._account_message_key(dialog, message)
                        if key in display_read_keys:
                            continue
                        scanned += 1
                        if curator.is_known_filtered(key):
                            curator.note_known_filtered({"key": key, "chat_title": chat.get("title")})
                            continue
                        item = await self._account_message_item(client, dialog, message, existing,
                                                                include_text=include_text)
                        if item:
                            messages[item["key"]] = item
                            message_objects[item["key"]] = message
                            kept_for_dialog += 1
                            if kept_for_dialog >= per_dialog:
                                break

            candidates = sorted(
                messages.values(),
                key=lambda item: (int(item.get("date") or 0), int(item.get("message_id") or 0)),
                reverse=True,
            )
            logger.info("Telegram Digest scanned %d chats, %d messages in %.1fs.", matched_dialogs, scanned,
                        time.monotonic() - scan_started)
            # The AI client blocks; run it off the loop so Telethon keeps its connection alive.
            ordered = (await asyncio.to_thread(curator.curate, candidates))[:max_messages]
            logger.info("Telegram Digest curated %d stories; fetching media.", len(ordered))
            await self._hydrate_account_media(client, ordered, message_objects, existing, media_download_limit,
                                              time_left=time_left)

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
        payload["stats"].update(curator.stats(), scanned_count=scanned)
        payload["curation"] = curator.state(candidates)
        payload["status"]["ai_error"] = curator.ai_error
        self._attach_display_read_state(payload, cache)
        return payload

    @staticmethod
    def _account_message_queries(base_query, floor, cursor):
        """Newest unread first; once a chat has shown some, new arrivals and then the backlog.

        ``cursor`` is the (oldest, newest) shown message id, so every refresh reaches further
        back into a long unread list instead of re-reading its newest end.
        """
        if cursor is None:
            query = dict(base_query)
            if floor is not None:
                query["min_id"] = floor
            return [query]
        oldest, newest = cursor
        fresh = dict(base_query, min_id=max(floor or 0, newest))
        backlog = dict(base_query, max_id=oldest)
        if floor is not None:
            backlog["min_id"] = floor
        return [fresh, backlog]

    @staticmethod
    def _display_cursors(keys):
        """Each chat's oldest and newest shown message id, from the remembered display keys."""
        cursors = {}
        for key in keys or ():
            chat, _, raw_id = str(key).rpartition(":")
            if not chat or not raw_id.isdigit():
                continue
            message_id = int(raw_id)
            oldest, newest = cursors.get(chat, (message_id, message_id))
            cursors[chat] = (min(oldest, message_id), max(newest, message_id))
        return cursors

    def _account_message_key(self, dialog, message):
        chat = self._dialog_chat(dialog)
        message_id = self._optional_int(getattr(message, "id", None)) or 0
        return f"{chat.get('id', '')}:{message_id}"

    async def _account_message_item(self, client, dialog, message, existing_messages, include_text=False):
        chat = self._dialog_chat(dialog)
        message_id = self._optional_int(getattr(message, "id", None)) or 0
        key = f"{chat.get('id', '')}:{message_id}"
        raw_text = getattr(message, "raw_text", None) or getattr(message, "message", None) or ""
        text = self._clean_text(raw_text)
        title, summary = self._split_title_summary(raw_text)
        media = self._account_media_candidate(message, chat)
        if not media or str(media.get("kind") or "").lower() not in {"photo", "video"}:
            # Stickers, voice notes and files only count through their text.
            if not include_text or not text:
                return None
            media = None
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
            "chat_kind": self._dialog_kind(dialog),
            "has_buttons": bool(getattr(message, "reply_markup", None) or getattr(message, "buttons", None)),
            "forwarded": bool(getattr(message, "fwd_from", None) or getattr(message, "forward", None)),
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
            # Photos sent as one album share this id: the page shows a lead's other photos with it.
            "grouped_id": str(getattr(message, "grouped_id", None) or ""),
        }

    @staticmethod
    def _album_siblings(lead, messages):
        """The other photos and videos of the lead's album (same chat, same grouped_id)."""
        group = str((lead or {}).get("grouped_id") or "")
        if not group:
            return []
        chat, key = str(lead.get("chat_id") or ""), lead.get("key")
        return [item for item in messages or [] if isinstance(item, dict) and item.get("key") != key
                and str(item.get("grouped_id") or "") == group and str(item.get("chat_id") or "") == chat
                and str(item.get("media_kind") or "").lower() in {"photo", "video"}]

    async def _hydrate_account_media(self, client, messages, message_objects, existing_messages, media_download_limit,
                                     time_left=None):
        downloaded = 0
        lead, _rest = self._split_page_lead(messages)
        lead_key = str((lead or {}).get("key") or "")
        # The lead's album photo may fill the half its text leaves, so it is fetched as sharp.
        large_keys = {lead_key} | {str(item.get("key") or "") for item in self._album_siblings(lead, messages)[:1]}
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
                record_resource_event("telegram_media", disk_hits=1)
                item["media_path"] = str(cached_media_path)
                continue
            if downloaded >= media_download_limit:
                return
            if time_left is not None and time_left() < ACCOUNT_MEDIA_DOWNLOAD_TIMEOUT_SECONDS + 2:
                logger.info("Telegram Digest left the remaining media for the next refresh.")
                return
            try:
                cached_media_path = await asyncio.wait_for(
                    self._download_account_media(
                        client,
                        message,
                        media,
                        LEAD_PHOTO_MIN_SIDE if key in large_keys else THUMB_PHOTO_MIN_SIDE,
                    ),
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

        lead, _rest = self._split_page_lead(messages)
        for item in self._visible_message_items(messages) + self._album_siblings(lead, messages)[:1]:
            if not isinstance(item, dict) or not item:
                continue
            key = str(item.get("key") or "")
            if key in seen:
                continue
            seen.add(key)
            if str(item.get("media_kind") or "").lower() in {"photo", "video"}:
                queue.append(item)

        prefetched = 0
        for item in messages or []:
            if prefetched >= ACCOUNT_MEDIA_PREFETCH_LIMIT:
                break
            if not isinstance(item, dict) or not item:
                continue
            key = str(item.get("key") or "")
            if key in seen:
                continue
            seen.add(key)
            if (
                str(item.get("media_kind") or "").lower() in {"photo", "video"}
                and not item.get("media_path")
            ):
                queue.append(item)
                prefetched += 1
        return queue

    def _account_config(self, settings, device_config):
        api_id = self._setting_or_env(settings, ("telegramApiId", "apiId"), device_config, "TELEGRAM_API_ID")
        api_hash = self._setting_or_env(settings, ("telegramApiHash", "apiHash"), device_config, "TELEGRAM_API_HASH")
        session_path = self._setting_or_env(settings, ("telegramSessionPath", "sessionPath"), device_config, "TELEGRAM_SESSION_PATH")
        session_candidates = self._session_path_candidates(session_path)
        session_path = session_candidates[0]
        session_file = self._session_file_for(session_path)
        for candidate in session_candidates:
            candidate_file = self._session_file_for(candidate)
            if not candidate_file.is_file():
                continue
            try:
                candidate_file.chmod(0o600)
            except OSError as exc:
                logger.warning("Could not restrict Telegram session permissions: %s", exc)
        return {
            "api_id": self._optional_int(api_id),
            "api_hash": str(api_hash or "").strip(),
            "session_path": str(Path(session_path).expanduser()),
            "session_file": str(session_file),
            "session_ready": any(
                self._session_file_for(candidate).is_file()
                for candidate in session_candidates
            ),
            "session_candidates": [str(candidate) for candidate in session_candidates],
            "openai_key": self._openai_key(settings, device_config),
        }

    def _openai_key(self, settings, device_config):
        if str(settings.get("curationMode") or "ai").strip().casefold() != "ai":
            return ""
        return (
            self._load_env_key("OPEN_AI_SECRET", device_config)
            or self._load_env_key("OPENAI_API_KEY", device_config)
        )

    def _session_path_candidates(self, configured_path):
        if configured_path:
            path = Path(str(configured_path)).expanduser()
            if not path.is_absolute() and os.getenv("INKYPI_DATA_DIR", "").strip():
                path = self.data_dir() / path
            paths = [path]
        else:
            runtime_root_raw = os.getenv("INKYPI_DATA_DIR", "").strip()
            shared_runtime_path = (
                Path(runtime_root_raw).expanduser() / "telegram_account"
                if runtime_root_raw
                else None
            )
            plugin_runtime_path = self.data_dir(
                leaf="telegram_account",
                legacy_leaf=Path("cache") / "telegram_account",
                create=False,
            )
            if (
                shared_runtime_path is not None
                and self._session_file_for(shared_runtime_path).is_file()
            ):
                # The hardened service migration and the one-shot account
                # authorizer both place the durable Telethon session here.
                paths = [shared_runtime_path, plugin_runtime_path]
            else:
                paths = [plugin_runtime_path]
        unique_paths = []
        seen = set()
        for candidate in paths:
            candidate = Path(candidate).expanduser()
            marker = str(candidate)
            if marker in seen:
                continue
            seen.add(marker)
            unique_paths.append(candidate)
        unique_paths[0].parent.mkdir(parents=True, exist_ok=True)
        return unique_paths

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

    def _telethon_media_filter(self):
        try:
            from telethon import types
        except ImportError as exc:
            raise RuntimeError("Telethon is required for Telegram account mode") from exc
        return types.InputMessagesFilterPhotoVideo

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

    def _dialog_kind(self, dialog):
        """``private``, ``group`` or ``channel``, from Telethon's dialog flags or the entity."""
        if getattr(dialog, "is_user", False):
            return "private"
        if getattr(dialog, "is_group", False):
            return "group"
        entity = getattr(dialog, "entity", None)
        if getattr(entity, "megagroup", False) or type(entity).__name__ in {"Chat", "ChatForbidden"}:
            return "group"
        if type(entity).__name__ == "User" or getattr(entity, "first_name", None):
            return "private"
        return "channel"

    def _account_read_inbox_max_id(self, dialog):
        raw_dialog = getattr(dialog, "dialog", None)
        return self._optional_int(getattr(raw_dialog, "read_inbox_max_id", None))

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

    async def _download_account_media(self, client, message, media, min_side=LEAD_PHOTO_MIN_SIDE):
        media_dir = self._cache_dir() / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        target = media_dir / f"{self._safe_media_id(media)}.jpg"
        if target.is_file():
            record_resource_event("telegram_media", disk_hits=1)
            return target

        kwargs = {"file": bytes}
        if media.get("kind") in {"video", "file"}:
            kwargs["thumb"] = -1
        else:
            size_type = self._photo_size_type(message, min_side)
            if size_type:
                kwargs["thumb"] = size_type
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
        record_resource_event("telegram_media", downloads=1, downloaded_bytes=len(data))
        self._cache_image_bytes(data, target)
        return target

    @staticmethod
    def _photo_size_type(message, min_side):
        """The smallest photo size at least ``min_side`` on its long edge (else the largest)."""
        sizes = []
        for size in getattr(getattr(message, "photo", None), "sizes", None) or []:
            width, height, kind = getattr(size, "w", 0), getattr(size, "h", 0), getattr(size, "type", "")
            if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0 and kind:
                sizes.append((max(width, height), kind))
        if not sizes:
            return ""
        sizes.sort()
        return next((kind for side, kind in sizes if side >= min_side), sizes[-1][1])

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
        if not media or str(media.get("kind") or "").lower() not in {"photo", "video"}:
            return None
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
            "chat_kind": {"private": "private", "group": "group", "supergroup": "group"}.get(
                str(chat.get("type") or ""), "channel"
            ),
            "has_buttons": bool(message.get("reply_markup")),
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
            record_resource_event("telegram_media", disk_hits=1)
            return target

        with provider_io_lease(
            TELEGRAM_PROVIDER_ORIGIN,
            context=current_task_context(),
            timeout=REQUEST_TIMEOUT,
        ):
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

        with provider_io_lease(
            TELEGRAM_PROVIDER_ORIGIN,
            context=current_task_context(),
            timeout=REQUEST_TIMEOUT,
        ):
            response = get_http_session().get(
                self._file_url(token, file_path),
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
            image = measured_image_response(
                response,
                limits=TELEGRAM_MEDIA_IMAGE_LIMITS,
            ).convert("RGB")
            record_resource_event("telegram_media", downloads=1, downloaded_bytes=image.info["resource_downloaded_bytes"])
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
        messages = self._page_messages(messages)
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

    @staticmethod
    def _page_messages(messages):
        """Messages the page can show: a photo or video, or a text with words in it."""
        return [
            item
            for item in messages or []
            if isinstance(item, dict)
            and (
                str(item.get("media_kind") or "").lower() in {"photo", "video"}
                or str(item.get("raw_text") or "").strip()
            )
        ]

    def _sample_payload(self, settings, now, source_state, error_message=""):
        base = int(now.timestamp())
        samples = (
            ("private", "家人", "周日回家吃饭吗？", "明天降温记得多穿点，几点到提前说一声。", 600),
            ("channel", "Daily Signal", "模型发布更新", "频道里的重要消息会按 AI 重要度排序，广告与推广先被规则过滤。", 1500),
            ("group", "湾区生活群", "周日马拉松封路", "早 5 点到午 1 点沿线禁行，公交多条线路改道。", 2700),
            ("channel", "财经快讯", "市场异动观察", "主要指数快速回撤，关注成交量与后续流动性。", 3600),
        )
        messages = [
            {
                "key": f"sample:{index}",
                "message_id": 100 - index,
                "date": base - age,
                "chat_id": f"sample-{kind}",
                "chat_title": chat,
                "chat_username": "",
                "chat_kind": kind,
                "title": title,
                "summary": summary,
                "raw_text": f"{title} {summary}",
                "media_kind": "text",
                "media_path": "",
                "url": "",
            }
            for index, (kind, chat, title, summary, age) in enumerate(samples, 1)
        ]
        messages = Curator({**settings, "curationMode": "rules"}, None, now).curate(messages)
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
        lead, rest = self._split_page_lead(messages)
        if not lead:
            return []
        return [lead] + rest[:VISIBLE_COUNT - 1]

    def _split_page_lead(self, messages):
        """The page's lead and the rest; decided from the media kind, before any download."""
        items = [item for item in messages or [] if isinstance(item, dict) and item]
        return split_lead(items, self._has_page_media)

    @staticmethod
    def _has_page_media(item):
        return str((item or {}).get("media_kind") or "").lower() in {"photo", "video"}

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
        pending = (cache or {}).get(PENDING_PRESENTATION_DISPLAY_STATE_KEY)
        if isinstance(pending, dict):
            payload[PENDING_PRESENTATION_DISPLAY_STATE_KEY] = dict(pending)
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

    def _palette(self, theme_context=None):
        mode = str((theme_context or {}).get("mode") or "day").lower()
        night = mode == "night"
        if not night:
            return {
                "background": (255, 255, 255),
                "panel": (255, 255, 255),
                "rule": (206, 198, 181),
                "ink": (29, 33, 38),
                "muted": (78, 85, 91),
                "dim": (125, 128, 126),
                "blue": (34, 158, 217),
                "red": (214, 48, 49),
                "tags": DAY_TAG_COLOURS,
            }
        background = self._theme_role(
            theme_context,
            "background",
            (246, 242, 232),
        )
        panel = self._theme_role(theme_context, "panel", (255, 252, 242))
        ink = self._theme_role(theme_context, "ink", (29, 33, 38))
        muted = self._theme_role(theme_context, "muted", (78, 85, 91))
        rule = self._theme_role(theme_context, "rule", (206, 198, 181))
        dim = self._blend(muted, background, 0.72)
        return {
            "background": background,
            "panel": panel,
            "rule": rule,
            "ink": ink,
            "muted": muted,
            "dim": dim,
            "blue": (38, 140, 200),
            "red": (200, 56, 52),
            "tags": NIGHT_TAG_COLOURS,
        }

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
            return "刚刚"
        if minutes < 60:
            return f"{minutes} 分钟前"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} 小时前"
        return f"{hours // 24} 天前"

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

    def _wrap_text(self, draw, text, font, max_width, max_lines, first_indent=0):
        """Lines of ``text`` within ``max_width``, the first ``first_indent`` narrower (a tag
        stands before it), at most ``max_lines``, the last ellipsized when text is left over."""
        max_width = int(max_width or 0)
        max_lines = int(max_lines or 0)
        if max_width <= 0 or max_lines <= 0:
            return []
        text = self._clean_text(text)
        if not text:
            return []

        # Latin words break at spaces and CJK at any character, so a Chinese
        # sentence with a number in it fills each line instead of leaving gaps.
        lines = []
        current = ""
        tokens = re_tokens(text)
        index = 0
        while index < len(tokens) and len(lines) < max_lines:
            token = tokens[index]
            candidate = current + token if current else token.lstrip()
            limit = max_width - (first_indent if not lines else 0)
            if self._text_width(draw, candidate, font) <= limit:
                current = candidate
                index += 1
                continue
            if current:
                # Closing punctuation never starts a line: it takes the last character along.
                if token.lstrip()[:1] in NO_LINE_START and len(current) > 1 and current[-1] not in NO_LINE_START:
                    lines.append(current[:-1].rstrip())
                    current = current[-1]
                    if len(lines) >= max_lines:
                        break
                    continue
                lines.append(current)
                current = ""
                continue
            # One token wider than the line: split it by characters.
            chunk = ""
            for char in candidate:
                if chunk and self._text_width(draw, chunk + char, font) > limit:
                    break
                chunk += char
            chunk = self._clip_text(draw, chunk, font, limit) or candidate[:1]
            lines.append(chunk)
            rest = candidate[len(chunk):]
            if rest:
                tokens[index] = rest
            else:
                index += 1
        if current and len(lines) < max_lines:
            lines.append(current)
        lines = lines[:max_lines]
        if index < len(tokens) and lines:
            lines[-1] = self._ellipsize_text(draw, lines[-1], font, max_width - (first_indent if len(lines) == 1 else 0))
        return lines

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
