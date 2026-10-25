import json
import sys
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.telegram_digest.telegram_digest as telegram_mod  # noqa: E402
from plugins.base_plugin.presentation import (  # noqa: E402
    PresentationMode,
    PresentationRequestContext,
)
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)
from runtime.runtime_state import PresentationCommitReceipt  # noqa: E402
from plugins.telegram_digest.curation import VISIBLE_COUNT  # noqa: E402
from plugins.telegram_digest.telegram_digest import (  # noqa: E402
    RENDERED_VISIBLE_KEYS_IMAGE_INFO_KEY,
    STATE_VERSION,
    TelegramDigest,
)


TEST_TMP_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "telegram_digest_tests"


@pytest.fixture(autouse=True)
def _account_fetch_in_process(monkeypatch):
    # These tests drive Telethon through in-process fakes; the child-process
    # boundary is covered in test_telegram_account_worker.py.
    monkeypatch.setattr(TelegramDigest, "ACCOUNT_FETCH_IN_CHILD_PROCESS", False)


class DummyDeviceConfig:
    def __init__(self, resolution=(800, 480), token="", env=None):
        self.resolution = resolution
        self.token = token
        self.env = dict(env or {})

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        if key == "orientation":
            return "horizontal"
        return default

    def load_env_key(self, key):
        if key == "TELEGRAM_BOT_TOKEN":
            return self.token
        return self.env.get(key, "")


class FakeResponse:
    def __init__(self, json_data=None, chunks=None):
        self._json = json_data
        self._chunks = chunks or []

    def raise_for_status(self):
        return None

    def json(self):
        return self._json

    def iter_content(self, chunk_size=8192):
        yield from self._chunks


def test_missing_account_sample_is_not_cacheable_or_source_healthy(tmp_path):
    plugin = _plugin(tmp_path)

    image = plugin.generate_image(
        {"accessMode": "account"},
        DummyDeviceConfig(),
    )

    assert image.info.get("inkypi_skip_cache") is True
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK


def test_telegram_refresh_on_display_uses_prepared_background_render(tmp_path):
    plugin = _plugin(tmp_path)
    plugin.config["refresh_on_display"] = True
    payload = {
        "schema": STATE_VERSION,
        "messages": [
            {
                "key": "chat:42",
                "message_id": 42,
                "date": 1_782_000_000,
                "chat_title": "Latest",
                "title": "Newest unread",
                "summary": "Fresh account result",
                "media_kind": "text",
                "unread": True,
            }
        ],
        "channel_label": "Telegram",
        "stats": {},
        "status": {
            "source_state": "live",
            "account_api": True,
            "generated_at": "2026-07-13T20:00:00+00:00",
        },
    }
    def fake_payload(*_args, **_kwargs):
        result = dict(payload)
        plugin._write_state(result)
        return result

    plugin._payload = fake_payload
    request = PresentationRequestContext(
        request_id="a" * 32,
        requested_at="2026-07-13T20:00:00+00:00",
        origin_display_commit_id="display-commit",
        last_receipt=None,
    )

    assert plugin.presentation_mode({}) is PresentationMode.PREPARED_BANK
    prepared = plugin.prepare_presentation(
        {},
        DummyDeviceConfig(),
        request=request,
        resolved_theme_context=plugin.resolve_theme({}, DummyDeviceConfig()),
    )

    assert prepared.changed is True
    assert prepared.request_id == request.request_id
    assert prepared.image.size == (800, 480)
    assert prepared.image.info.get("inkypi_skip_cache") is not True
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["pending_presentation_display"] == {
        "request_id": request.request_id,
        "origin_display_commit_id": request.origin_display_commit_id,
        "keys": ["chat:42"],
        "source_state": "live",
        "account_api": True,
    }


def test_telegram_prepared_receipt_keys_are_the_drawn_lead_and_rows(tmp_path):
    plugin = _plugin(tmp_path)
    plugin.config["refresh_on_display"] = True
    messages = [
        {"key": "lead", "title": "lead", "raw_text": "lead text", "media_kind": "text", "date": 100},
        *[
            {
                "key": f"row-{index}",
                "title": f"row {index}",
                "raw_text": "row text " * 30,
                "media_kind": "photo",
                "media_path": "",
                "date": 100 - index,
            }
            for index in range(1, 10)
        ],
    ]
    payload = {
        "schema": STATE_VERSION,
        "messages": messages,
        "channel_label": "Telegram",
        "stats": {},
        "status": {"source_state": "live", "account_api": True},
    }

    def fake_payload(*_args, **_kwargs):
        result = dict(payload)
        plugin._write_state(result)
        return result

    plugin._payload = fake_payload
    request = PresentationRequestContext(
        request_id="9" * 32,
        requested_at="2026-07-13T20:00:00+00:00",
        origin_display_commit_id="display-commit",
        last_receipt=None,
    )

    prepared = plugin.prepare_presentation(
        {},
        DummyDeviceConfig(),
        request=request,
        resolved_theme_context=plugin.resolve_theme({}, DummyDeviceConfig()),
    )

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    exact_keys = saved["pending_presentation_display"]["keys"]
    assert exact_keys == list(prepared.image.info["inkypi_rendered_visible_message_keys"])
    # The short lead's empty room takes the first rows' stories, so the list shows as many more:
    # the pre-render estimate is the start of the drawn page.
    assert exact_keys[:VISIBLE_COUNT] == plugin._displayed_message_keys(messages)
    assert len(exact_keys) > VISIBLE_COUNT and len(set(exact_keys)) == len(exact_keys)
    assert set(exact_keys[VISIBLE_COUNT:]) <= {f"row-{index}" for index in range(1, 10)}


def test_telegram_prepared_refresh_rejects_sample_fallback(tmp_path):
    plugin = _plugin(tmp_path)
    plugin.config["refresh_on_display"] = True
    request = PresentationRequestContext(
        request_id="b" * 32,
        requested_at="2026-07-13T20:00:00+00:00",
        origin_display_commit_id="display-commit",
        last_receipt=None,
    )

    with pytest.raises(RuntimeError, match="fresh cacheable image"):
        plugin.prepare_presentation(
            {"accessMode": "account"},
            DummyDeviceConfig(),
            request=request,
            resolved_theme_context=plugin.resolve_theme({}, DummyDeviceConfig()),
        )


def test_telegram_prepared_refresh_rejects_stale_cache(tmp_path):
    plugin = _plugin(tmp_path)
    plugin.config["refresh_on_display"] = True
    plugin._payload = lambda *_args, **_kwargs: {
        "schema": STATE_VERSION,
        "messages": [
            {
                "key": "cached:42",
                "title": "Last successful item",
                "summary": "Keep the existing displayed image",
                "media_kind": "text",
                "date": 42,
            }
        ],
        "channel_label": "Telegram",
        "stats": {},
        "status": {
            "source_state": "cache",
            "account_api": True,
        },
    }
    request = PresentationRequestContext(
        request_id="8" * 32,
        requested_at="2026-07-13T20:00:00+00:00",
        origin_display_commit_id="display-commit",
        last_receipt=None,
    )

    with pytest.raises(RuntimeError, match="fresh cacheable image"):
        plugin.prepare_presentation(
            {"accessMode": "account"},
            DummyDeviceConfig(),
            request=request,
            resolved_theme_context=plugin.resolve_theme({}, DummyDeviceConfig()),
        )

    assert (tmp_path / "state.json").exists() is False


def test_telegram_reconciles_plugin_read_state_only_from_display_receipt(tmp_path):
    plugin = _plugin(tmp_path)
    state = {
        "schema": STATE_VERSION,
        "messages": [
            {"key": "chat:42", "title": "shown", "media_kind": "text", "date": 42},
            {"key": "chat:41", "title": "not shown", "media_kind": "text", "date": 41},
        ],
        "stats": {},
        "status": {"source_state": "live", "account_api": True},
        "pending_presentation_display": {
            "request_id": "c" * 32,
            "origin_display_commit_id": "origin-display-commit",
            "keys": ["chat:42"],
            "source_state": "live",
            "account_api": True,
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    receipt = PresentationCommitReceipt(
        request_id="c" * 32,
        committed_at="2026-07-13T20:01:00+00:00",
        display_commit_id="physical-display-commit",
        structural_generation=1,
        settings_revision=1,
        theme_mode="day",
    )

    plugin.reconcile_presentation_receipt({"markDisplayedRead": True}, receipt)
    plugin.reconcile_presentation_receipt({"markDisplayedRead": True}, receipt)

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["display_read"]["keys"] == ["chat:42"]
    assert saved["display_read"]["last_display_commit_id"] == receipt.display_commit_id
    assert saved["display_read"]["last_receipt_request_id"] == receipt.request_id
    assert "pending_presentation_display" not in saved


def test_telegram_ignores_origin_receipt_until_a_new_display_commit(tmp_path):
    plugin = _plugin(tmp_path)
    state = {
        "schema": STATE_VERSION,
        "messages": [
            {"key": "chat:42", "title": "prepared", "media_kind": "text", "date": 42},
        ],
        "stats": {},
        "status": {"source_state": "live", "account_api": True},
        "pending_presentation_display": {
            "request_id": "d" * 32,
            "origin_display_commit_id": "origin-display-commit",
            "keys": ["chat:42"],
            "source_state": "live",
            "account_api": True,
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    origin_receipt = PresentationCommitReceipt(
        request_id="d" * 32,
        committed_at="2026-07-13T20:00:00+00:00",
        display_commit_id="origin-display-commit",
        structural_generation=1,
        settings_revision=1,
        theme_mode="night",
    )

    plugin.reconcile_presentation_receipt(
        {"markDisplayedRead": True},
        origin_receipt,
    )

    pending = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "display_read" not in pending
    assert pending["pending_presentation_display"]["request_id"] == "d" * 32

    physical_receipt = PresentationCommitReceipt(
        request_id="d" * 32,
        committed_at="2026-07-13T20:01:00+00:00",
        display_commit_id="physical-display-commit",
        structural_generation=1,
        settings_revision=1,
        theme_mode="day",
    )
    plugin.reconcile_presentation_receipt(
        {"markDisplayedRead": True},
        physical_receipt,
    )

    committed = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert committed["display_read"]["keys"] == ["chat:42"]
    assert (
        committed["display_read"]["last_display_commit_id"]
        == "physical-display-commit"
    )
    assert "pending_presentation_display" not in committed


def test_telegram_ignores_receipt_for_a_different_prepared_request(tmp_path):
    plugin = _plugin(tmp_path)
    state = {
        "schema": STATE_VERSION,
        "messages": [{"key": "chat:42", "title": "shown"}],
        "stats": {},
        "status": {"source_state": "live", "account_api": True},
        "pending_presentation_display": {
            "request_id": "e" * 32,
            "keys": ["chat:42"],
            "source_state": "live",
            "account_api": True,
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    receipt = PresentationCommitReceipt(
        request_id="f" * 32,
        committed_at="2026-07-13T20:01:00+00:00",
        display_commit_id="other-display-commit",
        structural_generation=1,
        settings_revision=1,
        theme_mode="day",
    )

    plugin.reconcile_presentation_receipt({"markDisplayedRead": True}, receipt)

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "display_read" not in saved
    assert saved["pending_presentation_display"]["request_id"] == "e" * 32


class FailingSession:
    def get(self, *args, **kwargs):
        raise RuntimeError("network down")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.get_calls = []

    def get(self, url, params=None, timeout=None, stream=False, **kwargs):
        self.get_calls.append({
            "url": url,
            "params": params or {},
            "timeout": timeout,
            "stream": stream,
        })
        if not self.responses:
            raise AssertionError(f"Unexpected GET {url}")
        return self.responses.pop(0)


async def async_items(items):
    for item in items:
        yield item


class FakeTelegramClient:
    dialogs = []
    messages_by_entity = {}
    download_calls = []
    message_calls = []
    instances = []
    authorized = True

    def __init__(self, session_path, api_id, api_hash):
        self.session_path = session_path
        self.api_id = api_id
        self.api_hash = api_hash
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def is_user_authorized(self):
        return type(self).authorized

    def iter_dialogs(self, limit=None):
        return async_items(type(self).dialogs[:limit])

    def iter_messages(self, entity, limit=None, **kwargs):
        type(self).message_calls.append({"entity": entity, "limit": limit, **kwargs})
        messages = list(type(self).messages_by_entity.get(id(entity), []))
        if kwargs.get("filter"):
            messages = [
                message
                for message in messages
                if getattr(message, "photo", None)
                or getattr(message, "video", None)
                or getattr(message, "gif", None)
            ]
        min_id = kwargs.get("min_id")
        if min_id is not None:
            messages = [message for message in messages if int(message.id) > int(min_id)]
        if kwargs.get("reverse") is True:
            messages = list(reversed(messages))
        return async_items(messages[:limit])

    async def download_media(self, message, **kwargs):
        type(self).download_calls.append(kwargs)
        return getattr(message, "media_bytes", b"")


class SessionFallbackTelegramClient(FakeTelegramClient):
    instances = []

    async def is_user_authorized(self):
        return "plugins" in str(self.session_path)



def make_test_tmp_dir(name):
    path = TEST_TMP_ROOT / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path



def image_chunks(color=(0, 130, 180), size=(320, 180)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return [buffer.getvalue()]



def _plugin(tmp_path):
    plugin = TelegramDigest({"id": "telegram_digest"})
    plugin._cache_dir = lambda: tmp_path
    return plugin


def test_account_refresh_falls_back_when_shared_session_is_not_authorized(
    tmp_path,
    monkeypatch,
):
    runtime_data = tmp_path / "runtime-data"
    shared = runtime_data / "telegram_account.session"
    plugin_session = (
        runtime_data / "plugins" / "telegram_digest" / "telegram_account.session"
    )
    plugin_session.parent.mkdir(parents=True)
    shared.write_text("not-authorized", encoding="utf-8")
    plugin_session.write_text("authorized", encoding="utf-8")
    monkeypatch.setenv("INKYPI_DATA_DIR", str(runtime_data))
    plugin = _plugin(tmp_path / "plugin-cache")
    SessionFallbackTelegramClient.instances = []
    monkeypatch.setattr(
        plugin,
        "_telethon_client_class",
        lambda: SessionFallbackTelegramClient,
    )

    payload = plugin._fetch_account_payload(
        {"unreadOnly": False},
        DummyDeviceConfig(
            env={"TELEGRAM_API_ID": "12345", "TELEGRAM_API_HASH": "hash-value"}
        ),
        {},
        datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc),
        8,
    )

    assert payload["status"]["source_state"] == "live"
    assert [Path(item.session_path) for item in SessionFallbackTelegramClient.instances] == [
        runtime_data / "telegram_account",
        runtime_data / "plugins" / "telegram_digest" / "telegram_account",
    ]


def _theme_context(mode, requested_mode="auto"):
    palettes = {
        "day": {
            "background": (237, 245, 248),
            "panel": (255, 255, 255),
            "ink": (10, 12, 15),
            "muted": (74, 78, 84),
            "rule": (185, 188, 194),
            "accent": (34, 133, 170),
        },
        "night": {
            "background": (7, 22, 27),
            "panel": (0, 0, 0),
            "ink": (255, 255, 255),
            "muted": (194, 196, 202),
            "rule": (46, 48, 56),
            "accent": (104, 200, 230),
        },
    }
    return {
        "requested_mode": requested_mode,
        "mode": mode,
        "source": "weather",
        "reason": "sunrise/sunset",
        "sunrise": "2026-07-13T05:57:00-07:00",
        "sunset": "2026-07-13T20:30:00-07:00",
        "palette": palettes[mode],
    }


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



def test_plugin_info_and_settings_defaults_are_declared():
    root = Path(__file__).resolve().parents[1] / "src" / "plugins" / "telegram_digest"
    info = json.loads((root / "plugin-info.json").read_text(encoding="utf-8"))
    settings = (root / "settings.html").read_text(encoding="utf-8")

    assert info["id"] == "telegram_digest"
    assert info["class"] == "TelegramDigest"
    assert "Telegram Digest" in info["display_name"]
    assert 'name="refreshOnDisplay"' in settings
    assert 'value="true"' in settings
    assert 'name="accessMode"' in settings
    assert 'value="account"' in settings
    assert 'name="telegramApiId"' in settings
    assert 'name="telegramApiHash"' in settings
    assert 'name="telegramSessionPath"' in settings
    assert 'name="unreadOnly"' in settings
    assert 'name="markDisplayedRead"' in settings
    assert 'name="mediaDownloadLimit"' in settings
    assert 'max="7" step="1" name="mediaDownloadLimit"' in settings
    assert 'id="mediaDownloadLimit" value="7"' in settings
    for name in ("includeText", "curationMode", "aiDailyLimit", "blockKeywords", "boostKeywords",
                 "blockChats", "pinChats"):
        assert f'name="{name}"' in settings
    assert "private chats included" in settings
    assert "key === 'mediaDownloadLimit'" in settings
    assert 'name="botToken"' in settings
    assert 'name="chatFilter"' in settings


def test_telegram_renderer_uses_injected_palette_for_day_night_and_weather_auto(
    tmp_path,
):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc)
    payload = plugin._sample_payload({}, now, "sample")
    day_theme = _theme_context("day", requested_mode="day")
    night_theme = _theme_context("night", requested_mode="night")
    auto_theme = _theme_context("night")

    day = plugin._render_page(
        (800, 480),
        payload,
        {"_inkypi_theme": day_theme},
        now,
    )
    night = plugin._render_page(
        (800, 480),
        payload,
        {"_inkypi_theme": night_theme},
        now,
    )
    auto = plugin._render_page(
        (800, 480),
        payload,
        {"_inkypi_theme": auto_theme},
        now,
    )

    assert day.size == night.size == auto.size == (800, 480)
    assert day.getpixel((0, 479)) == (255, 255, 255)
    assert night.getpixel((0, 479)) == night_theme["palette"]["background"]
    assert day.getpixel((8, 300)) == (255, 255, 255)
    assert night.getpixel((8, 300)) == night_theme["palette"]["background"]
    assert auto.getpixel((0, 479)) == auto_theme["palette"]["background"]
    assert auto.getpixel((8, 300)) == auto_theme["palette"]["background"]
    assert day.tobytes() != night.tobytes()


def test_telegram_day_page_surfaces_are_solid_native_white(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc)
    payload = plugin._sample_payload({}, now, "sample")

    image = plugin._render_page(
        (800, 480),
        payload,
        {"_inkypi_theme": _theme_context("day", requested_mode="day")},
        now,
    )

    gutter_colors = {
        image.getpixel((x, y))
        for x in range(14)
        for y in range(48, 449)
    }
    assert gutter_colors == {(255, 255, 255)}
    palette = plugin._palette(_theme_context("day", requested_mode="day"))
    assert {palette["background"], palette["panel"]} == {(255, 255, 255)}


def test_telegram_page_shows_lead_picture_and_list_thumbnails(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 9, 4, 2, 40, tzinfo=timezone.utc)
    colors = ((171, 32, 47), (23, 139, 81), (37, 79, 183), (188, 92, 24))
    messages = []
    for index, color in enumerate(colors, start=1):
        media_path = tmp_path / f"media-{index}.png"
        Image.new("RGB", (320, 180), color).save(media_path)
        messages.append({
            "key": f"media-{index}",
            "message_id": index,
            "date": int(now.timestamp()) - index,
            "chat_id": f"chat-{index}",
            "chat_title": f"Channel {index}",
            "title": f"Story {index}",
            "raw_text": f"Story {index} details",
            "media_kind": "photo",
            "media_path": str(media_path),
            "curation": {"score": 90 - index, "tag": "科技", "headline": f"要闻 {index}", "summary": "摘要"},
        })
    messages.append({
        "key": "text-1", "message_id": 9, "date": int(now.timestamp()) - 9, "chat_id": "chat-9",
        "chat_title": "Friend", "chat_kind": "private", "raw_text": "周日回家吃饭吗？", "media_kind": "text",
        "curation": {"score": 50, "tag": "私聊", "headline": "周日回家吃饭吗？", "summary": ""},
    })
    state = {
        "schema": STATE_VERSION,
        "messages": messages,
        "stats": {"message_count": len(messages), "blocked_count": 3, "dialog_count": 5},
        "status": {"source_state": "live", "generated_at": now.isoformat(), "account_api": True},
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    image = plugin.generate_image(
        {"_theme_render_only": True, "_inkypi_theme": _theme_context("day", requested_mode="day")},
        DummyDeviceConfig(),
    )

    assert image.info[RENDERED_VISIBLE_KEYS_IMAGE_INFO_KEY] == (
        "media-1", "media-2", "media-3", "media-4", "text-1",
    )
    lead_area = {image.getpixel((x, y)) for x in range(20, 460, 8) for y in range(60, 260, 8)}
    assert colors[0] in lead_area
    thumbnails = {image.getpixel((x, y)) for x in range(724, 786, 4) for y in range(72, 452, 4)}
    assert all(color in thumbnails for color in colors[1:])


def test_telegram_day_dim_text_has_small_text_contrast_on_all_surfaces(tmp_path):
    plugin = _plugin(tmp_path)
    palette = plugin._palette(_theme_context("day", requested_mode="day"))

    assert palette["dim"] == (125, 128, 126)
    assert _contrast_ratio(palette["ink"], palette["background"]) >= 4.5
    assert _contrast_ratio(palette["ink"], palette["panel"]) >= 4.5


def test_telegram_theme_only_render_uses_cache_without_provider_or_state_writes(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc)
    cache = plugin._build_payload(
        {},
        [
            {
                "key": "cached:1",
                "date": int(now.timestamp()),
                "chat_title": "Cached channel",
                "title": "Cached digest",
                "summary": "Provider-free theme redraw",
                "raw_text": "Provider-free theme redraw",
                "media_kind": "text",
                "media_path": "",
            }
        ],
        now,
        "live",
        auth_mode="account",
        unread_total=1,
    )
    provider_calls = []
    state_writes = []

    def account_provider(*_args, **_kwargs):
        provider_calls.append("account")
        return dict(cache)

    def bot_provider(*_args, **_kwargs):
        provider_calls.append("bot")
        return dict(cache)

    monkeypatch.setattr(plugin, "_read_state", lambda: cache)
    monkeypatch.setattr(plugin, "_write_state", lambda payload: state_writes.append(payload))
    monkeypatch.setattr(plugin, "_fetch_account_payload", account_provider)
    monkeypatch.setattr(plugin, "_fetch_live_payload", bot_provider)

    settings = {
        "accessMode": "account",
        "_theme_render_only": True,
        "_inkypiDisplayRender": True,
        "markDisplayedRead": True,
        "_inkypi_theme": _theme_context("night"),
    }
    image = plugin.generate_image(settings, DummyDeviceConfig())

    assert provider_calls == []
    assert state_writes == []
    assert image.getpixel((0, 479)) == settings["_inkypi_theme"]["palette"]["background"]



def test_photo_message_uses_largest_photo_and_caches_media(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    updates = [
        {
            "update_id": 7,
            "channel_post": {
                "message_id": 10,
                "date": int(now.timestamp()),
                "chat": {"id": -100123, "title": "Daily Signal", "username": "daily_signal"},
                "caption": "模型发布更新\n频道中的图片与视频封面优先展示",
                "photo": [
                    {"file_id": "small-photo", "file_unique_id": "small", "width": 160, "height": 90},
                    {"file_id": "large-photo", "file_unique_id": "large", "width": 1280, "height": 720},
                ],
            },
        }
    ]
    session = FakeSession([
        FakeResponse({"ok": True, "result": updates}),
        FakeResponse({"ok": True, "result": {"file_path": "photos/large.jpg"}}),
        FakeResponse(chunks=image_chunks()),
    ])
    monkeypatch.setattr(telegram_mod, "get_http_session", lambda: session)

    payload = plugin._payload(
        {"botToken": "token-123", "chatFilter": "@daily_signal", "channelLabel": "@daily_signal"},
        DummyDeviceConfig(),
        now,
    )

    assert payload["schema"] == STATE_VERSION
    assert payload["status"]["source_state"] == "live"
    assert payload["status"]["bot_api"] is True
    assert payload["messages"][0]["title"] == "模型发布更新"
    assert payload["messages"][0]["summary"] == "频道中的图片与视频封面优先展示"
    assert payload["messages"][0]["raw_text"] == (
        "模型发布更新 频道中的图片与视频封面优先展示"
    )
    assert payload["messages"][0]["media_kind"] == "photo"
    assert payload["messages"][0]["media_file_id"] == "large-photo"
    assert Path(payload["messages"][0]["media_path"]).is_file()
    assert session.get_calls[0]["url"].endswith("/getUpdates")
    assert session.get_calls[1]["url"].endswith("/getFile")
    assert session.get_calls[1]["params"]["file_id"] == "large-photo"
    assert session.get_calls[2]["stream"] is True



def test_video_message_uses_thumbnail_as_display_media(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    updates = [
        {
            "update_id": 8,
            "channel_post": {
                "message_id": 11,
                "date": int(now.timestamp()),
                "chat": {"id": -100123, "title": "Daily Signal", "username": "daily_signal"},
                "caption": "项目部署完成\n视频里有完整过程",
                "video": {
                    "file_id": "video-file",
                    "file_unique_id": "video-unique",
                    "duration": 138,
                    "width": 1280,
                    "height": 720,
                    "thumbnail": {
                        "file_id": "video-thumb",
                        "file_unique_id": "thumb-unique",
                        "width": 640,
                        "height": 360,
                    },
                },
            },
        }
    ]
    session = FakeSession([
        FakeResponse({"ok": True, "result": updates}),
        FakeResponse({"ok": True, "result": {"file_path": "videos/thumb.jpg"}}),
        FakeResponse(chunks=image_chunks(color=(60, 80, 120))),
    ])
    monkeypatch.setattr(telegram_mod, "get_http_session", lambda: session)

    payload = plugin._payload({"botToken": "token-123", "chatFilter": "-100123"}, DummyDeviceConfig(), now)

    message = payload["messages"][0]
    assert message["media_kind"] == "video"
    assert message["title"] == "项目部署完成"
    assert message["summary"] == "视频里有完整过程"
    assert message["media_file_id"] == "video-thumb"
    assert message["duration"] == 138
    assert Path(message["media_path"]).is_file()



def test_account_mode_fetches_unread_messages_and_caches_media(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    session_base = tmp_path / "telegram_account"
    session_base.with_suffix(".session").write_text("authorized", encoding="utf-8")
    entity = SimpleNamespace(id=-100123, username="daily_signal", title="Daily Signal")
    dialog = SimpleNamespace(entity=entity, id=-100123, title="Daily Signal", unread_count=2)
    message = SimpleNamespace(
        id=44,
        date=now,
        raw_text="未读图片\n频道重点",
        message="未读图片\n频道重点",
        photo=SimpleNamespace(w=1280, h=720),
        video=None,
        gif=None,
        document=None,
        out=False,
        media_bytes=image_chunks(color=(40, 120, 90))[0],
    )
    FakeTelegramClient.dialogs = [dialog]
    FakeTelegramClient.messages_by_entity = {id(entity): [message]}
    FakeTelegramClient.download_calls = []
    FakeTelegramClient.instances = []
    FakeTelegramClient.authorized = True
    monkeypatch.setattr(plugin, "_telethon_client_class", lambda: FakeTelegramClient)

    payload = plugin._payload(
        {
            "accessMode": "account",
            "telegramApiId": "12345",
            "telegramApiHash": "hash-value",
            "telegramSessionPath": str(session_base),
            "dialogFilter": "@daily_signal",
        },
        DummyDeviceConfig(),
        now,
    )

    assert payload["schema"] == STATE_VERSION
    assert payload["status"]["source_state"] == "live"
    assert payload["status"]["account_api"] is True
    assert payload["status"]["bot_api"] is False
    assert payload["stats"]["unread_count"] == 2
    assert payload["stats"]["dialog_count"] == 1
    assert payload["messages"][0]["title"] == "未读图片"
    assert payload["messages"][0]["summary"] == "频道重点"
    assert payload["messages"][0]["media_kind"] == "photo"
    assert Path(payload["messages"][0]["media_path"]).is_file()
    assert FakeTelegramClient.instances[0].session_path == str(session_base)
    assert FakeTelegramClient.instances[0].api_id == 12345
    assert FakeTelegramClient.download_calls[0]["file"] is bytes


def test_account_mode_requests_newest_unread_message_first(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    session_base = tmp_path / "telegram_account"
    session_base.with_suffix(".session").write_text("authorized", encoding="utf-8")
    entity = SimpleNamespace(id=-100123, username="daily_signal", title="Daily Signal")
    dialog = SimpleNamespace(entity=entity, id=-100123, title="Daily Signal", unread_count=3)
    newest = SimpleNamespace(
        id=52,
        date=now,
        raw_text="Newest unread\nThis should be visited first",
        message="Newest unread\nThis should be visited first",
        photo=SimpleNamespace(w=1280, h=720),
        video=None,
        gif=None,
        document=None,
        out=False,
        media_bytes=image_chunks(color=(50, 110, 90))[0],
    )
    middle = SimpleNamespace(
        id=51,
        date=now,
        raw_text="Middle unread",
        message="Middle unread",
        photo=None,
        video=None,
        gif=None,
        document=None,
        out=False,
    )
    oldest = SimpleNamespace(
        id=50,
        date=now,
        raw_text="Oldest unread",
        message="Oldest unread",
        photo=None,
        video=None,
        gif=None,
        document=None,
        out=False,
    )
    FakeTelegramClient.dialogs = [dialog]
    FakeTelegramClient.messages_by_entity = {id(entity): [newest, middle, oldest]}
    FakeTelegramClient.download_calls = []
    FakeTelegramClient.message_calls = []
    FakeTelegramClient.instances = []
    FakeTelegramClient.authorized = True
    monkeypatch.setattr(plugin, "_telethon_client_class", lambda: FakeTelegramClient)

    payload = plugin._payload(
        {
            "accessMode": "account",
            "telegramApiId": "12345",
            "telegramApiHash": "hash-value",
            "telegramSessionPath": str(session_base),
            "dialogFilter": "@daily_signal",
            "messagesPerDialog": "1",
        },
        DummyDeviceConfig(),
        now,
    )

    assert [item["key"] for item in payload["messages"]] == ["-100123:52"]
    assert payload["messages"][0]["title"] == "Newest unread"
    assert FakeTelegramClient.message_calls[0]["reverse"] is False


@pytest.mark.parametrize("unread_only", [True, False])
def test_account_mode_with_text_off_skips_text_only_unread_messages(
    tmp_path,
    monkeypatch,
    unread_only,
):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    session_base = tmp_path / "telegram_account"
    session_base.with_suffix(".session").write_text("authorized", encoding="utf-8")
    entity = SimpleNamespace(id=-100123, username="daily_signal", title="Daily Signal")
    dialog = SimpleNamespace(
        entity=entity,
        id=-100123,
        title="Daily Signal",
        unread_count=2,
        dialog=SimpleNamespace(read_inbox_max_id=50),
    )
    text_only = SimpleNamespace(
        id=53,
        date=now,
        raw_text="Newest unread text",
        message="Newest unread text",
        photo=None,
        video=None,
        gif=None,
        document=None,
        out=False,
    )
    outgoing_photo = SimpleNamespace(
        id=52,
        date=now,
        raw_text="Outgoing photo",
        message="Outgoing photo",
        photo=SimpleNamespace(w=1280, h=720),
        video=None,
        gif=None,
        document=None,
        out=True,
        media_bytes=image_chunks(color=(120, 80, 70))[0],
    )
    photo = SimpleNamespace(
        id=51,
        date=now,
        raw_text="Photo caption",
        message="Photo caption",
        photo=SimpleNamespace(w=1280, h=720),
        video=None,
        gif=None,
        document=None,
        out=False,
        media_bytes=image_chunks(color=(70, 120, 90))[0],
    )
    FakeTelegramClient.dialogs = [dialog]
    FakeTelegramClient.messages_by_entity = {
        id(entity): [text_only, outgoing_photo, photo]
    }
    FakeTelegramClient.download_calls = []
    FakeTelegramClient.message_calls = []
    FakeTelegramClient.instances = []
    FakeTelegramClient.authorized = True
    monkeypatch.setattr(plugin, "_telethon_client_class", lambda: FakeTelegramClient)

    payload = plugin._payload(
        {
            "accessMode": "account",
            "telegramApiId": "12345",
            "telegramApiHash": "hash-value",
            "telegramSessionPath": str(session_base),
            "dialogFilter": "@daily_signal",
            "messagesPerDialog": "1",
            "unreadOnly": unread_only,
            "includeText": "false",
        },
        DummyDeviceConfig(),
        now,
    )

    assert [item["key"] for item in payload["messages"]] == ["-100123:51"]
    assert payload["messages"][0]["media_kind"] == "photo"
    assert Path(payload["messages"][0]["media_path"]).is_file()
    assert FakeTelegramClient.message_calls[0]["limit"] == 4
    assert FakeTelegramClient.message_calls[0]["filter"].__name__ == (
        "InputMessagesFilterPhotoVideo"
    )
    if unread_only:
        assert FakeTelegramClient.message_calls[0]["min_id"] == 50
    else:
        assert "min_id" not in FakeTelegramClient.message_calls[0]
    assert len(FakeTelegramClient.download_calls) == 1


def test_account_mode_keeps_text_only_unread_messages_by_default(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    session_base = tmp_path / "telegram_account"
    session_base.with_suffix(".session").write_text("authorized", encoding="utf-8")
    entity = SimpleNamespace(id=-100123, username="daily_signal", title="Daily Signal")
    dialog = SimpleNamespace(entity=entity, id=-100123, title="Daily Signal", unread_count=2)
    text_only = SimpleNamespace(
        id=53, date=now, raw_text="Newest unread text", message="Newest unread text",
        photo=None, video=None, gif=None, document=None, out=False,
    )
    FakeTelegramClient.dialogs = [dialog]
    FakeTelegramClient.messages_by_entity = {id(entity): [text_only]}
    FakeTelegramClient.download_calls = []
    FakeTelegramClient.message_calls = []
    FakeTelegramClient.instances = []
    FakeTelegramClient.authorized = True
    monkeypatch.setattr(plugin, "_telethon_client_class", lambda: FakeTelegramClient)

    payload = plugin._payload(
        {
            "accessMode": "account",
            "telegramApiId": "12345",
            "telegramApiHash": "hash-value",
            "telegramSessionPath": str(session_base),
            "messagesPerDialog": "1",
        },
        DummyDeviceConfig(),
        now,
    )

    assert [item["key"] for item in payload["messages"]] == ["-100123:53"]
    assert payload["messages"][0]["media_kind"] == "text"
    assert payload["messages"][0]["curation"]["source"] == "rules"
    assert "filter" not in FakeTelegramClient.message_calls[0]
    assert FakeTelegramClient.download_calls == []
    assert payload["status"]["ai_error"]


def test_account_mode_downloads_every_visible_picture_with_hard_cap(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    session_base = tmp_path / "telegram_account"
    session_base.with_suffix(".session").write_text("authorized", encoding="utf-8")
    entity = SimpleNamespace(id=-100123, username="daily_signal", title="Daily Signal")
    dialog = SimpleNamespace(entity=entity, id=-100123, title="Daily Signal", unread_count=9)
    messages = []
    for offset in range(9):
        messages.append(SimpleNamespace(
            id=60 - offset,
            date=now,
            raw_text="",
            message="",
            photo=SimpleNamespace(w=1280, h=720),
            video=None,
            gif=None,
            document=None,
            out=False,
            media_bytes=image_chunks(color=(40 + offset * 20, 120, 90))[0],
        ))
    FakeTelegramClient.dialogs = [dialog]
    FakeTelegramClient.messages_by_entity = {id(entity): messages}
    FakeTelegramClient.download_calls = []
    FakeTelegramClient.instances = []
    FakeTelegramClient.authorized = True
    monkeypatch.setattr(plugin, "_telethon_client_class", lambda: FakeTelegramClient)

    payload = plugin._payload(
        {
            "accessMode": "account",
            "telegramApiId": "12345",
            "telegramApiHash": "hash-value",
            "telegramSessionPath": str(session_base),
            "dialogFilter": "@daily_signal",
            "messagesPerDialog": "9",
            "maxMessages": "9",
            "mediaDownloadLimit": "30",
        },
        DummyDeviceConfig(),
        now,
    )

    visible_keys = plugin._displayed_message_keys(payload["messages"])
    cached_keys = [item["key"] for item in payload["messages"] if item["media_path"]]

    assert visible_keys == [f"-100123:{60 - offset}" for offset in range(7)]
    assert cached_keys == visible_keys
    assert len(FakeTelegramClient.download_calls) == 7
    assert payload["stats"]["media_cached_count"] == 7
    assert payload["stats"]["media_missing_count"] == 2
    assert payload["status"]["media_cache"] == "partial"


def test_photo_download_picks_the_smallest_size_that_covers_the_slot(tmp_path):
    plugin = _plugin(tmp_path)
    sizes = [
        SimpleNamespace(type="s", w=90, h=60),
        SimpleNamespace(type="m", w=320, h=213),
        SimpleNamespace(type="x", w=800, h=533),
        SimpleNamespace(type="y", w=1280, h=853),
    ]
    message = SimpleNamespace(photo=SimpleNamespace(sizes=sizes))

    assert plugin._photo_size_type(message, 800) == "x"
    assert plugin._photo_size_type(message, 320) == "m"
    assert plugin._photo_size_type(message, 4000) == "y"
    assert plugin._photo_size_type(SimpleNamespace(photo=None), 800) == ""



def test_displayed_account_messages_are_recorded_as_plugin_read(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    messages = [
        {"key": "text-new", "title": "new text", "media_kind": "text", "date": 800},
        {"key": "photo-mid", "title": "photo", "media_kind": "photo", "date": 700},
        *[
            {"key": f"row-{index}", "title": f"row {index}", "media_kind": "text", "date": 600 - index}
            for index in range(1, 6)
        ],
        {"key": "hidden", "title": "hidden", "media_kind": "text", "date": 100},
    ]
    payload = {
        "schema": STATE_VERSION,
        "messages": messages,
        "status": {"source_state": "live", "account_api": True},
    }

    plugin._remember_displayed_messages(
        payload,
        {"_inkypiDisplayRender": True, "markDisplayedRead": "true"},
        now,
    )

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["display_read"]["keys"] == [
        "photo-mid", "text-new", "row-1", "row-2", "row-3", "row-4", "row-5",
    ]
    assert state["display_read"]["last_marked_count"] == VISIBLE_COUNT


def test_displayed_account_messages_are_limited_to_the_page(tmp_path):
    plugin = _plugin(tmp_path)
    messages = [
        {
            "key": f"msg-{index}",
            "title": f"message {index}",
            "chat_title": f"Group {index % 4}",
            "media_kind": "text",
            "date": 1000 - index,
        }
        for index in range(VISIBLE_COUNT + 5)
    ]

    keys = plugin._displayed_message_keys(messages)

    assert keys == [f"msg-{index}" for index in range(VISIBLE_COUNT)]


def test_title_asset_is_strict_size_and_transparent():
    root = Path(__file__).resolve().parents[1] / "src" / "plugins" / "telegram_digest"
    asset = root / "assets" / "telegram_digest_title.png"

    with Image.open(asset) as image:
        title = image.convert("RGBA")

    assert title.size == (330, 36)
    alpha = title.getchannel("A")
    assert alpha.getbbox() is not None
    assert alpha.getpixel((0, 0)) == 0
    assert alpha.getpixel((329, 35)) == 0

def test_media_only_fallback_text_explains_missing_caption(tmp_path):
    plugin = _plugin(tmp_path)

    assert plugin._fallback_title({"kind": "photo"}) == "图片无配字"
    assert plugin._fallback_summary({"kind": "photo"}, {"text": ""}) == "这条图片消息没有配字，仅展示图片内容。"
    assert plugin._fallback_title({"kind": "video"}) == "视频无配字"
    assert plugin._fallback_summary({"kind": "video"}, {"text": ""}) == "这条视频消息没有配字，仅展示视频封面。"


def test_featured_media_smart_crop_prefers_detailed_region(tmp_path):
    plugin = _plugin(tmp_path)
    source = Image.new("RGB", (300, 100), (128, 128, 128))
    draw = ImageDraw.Draw(source)
    for x in range(204, 298, 4):
        draw.line((x, 0, x, 99), fill=(255, 255, 255) if x % 8 else (0, 0, 0), width=2)
    draw.rectangle((220, 20, 292, 82), fill=(220, 24, 24))

    crop = plugin._smart_crop_box(source, 1.0)
    fitted = plugin._fit_media_image(source, (100, 100), featured=True)

    assert crop[0] >= 150
    assert fitted.getpixel((50, 50))[0] > 180

def test_chat_wrap_ellipsis_only_on_final_visible_line(tmp_path):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (320, 200), "white")
    draw = ImageDraw.Draw(image)
    font = plugin._font(12, "normal")

    lines = plugin._wrap_text(draw, "这是一条很长的频道消息" * 18, font, 92, 3)

    assert len(lines) == 3
    assert all(not line.endswith("...") for line in lines[:-1])
    assert lines[-1].endswith("...")
    assert all(plugin._text_width(draw, line, font) <= 92 for line in lines)


def test_chat_wrap_long_url_only_ellipsizes_final_chunk(tmp_path):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (320, 200), "white")
    draw = ImageDraw.Draw(image)
    font = plugin._font(12, "normal")
    text = "https://example.com/" + "verylongsegment" * 12

    lines = plugin._wrap_text(draw, text, font, 96, 4)

    assert len(lines) == 4
    assert all(not line.endswith("...") for line in lines[:-1])
    assert lines[-1].endswith("...")
    assert all(plugin._text_width(draw, line, font) <= 96 for line in lines)

def test_fit_text_respects_tiny_widths(tmp_path):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (120, 80), "white")
    draw = ImageDraw.Draw(image)
    font = plugin._font(12, "normal")

    for width in range(0, 18):
        fitted = plugin._fit_text(draw, "overflow", font, width)
        assert plugin._text_width(draw, fitted, font) <= width

def test_remember_displayed_messages_uses_rendered_visible_keys(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    payload = {
        "schema": STATE_VERSION,
        "messages": [
            {"key": "a", "media_kind": "text"},
            {"key": "b", "media_kind": "text"},
            {"key": "c", "media_kind": "text"},
        ],
        "_rendered_visible_keys": ["a", "c"],
        "status": {"source_state": "live", "account_api": True},
    }

    plugin._remember_displayed_messages(payload, {"_inkypiDisplayRender": True}, now)

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["display_read"]["keys"] == ["a", "c"]
    assert state["display_read"]["last_marked_count"] == 2

def test_account_mode_skips_plugin_read_messages_and_scans_next_unread(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    session_base = tmp_path / "telegram_account"
    session_base.with_suffix(".session").write_text("authorized", encoding="utf-8")
    state = {
        "schema": STATE_VERSION,
        "channel_label": "@daily_signal",
        "messages": [],
        "display_read": {"keys": ["-100123:44"]},
        "stats": {},
        "status": {"source_state": "live", "account_api": True},
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    entity = SimpleNamespace(id=-100123, username="daily_signal", title="Daily Signal")
    dialog = SimpleNamespace(entity=entity, id=-100123, title="Daily Signal", unread_count=2)
    displayed_message = SimpleNamespace(
        id=44,
        date=now,
        raw_text="Already shown\nThis should be skipped",
        message="Already shown\nThis should be skipped",
        photo=SimpleNamespace(w=1280, h=720),
        video=None,
        gif=None,
        document=None,
        out=False,
        media_bytes=image_chunks(color=(40, 120, 90))[0],
    )
    next_message = SimpleNamespace(
        id=43,
        date=now,
        raw_text="Next unread\nThis should be displayed",
        message="Next unread\nThis should be displayed",
        photo=SimpleNamespace(w=1280, h=720),
        video=None,
        gif=None,
        document=None,
        out=False,
        media_bytes=image_chunks(color=(70, 130, 100))[0],
    )
    FakeTelegramClient.dialogs = [dialog]
    FakeTelegramClient.messages_by_entity = {id(entity): [displayed_message, next_message]}
    FakeTelegramClient.download_calls = []
    FakeTelegramClient.instances = []
    FakeTelegramClient.authorized = True
    monkeypatch.setattr(plugin, "_telethon_client_class", lambda: FakeTelegramClient)

    payload = plugin._payload(
        {
            "accessMode": "account",
            "telegramApiId": "12345",
            "telegramApiHash": "hash-value",
            "telegramSessionPath": str(session_base),
            "dialogFilter": "@daily_signal",
            "messagesPerDialog": "1",
        },
        DummyDeviceConfig(),
        now,
    )

    assert [item["key"] for item in payload["messages"]] == ["-100123:43"]
    assert payload["messages"][0]["title"] == "Next unread"
    assert payload["display_read"]["keys"] == ["-100123:44"]
    assert len(FakeTelegramClient.download_calls) == 1

def test_account_mode_without_authorized_session_renders_setup_sample(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)

    payload = plugin._payload(
        {
            "accessMode": "account",
            "telegramApiId": "12345",
            "telegramApiHash": "hash-value",
            "telegramSessionPath": str(tmp_path / "missing_account"),
        },
        DummyDeviceConfig(),
        now,
    )

    assert payload["status"]["source_state"] == "missing_account"
    assert payload["status"]["account_api"] is True
    assert "not authorized" in payload["status"]["live_error"]



def test_photo_close_to_the_best_score_leads_the_page(tmp_path):
    plugin = _plugin(tmp_path)
    messages = [
        {"key": "text-new", "media_kind": "text", "date": 300, "curation": {"score": 80}},
        {"key": "photo-mid", "media_kind": "photo", "date": 200, "curation": {"score": 75}},
        {"key": "video-old", "media_kind": "video", "date": 100, "curation": {"score": 40}},
    ]

    lead, rest = plugin._split_page_lead(messages)

    assert lead["key"] == "photo-mid"
    assert [item["key"] for item in rest] == ["text-new", "video-old"]


def test_far_lower_photo_does_not_displace_the_best_story(tmp_path):
    plugin = _plugin(tmp_path)
    messages = [
        {"key": "text-new", "media_kind": "text", "date": 300, "curation": {"score": 90}},
        {"key": "photo-mid", "media_kind": "photo", "date": 200, "curation": {"score": 60}},
    ]

    lead, rest = plugin._split_page_lead(messages)

    assert lead["key"] == "text-new"
    assert [item["key"] for item in rest] == ["photo-mid"]


def test_lead_is_the_best_story_when_no_picture_is_close(tmp_path):
    plugin = _plugin(tmp_path)
    messages = [
        {"key": "text-new", "media_kind": "text", "date": 300, "curation": {"score": 70}},
        {"key": "text-old", "media_kind": "text", "date": 100, "curation": {"score": 65}},
    ]

    lead, rest = plugin._split_page_lead(messages)

    assert lead["key"] == "text-new"
    assert [item["key"] for item in rest] == ["text-old"]


def test_text_only_bot_message_is_skipped_without_media_download(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    updates = [
        {
            "update_id": 9,
            "channel_post": {
                "message_id": 12,
                "date": int(now.timestamp()),
                "chat": {"id": -100123, "title": "Daily Signal", "username": "daily_signal"},
                "text": "链接收藏\nhttps://core.telegram.org/bots/api",
            },
        }
    ]
    session = FakeSession([FakeResponse({"ok": True, "result": updates})])
    monkeypatch.setattr(telegram_mod, "get_http_session", lambda: session)

    payload = plugin._payload({"botToken": "token-123", "chatFilter": "daily_signal"}, DummyDeviceConfig(), now)

    assert payload["messages"] == []
    assert payload["last_update_id"] == 9
    assert payload["stats"]["message_count"] == 0
    assert len(session.get_calls) == 1


def test_bot_force_refresh_still_confirms_skipped_text_update_cursor(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    updates = [
        {
            "update_id": 9,
            "channel_post": {
                "message_id": 12,
                "date": int(now.timestamp()),
                "chat": {"id": -100123, "username": "daily_signal"},
                "text": "Text-only update",
            },
        }
    ]
    session = FakeSession(
        [
            FakeResponse({"ok": True, "result": updates}),
            FakeResponse({"ok": True, "result": []}),
        ]
    )
    monkeypatch.setattr(telegram_mod, "get_http_session", lambda: session)

    plugin._payload({"botToken": "token-123"}, DummyDeviceConfig(), now)
    payload = plugin._payload(
        {"botToken": "token-123", "forceRefresh": True},
        DummyDeviceConfig(),
        now,
    )

    assert "offset" not in session.get_calls[0]["params"]
    assert session.get_calls[1]["params"]["offset"] == 10
    assert payload["last_update_id"] == 9
    assert payload["messages"] == []



def test_live_failure_uses_stale_message_cache(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    state = {
        "schema": STATE_VERSION,
        "channel_label": "@daily_signal",
        "last_update_id": 12,
        "messages": [
            {
                "key": "-100123:1",
                "message_id": 1,
                "date": int(now.timestamp()),
                "title": "Cached item",
                "summary": "Previous successful Telegram refresh",
                "media_kind": "text",
            }
        ],
        "stats": {"message_count": 1, "photo_count": 0, "video_count": 0, "new_count": 0},
        "status": {"source_state": "live", "generated_at": now.isoformat()},
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(telegram_mod, "get_http_session", lambda: FailingSession())

    payload = plugin._payload({"botToken": "token-123"}, DummyDeviceConfig(), now)

    assert payload["status"]["source_state"] == "cache"
    assert payload["status"]["live_error"] == "network down"
    assert payload["messages"] == []
    assert payload["stats"]["message_count"] == 0



def test_generate_image_without_token_renders_sample_digest(tmp_path):
    plugin = _plugin(tmp_path)

    image = plugin.generate_image({}, DummyDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    assert len(image.getcolors(maxcolors=1_000_000)) > 20


def test_telegram_digest_base_font_uses_shared_resolver(monkeypatch, tmp_path):
    plugin = _plugin(tmp_path)
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        telegram_mod,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
        raising=False,
    )

    assert plugin._font(15, "bold") is sentinel
    assert calls == [(15, True)]


def test_telegram_digest_preserves_shared_bold_fallback_raster(monkeypatch, tmp_path):
    plugin = _plugin(tmp_path)
    shared = telegram_mod.get_base_ui_font(48, bold=True)
    expected = bytes(shared.getmask("Readable UI"))
    monkeypatch.setattr(
        telegram_mod,
        "get_base_ui_font",
        lambda size, bold=False: shared,
    )

    font = plugin._font(48, "bold")

    assert font is shared
    assert bytes(font.getmask("Readable UI")) == expected


def test_a_short_portrait_lead_promotes_the_next_picture_story_under_its_text(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 9, 4, 2, 40, tzinfo=timezone.utc)
    portrait, second = tmp_path / "portrait.png", tmp_path / "second.png"
    Image.new("RGB", (300, 420), (171, 32, 47)).save(portrait)
    Image.new("RGB", (320, 180), (23, 139, 81)).save(second)

    def story(key, score, media=None, **extra):
        return {"key": key, "message_id": score, "date": int(now.timestamp()) - (100 - score), "chat_id": key,
                "chat_title": key, "title": key, "raw_text": f"{key} text", "media_kind": "photo" if media else "text",
                "media_path": str(media or ""),
                "curation": {"score": score, "tag": "时事", "headline": f"{key} 标题", "summary": "短摘要"}, **extra}

    messages = [story("lead", 95, portrait), story("second", 80, second)] + [story(f"row-{i}", 70 - i) for i in range(1, 8)]
    state = {"schema": STATE_VERSION, "messages": messages, "stats": {"message_count": len(messages)},
             "status": {"source_state": "live", "generated_at": now.isoformat(), "account_api": True}}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    image = plugin.generate_image(
        {"_theme_render_only": True, "_inkypi_theme": _theme_context("day", requested_mode="day")}, DummyDeviceConfig())

    keys = image.info[RENDERED_VISIBLE_KEYS_IMAGE_INFO_KEY]
    # The second picture fills the lead's empty half and the list still shows six rows.
    assert keys[:2] == ("lead", "second") and keys[2:] == tuple(f"row-{i}" for i in range(1, 7))
    lower_lead = {image.getpixel((x, y)) for x in range(210, 460, 6) for y in range(300, 420, 6)}
    assert (23, 139, 81) in lower_lead


def test_without_any_picture_the_next_story_fills_the_room_under_a_short_lead(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 9, 4, 2, 40, tzinfo=timezone.utc)
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (300, 420), (171, 32, 47)).save(portrait)

    def story(key, score, media=None):
        return {"key": key, "message_id": score, "date": int(now.timestamp()) - (100 - score), "chat_id": key,
                "chat_title": key, "title": key, "raw_text": f"{key} text", "media_kind": "photo" if media else "text",
                "media_path": str(media or ""),
                "curation": {"score": score, "tag": "时事", "headline": f"{key} 标题", "summary": "短摘要"}}

    messages = [story("lead", 95, portrait)] + [story(f"row-{i}", 80 - i) for i in range(1, 9)]
    state = {"schema": STATE_VERSION, "messages": messages, "stats": {"message_count": len(messages)},
             "status": {"source_state": "live", "generated_at": now.isoformat(), "account_api": True}}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    image = plugin.generate_image(
        {"_theme_render_only": True, "_inkypi_theme": _theme_context("day", requested_mode="day")}, DummyDeviceConfig())

    keys = image.info[RENDERED_VISIBLE_KEYS_IMAGE_INFO_KEY]
    # The first list stories move under the lead, in order, and the list still shows six more.
    assert keys == ("lead",) + tuple(f"row-{i}" for i in range(1, len(keys)))
    assert len(keys) >= 1 + 1 + 6
    lower_lead = image.crop((204, 250, 470, 440))
    assert lower_lead.getextrema() != image.crop((204, 250, 205, 251)).getextrema()


def test_the_lower_picture_is_the_leads_own_album_photo_before_another_story(tmp_path):
    plugin = _plugin(tmp_path)
    portrait, album, other = tmp_path / "portrait.png", tmp_path / "album.png", tmp_path / "other.png"
    Image.new("RGB", (300, 420), (171, 32, 47)).save(portrait)
    Image.new("RGB", (320, 180), (37, 79, 183)).save(album)
    Image.new("RGB", (320, 180), (23, 139, 81)).save(other)
    lead = {"key": "lead", "chat_id": "c1", "grouped_id": "g1", "raw_text": "lead", "media_path": str(portrait)}
    sibling = {"key": "album-2", "chat_id": "c1", "grouped_id": "g1", "raw_text": "", "media_path": str(album)}
    textless = {"key": "bare", "chat_id": "c2", "raw_text": "", "media_path": str(other)}
    story = {"key": "story", "chat_id": "c3", "raw_text": "has words", "media_path": str(other)}

    assert plugin._second_picture(lead, [story, sibling]) == (sibling, True)
    # Without an album photo, a picture story with no words gives way to one that has them.
    assert plugin._second_picture(dict(lead, grouped_id=""), [textless, story]) == (story, False)
    # A picture with no words still beats an empty half; no picture at all gives (None, False).
    assert plugin._second_picture(dict(lead, grouped_id=""), [textless]) == (textless, False)
    assert plugin._second_picture(dict(lead, grouped_id=""), [dict(story, media_path="")]) == (None, False)
    # The album photo is fetched with the lead, as sharp as the lead.
    assert plugin._album_siblings(dict(lead, media_kind="photo"), [lead, dict(sibling, media_kind="photo")]) == [
        dict(sibling, media_kind="photo")]
