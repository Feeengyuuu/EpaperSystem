import hashlib
import importlib
import json
import random
import sys
import threading
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.apod import apod as apod_module  # noqa: E402
from plugins.apod import space_weather as space_weather_module  # noqa: E402
from plugins.apod.apod import Apod, ApodRecord  # noqa: E402
from plugins.apod.space_weather import (  # noqa: E402
    ALERTS_ENDPOINT,
    DONKI_CME_ENDPOINT,
    DONKI_FLR_ENDPOINT,
    KP_ENDPOINT,
    MAX_SOURCE_CACHE_BYTES,
    SCALES_ENDPOINT,
    WIND_MAG_ENDPOINT,
    WIND_SPEED_ENDPOINT,
    SourceEnvelope,
    SourceResult,
    SpaceWeatherRepository,
    fold_alerts,
    normalize_kp,
    normalize_scales,
    normalize_wind_magnetic_field,
    normalize_wind_speed,
    refresh_space_weather,
    select_donki_event,
)
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    attach_source_provenance,
    read_source_provenance,
)
from runtime.long_task_executor import InstanceIdentity  # noqa: E402
from runtime.refresh_contracts import (  # noqa: E402
    TaskCancelled,
    TaskDeadlineExceeded,
)


@pytest.fixture(autouse=True)
def apod_test_media_policy(monkeypatch):
    """Inject deterministic fixture hosts without weakening production trust."""

    production_policy = getattr(apod_module, "_trusted_apod_media_host", None)
    production_transport = getattr(
        apod_module,
        "_require_public_hostname_resolution",
        None,
    )
    production_downloader = getattr(
        apod_module,
        "_download_apod_media_to_file",
        None,
    )
    if production_policy is not None:
        monkeypatch.setattr(
            apod_module,
            "_trusted_apod_media_host",
            lambda host: production_policy(host) or host == "media.example.test",
        )
    if production_policy is not None and production_transport is not None:
        def test_transport(host, port, *, context=None):
            if host == "media.example.test":
                apod_module._task_checkpoint(context)
                return
            return production_transport(host, port, context=context)

        monkeypatch.setattr(
            apod_module,
            "_require_public_hostname_resolution",
            test_transport,
        )
    if production_policy is not None and production_downloader is not None:
        def test_downloader(
            media_url,
            path,
            *,
            context,
            timeout,
            max_bytes,
            mode=0o600,
        ):
            if str(media_url).startswith("https://media.example.test/"):
                return apod_module.get_http_client().stream_to_file(
                    "GET",
                    media_url,
                    path,
                    context=context,
                    timeout=timeout,
                    max_bytes=max_bytes,
                    mode=mode,
                    allow_redirects=False,
                )
            return production_downloader(
                media_url,
                path,
                context=context,
                timeout=timeout,
                max_bytes=max_bytes,
                mode=mode,
            )

        monkeypatch.setattr(
            apod_module,
            "_download_apod_media_to_file",
            test_downloader,
        )
    return SimpleNamespace(
        production_policy=production_policy,
        production_transport=production_transport,
        production_downloader=production_downloader,
    )


@pytest.fixture
def apod_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    return Apod({"id": "apod"})


def _identity(uuid, generation, revision=1):
    return InstanceIdentity(uuid, generation, revision)


def test_instance_namespace_uses_uuid_and_generation_not_settings_revision(
    monkeypatch, apod_storage
):
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7, 1),
    )
    first = apod_module._instance_paths(apod_storage)

    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7, 99),
    )
    revised = apod_module._instance_paths(apod_storage)

    expected = hashlib.sha256(
        b"4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531:7"
    ).hexdigest()
    assert first.identity_key == expected
    assert first == revised
    assert first.cache.is_dir()
    assert first.data.is_dir()
    assert first.media.is_dir()


def test_instance_namespace_separates_uuid_and_generation(monkeypatch, apod_storage):
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7),
    )
    first = apod_module._instance_paths(apod_storage)

    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("a2e3b8e3-6eb0-4d31-a7bc-0d0b9beb4737", 7),
    )
    other_uuid = apod_module._instance_paths(apod_storage)
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 8),
    )
    other_generation = apod_module._instance_paths(apod_storage)

    assert first.cache != other_uuid.cache != other_generation.cache
    assert first.data != other_uuid.data != other_generation.data


@pytest.mark.parametrize("identity", [None, _identity(None, 7), _identity("uuid", None)])
def test_instance_namespace_fails_closed_without_runtime_identity(
    monkeypatch, apod_storage, identity
):
    monkeypatch.setattr(apod_module, "current_instance_identity", lambda: identity)

    with pytest.raises(RuntimeError, match="instance identity"):
        apod_module._instance_paths(apod_storage)


def test_instance_namespace_allows_explicit_preview_namespace_without_identity(
    monkeypatch, apod_storage
):
    monkeypatch.setattr(apod_module, "current_instance_identity", lambda: None)

    paths = apod_module._instance_paths(apod_storage, preview_namespace="test-preview")

    assert paths.identity_key.startswith("preview-")
    assert paths.cache.is_dir()
    assert paths.data.is_dir()
    assert paths.media.is_dir()


@pytest.fixture
def selection_paths(apod_storage):
    return apod_module._instance_paths(apod_storage, preview_namespace="selection")


def test_selection_today_persists_the_device_day(selection_paths):
    selection = apod_module._resolve_selection(
        settings={},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(11),
    )

    assert selection.mode == "today"
    assert selection.device_day == "2026-07-22"
    assert selection.requested_date == "2026-07-22"
    assert selection.resolved_record_date == "2026-07-22"
    assert selection.provisional is False


def test_selection_custom_date_persists_exact_requested_date(selection_paths):
    selection = apod_module._resolve_selection(
        settings={"customDate": "2024-05-07"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(11),
    )

    assert selection.mode == "custom"
    assert selection.requested_date == "2024-05-07"
    assert selection.resolved_record_date == "2024-05-07"


def test_selection_compatibility_random_mode_is_stable_for_the_day(selection_paths):
    first = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(17),
    )
    second = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=object(),
    )

    assert first.mode == "random"
    assert date(2015, 1, 1) <= date.fromisoformat(first.requested_date) <= date(2026, 7, 22)
    assert first == second
    assert first.provisional is True


def test_selection_persists_a_device_day_bounded_random_candidate_sequence(
    selection_paths,
):
    selection = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(17),
    )

    assert selection.candidate_dates
    assert len(selection.candidate_dates) == 5
    assert len(set(selection.candidate_dates)) == 5
    assert all(
        date(2015, 1, 1) <= date.fromisoformat(candidate) <= date(2026, 7, 22)
        for candidate in selection.candidate_dates
    )
    assert selection.candidate_dates[0] == selection.resolved_record_date


def test_selection_manual_force_repeat_reuses_existing_selection(selection_paths):
    first = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(9),
    )
    forced = apod_module._resolve_selection(
        settings={"randomizeApod": "true", "forceRefresh": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=object(),
    )

    assert forced == first


def test_selection_random_mode_rerolls_on_next_device_day(selection_paths):
    first = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(3),
    )
    next_day = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 23),
        paths=selection_paths,
        rng=random.Random(4),
    )

    assert next_day.device_day == "2026-07-23"
    assert next_day.fingerprint != first.fingerprint


def test_selection_fingerprint_invalidates_mode_requested_and_custom_date():
    device_day = date(2026, 7, 22)
    today = apod_module._selection_fingerprint(
        mode="today", device_day=device_day, requested_date="2026-07-22", custom_date=""
    )
    random_mode = apod_module._selection_fingerprint(
        mode="random", device_day=device_day, requested_date="2026-07-22", custom_date=""
    )
    requested = apod_module._selection_fingerprint(
        mode="today", device_day=device_day, requested_date="2026-07-21", custom_date=""
    )
    custom = apod_module._selection_fingerprint(
        mode="custom", device_day=device_day, requested_date="2024-05-07", custom_date="2024-05-07"
    )

    assert len({today, random_mode, requested, custom}) == 4


def test_selection_json_is_atomic_and_contains_selection_contract(selection_paths):
    selection = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(7),
    )

    persisted = json.loads((selection_paths.data / "selection.json").read_text("utf-8"))
    assert persisted == {
        "schema": 1,
        "device_day": "2026-07-22",
        "mode": "random",
        "requested_date": selection.requested_date,
        "selected_apod_date": selection.resolved_record_date,
        "selection_fingerprint": selection.fingerprint,
        "provisional": True,
        "record_cache_key": selection.record_cache_key,
        "candidate_dates": list(selection.candidate_dates),
    }
    assert not list(selection_paths.data.glob("selection.json.*.tmp"))


def test_selection_json_read_is_bounded_before_decode(selection_paths):
    path = selection_paths.data / "selection.json"
    path.write_bytes(b"{" + b"x" * apod_module.MAX_SELECTION_JSON_BYTES + b"}")

    assert apod_module._read_selection(path) is None


def test_bounded_json_treats_deep_recursion_as_a_cache_miss(tmp_path):
    path = tmp_path / "deep.json"
    path.write_bytes(
        b'{"nested":'
        + (b"[" * 1100)
        + b"0"
        + (b"]" * 1100)
        + b"}"
    )

    assert apod_module._read_bounded_json(path, max_bytes=16 * 1024) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.__setitem__("schema", 99),
        lambda doc: doc.__setitem__("provisional", "false"),
        lambda doc: doc.__setitem__("candidate_dates", ["2026-07-20"] * 2),
        lambda doc: doc.__setitem__("candidate_dates", doc["candidate_dates"][:4]),
        lambda doc: doc.__setitem__(
            "candidate_dates",
            [f"2026-07-{day:02d}" for day in range(14, 20)],
        ),
        lambda doc: doc.__setitem__("selected_apod_date", "not-a-date"),
        lambda doc: doc.__setitem__(
            "selected_apod_date",
            doc["selected_apod_date"].replace("-", ""),
        ),
    ],
    ids=[
        "schema",
        "strict-bool",
        "unique-candidates",
        "exact-candidate-count",
        "candidate-count",
        "strict-date",
        "canonical-date",
    ],
)
def test_selection_json_rejects_invalid_schema_types_dates_and_candidates(
    selection_paths,
    mutate,
):
    apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(17),
    )
    path = selection_paths.data / "selection.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document), encoding="utf-8")

    assert apod_module._read_selection(path) is None


def test_selection_cancellation_during_candidate_generation_precedes_state_write(
    selection_paths,
):
    signal = TaskCancelled("stop selection")

    class Context:
        cancelled = False

        def raise_if_cancelled(self):
            if self.cancelled:
                raise signal

    class CancellingRng:
        def randint(self, _start, _end):
            context.cancelled = True
            return 0

    context = Context()

    with pytest.raises(TaskCancelled) as caught:
        apod_module._resolve_selection(
            settings={"randomizeApod": "true"},
            device_day=date(2026, 7, 22),
            paths=selection_paths,
            rng=CancellingRng(),
            context=context,
        )

    assert caught.value is signal
    assert not (selection_paths.data / "selection.json").exists()


def test_selection_today_rejects_semantically_retargeted_persisted_date(
    selection_paths,
):
    apod_module._resolve_selection(
        settings={},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(1),
    )
    path = selection_paths.data / "selection.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["requested_date"] = "2026-07-01"
    document["selected_apod_date"] = "2026-07-01"
    path.write_text(json.dumps(document), encoding="utf-8")

    resolved = apod_module._resolve_selection(
        settings={},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(2),
    )

    assert resolved.requested_date == "2026-07-22"
    assert resolved.resolved_record_date == "2026-07-22"


def test_selection_settings_revision_changes_do_not_change_namespace_or_reroll(
    monkeypatch, apod_storage
):
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7, 1),
    )
    first_paths = apod_module._instance_paths(apod_storage)
    first = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=first_paths,
        rng=random.Random(2),
    )
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7, 2),
    )
    revised_paths = apod_module._instance_paths(apod_storage)
    revised = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=revised_paths,
        rng=object(),
    )

    assert revised_paths == first_paths
    assert revised == first


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "apod"
NOW_UTC = datetime(2026, 7, 22, 12, 20, tzinfo=timezone.utc)


def _fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_scales_uses_key_zero_as_current_and_parses_all_product_keys():
    normalized = normalize_scales(_fixture("noaa_scales.json"), now_utc=NOW_UTC)

    assert normalized["observed_at_utc"] == "2026-07-22T12:15:00Z"
    assert normalized["current"] == {"g": 2, "r": 1, "s": 0}
    assert [entry["product_key"] for entry in normalized["timeline"]] == [
        "-1",
        "0",
        "1",
        "2",
        "3",
    ]
    assert normalized["timeline"][0]["g"] == 4
    assert normalized["timeline"][1]["g"] == 2


def test_scales_selects_earliest_forecast_day_with_valid_probabilities():
    normalized = normalize_scales(_fixture("noaa_scales.json"), now_utc=NOW_UTC)

    assert normalized["probabilities"] == {
        "valid_at_utc": "2026-07-23T00:00:00Z",
        "r_minor": 55,
        "r_major": 10,
        "s": 5,
    }


def test_scales_rejects_probabilities_outside_zero_to_one_hundred():
    raw = _fixture("noaa_scales.json")
    raw["2"]["R"]["MinorProb"] = "101"

    with pytest.raises(ValueError, match="probability"):
        normalize_scales(raw, now_utc=NOW_UTC)


def test_scales_rejects_an_out_of_range_partial_probability_day():
    raw = _fixture("noaa_scales.json")
    raw["1"]["R"]["MinorProb"] = "101"

    with pytest.raises(ValueError, match="probability"):
        normalize_scales(raw, now_utc=NOW_UTC)


def test_kp_object_rows_preserve_decimal_scale_and_bound_forecast_window():
    normalized = normalize_kp(_fixture("noaa_kp_objects.json"), now_utc=NOW_UTC)

    assert normalized["current"] == {
        "time_tag": "2026-07-22T12:00:00Z",
        "kp": 4.67,
        "observed": "estimated",
        "noaa_scale": "G1",
    }
    assert normalized["predicted_peak"] == {
        "time_tag": "2026-07-24T12:20:00Z",
        "kp": 6.33,
        "observed": "predicted",
        "noaa_scale": "G2",
    }
    assert all(
        NOW_UTC < datetime.fromisoformat(row["time_tag"].replace("Z", "+00:00"))
        <= NOW_UTC + timedelta(hours=48)
        for row in normalized["forecast_48h"]
    )


def test_kp_accepts_legacy_header_and_row_arrays():
    normalized = normalize_kp(_fixture("noaa_kp_rows.json"), now_utc=NOW_UTC)

    assert normalized["current"]["time_tag"] == "2026-07-22T12:00:00Z"
    assert normalized["current"]["kp"] == 4.67
    assert normalized["current"]["noaa_scale"] == "G1"
    assert normalized["predicted_peak"]["kp"] == 5.33


def test_wind_normalizers_choose_newest_timestamp_and_keep_source_times_separate():
    speed = normalize_wind_speed(
        _fixture("noaa_wind_speed.json"), now_utc=NOW_UTC
    )
    magnetic = normalize_wind_magnetic_field(
        _fixture("noaa_wind_mag.json"), now_utc=NOW_UTC
    )

    assert speed == {
        "observed_at_utc": "2026-07-22T12:18:00Z",
        "speed_km_s": 455.0,
    }
    assert magnetic == {
        "observed_at_utc": "2026-07-22T12:11:00Z",
        "bt_nt": 5.2,
        "bz_gsm_nt": -0.04,
        "bz_direction": "south",
    }
    assert speed["observed_at_utc"] != magnetic["observed_at_utc"]


class FakeHttp:
    def __init__(self, responses):
        self.responses = {
            endpoint: list(values) for endpoint, values in responses.items()
        }
        self.calls = []

    def request_bytes(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        value = self.responses[endpoint].pop(0)
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, bytes):
            value = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return SimpleNamespace(data=value)


def _core_http(scales, kp):
    return FakeHttp({SCALES_ENDPOINT: [scales], KP_ENDPOINT: [kp]})


@pytest.mark.parametrize("abort_type", [TaskCancelled, TaskDeadlineExceeded])
@pytest.mark.parametrize("operation", ["core", "alerts", "donki"])
def test_space_weather_provider_abort_is_never_converted_to_cache_fallback(
    tmp_path,
    abort_type,
    operation,
):
    signal = abort_type(f"stop {operation}")

    class AbortHttp:
        def __init__(self):
            self.calls = []

        def request_bytes(self, _method, endpoint, **_kwargs):
            self.calls.append(endpoint)
            raise signal

    http = AbortHttp()
    repository = SpaceWeatherRepository(cache_dir=tmp_path, http=http)
    calls = {
        "core": lambda: repository.refresh_core(
            now_utc=NOW_UTC,
            context=None,
        ),
        "alerts": lambda: repository.refresh_alerts(
            now_utc=NOW_UTC,
            context=None,
        ),
        "donki": lambda: repository.refresh_donki(
            nasa_api_key="nasa-key",
            now_utc=NOW_UTC,
            context=None,
        ),
    }

    with pytest.raises(abort_type) as caught:
        calls[operation]()

    assert caught.value is signal
    assert len(http.calls) == 1
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("abort_type", [TaskCancelled, TaskDeadlineExceeded])
def test_space_weather_cancellation_after_provider_response_precedes_cache_write(
    tmp_path,
    abort_type,
):
    signal = abort_type("stop before cache write")

    class Context:
        cancelled = False

        def raise_if_cancelled(self):
            if self.cancelled:
                raise signal

    class Http:
        def request_bytes(self, *_args, **_kwargs):
            context.cancelled = True
            return SimpleNamespace(data=b"[]")

    context = Context()
    repository = SpaceWeatherRepository(cache_dir=tmp_path, http=Http())

    with pytest.raises(abort_type) as caught:
        repository.refresh_alerts(now_utc=NOW_UTC, context=context)

    assert caught.value is signal
    assert not (tmp_path / "alerts.json").exists()


def test_source_envelope_is_immutable_and_persists_raw_digest(tmp_path):
    raw_scales = (FIXTURE_DIR / "noaa_scales.json").read_bytes()
    context = object()
    http = _core_http(raw_scales, _fixture("noaa_kp_objects.json"))
    repository = SpaceWeatherRepository(cache_dir=tmp_path, http=http)

    scales, kp = repository.refresh_core(now_utc=NOW_UTC, context=context)

    assert scales.state == "live"
    assert kp.state == "live"
    assert isinstance(scales.envelope, SourceEnvelope)
    assert scales.envelope.raw_digest == hashlib.sha256(raw_scales).hexdigest()
    assert scales.envelope.fetched_at_utc == NOW_UTC
    assert scales.envelope.observed_at_utc == datetime(
        2026, 7, 22, 12, 15, tzinfo=timezone.utc
    )
    with pytest.raises(FrozenInstanceError):
        scales.envelope.schema = 99
    persisted = json.loads((tmp_path / "scales.json").read_text(encoding="utf-8"))
    assert persisted["raw_digest"] == scales.envelope.raw_digest
    assert persisted["fetched_at_utc"] == "2026-07-22T12:20:00Z"
    assert all(
        method == "GET"
        and kwargs["context"] is context
        and kwargs["timeout"]
        and kwargs["max_bytes"] > 0
        for method, _endpoint, kwargs in http.calls
    )


def test_scales_http_200_frozen_provider_observation_is_stale(tmp_path):
    frozen = _fixture("noaa_scales.json")
    frozen["0"]["DateStamp"] = "2026-07-22"
    frozen["0"]["TimeStamp"] = "10:45:00"
    http = _core_http(frozen, _fixture("noaa_kp_objects.json"))

    scales, _kp = SpaceWeatherRepository(
        cache_dir=tmp_path, http=http
    ).refresh_core(now_utc=NOW_UTC, context=None)

    assert scales.state == "stale_cache"
    assert scales.envelope.fetched_at_utc == NOW_UTC
    assert scales.envelope.observed_at_utc == datetime(
        2026, 7, 22, 10, 45, tzinfo=timezone.utc
    )


def test_failed_scales_preserves_last_good_file_and_uses_fresh_then_stale_cache(
    tmp_path,
):
    raw_scales = (FIXTURE_DIR / "noaa_scales.json").read_bytes()
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=_core_http(raw_scales, _fixture("noaa_kp_objects.json")),
    )
    repository.refresh_core(now_utc=NOW_UTC, context=None)
    path = tmp_path / "scales.json"
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    repository.http = _core_http(RuntimeError("scales offline"), RuntimeError("kp offline"))
    fresh, _kp = repository.refresh_core(
        now_utc=NOW_UTC + timedelta(minutes=20), context=None
    )
    stale, _kp = repository.refresh_core(
        now_utc=NOW_UTC + timedelta(minutes=60), context=None
    )

    assert fresh.state == "fresh_cache"
    assert stale.state == "stale_cache"
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_corrupt_source_cache_is_isolated_and_refetched(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "scales.json").write_text("{broken", encoding="utf-8")
    untouched = tmp_path / "wind_speed.json"
    untouched.write_bytes(b"separate-source")
    untouched_mtime = untouched.stat().st_mtime_ns
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=_core_http(
            _fixture("noaa_scales.json"), _fixture("noaa_kp_objects.json")
        ),
    )

    scales, kp = repository.refresh_core(now_utc=NOW_UTC, context=None)

    assert scales.state == "live"
    assert kp.state == "live"
    assert json.loads((tmp_path / "scales.json").read_text(encoding="utf-8"))[
        "schema"
    ] == 1
    assert untouched.read_bytes() == b"separate-source"
    assert untouched.stat().st_mtime_ns == untouched_mtime


def test_wind_source_failure_does_not_overwrite_either_last_good_file(tmp_path):
    first_http = FakeHttp(
        {
            WIND_SPEED_ENDPOINT: [_fixture("noaa_wind_speed.json")],
            WIND_MAG_ENDPOINT: [_fixture("noaa_wind_mag.json")],
        }
    )
    repository = SpaceWeatherRepository(cache_dir=tmp_path, http=first_http)
    repository.refresh_wind(now_utc=NOW_UTC, context=None)
    paths = [tmp_path / "wind_speed.json", tmp_path / "wind_mag.json"]
    snapshots = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
    repository.http = FakeHttp(
        {
            WIND_SPEED_ENDPOINT: [RuntimeError("speed offline")],
            WIND_MAG_ENDPOINT: [RuntimeError("mag offline")],
        }
    )

    speed, magnetic = repository.refresh_wind(
        now_utc=NOW_UTC + timedelta(minutes=40), context=None
    )

    assert speed.state == "stale_cache"
    assert magnetic.state == "stale_cache"
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths] == snapshots


def test_scales_forecast_dates_not_product_keys_control_order_and_probabilities(
    tmp_path,
):
    raw = _fixture("noaa_scales_shuffled_dates.json")

    normalized = normalize_scales(raw, now_utc=NOW_UTC)

    assert [entry["product_key"] for entry in normalized["timeline"]] == [
        "-1",
        "0",
        "2",
        "3",
        "1",
    ]
    assert [entry["valid_at_utc"] for entry in normalized["forecast_g"]] == [
        "2026-07-23T00:00:00Z",
        "2026-07-24T00:00:00Z",
        "2026-07-25T00:00:00Z",
    ]
    assert normalized["probabilities"] == {
        "valid_at_utc": "2026-07-23T00:00:00Z",
        "r_minor": 35,
        "r_major": 5,
        "s": 1,
    }

    scales, _kp = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=_core_http(raw, _fixture("noaa_kp_objects.json")),
    ).refresh_core(now_utc=NOW_UTC, context=None)
    assert scales.envelope.valid_from_utc == datetime(
        2026, 7, 23, tzinfo=timezone.utc
    )
    assert scales.envelope.valid_until_utc == datetime(
        2026, 7, 26, tzinfo=timezone.utc
    )


def test_scales_kp_wind_out_of_window_http_200_preserves_every_source_last_good(
    tmp_path,
):
    valid_http = FakeHttp(
        {
            SCALES_ENDPOINT: [_fixture("noaa_scales.json")],
            KP_ENDPOINT: [_fixture("noaa_kp_rows.json")],
            WIND_SPEED_ENDPOINT: [_fixture("noaa_wind_speed.json")],
            WIND_MAG_ENDPOINT: [_fixture("noaa_wind_mag.json")],
        }
    )
    repository = SpaceWeatherRepository(cache_dir=tmp_path, http=valid_http)
    repository.refresh_core(now_utc=NOW_UTC, context=None)
    repository.refresh_wind(now_utc=NOW_UTC, context=None)
    paths = [
        tmp_path / "scales.json",
        tmp_path / "kp.json",
        tmp_path / "wind_speed.json",
        tmp_path / "wind_mag.json",
    ]
    snapshots = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]

    old_scales = _fixture("noaa_scales.json")
    old_scales["0"]["TimeStamp"] = "08:00:00"
    old_kp = _fixture("noaa_kp_rows.json")
    old_kp[1][0] = "2026-07-22T00:00:00"
    old_kp[2][0] = "2026-07-22T03:00:00"
    old_speed = [
        {"proton_speed": 390, "time_tag": "2026-07-22T10:00:00Z"}
    ]
    old_mag = [
        {"bt": 4.0, "bz_gsm": -1.0, "time_tag": "2026-07-22T10:00:00Z"}
    ]
    repository.http = FakeHttp(
        {
            SCALES_ENDPOINT: [old_scales],
            KP_ENDPOINT: [old_kp],
            WIND_SPEED_ENDPOINT: [old_speed],
            WIND_MAG_ENDPOINT: [old_mag],
        }
    )

    core = repository.refresh_core(now_utc=NOW_UTC, context=None)
    wind = repository.refresh_wind(now_utc=NOW_UTC, context=None)

    assert [result.state for result in (*core, *wind)] == [
        "fresh_cache",
        "fresh_cache",
        "fresh_cache",
        "fresh_cache",
    ]
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths] == snapshots


def test_corrupt_cache_reader_uses_one_bounded_handle_not_path_read_bytes(
    monkeypatch, tmp_path
):
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=_core_http(
            _fixture("noaa_scales.json"), _fixture("noaa_kp_objects.json")
        ),
    )
    repository.refresh_core(now_utc=NOW_UTC, context=None)
    repository.http = _core_http(RuntimeError("offline"), RuntimeError("offline"))

    def reject_unbounded_read(_path):
        raise AssertionError("cache reader must not use Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", reject_unbounded_read)

    scales, kp = repository.refresh_core(
        now_utc=NOW_UTC + timedelta(minutes=5), context=None
    )

    assert scales.state == "fresh_cache"
    assert kp.state == "fresh_cache"


@pytest.mark.parametrize(
    ("source_name", "filename"),
    [
        ("scales", "scales.json"),
        ("kp", "kp.json"),
        ("wind_speed", "wind_speed.json"),
        ("wind_mag", "wind_mag.json"),
    ],
)
def test_corrupt_source_specific_normalized_payload_is_not_accepted(
    tmp_path, source_name, filename
):
    valid_responses = {
        SCALES_ENDPOINT: [_fixture("noaa_scales.json")],
        KP_ENDPOINT: [_fixture("noaa_kp_objects.json")],
        WIND_SPEED_ENDPOINT: [_fixture("noaa_wind_speed.json")],
        WIND_MAG_ENDPOINT: [_fixture("noaa_wind_mag.json")],
    }
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path, http=FakeHttp(valid_responses)
    )
    repository.refresh_core(now_utc=NOW_UTC, context=None)
    repository.refresh_wind(now_utc=NOW_UTC, context=None)
    target = tmp_path / filename
    malformed = json.loads(target.read_text(encoding="utf-8"))
    malformed["payload"] = {}
    target.write_text(json.dumps(malformed), encoding="utf-8")

    repository.http = FakeHttp(
        {
            SCALES_ENDPOINT: [RuntimeError("offline")],
            KP_ENDPOINT: [RuntimeError("offline")],
            WIND_SPEED_ENDPOINT: [RuntimeError("offline")],
            WIND_MAG_ENDPOINT: [RuntimeError("offline")],
        }
    )
    failed_results = {
        result.name: result
        for result in (
            *repository.refresh_core(now_utc=NOW_UTC, context=None),
            *repository.refresh_wind(now_utc=NOW_UTC, context=None),
        )
    }
    assert failed_results[source_name].state == "unavailable"
    assert failed_results[source_name].envelope is None

    repository.http = FakeHttp(
        {
            SCALES_ENDPOINT: [_fixture("noaa_scales.json")],
            KP_ENDPOINT: [_fixture("noaa_kp_objects.json")],
            WIND_SPEED_ENDPOINT: [_fixture("noaa_wind_speed.json")],
            WIND_MAG_ENDPOINT: [_fixture("noaa_wind_mag.json")],
        }
    )
    refreshed_results = {
        result.name: result
        for result in (
            *repository.refresh_core(now_utc=NOW_UTC, context=None),
            *repository.refresh_wind(now_utc=NOW_UTC, context=None),
        )
    }
    assert refreshed_results[source_name].state == "live"
    assert json.loads(target.read_text(encoding="utf-8"))["payload"]


def test_scales_cache_fetched_at_allows_five_minute_future_clock_skew(tmp_path):
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=_core_http(
            _fixture("noaa_scales.json"), _fixture("noaa_kp_objects.json")
        ),
    )
    repository.refresh_core(now_utc=NOW_UTC, context=None)
    path = tmp_path / "scales.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted["fetched_at_utc"] = "2026-07-22T12:25:00Z"
    path.write_text(json.dumps(persisted), encoding="utf-8")
    repository.http = _core_http(RuntimeError("offline"), RuntimeError("offline"))

    allowed, _kp = repository.refresh_core(now_utc=NOW_UTC, context=None)

    assert allowed.state == "fresh_cache"

    persisted["fetched_at_utc"] = "2026-07-22T12:25:01Z"
    path.write_text(json.dumps(persisted), encoding="utf-8")
    repository.http = _core_http(RuntimeError("offline"), RuntimeError("offline"))
    rejected, _kp = repository.refresh_core(now_utc=NOW_UTC, context=None)
    assert rejected.state == "unavailable"


@pytest.mark.parametrize(
    ("now_utc", "expected_state"),
    [
        (datetime(2026, 7, 22, 15, 30, tzinfo=timezone.utc), "live"),
        (datetime(2026, 7, 22, 15, 30, 1, tzinfo=timezone.utc), "stale_cache"),
        (datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc), "stale_cache"),
        (datetime(2026, 7, 22, 18, 0, 1, tzinfo=timezone.utc), "unavailable"),
    ],
)
def test_kp_three_hour_grace_and_six_hour_diagnostic_boundaries(
    tmp_path, now_utc, expected_state
):
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path / now_utc.isoformat().replace(":", "-"),
        http=_core_http(
            _fixture("noaa_scales.json"), _fixture("noaa_kp_rows.json")
        ),
    )

    _scales, kp = repository.refresh_core(now_utc=now_utc, context=None)

    assert kp.state == expected_state


@pytest.mark.parametrize(
    ("now_utc", "expected_state"),
    [
        (datetime(2026, 7, 22, 12, 48, tzinfo=timezone.utc), "live"),
        (datetime(2026, 7, 22, 12, 48, 1, tzinfo=timezone.utc), "stale_cache"),
        (datetime(2026, 7, 22, 13, 18, tzinfo=timezone.utc), "stale_cache"),
        (datetime(2026, 7, 22, 13, 18, 1, tzinfo=timezone.utc), "unavailable"),
    ],
)
def test_wind_and_magnetic_thirty_and_sixty_minute_boundaries(
    tmp_path, now_utc, expected_state
):
    magnetic = _fixture("noaa_wind_mag.json")
    magnetic[1]["time_tag"] = "2026-07-22T12:18:00Z"
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path / now_utc.isoformat().replace(":", "-"),
        http=FakeHttp(
            {
                WIND_SPEED_ENDPOINT: [_fixture("noaa_wind_speed.json")],
                WIND_MAG_ENDPOINT: [magnetic],
            }
        ),
    )

    speed, field = repository.refresh_wind(now_utc=now_utc, context=None)

    assert speed.state == expected_state
    assert field.state == expected_state


def test_corrupt_oversized_cache_is_bounded_and_unavailable(tmp_path):
    (tmp_path / "scales.json").write_bytes(b"{" + b"x" * MAX_SOURCE_CACHE_BYTES)
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=_core_http(RuntimeError("offline"), RuntimeError("offline")),
    )

    scales, _kp = repository.refresh_core(now_utc=NOW_UTC, context=None)

    assert scales.state == "unavailable"


def _alerts(*product_ids):
    wanted = set(product_ids)
    return [
        row
        for row in _fixture("noaa_alerts.json")
        if not wanted or row["product_id"] in wanted
    ]


def test_alert_fold_sorts_ascending_then_extends_and_cancels_by_serial():
    raw = _alerts("WARK05", "CWARK05")
    active_raw = raw[:2]
    active_raw.reverse()
    raw.reverse()

    state, alert = fold_alerts(active_raw, now_utc=NOW_UTC)
    cancelled_state, cancelled = fold_alerts(raw, now_utc=NOW_UTC)

    assert state == "active"
    assert alert["serial"] == "102"
    assert alert["severity"] == "R3"
    assert alert["valid_until"] == "2026-07-22T16:00:00Z"
    assert alert["display_until"] == "2026-07-22T16:00:00Z"
    assert alert["display_until_source"] == "provider"
    assert cancelled_state == "confirmed_empty"
    assert cancelled is None


def test_alert_fold_supersedes_prior_watches_and_uses_daily_table_end():
    state, alert = fold_alerts(
        list(reversed(_alerts("WATA20", "WATA10"))), now_utc=NOW_UTC
    )

    assert state == "active"
    assert alert["serial"] == "201"
    assert alert["kind"] == "WATCH"
    assert alert["valid_until"] is None
    assert alert["display_until"] == "2026-07-24T23:59:59Z"
    assert alert["display_until_source"] == "local_watch_forecast"


def test_alert_watch_daily_table_is_capped_at_issue_plus_ninety_six_hours():
    raw = [
        {
            "product_id": "WATA30",
            "issue_datetime": "2026-07-20T01:00:00Z",
            "message": (
                "Serial Number: 600\nWATCH: Geomagnetic Storm\n"
                "NOAA Scale: G3\nDay 1: 2026-07-23\nDay 2: 2026-07-25"
            ),
        }
    ]

    state, alert = fold_alerts(
        raw, now_utc=datetime(2026, 7, 22, tzinfo=timezone.utc)
    )

    assert state == "active"
    assert alert["display_until"] == "2026-07-24T01:00:00Z"


def test_alert_synoptic_period_is_event_metadata_not_alert_expiry():
    raw = _alerts("ALTK20")

    state, alert = fold_alerts(raw, now_utc=NOW_UTC)

    assert state == "active"
    assert alert["event_period_start"] == "2026-07-22T09:00:00Z"
    assert alert["event_period_end"] == "2026-07-22T12:00:00Z"
    assert alert["display_until"] == "2026-07-22T15:05:00Z"
    assert fold_alerts(
        raw, now_utc=datetime(2026, 7, 22, 15, 5, 1, tzinfo=timezone.utc)
    ) == ("confirmed_empty", None)


def test_alert_synoptic_period_without_date_uses_issue_utc_day():
    raw = _alerts("ALTK20")
    raw[0]["message"] = raw[0]["message"].replace(
        "2026-07-22 09:00-12:00 UTC", "0900-1200Z"
    )

    state, alert = fold_alerts(raw, now_utc=NOW_UTC)

    assert state == "active"
    assert alert["event_period_start"] == "2026-07-22T09:00:00Z"
    assert alert["event_period_end"] == "2026-07-22T12:00:00Z"


def test_alert_watch_reads_forecast_header_dates_without_day_labels():
    raw = [
        {
            "product_id": "WATA20",
            "issue_datetime": "2026-07-22T09:00:00Z",
            "message": (
                "Serial Number: 700\nWATCH: Geomagnetic Storm\n"
                "NOAA Scale: G2\nNOAA Kp index forecast 22 Jul - 24 Jul\n"
                "            Jul 22    Jul 23    Jul 24\n00-03UT       5         6         5"
            ),
        }
    ]

    state, alert = fold_alerts(raw, now_utc=NOW_UTC)

    assert state == "active"
    assert alert["display_until"] == "2026-07-24T23:59:59Z"


def test_alert_summary_uses_twenty_four_hours_and_warning_requires_validity():
    summary_state, summary = fold_alerts(_alerts("SUMX01"), now_utc=NOW_UTC)
    warning_state, warning = fold_alerts(_alerts("WARK50"), now_utc=NOW_UTC)

    assert summary_state == "active"
    assert summary["display_until"] == "2026-07-23T11:00:00Z"
    assert warning_state == "confirmed_empty"
    assert warning is None


def test_alert_priority_ignores_unknown_keyword_text_without_guessing():
    state, alert = fold_alerts(_fixture("noaa_alerts.json"), now_utc=NOW_UTC)

    assert state == "active"
    assert alert["serial"] == "300"
    assert alert["kind"] == "ALERT"
    assert alert["severity"] == "G2"


def test_alert_fold_ignores_one_malformed_structured_message_without_losing_valid_rows():
    malformed = {
        "product_id": "WARK40",
        "issue_datetime": "2026-07-22T12:10:00Z",
        "message": (
            "Serial Number: 800\nWARNING: Radio Blackout\n"
            "NOAA Scale: R4\nValid To: after lunch"
        ),
    }

    state, alert = fold_alerts(
        [malformed, *_alerts("ALTK20")], now_utc=NOW_UTC
    )

    assert state == "active"
    assert alert["serial"] == "300"


def test_alert_warning_watch_or_alert_precedes_summary_even_at_lower_scale():
    summary = _alerts("SUMX01")[0]
    summary["message"] = summary["message"].replace("R1", "R5")
    low_alert = _alerts("ALTK20")[0]
    low_alert["message"] = low_alert["message"].replace("G2", "G1")

    state, alert = fold_alerts([summary, low_alert], now_utc=NOW_UTC)

    assert state == "active"
    assert alert["serial"] == "300"
    assert alert["kind"] == "ALERT"


def test_alert_unknown_prose_with_cancel_reference_cannot_cancel_active_product():
    active = _alerts("ALTK20")[0]
    false_cancel = {
        "product_id": "UNKN99",
        "issue_datetime": "2026-07-22T12:10:00Z",
        "message": "Operator note only\nCancel Serial Number: 300",
    }

    state, alert = fold_alerts([active, false_cancel], now_utc=NOW_UTC)

    assert state == "active"
    assert alert["serial"] == "300"


def _same_serial_cross_product_alerts():
    return [
        {
            "product_id": "ALTK20",
            "issue_datetime": "2026-07-22T11:00:00Z",
            "message": (
                "Serial Number: 42\nALERT: Geomagnetic event\nNOAA Scale: G2"
            ),
        },
        {
            "product_id": "ALTX10",
            "issue_datetime": "2026-07-22T11:05:00Z",
            "message": "Serial Number: 42\nALERT: X-ray event\nNOAA Scale: R3",
        },
    ]


def test_alert_cancel_is_scoped_to_product_identity_when_serials_collide():
    cancel_xray = {
        "product_id": "CALTX10",
        "issue_datetime": "2026-07-22T11:10:00Z",
        "message": "CANCEL ALERT\nCancel Serial Number: 42",
    }

    state, alert = fold_alerts(
        [*_same_serial_cross_product_alerts(), cancel_xray], now_utc=NOW_UTC
    )

    assert state == "active"
    assert alert["product_id"] == "ALTK20"
    assert alert["serial"] == "42"


def test_alert_extension_replaces_only_its_product_when_serials_collide():
    extend_geomagnetic = {
        "product_id": "ALTK20",
        "issue_datetime": "2026-07-22T11:10:00Z",
        "message": (
            "Serial Number: 43\nEXTENDED ALERT: Geomagnetic event\n"
            "Extension to Serial Number: 42\nNOAA Scale: G1"
        ),
    }

    state, alert = fold_alerts(
        [*_same_serial_cross_product_alerts(), extend_geomagnetic], now_utc=NOW_UTC
    )

    assert state == "active"
    assert alert["product_id"] == "ALTX10"
    assert alert["serial"] == "42"
    assert alert["severity"] == "R3"


def test_alert_repository_distinguishes_live_fresh_empty_and_unavailable(tmp_path):
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=FakeHttp(
            {
                ALERTS_ENDPOINT: [
                    _alerts("ALTK20"),
                    RuntimeError("alerts offline"),
                    RuntimeError("alerts offline"),
                    RuntimeError("alerts offline"),
                ]
            }
        ),
    )

    live, live_state, live_alert = repository.refresh_alerts(
        now_utc=NOW_UTC, context=None
    )
    fresh, fresh_state, fresh_alert = repository.refresh_alerts(
        now_utc=NOW_UTC + timedelta(minutes=20), context=None
    )
    boundary, boundary_state, boundary_alert = repository.refresh_alerts(
        now_utc=NOW_UTC + timedelta(minutes=30), context=None
    )
    stale, stale_state, stale_alert = repository.refresh_alerts(
        now_utc=NOW_UTC + timedelta(minutes=30, seconds=1), context=None
    )

    assert (live.state, live_state, live_alert["serial"]) == ("live", "active", "300")
    assert (fresh.state, fresh_state, fresh_alert["serial"]) == (
        "fresh_cache",
        "active",
        "300",
    )
    assert (boundary.state, boundary_state, boundary_alert["serial"]) == (
        "fresh_cache",
        "active",
        "300",
    )
    assert stale.state == "stale_cache"
    assert (stale_state, stale_alert) == ("unavailable", None)


def test_successful_empty_alert_response_clears_old_selection_but_not_on_failure(
    tmp_path,
):
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=FakeHttp(
            {
                ALERTS_ENDPOINT: [
                    _alerts("ALTK20"),
                    [],
                    RuntimeError("alerts offline"),
                ]
            }
        ),
    )
    repository.refresh_alerts(now_utc=NOW_UTC, context=None)

    empty, empty_state, empty_alert = repository.refresh_alerts(
        now_utc=NOW_UTC + timedelta(minutes=1), context=None
    )
    failed, failed_state, failed_alert = repository.refresh_alerts(
        now_utc=NOW_UTC + timedelta(minutes=2), context=None
    )

    assert (empty.state, empty_state, empty_alert) == ("live", "confirmed_empty", None)
    assert json.loads((tmp_path / "alerts.json").read_text(encoding="utf-8"))[
        "payload"
    ]["candidates"] == []
    assert failed.state == "fresh_cache"
    assert (failed_state, failed_alert) == ("unavailable", None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product_id", ""),
        ("product_key", "wrong-family"),
        ("serial", ""),
        ("serial", "bad serial"),
        ("kind", "NOTICE"),
        ("severity", "R9"),
        ("severity", "R"),
        ("issue_datetime", "not-a-time"),
        ("valid_until", "not-a-time"),
        ("event_period_end", None),
        ("display_until", "2026-07-22T14:00:00Z"),
        ("headline", ""),
        ("display_until_source", "guessed"),
    ],
)
def test_alert_corrupt_cache_rejects_every_consumer_critical_field(
    tmp_path, field, value
):
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=FakeHttp({ALERTS_ENDPOINT: [_alerts("ALTK20")]}),
    )
    repository.refresh_alerts(now_utc=NOW_UTC, context=None)
    path = tmp_path / "alerts.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted["payload"]["candidates"][0][field] = value
    path.write_text(json.dumps(persisted), encoding="utf-8")
    repository.http = FakeHttp({ALERTS_ENDPOINT: [RuntimeError("alerts offline")]})

    result, alert_state, alert = repository.refresh_alerts(
        now_utc=NOW_UTC + timedelta(minutes=1), context=None
    )

    assert result.state == "unavailable"
    assert result.envelope is None
    assert (alert_state, alert) == ("unavailable", None)


@pytest.mark.parametrize("severity", ["R9", "R"])
def test_alert_malformed_cached_severity_is_unavailable_without_crashing_aggregate(
    tmp_path, severity
):
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=FakeHttp({ALERTS_ENDPOINT: [_alerts("ALTK20")]}),
    )
    repository.refresh_alerts(now_utc=NOW_UTC, context=None)
    path = tmp_path / "alerts.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted["payload"]["candidates"][0]["severity"] = severity
    path.write_text(json.dumps(persisted), encoding="utf-8")
    repository.http = FakeHttp(
        {
            SCALES_ENDPOINT: [_fixture("noaa_scales.json")],
            KP_ENDPOINT: [_fixture("noaa_kp_objects.json")],
            WIND_SPEED_ENDPOINT: [RuntimeError("wind offline")],
            WIND_MAG_ENDPOINT: [RuntimeError("mag offline")],
            ALERTS_ENDPOINT: [RuntimeError("alerts offline")],
            DONKI_FLR_ENDPOINT: [RuntimeError("DONKI offline")],
        }
    )

    snapshot = refresh_space_weather(
        repository,
        nasa_api_key="test-secret",
        now_utc=NOW_UTC + timedelta(minutes=1),
        context=None,
    )

    assert snapshot.alerts.state == "unavailable"
    assert snapshot.alert_state == "unavailable"
    assert snapshot.alert is None
    assert snapshot.aggregate_state is SourceProvenance.LIVE


def test_alert_watch_cache_rejects_non_day_end_local_display_semantics(tmp_path):
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=FakeHttp({ALERTS_ENDPOINT: [_alerts("WATA10")]}),
    )
    repository.refresh_alerts(now_utc=NOW_UTC, context=None)
    path = tmp_path / "alerts.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted["payload"]["candidates"][0]["display_until"] = (
        "2026-07-23T12:00:00Z"
    )
    path.write_text(json.dumps(persisted), encoding="utf-8")
    repository.http = FakeHttp({ALERTS_ENDPOINT: [RuntimeError("alerts offline")]})

    result, alert_state, alert = repository.refresh_alerts(
        now_utc=NOW_UTC + timedelta(minutes=1), context=None
    )

    assert result.state == "unavailable"
    assert (alert_state, alert) == ("unavailable", None)


def test_donki_selection_prefers_explicit_earth_impact_cme_then_best_flare():
    flr = _fixture("donki_flr.json")
    cme = _fixture("donki_cme.json")

    selected = select_donki_event(flr=flr, cme=cme, now_utc=NOW_UTC)
    flare_only = select_donki_event(flr=flr, cme=[], now_utc=NOW_UTC)

    assert selected == {
        "kind": "CME",
        "event_id": "2026-07-22T09:00:00-CME-003",
        "predicted_arrival_utc": "2026-07-22T12:50:00Z",
        "analysis_submitted_at_utc": "2026-07-22T11:30:00Z",
        "speed_km_s": 900.0,
        "source_note": "NASA experimental/model estimate",
    }
    assert flare_only["kind"] == "FLR"
    assert flare_only["event_id"] == "2026-07-22T10:00:00-FLR-004"
    assert flare_only["class_type"] == "X1.0"


def test_donki_does_not_infer_earth_impact_from_note_or_inaccurate_analysis():
    flare = _fixture("donki_flr.json")
    cme = _fixture("donki_cme.json")
    first_without_evidence = cme[0]
    inaccurate_only = dict(cme[1])
    inaccurate_only["cmeAnalyses"] = [cme[1]["cmeAnalyses"][0]]

    from_note = select_donki_event(
        flr=flare, cme=[first_without_evidence], now_utc=NOW_UTC
    )
    from_inaccurate = select_donki_event(
        flr=flare, cme=[inaccurate_only], now_utc=NOW_UTC
    )

    assert from_note["kind"] == "FLR"
    assert from_inaccurate["kind"] == "FLR"


def test_donki_same_event_keeps_latest_most_accurate_analysis():
    duplicates = [
        row
        for row in _fixture("donki_cme.json")
        if row["activityID"] == "2026-07-22T08:00:00-CME-002"
    ]

    selected = select_donki_event(flr=[], cme=duplicates, now_utc=NOW_UTC)

    assert selected["event_id"] == "2026-07-22T08:00:00-CME-002"
    assert selected["predicted_arrival_utc"] == "2026-07-22T13:00:00Z"
    assert selected["analysis_submitted_at_utc"] == "2026-07-22T11:00:00Z"


def test_donki_latest_most_accurate_analysis_replaces_older_earth_prediction():
    duplicate = [
        {
            "activityID": "CME-REVISED",
            "cmeAnalyses": [
                {
                    "isMostAccurate": True,
                    "submissionTime": "2026-07-22T10:00:00Z",
                    "speed": 1000,
                    "enlilList": [
                        {
                            "estimatedShockArrivalTime": "2026-07-22T13:00:00Z",
                            "isEarthGB": True,
                        }
                    ],
                }
            ],
        },
        {
            "activityID": "CME-REVISED",
            "cmeAnalyses": [
                {
                    "isMostAccurate": True,
                    "submissionTime": "2026-07-22T11:00:00Z",
                    "speed": 1000,
                    "enlilList": [
                        {
                            "estimatedShockArrivalTime": "2026-07-22T13:00:00Z",
                            "isEarthGB": False,
                            "impactList": [],
                        }
                    ],
                }
            ],
        },
    ]

    assert select_donki_event(flr=[], cme=duplicate, now_utc=NOW_UTC) is None


def test_donki_equal_submission_revisions_are_independent_of_input_order():
    def revision(arrival, speed):
        return {
            "activityID": "CME-EQUAL-TIME",
            "cmeAnalyses": [
                {
                    "isMostAccurate": True,
                    "submissionTime": "2026-07-22T11:00:00Z",
                    "speed": speed,
                    "enlilList": [
                        {
                            "estimatedShockArrivalTime": arrival,
                            "isEarthGB": True,
                        }
                    ],
                }
            ],
        }

    first = revision("2026-07-22T13:00:00Z", 1000)
    second = revision("2026-07-22T14:00:00Z", 1200)

    forward = select_donki_event(flr=[], cme=[first, second], now_utc=NOW_UTC)
    reversed_result = select_donki_event(
        flr=[], cme=[second, first], now_utc=NOW_UTC
    )

    assert forward == reversed_result


def test_donki_flare_equal_class_and_peak_uses_stable_event_id_tiebreaker():
    def flare(event_id):
        return {
            "flrID": event_id,
            "peakTime": "2026-07-22T11:00:00Z",
            "classType": "X1.0",
            "sourceLocation": "N00E00",
        }

    forward = select_donki_event(
        flr=[flare("FLR-A"), flare("FLR-B")], cme=[], now_utc=NOW_UTC
    )
    reversed_result = select_donki_event(
        flr=[flare("FLR-B"), flare("FLR-A")], cme=[], now_utc=NOW_UTC
    )

    assert forward == reversed_result
    assert forward["event_id"] == "FLR-B"


def test_donki_cme_arrival_window_is_inclusive_and_rejects_one_second_outside():
    def event(event_id, arrival):
        return {
            "activityID": event_id,
            "cmeAnalyses": [
                {
                    "isMostAccurate": True,
                    "submissionTime": "2026-07-22T12:00:00Z",
                    "speed": 800,
                    "enlilList": [
                        {
                            "estimatedShockArrivalTime": arrival,
                            "isEarthGB": True,
                        }
                    ],
                }
            ],
        }

    accepted = select_donki_event(
        flr=[],
        cme=[
            event("minus-six", "2026-07-22T06:20:00Z"),
            event("plus-seventy-two", "2026-07-25T12:20:00Z"),
        ],
        now_utc=NOW_UTC,
    )
    rejected = select_donki_event(
        flr=[],
        cme=[
            event("too-old", "2026-07-22T06:19:59Z"),
            event("too-far", "2026-07-25T12:20:01Z"),
        ],
        now_utc=NOW_UTC,
    )

    assert accepted["event_id"] == "minus-six"
    assert rejected is None


def test_donki_repository_uses_exact_query_windows_and_hides_diagnostic_stale(
    tmp_path,
):
    http = FakeHttp(
        {
            DONKI_FLR_ENDPOINT: [
                _fixture("donki_flr.json"),
                RuntimeError("FLR offline"),
                RuntimeError("FLR offline"),
            ],
            DONKI_CME_ENDPOINT: [
                _fixture("donki_cme.json"),
                RuntimeError("CME offline"),
            ],
        }
    )
    repository = SpaceWeatherRepository(cache_dir=tmp_path, http=http)

    live, live_event = repository.refresh_donki(
        nasa_api_key="test-secret", now_utc=NOW_UTC, context=None
    )
    boundary, boundary_event = repository.refresh_donki(
        nasa_api_key="test-secret",
        now_utc=NOW_UTC + timedelta(minutes=60),
        context=None,
    )
    stale, stale_event = repository.refresh_donki(
        nasa_api_key="test-secret",
        now_utc=NOW_UTC + timedelta(minutes=60, seconds=1),
        context=None,
    )

    assert live.state == "live"
    assert live_event["kind"] == "CME"
    assert http.calls[0][2]["params"] == {
        "startDate": "2026-07-21",
        "endDate": "2026-07-22",
        "api_key": "test-secret",
    }
    assert http.calls[1][2]["params"] == {
        "startDate": "2026-07-15",
        "endDate": "2026-07-22",
        "api_key": "test-secret",
    }
    assert all("test-secret" not in endpoint for _method, endpoint, _kwargs in http.calls)
    assert boundary.state == "fresh_cache"
    assert boundary_event["kind"] == "CME"
    assert stale.state == "stale_cache"
    assert stale.envelope is not None
    assert stale_event is None
    assert (tmp_path / "donki.json").is_file()


def test_donki_combined_cache_above_generic_limit_remains_readable_on_failure(
    monkeypatch, tmp_path
):
    padding = "x" * 1_100_000
    flr = [{"flrID": "ignored-flare", "padding": padding}]
    cme = [
        {
            "activityID": "ignored-cme",
            "cmeAnalyses": [],
            "padding": padding,
        }
    ]
    probe = SpaceWeatherRepository(
        cache_dir=tmp_path / "probe",
        http=FakeHttp({DONKI_FLR_ENDPOINT: [flr], DONKI_CME_ENDPOINT: [cme]}),
    )
    probe.refresh_donki(
        nasa_api_key="test-secret", now_utc=NOW_UTC, context=None
    )
    cache_limit = (tmp_path / "probe" / "donki.json").stat().st_size
    monkeypatch.setattr(space_weather_module, "MAX_DONKI_CACHE_BYTES", cache_limit)
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path / "bounded",
        http=FakeHttp({DONKI_FLR_ENDPOINT: [flr], DONKI_CME_ENDPOINT: [cme]}),
    )
    live, event = repository.refresh_donki(
        nasa_api_key="test-secret", now_utc=NOW_UTC, context=None
    )
    path = tmp_path / "bounded" / "donki.json"
    repository.http = FakeHttp(
        {
            DONKI_FLR_ENDPOINT: [RuntimeError("DONKI offline")],
            DONKI_CME_ENDPOINT: [RuntimeError("DONKI offline")],
        }
    )

    cached, cached_event = repository.refresh_donki(
        nasa_api_key="test-secret",
        now_utc=NOW_UTC + timedelta(minutes=1),
        context=None,
    )

    assert live.state == "live"
    assert event is None
    assert path.stat().st_size == cache_limit
    assert cache_limit > MAX_SOURCE_CACHE_BYTES
    assert cached.state == "fresh_cache"
    assert cached.envelope is not None
    assert cached_event is None


def test_donki_cache_is_diagnostic_at_exactly_twenty_four_hours_then_unavailable(
    tmp_path,
):
    repository = SpaceWeatherRepository(
        cache_dir=tmp_path,
        http=FakeHttp(
            {
                DONKI_FLR_ENDPOINT: [
                    _fixture("donki_flr.json"),
                    RuntimeError("DONKI offline"),
                    RuntimeError("DONKI offline"),
                ],
                DONKI_CME_ENDPOINT: [_fixture("donki_cme.json")],
            }
        ),
    )
    repository.refresh_donki(
        nasa_api_key="test-secret", now_utc=NOW_UTC, context=None
    )

    boundary, boundary_event = repository.refresh_donki(
        nasa_api_key="test-secret",
        now_utc=NOW_UTC + timedelta(hours=24),
        context=None,
    )
    expired, expired_event = repository.refresh_donki(
        nasa_api_key="test-secret",
        now_utc=NOW_UTC + timedelta(hours=24, seconds=1),
        context=None,
    )

    assert boundary.state == "stale_cache"
    assert boundary.envelope is not None
    assert boundary_event is None
    assert expired.state == "unavailable"
    assert expired.envelope is None
    assert expired_event is None


def _source_result(name, state, payload=None, error=None, observed_at=NOW_UTC):
    envelope = None
    if payload is not None:
        envelope = SourceEnvelope(
            schema=1,
            endpoint=f"https://example.test/{name}",
            fetched_at_utc=NOW_UTC,
            observed_at_utc=observed_at,
            issued_at_utc=None,
            valid_from_utc=None,
            valid_until_utc=None,
            payload=payload,
            raw_digest="0" * 64,
        )
    return SourceResult(name=name, state=state, envelope=envelope, error=error)


class AggregateRepository:
    def __init__(self, *, core, wind, alerts, donki):
        self.core = core
        self.wind = wind
        self.alerts = alerts
        self.donki = donki

    def refresh_core(self, **_kwargs):
        return self.core

    def refresh_wind(self, **_kwargs):
        return self.wind

    def refresh_alerts(self, **_kwargs):
        return self.alerts

    def refresh_donki(self, **_kwargs):
        return self.donki


def _aggregate_repository(*, core_state="live", wind_state="fresh_cache"):
    scales = _source_result(
        "scales",
        core_state,
        {
            "current": {"g": 2, "r": 1, "s": 0},
            "forecast_g": [],
            "probabilities": {
                "valid_at_utc": "2026-07-23T00:00:00Z",
                "r_minor": 55,
                "r_major": 10,
                "s": 5,
            },
        },
        error="scales offline" if core_state != "live" else None,
        observed_at=datetime(2026, 7, 22, 12, 15, tzinfo=timezone.utc),
    )
    kp = _source_result(
        "kp",
        core_state,
        {
            "current": {
                "time_tag": "2026-07-22T12:00:00Z",
                "kp": 4.67,
                "observed": "estimated",
                "noaa_scale": "G1",
            },
            "forecast_48h": [],
            "predicted_peak": {
                "time_tag": "2026-07-23T18:00:00Z",
                "kp": 6.33,
                "observed": "predicted",
                "noaa_scale": "G2",
            },
        },
        error="kp offline" if core_state != "live" else None,
        observed_at=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
    )
    speed = _source_result(
        "wind_speed",
        wind_state,
        {"observed_at_utc": "2026-07-22T12:18:00Z", "speed_km_s": 455.0},
        observed_at=datetime(2026, 7, 22, 12, 18, tzinfo=timezone.utc),
    )
    magnetic = _source_result(
        "wind_mag", "unavailable", error="magnetic field offline"
    )
    alerts = (
        _source_result("alerts", "unavailable", error="alerts offline"),
        "unavailable",
        None,
    )
    donki = (_source_result("donki", "unavailable", error="DONKI offline"), None)
    return AggregateRepository(
        core=(scales, kp), wind=(speed, magnetic), alerts=alerts, donki=donki
    )


def test_aggregate_snapshot_maps_provider_fields_and_optional_failures_do_not_fail_core():
    snapshot = refresh_space_weather(
        _aggregate_repository(),
        nasa_api_key="test-secret",
        now_utc=NOW_UTC,
        context=None,
    )

    assert snapshot.current_scales == {"g": 2, "r": 1, "s": 0}
    assert snapshot.current_kp == {
        "value": 4.67,
        "mode": "estimated",
        "time_tag": "2026-07-22T12:00:00Z",
        "noaa_scale": "G1",
    }
    assert snapshot.forecast_48h == {
        "max_kp": 6.33,
        "noaa_scale": "G2",
        "time_tag": "2026-07-23T18:00:00Z",
    }
    assert snapshot.solar_wind == {
        "speed_km_s": 455.0,
        "time_tag": "2026-07-22T12:18:00Z",
    }
    assert snapshot.probabilities == {
        "r1_r2": 55,
        "r3_r5": 10,
        "s1_plus": 5,
        "forecast_date": "2026-07-23T00:00:00Z",
    }
    assert snapshot.oldest_core_observed_at_utc == datetime(
        2026, 7, 22, 12, 0, tzinfo=timezone.utc
    )
    assert snapshot.aggregate_state is SourceProvenance.LIVE
    assert not any("mandatory-core failure" in error for error in snapshot.errors)
    assert any("alerts offline" in error for error in snapshot.errors)


def test_aggregate_reports_current_cycle_core_failure_with_readable_diagnostic_cache():
    repository = _aggregate_repository(core_state="fresh_cache")

    snapshot = refresh_space_weather(
        repository,
        nasa_api_key="test-secret",
        now_utc=NOW_UTC,
        context=None,
    )

    assert snapshot.current_scales == {"g": 2, "r": 1, "s": 0}
    assert snapshot.current_kp["value"] == 4.67
    assert snapshot.aggregate_state is SourceProvenance.FRESH_CACHE
    assert "mandatory-core failure: scales" in snapshot.errors
    assert "mandatory-core failure: kp" in snapshot.errors


def test_aggregate_provenance_uses_only_values_that_are_actually_displayed():
    stale_repository = _aggregate_repository(wind_state="stale_cache")
    stale_repository.wind = (
        stale_repository.wind[0],
        _source_result(
            "wind_mag",
            "stale_cache",
            {
                "observed_at_utc": "2026-07-22T11:30:00Z",
                "bt_nt": 5.0,
                "bz_gsm_nt": -1.0,
                "bz_direction": "south",
            },
        ),
    )
    stale_snapshot = refresh_space_weather(
        stale_repository,
        nasa_api_key="test-secret",
        now_utc=NOW_UTC,
        context=None,
    )
    unavailable = _source_result("missing", "unavailable", error="offline")
    empty_repository = AggregateRepository(
        core=(unavailable, unavailable),
        wind=(unavailable, unavailable),
        alerts=(unavailable, "unavailable", None),
        donki=(unavailable, None),
    )
    empty_snapshot = refresh_space_weather(
        empty_repository,
        nasa_api_key="test-secret",
        now_utc=NOW_UTC,
        context=None,
    )

    assert not stale_snapshot.solar_wind
    assert not stale_snapshot.magnetic_field
    assert stale_snapshot.sources["wind_speed"].envelope is not None
    assert stale_snapshot.sources["wind_mag"].envelope is not None
    assert stale_snapshot.aggregate_state is SourceProvenance.LIVE
    assert empty_snapshot.aggregate_state is SourceProvenance.LOCAL_FALLBACK
    assert not empty_snapshot.current_scales
    assert not empty_snapshot.current_kp


def _apod_page_module():
    return importlib.import_module("plugins.apod.apod_page")


def _page_record(
    *,
    title_en="The Corona Australis Molecular Cloud and the Chamaeleon Cluster",
    title_zh="南冕座分子云与变色龙星团",
    copyright="NASA / APOD",
):
    return ApodRecord(
        selection_key="fixture-2026-07-22",
        requested_device_date="2026-07-22",
        date="2026-07-22",
        media_type="image",
        title_en=title_en,
        title_zh=title_zh,
        translation_state="live" if title_zh else "unavailable",
        explanation="Deterministic renderer fixture.",
        copyright=copyright,
        url="https://example.test/apod.jpg",
        hdurl=None,
        image_url="https://example.test/apod.jpg",
        image_cache_key="fixture-image",
        fetched_at_utc=NOW_UTC,
        source_state="live",
        warning=None,
    )


def _page_weather():
    return refresh_space_weather(
        _aggregate_repository(),
        nasa_api_key="test-secret",
        now_utc=NOW_UTC,
        context=None,
    )


PHOTO_RED = (219, 48, 45)
PHOTO_GREEN = (40, 180, 99)
PHOTO_BLUE = (42, 96, 209)
PHOTO_YELLOW = (244, 194, 13)
PHOTO_COLORS = {PHOTO_RED, PHOTO_GREEN, PHOTO_BLUE, PHOTO_YELLOW}


def _quadrant_image(size, *, orientation=None):
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width // 2 - 1, height // 2 - 1), fill=PHOTO_RED)
    draw.rectangle((width // 2, 0, width - 1, height // 2 - 1), fill=PHOTO_GREEN)
    draw.rectangle((0, height // 2, width // 2 - 1, height - 1), fill=PHOTO_BLUE)
    draw.rectangle((width // 2, height // 2, width - 1, height - 1), fill=PHOTO_YELLOW)
    if orientation is not None:
        image.getexif()[274] = orientation
    return image


def _photo_edge_pixels(image):
    width, height = image.size
    return (
        [image.getpixel((x, 0)) for x in range(width)]
        + [image.getpixel((x, height - 1)) for x in range(width)]
        + [image.getpixel((0, y)) for y in range(height)]
        + [image.getpixel((width - 1, y)) for y in range(height)]
    )


@pytest.mark.parametrize(
    ("source", "photo_size"),
    [
        (_quadrant_image((1200, 400)), (432, 299)),
        (_quadrant_image((400, 1200)), (432, 299)),
        (_quadrant_image((240, 480), orientation=6), (432, 299)),
    ],
    ids=["wide", "tall", "exif-rotated"],
)
def test_apod_page_cover_crop_is_centered_and_every_photo_edge_is_content(
    source, photo_size
):
    page = _apod_page_module()

    fitted = page.fit_photo(source, photo_size)
    expected = ImageOps.fit(
        ImageOps.exif_transpose(source).convert("RGB"),
        photo_size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    assert fitted.mode == "RGB"
    assert fitted.size == photo_size
    assert fitted.tobytes() == expected.tobytes()
    assert all(
        pixel not in {(0, 0, 0), (255, 255, 255)}
        for pixel in _photo_edge_pixels(fitted)
    )


def test_apod_page_short_caption_has_fixed_photo_and_divider_boundaries():
    page = _apod_page_module()
    source = Image.new("RGB", (960, 640), PHOTO_BLUE)

    rendered = page.render_apod_page(
        apod=_page_record(title_en="Aurora", title_zh="极光"),
        title_zh="极光",
        translation_unavailable=False,
        weather=_page_weather(),
        source_image=source,
        rendered_at_utc=NOW_UTC,
    )

    assert rendered.mode == "RGB"
    assert rendered.size == (800, 480)
    assert rendered.getpixel((500, 363)) == PHOTO_BLUE
    assert rendered.getpixel((500, 364)) == page.ORANGE_COLOR
    assert rendered.getpixel((500, 367)) == page.CAPTION_COLOR
    assert rendered.getpixel((366, 200)) == page.DIVIDER_COLOR
    assert rendered.getpixel((367, 200)) == page.DIVIDER_COLOR
    assert rendered.getpixel((368, 200)) == PHOTO_BLUE


def test_apod_page_uses_every_approved_fixed_rectangle_exactly():
    page = _apod_page_module()

    assert page.HEADER_RECT == (0, 0, 800, 65)
    assert page.LEFT_RECT == (0, 65, 368, 480)
    assert page.KP_RECT == (20, 77, 354, 180)
    assert page.GRS_RECT == (20, 190, 354, 218)
    assert page.METRICS_RECT == (20, 228, 354, 318)
    assert page.PROBABILITIES_RECT == (20, 326, 354, 364)
    assert page.ALERT_RECT == (20, 374, 354, 414)
    assert page.SOURCE_RECT == (20, 456, 354, 476)


@pytest.mark.parametrize(
    "source",
    [
        _quadrant_image((1200, 400)),
        _quadrant_image((400, 1200)),
        _quadrant_image((240, 480), orientation=6),
    ],
    ids=["wide", "tall", "exif-rotated"],
)
def test_apod_page_composed_photo_edges_are_exact_cover_pixels(source):
    page = _apod_page_module()
    expected = page.fit_photo(source, (432, 299))

    rendered = page.render_apod_page(
        apod=_page_record(title_en="Aurora", title_zh="极光"),
        title_zh="极光",
        translation_unavailable=False,
        weather=_page_weather(),
        source_image=source,
        rendered_at_utc=NOW_UTC,
    )

    edge_points = {
        (0, 0),
        (216, 0),
        (431, 0),
        (0, 149),
        (431, 149),
        (0, 298),
        (216, 298),
        (431, 298),
    }
    for x, y in edge_points:
        assert rendered.getpixel((368 + x, 65 + y)) == expected.getpixel((x, y))
    assert rendered.getpixel((366, 65)) == page.DIVIDER_COLOR
    assert rendered.getpixel((367, 479)) == page.DIVIDER_COLOR
    assert rendered.getpixel((368, 364)) == page.ORANGE_COLOR
    assert rendered.getpixel((799, 364)) == page.ORANGE_COLOR
    assert rendered.getpixel((500, 367)) == page.CAPTION_COLOR


def test_apod_page_maximum_caption_has_fixed_photo_boundary():
    page = _apod_page_module()
    title_en = "i" * 300
    title_zh = "星" * 60
    source = Image.new("RGB", (960, 640), PHOTO_GREEN)
    apod = _page_record(title_en=title_en, title_zh=title_zh)
    measurement = page.measure_apod_page(
        apod=apod,
        title_zh=title_zh,
        translation_unavailable=False,
    )

    rendered = page.render_apod_page(
        apod=apod,
        title_zh=title_zh,
        translation_unavailable=False,
        weather=_page_weather(),
        source_image=source,
        rendered_at_utc=NOW_UTC,
        measurement=measurement,
    )

    caption_top = measurement.caption.caption_top
    assert page.CAPTION_MIN_TOP <= caption_top <= page.CAPTION_MAX_TOP
    assert rendered.getpixel((500, caption_top - 1)) == PHOTO_GREEN
    assert rendered.getpixel((500, caption_top)) == page.ORANGE_COLOR


@pytest.mark.parametrize(
    "candidate",
    [
        "english",
        "chinese",
        "combined",
    ],
)
def test_apod_caption_layout_uses_true_fonts_and_preserves_complete_text_or_rejects(
    candidate
):
    page = _apod_page_module()
    title_en = "i" * 300 if candidate in {"english", "combined"} else "Aurora"
    title_zh = "星" * 60 if candidate in {"chinese", "combined"} else None
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))

    try:
        layout = page._layout_caption(
            draw=draw,
            title_en=title_en,
            title_zh=title_zh,
            translation_unavailable=False,
        )
    except page.ApodPageLayoutError:
        return

    assert isinstance(layout.title_en_font, ImageFont.FreeTypeFont)
    assert "".join(layout.title_en_lines) == title_en
    assert 11 <= layout.title_en_font.size <= 14
    assert len(layout.title_en_lines) <= 6
    assert not any("..." in line or "…" in line for line in layout.title_en_lines)
    if title_zh:
        assert isinstance(layout.title_zh_font, ImageFont.FreeTypeFont)
        assert 16 <= layout.title_zh_font.size <= 20
        assert "".join(layout.title_zh_lines) == title_zh
        assert len(layout.title_zh_lines) <= 3
        assert not any("..." in line or "…" in line for line in layout.title_zh_lines)
    assert 300 <= layout.caption_top <= 364


def test_apod_caption_allows_complete_english_through_six_lines():
    page = _apod_page_module()
    title_en = "i" * 600
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))

    layout = page._layout_caption(
        draw=draw,
        title_en=title_en,
        title_zh=None,
        translation_unavailable=False,
        copyright="NASA / APOD",
        apod_date="2026-07-22",
    )

    assert "".join(layout.title_en_lines) == title_en
    assert 4 <= len(layout.title_en_lines) <= 6
    assert 11 <= layout.title_en_font.size <= 14


def test_apod_caption_rejects_chinese_that_needs_more_than_three_lines():
    page = _apod_page_module()
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))

    with pytest.raises(page.ApodPageLayoutError, match="3-line"):
        page._layout_caption(
            draw=draw,
            title_en="Aurora",
            title_zh="星" * 100,
            translation_unavailable=False,
            copyright="NASA / APOD",
            apod_date="2026-07-22",
        )


def test_apod_caption_rejects_previous_clamped_title_overflow():
    page = _apod_page_module()
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))

    with pytest.raises(page.ApodPageLayoutError, match="180"):
        page._layout_caption(
            draw=draw,
            title_en="i" * 600,
            title_zh="星" * 60,
            translation_unavailable=False,
            copyright="NASA / APOD",
            apod_date="2026-07-22",
        )


def test_apod_caption_legal_measured_180px_case_hides_kicker_and_stays_separate():
    page = _apod_page_module()
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))

    layout = page._layout_caption(
        draw=draw,
        title_en="i" * 300,
        title_zh="星" * 60,
        translation_unavailable=False,
        copyright="NASA / APOD",
        apod_date="2026-07-22",
    )

    assert 300 <= layout.caption_top <= 301
    assert 179 <= layout.caption_height <= 180
    assert layout.show_kicker is False
    assert layout.title_bottom <= layout.credit_y
    assert layout.credit_bottom <= layout.date_y
    assert layout.date_bottom <= 480


def test_apod_page_real_multiline_credit_preserves_every_character_within_budget(
    monkeypatch,
):
    page = _apod_page_module()
    copyright_text = (
        "DES/DOE/FNAL/DECam/CTIO/NOIRLab/NSF/AURA\n"
        "Image Processing: T.A. Rector (UAA/NOIRLab), "
        "R. Colombari & M. Zamani (NOIRLab)"
    )
    apod = _page_record(
        title_en="The Corona Australis Molecular Cloud",
        title_zh="南冕座分子云",
        copyright=copyright_text,
    )
    measurement = page.measure_apod_page(
        apod=apod,
        title_zh=apod.title_zh,
        translation_unavailable=False,
    )
    layout = measurement.caption

    expected_credit = f"CREDIT | {' '.join(copyright_text.split())}"
    drawn_credit = "".join(layout.credit_lines)
    assert drawn_credit == expected_credit
    assert 1 <= len(layout.credit_lines) <= 2
    assert layout.meta_font.size >= 10
    assert 120 <= layout.caption_height <= 180
    assert layout.title_bottom <= layout.credit_y
    assert layout.credit_bottom <= layout.date_y
    assert layout.date_bottom <= 480
    assert measurement.photo_rect == (368, 65, 800, layout.caption_top)
    assert measurement.photo_size == (432, layout.caption_top - 65)

    original_text = ImageDraw.ImageDraw.text
    drawn_text = []
    kp_calls = []

    def capture_text(draw, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(draw, xy, text, *args, **kwargs)

    def capture_kp(draw, rect, snapshot, fonts):
        kp_calls.append(rect)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    monkeypatch.setattr(page, "_draw_kp_panel", capture_kp)
    source = Image.new("RGB", (960, 640), PHOTO_BLUE)
    rendered = page.render_apod_page(
        apod=apod,
        title_zh=apod.title_zh,
        translation_unavailable=False,
        weather=_page_weather(),
        source_image=source,
        rendered_at_utc=NOW_UTC,
        measurement=measurement,
    )

    assert kp_calls == [page.KP_RECT]
    assert all(line in drawn_text for line in layout.credit_lines)
    assert not any("..." in line or "…" in line for line in layout.credit_lines)
    assert rendered.getpixel((500, layout.caption_top - 1)) == PHOTO_BLUE
    assert rendered.getpixel((500, layout.caption_top)) == page.ORANGE_COLOR


def test_apod_caption_flattens_provider_credit_whitespace_before_wrapping():
    page = _apod_page_module()
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))
    copyright_text = (
        "Monica Mesa\n"
        "Text:\n"
        "Cecilia Chirenti\n"
        "(NASA\n"
        "GSFC,\n"
        "UMCP,\n"
        "CRESST II)"
    )

    layout = page._layout_caption(
        draw=draw,
        title_en="The Large Magellanic Cloud",
        title_zh="大麦哲伦云",
        translation_unavailable=False,
        copyright=copyright_text,
        apod_date="2026-07-23",
    )

    assert layout.credit_lines == (
        "CREDIT | Monica Mesa Text: Cecilia Chirenti "
        "(NASA GSFC, UMCP, CRESST II)",
    )
    assert layout.caption_top == page.CAPTION_MAX_TOP


def test_apod_caption_rewrites_legacy_warning_separator_for_bold_font():
    page = _apod_page_module()
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))

    layout = page._layout_caption(
        draw=draw,
        title_en="Fallback APOD",
        title_zh="回退天文图",
        translation_unavailable=False,
        copyright="NASA / APOD",
        apod_date="2026-07-21",
        warning="LATEST AVAILABLE · APOD 2026-07-21",
    )

    assert layout.show_kicker is True
    assert layout.kicker_copy == "LATEST AVAILABLE | APOD 2026-07-21"


def test_apod_page_uses_clear_ascii_separators_without_moving_kp_rect(monkeypatch):
    page = _apod_page_module()
    original_text = ImageDraw.ImageDraw.text
    drawn_text = []
    kp_calls = []

    def capture_text(draw, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(draw, xy, text, *args, **kwargs)

    def capture_kp(draw, rect, snapshot, fonts):
        kp_calls.append(rect)
        return original_kp(draw, rect, snapshot, fonts)

    original_kp = page._draw_kp_panel
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    monkeypatch.setattr(page, "_draw_kp_panel", capture_kp)

    page.render_apod_page(
        apod=_page_record(title_en="Aurora", title_zh="极光"),
        title_zh="极光",
        translation_unavailable=False,
        weather=_page_weather(),
        source_image=Image.new("RGB", (960, 640), PHOTO_BLUE),
        rendered_at_utc=NOW_UTC,
    )

    assert kp_calls == [page.KP_RECT]
    assert not any("·" in text or "•" in text for text in drawn_text)
    assert "当前地磁指数 | CURRENT KP" in drawn_text
    assert "太阳风 | WIND" in drawn_text
    assert "CREDIT | NASA / APOD" in drawn_text
    assert "NASA APOD | 2026-07-22" in drawn_text


def test_apod_caption_jointly_fits_complete_titles_and_official_three_part_credit():
    page = _apod_page_module()
    title_en = "The Corona Australis Molecular Cloud and the Chandelier Cluster"
    title_zh = (
        "南冕座分子云的壮丽广域影像：尘埃、气体与年轻恒星共同勾勒出"
        "银河系附近恒星诞生区域的复杂结构和绚丽宇宙风景"
    )
    copyright_text = (
        "DES/DOE/FNAL/DECam/CTIO/NOIRLab/NSF/AURA\n"
        "Image Processing: T.A. Rector (UAA/NOIRLab), "
        "R. Colombari & M. Zamani (NOIRLab)\n"
        "Text: Keighley Rockcliffe (NASA GSFC, UMBC CSST, CRESST II)"
    )
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))

    layout = page._layout_caption(
        draw=draw,
        title_en=title_en,
        title_zh=title_zh,
        translation_unavailable=False,
        copyright=copyright_text,
        apod_date="2026-07-22",
    )

    assert "".join(layout.title_en_lines) == title_en
    assert "".join(layout.title_zh_lines) == title_zh
    assert "".join(layout.credit_lines) == (
        f"CREDIT | {' '.join(copyright_text.split())}"
    )
    assert 11 <= layout.title_en_font.size <= 14
    assert layout.title_zh_font is not None
    assert 16 <= layout.title_zh_font.size <= 20
    assert (
        layout.title_en_font.size < 14 or layout.title_zh_font.size < 20
    )
    assert layout.show_kicker is True
    assert layout.meta_font.size >= 10
    assert 120 <= layout.caption_height <= 180
    assert layout.caption_top >= 300
    assert layout.title_bottom <= layout.credit_y
    assert layout.credit_bottom <= layout.date_y
    assert layout.date_bottom <= 480
    assert not any(
        "..." in line or "…" in line
        for line in (
            *layout.title_en_lines,
            *layout.title_zh_lines,
            *layout.credit_lines,
        )
    )


@pytest.mark.parametrize(
    "scenario",
    [
        "short-english",
        "long-english",
        "long-chinese",
        "translation-unavailable",
        "missing-copyright",
        "missing-weather",
        "maximum-caption",
    ],
)
def test_apod_page_kp_rect_is_direct_and_invariant(monkeypatch, scenario):
    page = _apod_page_module()
    title_en = "Aurora"
    title_zh = "极光"
    translation_unavailable = False
    copyright_text = "NASA / APOD"
    weather = _page_weather()
    if scenario == "long-english":
        title_en = "i" * 300
    elif scenario == "long-chinese":
        title_zh = "星" * 60
    elif scenario == "translation-unavailable":
        title_zh = None
        translation_unavailable = True
    elif scenario == "missing-copyright":
        copyright_text = None
    elif scenario == "missing-weather":
        weather = replace(
            weather,
            current_scales={},
            current_kp={},
            forecast_48h={},
            solar_wind={},
            magnetic_field={},
            probabilities={},
        )
    elif scenario == "maximum-caption":
        title_en = "i" * 300
        title_zh = "星" * 60

    calls = []

    def capture(draw, rect, snapshot, fonts):
        calls.append((rect, snapshot, fonts))

    monkeypatch.setattr(page, "_draw_kp_panel", capture)
    page.render_apod_page(
        apod=_page_record(
            title_en=title_en,
            title_zh=title_zh,
            copyright=copyright_text,
        ),
        title_zh=title_zh,
        translation_unavailable=translation_unavailable,
        weather=weather,
        source_image=Image.new("RGB", (960, 640), PHOTO_RED),
        rendered_at_utc=NOW_UTC,
    )

    assert len(calls) == 1
    assert calls[0][0] == (20, 77, 354, 180)
    assert calls[0][0] == page.KP_RECT
    assert calls[0][1] is weather


def test_apod_page_left_text_bboxes_stop_at_x_354_and_never_use_ellipsis(
    monkeypatch,
):
    page = _apod_page_module()
    original_text = ImageDraw.ImageDraw.text
    calls = []

    def capture_text(draw, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        bbox = draw.textbbox(xy, str(text), font=font)
        calls.append((xy, str(text), bbox))
        return original_text(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    page.render_apod_page(
        apod=_page_record(),
        title_zh="南冕座分子云与变色龙星团",
        translation_unavailable=False,
        weather=_page_weather(),
        source_image=Image.new("RGB", (960, 640), PHOTO_YELLOW),
        rendered_at_utc=NOW_UTC,
    )

    left_calls = [call for call in calls if call[0][0] < 368 and call[0][1] >= 65]
    assert left_calls
    assert all(bbox[2] <= 354 for _xy, _text, bbox in left_calls)
    assert not any("..." in text or "…" in text for _xy, text, _bbox in calls)
    all_copy = " | ".join(text for _xy, text, _bbox in calls)
    for required_copy in (
        "4.7",
        "MODE estimated",
        "48H PEAK | Kp 6.3 / G2",
        "G2",
        "R1",
        "S0",
        "455 km/s",
        "磁场 Bz",
        "磁场强度 Bt",
        "R1–R2",
        "55%",
        "R3–R5",
        "10%",
        "S1+",
        "5%",
        "NOAA ALERTS",
        "NOAA SWPC | OBS 12:00Z | CACHE 12:20Z",
    ):
        assert required_copy in all_copy


def test_apod_page_renders_magnetic_field_from_aggregate_projection(monkeypatch):
    page = _apod_page_module()
    repository = _aggregate_repository(wind_state="live")
    repository.wind = (
        repository.wind[0],
        _source_result(
            "wind_mag",
            "live",
            {
                "observed_at_utc": "2026-07-22T12:11:00Z",
                "bt_nt": 5.2,
                "bz_gsm_nt": -0.04,
                "bz_direction": "south",
            },
            observed_at=datetime(2026, 7, 22, 12, 11, tzinfo=timezone.utc),
        ),
    )
    weather = refresh_space_weather(
        repository,
        nasa_api_key="test-secret",
        now_utc=NOW_UTC,
        context=None,
    )
    assert dict(weather.magnetic_field) == {
        "bt_nt": 5.2,
        "bz_nt": -0.04,
        "direction": "south",
        "time_tag": "2026-07-22T12:11:00Z",
    }

    original_text = ImageDraw.ImageDraw.text
    calls = []

    def capture_text(draw, xy, text, *args, **kwargs):
        calls.append(str(text))
        return original_text(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    page.render_apod_page(
        apod=_page_record(),
        title_zh="南冕座分子云与变色龙星团",
        translation_unavailable=False,
        weather=weather,
        source_image=Image.new("RGB", (960, 640), PHOTO_BLUE),
        rendered_at_utc=NOW_UTC,
    )

    assert "south -0.04 nT" in calls
    assert "5.2 nT" in calls


def test_apod_page_draws_every_required_role_at_approved_true_font_size(
    monkeypatch,
):
    page = _apod_page_module()
    original_text = ImageDraw.ImageDraw.text
    calls = []

    def capture_text(draw, xy, text, *args, **kwargs):
        calls.append((str(text), kwargs.get("font")))
        return original_text(draw, xy, text, *args, **kwargs)

    weather = replace(
        _page_weather(),
        magnetic_field={
            "bt_nt": 5.2,
            "bz_nt": -0.04,
            "direction": "south",
        },
        alert_state="active",
        alert={
            "kind": "WATCH",
            "severity": "G2",
            "headline": "Geomagnetic storm watch",
        },
        donki_event={"kind": "FLR", "class_type": "M7.2"},
    )
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    page.render_apod_page(
        apod=_page_record(title_en="Aurora", title_zh="极光"),
        title_zh="极光",
        translation_unavailable=False,
        weather=weather,
        source_image=Image.new("RGB", (960, 640), PHOTO_BLUE),
        rendered_at_utc=NOW_UTC,
    )

    by_text = {text: font for text, font in calls}
    assert all(isinstance(font, ImageFont.FreeTypeFont) for _text, font in calls)
    assert all(font.size >= 10 for _text, font in calls)
    assert 26 <= by_text["NASAPics × SPACE WEATHER"].size <= 29
    assert by_text["2026-07-22 12:20Z"].size >= 10
    assert 36 <= by_text["4.7"].size <= 44
    assert 16 <= by_text["CURRENT G2 | MODE estimated"].size <= 20
    assert by_text["太阳风 | WIND"].size >= 12
    assert 17 <= by_text["455 km/s"].size <= 19
    assert by_text["NOAA WATCH | G2 | Geomagnetic storm watch"].size >= 12
    assert by_text["NOAA SWPC | OBS 12:00Z | CACHE 12:20Z"].size >= 10
    assert by_text["CREDIT | NASA / APOD"].size >= 10
    assert by_text["NASA APOD | 2026-07-22"].size >= 10
    assert 16 <= by_text["极光"].size <= 20
    assert 11 <= by_text["Aurora"].size <= 14


def test_apod_page_active_noaa_alert_wins_over_donki_and_preserves_full_copy(
    monkeypatch,
):
    page = _apod_page_module()
    original_text = ImageDraw.ImageDraw.text
    calls = []

    def capture_text(draw, xy, text, *args, **kwargs):
        calls.append((xy, str(text), kwargs.get("font")))
        return original_text(draw, xy, text, *args, **kwargs)

    weather = replace(
        _page_weather(),
        alert_state="active",
        alert={
            "kind": "WATCH",
            "severity": "G2",
            "headline": "Geomagnetic storm watch",
        },
        donki_event={"kind": "FLR", "class_type": "M7.2"},
    )
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    page.render_apod_page(
        apod=_page_record(title_en="Aurora", title_zh="极光"),
        title_zh="极光",
        translation_unavailable=False,
        weather=weather,
        source_image=Image.new("RGB", (960, 640), PHOTO_BLUE),
        rendered_at_utc=NOW_UTC,
    )

    alert_calls = [
        (text, font)
        for (x, y), text, font in calls
        if page.ALERT_RECT[0] <= x < page.ALERT_RECT[2]
        and page.ALERT_RECT[1] <= y < page.ALERT_RECT[3]
    ]
    assert "".join(text for text, _font in alert_calls) == (
        "NOAA WATCH | G2 | Geomagnetic storm watch"
    )
    assert alert_calls
    assert all(font.size >= 12 for _text, font in alert_calls)
    assert not any("DONKI" in text for text, _font in alert_calls)


def test_apod_page_rejects_required_active_alert_that_cannot_fit_at_12px():
    page = _apod_page_module()
    weather = replace(
        _page_weather(),
        alert_state="active",
        alert={
            "kind": "WARNING",
            "severity": "G4",
            "headline": "W" * 120,
        },
    )

    with pytest.raises(page.ApodPageLayoutError, match="active alert"):
        page.render_apod_page(
            apod=_page_record(title_en="Aurora", title_zh="极光"),
            title_zh="极光",
            translation_unavailable=False,
            weather=weather,
            source_image=Image.new("RGB", (960, 640), PHOTO_BLUE),
            rendered_at_utc=NOW_UTC,
        )


def test_apod_page_rejects_non_approved_dimensions():
    page = _apod_page_module()

    with pytest.raises(ValueError, match="800x480"):
        page.render_apod_page(
            apod=_page_record(),
            title_zh="极光",
            translation_unavailable=False,
            weather=_page_weather(),
            source_image=Image.new("RGB", (960, 640), PHOTO_BLUE),
            rendered_at_utc=NOW_UTC,
            dimensions=(600, 448),
        )


# Task 5: APOD media, fallback, translation, orchestration, and admission.


def _image_bytes(size=(960, 640), *, color=PHOTO_BLUE, image_format="JPEG"):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=image_format)
    return buffer.getvalue()


def _task5_record(
    *,
    apod_date="2026-07-22",
    media_type="image",
    title="A Coronal Aurora",
    url="https://media.example.test/standard.jpg",
    hdurl="https://media.example.test/hd.jpg",
    copyright="NASA Test Team",
):
    return ApodRecord(
        selection_key=f"selection-{apod_date}",
        requested_device_date="2026-07-22",
        date=apod_date,
        media_type=media_type,
        title_en=title,
        title_zh=None,
        translation_state="pending",
        explanation="One exact APOD response.",
        copyright=copyright,
        url=url,
        hdurl=hdurl,
        image_url=None,
        image_cache_key=None,
        fetched_at_utc=NOW_UTC,
        source_state="live",
        warning=None,
    )


def _persist_valid_task5_fallback_state(paths):
    selection = apod_module._resolve_selection(
        settings={},
        device_day=date(2026, 7, 22),
        paths=paths,
        rng=random.Random(1),
    )
    requested = replace(
        _task5_record(
            media_type="video",
            url="https://media.example.test/video",
            hdurl=None,
        ),
        selection_key=selection.fingerprint,
        requested_device_date=selection.device_day,
    )
    fallback_url = "https://media.example.test/fallback-state.jpg"
    displayed = replace(
        _task5_record(
            apod_date="2026-07-21",
            url=fallback_url,
            hdurl=None,
        ),
        selection_key=selection.fingerprint,
        requested_device_date=selection.device_day,
        warning="LATEST AVAILABLE · APOD 2026-07-21",
        image_url=fallback_url,
        image_cache_key=hashlib.sha256(fallback_url.encode("utf-8")).hexdigest(),
    )
    apod_module._persist_apod_state(
        paths,
        apod_module.ApodDisplayState(
            selection_fingerprint=selection.fingerprint,
            device_day=selection.device_day,
            requested_date=requested.date,
            requested_record=requested,
            display_record=displayed,
            fallback_reason="video",
            provisional_media=False,
        ),
    )
    return selection, paths.cache / "apod-state.json"


def test_apod_persisted_fallback_state_accepts_legacy_middle_dot_warning(
    apod_storage,
):
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace="state-valid-legacy-warning",
    )
    selection, state_path = _persist_valid_task5_fallback_state(paths)

    loaded = apod_module._read_apod_state(state_path, selection=selection)

    assert loaded is not None
    assert loaded.display_record.warning == (
        "LATEST AVAILABLE · APOD 2026-07-21"
    )


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("display_record", "date"), "2026-07-22"),
        (("display_record", "date"), "2026-07-23"),
        (("display_record", "date"), "2026-07-14"),
        (("display_record", "warning"), "LATEST AVAILABLE · APOD 2026-07-20"),
        (("display_record", "requested_device_date"), "2026-07-21"),
        (("requested_record", "requested_device_date"), "2026-07-21"),
        (("requested_record", "warning"), "LATEST AVAILABLE · APOD 2026-07-21"),
        (("display_record", "selection_key"), "0" * 64),
        (("requested_record", "selection_key"), "0" * 64),
        (("display_record", "image_cache_key"), "0" * 64),
        (("requested_record", "media_type"), "other"),
    ],
    ids=[
        "same-day",
        "future",
        "older-than-seven-days",
        "warning-mismatch",
        "display-device-day",
        "requested-device-day",
        "requested-warning",
        "display-fingerprint",
        "requested-fingerprint",
        "display-image-key",
        "fallback-reason-media-type",
    ],
)
def test_apod_persisted_fallback_state_rejects_semantic_corruption(
    apod_storage,
    field_path,
    value,
):
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace=f"state-semantic-{field_path[0]}-{value}",
    )
    selection, state_path = _persist_valid_task5_fallback_state(paths)
    document = json.loads(state_path.read_text(encoding="utf-8"))
    document[field_path[0]][field_path[1]] = value
    state_path.write_text(json.dumps(document), encoding="utf-8")

    assert apod_module._read_apod_state(state_path, selection=selection) is None


def test_apod_persisted_same_day_state_requires_admitted_requested_media(
    apod_storage,
):
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace="state-same-day-admission",
    )
    selection = apod_module._resolve_selection(
        settings={},
        device_day=date(2026, 7, 22),
        paths=paths,
        rng=random.Random(1),
    )
    media_url = "https://media.example.test/same-day-state.jpg"
    admitted = replace(
        _task5_record(url=media_url, hdurl=None),
        selection_key=selection.fingerprint,
        requested_device_date=selection.device_day,
        image_url=media_url,
        image_cache_key=hashlib.sha256(media_url.encode("utf-8")).hexdigest(),
    )
    apod_module._persist_apod_state(
        paths,
        apod_module.ApodDisplayState(
            selection_fingerprint=selection.fingerprint,
            device_day=selection.device_day,
            requested_date=admitted.date,
            requested_record=admitted,
            display_record=admitted,
            fallback_reason=None,
            provisional_media=False,
        ),
    )
    state_path = paths.cache / "apod-state.json"
    document = json.loads(state_path.read_text(encoding="utf-8"))
    document["requested_record"]["image_url"] = None
    document["requested_record"]["image_cache_key"] = None
    state_path.write_text(json.dumps(document), encoding="utf-8")

    assert apod_module._read_apod_state(state_path, selection=selection) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document.__setitem__("schema", "1"),
        lambda document: document.__setitem__("schema", True),
        lambda document: document.__setitem__("requested_date", "20260722"),
        lambda document: document["requested_record"].__setitem__(
            "requested_device_date",
            "20260722",
        ),
        lambda document: document["requested_record"].__setitem__(
            "date",
            "20260722",
        ),
        lambda document: document["display_record"].__setitem__(
            "requested_device_date",
            "20260722",
        ),
        lambda document: document["display_record"].__setitem__(
            "date",
            "20260721",
        ),
    ],
    ids=[
        "string-schema",
        "boolean-schema",
        "compact-requested-date",
        "compact-requested-device-date",
        "compact-requested-record-date",
        "compact-display-device-date",
        "compact-display-record-date",
    ],
)
def test_apod_state_rejects_non_strict_schema_and_noncanonical_dates(
    apod_storage,
    mutate,
):
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace="state-strict-schema-date",
    )
    selection, state_path = _persist_valid_task5_fallback_state(paths)
    document = json.loads(state_path.read_text(encoding="utf-8"))
    mutate(document)
    state_path.write_text(json.dumps(document), encoding="utf-8")

    assert apod_module._read_apod_state(state_path, selection=selection) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://media.example.test/a.jpg?X-Amz-Credential=secret",
        "https://media.example.test/a.jpg?Policy=secret",
        "https://media.example.test/a.jpg?Signature=secret",
        "https://media.example.test/a.jpg?Key-Pair-Id=secret",
        "https://media.example.test/a.jpg?X-Goog-Signature=secret",
        "https://media.example.test/a.jpg?skoid=secret",
        "https://media.example.test/a.jpg?sig=secret",
        "https://media.example.test/a.jpg?client_secret=secret",
        "https://media.example.test/a.jpg?credential=secret",
        "https://media.example.test/a.jpg?auth=secret",
        "https://media.example.test/a.jpg?X-Amz-Signature=",
        "https://media.example.test/a.jpg?authToken=secret",
        "https://media.example.test/a.jpg?secretKey=secret",
        "https://media.example.test/a.jpg?accessKeyId=secret",
        "https://media.example.test/a.jpg?password=secret",
        "https://media.example.test/a.jpg?passwd=secret",
        "https://media.example.test/a.jpg?pwd=secret",
    ],
)
def test_apod_media_url_rejects_signed_and_credential_query_forms(url):
    with pytest.raises(ValueError, match="credential|sensitive|query"):
        apod_module._public_http_url(url)


def test_apod_media_url_does_not_treat_unrelated_monkey_query_as_a_key_secret():
    url = "https://media.example.test/a.jpg?monkey=capuchin"

    assert apod_module._public_http_url(url) == url


def test_apod_production_media_host_policy_is_nasa_only(apod_test_media_policy):
    policy = apod_test_media_policy.production_policy

    assert policy is not None
    assert policy("nasa.gov") is True
    assert policy("apod.nasa.gov") is True
    assert policy("media.example.test") is False
    assert policy("nasa.gov.example.test") is False
    assert policy("127.0.0.1.nip.io") is False


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/image.jpg",
        "http://[::1]/image.jpg",
        "http://10.0.0.7/image.jpg",
        "http://192.168.1.9/image.jpg",
        "http://169.254.169.254/latest/meta-data",
        "http://224.0.0.1/image.jpg",
        "http://0.0.0.0/image.jpg",
        "http://localhost/image.jpg",
        "http://nas.local/image.jpg",
        "http://printer/image.jpg",
        "http://127.0.0.1.nip.io/image.jpg",
        "http://localtest.me/image.jpg",
    ],
)
def test_apod_media_url_rejects_local_private_and_non_global_targets(url):
    with pytest.raises(ValueError, match="public|host|address|local"):
        apod_module._public_http_url(url)


def test_apod_download_transport_rejects_nasa_hostname_resolving_private(
    monkeypatch,
    apod_test_media_policy,
):
    transport = apod_test_media_policy.production_transport
    assert transport is not None
    monkeypatch.setattr(
        apod_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("10.0.0.8", 443),
            )
        ],
    )

    with pytest.raises(ValueError, match="public|address|host"):
        transport("apod.nasa.gov", 443, context=None)


def test_apod_media_download_pins_numeric_peer_and_preserves_tls_host(
    monkeypatch,
    tmp_path,
):
    payload = _image_bytes()
    connections = []
    tls_hosts = []

    class Connection:
        def __init__(self):
            self.sent = b""
            self.closed = False

        def settimeout(self, _timeout):
            return None

        def sendall(self, payload_bytes):
            self.sent += payload_bytes

        def close(self):
            self.closed = True

    connection = Connection()

    monkeypatch.setattr(
        apod_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            )
        ],
    )

    def connect(endpoint, *, timeout):
        connections.append((endpoint, timeout))
        return connection

    monkeypatch.setattr(apod_module.socket, "create_connection", connect)

    class TlsContext:
        def wrap_socket(self, raw_socket, *, server_hostname):
            assert raw_socket is connection
            tls_hosts.append(server_hostname)
            return raw_socket

    monkeypatch.setattr(
        apod_module.ssl,
        "create_default_context",
        lambda: TlsContext(),
    )

    class Response:
        status = 200
        headers = {"Content-Length": str(len(payload))}

        def __init__(self):
            self.remaining = payload

        def begin(self):
            return None

        def read(self, _chunk_size):
            chunk, self.remaining = self.remaining, b""
            return chunk

        def close(self):
            return None

    monkeypatch.setattr(
        apod_module.http.client,
        "HTTPResponse",
        lambda active_connection: Response(),
    )
    target = tmp_path / "pinned.img"

    apod_module._download_apod_media_to_file(
        "https://apod.nasa.gov/apod/image/example.jpg?size=full",
        target,
        context=None,
        timeout=10,
        max_bytes=len(payload) + 1,
    )

    assert connections[0][0] == ("8.8.8.8", 443)
    assert tls_hosts == ["apod.nasa.gov"]
    assert b"GET /apod/image/example.jpg?size=full HTTP/1.1" in connection.sent
    assert b"Host: apod.nasa.gov" in connection.sent
    assert target.read_bytes() == payload

    Response.status = 302
    redirect_target = tmp_path / "redirect.img"
    with pytest.raises(apod_module.ApodMediaUnavailable, match="could not be reached"):
        apod_module._download_apod_media_to_file(
            "https://apod.nasa.gov/apod/image/redirect.jpg",
            redirect_target,
            context=None,
            timeout=10,
            max_bytes=len(payload) + 1,
        )
    assert not redirect_target.exists()


def test_apod_media_download_rejects_mixed_public_private_dns_before_connect(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        apod_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            ),
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("10.0.0.8", 443),
            ),
        ],
    )
    monkeypatch.setattr(
        apod_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail(
            "mixed DNS answers must fail before connect"
        ),
    )

    with pytest.raises(ValueError, match="public|address|host"):
        apod_module._download_apod_media_to_file(
            "https://apod.nasa.gov/apod/image/example.jpg",
            tmp_path / "mixed.img",
            context=None,
            timeout=10,
            max_bytes=1024,
        )


@pytest.mark.parametrize("abort_type", [TaskCancelled, TaskDeadlineExceeded])
def test_apod_pinned_download_propagates_abort_after_dns_without_connecting(
    monkeypatch,
    tmp_path,
    abort_type,
):
    signal = abort_type("abort after DNS")

    class Context:
        cancelled = False

        def raise_if_cancelled(self):
            if self.cancelled:
                raise signal

    context = Context()

    def resolve(*_args, **_kwargs):
        context.cancelled = True
        return [
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            )
        ]

    monkeypatch.setattr(apod_module.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(
        apod_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail(
            "abort after DNS must precede connection"
        ),
    )
    target = tmp_path / "abort.img"

    with pytest.raises(abort_type) as caught:
        apod_module._download_apod_media_to_file(
            "https://apod.nasa.gov/apod/image/example.jpg",
            target,
            context=context,
            timeout=10,
            max_bytes=1024,
        )

    assert caught.value is signal
    assert not target.exists()


def test_apod_pinned_download_uses_one_total_deadline_across_all_addresses(
    monkeypatch,
    tmp_path,
):
    now = [100.0]
    connect_calls = []
    socket_timeouts = []
    payload = _image_bytes()

    monkeypatch.setattr(
        apod_module,
        "_media_monotonic",
        lambda: now[0],
        raising=False,
    )

    def resolve(*_args, **_kwargs):
        now[0] += 1.0
        return [
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            ),
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("1.1.1.1", 443),
            ),
        ]

    monkeypatch.setattr(apod_module.socket, "getaddrinfo", resolve)

    class Connection:
        def settimeout(self, timeout):
            socket_timeouts.append(timeout)

        def sendall(self, _payload):
            now[0] += 1.0

        def close(self):
            return None

    connection = Connection()

    def connect(endpoint, *, timeout):
        connect_calls.append((endpoint, timeout))
        if endpoint[0] == "8.8.8.8":
            now[0] += 2.0
            raise OSError("first approved address unavailable")
        now[0] += 1.0
        return connection

    monkeypatch.setattr(apod_module.socket, "create_connection", connect)

    class TlsContext:
        def wrap_socket(self, raw_socket, *, server_hostname):
            assert raw_socket is connection
            assert server_hostname == "apod.nasa.gov"
            now[0] += 1.0
            return raw_socket

    monkeypatch.setattr(
        apod_module.ssl,
        "create_default_context",
        lambda: TlsContext(),
    )

    class Response:
        status = 200
        headers = {"Content-Length": str(len(payload))}

        def __init__(self):
            self.remaining = payload

        def begin(self):
            now[0] += 1.0

        def read(self, _chunk_size):
            chunk, self.remaining = self.remaining, b""
            if chunk:
                now[0] += 1.0
            return chunk

        def close(self):
            return None

    monkeypatch.setattr(
        apod_module.http.client,
        "HTTPResponse",
        lambda _connection: Response(),
    )

    apod_module._download_apod_media_to_file(
        "https://apod.nasa.gov/apod/image/deadline.jpg",
        tmp_path / "deadline.img",
        context=None,
        timeout=10.0,
        max_bytes=len(payload) + 1,
    )

    assert [call[0] for call in connect_calls] == [
        ("8.8.8.8", 443),
        ("1.1.1.1", 443),
    ]
    assert [call[1] for call in connect_calls] == pytest.approx([9.0, 7.0])
    assert socket_timeouts == pytest.approx([6.0, 5.0, 4.0, 3.0, 2.0])


def test_apod_blocking_dns_is_bounded_by_default_media_deadline(
    monkeypatch,
    tmp_path,
):
    now = [50.0]
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    wait_slices = []

    monkeypatch.setattr(
        apod_module,
        "_media_monotonic",
        lambda: now[0],
        raising=False,
    )

    def blocking_resolver(*_args, **_kwargs):
        if threading.current_thread() is threading.main_thread():
            raise AssertionError("DNS resolution must not block the task thread")
        resolver_started.set()
        release_resolver.wait()
        return [
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            )
        ]

    monkeypatch.setattr(apod_module.socket, "getaddrinfo", blocking_resolver)

    def fake_wait(done, timeout):
        assert resolver_started.wait(timeout=1.0)
        wait_slices.append(timeout)
        now[0] += timeout
        return done.is_set()

    monkeypatch.setattr(
        apod_module,
        "_wait_for_dns_completion",
        fake_wait,
        raising=False,
    )
    monkeypatch.setattr(
        apod_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail(
            "expired DNS must not enter connect"
        ),
    )

    try:
        with pytest.raises(TaskDeadlineExceeded):
            apod_module._download_apod_media_to_file(
                "https://apod.nasa.gov/apod/image/dns-timeout.jpg",
                tmp_path / "dns-timeout.img",
                context=None,
                timeout=0.12,
                max_bytes=1024,
            )
    finally:
        release_resolver.set()

    assert sum(wait_slices) == pytest.approx(0.12)
    assert not (tmp_path / "dns-timeout.img").exists()


def test_apod_blocking_dns_propagates_external_cancellation_before_connect(
    monkeypatch,
    tmp_path,
):
    signal = TaskCancelled("cancel blocked DNS")
    now = [75.0]
    resolver_started = threading.Event()
    release_resolver = threading.Event()

    class Context:
        cancelled = False

        def raise_if_cancelled(self):
            if self.cancelled:
                raise signal

        def remaining_seconds(self):
            return 60.0

    context = Context()
    monkeypatch.setattr(
        apod_module,
        "_media_monotonic",
        lambda: now[0],
        raising=False,
    )

    def blocking_resolver(*_args, **_kwargs):
        if threading.current_thread() is threading.main_thread():
            raise AssertionError("DNS resolution must not block the task thread")
        resolver_started.set()
        release_resolver.wait()
        return [
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            )
        ]

    monkeypatch.setattr(apod_module.socket, "getaddrinfo", blocking_resolver)

    def cancel_while_waiting(_done, _timeout):
        assert resolver_started.wait(timeout=1.0)
        context.cancelled = True
        return False

    monkeypatch.setattr(
        apod_module,
        "_wait_for_dns_completion",
        cancel_while_waiting,
        raising=False,
    )
    monkeypatch.setattr(
        apod_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail(
            "cancelled DNS must not enter connect"
        ),
    )

    try:
        with pytest.raises(TaskCancelled) as caught:
            apod_module._download_apod_media_to_file(
                "https://apod.nasa.gov/apod/image/dns-cancel.jpg",
                tmp_path / "dns-cancel.img",
                context=context,
                timeout=10.0,
                max_bytes=1024,
            )
    finally:
        release_resolver.set()

    assert caught.value is signal
    assert not (tmp_path / "dns-cancel.img").exists()


def test_apod_repeated_blocking_dns_timeouts_use_one_bounded_worker(
    monkeypatch,
    tmp_path,
):
    now = [125.0]
    active_attempt = [0]
    resolver_started = [threading.Event(), threading.Event()]
    release_resolvers = threading.Event()
    workers = []

    monkeypatch.setattr(
        apod_module,
        "_media_monotonic",
        lambda: now[0],
        raising=False,
    )

    def blocking_resolver(*_args, **_kwargs):
        worker_index = len(workers)
        workers.append(threading.current_thread())
        if worker_index < len(resolver_started):
            resolver_started[worker_index].set()
        release_resolvers.wait()
        return [
            (
                apod_module.socket.AF_INET,
                apod_module.socket.SOCK_STREAM,
                apod_module.socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            )
        ]

    monkeypatch.setattr(apod_module.socket, "getaddrinfo", blocking_resolver)

    def fake_dns_wait(done, timeout):
        assert resolver_started[active_attempt[0]].wait(timeout=1.0)
        now[0] += timeout
        return done.is_set()

    def fake_worker_slot_wait(timeout):
        slot = getattr(apod_module, "_DNS_WORKER_SLOT", None)
        if slot is None or slot.acquire(blocking=False):
            return True
        now[0] += timeout
        return False

    monkeypatch.setattr(
        apod_module,
        "_wait_for_dns_completion",
        fake_dns_wait,
        raising=False,
    )
    monkeypatch.setattr(
        apod_module,
        "_wait_for_dns_worker_slot",
        fake_worker_slot_wait,
        raising=False,
    )
    monkeypatch.setattr(
        apod_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail(
            "expired DNS must not enter connect"
        ),
    )

    try:
        for attempt in range(2):
            active_attempt[0] = attempt
            with pytest.raises(TaskDeadlineExceeded):
                apod_module._download_apod_media_to_file(
                    "https://apod.nasa.gov/apod/image/bounded-dns.jpg",
                    tmp_path / f"bounded-dns-{attempt}.img",
                    context=None,
                    timeout=0.12,
                    max_bytes=1024,
                )
        assert len(workers) == 1
        assert workers[0].daemon is True
    finally:
        release_resolvers.set()
        for worker in tuple(workers):
            worker.join(timeout=1.0)


@pytest.mark.parametrize(
    ("standard", "hd", "expected_standard", "expected_hd"),
    [
        (
            "http://127.0.0.1/private.jpg",
            "https://media.example.test/safe-hd.jpg",
            None,
            "https://media.example.test/safe-hd.jpg",
        ),
        (
            "https://media.example.test/safe-standard.jpg",
            "https://media.example.test/hd.jpg?X-Amz-Signature=secret",
            "https://media.example.test/safe-standard.jpg",
            None,
        ),
    ],
)
def test_apod_fetch_validates_standard_and_hd_candidates_independently(
    standard,
    hd,
    expected_standard,
    expected_hd,
):
    class Http:
        def request_json(self, *_args, **_kwargs):
            return SimpleNamespace(
                data={
                    "date": "2026-07-22",
                    "media_type": "image",
                    "title": "Safe independent candidate",
                    "url": standard,
                    "hdurl": hd,
                }
            )

    record = apod_module._fetch_apod_record(
        http=Http(),
        api_key="nasa-key",
        requested_date="2026-07-22",
        context=None,
    )

    assert record.url == expected_standard
    assert record.hdurl == expected_hd


def test_apod_fetch_rejects_image_when_both_media_candidates_are_unsafe():
    class Http:
        def request_json(self, *_args, **_kwargs):
            return SimpleNamespace(
                data={
                    "date": "2026-07-22",
                    "media_type": "image",
                    "title": "Unsafe candidates",
                    "url": "http://localhost/standard.jpg",
                    "hdurl": "https://media.example.test/hd.jpg?Signature=secret",
                }
            )

    with pytest.raises(RuntimeError, match="NASA APOD"):
        apod_module._fetch_apod_record(
            http=Http(),
            api_key="nasa-key",
            requested_date="2026-07-22",
            context=None,
        )


class _MediaHttp:
    def __init__(self, payloads):
        self.payloads = dict(payloads)
        self.downloads = []

    def stream_to_file(self, method, url, path, **kwargs):
        self.downloads.append(
            {
                "method": method,
                "url": url,
                "path": Path(path),
                "kwargs": kwargs,
            }
        )
        payload = self.payloads[url]
        if isinstance(payload, Exception):
            raise payload
        Path(path).write_bytes(payload)
        return SimpleNamespace(status=200, data=Path(path), headers={}, url=url)


def test_apod_media_prefers_valid_standard_url_without_touching_hd(
    monkeypatch, apod_storage
):
    standard = "https://media.example.test/standard.jpg"
    hd = "https://media.example.test/hd.jpg"
    http = _MediaHttp({standard: _image_bytes(), hd: _image_bytes((1600, 1200))})
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http, raising=False)
    paths = apod_module._instance_paths(apod_storage, preview_namespace="media-standard")

    blob, selected_url = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=_task5_record(url=standard, hdurl=hd),
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )

    digest = hashlib.sha256(standard.encode("utf-8")).hexdigest()
    assert selected_url == standard
    assert digest in blob.name
    assert blob.is_file()
    assert [item["url"] for item in http.downloads] == [standard]


def test_apod_media_download_disables_redirect_following(
    monkeypatch, apod_storage
):
    media_url = "https://media.example.test/no-redirect.jpg"
    http = _MediaHttp({media_url: _image_bytes()})
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace="media-no-redirect",
    )

    apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=_task5_record(url=media_url, hdurl=None),
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )

    assert http.downloads[0]["kwargs"]["allow_redirects"] is False


def test_apod_invalid_cached_blob_is_removed_before_managed_lru_read(
    monkeypatch, apod_storage
):
    media_url = "https://media.example.test/corrupt-cache.jpg"
    http = _MediaHttp({media_url: RuntimeError("offline")})
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace="media-corrupt-hit",
    )
    namespace = apod_storage.managed_cache_namespace(paths.media)
    monkeypatch.setattr(
        apod_storage,
        "managed_cache_namespace",
        lambda _directory: namespace,
    )
    digest = hashlib.sha256(media_url.encode("utf-8")).hexdigest()
    target = namespace.path(digest, suffix=".img")
    target.write_bytes(b"corrupt-image")
    managed_reads = []
    real_get = namespace.get_bytes

    def recording_get(key, *, suffix=""):
        managed_reads.append((key, suffix))
        return real_get(key, suffix=suffix)

    monkeypatch.setattr(namespace, "get_bytes", recording_get)

    with pytest.raises(apod_module.ApodMediaUnavailable):
        apod_module._resolve_media_blob(
            plugin=apod_storage,
            record=_task5_record(url=media_url, hdurl=None),
            paths=paths,
            minimum_size=(432, 299),
            context=None,
        )

    assert managed_reads == []
    assert not target.exists()


@pytest.mark.parametrize(
    "standard_payload",
    [b"not-an-image", _image_bytes((200, 120))],
    ids=["corrupt", "undersized"],
)
def test_apod_media_uses_hd_only_after_standard_is_invalid_or_undersized(
    monkeypatch, apod_storage, standard_payload
):
    standard = "https://media.example.test/standard.jpg"
    hd = "https://media.example.test/hd.jpg"
    http = _MediaHttp(
        {standard: standard_payload, hd: _image_bytes((1600, 1200))}
    )
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http, raising=False)
    paths = apod_module._instance_paths(apod_storage, preview_namespace="media-hd")

    blob, selected_url = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=_task5_record(url=standard, hdurl=hd),
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )

    assert selected_url == hd
    assert blob.is_file()
    assert [item["url"] for item in http.downloads] == [standard, hd]


def test_apod_media_reuses_persisted_hd_without_retrying_invalid_standard(
    monkeypatch, apod_storage
):
    standard = "https://media.example.test/persisted-standard.jpg"
    hd = "https://media.example.test/persisted-hd.jpg"
    http = _MediaHttp(
        {standard: b"not-an-image", hd: _image_bytes((1600, 1200))}
    )
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace="media-persisted-hd",
    )
    record = _task5_record(url=standard, hdurl=hd)

    first_blob, first_url = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=record,
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )
    persisted = replace(
        record,
        image_url=first_url,
        image_cache_key=hashlib.sha256(first_url.encode("utf-8")).hexdigest(),
    )
    http.downloads.clear()

    second_blob, second_url = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=persisted,
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )

    assert first_url == second_url == hd
    assert first_blob == second_blob
    assert http.downloads == []


def test_apod_record_media_continues_to_hd_after_standard_load_failure(
    monkeypatch, apod_storage
):
    standard = "https://media.example.test/load-fails-standard.jpg"
    hd = "https://media.example.test/load-succeeds-hd.jpg"
    http = _MediaHttp(
        {
            standard: _image_bytes((960, 640)),
            hd: _image_bytes((1600, 1200)),
        }
    )
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace="media-load-fallback",
    )
    standard_digest = hashlib.sha256(standard.encode("utf-8")).hexdigest()
    real_decode = apod_module._decode_media_blob

    def decode(*, blob_path, photo_size):
        if standard_digest in Path(blob_path).name:
            raise apod_module.ApodMediaUnavailable("standard pixel load failed")
        return real_decode(blob_path=blob_path, photo_size=photo_size)

    monkeypatch.setattr(apod_module, "_decode_media_blob", decode)

    admitted, source_image = apod_storage._resolve_record_media(
        _task5_record(url=standard, hdurl=hd),
        paths=paths,
        photo_size=(432, 299),
        context=None,
    )

    assert admitted.image_url == hd
    assert source_image.size != (800, 480)
    assert [item["url"] for item in http.downloads] == [standard, hd]
    assert not (paths.media / f"{standard_digest}.img").exists()


@pytest.mark.parametrize("abort_type", [TaskCancelled, TaskDeadlineExceeded])
def test_apod_abort_after_candidate_read_never_publishes_managed_blob(
    monkeypatch,
    apod_storage,
    abort_type,
):
    media_url = "https://media.example.test/cancel-after-read.jpg"
    http = _MediaHttp({media_url: _image_bytes()})
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace=f"media-read-abort-{abort_type.__name__}",
    )
    digest = hashlib.sha256(media_url.encode("utf-8")).hexdigest()
    signal = abort_type("abort after candidate read")

    class Context:
        cancelled = False

        def raise_if_cancelled(self):
            if self.cancelled:
                raise signal

    context = Context()
    original_read_bytes = Path.read_bytes

    def cancelling_read_bytes(path):
        payload = original_read_bytes(path)
        if path.parent == paths.media and path.name.startswith(f".{digest}."):
            context.cancelled = True
        return payload

    monkeypatch.setattr(Path, "read_bytes", cancelling_read_bytes)

    with pytest.raises(abort_type) as caught:
        apod_module._resolve_media_blob(
            plugin=apod_storage,
            record=_task5_record(url=media_url, hdurl=None),
            paths=paths,
            minimum_size=(432, 299),
            context=context,
        )

    assert caught.value is signal
    assert not (paths.media / f"{digest}.img").exists()
    assert not [
        path for path in paths.media.iterdir() if path.name.startswith(f".{digest}.")
    ]


def test_apod_media_publication_is_accounted_and_evicts_the_oldest_blob(
    monkeypatch, apod_storage
):
    from utils.cache_manager import CacheBudget, cache_namespace_for_directory

    first_url = "https://media.example.test/accounted-first.jpg"
    second_url = "https://media.example.test/accounted-second.jpg"
    http = _MediaHttp(
        {
            first_url: _image_bytes(color=(25, 80, 140)),
            second_url: _image_bytes(color=(150, 80, 25)),
        }
    )
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace="media-accounting",
    )
    namespace = cache_namespace_for_directory(
        paths.media,
        CacheBudget(
            max_age_seconds=24 * 60 * 60,
            max_files=1,
            max_bytes=apod_module.MAX_MEDIA_BYTES,
        ),
    )
    monkeypatch.setattr(
        apod_storage,
        "managed_cache_namespace",
        lambda _directory: namespace,
    )

    first_blob, _ = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=_task5_record(url=first_url, hdurl=None),
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )
    first_status = namespace.status()
    second_blob, _ = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=_task5_record(url=second_url, hdurl=None),
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )
    final_status = namespace.status()

    assert first_status.files == 1
    assert first_status.bytes == len(http.payloads[first_url])
    assert not first_blob.exists()
    assert second_blob.is_file()
    assert final_status.files == 1
    assert final_status.bytes == len(http.payloads[second_url])
    assert final_status.evicted_total >= 1
    assert not [
        path for path in paths.media.iterdir() if path.name.endswith(".tmp")
    ]


def test_apod_media_cache_hit_refreshes_managed_lru_before_next_eviction(
    monkeypatch, apod_storage
):
    from utils.cache_manager import CacheBudget, cache_namespace_for_directory

    first_url = "https://media.example.test/lru-first.jpg"
    second_url = "https://media.example.test/lru-second.jpg"
    third_url = "https://media.example.test/lru-third.jpg"
    http = _MediaHttp(
        {
            first_url: _image_bytes(color=(20, 60, 120)),
            second_url: _image_bytes(color=(120, 60, 20)),
            third_url: _image_bytes(color=(60, 120, 20)),
        }
    )
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace="media-lru-touch",
    )
    namespace = cache_namespace_for_directory(
        paths.media,
        CacheBudget(
            max_age_seconds=24 * 60 * 60,
            max_files=2,
            max_bytes=apod_module.MAX_MEDIA_BYTES,
        ),
    )
    monkeypatch.setattr(
        apod_storage,
        "managed_cache_namespace",
        lambda _directory: namespace,
    )

    first_record = _task5_record(url=first_url, hdurl=None)
    first_blob, _ = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=first_record,
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )
    second_blob, _ = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=_task5_record(url=second_url, hdurl=None),
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )
    persisted_first = replace(
        first_record,
        image_url=first_url,
        image_cache_key=hashlib.sha256(first_url.encode("utf-8")).hexdigest(),
    )
    apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=persisted_first,
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )
    third_blob, _ = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=_task5_record(url=third_url, hdurl=None),
        paths=paths,
        minimum_size=(432, 299),
        context=None,
    )

    assert first_blob.is_file()
    assert not second_blob.exists()
    assert third_blob.is_file()
    assert namespace.status().files == 2


def test_apod_media_full_sha_namespace_is_global_across_trusted_instances(
    monkeypatch, apod_storage
):
    media_url = "https://media.example.test/shared.jpg"
    http = _MediaHttp({media_url: _image_bytes()})
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http, raising=False)

    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7),
    )
    first_paths = apod_module._instance_paths(apod_storage)
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("a2e3b8e3-6eb0-4d31-a7bc-0d0b9beb4737", 3),
    )
    second_paths = apod_module._instance_paths(apod_storage)

    first_blob, _ = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=_task5_record(url=media_url, hdurl=None),
        paths=first_paths,
        minimum_size=(432, 299),
        context=None,
    )
    second_blob, _ = apod_module._resolve_media_blob(
        plugin=apod_storage,
        record=_task5_record(url=media_url, hdurl=None),
        paths=second_paths,
        minimum_size=(432, 299),
        context=None,
    )

    digest = hashlib.sha256(media_url.encode("utf-8")).hexdigest()
    assert first_paths.cache != second_paths.cache
    assert first_paths.data != second_paths.data
    assert first_paths.media == second_paths.media
    assert first_blob == second_blob
    assert digest in first_blob.name
    assert len(digest) == 64
    assert len(http.downloads) == 1


def test_apod_media_decode_drafts_to_measured_photo_geometry_without_fullscreen_crop(
    monkeypatch, tmp_path
):
    from PIL import JpegImagePlugin

    blob = tmp_path / "large.jpg"
    blob.write_bytes(_image_bytes((1600, 1200)))
    draft_calls = []
    original_draft = JpegImagePlugin.JpegImageFile.draft

    def recording_draft(self, mode, size):
        draft_calls.append((mode, size))
        return original_draft(self, mode, size)

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", recording_draft)

    decoded = apod_module._decode_media_blob(
        blob_path=blob,
        photo_size=(432, 299),
    )

    assert draft_calls == [("RGB", (432, 299))]
    assert decoded.mode in {"RGB", "RGBA", "L"}
    assert decoded.size != (800, 480)


def test_apod_media_decode_rejects_large_non_draft_format_before_pixel_load(
    monkeypatch, tmp_path
):
    from PIL import PngImagePlugin

    blob = tmp_path / "large-solid.png"
    blob.write_bytes(_image_bytes((5000, 4000), image_format="PNG"))
    load_calls = []
    original_load = PngImagePlugin.PngImageFile.load

    def recording_load(self, *args, **kwargs):
        load_calls.append(self.size)
        return original_load(self, *args, **kwargs)

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", recording_load)

    with pytest.raises(
        apod_module.ApodMediaUnavailable,
        match="decoded memory",
    ):
        apod_module._decode_media_blob(
            blob_path=blob,
            photo_size=(432, 299),
        )

    assert load_calls == []


def test_apod_page_measurement_drives_one_final_photo_cover_crop(monkeypatch):
    page = _apod_page_module()
    record = _task5_record(title="Aurora")
    measurement = page.measure_apod_page(
        apod=record,
        title_zh="极光",
        translation_unavailable=False,
    )
    fit_calls = []
    original_fit = page.ImageOps.fit

    def recording_fit(image, size, **kwargs):
        fit_calls.append(tuple(size))
        return original_fit(image, size, **kwargs)

    monkeypatch.setattr(page.ImageOps, "fit", recording_fit)

    rendered = page.render_apod_page(
        apod=record,
        title_zh="极光",
        translation_unavailable=False,
        weather=_page_weather(),
        source_image=Image.new("RGB", (1600, 1200), PHOTO_BLUE),
        rendered_at_utc=NOW_UTC,
        measurement=measurement,
    )

    assert rendered.size == (800, 480)
    assert measurement.photo_size == (
        measurement.photo_rect[2] - measurement.photo_rect[0],
        measurement.photo_rect[3] - measurement.photo_rect[1],
    )
    assert fit_calls == [measurement.photo_size]
    assert (800, 480) not in fit_calls


def test_apod_page_rejects_stale_measurement_for_different_caption_content():
    page = _apod_page_module()
    measured_record = _task5_record(title="Aurora")
    rendered_record = _task5_record(title="A Different Short Title")
    measurement = page.measure_apod_page(
        apod=measured_record,
        title_zh="极光",
        translation_unavailable=False,
    )

    with pytest.raises(ValueError, match="measurement|caption"):
        page.render_apod_page(
            apod=rendered_record,
            title_zh="不同标题",
            translation_unavailable=False,
            weather=_page_weather(),
            source_image=Image.new("RGB", (1600, 1200), PHOTO_BLUE),
            rendered_at_utc=NOW_UTC,
            measurement=measurement,
        )


def test_apod_video_fallback_searches_at_most_seven_days_and_keeps_one_response_metadata():
    records = {
        "2026-07-22": _task5_record(
            media_type="video",
            title="Video Today",
            url="https://media.example.test/video",
            hdurl=None,
        ),
        "2026-07-21": _task5_record(
            apod_date="2026-07-21",
            media_type="video",
            title="Another Video",
            url="https://media.example.test/video-2",
            hdurl=None,
        ),
        "2026-07-20": _task5_record(
            apod_date="2026-07-20",
            title="Fallback Image Title",
            url="https://media.example.test/fallback.jpg",
            hdurl=None,
            copyright="Fallback Photographer",
        ),
    }
    calls = []

    def fetch_for_date(value):
        calls.append(value)
        return records[value]

    resolved, used_fallback = apod_module._resolve_image_record(
        requested=records["2026-07-22"],
        fetch_for_date=fetch_for_date,
        max_prior_days=7,
    )

    assert used_fallback is True
    assert calls == ["2026-07-21", "2026-07-20"]
    assert (
        resolved.date,
        resolved.title_en,
        resolved.copyright,
        resolved.url,
    ) == (
        "2026-07-20",
        "Fallback Image Title",
        "Fallback Photographer",
        "https://media.example.test/fallback.jpg",
    )


def test_apod_video_fallback_stops_after_exactly_seven_prior_days():
    requested = _task5_record(media_type="video", url=None, hdurl=None)
    calls = []

    def video_for_date(value):
        calls.append(value)
        return _task5_record(apod_date=value, media_type="video", url=None, hdurl=None)

    with pytest.raises(RuntimeError, match="seven|7|image"):
        apod_module._resolve_image_record(
            requested=requested,
            fetch_for_date=video_for_date,
            max_prior_days=7,
        )

    assert calls == [f"2026-07-{day:02d}" for day in range(21, 14, -1)]


class _TranslationConfig:
    def __init__(self, values):
        self.values = dict(values)

    def load_env_key(self, name):
        return self.values.get(name, "")


class _TranslationHttp:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            status=200,
            data={"choices": [{"message": {"content": outcome}}]},
            headers={},
            url=url,
        )


@pytest.mark.parametrize(
    ("env", "expected_key"),
    [
        (
            {
                "OPEN_AI_SECRET": "primary-secret",
                "OPENAI_API_KEY": "alias-secret",
                "GROQ_API_KEY": "groq-secret",
            },
            "primary-secret",
        ),
        ({"OPENAI_API_KEY": "alias-secret"}, "alias-secret"),
    ],
    ids=["primary-before-alias-and-groq", "openai-alias"],
)
def test_apod_translation_openai_key_priority_and_title_only_request(
    monkeypatch, apod_storage, env, expected_key
):
    http = _TranslationHttp(["日冕极光"])
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http, raising=False)
    paths = apod_module._instance_paths(apod_storage, preview_namespace=expected_key)
    title = "A Coronal Aurora"

    translated, unavailable = apod_module._translate_title(
        title=title,
        apod_date="2026-07-22",
        paths=paths,
        device_config=_TranslationConfig(env),
        context=None,
    )

    assert (translated, unavailable) == ("日冕极光", False)
    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {expected_key}"
    assert call["json"]["messages"][-1]["content"] == title
    assert "One exact APOD response" not in json.dumps(call["json"])


def test_apod_translation_falls_back_to_groq_without_secret_in_errors_or_logs(
    monkeypatch, apod_storage, caplog
):
    openai_secret = "openai-echo-secret"
    groq_secret = "groq-echo-secret"
    http = _TranslationHttp(
        [RuntimeError(f"provider echoed {openai_secret}"), "银河拱门"]
    )
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http, raising=False)
    paths = apod_module._instance_paths(apod_storage, preview_namespace="translation-groq")

    translated, unavailable = apod_module._translate_title(
        title="The Galactic Arch",
        apod_date="2026-07-22",
        paths=paths,
        device_config=_TranslationConfig(
            {"OPEN_AI_SECRET": openai_secret, "GROQ_API_KEY": groq_secret}
        ),
        context=None,
    )

    assert (translated, unavailable) == ("银河拱门", False)
    assert [call["url"] for call in http.calls] == [
        "https://api.openai.com/v1/chat/completions",
        "https://api.groq.com/openai/v1/chat/completions",
    ]
    assert http.calls[1]["headers"]["Authorization"] == f"Bearer {groq_secret}"
    assert openai_secret not in caplog.text
    assert groq_secret not in caplog.text


def test_apod_translation_cache_is_exact_date_plus_title_hash_and_never_cross_reuses(
    monkeypatch, apod_storage
):
    title = "The Galactic Arch"
    title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()
    http = _TranslationHttp(["银河拱门"])
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http, raising=False)
    paths = apod_module._instance_paths(apod_storage, preview_namespace="translation-cache")
    config = _TranslationConfig({"OPEN_AI_SECRET": "translation-secret"})

    first = apod_module._translate_title(
        title=title,
        apod_date="2026-07-22",
        paths=paths,
        device_config=config,
        context=None,
    )
    http.outcomes.append(RuntimeError("offline"))
    matching_last_good = apod_module._translate_title(
        title=title,
        apod_date="2026-07-22",
        paths=paths,
        device_config=config,
        context=None,
    )
    different_title = apod_module._translate_title(
        title="A Different Galactic Arch",
        apod_date="2026-07-22",
        paths=paths,
        device_config=config,
        context=None,
    )

    assert first == ("银河拱门", False)
    assert matching_last_good == ("银河拱门", False)
    assert different_title == (None, True)
    assert len(http.calls) == 2
    cache_files = list(paths.cache.glob("translation-*.json"))
    assert len(cache_files) == 1
    assert title_hash in cache_files[0].name
    cached = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert cached["apod_date"] == "2026-07-22"
    assert cached["title_sha256"] == title_hash
    assert cached["title_en"] == title
    assert cached["title_zh"] == "银河拱门"
    assert not list(paths.cache.glob("translation-*.json.*.tmp"))


def test_apod_translation_unavailable_is_explicit_and_does_not_invent_copy(
    monkeypatch, apod_storage
):
    http = _TranslationHttp([])
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http, raising=False)
    paths = apod_module._instance_paths(
        apod_storage, preview_namespace="translation-unavailable"
    )

    assert apod_module._translate_title(
        title="English Only",
        apod_date="2026-07-22",
        paths=paths,
        device_config=_TranslationConfig({}),
        context=None,
    ) == (None, True)
    assert http.calls == []


@pytest.mark.parametrize("preseed_cache", [False, True], ids=["provider", "cache"])
def test_apod_unfittable_translation_falls_back_and_removes_poisoned_cache(
    monkeypatch,
    apod_storage,
    preseed_cache,
):
    title = "Aurora"
    verbose_translation = "鏄" * 1000
    http = _TranslationHttp([verbose_translation])
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    paths = apod_module._instance_paths(
        apod_storage,
        preview_namespace=f"translation-poison-{preseed_cache}",
    )
    config = _TranslationConfig({"OPEN_AI_SECRET": "translation-secret"})
    if preseed_cache:
        assert apod_module._translate_title(
            title=title,
            apod_date="2026-07-22",
            paths=paths,
            device_config=config,
            context=None,
        ) == (verbose_translation, False)
        http.calls.clear()

    prepared, unavailable, measurement = apod_storage._prepare_record(
        _task5_record(title=title),
        paths=paths,
        device_config=config,
        context=None,
    )

    assert prepared.title_en == title
    assert prepared.title_zh is None
    assert prepared.translation_state == "unavailable"
    assert unavailable is True
    assert measurement.photo_size[0] > 0
    assert not apod_module._translation_cache_path(
        paths,
        "2026-07-22",
        title,
    ).exists()
    assert len(http.calls) == (0 if preseed_cache else 1)


class _Task5DeviceConfig(_TranslationConfig):
    def get_config(self, key=None, default=None):
        values = {
            "timezone": "UTC",
            "orientation": "horizontal",
            "resolution": "800x480",
            "width": 800,
            "height": 480,
        }
        if key is None:
            return values
        return values.get(key, default)

    def get_resolution(self):
        return (800, 480)


class _OrchestrationHttp(_MediaHttp):
    def __init__(self, apod_payload, media_payload=None):
        media_url = apod_payload.get("url")
        super().__init__(
            {media_url: media_payload or _image_bytes()} if media_url else {}
        )
        self.apod_payload = dict(apod_payload)
        self.json_calls = []

    def request_json(self, method, url, **kwargs):
        self.json_calls.append({"method": method, "url": url, **kwargs})
        return SimpleNamespace(
            status=200,
            data=dict(self.apod_payload),
            headers={},
            url=url,
        )


def _task5_apod_payload():
    return {
        "date": "2026-07-22",
        "media_type": "image",
        "title": "A Coronal Aurora",
        "explanation": "Provider explanation must not enter translation.",
        "copyright": "NASA Test Team",
        "url": "https://media.example.test/today.jpg",
        "hdurl": "https://media.example.test/today-hd.jpg",
    }


def _patch_task5_orchestration(
    monkeypatch,
    *,
    weather,
    http=None,
    events=None,
):
    events = [] if events is None else events
    http = _OrchestrationHttp(_task5_apod_payload()) if http is None else http
    weather_calls = []
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7),
    )
    monkeypatch.setattr(
        apod_module, "_device_day", lambda _config: date(2026, 7, 22)
    )
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http, raising=False)
    monkeypatch.setattr(
        apod_module, "current_task_context", lambda: None, raising=False
    )

    def refresh(repository, **kwargs):
        weather_calls.append((repository, kwargs))
        return weather

    def render(**kwargs):
        events.append("render")
        assert kwargs["source_image"].size != (800, 480)
        return Image.new("RGB", (800, 480), PHOTO_BLUE)

    monkeypatch.setattr(apod_module, "refresh_space_weather", refresh, raising=False)
    monkeypatch.setattr(apod_module, "render_apod_page", render, raising=False)
    return http, weather_calls, events


def test_apod_orchestration_same_day_refreshes_weather_without_apod_or_media_network(
    monkeypatch, apod_storage
):
    weather = _page_weather()
    events = []
    http, weather_calls, events = _patch_task5_orchestration(
        monkeypatch, weather=weather, events=events
    )
    context_payloads = []
    monkeypatch.setattr(
        apod_module,
        "write_context",
        lambda *args, **kwargs: (events.append("context"), context_payloads.append((args, kwargs))),
    )
    real_attach = attach_source_provenance

    def recording_attach(image, provenance, **kwargs):
        events.append("provenance")
        return real_attach(image, provenance, **kwargs)

    monkeypatch.setattr(
        apod_module, "attach_source_provenance", recording_attach, raising=False
    )
    plugin = apod_storage
    monkeypatch.setattr(
        plugin,
        "_overlay_nasa_logo",
        lambda _image: pytest.fail("NASA logo overlay must not be called"),
        raising=False,
    )
    config = _Task5DeviceConfig({"NASA_SECRET": "nasa-super-secret"})

    first = plugin.generate_image({}, config)
    second = plugin.generate_image({"forceRefresh": "true"}, config)

    assert len(http.json_calls) == 1
    assert http.json_calls[0]["url"] == "https://api.nasa.gov/planetary/apod"
    assert len(http.downloads) == 1
    assert len(weather_calls) == 2
    assert read_source_provenance(first) is SourceProvenance.LIVE
    assert read_source_provenance(second) is SourceProvenance.LIVE
    assert events == [
        "render",
        "provenance",
        "context",
        "render",
        "provenance",
        "context",
    ]
    serialized_context = json.dumps(context_payloads, default=str)
    assert "nasa-super-secret" not in serialized_context
    assert "api_key" not in serialized_context
    assert "image_url" not in serialized_context


def test_apod_orchestration_current_cycle_core_failure_precedes_media_render_and_context(
    monkeypatch, apod_storage
):
    healthy = _page_weather()
    failed_scales = replace(healthy.scales, state="fresh_cache", error="offline")
    weather = replace(
        healthy,
        scales=failed_scales,
        aggregate_state=SourceProvenance.FRESH_CACHE,
    )
    events = []
    http, weather_calls, events = _patch_task5_orchestration(
        monkeypatch, weather=weather, events=events
    )
    monkeypatch.setattr(
        apod_module,
        "write_context",
        lambda *_args, **_kwargs: events.append("context"),
    )

    with pytest.raises(RuntimeError, match="current-cycle|scales|core"):
        apod_storage.generate_image(
            {}, _Task5DeviceConfig({"NASA_SECRET": "nasa-key"})
        )

    assert len(http.json_calls) == 1
    assert len(weather_calls) == 1
    assert http.downloads == []
    assert events == []


def test_apod_orchestration_rejects_live_core_result_that_still_has_an_error(
    monkeypatch, apod_storage
):
    healthy = _page_weather()
    malformed_live = replace(
        healthy,
        scales=replace(healthy.scales, state="live", error="semantic failure"),
    )
    events = []
    http, weather_calls, events = _patch_task5_orchestration(
        monkeypatch, weather=malformed_live, events=events
    )
    monkeypatch.setattr(
        apod_module,
        "write_context",
        lambda *_args, **_kwargs: events.append("context"),
    )

    with pytest.raises(RuntimeError, match="current-cycle|scales|core"):
        apod_storage.generate_image(
            {}, _Task5DeviceConfig({"NASA_SECRET": "nasa-key"})
        )

    assert len(http.json_calls) == 1
    assert len(weather_calls) == 1
    assert http.downloads == []
    assert events == []


def test_apod_orchestration_optional_failures_render_fixed_unavailable_snapshot(
    monkeypatch, apod_storage
):
    weather = _page_weather()
    captured = []
    http, _weather_calls, _events = _patch_task5_orchestration(
        monkeypatch, weather=weather
    )

    def capture_render(**kwargs):
        captured.append(kwargs["weather"])
        return Image.new("RGB", (800, 480), PHOTO_BLUE)

    monkeypatch.setattr(apod_module, "render_apod_page", capture_render, raising=False)

    result = apod_storage.generate_image(
        {}, _Task5DeviceConfig({"NASA_SECRET": "nasa-key"})
    )

    assert result.size == (800, 480)
    assert len(captured) == 1
    assert captured[0].scales.state == "live"
    assert captured[0].kp.state == "live"
    assert captured[0].wind_magnetic.state == "unavailable"
    assert captured[0].alerts.state == "unavailable"
    assert captured[0].donki.state == "unavailable"
    assert len(http.downloads) == 1


@pytest.mark.parametrize(
    "aggregate_state",
    [SourceProvenance.STALE_CACHE, SourceProvenance.LOCAL_FALLBACK],
)
def test_apod_provenance_stale_or_local_is_trusted_but_explicitly_skip_cache(
    monkeypatch, apod_storage, aggregate_state
):
    weather = replace(_page_weather(), aggregate_state=aggregate_state)
    _patch_task5_orchestration(monkeypatch, weather=weather)

    result = apod_storage.generate_image(
        {}, _Task5DeviceConfig({"NASA_SECRET": "nasa-key"})
    )

    assert read_source_provenance(result) is aggregate_state
    assert result.info["inkypi_skip_cache"] is True


def test_space_weather_aggregate_cache_is_atomic_and_contains_provenance(tmp_path):
    repository = _aggregate_repository()
    repository.cache_dir = tmp_path

    snapshot = refresh_space_weather(
        repository,
        nasa_api_key="nasa-key",
        now_utc=NOW_UTC,
        context=None,
    )

    aggregate_path = tmp_path / "aggregate.json"
    cached = json.loads(aggregate_path.read_text(encoding="utf-8"))
    assert cached["schema"] == 1
    assert cached["fetched_at_utc"] == "2026-07-22T12:20:00Z"
    assert cached["aggregate_state"] == snapshot.aggregate_state.value
    assert cached["sources"]["scales"] == "live"
    assert cached["sources"]["kp"] == "live"
    assert not list(tmp_path.glob("aggregate.json.*.tmp"))


@pytest.mark.parametrize("cancel_after", ["core", "wind", "alerts", "donki"])
def test_space_weather_phase_cancellation_stops_before_later_sources_and_aggregate(
    tmp_path,
    cancel_after,
):
    signal = TaskCancelled(f"stop after {cancel_after}")

    class Context:
        cancelled = False

        def raise_if_cancelled(self):
            if self.cancelled:
                raise signal

    context = Context()
    repository = _aggregate_repository()
    repository.cache_dir = tmp_path
    calls = []
    phase_methods = {
        "core": "refresh_core",
        "wind": "refresh_wind",
        "alerts": "refresh_alerts",
        "donki": "refresh_donki",
    }
    for phase, method_name in phase_methods.items():
        original = getattr(repository, method_name)

        def wrapped(*args, _phase=phase, _original=original, **kwargs):
            calls.append(_phase)
            result = _original(*args, **kwargs)
            if _phase == cancel_after:
                context.cancelled = True
            return result

        setattr(repository, method_name, wrapped)

    with pytest.raises(TaskCancelled) as caught:
        refresh_space_weather(
            repository,
            nasa_api_key="nasa-key",
            now_utc=NOW_UTC,
            context=context,
        )

    expected = list(phase_methods)
    assert caught.value is signal
    assert calls == expected[: expected.index(cancel_after) + 1]
    assert not (tmp_path / "aggregate.json").exists()


def test_space_weather_core_failure_preserves_prior_aggregate_bytes_and_mtime(tmp_path):
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_path.write_bytes(b'{"schema":1,"sentinel":"last-good"}')
    before_bytes = aggregate_path.read_bytes()
    before_mtime = aggregate_path.stat().st_mtime_ns
    repository = _aggregate_repository(core_state="fresh_cache")
    repository.cache_dir = tmp_path

    snapshot = refresh_space_weather(
        repository,
        nasa_api_key="nasa-key",
        now_utc=NOW_UTC,
        context=None,
    )

    assert snapshot.scales.state == "fresh_cache"
    assert snapshot.kp.state == "fresh_cache"
    assert aggregate_path.read_bytes() == before_bytes
    assert aggregate_path.stat().st_mtime_ns == before_mtime
