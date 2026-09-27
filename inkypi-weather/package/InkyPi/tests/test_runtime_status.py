from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace
import json

from tests.test_health_blueprint import _app_with_health
from tests.test_refresh_progress import instance
from runtime.runtime_state import InstanceRuntimeState, RefreshLaneState


def test_runtime_status_is_admin_only_and_reads_detached_snapshot():
    app, _ = _app_with_health()
    app.config["HEALTH_DETAIL_AUTHORIZER"] = lambda request: request.headers.get("X-Test-Admin") == "yes"
    app.config["REFRESH_TASK"] = SimpleNamespace(runtime_status_snapshot=lambda: {
        "observed_at": "2026-09-27T12:00:00+00:00", "instances": [], "recoveries": [],
    })
    client = app.test_client()
    assert client.get("/api/runtime-status").status_code == 401
    response = client.get("/api/runtime-status", headers={"X-Test-Admin": "yes"})
    assert response.status_code == 200
    assert response.get_json()["error_codes"] == []
    assert response.get_json()["runtime"]["instances"] == []
    assert response.headers["Cache-Control"] == "no-store"


def test_status_distinguishes_recovered_failures_and_display_owned_policy(tmp_path):
    from runtime.runtime_status import RuntimeStatusObserver
    now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    item = replace(instance(), plugin_id="weather")
    state = InstanceRuntimeState(data=RefreshLaneState(
        last_success_at=(now - timedelta(hours=2)).isoformat(),
        last_failure_at=(now - timedelta(hours=3)).isoformat(),
        last_error="secret-token: do not publish this",
    ))
    source = tmp_path / "weather"
    source.mkdir()
    (source / "onecall_test.json").write_text(json.dumps({
        "fetched_at": (now - timedelta(hours=2)).isoformat(), "stale": False,
        "data": {"current": {"dt": (now - timedelta(hours=2)).timestamp()}, "lat": 42},
    }))
    observer = RuntimeStatusObserver(cache_dir=tmp_path, data_dir=tmp_path)
    observer.observe([item], {item.instance_uuid: state},
                     {"weather": {"_manifest": SimpleNamespace(capabilities=SimpleNamespace(
                         refresh_data_before_display=True))}}, now=now)
    result = observer.snapshot()
    row = result["instances"][0]
    assert row["policy"] == "before_display"
    assert row["issue"] is None
    assert row["source"]["age_seconds"] == 7200
    encoded = json.dumps(result)
    assert "secret-token" not in encoded and '"lat"' not in encoded and "private-key" not in encoded
    result["instances"].clear()
    assert len(observer.snapshot()["instances"]) == 1


def test_vehicle_offline_source_age_is_not_replaced_by_success_time(tmp_path):
    from runtime.runtime_status import RuntimeStatusObserver
    now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    item = replace(instance(), plugin_id="vehicle_status")
    source = tmp_path / "plugins/vehicle_status/cache"
    source.mkdir(parents=True)
    (source / "summary-v3.json").write_text(json.dumps({"summary": {"snapshot": {
        "captured_at": (now - timedelta(hours=5)).isoformat(),
        "freshness": "stale_cache", "vehicle_connectivity": "offline", "latitude": 42,
    }}}))
    observer = RuntimeStatusObserver(cache_dir=tmp_path)
    state = InstanceRuntimeState(data=RefreshLaneState(last_success_at=now.isoformat()))
    observer.observe([item], {item.instance_uuid: state}, {}, now=now)
    row = observer.snapshot()["instances"][0]
    assert row["source"]["age_seconds"] == 18000
    assert row["source"]["state"] == "stale_cache"
    assert row["source"]["connectivity"] == "offline"
    assert "latitude" not in json.dumps(row)


def test_steam_media_completeness_is_separate_from_metadata_failure(monkeypatch):
    from plugins.steam_charts.steam_charts import SteamCharts
    import plugins.steam_charts.steam_charts as module
    plugin = SteamCharts({"id": "steam_charts"})
    monkeypatch.setattr(plugin, "_fetch_store_appdetails", lambda *_: {})
    monkeypatch.setattr(plugin, "_resolve_cover_image", lambda *_: "verified-image")
    saved = []
    monkeypatch.setattr(module, "write_context", lambda _id, payload, **_: saved.append(payload))
    games = [{"app_id": 123, "name": "Sample"}]
    plugin._apply_store_metadata(games)
    plugin._write_charts_context({}, games, "now")
    assert saved[0]["diagnostics"] == {
        "items": 1, "metadata_missing": 2, "metadata_checked": 2,
        "covers_requested": 1, "covers_available": 1,
    }


def test_recovery_history_is_bounded_and_survives_observer_recreation(tmp_path):
    from runtime.runtime_status import record_supervised_recovery, RuntimeStatusObserver
    config = SimpleNamespace(runtime_paths=SimpleNamespace(data_dir=tmp_path, release_id="release-a"))
    for _ in range(35):
        record_supervised_recovery(config, "isolated_worker_cleanup_failed")
    config.runtime_paths.release_id = "release-b"
    record_supervised_recovery(config, "memory_pressure")
    observer = RuntimeStatusObserver(data_dir=tmp_path)
    observer.observe([], {}, {}, now=datetime.now(timezone.utc))
    rows = observer.snapshot()["recoveries"]
    assert len(rows) == 32
    assert rows[0]["release_id"] == "release-a"
    assert rows[-1]["release_id"] == "release-b"
    assert rows[-1]["reason"] == "memory_pressure"
