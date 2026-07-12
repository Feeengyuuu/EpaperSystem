import ast
from contextlib import contextmanager
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace

from flask import Flask
from jinja2 import ChoiceLoader, FileSystemLoader
from PIL import Image
import pytest
from werkzeug.datastructures import MultiDict

import blueprints.plugin as plugin_blueprint
import blueprints.playlist as playlist_blueprint
import utils.app_utils as app_utils
from model import PlaylistManager
from runtime.refresh_contracts import JobRecord, JobStatus, TaskContext
from runtime.refresh_queue import QueueFullError, QueueStoppingError
from runtime.render_arbiter import RenderArbiter
from security.request_limits import UploadTooLarge, configure_request_limits


INVALID_REFRESH_SETTINGS = [
    "{not-json",
    json.dumps([]),
    json.dumps({}),
    json.dumps({"refreshType": "unknown"}),
    json.dumps({"refreshType": "interval", "unit": "week", "interval": 1}),
    json.dumps({"refreshType": "interval", "unit": "minute", "interval": 0}),
    json.dumps({"refreshType": "interval", "unit": "minute", "interval": -1}),
    json.dumps({"refreshType": "interval", "unit": "minute", "interval": "abc"}),
    json.dumps({"refreshType": "scheduled", "refreshTime": "24:00"}),
    json.dumps({"refreshType": "scheduled", "refreshTime": "not-a-time"}),
]


def _png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _playlist_manager():
    return PlaylistManager.from_dict({
        "playlists": [{
            "name": "Default",
            "start_time": "00:00",
            "end_time": "24:00",
            "plugins": [{
                "plugin_id": "weather",
                "name": "Home",
                "plugin_settings": {"units": "metric"},
                "refresh": {"interval": 300},
                "instance_uuid": "home-uuid",
            }],
        }],
    })


class RecordingManager:
    """Expose only immutable lookup and atomic mutation APIs to route tests."""

    def __init__(self, manager, events):
        self.manager = manager
        self.events = events
        self.update_calls = []
        self.add_mutated = threading.Event()

    @contextmanager
    def instance_lifecycle_guard(self):
        with self.manager.instance_lifecycle_guard():
            yield

    def resolve_plugin_instance_snapshot(self, playlist_name, plugin_id, instance_name):
        self.events.append(("resolve", playlist_name, plugin_id, instance_name))
        return self.manager.resolve_plugin_instance_snapshot(
            playlist_name,
            plugin_id,
            instance_name,
        )

    def update_plugin_instance_atomic(self, instance_uuid, **kwargs):
        self.events.append(("mutation", "update", instance_uuid))
        self.update_calls.append((instance_uuid, kwargs))
        return self.manager.update_plugin_instance_atomic(instance_uuid, **kwargs)

    def delete_plugin_instance_atomic(self, instance_uuid, **kwargs):
        self.events.append(("mutation", "delete", instance_uuid))
        return self.manager.delete_plugin_instance_atomic(instance_uuid, **kwargs)

    def snapshot_instance(self, instance_uuid):
        return self.manager.snapshot_instance(instance_uuid)

    def add_plugin_to_playlist_snapshot(self, playlist_name, plugin_data):
        result = self.manager.add_plugin_to_playlist_snapshot(playlist_name, plugin_data)
        if result is not None:
            self.add_mutated.set()
        return result


class RecordingQueue:
    def __init__(self, events):
        self.events = events
        self.canceled = []

    def cancel_instance(self, instance_uuid):
        self.events.append(("cancel", instance_uuid))
        self.canceled.append(instance_uuid)
        return 1


class RecordingRetryRegistry:
    def __init__(self, events):
        self.events = events
        self.discarded = []

    def discard(self, instance_uuid):
        self.events.append(("retry_discard", instance_uuid))
        self.discarded.append(instance_uuid)


class RecordingArbiter:
    def __init__(self, events):
        self.events = events
        self.inside = False
        self.plugin_ids = []

    @contextmanager
    def lease(self, plugin_id, _context):
        self.plugin_ids.append(plugin_id)
        self.events.append(("lease_enter", plugin_id))
        self.inside = True
        try:
            yield
        finally:
            self.inside = False
            self.events.append(("lease_exit", plugin_id))


class RecordingRefreshTask:
    def __init__(self, events):
        self.events = events
        self.running = True
        self.refresh_queue = RecordingQueue(events)
        self.retry_registry = RecordingRetryRegistry(events)
        self.render_arbiter = RecordingArbiter(events)
        self.queue_error = None
        self.job = {
            "id": "job-1",
            "status": "queued",
            "plugin_id": "weather",
        }
        self.jobs = {"job-1": dict(self.job)}
        self.managed_paths = ()
        self.signal_callback = None
        self.transient_paths = ()

    def cache_refresh_in_progress(self):
        return False

    def submit_manual_update(self, action, *, transient_paths=()):
        self.events.append(("submit_manual", type(action).__name__))
        self.transient_paths = tuple(transient_paths)
        if self.queue_error is not None:
            raise self.queue_error
        return self.job

    def submit_playlist_display(self, instance_uuid, **kwargs):
        self.events.append(("submit_display", instance_uuid, kwargs))
        if self.queue_error is not None:
            raise self.queue_error
        return self.job

    def get_manual_update_job(self, job_id):
        return self.jobs.get(job_id)

    def signal_config_change(self):
        self.events.append(("signal",))
        if self.signal_callback is not None:
            self.signal_callback()

    def make_cleanup_context(self):
        self.events.append(("cleanup_context",))
        return TaskContext(
            threading.Event(),
            time.monotonic() + 2.0,
            time.monotonic,
        )

    def managed_cache_paths(self, instance_uuid, **kwargs):
        self.events.append(("managed_paths", instance_uuid, kwargs))
        return self.managed_paths


class RecordingDeviceConfig:
    def __init__(self, manager, events, plugin_image_dir):
        self.manager = manager
        self.events = events
        self.plugin_image_dir = str(plugin_image_dir)
        self.fail_write = False
        self.write_event = threading.Event()

    def get_playlist_manager(self):
        return self.manager

    def write_config(self):
        self.events.append(("write",))
        self.write_event.set()
        if self.fail_write:
            raise RuntimeError("config write failed")

    def get_plugin(self, plugin_id):
        self.events.append(("get_plugin", plugin_id))
        return {"id": plugin_id}


class ForbiddenDisplayManager:
    def display_image(self, *_args, **_kwargs):
        raise AssertionError("Web routes must never render outside the refresh queue")


@pytest.fixture
def plugin_env(tmp_path):
    events = []
    inner_manager = _playlist_manager()
    manager = RecordingManager(inner_manager, events)
    task = RecordingRefreshTask(events)
    device_config = RecordingDeviceConfig(manager, events, tmp_path)
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        DEVICE_CONFIG=device_config,
        REFRESH_TASK=task,
        DISPLAY_MANAGER=ForbiddenDisplayManager(),
    )
    configure_request_limits(app)
    app.register_blueprint(plugin_blueprint.plugin_bp)
    return SimpleNamespace(
        app=app,
        client=app.test_client(),
        events=events,
        manager=manager,
        inner_manager=inner_manager,
        task=task,
        device_config=device_config,
        tmp_path=tmp_path,
    )


def test_display_instance_force_refresh_skips_sports_dashboard_force_mode():
    assert plugin_blueprint._display_instance_force_refresh(
        "sports_dashboard",
        cache_refresh_busy=False,
    ) is False
    assert plugin_blueprint._display_instance_force_refresh(
        "newspaper",
        cache_refresh_busy=False,
    ) is True
    assert plugin_blueprint._display_instance_force_refresh(
        "newspaper",
        cache_refresh_busy=True,
    ) is False


@pytest.fixture
def theme_page_client_factory(monkeypatch):
    def create(
        *,
        settings=None,
        supports_day_night_theme=True,
        plugin_id="themed",
        settings_template="base_plugin/settings.html",
    ):
        manifest = SimpleNamespace(
            capabilities=SimpleNamespace(
                supports_day_night_theme=supports_day_night_theme,
            ),
        )
        plugin_config = {
            "id": plugin_id,
            "display_name": "Themed Plugin",
            "_manifest": manifest,
        }
        manager = PlaylistManager.from_dict({
            "playlists": [{
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [{
                    "plugin_id": plugin_id,
                    "name": "Home",
                    "plugin_settings": dict(settings or {}),
                    "refresh": {"interval": 300},
                    "instance_uuid": f"{plugin_id}-home-uuid",
                }],
            }],
        })

        class DeviceConfig:
            def get_playlist_manager(self):
                return manager

            def get_plugin(self, requested_plugin_id):
                return plugin_config if requested_plugin_id == plugin_id else None

        class Plugin:
            def generate_settings_template(self):
                template_params = {
                    "settings_template": settings_template,
                    "frame_styles": [],
                    "supports_day_night_theme": False,
                }
                if plugin_id == "steam_charts":
                    template_params["chart_modes"] = {
                        "new_trending": {"label": "Trending"},
                    }
                return template_params

        monkeypatch.setattr(
            plugin_blueprint,
            "get_plugin_instance",
            lambda _config: Plugin(),
        )
        source_root = Path(plugin_blueprint.__file__).resolve().parents[1]
        app = Flask(__name__, template_folder=str(source_root / "templates"))
        app.jinja_loader = ChoiceLoader([
            FileSystemLoader(source_root / "templates"),
            FileSystemLoader(source_root / "plugins"),
        ])
        app.config.update(TESTING=True, DEVICE_CONFIG=DeviceConfig())
        app.register_blueprint(plugin_blueprint.plugin_bp)
        app.register_blueprint(playlist_blueprint.playlist_bp)
        return app.test_client()

    return create


def _theme_selector(html):
    match = re.search(
        r'<select id="themeMode"[^>]*>.*?</select>',
        html,
        flags=re.DOTALL,
    )
    return match.group(0) if match else None


def _assert_selected_theme(html, expected):
    selector = _theme_selector(html)
    assert selector is not None
    assert html.count('name="themeMode"') == 1
    assert html.count('id="themeMode"') == 1
    assert selector.count(" selected") == 1
    assert f'<option value="{expected}" selected>' in selector


class _SettingsFormSelectParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_settings_form = False
        self.current_select = None
        self.selects = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "settingsForm":
            self.in_settings_form = True
            return
        if not self.in_settings_form:
            return
        if tag == "select" and attributes.get("name"):
            self.current_select = {
                "name": attributes["name"],
                "options": [],
            }
        elif tag == "option" and self.current_select is not None:
            self.current_select["options"].append(
                (attributes.get("value", ""), "selected" in attributes),
            )

    def handle_endtag(self, tag):
        if tag == "select" and self.current_select is not None:
            options = self.current_select["options"]
            value = next(
                (option_value for option_value, selected in options if selected),
                options[0][0] if options else "",
            )
            self.selects.append((self.current_select["name"], value))
            self.current_select = None
        elif tag == "form" and self.in_settings_form:
            self.in_settings_form = False


def _settings_form_html(html):
    match = re.search(
        r'<form\b[^>]*\bid\s*=\s*["\']settingsForm["\'][^>]*>.*?</form>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _submitted_select_fields(html, *, theme_mode):
    parser = _SettingsFormSelectParser()
    parser.feed(_settings_form_html(html))
    theme_indexes = [
        index
        for index, (name, _value) in enumerate(parser.selects)
        if name == "themeMode"
    ]
    assert theme_indexes
    canonical_theme_index = theme_indexes[-1]

    fields = MultiDict()
    for index, (name, value) in enumerate(parser.selects):
        fields.add(name, theme_mode if index == canonical_theme_index else value)
    return fields


BUILTIN_THEME_SETTINGS = [
    pytest.param(
        "box_office_top_movies",
        {"themeMode": "paper"},
        "day",
        id="box-office-paper",
    ),
    pytest.param(
        "china_box_office_top_movies",
        {"themeMode": "cinema"},
        "night",
        id="china-box-office-cinema",
    ),
    pytest.param(
        "live_radar",
        {"themeMode": "dark"},
        "night",
        id="live-radar-dark",
    ),
    pytest.param(
        "species_radar",
        {"themeMode": "comic"},
        "day",
        id="species-radar-comic",
    ),
    pytest.param(
        "steam_charts",
        {"themeMode": "midnight"},
        "night",
        id="steam-charts-midnight",
    ),
    pytest.param(
        "tech_pulse",
        {"themeMode": "paper"},
        "day",
        id="tech-pulse-paper",
    ),
    pytest.param(
        "us_tv_hot_shows",
        {"themeMode": "streaming"},
        "night",
        id="us-tv-streaming",
    ),
    pytest.param(
        "daily_wiki_page",
        {"theme": "paper"},
        "day",
        id="daily-wiki-theme-alias",
    ),
    pytest.param(
        "sports_dashboard",
        {"sportsDashboardTheme": "night"},
        "night",
        id="sports-dashboard-theme-alias",
    ),
]


@pytest.mark.parametrize(
    ("plugin_id", "saved_settings", "expected_theme"),
    BUILTIN_THEME_SETTINGS,
)
def test_builtin_theme_settings_render_one_canonical_selector(
    theme_page_client_factory,
    plugin_id,
    saved_settings,
    expected_theme,
):
    client = theme_page_client_factory(
        plugin_id=plugin_id,
        settings=saved_settings,
        settings_template=f"{plugin_id}/settings.html",
    )

    response = client.get(f"/plugin/{plugin_id}?instance=Home")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    _assert_selected_theme(html, expected_theme)
    settings_form = _settings_form_html(html)
    assert re.search(r'name\s*=\s*["\']theme["\']', settings_form) is None
    assert re.search(r'id\s*=\s*["\']theme["\']', settings_form) is None
    assert "sportsDashboardTheme" not in settings_form
    assert re.search(
        r'getElementById\(\s*["\']themeMode["\']',
        settings_form,
    ) is None
    assert re.search(r"\blet\s+themeMode\b", settings_form) is None
    assert re.search(r"\bthemeMode\s*:", settings_form) is None


@pytest.mark.parametrize(
    ("plugin_id", "saved_settings", "_expected_theme"),
    BUILTIN_THEME_SETTINGS,
)
def test_builtin_theme_settings_parse_submitted_night_once(
    theme_page_client_factory,
    plugin_id,
    saved_settings,
    _expected_theme,
):
    client = theme_page_client_factory(
        plugin_id=plugin_id,
        settings=saved_settings,
        settings_template=f"{plugin_id}/settings.html",
    )
    response = client.get(f"/plugin/{plugin_id}?instance=Home")
    assert response.status_code == 200

    submitted_fields = _submitted_select_fields(
        response.get_data(as_text=True),
        theme_mode="night",
    )
    assert submitted_fields.getlist("themeMode") == ["night"]
    parsed_form = app_utils.parse_form(submitted_fields)
    parsed_theme_fields = {
        key: parsed_form[key]
        for key in plugin_blueprint._PLUGIN_THEME_SETTING_KEYS
        if key in parsed_form
    }
    assert parsed_theme_fields == {"themeMode": "night"}


def test_new_plugin_page_defaults_theme_to_auto(theme_page_client_factory):
    client = theme_page_client_factory(settings={"themeMode": "night"})

    response = client.get("/plugin/themed")

    assert response.status_code == 200
    _assert_selected_theme(response.get_data(as_text=True), "auto")


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        pytest.param({}, "auto", id="missing"),
        pytest.param({"themeMode": "auto"}, "auto", id="canonical-auto"),
        pytest.param({"themeMode": "day"}, "day", id="canonical-day"),
        pytest.param({"themeMode": "night"}, "night", id="canonical-night"),
        pytest.param({"themeMode": " NIGHT "}, "night", id="trim-and-case"),
        pytest.param({"theme": "light"}, "day", id="alias-light"),
        pytest.param({"theme": "paper"}, "day", id="alias-paper"),
        pytest.param({"theme": "comic"}, "day", id="alias-comic"),
        pytest.param({"theme": "white"}, "day", id="alias-white"),
        pytest.param({"theme": "dark"}, "night", id="alias-dark"),
        pytest.param({"theme": "cinema"}, "night", id="alias-cinema"),
        pytest.param({"theme": "streaming"}, "night", id="alias-streaming"),
        pytest.param({"theme": "midnight"}, "night", id="alias-midnight"),
        pytest.param(
            {
                "themeMode": "day",
                "theme_mode": "night",
                "theme": "midnight",
                "sportsDashboardTheme": "night",
            },
            "day",
            id="canonical-precedence",
        ),
        pytest.param(
            {"theme_mode": "day", "theme": "night"},
            "day",
            id="snake-case-precedence",
        ),
        pytest.param(
            {"theme": "paper", "sportsDashboardTheme": "midnight"},
            "day",
            id="theme-precedence",
        ),
        pytest.param(
            {"sportsDashboardTheme": "midnight"},
            "night",
            id="sports-fallback",
        ),
        pytest.param(
            {"themeMode": "", "theme_mode": "night"},
            "auto",
            id="present-empty-does-not-fall-through",
        ),
        pytest.param(
            {"themeMode": "unknown", "theme_mode": "night"},
            "auto",
            id="present-unknown-does-not-fall-through",
        ),
        pytest.param(
            {"theme_mode": None, "theme": "day"},
            "auto",
            id="present-none-does-not-fall-through",
        ),
    ],
)
def test_plugin_page_selects_saved_theme_by_key_presence(
    theme_page_client_factory,
    settings,
    expected,
):
    client = theme_page_client_factory(settings=settings)

    response = client.get("/plugin/themed?instance=Home")

    assert response.status_code == 200
    _assert_selected_theme(response.get_data(as_text=True), expected)


def test_plugin_page_omits_theme_selector_when_capability_is_false(
    theme_page_client_factory,
):
    client = theme_page_client_factory(
        settings={"themeMode": "night"},
        supports_day_night_theme=False,
    )

    response = client.get("/plugin/themed?instance=Home")

    assert response.status_code == 200
    assert _theme_selector(response.get_data(as_text=True)) is None


@pytest.mark.parametrize("refresh_settings", INVALID_REFRESH_SETTINGS)
def test_update_rejects_invalid_refresh_before_any_effect(
    plugin_env,
    refresh_settings,
):
    before = plugin_env.inner_manager.snapshot_instance("home-uuid")

    response = plugin_env.client.put(
        "/update_plugin_instance/Home",
        data={
            "plugin_id": "weather",
            "refresh_settings": refresh_settings,
        },
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload == {
        "success": False,
        "error_code": "invalid_refresh_config",
        "error": payload["error"],
        "message": payload["error"],
    }
    assert plugin_env.inner_manager.snapshot_instance("home-uuid") == before
    assert plugin_env.events == []


@pytest.mark.parametrize(
    ("path", "request_kwargs", "submit_event"),
    [
        (
            "/update_now",
            {"data": {"plugin_id": "weather"}},
            "submit_manual",
        ),
        (
            "/display_plugin_instance",
            {"json": {
                "playlist_name": "Default",
                "plugin_id": "weather",
                "plugin_instance": "Home",
            }},
            "submit_display",
        ),
    ],
)
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (QueueFullError("queue full"), 429, "refresh_queue_full"),
        (
            QueueStoppingError("refresh service stopping"),
            503,
            "refresh_service_stopping",
        ),
    ],
)
def test_queue_errors_have_stable_retryable_http_contract(
    plugin_env,
    path,
    request_kwargs,
    submit_event,
    error,
    expected_status,
    expected_code,
):
    plugin_env.task.queue_error = error

    response = plugin_env.client.post(path, **request_kwargs)

    assert response.status_code == expected_status
    assert response.headers["Retry-After"] == "5"
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["error_code"] == expected_code
    assert payload["error"] == str(error)
    assert payload["message"] == str(error)
    assert payload["job"] is None
    assert payload["job_id"] is None
    assert any(event[0] == submit_event for event in plugin_env.events)


def test_queue_error_with_job_record_is_serialized_without_enum(plugin_env):
    rejected = JobRecord(
        id="rejected-job",
        command_id="rejected-command",
        status=JobStatus.REJECTED,
        submitted_at=1.0,
        completed_at=2.0,
        error_code="refresh_queue_full",
        error="queue full",
    )
    plugin_env.task.queue_error = QueueFullError("queue full", rejected)

    response = plugin_env.client.post(
        "/update_now",
        data={"plugin_id": "weather"},
    )

    assert response.status_code == 429
    payload = response.get_json()
    assert payload["job_id"] == "rejected-job"
    assert payload["job"]["status"] == "rejected"
    assert payload["job"]["error_code"] == "refresh_queue_full"


@pytest.mark.parametrize(
    ("path", "request_kwargs", "expected_submit"),
    [
        (
            "/update_now",
            {"data": {"plugin_id": "weather", "units": "metric"}},
            "submit_manual",
        ),
        (
            "/display_plugin_instance",
            {"json": {
                "playlist_name": "Default",
                "plugin_id": "weather",
                "plugin_instance": "Home",
            }},
            "submit_display",
        ),
    ],
)
def test_queue_acceptance_preserves_existing_202_shape(
    plugin_env,
    path,
    request_kwargs,
    expected_submit,
):
    response = plugin_env.client.post(path, **request_kwargs)

    assert response.status_code == 202
    assert response.get_json() == {
        "success": True,
        "message": "Display update queued",
        "job": plugin_env.task.job,
        "job_id": "job-1",
        "status_url": "/refresh_job/job-1",
    }
    assert any(event[0] == expected_submit for event in plugin_env.events)


def test_generic_rejected_job_is_not_blanket_mapped_to_503(plugin_env):
    plugin_env.task.running = False
    plugin_env.task.job = {
        "id": "rejected-job",
        "status": "rejected",
        "error_code": "invalid_refresh_command",
        "error": "invalid command",
    }

    response = plugin_env.client.post(
        "/update_now",
        data={"plugin_id": "weather"},
    )

    assert response.status_code == 400
    assert response.get_json()["error_code"] == "invalid_refresh_command"


def test_display_submits_only_the_resolved_immutable_uuid(plugin_env):
    response = plugin_env.client.post(
        "/display_plugin_instance",
        json={
            "playlist_name": "Default",
            "plugin_id": "weather",
            "plugin_instance": "Home",
        },
    )

    assert response.status_code == 202
    submit = next(event for event in plugin_env.events if event[0] == "submit_display")
    assert submit == (
        "submit_display",
        "home-uuid",
        {
            "force": True,
            "display_cached_only": False,
            "expected_playlist_name": "Default",
            "expected_generation": 1,
            "expected_settings_revision": 1,
            "require_active": False,
        },
    )
    assert not any(event[0] == "submit_manual" for event in plugin_env.events)


def test_update_uses_resolved_cas_then_cancel_write_and_signal(plugin_env):
    queued_revisions = []
    before = plugin_env.inner_manager.snapshot_instance("home-uuid")
    plugin_env.task.signal_callback = lambda: queued_revisions.append(
        plugin_env.inner_manager.snapshot_instance("home-uuid").settings_revision
    )

    response = plugin_env.client.put(
        "/update_plugin_instance/Home",
        data={
            "plugin_id": "weather",
            "units": "imperial",
            "refresh_settings": json.dumps({
                "refreshType": "interval",
                "unit": "minute",
                "interval": "10",
            }),
        },
    )

    assert response.status_code == 200
    after = plugin_env.inner_manager.snapshot_instance("home-uuid")
    assert after.settings == {"units": "imperial"}
    assert after.refresh == {"interval": 600}
    assert after.settings_revision == before.settings_revision + 1
    assert plugin_env.manager.update_calls == [(
        "home-uuid",
        {
            "settings": {"units": "imperial"},
            "refresh": {"interval": 600},
            "expected_generation": before.structural_generation,
            "expected_settings_revision": before.settings_revision,
        },
    )]
    names = [event[0] for event in plugin_env.events]
    assert names.index("mutation") < names.index("cancel")
    assert names.index("cancel") < names.index("write")
    assert names.index("write") < names.index("signal")
    assert queued_revisions == [after.settings_revision]
    assert plugin_env.task.refresh_queue.canceled == ["home-uuid"]


def test_resolve_then_recreate_same_name_cannot_update_replacement(
    plugin_env,
    monkeypatch,
):
    before = plugin_env.inner_manager.snapshot_instance("home-uuid")

    def recreate_before_cas(instance_uuid, **kwargs):
        plugin_env.inner_manager.delete_plugin_instance_atomic(
            instance_uuid,
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
        )
        replacement = plugin_env.inner_manager.add_plugin_to_playlist_snapshot(
            "Default",
            {
                "plugin_id": "weather",
                "name": "Home",
                "plugin_settings": {"units": "replacement"},
                "refresh": {"interval": 300},
            },
        )
        assert replacement.instance.instance_uuid != instance_uuid
        return plugin_env.inner_manager.update_plugin_instance_atomic(
            instance_uuid,
            **kwargs,
        )

    monkeypatch.setattr(
        plugin_env.manager,
        "update_plugin_instance_atomic",
        recreate_before_cas,
    )

    response = plugin_env.client.put(
        "/update_plugin_instance/Home",
        data={
            "plugin_id": "weather",
            "units": "imperial",
            "refresh_settings": json.dumps({
                "refreshType": "interval",
                "unit": "minute",
                "interval": "10",
            }),
        },
    )

    assert response.status_code == 400
    replacement = plugin_env.inner_manager.resolve_plugin_instance_snapshot(
        "Default",
        "weather",
        "Home",
    ).instance
    assert replacement.settings == {"units": "replacement"}
    assert plugin_env.task.refresh_queue.canceled == []
    assert not any(event[0] in {"write", "signal"} for event in plugin_env.events)


def test_plugin_instance_image_serves_versioned_cache_not_legacy_alias(plugin_env):
    versioned = plugin_env.tmp_path / ".refresh-cache" / "current-revision.png"
    versioned.parent.mkdir()
    versioned.write_bytes(b"versioned")
    (plugin_env.tmp_path / "weather_Home.png").write_bytes(b"legacy")
    plugin_env.task.cache_path_for_snapshot = lambda _snapshot: str(versioned)

    response = plugin_env.client.get(
        "/plugin_instance_image/Default/weather/Home"
    )

    assert response.status_code == 200
    assert response.data == b"versioned"


def _create_cleanup_files(plugin_env):
    canonical = plugin_env.tmp_path / "weather_Home.png"
    staged = plugin_env.tmp_path / "home-uuid-staged.png"
    canonical.write_bytes(b"canonical")
    staged.write_bytes(b"staged")
    plugin_env.task.managed_paths = (str(staged),)
    return canonical, staged


def test_delete_orders_cancel_retry_persist_arbitrated_cleanup_then_signal(
    plugin_env,
    monkeypatch,
):
    canonical, staged = _create_cleanup_files(plugin_env)
    cleanup_settings = []

    class Plugin:
        def cleanup(self, settings):
            assert plugin_env.task.render_arbiter.inside
            cleanup_settings.append(settings)
            settings["mutated_by_cleanup"] = True
            plugin_env.events.append(("plugin_cleanup",))

    monkeypatch.setattr(plugin_blueprint, "get_plugin_instance", lambda _config: Plugin())
    original_remove = plugin_blueprint.os.remove

    def guarded_remove(path):
        assert plugin_env.task.render_arbiter.inside
        plugin_env.events.append(("remove", Path(path).name))
        original_remove(path)

    monkeypatch.setattr(plugin_blueprint.os, "remove", guarded_remove)

    response = plugin_env.client.post(
        "/delete_plugin_instance",
        json={
            "playlist_name": "Default",
            "plugin_id": "weather",
            "plugin_instance": "Home",
        },
    )

    assert response.status_code == 200
    assert plugin_env.inner_manager.snapshot_instance("home-uuid") is None
    assert plugin_env.task.refresh_queue.canceled == ["home-uuid"]
    assert plugin_env.task.retry_registry.discarded == ["home-uuid"]
    assert not canonical.exists()
    assert not staged.exists()
    assert cleanup_settings == [{"units": "metric", "mutated_by_cleanup": True}]
    assert plugin_env.task.render_arbiter.plugin_ids == ["weather"]
    names = [event[0] for event in plugin_env.events]
    assert names.index("mutation") < names.index("cancel")
    assert names.index("cancel") < names.index("retry_discard")
    assert names.index("retry_discard") < names.index("write")
    assert names.index("write") < names.index("lease_enter")
    assert names.index("lease_exit") < names.index("signal")


def test_delete_write_failure_never_performs_irreversible_cleanup(
    plugin_env,
    monkeypatch,
):
    canonical, staged = _create_cleanup_files(plugin_env)
    plugin_env.device_config.fail_write = True
    cleanup_called = False

    class Plugin:
        def cleanup(self, _settings):
            nonlocal cleanup_called
            cleanup_called = True

    monkeypatch.setattr(plugin_blueprint, "get_plugin_instance", lambda _config: Plugin())

    response = plugin_env.client.post(
        "/delete_plugin_instance",
        json={
            "playlist_name": "Default",
            "plugin_id": "weather",
            "plugin_instance": "Home",
        },
    )

    assert response.status_code == 500
    assert plugin_env.task.refresh_queue.canceled == ["home-uuid"]
    assert plugin_env.task.retry_registry.discarded == ["home-uuid"]
    assert canonical.exists()
    assert staged.exists()
    assert cleanup_called is False
    assert not any(event[0] in {"lease_enter", "remove", "signal"} for event in plugin_env.events)


def test_delete_cleanup_waits_for_shared_render_lease(plugin_env, monkeypatch):
    canonical, _staged = _create_cleanup_files(plugin_env)
    arbiter = RenderArbiter()
    plugin_env.task.render_arbiter = arbiter
    cleaned = threading.Event()
    response_holder = []

    class Plugin:
        def cleanup(self, _settings):
            cleaned.set()

    monkeypatch.setattr(plugin_blueprint, "get_plugin_instance", lambda _config: Plugin())

    def request_delete():
        with plugin_env.app.test_client() as client:
            response_holder.append(client.post(
                "/delete_plugin_instance",
                json={
                    "playlist_name": "Default",
                    "plugin_id": "weather",
                    "plugin_instance": "Home",
                },
            ))

    render_context = TaskContext(
        threading.Event(),
        time.monotonic() + 2.0,
        time.monotonic,
    )
    worker = threading.Thread(target=request_delete)
    with arbiter.lease("weather", render_context):
        worker.start()
        assert plugin_env.device_config.write_event.wait(1.0)
        time.sleep(0.05)
        assert worker.is_alive()
        assert canonical.exists()
        assert not cleaned.is_set()

    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert response_holder[0].status_code == 200
    assert cleaned.is_set()
    assert not canonical.exists()


def test_old_cleanup_cannot_delete_same_name_replacement_cache(
    plugin_env,
    monkeypatch,
):
    legacy_alias, old_cache = _create_cleanup_files(plugin_env)
    new_cache = plugin_env.tmp_path / ".refresh-cache" / "replacement.png"
    new_cache.parent.mkdir()
    new_cache.write_bytes(b"replacement")
    shared_resource = plugin_env.tmp_path / "shared.png"
    shared_resource.write_bytes(b"replacement-owned")
    old_snapshot = plugin_env.inner_manager.snapshot_instance("home-uuid")
    updated = plugin_env.inner_manager.update_plugin_instance_atomic(
        "home-uuid",
        settings={"imageFiles[]": [str(shared_resource)]},
        expected_generation=old_snapshot.structural_generation,
        expected_settings_revision=old_snapshot.settings_revision,
    )
    assert updated is not None
    original_write = plugin_env.device_config.write_config

    def persist_then_recreate():
        original_write()
        replacement = plugin_env.inner_manager.add_plugin_to_playlist_snapshot(
            "Default",
            {
                "plugin_id": "weather",
                "name": "Home",
                "plugin_settings": {"imageFiles[]": [str(shared_resource)]},
                "refresh": {"interval": 300},
            },
        )
        assert replacement is not None
        assert replacement.instance.instance_uuid != "home-uuid"

    monkeypatch.setattr(
        plugin_env.device_config,
        "write_config",
        persist_then_recreate,
    )

    cleanup_called = False

    class Plugin:
        def cleanup(self, settings):
            nonlocal cleanup_called
            cleanup_called = True
            for path in settings.get("imageFiles[]", []):
                Path(path).unlink()

    monkeypatch.setattr(
        plugin_blueprint,
        "get_plugin_instance",
        lambda _config: Plugin(),
    )

    response = plugin_env.client.post(
        "/delete_plugin_instance",
        json={
            "playlist_name": "Default",
            "plugin_id": "weather",
            "plugin_instance": "Home",
        },
    )

    assert response.status_code == 200
    assert not old_cache.exists()
    assert new_cache.read_bytes() == b"replacement"
    assert legacy_alias.exists()
    assert shared_resource.read_bytes() == b"replacement-owned"
    assert cleanup_called is False


def test_cleanup_failure_releases_lease_and_keeps_durable_delete_successful(
    plugin_env,
    monkeypatch,
):
    _create_cleanup_files(plugin_env)
    arbiter = RenderArbiter()
    plugin_env.task.render_arbiter = arbiter

    class Plugin:
        def cleanup(self, _settings):
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(plugin_blueprint, "get_plugin_instance", lambda _config: Plugin())

    response = plugin_env.client.post(
        "/delete_plugin_instance",
        json={
            "playlist_name": "Default",
            "plugin_id": "weather",
            "plugin_instance": "Home",
        },
    )

    assert response.status_code == 200
    assert any(event[0] == "signal" for event in plugin_env.events)
    context = TaskContext(
        threading.Event(),
        time.monotonic() + 1.0,
        time.monotonic,
    )
    with arbiter.lease("weather", context):
        pass


def test_missing_delete_mutation_has_no_cancel_write_cleanup_or_signal(plugin_env):
    response = plugin_env.client.post(
        "/delete_plugin_instance",
        json={
            "playlist_name": "Default",
            "plugin_id": "weather",
            "plugin_instance": "Missing",
        },
    )

    assert response.status_code == 400
    assert not any(
        event[0] in {
            "mutation",
            "cancel",
            "retry_discard",
            "write",
            "lease_enter",
            "signal",
        }
        for event in plugin_env.events
    )


def test_refresh_job_endpoint_retains_200_and_404_behavior(plugin_env):
    found = plugin_env.client.get("/refresh_job/job-1")
    missing = plugin_env.client.get("/refresh_job/missing")

    assert found.status_code == 200
    assert found.get_json() == {"success": True, "job": plugin_env.task.job}
    assert missing.status_code == 404
    assert missing.get_json()["success"] is False


def test_refresh_job_endpoint_serializes_job_record_and_enum(
    plugin_env,
    monkeypatch,
):
    serialized = []
    original_serialize = plugin_blueprint._serialize_job

    def record_serialization(job):
        serialized.append(job)
        return original_serialize(job)

    monkeypatch.setattr(plugin_blueprint, "_serialize_job", record_serialization)
    plugin_env.task.jobs["completed"] = JobRecord(
        id="completed",
        command_id="command",
        status=JobStatus.SUCCEEDED,
        submitted_at=1.0,
        completed_at=2.0,
    )

    response = plugin_env.client.get("/refresh_job/completed")

    assert response.status_code == 200
    assert response.get_json()["job"]["status"] == "succeeded"
    assert serialized == [plugin_env.task.jobs["completed"]]


def test_mutation_routes_have_no_live_field_writes_or_direct_render_bypass():
    source_path = Path(plugin_blueprint.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_attributes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        forbidden_attributes.extend(
            target.attr
            for target in targets
            if isinstance(target, ast.Attribute)
            and target.attr in {"plugins", "settings", "refresh"}
        )

    assert forbidden_attributes == []
    assert "PlaylistRefresh" not in source
    assert ".generate_image(" not in source
    assert ".display_image(" not in source


@pytest.mark.parametrize(
    ("path", "method", "data"),
    [
        (
            "/update_plugin_instance/Home",
            "put",
            {
                "plugin_id": "weather",
                "refresh_settings": json.dumps({
                    "refreshType": "interval",
                    "unit": "minute",
                    "interval": "10",
                }),
            },
        ),
        ("/update_now", "post", {"plugin_id": "weather"}),
        (
            "/add_plugin",
            "post",
            {
                "plugin_id": "weather",
                "refresh_settings": json.dumps({
                    "refreshType": "interval",
                    "unit": "minute",
                    "interval": "5",
                    "playlist": "Default",
                    "instance_name": "Office",
                }),
            },
        ),
    ],
)
def test_all_upload_entrypoints_reject_invalid_content_without_partial_files(
    plugin_env,
    monkeypatch,
    path,
    method,
    data,
):
    if path == "/add_plugin":
        plugin_env.app.register_blueprint(playlist_blueprint.playlist_bp)
    saved = plugin_env.tmp_path / "saved"
    saved.mkdir()
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))
    data["imageFile"] = (BytesIO(b"not-a-png"), "image.png", "image/png")

    response = getattr(plugin_env.client, method)(
        path,
        data=data,
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["error_code"] == "upload_content_mismatch"
    assert list(saved.iterdir()) == []
    assert not any(event[0] in {"mutation", "submit_manual", "write"} for event in plugin_env.events)


@pytest.mark.parametrize(
    ("path", "method", "data"),
    [
        (
            "/update_plugin_instance/Home",
            "put",
            {
                "plugin_id": "weather",
                "refresh_settings": json.dumps({
                    "refreshType": "interval",
                    "unit": "minute",
                    "interval": "10",
                }),
            },
        ),
        ("/update_now", "post", {"plugin_id": "weather"}),
        (
            "/add_plugin",
            "post",
            {
                "plugin_id": "weather",
                "refresh_settings": json.dumps({
                    "refreshType": "interval",
                    "unit": "minute",
                    "interval": "5",
                    "playlist": "Default",
                    "instance_name": "Office",
                }),
            },
        ),
    ],
)
def test_all_upload_entrypoints_preserve_upload_error_contract_from_route_processing(
    plugin_env,
    monkeypatch,
    path,
    method,
    data,
):
    target_module = plugin_blueprint
    if path == "/add_plugin":
        plugin_env.app.register_blueprint(playlist_blueprint.playlist_bp)
        target_module = playlist_blueprint

    def reject_upload(*_args, **_kwargs):
        raise UploadTooLarge("normalized upload exceeds the configured limit")

    monkeypatch.setattr(target_module, "prepare_request_files", reject_upload)

    response = getattr(plugin_env.client, method)(path, data=data)

    assert response.status_code == 413
    assert response.get_json()["error_code"] == "upload_too_large"
    assert not any(
        event[0] in {"mutation", "submit_manual", "write"}
        for event in plugin_env.events
    )


def test_update_now_rejects_file_over_five_mib_without_partial_files(
    plugin_env,
    monkeypatch,
):
    saved = plugin_env.tmp_path / "saved"
    saved.mkdir()
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))

    response = plugin_env.client.post(
        "/update_now",
        data={
            "plugin_id": "weather",
            "dataFile": (
                BytesIO(b"x" * (5 * 1024 * 1024 + 1)),
                "data.csv",
                "text/csv",
            ),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert response.get_json()["error_code"] == "upload_too_large"
    assert list(saved.iterdir()) == []
    assert not any(event[0] == "submit_manual" for event in plugin_env.events)


def test_stale_update_upload_never_overwrites_existing_file(
    plugin_env,
    monkeypatch,
):
    saved = plugin_env.tmp_path / "saved"
    saved.mkdir()
    victim = saved / "victim.png"
    victim.write_bytes(b"old-content")
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))
    monkeypatch.setattr(
        plugin_env.manager,
        "update_plugin_instance_atomic",
        lambda *_args, **_kwargs: None,
    )

    response = plugin_env.client.put(
        "/update_plugin_instance/Home",
        data={
            "plugin_id": "weather",
            "units": "imperial",
            "refresh_settings": json.dumps({
                "refreshType": "interval",
                "unit": "minute",
                "interval": "10",
            }),
            "imageFile": (BytesIO(_png_bytes()), "victim.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert victim.read_bytes() == b"old-content"
    assert sorted(path.name for path in saved.iterdir()) == ["victim.png"]


def test_queue_rejected_manual_upload_is_rolled_back(plugin_env, monkeypatch):
    saved = plugin_env.tmp_path / "saved"
    saved.mkdir()
    victim = saved / "victim.png"
    victim.write_bytes(b"old-content")
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))
    plugin_env.task.queue_error = QueueFullError("full")

    response = plugin_env.client.post(
        "/update_now",
        data={
            "plugin_id": "weather",
            "imageFile": (BytesIO(_png_bytes()), "victim.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 429
    assert victim.read_bytes() == b"old-content"
    assert sorted(path.name for path in saved.iterdir()) == ["victim.png"]


def test_update_write_failure_keeps_live_model_upload_owned(
    plugin_env,
    monkeypatch,
):
    saved = plugin_env.tmp_path / "saved"
    saved.mkdir()
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))
    before = plugin_env.inner_manager.snapshot_instance("home-uuid")
    plugin_env.device_config.fail_write = True

    response = plugin_env.client.put(
        "/update_plugin_instance/Home",
        data={
            "plugin_id": "weather",
            "refresh_settings": json.dumps({
                "refreshType": "interval",
                "unit": "minute",
                "interval": "10",
            }),
            "imageFile": (BytesIO(_png_bytes()), "replacement.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 500
    current = plugin_env.inner_manager.snapshot_instance("home-uuid")
    assert current.settings_revision == before.settings_revision + 1
    referenced = Path(current.settings["imageFile"])
    assert referenced.read_bytes() == _png_bytes()
    assert not list(saved.glob(".*.pending-*"))
    assert not any(event[0] == "signal" for event in plugin_env.events)


def test_manual_preview_transfers_upload_to_job_lifecycle(
    plugin_env,
    monkeypatch,
):
    saved = plugin_env.tmp_path / "saved"
    saved.mkdir()
    monkeypatch.setattr(app_utils, "resolve_path", lambda _path: str(saved))

    response = plugin_env.client.post(
        "/update_now",
        data={
            "plugin_id": "weather",
            "imageFile": (BytesIO(_png_bytes()), "preview.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    assert len(plugin_env.task.transient_paths) == 1
    owned = Path(plugin_env.task.transient_paths[0])
    assert owned.read_bytes() == _png_bytes()
    assert not list(saved.glob(".*.pending-*"))


def test_cleanup_serializes_same_name_replacement_add(
    plugin_env,
    monkeypatch,
):
    plugin_env.app.register_blueprint(playlist_blueprint.playlist_bp)
    plugin_env.task.render_arbiter = RenderArbiter()
    shared_resource = plugin_env.tmp_path / "shared.png"
    shared_resource.write_bytes(b"old-owner")
    initial = plugin_env.inner_manager.snapshot_instance("home-uuid")
    updated = plugin_env.inner_manager.update_plugin_instance_atomic(
        "home-uuid",
        settings={"imageFiles[]": [str(shared_resource)]},
        expected_generation=initial.structural_generation,
        expected_settings_revision=initial.settings_revision,
    )
    old_snapshot = updated.new_snapshot
    removed = plugin_env.inner_manager.delete_plugin_instance_atomic(
        "home-uuid",
        expected_generation=old_snapshot.structural_generation,
        expected_settings_revision=old_snapshot.settings_revision,
    ).old_snapshot

    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()
    cleanup_done = threading.Event()
    add_ready = threading.Event()
    add_done = threading.Event()
    add_responses = []

    class BlockingPlugin:
        def cleanup(self, settings):
            cleanup_entered.set()
            assert release_cleanup.wait(timeout=2.0)
            Path(settings["imageFiles[]"][0]).unlink()

    monkeypatch.setattr(
        plugin_blueprint,
        "get_plugin_instance",
        lambda _config: BlockingPlugin(),
    )
    original_prepare = playlist_blueprint.prepare_request_files

    def prepare_then_signal(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        add_ready.set()
        return prepared

    monkeypatch.setattr(
        playlist_blueprint,
        "prepare_request_files",
        prepare_then_signal,
    )

    def run_cleanup():
        with plugin_env.app.app_context():
            plugin_blueprint._cleanup_plugin_instance_snapshot(
                plugin_env.device_config,
                plugin_env.task,
                removed,
            )
        cleanup_done.set()

    def run_add():
        with plugin_env.app.test_client() as client:
            add_responses.append(client.post(
                "/add_plugin",
                data={
                    "plugin_id": "weather",
                    "refresh_settings": json.dumps({
                        "refreshType": "interval",
                        "unit": "minute",
                        "interval": "5",
                        "playlist": "Default",
                        "instance_name": "Home",
                    }),
                    "imageFiles[]": str(shared_resource),
                },
            ))
        add_done.set()

    cleanup_thread = threading.Thread(target=run_cleanup)
    add_thread = threading.Thread(target=run_add)
    cleanup_thread.start()
    assert cleanup_entered.wait(timeout=2.0)
    add_thread.start()
    assert add_ready.wait(timeout=2.0)
    assert not plugin_env.manager.add_mutated.wait(timeout=0.2)

    release_cleanup.set()
    cleanup_thread.join(timeout=2.0)
    add_thread.join(timeout=2.0)

    assert cleanup_done.is_set()
    assert add_done.is_set()
    assert add_responses[0].status_code == 400
    assert plugin_env.inner_manager.resolve_plugin_instance_snapshot(
        "Default",
        "weather",
        "Home",
    ) is None
    assert not shared_resource.exists()
