import hashlib
import json
import random
import sys
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.apod import apod as apod_module  # noqa: E402
from plugins.apod.apod import Apod  # noqa: E402
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
from plugins.base_plugin.render_provenance import SourceProvenance  # noqa: E402
from runtime.long_task_executor import InstanceIdentity  # noqa: E402


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
    stale_snapshot = refresh_space_weather(
        _aggregate_repository(wind_state="stale_cache"),
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

    assert stale_snapshot.solar_wind
    assert stale_snapshot.aggregate_state is SourceProvenance.STALE_CACHE
    assert empty_snapshot.aggregate_state is SourceProvenance.LOCAL_FALLBACK
    assert not empty_snapshot.current_scales
    assert not empty_snapshot.current_kp
