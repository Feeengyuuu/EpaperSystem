from datetime import datetime, timedelta, timezone
import base64
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
from pathlib import Path
import sys

from flask import Flask
from PIL import Image
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[4] / "tools" / "live_all_instances_acceptance.py"


@pytest.fixture(scope="module")
def acceptance():
    assert SCRIPT_PATH.is_file(), "live all-instances acceptance script is missing"
    spec = spec_from_file_location("live_all_instances_acceptance", SCRIPT_PATH)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _config(count=26, *, duplicate_uuid=False):
    plugins = []
    for index in range(count):
        instance_uuid = f"{index + 1:032x}"
        if duplicate_uuid and index == count - 1:
            instance_uuid = f"{1:032x}"
        plugins.append({
            "plugin_id": f"plugin_{index:02d}",
            "name": f"Private instance {index}",
            "plugin_settings": {
                "secret": f"secret-{index}",
                "refreshOnDisplay": False,
            },
            "refresh": {"interval": 300},
            "instance_uuid": instance_uuid,
            "structural_generation": 2,
            "settings_revision": 3,
        })
    return {
        "timezone": "UTC",
        "playlist_config": {
            "active_playlist": "Factory",
            "playlists": [{
                "name": "Factory",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": plugins,
            }],
        },
    }


def _png_bytes(color=(10, 20, 30), size=(800, 480)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _instance(acceptance):
    return acceptance.build_acceptance_plan(
        _config(),
        now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )[0]


def _runtime(
    instance,
    timestamp,
    *,
    request=None,
    receipt=None,
    commit_id="a" * 32,
    presentation_success=None,
):
    return {
        "schema_version": 4,
        "updated_at": timestamp,
        "display": {
            "state": "committed",
            "commit_id": commit_id,
            "instance_uuid": instance.instance_uuid,
        },
        "instances": {
            instance.instance_uuid: {
                "lanes": {
                    "data": {
                        "last_attempt_at": timestamp,
                        "last_success_at": timestamp,
                        "last_failure_at": None,
                        "last_error": None,
                        "next_retry_at": None,
                    },
                    "live": {},
                    "theme": {},
                    "presentation": {
                        "last_success_at": presentation_success,
                        "next_retry_at": None,
                    },
                },
                "last_good_cache": {
                    "theme_mode": "day",
                    "structural_generation": instance.structural_generation,
                    "settings_revision": instance.settings_revision,
                    "promoted_at": timestamp,
                },
                "presentation_request": request,
                "presentation_receipt": receipt,
            },
        },
    }


def _presentation_request(instance, timestamp, *, request_id="request-123"):
    return {
        "request_id": request_id,
        "requested_at": timestamp,
        "origin_display_commit_id": "origin-display",
        "origin_theme_mode": "day",
        "structural_generation": instance.structural_generation,
        "settings_revision": instance.settings_revision,
        "prepared_at": None,
        "prepared_theme_mode": None,
    }


def _manifest(instance, pixel_hash, timestamp, *, commit_id="a" * 32, hardware=True):
    return {
        "schema_version": 1,
        "commit_id": commit_id,
        "image": f"objects/{commit_id}.png",
        "pixel_hash": pixel_hash,
        "hardware_fingerprint": "safe-fingerprint",
        "logical_target": {
            "kind": "playlist",
            "playlist": instance.playlist_name,
            "plugin_id": instance.plugin_id,
            "plugin_instance": "Private instance 0",
            "instance_uuid": instance.instance_uuid,
        },
        "instance_revision": [
            instance.structural_generation,
            instance.settings_revision,
        ],
        "image_settings": [],
        "hardware_written": hardware,
        "committed_at": timestamp,
    }


@pytest.mark.parametrize(
    ("config", "expected_code"),
    [
        (_config(25), "config_instance_count"),
        (_config(26, duplicate_uuid=True), "config_duplicate_uuid"),
    ],
)
def test_plan_has_strict_26_instance_and_unique_uuid_gate(
    acceptance,
    config,
    expected_code,
):
    with pytest.raises(acceptance.AuditAbort) as captured:
        acceptance.build_acceptance_plan(
            config,
            now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        )

    assert captured.value.code == expected_code


def test_plan_selects_current_priority_winner_and_keeps_private_name_internal(acceptance):
    config = _config()
    config["playlist_config"]["playlists"].insert(0, {
        "name": "All day",
        "start_time": "00:00",
        "end_time": "24:00",
        "plugins": [],
    })
    config["playlist_config"]["playlists"][1].update({
        "start_time": "11:00",
        "end_time": "13:00",
    })

    plan = acceptance.build_acceptance_plan(
        config,
        now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )

    assert len(plan) == 26
    assert plan[0].playlist_name == "Factory"
    assert plan[0].instance_name == "Private instance 0"
    assert plan[0].expects_presentation_refresh is False
    assert "Private instance" not in json.dumps(plan[0].safe_identity())


def test_plan_resolves_expected_presentation_from_settings_and_manifest(
    acceptance,
    tmp_path,
):
    config = _config()
    first = config["playlist_config"]["playlists"][0]["plugins"][0]
    first["plugin_settings"]["refreshOnDisplay"] = True
    manifest_dir = tmp_path / first["plugin_id"]
    manifest_dir.mkdir()
    (manifest_dir / "plugin-info.json").write_text(
        json.dumps({
            "id": first["plugin_id"],
            "refresh_on_display": False,
            "capabilities": {"supports_presentation_refresh": True},
        }),
        encoding="utf-8",
    )

    plan = acceptance.build_acceptance_plan(
        config,
        now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
        plugin_root=tmp_path,
    )

    assert plan[0].expects_presentation_refresh is True
    assert plan[1].expects_presentation_refresh is False


def test_png_evidence_uses_rgb_pixels_and_requires_800_by_480(acceptance):
    payload = _png_bytes()

    evidence = acceptance.inspect_png(payload)

    expected_hash = hashlib.sha256(
        Image.open(BytesIO(payload)).convert("RGB").tobytes()
    ).hexdigest()
    assert evidence.width == 800
    assert evidence.height == 480
    assert evidence.pixel_hash == expected_hash
    assert evidence.png_sha256 == hashlib.sha256(payload).hexdigest()

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.inspect_png(_png_bytes(size=(10, 10)))
    assert captured.value.code == "image_dimensions_mismatch"


class _Response:
    def __init__(self, payload, status_code=200, headers=None, content=b""):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content

    def json(self):
        return self._payload


class _PollingSession:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return _Response({"success": True, "job": self.jobs.pop(0)})


def test_job_polling_waits_until_terminal_without_copying_raw_error(acceptance):
    session = _PollingSession([
        {"id": "job-1", "status": "queued"},
        {"id": "job-1", "status": "running"},
        {
            "id": "job-1",
            "status": "failed",
            "error_code": "provider_failed",
            "error": "private message text must not escape",
            "settings": {"token": "secret"},
        },
    ])
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    job = acceptance.poll_job(
        session,
        "http://127.0.0.1",
        "job-1",
        timeout_seconds=10,
        monotonic=lambda: clock[0],
        sleep=sleep,
        poll_interval=1,
    )

    assert job["status"] == "failed"
    assert len(session.calls) == 3
    encoded = json.dumps(acceptance.safe_job_record(job))
    assert "provider_failed" in encoded
    assert "private message" not in encoded
    assert "secret" not in encoded
    assert '"settings":' not in encoded


def test_safe_job_record_is_idempotent_and_keeps_the_job_hash(acceptance):
    raw = {
        "id": "private-job-id",
        "status": "failed",
        "error_code": "provider_failed",
        "error": "private provider content",
    }

    first = acceptance.safe_job_record(raw)
    second = acceptance.safe_job_record(first)

    assert second == first
    assert second["job_id_hash"] == acceptance.hash_identifier("private-job-id")
    assert "private-job-id" not in json.dumps(second)
    assert "private provider content" not in json.dumps(second)


class _SubmitSession:
    def post(self, _url, **_kwargs):
        return _Response({
            "success": True,
            "job_id": "job-created-by-api",
            "job": {"status": "queued"},
        })


def test_submit_job_normalizes_the_authoritative_job_id(acceptance):
    job = acceptance.submit_job(
        _SubmitSession(),
        "http://127.0.0.1",
        "/refresh_plugin_instance",
        _instance(acceptance),
    )

    assert job["id"] == "job-created-by-api"
    assert job["status"] == "queued"


def test_admin_session_cookie_contains_only_admin_csrf_and_permanent_state(acceptance):
    session, csrf_token = acceptance.build_admin_session("unit-test-flask-secret")
    cookie = session.cookies.get("session")
    app = Flask(__name__)
    app.secret_key = "unit-test-flask-secret"
    decoded = app.session_interface.get_signing_serializer(app).loads(cookie)

    assert decoded == {
        "admin_identity": "admin",
        "csrf_token": csrf_token,
        "_permanent": True,
    }
    assert session.headers["X-CSRF-Token"] == csrf_token


class _ReadySession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, _url, **_kwargs):
        self.calls += 1
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def _runner_for_ready_test(acceptance, tmp_path, session, clock):
    return acceptance.AcceptanceRunner(
        session=session,
        base_url="http://127.0.0.1",
        config_path=tmp_path / "unused-config.json",
        runtime_state_path=tmp_path / "unused-runtime.json",
        display_manifest_path=tmp_path / "unused-manifest.json",
        output_dir=tmp_path / "evidence",
        monotonic=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )


def test_ready_retries_transient_503_then_accepts_recovery(
    acceptance,
    tmp_path,
):
    clock = [0.0]
    session = _ReadySession([
        _Response({"status": "not_ready"}, status_code=503),
        _Response({"status": "ready", "boot_id": "boot-1", "release_id": "r1"}),
    ])
    runner = _runner_for_ready_test(acceptance, tmp_path, session, clock)

    health = runner._ready()

    assert health["status"] == "ready"
    assert session.calls == 2
    assert clock[0] == acceptance.HEALTH_POLL_INTERVAL_SECONDS


def test_default_health_recovery_window_covers_heavy_physical_refresh(acceptance):
    assert acceptance.HEALTH_RETRY_SECONDS >= 90


def test_ready_requires_http_200_degraded_to_recover_at_strict_boundary(
    acceptance,
    tmp_path,
):
    clock = [0.0]
    session = _ReadySession([
        _Response({
            "status": "degraded",
            "boot_id": "boot-1",
            "error_codes": ["disk_low"],
        }),
        _Response({"status": "ready", "boot_id": "boot-1"}),
    ])
    runner = _runner_for_ready_test(acceptance, tmp_path, session, clock)

    health = runner._ready()

    assert health["status"] == "ready"
    assert session.calls == 2
    assert clock[0] == acceptance.HEALTH_POLL_INTERVAL_SECONDS


def test_ready_allows_only_allowlisted_degraded_reason_during_processing(
    acceptance,
    tmp_path,
):
    clock = [0.0]
    session = _ReadySession([_Response({
        "status": "degraded",
        "boot_id": "boot-1",
        "error_codes": ["queue_full"],
    })])
    runner = _runner_for_ready_test(acceptance, tmp_path, session, clock)

    health = runner._ready(allow_transient_degraded=True)

    assert health == {
        "status": "degraded",
        "release_id": "unknown",
        "boot_id_hash": acceptance.hash_identifier("boot-1"),
        "reason_codes": ["queue_full"],
    }
    assert runner._health_events[0]["reason_codes"] == ["queue_full"]
    assert "boot-1" not in json.dumps(runner._health_events)


def test_ready_rejects_nonallowlisted_degraded_reason_during_processing(
    acceptance,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(acceptance, "HEALTH_RETRY_SECONDS", 1)
    clock = [0.0]
    session = _ReadySession([_Response({
        "status": "degraded",
        "boot_id": "boot-1",
        "error_codes": ["startup_degraded"],
    })])
    runner = _runner_for_ready_test(acceptance, tmp_path, session, clock)

    with pytest.raises(acceptance.AuditAbort) as captured:
        runner._ready(allow_transient_degraded=True)

    assert captured.value.code == "health_not_ready"
    assert captured.value.safe_details["reason_codes"] == ["startup_degraded"]


def test_ready_aborts_after_bounded_persistent_unhealthy_window(
    acceptance,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(acceptance, "HEALTH_RETRY_SECONDS", 2)
    clock = [0.0]
    session = _ReadySession([_Response({"status": "not_ready"}, status_code=503)])
    runner = _runner_for_ready_test(acceptance, tmp_path, session, clock)

    with pytest.raises(acceptance.AuditAbort) as captured:
        runner._ready()

    assert captured.value.code == "health_not_ready"
    assert clock[0] == 2


def test_ready_aborts_immediately_if_boot_id_changes(acceptance, tmp_path):
    clock = [0.0]
    session = _ReadySession([
        _Response({"status": "ready", "boot_id": "boot-1"}),
        _Response({"status": "ready", "boot_id": "boot-2"}),
    ])
    runner = _runner_for_ready_test(acceptance, tmp_path, session, clock)

    runner._ready()
    with pytest.raises(acceptance.AuditAbort) as captured:
        runner._ready()

    assert captured.value.code == "health_boot_changed"
    assert clock[0] == 0


def test_reset_health_boot_tracking_accepts_new_boot_after_intentional_restart(
    acceptance,
    tmp_path,
):
    clock = [0.0]
    session = _ReadySession([
        _Response({"status": "ready", "boot_id": "boot-1"}),
        _Response({"status": "ready", "boot_id": "boot-2"}),
    ])
    runner = _runner_for_ready_test(acceptance, tmp_path, session, clock)

    runner._ready()
    runner.reset_health_boot_tracking()
    health = runner._ready()

    assert health["status"] == "ready"
    assert health["boot_id_hash"] == acceptance.hash_identifier("boot-2")


def test_data_evidence_requires_new_attempt_success_cache_and_exact_revision(acceptance):
    instance = _instance(acceptance)
    started = datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc)
    completed = (started + timedelta(seconds=2)).isoformat()

    evidence = acceptance.validate_data_evidence(
        _runtime(instance, completed),
        instance,
        started_at=started,
    )

    assert evidence["last_attempt_at"] == completed
    assert evidence["last_success_at"] == completed
    assert evidence["last_good_cache"]["structural_generation"] == 2
    assert "last_error" not in json.dumps(evidence)

    stale = _runtime(instance, (started - timedelta(seconds=1)).isoformat())
    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_data_evidence(stale, instance, started_at=started)
    assert captured.value.code == "data_evidence_stale"


def _instance_for_plugin(acceptance, plugin_id):
    base = _instance(acceptance)
    return acceptance.InstancePlan(
        index=base.index,
        playlist_name=base.playlist_name,
        plugin_id=plugin_id,
        instance_name=base.instance_name,
        instance_uuid=base.instance_uuid,
        structural_generation=base.structural_generation,
        settings_revision=base.settings_revision,
        expects_presentation_refresh=base.expects_presentation_refresh,
    )


def test_select_acceptance_plan_filters_requested_plugins_in_playlist_order(acceptance):
    plan = (
        _instance_for_plugin(acceptance, "weather"),
        _instance_for_plugin(acceptance, "steam_profile_dashboard"),
        _instance_for_plugin(acceptance, "sports_dashboard"),
    )

    selected = acceptance.select_acceptance_plan(
        plan,
        {"sports_dashboard", "weather"},
    )

    assert [item.plugin_id for item in selected] == ["weather", "sports_dashboard"]


def test_select_acceptance_plan_rejects_unknown_requested_plugin(acceptance):
    with pytest.raises(acceptance.AuditAbort) as captured:
        acceptance.select_acceptance_plan(
            (_instance_for_plugin(acceptance, "weather"),),
            {"missing_plugin"},
        )

    assert captured.value.code == "requested_plugin_not_configured"


def _bank_document(instance, *, attempt_field, attempted_at, status):
    return {
        "instance_profiles": {instance.instance_uuid: "private-profile-key"},
        "profiles": {
            "private-profile-key": {
                attempt_field: attempted_at,
                "last_provider_status": status,
                "last_provider_error": "private provider error must not escape",
                "records": [{"private": "content must not escape"}],
            },
        },
    }


def test_bank_provider_contract_covers_the_exact_seven_banked_plugins(acceptance):
    assert {
        plugin_id: spec.attempt_field
        for plugin_id, spec in acceptance.BANK_PROVIDER_EVIDENCE_SPECS.items()
    } == {
        "backtothedate": "last_provider_attempt_at",
        "daily_art": "last_provider_attempt_at",
        "gcd_comic_covers": "last_provider_attempt_at",
        "magazine_covers": "library_last_attempt_at",
        "newspaper": "last_provider_attempt_at",
        "pixiv_r18_ranking": "last_provider_attempt_at",
        "species_radar": "last_provider_attempt_at",
    }


def test_bank_provider_state_paths_match_plugin_persistence_contracts(
    acceptance,
    tmp_path,
):
    cache_root = tmp_path / "cache"
    data_root = tmp_path / "data"
    expected = {
        "backtothedate": (
            data_root
            / "plugins"
            / "backtothedate"
            / ".backtothedate_state.json"
        ),
        "daily_art": (
            cache_root
            / "plugins"
            / "daily_art"
            / ".daily_art_cache"
            / "presentation-state.json"
        ),
        "gcd_comic_covers": (
            cache_root
            / "plugins"
            / "gcd_comic_covers"
            / "gcd_comic_covers_cache"
            / "state.json"
        ),
        "magazine_covers": (
            cache_root
            / "plugins"
            / "magazine_covers"
            / ".magazine_covers_cache"
            / "presentation-state.json"
        ),
        "newspaper": (
            data_root
            / "plugins"
            / "newspaper"
            / ".newspaper_presentation_state.json"
        ),
        "pixiv_r18_ranking": (
            data_root
            / "plugins"
            / "pixiv_r18_ranking"
            / "presentation-bank"
            / "presentation-state.json"
        ),
        "species_radar": (
            cache_root
            / "plugins"
            / "species_radar"
            / "cache"
            / "presentation-state.json"
        ),
    }

    actual = {
        plugin_id: acceptance.resolve_bank_provider_state_path(
            _instance_for_plugin(acceptance, plugin_id),
            cache_root=cache_root,
            data_root=data_root,
            environ={},
        )
        for plugin_id in expected
    }

    assert actual == expected


def test_daily_art_requires_bank_evidence_only_for_every_refresh_cadence(acceptance):
    daily = {"plugin_settings": {"rotationCadence": "daily"}}
    every_refresh = {"plugin_settings": {"rotationCadence": "every_refresh"}}

    assert acceptance._bank_provider_expectation(daily, "daily_art") is False
    assert acceptance._bank_provider_expectation(every_refresh, "daily_art") is True


def test_pixiv_bank_path_honors_cache_override_before_data_default(
    acceptance,
    tmp_path,
):
    instance = _instance_for_plugin(acceptance, "pixiv_r18_ranking")
    override = tmp_path / "private-pixiv-cache"

    path = acceptance.resolve_bank_provider_state_path(
        instance,
        cache_root=tmp_path / "cache",
        data_root=tmp_path / "data",
        environ={"INKYPI_PIXIV_R18_CACHE": str(override)},
    )

    assert path == override / "presentation-state.json"


def test_bank_provider_evidence_requires_fresh_exact_success_and_is_private(acceptance):
    instance = _instance_for_plugin(acceptance, "magazine_covers")
    started = datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc)
    attempted_at = (started + timedelta(seconds=1)).isoformat()
    document = _bank_document(
        instance,
        attempt_field="library_last_attempt_at",
        attempted_at=attempted_at,
        status="success",
    )

    evidence = acceptance.validate_bank_provider_evidence(
        document,
        instance,
        started_at=started,
    )

    assert evidence == {"attempted_at": attempted_at, "status": "success"}
    encoded = json.dumps(evidence)
    assert "private-profile-key" not in encoded
    assert "private provider error" not in encoded
    assert "content must not escape" not in encoded


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        ("empty", "bank_provider_empty"),
        ("error", "bank_provider_error"),
        ("SUCCESS", "bank_provider_status_invalid"),
        (None, "bank_provider_status_invalid"),
    ],
)
def test_bank_provider_evidence_rejects_every_non_success_status(
    acceptance,
    status,
    expected_code,
):
    instance = _instance_for_plugin(acceptance, "daily_art")
    started = datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc)
    document = _bank_document(
        instance,
        attempt_field="last_provider_attempt_at",
        attempted_at=(started + timedelta(seconds=1)).isoformat(),
        status=status,
    )

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_bank_provider_evidence(
            document,
            instance,
            started_at=started,
        )

    assert captured.value.code == expected_code
    assert captured.value.safe_details == {}


def test_bank_provider_evidence_rejects_attempt_before_job_start(acceptance):
    instance = _instance_for_plugin(acceptance, "newspaper")
    started = datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc)
    document = _bank_document(
        instance,
        attempt_field="last_provider_attempt_at",
        attempted_at=(started - timedelta(microseconds=1)).isoformat(),
        status="success",
    )

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_bank_provider_evidence(
            document,
            instance,
            started_at=started,
        )

    assert captured.value.code == "bank_provider_attempt_stale"


def test_wait_for_data_evidence_includes_required_bank_provider_proof(
    acceptance,
    tmp_path,
):
    instance = _instance_for_plugin(acceptance, "daily_art")
    started = datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc)
    completed = (started + timedelta(seconds=1)).isoformat()
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(
        json.dumps(_runtime(instance, completed)),
        encoding="utf-8",
    )
    cache_root = tmp_path / "cache"
    state_path = (
        cache_root
        / "plugins"
        / "daily_art"
        / ".daily_art_cache"
        / "presentation-state.json"
    )
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(_bank_document(
            instance,
            attempt_field="last_provider_attempt_at",
            attempted_at=completed,
            status="success",
        )),
        encoding="utf-8",
    )
    runner = acceptance.AcceptanceRunner(
        session=None,
        base_url="http://127.0.0.1",
        config_path=tmp_path / "unused-config.json",
        runtime_state_path=runtime_path,
        display_manifest_path=tmp_path / "unused-manifest.json",
        output_dir=tmp_path / "evidence",
        cache_root=cache_root,
        data_root=tmp_path / "data",
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: pytest.fail("fresh state should pass immediately"),
    )

    _runtime_state, evidence = runner._wait_for_data_evidence(instance, started)

    assert evidence["provider"] == {
        "attempted_at": completed,
        "status": "success",
    }


def test_display_evidence_matches_runtime_manifest_headers_and_pixel_hash(acceptance):
    instance = _instance(acceptance)
    timestamp = datetime(2026, 7, 13, 19, 1, tzinfo=timezone.utc).isoformat()
    image = acceptance.inspect_png(_png_bytes())
    manifest = _manifest(instance, image.pixel_hash, timestamp)
    runtime = _runtime(instance, timestamp)
    headers = {
        "ETag": f'"{manifest["commit_id"]}"',
        "Last-Modified": "Mon, 13 Jul 2026 19:01:00 GMT",
        "Content-Type": "image/png",
    }

    evidence = acceptance.validate_display_evidence(
        runtime,
        manifest,
        instance,
        image,
        headers,
    )

    assert evidence["hardware_written"] is True
    assert evidence["pixel_hash"] == image.pixel_hash
    assert evidence["instance_revision"] == [2, 3]
    assert "plugin_instance" not in json.dumps(evidence)

    manifest["hardware_written"] = False
    baseline_manifest = _manifest(instance, image.pixel_hash, timestamp)
    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_display_evidence(
            runtime,
            manifest,
            instance,
            image,
            headers,
            baseline_manifest=baseline_manifest,
        )
    assert captured.value.code == "hardware_not_written"
    assert captured.value.safe_details["equivalent_candidate"] is True


def test_display_evidence_rejects_preexisting_or_predated_commit(acceptance):
    instance = _instance(acceptance)
    started = datetime(2026, 7, 13, 19, 1, tzinfo=timezone.utc)
    image = acceptance.inspect_png(_png_bytes())
    timestamp = started.isoformat()
    manifest = _manifest(instance, image.pixel_hash, timestamp)
    runtime = _runtime(instance, timestamp)
    headers = {
        "ETag": f'"{manifest["commit_id"]}"',
        "Last-Modified": "Mon, 13 Jul 2026 19:01:00 GMT",
        "Content-Type": "image/png",
    }

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_display_evidence(
            runtime,
            manifest,
            instance,
            image,
            headers,
            baseline_manifest=dict(manifest),
            display_started_at=started,
        )
    assert captured.value.code == "display_commit_stale"

    old_timestamp = (started - timedelta(seconds=1)).isoformat()
    old_manifest = _manifest(
        instance,
        image.pixel_hash,
        old_timestamp,
        commit_id="b" * 32,
    )
    old_runtime = _runtime(instance, old_timestamp, commit_id="b" * 32)
    old_headers = {
        "ETag": f'"{old_manifest["commit_id"]}"',
        "Last-Modified": "Mon, 13 Jul 2026 19:00:59 GMT",
        "Content-Type": "image/png",
    }
    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_display_evidence(
            old_runtime,
            old_manifest,
            instance,
            image,
            old_headers,
            baseline_manifest=manifest,
            display_started_at=started,
        )
    assert captured.value.code == "display_commit_precedes_job"


class _CurrentImageSession:
    def __init__(self, payload, headers):
        self.payload = payload
        self.headers = headers

    def get(self, _url, **_kwargs):
        return _Response(
            {},
            headers=self.headers,
            content=self.payload,
        )


def test_failed_display_validation_still_persists_image_and_safe_headers(
    acceptance,
    tmp_path,
):
    instance = _instance(acceptance)
    timestamp = datetime(2026, 7, 13, 19, 1, tzinfo=timezone.utc).isoformat()
    payload = _png_bytes()
    image = acceptance.inspect_png(payload)
    manifest = _manifest(instance, image.pixel_hash, timestamp, hardware=False)
    runtime = _runtime(instance, timestamp)
    runtime_path = tmp_path / "runtime.json"
    manifest_path = tmp_path / "manifest.json"
    output_dir = tmp_path / "evidence"
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    headers = {
        "ETag": f'"{manifest["commit_id"]}"',
        "Last-Modified": "Mon, 13 Jul 2026 19:01:00 GMT",
        "Content-Type": "image/png",
        "X-Private-Provider-Data": "must-not-be-saved",
    }
    runner = acceptance.AcceptanceRunner(
        session=_CurrentImageSession(payload, headers),
        base_url="http://127.0.0.1",
        config_path=tmp_path / "unused-config.json",
        runtime_state_path=runtime_path,
        display_manifest_path=manifest_path,
        output_dir=output_dir,
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: pytest.fail("hardware false must fail immediately"),
    )

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        runner._capture_display(
            instance,
            baseline_manifest=_manifest(instance, image.pixel_hash, timestamp),
            artifact_suffix="display",
        )

    assert captured.value.code == "hardware_not_written"
    artifacts = captured.value.safe_details["artifacts"]
    assert (output_dir / artifacts["image"]).read_bytes() == payload
    stored_headers = json.loads((output_dir / artifacts["headers"]).read_text())
    assert stored_headers["ETag"] == headers["ETag"]
    assert "X-Private-Provider-Data" not in stored_headers


def test_presentation_completion_requires_exact_receipt_and_final_commit(acceptance):
    instance = _instance(acceptance)
    timestamp = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    request_id = "request-123"
    commit_id = "b" * 32
    receipt = {
        "request_id": request_id,
        "committed_at": timestamp,
        "display_commit_id": commit_id,
        "structural_generation": instance.structural_generation,
        "settings_revision": instance.settings_revision,
        "theme_mode": "day",
    }
    runtime = _runtime(
        instance,
        timestamp,
        request=None,
        receipt=receipt,
        commit_id=commit_id,
    )
    manifest = _manifest(
        instance,
        "c" * 64,
        timestamp,
        commit_id=commit_id,
    )

    evidence = acceptance.validate_presentation_completion(
        runtime,
        manifest,
        instance,
        request_id=request_id,
    )

    assert evidence["request_id_hash"] == acceptance.hash_identifier(request_id)
    assert evidence["display_commit_id_hash"] == acceptance.hash_identifier(commit_id)

    runtime["instances"][instance.instance_uuid]["presentation_receipt"][
        "request_id"
    ] = "other-request"
    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_presentation_completion(
            runtime,
            manifest,
            instance,
            request_id=request_id,
        )
    assert captured.value.code == "presentation_receipt_not_ready"


def test_presentation_outcome_accepts_exact_no_change_lane_success(acceptance):
    instance = _instance(acceptance)
    requested_at = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    request = _presentation_request(instance, requested_at)
    runtime = _runtime(
        instance,
        requested_at,
        request=None,
        receipt=None,
        presentation_success=requested_at,
    )
    manifest = _manifest(instance, "c" * 64, requested_at)

    evidence = acceptance.validate_presentation_outcome(
        runtime,
        manifest,
        instance,
        request=request,
        expected_display_commit_id=manifest["commit_id"],
    )

    assert evidence == {
        "completion": "no_change",
        "request_id_hash": acceptance.hash_identifier(request["request_id"]),
        "completed_at": requested_at,
        "structural_generation": instance.structural_generation,
        "settings_revision": instance.settings_revision,
    }


def test_presentation_no_change_rejects_display_drift(acceptance):
    instance = _instance(acceptance)
    requested_at = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    request = _presentation_request(instance, requested_at)
    runtime = _runtime(
        instance,
        requested_at,
        request=None,
        receipt=None,
        presentation_success=requested_at,
    )
    manifest = _manifest(instance, "c" * 64, requested_at)

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_presentation_outcome(
            runtime,
            manifest,
            instance,
            request=request,
            expected_display_commit_id="different-display-commit",
        )

    assert captured.value.code == "presentation_no_change_display_drift"


def test_atomic_presentation_no_change_requires_new_exact_display_marker(acceptance):
    instance = _instance(acceptance)
    committed_at = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    baseline = _runtime(
        instance,
        committed_at,
        presentation_success="2026-07-13T18:00:00+00:00",
    )
    runtime = _runtime(
        instance,
        committed_at,
        presentation_success=committed_at,
    )
    manifest = _manifest(instance, "c" * 64, committed_at)

    evidence = acceptance.validate_atomic_presentation_no_change(
        runtime,
        baseline,
        manifest,
        instance,
    )

    assert evidence["completion"] == "no_change_atomic"
    runtime["instances"][instance.instance_uuid]["presentation_receipt"] = {
        "request_id": "unbound-fast-change",
    }
    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_atomic_presentation_no_change(
            runtime,
            baseline,
            manifest,
            instance,
        )
    assert captured.value.code == "presentation_atomic_changed_unbindable"
    runtime["instances"][instance.instance_uuid]["presentation_receipt"] = None
    baseline["instances"][instance.instance_uuid]["lanes"]["presentation"][
        "last_success_at"
    ] = committed_at
    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_atomic_presentation_no_change(
            runtime,
            baseline,
            manifest,
            instance,
        )
    assert captured.value.code == "presentation_atomic_no_change_unproven"


def test_presentation_outcome_rejects_request_replacement(acceptance):
    instance = _instance(acceptance)
    requested_at = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    expected = _presentation_request(instance, requested_at, request_id="expected-request")
    replacement = _presentation_request(
        instance,
        requested_at,
        request_id="replacement-request",
    )
    runtime = _runtime(instance, requested_at, request=replacement)
    manifest = _manifest(instance, "c" * 64, requested_at)

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_presentation_outcome(
            runtime,
            manifest,
            instance,
            request=expected,
            expected_display_commit_id=manifest["commit_id"],
        )

    assert captured.value.code == "presentation_request_replaced"


def test_presentation_outcome_rejects_unproven_request_clear(acceptance):
    instance = _instance(acceptance)
    requested_at = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    request = _presentation_request(instance, requested_at)
    runtime = _runtime(instance, requested_at, request=None, receipt=None)
    manifest = _manifest(instance, "c" * 64, requested_at)

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_presentation_outcome(
            runtime,
            manifest,
            instance,
            request=request,
            expected_display_commit_id=manifest["commit_id"],
        )

    assert captured.value.code == "presentation_request_cleared_unproven"


def test_display_created_presentation_request_requires_exact_commit_and_time(acceptance):
    instance = _instance(acceptance)
    started_at = datetime(2026, 7, 13, 19, 1, 59, tzinfo=timezone.utc)
    committed_at = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    manifest = _manifest(
        instance,
        "c" * 64,
        committed_at,
        commit_id="display-commit",
    )
    request = _presentation_request(instance, committed_at)
    request["origin_display_commit_id"] = manifest["commit_id"]

    acceptance.validate_display_created_presentation_request(
        request,
        instance,
        manifest,
        display_started_at=started_at,
    )

    request["origin_display_commit_id"] = "other-display"
    with pytest.raises(acceptance.EvidenceFailure) as captured:
        acceptance.validate_display_created_presentation_request(
            request,
            instance,
            manifest,
            display_started_at=started_at,
        )
    assert captured.value.code == "presentation_request_origin_mismatch"


def test_presentation_timeout_has_soft_admission_slack(acceptance):
    ordinary = _instance_for_plugin(acceptance, "live_radar")
    heavy = _instance_for_plugin(acceptance, "telegram_digest")

    assert acceptance.presentation_timeout_for(ordinary) >= 360
    assert acceptance.presentation_timeout_for(ordinary) > acceptance.ORDINARY_TIMEOUT_SECONDS
    assert acceptance.presentation_timeout_for(heavy) >= acceptance.HEAVY_TIMEOUT_SECONDS


def test_run_instance_records_prepared_request_consumed_by_exact_display(
    acceptance,
    tmp_path,
    monkeypatch,
):
    instance = _instance(acceptance)
    requested_at = datetime(2026, 7, 13, 19, 1, tzinfo=timezone.utc).isoformat()
    committed_at = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    request = _presentation_request(instance, requested_at)
    request["prepared_at"] = requested_at
    request["prepared_theme_mode"] = "day"
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(
        json.dumps(_runtime(instance, requested_at, request=request)),
        encoding="utf-8",
    )
    baseline_manifest = _manifest(
        instance,
        "a" * 64,
        requested_at,
        commit_id="a" * 32,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(baseline_manifest), encoding="utf-8")
    final_commit = "b" * 32
    receipt = {
        "request_id": request["request_id"],
        "committed_at": committed_at,
        "display_commit_id": final_commit,
        "structural_generation": instance.structural_generation,
        "settings_revision": instance.settings_revision,
        "theme_mode": "day",
    }
    final_runtime = _runtime(
        instance,
        committed_at,
        receipt=receipt,
        commit_id=final_commit,
    )
    final_manifest = _manifest(
        instance,
        "b" * 64,
        committed_at,
        commit_id=final_commit,
    )
    runner = acceptance.AcceptanceRunner(
        session=None,
        base_url="http://127.0.0.1",
        config_path=tmp_path / "unused-config.json",
        runtime_state_path=runtime_path,
        display_manifest_path=manifest_path,
        output_dir=tmp_path / "evidence",
    )
    monkeypatch.setattr(
        acceptance,
        "submit_job",
        lambda *_args, **_kwargs: {"id": "job"},
    )
    monkeypatch.setattr(
        acceptance,
        "poll_job",
        lambda *_args, **_kwargs: {"id": "job", "status": "completed"},
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_data_evidence",
        lambda *_args, **_kwargs: ({}, {"fresh": True}),
    )
    captures = []

    def capture(*_args, **_kwargs):
        captures.append(_kwargs["artifact_suffix"])
        return (
            final_runtime,
            final_manifest,
            {"hardware_written": True},
            {"image": "display.png", "headers": "display.headers.json"},
        )

    monkeypatch.setattr(runner, "_capture_display", capture)

    result = runner._run_instance(instance)

    assert result["presentation_evidence"]["completion"] == "changed"
    assert result["presentation_evidence"]["request_id_hash"] == acceptance.hash_identifier(
        request["request_id"]
    )
    assert captures == ["display"]


def test_run_instance_rejects_missing_expected_presentation_request(
    acceptance,
    tmp_path,
    monkeypatch,
):
    base = _instance(acceptance)
    instance = acceptance.InstancePlan(
        index=base.index,
        playlist_name=base.playlist_name,
        plugin_id=base.plugin_id,
        instance_name=base.instance_name,
        instance_uuid=base.instance_uuid,
        structural_generation=base.structural_generation,
        settings_revision=base.settings_revision,
        expects_presentation_refresh=True,
    )
    timestamp = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(
        json.dumps(_runtime(instance, timestamp, request=None, receipt=None)),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(_manifest(instance, "a" * 64, timestamp)),
        encoding="utf-8",
    )
    ticks = iter((0.0, 31.0))
    runner = acceptance.AcceptanceRunner(
        session=None,
        base_url="http://127.0.0.1",
        config_path=tmp_path / "unused-config.json",
        runtime_state_path=runtime_path,
        display_manifest_path=manifest_path,
        output_dir=tmp_path / "evidence",
        monotonic=lambda: next(ticks),
        sleep=lambda _seconds: None,
    )
    monkeypatch.setattr(
        acceptance,
        "submit_job",
        lambda *_args, **_kwargs: {"id": "job"},
    )
    monkeypatch.setattr(
        acceptance,
        "poll_job",
        lambda *_args, **_kwargs: {"id": "job", "status": "completed"},
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_data_evidence",
        lambda *_args, **_kwargs: ({}, {"fresh": True}),
    )
    monkeypatch.setattr(
        runner,
        "_capture_display",
        lambda *_args, **_kwargs: (
            _runtime(
                instance,
                timestamp,
                request=None,
                receipt=None,
                commit_id="b" * 32,
            ),
            _manifest(instance, "b" * 64, timestamp, commit_id="b" * 32),
            {"hardware_written": True},
            {"image": "display.png", "headers": "display.headers.json"},
        ),
    )

    with pytest.raises(acceptance.EvidenceFailure) as captured:
        runner._run_instance(instance)

    assert captured.value.code == "presentation_request_missing"


def test_run_instance_can_verify_fresh_data_display_without_post_display_refresh(
    acceptance,
    tmp_path,
    monkeypatch,
):
    base = _instance(acceptance)
    instance = acceptance.InstancePlan(
        index=base.index,
        playlist_name=base.playlist_name,
        plugin_id=base.plugin_id,
        instance_name=base.instance_name,
        instance_uuid=base.instance_uuid,
        structural_generation=base.structural_generation,
        settings_revision=base.settings_revision,
        expects_presentation_refresh=True,
    )
    timestamp = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(
        json.dumps(_runtime(instance, timestamp, request=None, receipt=None)),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(_manifest(instance, "a" * 64, timestamp)),
        encoding="utf-8",
    )
    runner = acceptance.AcceptanceRunner(
        session=None,
        base_url="http://127.0.0.1",
        config_path=tmp_path / "unused-config.json",
        runtime_state_path=runtime_path,
        display_manifest_path=manifest_path,
        output_dir=tmp_path / "evidence",
        verify_post_display_presentation=False,
    )
    submissions = []

    def submit(_session, _url, endpoint, _instance, **kwargs):
        submissions.append((endpoint, kwargs))
        return {"id": "job"}

    monkeypatch.setattr(acceptance, "submit_job", submit)
    monkeypatch.setattr(
        acceptance,
        "poll_job",
        lambda *_args, **_kwargs: {"id": "job", "status": "completed"},
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_data_evidence",
        lambda *_args, **_kwargs: ({}, {"fresh": True}),
    )
    monkeypatch.setattr(
        runner,
        "_capture_display",
        lambda *_args, **_kwargs: (
            _runtime(instance, timestamp, request=None, receipt=None, commit_id="b" * 32),
            _manifest(instance, "b" * 64, timestamp, commit_id="b" * 32),
            {"hardware_written": True},
            {"image": "display.png", "headers": "display.headers.json"},
        ),
    )
    monkeypatch.setattr(
        runner,
        "_wait_for_presentation",
        lambda *_args, **_kwargs: pytest.fail("post-display refresh must not be awaited"),
    )

    result = runner._run_instance(instance)

    assert result["status"] == "passed"
    assert result["presentation_evidence"] == {
        "completion": "not_required_after_fresh_data",
        "request_origin": "suppressed",
    }
    assert submissions[1] == (
        "/display_plugin_instance",
        {"extra_payload": {"request_presentation": False}},
    )


def test_wait_for_display_presentation_start_accepts_late_exact_request(
    acceptance,
    tmp_path,
):
    base = _instance(acceptance)
    instance = acceptance.InstancePlan(
        index=base.index,
        playlist_name=base.playlist_name,
        plugin_id=base.plugin_id,
        instance_name=base.instance_name,
        instance_uuid=base.instance_uuid,
        structural_generation=base.structural_generation,
        settings_revision=base.settings_revision,
        expects_presentation_refresh=True,
    )
    committed_at = datetime(2026, 7, 13, 19, 2, tzinfo=timezone.utc).isoformat()
    commit_id = "b" * 32
    initial_runtime = _runtime(
        instance,
        committed_at,
        request=None,
        receipt=None,
        commit_id=commit_id,
    )
    manifest = _manifest(
        instance,
        "b" * 64,
        committed_at,
        commit_id=commit_id,
    )
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps(initial_runtime), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    request = _presentation_request(instance, committed_at)
    request["origin_display_commit_id"] = commit_id

    def publish_request(_seconds):
        updated = _runtime(
            instance,
            committed_at,
            request=request,
            receipt=None,
            commit_id=commit_id,
        )
        runtime_path.write_text(json.dumps(updated), encoding="utf-8")

    runner = acceptance.AcceptanceRunner(
        session=None,
        base_url="http://127.0.0.1",
        config_path=tmp_path / "unused-config.json",
        runtime_state_path=runtime_path,
        display_manifest_path=manifest_path,
        output_dir=tmp_path / "evidence",
        monotonic=lambda: 0.0,
        sleep=publish_request,
    )

    _runtime_state, _manifest_state, observed, evidence = (
        runner._wait_for_display_presentation_start(
            instance,
            initial_runtime,
            initial_runtime,
            manifest,
            display_started_at=datetime(2026, 7, 13, 19, 1, tzinfo=timezone.utc),
        )
    )

    assert observed["request_id"] == request["request_id"]
    assert evidence is None


def test_safe_instance_result_never_serializes_name_uuid_settings_or_raw_error(acceptance):
    instance = _instance(acceptance)
    result = acceptance.safe_instance_result(
        instance,
        status="failed",
        failure_code="provider_failed",
        data_job={
            "id": "data-job",
            "status": "failed",
            "error_code": "provider_failed",
            "error": "private raw provider error",
            "settings": {"api_key": "top-secret"},
        },
    )

    encoded = json.dumps(result, sort_keys=True)
    assert instance.plugin_id in encoded
    assert instance.uuid_hash in encoded
    assert instance.instance_uuid not in encoded
    assert instance.instance_name not in encoded
    assert "private raw provider error" not in encoded
    assert "top-secret" not in encoded
    assert '"settings":' not in encoded


def test_cycle_interval_freeze_restores_only_original_interval(acceptance):
    original = _config()
    original["plugin_cycle_interval_seconds"] = 300
    original["background_cache_refresh_min_available_mb"] = 96
    original["refresh_info"] = {
        "refresh_time": "2026-07-13T18:00:00+00:00",
        "image_hash": "before-freeze",
    }

    prepared = acceptance.prepare_cycle_interval_freeze(
        original,
        interval_seconds=86400,
    )

    assert original["plugin_cycle_interval_seconds"] == 300
    assert prepared.document["plugin_cycle_interval_seconds"] == 86400
    assert prepared.document["background_cache_refresh_min_available_mb"] == 1_000_000

    current = json.loads(json.dumps(prepared.document))
    current["refresh_info"] = {
        "refresh_time": "2026-07-13T18:10:00+00:00",
        "image_hash": "real-runtime-write",
    }
    current["runtime_written_field"] = {"keep": True}

    restored = acceptance.restore_cycle_interval(current, prepared)

    assert restored["plugin_cycle_interval_seconds"] == 300
    assert restored["background_cache_refresh_min_available_mb"] == 96
    assert restored["refresh_info"]["image_hash"] == "real-runtime-write"
    assert restored["runtime_written_field"] == {"keep": True}


def test_cycle_interval_freeze_removes_interval_when_originally_absent(acceptance):
    original = _config()
    prepared = acceptance.prepare_cycle_interval_freeze(
        original,
        interval_seconds=86400,
    )

    restored = acceptance.restore_cycle_interval(prepared.document, prepared)

    assert "plugin_cycle_interval_seconds" not in restored
    assert "background_cache_refresh_min_available_mb" not in restored


class _CycleFreezeController:
    def __init__(self, config_path, events):
        self.config_path = config_path
        self.events = events

    def stop(self):
        self.events.append("stop")

    def start(self):
        interval = json.loads(self.config_path.read_text(encoding="utf-8")).get(
            "plugin_cycle_interval_seconds",
            "default",
        )
        self.events.append(f"start:{interval}")


class _CycleFreezeRunner:
    def __init__(self, acceptance, config_path, output_dir, events):
        self.acceptance = acceptance
        self.config_path = config_path
        self.output_dir = output_dir
        self.events = events

    def reset_health_boot_tracking(self):
        self.events.append("reset_boot")

    def _ready(self):
        interval = json.loads(self.config_path.read_text(encoding="utf-8")).get(
            "plugin_cycle_interval_seconds",
            "default",
        )
        self.events.append(f"ready:{interval}")
        return {"status": "ready"}

    def run(self):
        current = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.events.append(
            f"cas_plan:{current['plugin_cycle_interval_seconds']}"
        )
        current["refresh_info"] = {
            "refresh_time": "2026-07-13T18:10:00+00:00",
            "image_hash": "real-runtime-write",
        }
        current["runtime_written_field"] = {"keep": True}
        self.acceptance.atomic_write_json(self.config_path, current)
        return {
            "schema_version": 1,
            "status": "passed",
            "passed": 26,
            "failed": 0,
        }


def test_cycle_interval_freeze_orders_ready_before_plan_and_restores_in_finally(
    acceptance,
    tmp_path,
):
    config_path = tmp_path / "device.json"
    original = _config()
    original["plugin_cycle_interval_seconds"] = 300
    original["refresh_info"] = {"image_hash": "before-freeze"}
    config_path.write_text(json.dumps(original), encoding="utf-8")
    events = []
    runner = _CycleFreezeRunner(
        acceptance,
        config_path,
        tmp_path / "evidence",
        events,
    )
    controller = _CycleFreezeController(config_path, events)
    orchestrator = acceptance.CycleIntervalFreezeAcceptance(
        runner=runner,
        controller=controller,
        interval_seconds=86400,
    )

    summary = orchestrator.run()

    restored = json.loads(config_path.read_text(encoding="utf-8"))
    assert events == [
        "stop",
        "start:86400",
        "reset_boot",
        "ready:86400",
        "cas_plan:86400",
        "stop",
        "start:300",
        "reset_boot",
        "ready:300",
    ]
    assert restored["plugin_cycle_interval_seconds"] == 300
    assert restored["refresh_info"]["image_hash"] == "real-runtime-write"
    assert restored["runtime_written_field"] == {"keep": True}
    assert summary["cycle_interval_freeze_seconds"] == 86400
    assert summary["cycle_interval_restored"] is True
    assert summary["service_ready_restored"] is True


def test_cycle_interval_freeze_keeps_service_stopped_when_restore_write_fails(
    acceptance,
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "device.json"
    original = _config()
    original["plugin_cycle_interval_seconds"] = 300
    config_path.write_text(json.dumps(original), encoding="utf-8")
    events = []
    runner = _CycleFreezeRunner(
        acceptance,
        config_path,
        tmp_path / "evidence",
        events,
    )
    controller = _CycleFreezeController(config_path, events)
    real_atomic_write = acceptance.atomic_write_json
    writes = 0

    def fail_restore_write(path, document):
        nonlocal writes
        writes += 1
        if writes == 3:
            raise acceptance.AuditAbort("injected_restore_write_failure")
        return real_atomic_write(path, document)

    monkeypatch.setattr(acceptance, "atomic_write_json", fail_restore_write)
    orchestrator = acceptance.CycleIntervalFreezeAcceptance(
        runner=runner,
        controller=controller,
        interval_seconds=86400,
    )

    with pytest.raises(acceptance.AuditAbort) as captured:
        orchestrator.run()

    current = json.loads(config_path.read_text(encoding="utf-8"))
    persisted = json.loads(
        (runner.output_dir / "summary.json").read_text(encoding="utf-8")
    )
    assert captured.value.code == "cycle_freeze_restore_config_failed"
    assert current["plugin_cycle_interval_seconds"] == 86400
    assert events == [
        "stop",
        "start:86400",
        "reset_boot",
        "ready:86400",
        "cas_plan:86400",
        "stop",
    ]
    assert persisted["status"] == "aborted"
    assert persisted["abort_code"] == "cycle_freeze_restore_config_failed"
    assert persisted["service_left_stopped"] is True


def test_cycle_interval_freeze_cli_is_opt_in_and_parameterized(acceptance):
    assert acceptance._parser().parse_args([]).freeze_cycle_interval_seconds is None
    assert (
        acceptance._parser()
        .parse_args(["--freeze-cycle-interval-seconds", "43200"])
        .freeze_cycle_interval_seconds
        == 43200
    )


def test_systemd_controller_waits_out_the_manual_refresh_ceiling(acceptance):
    controller = acceptance.SystemdController()

    assert controller.timeout_seconds == 240


def test_print_summary_flag_prints_existing_summary(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    (output_dir / "summary.json").write_text(
        json.dumps({"status": "failed", "passed": 17, "failed": 9}),
        encoding="utf-8",
    )

    exit_code = acceptance.main([
        "--output-dir", str(output_dir),
        "--print-summary",
    ])

    printed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed["failed"] == 9


def test_print_summary_flag_aborts_cleanly_without_summary(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)

    missing_dir = acceptance.main([
        "--output-dir", str(tmp_path / "missing"),
        "--print-summary",
    ])
    no_dir = acceptance.main(["--print-summary"])

    lines = capsys.readouterr().out.strip().splitlines()
    assert missing_dir == 2
    assert no_dir == 2
    assert json.loads(lines[0])["abort_code"] == "summary_read_failed"
    assert (
        json.loads(lines[1])["abort_code"]
        == "print_summary_requires_output_dir"
    )


def test_print_output_png_base64_allows_only_a_png_basename(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    payload = b"\x89PNG\r\nprivate-evidence"
    (output_dir / "money.png").write_bytes(payload)

    exit_code = acceptance.main([
        "--output-dir", str(output_dir),
        "--print-output-png-base64", "money.png",
    ])
    encoded = capsys.readouterr().out.strip()
    traversal_code = acceptance.main([
        "--output-dir", str(output_dir),
        "--print-output-png-base64", "../money.png",
    ])
    traversal = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert base64.b64decode(encoded) == payload
    assert traversal_code == 2
    assert traversal["abort_code"] == "invalid_output_png_request"


def test_print_runtime_state_flag_prints_state_document(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)
    state_path = tmp_path / "runtime_state.json"
    state_path.write_text(
        json.dumps({"instances": {"abc": {"data": {"last_error": "boom"}}}}),
        encoding="utf-8",
    )

    exit_code = acceptance.main([
        "--runtime-state", str(state_path),
        "--print-runtime-state",
    ])

    printed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed["instances"]["abc"]["data"]["last_error"] == "boom"


def test_print_config_keys_prints_only_allowlisted_scalars(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)
    config_path = tmp_path / "device.json"
    config_path.write_text(
        json.dumps({
            "active_theme": "night",
            "plugin_cycle_interval_seconds": 300,
            "displayed_instance_uuid": "abc",
            "plugin_settings_secret": "must-not-leak",
        }),
        encoding="utf-8",
    )

    exit_code = acceptance.main([
        "--config", str(config_path),
        "--print-config-keys", "active_theme,plugin_cycle_interval_seconds,plugin_settings_secret",
    ])

    printed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed == {
        "active_theme": "night",
        "plugin_cycle_interval_seconds": 300,
    }


def test_print_cache_tree_lists_names_sizes_and_mtimes_only(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    (cache_root / "abc-1-1-day.png").write_bytes(b"px")

    exit_code = acceptance.main([
        "--cache-root", str(cache_root),
        "--print-cache-tree",
    ])

    printed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed["entries"][0]["name"] == "abc-1-1-day.png"
    assert printed["entries"][0]["size"] == 2
    assert "mtime" in printed["entries"][0]


def test_set_open_display_control_stops_writes_and_restarts(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps({"name": "frame"}), encoding="utf-8")
    events = []

    class _FakeController:
        def stop(self):
            events.append("stop")

        def start(self):
            interval = json.loads(
                config_path.read_text(encoding="utf-8")
            ).get("open_display_control")
            events.append(f"start:{interval}")

    monkeypatch.setattr(
        acceptance,
        "SystemdController",
        lambda: _FakeController(),
    )

    exit_code = acceptance.main([
        "--config", str(config_path),
        "--set-open-display-control", "true",
    ])

    printed = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed["open_display_control"] is True
    assert events == ["stop", "start:True"]
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["open_display_control"] is True
    assert persisted["name"] == "frame"


def test_merge_env_adds_missing_keys_without_overwriting_or_printing_values(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)
    source = tmp_path / "legacy.env"
    source.write_text(
        "# legacy keys\n"
        "MASSIVE_API_KEY=massive-secret\n"
        "PIXIV_PHPSESSID='pixiv-secret'\n"
        "OPEN_WEATHER_MAP_SECRET=old-weather-secret\n"
        "\n",
        encoding="utf-8",
    )
    target = tmp_path / "inkypi.env"
    target.write_text(
        "OPEN_WEATHER_MAP_SECRET=current-weather-secret\n",
        encoding="utf-8",
    )

    exit_code = acceptance.main([
        "--merge-env-from", str(source),
        "--env-target", str(target),
    ])

    printed = capsys.readouterr().out
    payload = json.loads(printed)
    merged = target.read_text(encoding="utf-8")
    assert exit_code == 0
    assert payload["added_keys"] == ["MASSIVE_API_KEY", "PIXIV_PHPSESSID"]
    assert payload["skipped_existing_keys"] == ["OPEN_WEATHER_MAP_SECRET"]
    assert "massive-secret" not in printed
    assert "pixiv-secret" not in printed
    assert "MASSIVE_API_KEY=massive-secret" in merged
    assert "PIXIV_PHPSESSID='pixiv-secret'" in merged
    assert "OPEN_WEATHER_MAP_SECRET=current-weather-secret" in merged
    assert "old-weather-secret" not in merged


def test_merge_env_aborts_cleanly_when_source_is_missing(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)

    exit_code = acceptance.main([
        "--merge-env-from", str(tmp_path / "missing.env"),
        "--env-target", str(tmp_path / "inkypi.env"),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["abort_code"] == "env_source_read_failed"


def test_main_routes_freeze_flag_through_cycle_interval_orchestrator(
    acceptance,
    tmp_path,
    monkeypatch,
):
    secret_path = tmp_path / "flask_secret"
    secret_path.write_text("unit-test-secret", encoding="utf-8")
    created = {}

    class _FakeRunner:
        def __init__(self, **_kwargs):
            self.output_dir = tmp_path / "evidence"

        def run(self):
            raise AssertionError(
                "runner.run must go through the freeze orchestrator"
            )

    class _FakeOrchestrator:
        def __init__(self, *, runner, controller, interval_seconds):
            created["runner"] = runner
            created["controller"] = controller
            created["interval_seconds"] = interval_seconds

        def run(self):
            return {"status": "passed", "passed": 26, "failed": 0}

    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(acceptance, "AcceptanceRunner", _FakeRunner)
    monkeypatch.setattr(
        acceptance,
        "CycleIntervalFreezeAcceptance",
        _FakeOrchestrator,
    )

    exit_code = acceptance.main([
        "--flask-secret", str(secret_path),
        "--output-dir", str(tmp_path / "evidence"),
        "--freeze-cycle-interval-seconds", "86400",
    ])

    assert exit_code == 0
    assert created["interval_seconds"] == 86400
    assert isinstance(created["runner"], _FakeRunner)
    assert isinstance(created["controller"], acceptance.SystemdController)


def test_stocktracker_config_diagnostics_never_prints_private_values(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    config = _config()
    settings = config["playlist_config"]["playlists"][0]["plugins"][0]
    settings["plugin_id"] = "stocktracker"
    settings["plugin_settings"] = {
        "tickers": "PRIVATE",
        "shares": "99",
        "portfolio_csv_path": str(tmp_path / "missing.csv"),
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)

    exit_code = acceptance.main([
        "--config", str(config_path),
        "--print-stocktracker-diagnostics",
    ])

    printed = capsys.readouterr().out
    payload = json.loads(printed)
    assert exit_code == 0
    assert payload == {
        "csv_exists": False,
        "existing_csv_active_spcx": False,
        "existing_csv_has_spcx": False,
        "has_csv_setting": True,
        "has_inline_shares": True,
        "has_inline_tickers": True,
        "inline_has_spcx": False,
        "instance_count": 1,
        "latest_source_generated_at": None,
        "latest_source_has_spcx": False,
        "latest_source_symbol_count": 0,
        "source_cache_files": 0,
        "service_csv_readable": False,
    }
    assert "PRIVATE" not in printed
    assert "99" not in printed


def test_stocktracker_config_diagnostics_detects_spcx_without_printing_holdings(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    portfolio_path = tmp_path / "portfolio.csv"
    portfolio_path.write_text("ticker,shares\nSPCX,7\nPRIVATE,99\n", encoding="utf-8")
    config = _config()
    settings = config["playlist_config"]["playlists"][0]["plugins"][0]
    settings["plugin_id"] = "stocktracker"
    settings["plugin_settings"] = {
        "tickers": "SPCX,PRIVATE",
        "shares": "7,99",
        "portfolio_csv_path": str(portfolio_path),
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)

    exit_code = acceptance.main([
        "--config", str(config_path),
        "--print-stocktracker-diagnostics",
    ])

    printed = capsys.readouterr().out
    payload = json.loads(printed)
    assert exit_code == 0
    assert payload["csv_exists"] is True
    assert payload["existing_csv_active_spcx"] is True
    assert payload["existing_csv_has_spcx"] is True
    assert payload["inline_has_spcx"] is True
    assert "PRIVATE" not in printed
    assert "99" not in printed


def test_configure_robinhood_mcp_removes_every_stale_money_fallback(acceptance):
    config = _config()
    plugins = config["playlist_config"]["playlists"][0]["plugins"]
    plugins[0]["plugin_id"] = "stocktracker"
    plugins[0]["settings_revision"] = 4
    plugins[0]["plugin_settings"] = {
        "period": "1mo",
        "portfolio_csv_path": "/old/portfolio.csv",
        "portfolio_csv_file": "/old/upload.csv",
        "tickers": "OLD,SPCX",
        "shares": "99,1",
        "cash_balance": "123",
        "ticker_theme": "auto",
    }

    updated, count = acceptance.configure_robinhood_mcp(
        config,
        account_hash="b76f3558212e",
        token_path="/var/lib/inkypi/secrets/robinhood_mcp.json",
    )

    assert count == 1
    item = updated["playlist_config"]["playlists"][0]["plugins"][0]
    settings = item["plugin_settings"]
    assert item["settings_revision"] == 5
    assert settings["data_provider"] == "robinhood_mcp"
    assert settings["robinhood_account_hash"] == "b76f3558212e"
    assert settings["robinhood_token_path"] == "/var/lib/inkypi/secrets/robinhood_mcp.json"
    assert settings["refreshOnDisplay"] is True
    assert settings["holdings_pin_symbols"] == "SPCX"
    assert settings["header_brand"] == "robinhood"
    assert settings["ticker_theme"] == "auto"
    for stale_key in (
        "portfolio_csv_path",
        "portfolio_csv_file",
        "tickers",
        "shares",
        "cash_balance",
    ):
        assert stale_key not in settings


def test_install_robinhood_token_validates_and_never_prints_secret(
    acceptance,
    tmp_path,
    capsys,
    monkeypatch,
):
    source = tmp_path / "staged-token.json"
    target = tmp_path / "secrets" / "robinhood_mcp.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mcp_url": "https://agent.robinhood.com/mcp/trading",
                "token_url": "https://api.robinhood.com/oauth2/token/",
                "registration": {"client_id": "private-client"},
                "token": {
                    "access_token": "private-access",
                    "refresh_token": "private-refresh",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(acceptance.os, "geteuid", lambda: 0, raising=False)
    chowned = []
    monkeypatch.setattr(acceptance, "_chown_inkypi", lambda path: chowned.append(path))

    exit_code = acceptance.main(
        [
            "--install-robinhood-token-from",
            str(source),
            "--robinhood-token-target",
            str(target),
        ]
    )

    printed = capsys.readouterr().out
    payload = json.loads(printed)
    assert exit_code == 0
    assert payload == {
        "has_refresh_token": True,
        "status": "installed",
        "target": str(target),
    }
    assert not source.exists()
    assert target.exists()
    assert chowned == [target.parent, target]
    assert "private-client" not in printed
    assert "private-access" not in printed
    assert "private-refresh" not in printed


def test_stocktracker_history_diagnostics_reports_dates_without_values(acceptance, tmp_path):
    durable = tmp_path / "data" / "plugins" / "stocktracker" / "history"
    durable.mkdir(parents=True)
    (durable / "private-holdings-hash.json").write_text(
        json.dumps(
            [
                {"date": "2026-06-01", "value": 123456.78},
                {"date": "2026-07-14", "value": 234567.89},
            ]
        ),
        encoding="utf-8",
    )

    result = acceptance.stocktracker_history_diagnostics(
        data_root=tmp_path / "data",
        plugin_root=tmp_path / "plugins",
        install_root=tmp_path / "missing-install",
        legacy_root=tmp_path / "missing-legacy",
    )

    printed = json.dumps(result, sort_keys=True)
    assert result == {
        "earliest_date": "2026-06-01",
        "files": 1,
        "latest_date": "2026-07-14",
        "records": 2,
        "roots_with_history": ["durable"],
    }
    assert "123456" not in printed
    assert "234567" not in printed
    assert "private-holdings-hash" not in printed
