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
from plugins.telegram_digest.telegram_digest import CHAT_FEED_MAX_ROWS, STATE_VERSION, TelegramDigest  # noqa: E402


TEST_TMP_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "telegram_digest_tests"


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
        "keys": ["chat:42"],
        "source_state": "live",
        "account_api": True,
    }


def test_telegram_prepared_receipt_uses_exact_pixel_layout_keys(tmp_path):
    plugin = _plugin(tmp_path)
    plugin.config["refresh_on_display"] = True
    long_caption = "long caption " * 80
    messages = [
        {
            "key": "lead",
            "title": "lead",
            "summary": "featured text",
            "media_kind": "text",
            "date": 100,
        },
        *[
            {
                "key": f"media-{index}",
                "title": f"media {index}",
                "summary": long_caption,
                "media_kind": "photo",
                "media_path": "",
                "date": 100 - index,
            }
            for index in range(1, 5)
        ],
        {
            "key": "late-short",
            "title": "short",
            "summary": "fits remaining pixels",
            "media_kind": "text",
            "date": 1,
        },
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
    heuristic_keys = plugin._displayed_message_keys(messages)
    assert exact_keys == list(
        prepared.image.info["inkypi_rendered_visible_message_keys"]
    )
    assert "media-3" in exact_keys
    assert "media-3" not in heuristic_keys


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
    assert day.getpixel((0, 479)) == day_theme["palette"]["background"]
    assert night.getpixel((0, 479)) == night_theme["palette"]["background"]
    assert day.getpixel((20, 60)) == day_theme["palette"]["panel"]
    assert night.getpixel((20, 60)) == night_theme["palette"]["panel"]
    assert auto.getpixel((0, 479)) == auto_theme["palette"]["background"]
    assert auto.getpixel((20, 60)) == auto_theme["palette"]["panel"]
    assert day.tobytes() != night.tobytes()


def test_telegram_day_dim_text_has_small_text_contrast_on_all_surfaces(tmp_path):
    plugin = _plugin(tmp_path)
    palette = plugin._palette(_theme_context("day", requested_mode="day"))

    assert _contrast_ratio(palette["dim"], palette["background"]) >= 4.5
    assert _contrast_ratio(palette["dim"], palette["panel"]) >= 4.5


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
        photo=None,
        video=None,
        gif=None,
        document=None,
        out=False,
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


def test_account_mode_default_media_download_limit_caches_more_than_three_photos(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    session_base = tmp_path / "telegram_account"
    session_base.with_suffix(".session").write_text("authorized", encoding="utf-8")
    entity = SimpleNamespace(id=-100123, username="daily_signal", title="Daily Signal")
    dialog = SimpleNamespace(entity=entity, id=-100123, title="Daily Signal", unread_count=5)
    messages = []
    for offset in range(5):
        messages.append(SimpleNamespace(
            id=60 - offset,
            date=now,
            raw_text=f"Photo {offset}\nCaption {offset}",
            message=f"Photo {offset}\nCaption {offset}",
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
            "messagesPerDialog": "5",
            "maxMessages": "8",
        },
        DummyDeviceConfig(),
        now,
    )

    assert len(FakeTelegramClient.download_calls) == 5
    assert all(Path(item["media_path"]).is_file() for item in payload["messages"][:5])
    assert payload["stats"]["media_cached_count"] == 5
    assert payload["stats"]["media_missing_count"] == 0
    assert payload["status"]["media_cache"] == "ok"



def test_displayed_account_messages_are_recorded_as_plugin_read(tmp_path):
    plugin = _plugin(tmp_path)
    now = datetime(2026, 6, 27, 18, 42, tzinfo=timezone.utc)
    messages = [
        {"key": "text-new", "title": "new text", "media_kind": "text", "date": 700},
        {"key": "photo-mid", "title": "photo", "media_kind": "photo", "date": 600},
        {"key": "row-1", "title": "row 1", "media_kind": "text", "date": 500},
        {"key": "row-2", "title": "row 2", "media_kind": "text", "date": 400},
        {"key": "row-3", "title": "row 3", "media_kind": "text", "date": 300},
        {"key": "row-4", "title": "row 4", "media_kind": "text", "date": 200},
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
    assert state["display_read"]["keys"] == ["text-new", "photo-mid", "row-1", "row-2", "row-3", "row-4"]
    assert state["display_read"]["last_marked_count"] == 6

def test_displayed_account_messages_are_limited_to_dense_chat_capacity(tmp_path):
    plugin = _plugin(tmp_path)
    messages = [
        {
            "key": f"msg-{index}",
            "title": f"message {index}",
            "summary": "dense unread line",
            "chat_title": f"Group {index % 4}",
            "media_kind": "text",
            "date": 1000 - index,
        }
        for index in range(CHAT_FEED_MAX_ROWS + 5)
    ]

    keys = plugin._displayed_message_keys(messages)

    assert keys == [f"msg-{index}" for index in range(8)]

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

def test_chat_feed_uses_raw_text_without_media_summary(tmp_path):
    plugin = _plugin(tmp_path)
    photo_with_caption = {
        "key": "photo-caption",
        "title": "图片无配字",
        "summary": "这条图片消息没有配字，仅展示图片内容。",
        "raw_text": "真实群消息正文，直接显示这一段",
        "media_kind": "photo",
    }
    photo_without_caption = {
        "key": "photo-only",
        "title": "图片无配字",
        "summary": "这条图片消息没有配字，仅展示图片内容。",
        "raw_text": "",
        "media_kind": "photo",
    }

    assert plugin._chat_line_text(photo_with_caption) == "真实群消息正文，直接显示这一段"
    assert plugin._chat_line_text(photo_without_caption) == ""


def test_media_only_fallback_text_explains_missing_caption(tmp_path):
    plugin = _plugin(tmp_path)

    assert plugin._fallback_title({"kind": "photo"}) == "图片无配字"
    assert plugin._fallback_summary({"kind": "photo"}, {"text": ""}) == "这条图片消息没有配字，仅展示图片内容。"
    assert plugin._fallback_title({"kind": "video"}) == "视频无配字"
    assert plugin._fallback_summary({"kind": "video"}, {"text": ""}) == "这条视频消息没有配字，仅展示视频封面。"


def test_featured_media_without_caption_expands_image_into_text_area(tmp_path):
    plugin = _plugin(tmp_path)
    cached = tmp_path / "captionless.jpg"
    Image.new("RGB", (640, 480), (60, 120, 160)).save(cached)
    image = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(image)
    scale = 1
    fonts = {
        "headline": plugin._font(24, "bold"),
        "body": plugin._font(15, "normal"),
        "small": plugin._font(12, "normal"),
        "label": plugin._font(11, "bold"),
    }
    p = plugin._palette()
    media_boxes = []
    drawn_text = []

    def record_media(image_obj, draw_obj, item, box, palette, featured=False):
        media_boxes.append(tuple(box))

    def record_text(draw_obj, xy, text, font, fill):
        value = str(text or "")
        drawn_text.append(value)
        draw_obj.text(xy, value, font=font, fill=fill)

    plugin._draw_media = record_media
    plugin._draw_text = record_text
    item = {
        "key": "photo-only",
        "title": "No caption fallback",
        "summary": "Fallback summary should not occupy the image card.",
        "raw_text": "",
        "media_kind": "photo",
        "media_path": str(cached),
        "date": 100,
        "chat_title": "Daily Signal",
    }

    plugin._draw_featured_post(image, draw, item, (14, 44, 486, 449), fonts, p, scale)

    assert media_boxes == [(26, 56, 474, 437)]
    assert "No caption fallback" not in drawn_text
    assert "Fallback summary should not occupy the image card." not in drawn_text
    assert all("Daily Signal" not in value for value in drawn_text)


def test_featured_media_with_caption_keeps_text_layout(tmp_path):
    plugin = _plugin(tmp_path)
    cached = tmp_path / "captioned.jpg"
    Image.new("RGB", (640, 480), (60, 120, 160)).save(cached)
    image = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(image)
    scale = 1
    fonts = {
        "headline": plugin._font(24, "bold"),
        "body": plugin._font(15, "normal"),
        "small": plugin._font(12, "normal"),
        "label": plugin._font(11, "bold"),
    }
    p = plugin._palette()
    media_boxes = []

    def record_media(image_obj, draw_obj, item, box, palette, featured=False):
        media_boxes.append(tuple(box))

    plugin._draw_media = record_media
    item = {
        "key": "photo-caption",
        "title": "Caption title",
        "summary": "Caption detail",
        "raw_text": "Caption title\nCaption detail",
        "media_kind": "photo",
        "media_path": str(cached),
        "date": 100,
        "chat_title": "Daily Signal",
    }

    plugin._draw_featured_post(image, draw, item, (14, 44, 486, 449), fonts, p, scale)

    assert media_boxes == [(26, 56, 474, 280)]

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

def test_chat_feed_fills_remaining_space_with_later_short_items(tmp_path):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(image)
    scale = 1
    fonts = {
        "row_title": plugin._font(16, "bold"),
        "small": plugin._font(12, "normal"),
        "label": plugin._font(11, "bold"),
        "chat": plugin._font(12, "normal"),
        "chat_channel": plugin._font(11, "bold"),
        "chat_meta": plugin._font(10, "normal"),
        "chat_badge": plugin._font(9, "bold"),
    }
    p = plugin._palette()
    messages = [
        {"key": "photo", "chat_title": "A", "media_kind": "photo", "raw_text": "caption", "date": 100},
        {"key": "long", "chat_title": "B", "media_kind": "text", "raw_text": "这是一条很长的消息" * 20, "date": 99},
        {"key": "short", "chat_title": "C", "media_kind": "text", "raw_text": "短消息", "date": 98},
    ]

    drawn = plugin._draw_chat_feed_panel(image, draw, messages, (498, 44, 786, 282), fonts, p, scale)

    assert [item["key"] for item in drawn] == ["photo", "short"]


def test_chat_text_item_never_draws_past_right_edge(tmp_path):
    plugin = _plugin(tmp_path)
    image = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(image)
    scale = 1
    fonts = {
        "chat": plugin._font(12, "normal"),
        "chat_channel": plugin._font(11, "bold"),
        "chat_meta": plugin._font(10, "normal"),
    }
    box = (500, 50, 786, 135)
    calls = []

    def record_text(draw_obj, xy, text, font, fill):
        value = str(text or "")
        calls.append((xy, value, font))
        draw_obj.text(xy, value, font=font, fill=fill)

    plugin._draw_text = record_text
    item = {
        "key": "wide",
        "chat_title": "这是一个特别长的频道名称 WithVeryLongChannelName",
        "media_kind": "text",
        "raw_text": "https://example.com/" + "verylongsegment" * 20 + " " + "后面还有很多中文内容" * 20,
        "date": 100,
    }

    plugin._draw_chat_text_item(draw, item, box, fonts, scale, (27, 32, 34), (88, 83, 71), (234, 238, 231), (151, 157, 151))

    assert calls
    for (x, _y), value, font in calls:
        assert x + plugin._text_width(draw, value, font) <= box[2] + 0.5


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
        photo=None,
        video=None,
        gif=None,
        document=None,
        out=False,
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
    assert FakeTelegramClient.download_calls == []

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



def test_cached_photo_message_is_featured_before_newer_text_items(tmp_path):
    plugin = _plugin(tmp_path)
    cached = tmp_path / "featured.jpg"
    Image.new("RGB", (120, 80), (80, 130, 150)).save(cached)
    messages = [
        {"key": "text-new", "title": "new text", "media_kind": "text", "date": 300},
        {"key": "photo-mid", "title": "photo", "media_kind": "photo", "media_path": str(cached), "date": 200},
        {"key": "video-old", "title": "video", "media_kind": "video", "date": 100},
    ]

    lead, secondary = plugin._prioritize_featured_messages(messages)

    assert lead["key"] == "photo-mid"
    assert [item["key"] for item in secondary] == ["text-new", "video-old"]


def test_missing_photo_does_not_displace_newer_text_feature(tmp_path):
    plugin = _plugin(tmp_path)
    messages = [
        {"key": "text-new", "title": "new text", "media_kind": "text", "date": 300},
        {"key": "photo-mid", "title": "photo", "media_kind": "photo", "media_path": "", "date": 200},
        {"key": "video-old", "title": "video", "media_kind": "video", "date": 100},
    ]

    lead, secondary = plugin._prioritize_featured_messages(messages)

    assert lead["key"] == "text-new"
    assert [item["key"] for item in secondary] == ["photo-mid", "video-old"]


def test_featured_message_defaults_to_newest_when_no_photo(tmp_path):
    plugin = _plugin(tmp_path)
    messages = [
        {"key": "text-new", "title": "new text", "media_kind": "text", "date": 300},
        {"key": "video-old", "title": "video", "media_kind": "video", "date": 100},
    ]

    lead, secondary = plugin._prioritize_featured_messages(messages)

    assert lead["key"] == "text-new"
    assert [item["key"] for item in secondary] == ["video-old"]


def test_text_only_message_becomes_link_card_without_media_download(tmp_path, monkeypatch):
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

    assert payload["messages"][0]["media_kind"] == "link"
    assert payload["messages"][0]["media_path"] == ""
    assert payload["messages"][0]["url"] == "https://core.telegram.org/bots/api"
    assert len(session.get_calls) == 1



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
    assert payload["messages"][0]["title"] == "Cached item"



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
