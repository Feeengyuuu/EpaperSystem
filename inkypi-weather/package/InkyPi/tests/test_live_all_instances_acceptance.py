from datetime import datetime, timedelta, timezone
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
            "plugin_settings": {"secret": f"secret-{index}"},
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


def _runtime(instance, timestamp, *, request=None, receipt=None, commit_id="a" * 32):
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
                    "presentation": {},
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
    assert "Private instance" not in json.dumps(plan[0].safe_identity())


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
    )


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
            / ".gcd_comic_covers_cache"
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
