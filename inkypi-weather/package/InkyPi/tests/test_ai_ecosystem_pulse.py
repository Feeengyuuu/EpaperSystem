import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

try:
    import psutil  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault(
        "psutil",
        SimpleNamespace(
            virtual_memory=lambda: SimpleNamespace(total=2 * 1024**3),
            swap_memory=lambda: SimpleNamespace(percent=0.0),
        ),
    )
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.ai_ecosystem_pulse.ai_ecosystem_pulse import (  # noqa: E402
    AiEcosystemPulse,
    DEFAULT_FONT,
    PLUGIN_ID,
)
from plugins.base_plugin.presentation import PresentationMode  # noqa: E402
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)
from plugins.ai_ecosystem_pulse.sources import (  # noqa: E402
    fetch_github,
    merge_github_candidates,
    normalize_hf_models,
    parse_skills_html,
    record_star_snapshot,
    stars_24h,
)


SKILLS_HTML = """
<a class="row" href="/101-skills/skills/ai-video-generation">
  <span>1</span><span>ai-video-generation</span><span>101-skills/skills</span><span>21.9K</span>
</a>
<a class="row" href="/vercel-labs/skills/find-skills">
  <span>6</span><span>find-skills</span><span>vercel-labs/skills</span><span>16.9K</span>
</a>
"""

VALID_SKILL_ROW = {
    "rank": 1,
    "name": "valid-skill",
    "source": "org/repo",
    "installs": 100,
    "installs_display": "100",
}


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone_name="America/Los_Angeles"):
        self.resolution = resolution
        self.timezone_name = timezone_name

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"timezone": self.timezone_name, "orientation": "horizontal", "theme_mode": "day"}
        return values if key is None else values.get(key, default)

    def load_env_key(self, key):
        return ""


def test_manifest_matches_plugin_identity_and_theme_contract():
    info_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / PLUGIN_ID / "plugin-info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))

    assert info["id"] == "ai_ecosystem_pulse"
    assert info["class"] == "AiEcosystemPulse"
    assert info["display_name"] == "AI Ecosystem Pulse"
    assert info["capabilities"]["supports_day_night_theme"] is True
    assert info["theme"]["presentation"] == "ui"
    assert info["refresh_on_display"] is True


def test_settings_expose_only_approved_defaults():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / PLUGIN_ID / "settings.html"
    html = settings_path.read_text(encoding="utf-8")

    assert 'name="refreshOnDisplay"' in html
    assert 'value="true"' in html
    assert 'name="refreshMinutes"' in html
    assert 'value="60"' in html
    assert 'name="fontFamily"' in html
    assert 'name="forceRefresh"' in html
    assert DEFAULT_FONT == "Microsoft YaHei"


def test_presentation_mode_is_explicit_no_change():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    assert plugin.presentation_mode({}) is PresentationMode.NO_CHANGE


def test_skills_parser_preserves_official_rank_gaps_and_counts():
    rows = parse_skills_html(SKILLS_HTML, limit=6)
    assert [row["rank"] for row in rows] == [1, 6]
    assert rows[0]["name"] == "ai-video-generation"
    assert rows[0]["source"] == "101-skills/skills"
    assert rows[0]["installs"] == 21900
    assert rows[0]["installs_display"] == "21.9K"
    assert rows[1]["url"] == "https://www.skills.sh/vercel-labs/skills/find-skills"


def test_hugging_face_normalizer_keeps_30_day_metrics():
    models = normalize_hf_models(
        [
            {
                "id": "thinkingmachines/Inkling",
                "author": "thinkingmachines",
                "pipeline_tag": "image-text-to-text",
                "trendingScore": 922,
                "likes": 942,
                "downloads": 7870,
                "createdAt": "2026-07-14T13:23:14.000Z",
                "lastModified": "2026-07-16T15:39:18.000Z",
                "gated": False,
                "tags": ["transformers", "safetensors", "multimodal", "extra"],
            }
        ]
    )
    assert models[0]["id"] == "thinkingmachines/Inkling"
    assert models[0]["trending_score"] == 922
    assert models[0]["downloads_30d"] == 7870
    assert models[0]["tags"] == ["transformers", "safetensors", "multimodal"]


def test_hugging_face_normalizer_skips_bad_rows_and_accepts_numeric_strings():
    models = normalize_hf_models(
        [
            None,
            "bad row",
            {
                "modelId": "missing/required-id",
                "trendingScore": 1,
                "likes": 2,
                "downloads": 3,
            },
            {"id": "missing/metrics", "trendingScore": 1, "likes": 2},
            {
                "id": "bad/metric",
                "trendingScore": "not-a-number",
                "likes": 2,
                "downloads": 3,
            },
            {
                "id": "valid/model",
                "trendingScore": "17",
                "likes": "23",
                "downloads": "4500",
                "tags": "not-a-list",
            },
        ]
    )

    assert models == [
        {
            "id": "valid/model",
            "author": "valid",
            "pipeline_tag": "model",
            "trending_score": 17,
            "likes": 23,
            "downloads_30d": 4500,
            "created_at": None,
            "last_modified": None,
            "gated": False,
            "tags": [],
            "url": "https://huggingface.co/valid/model",
        }
    ]


def test_hugging_face_normalizer_raises_when_every_remote_row_is_unusable():
    with pytest.raises(RuntimeError, match="no usable"):
        normalize_hf_models(
            [
                None,
                {"id": "bad/model", "trendingScore": 1, "likes": "bad", "downloads": 3},
            ]
        )


def test_github_candidates_deduplicate_and_keep_richer_row():
    lean = {"id": 1, "full_name": "anthropics/skills", "stargazers_count": 100, "topics": []}
    rich = {**lean, "description": "Public repository for Agent Skills", "topics": ["agent-skills"]}
    rows = merge_github_candidates([[lean], [rich]])
    assert len(rows) == 1
    assert rows[0]["full_name"] == "anthropics/skills"
    assert rows[0]["description"] == "Public repository for Agent Skills"


def test_github_normalizer_skips_bad_rows_and_defends_owner_and_topics_types():
    rows = merge_github_candidates(
        [
            [
                None,
                "bad row",
                {
                    "id": 1,
                    "full_name": "bad/stars",
                    "stargazers_count": "bad",
                    "forks_count": 2,
                },
                {
                    "id": 2,
                    "full_name": "bad/forks",
                    "stargazers_count": 3,
                    "forks_count": "bad",
                },
                {
                    "full_name": "valid/no-id",
                    "stargazers_count": "41",
                    "owner": "not-an-object",
                    "topics": "not-a-list",
                },
            ]
        ]
    )

    assert len(rows) == 1
    assert rows[0]["id"] is None
    assert rows[0]["full_name"] == "valid/no-id"
    assert rows[0]["stars"] == 41
    assert rows[0]["forks"] == 0
    assert rows[0]["topics"] == []
    assert rows[0]["owner_avatar_url"] == ""


def test_github_normalizer_raises_when_every_remote_row_is_unusable():
    with pytest.raises(RuntimeError, match="no usable"):
        merge_github_candidates(
            [[None, {"full_name": "bad/repo", "stargazers_count": "bad"}]]
        )


def test_star_delta_uses_only_twenty_to_thirty_two_hour_baseline():
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    valid = [{"captured_at": (now - timedelta(hours=24)).isoformat(), "stars": 100}]
    too_new = [{"captured_at": (now - timedelta(hours=10)).isoformat(), "stars": 100}]
    assert stars_24h(valid, 137, now) == 37
    assert stars_24h(too_new, 137, now) is None


def test_first_snapshot_remains_new_and_history_is_bounded():
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    history = record_star_snapshot([], 137, now)
    assert stars_24h(history, 137, now) is None
    many = [{"captured_at": (now - timedelta(days=day)).isoformat(), "stars": day} for day in range(20)]
    assert len(record_star_snapshot(many, 200, now)) <= 9


def test_hourly_snapshots_keep_a_twenty_four_hour_star_baseline():
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    history = []
    for hour in range(26):
        captured_at = now - timedelta(hours=25 - hour)
        history = record_star_snapshot(history, 100 + hour, captured_at)

    assert stars_24h(history, 125, now) == 24


def test_hourly_snapshots_keep_bounded_multi_day_diagnostics():
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    history = []
    for hour in range(7 * 24 + 1):
        captured_at = now - timedelta(hours=7 * 24 - hour)
        history = record_star_snapshot(history, 100 + hour, captured_at)

    retained_dates = {datetime.fromisoformat(point["captured_at"]).date() for point in history}
    assert stars_24h(history, 268, now) == 24
    assert len(history) <= 42
    assert len(retained_dates) >= 7


def test_payload_keeps_successful_panels_when_one_source_fails(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_skills",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_huggingface",
        lambda: [
            {
                "id": "org/model",
                "trending_score": 10,
                "likes": 5,
                "downloads_30d": 20,
                "pipeline_tag": "text-generation",
            }
        ],
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": [
            {
                "id": 1,
                "full_name": "org/repo",
                "stars": 100,
                "language": "Python",
            }
        ],
    )

    payload = plugin._payload({"forceRefresh": True}, FakeDeviceConfig(), now)

    assert payload["status"]["aggregate"] == "PARTIAL"
    assert payload["status"]["sources"]["skills"] == "fixture"
    assert payload["status"]["sources"]["huggingface"] == "live"
    assert payload["status"]["sources"]["github"] == "live"
    assert payload["models"][0]["id"] == "org/model"
    assert payload["repos"][0]["full_name"] == "org/repo"


def test_github_first_capture_is_new_then_uses_valid_delta(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    rows = [{"id": 1, "full_name": "org/repo", "stars": 100, "language": "Python"}]
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": rows,
    )

    first = plugin._fetch_github_with_snapshots(FakeDeviceConfig(), now)
    rows[0]["stars"] = 137
    baseline_file = plugin._data_dir() / "github-stars.json"
    state = json.loads(baseline_file.read_text(encoding="utf-8"))
    state["1"][0]["captured_at"] = (now - timedelta(hours=24)).isoformat()
    baseline_file.write_text(json.dumps(state), encoding="utf-8")
    second = plugin._fetch_github_with_snapshots(FakeDeviceConfig(), now)

    assert first[0]["stars_24h"] is None
    assert second[0]["stars_24h"] == 37


def test_theme_only_payload_never_fetches_network(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    plugin._write_json(plugin._cache_dir() / "aggregate.json", plugin._fixture_payload(now))
    monkeypatch.setattr(
        plugin,
        "_fetch_live_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )

    payload = plugin._payload({"_theme_render_only": True}, FakeDeviceConfig(), now)

    assert payload["skills"]
    assert payload["models"]
    assert payload["repos"]


def test_malformed_source_cache_does_not_block_source_fallback(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    plugin._write_json(plugin._cache_dir() / "skills.json", ["not a cache record"])
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_skills",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_huggingface",
        lambda: [{"id": "org/model"}],
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": [{"id": 1, "full_name": "org/repo", "stars": 100}],
    )

    payload = plugin._payload({}, FakeDeviceConfig(), now)

    assert payload["status"]["sources"]["skills"] == "fixture"


def test_theme_only_rejects_invalid_aggregate_payloads_without_network(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        plugin,
        "_fetch_live_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )
    invalid_payloads = [
        ["not an aggregate"],
        "not an aggregate",
        {"schema": "ai-ecosystem-pulse-v1", "skills": []},
    ]

    for invalid in invalid_payloads:
        plugin._write_json(plugin._cache_dir() / "aggregate.json", invalid)
        payload = plugin._payload({"_theme_render_only": True}, FakeDeviceConfig(), now)

        assert payload["status"]["aggregate"] == "DEMO"
        assert payload["skills"]
        assert payload["models"]
        assert payload["repos"]


def test_empty_source_response_uses_fixture_and_marks_payload_partial(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_skills", lambda: [])
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_huggingface",
        lambda: [{"id": "org/model"}],
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": [{"id": 1, "full_name": "org/repo", "stars": 100}],
    )

    payload = plugin._payload({"forceRefresh": True}, FakeDeviceConfig(), now)

    assert payload["status"]["sources"]["skills"] == "fixture"
    assert payload["status"]["aggregate"] == "PARTIAL"
    assert payload["_source_provenance"] == "local_fallback"
    assert payload["skills"]


def test_empty_fresh_source_cache_falls_back_to_fixture(tmp_path):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    plugin._write_json(
        plugin._cache_dir() / "skills.json",
        {"schema": "ai-ecosystem-pulse-v1", "fetched_at": now.isoformat(), "items": []},
    )

    rows, state, error = plugin._resolve_source("skills", now, False, lambda: [], [{"name": "fixture"}], 60)

    assert rows == [{"name": "fixture"}]
    assert state == "fixture"
    assert error == "ValueError"


def test_empty_response_uses_nonempty_stale_cache(tmp_path):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    plugin._write_json(
        plugin._cache_dir() / "skills.json",
        {
            "schema": "ai-ecosystem-pulse-v1",
            "fetched_at": (now - timedelta(minutes=61)).isoformat(),
            "items": [{**VALID_SKILL_ROW, "name": "stale"}],
        },
    )

    rows, state, error = plugin._resolve_source("skills", now, False, lambda: [], [VALID_SKILL_ROW], 60)

    assert rows == [{**VALID_SKILL_ROW, "name": "stale"}]
    assert state == "stale_cache"
    assert error == "ValueError"


def test_github_state_prunes_old_keys_and_self_heals_invalid_histories(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    old_point = {"captured_at": (now - timedelta(days=9)).isoformat(), "stars": 1}
    state = {f"old-{index}": [old_point] for index in range(100)}
    dormant_at = now - timedelta(hours=25)
    state["dormant"] = [{"captured_at": dormant_at.isoformat(), "stars": 50}]
    state["1"] = [
        "bad member",
        {"captured_at": "not-a-time", "stars": 1},
        {"captured_at": (now - timedelta(hours=24)).isoformat(), "stars": "100"},
    ]
    state["bad-history"] = {"captured_at": now.isoformat(), "stars": 7}
    plugin._write_json(plugin._data_dir() / "github-stars.json", state)
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": [{"id": 1, "full_name": "org/repo", "stars": 137}],
    )

    rows = plugin._fetch_github_with_snapshots(FakeDeviceConfig(), now)
    saved = json.loads((plugin._data_dir() / "github-stars.json").read_text(encoding="utf-8"))

    assert rows[0]["stars_24h"] == 37
    assert set(saved) == {"1", "dormant"}
    assert saved["dormant"] == [{"captured_at": dormant_at.isoformat(), "stars": 50}]
    assert all(isinstance(point, dict) for point in saved["1"])
    assert all(datetime.fromisoformat(point["captured_at"]) >= now - timedelta(days=8) for point in saved["1"])


def test_force_refresh_bypasses_fresh_nonempty_cache(tmp_path):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    plugin._write_json(
        plugin._cache_dir() / "skills.json",
        {"schema": "ai-ecosystem-pulse-v1", "fetched_at": now.isoformat(), "items": [VALID_SKILL_ROW]},
    )

    rows, state, _error = plugin._resolve_source(
        "skills", now, True, lambda: [{**VALID_SKILL_ROW, "name": "live"}], [VALID_SKILL_ROW], 60
    )

    assert rows == [{**VALID_SKILL_ROW, "name": "live"}]
    assert state == "live"


def test_cache_fresh_honors_ttl_boundary_and_rejects_future_timestamp():
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    boundary = {"fetched_at": (now - timedelta(minutes=60)).isoformat()}
    future = {"fetched_at": (now + timedelta(seconds=1)).isoformat()}

    assert AiEcosystemPulse._cache_fresh(boundary, now, 60) is True
    assert AiEcosystemPulse._cache_fresh(future, now, 60) is False


def test_corrupt_source_cache_never_becomes_fresh_or_stale_fallback(tmp_path):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    plugin._write_json(
        plugin._cache_dir() / "skills.json",
        {
            "schema": "ai-ecosystem-pulse-v1",
            "fetched_at": now.isoformat(),
            "items": ["bad row"],
        },
    )

    rows, state, error = plugin._resolve_source(
        "skills",
        now,
        False,
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        [VALID_SKILL_ROW],
        60,
    )

    assert rows == [VALID_SKILL_ROW]
    assert state == "fixture"
    assert error == "RuntimeError"


def test_live_source_keeps_valid_rows_and_discards_malformed_rows(tmp_path):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)

    rows, state, _error = plugin._resolve_source(
        "skills",
        now,
        True,
        lambda: ["bad row", {"rank": "not-an-int"}, VALID_SKILL_ROW],
        [VALID_SKILL_ROW],
        60,
    )
    cached = json.loads((plugin._cache_dir() / "skills.json").read_text(encoding="utf-8"))

    assert rows == [VALID_SKILL_ROW]
    assert cached["items"] == [VALID_SKILL_ROW]
    assert state == "live"


def test_theme_only_rejects_inconsistent_source_states_and_provenance(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        plugin,
        "_fetch_live_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )
    inconsistent = plugin._fixture_payload(now)
    inconsistent["status"]["sources"] = {
        "skills": "live",
        "huggingface": "fresh_cache",
        "github": "live",
    }
    plugin._write_json(plugin._cache_dir() / "aggregate.json", inconsistent)

    payload = plugin._payload({"_theme_render_only": True}, FakeDeviceConfig(), now)

    assert payload["status"]["aggregate"] == "DEMO"
    assert payload["status"]["sources"]["skills"] == "fixture"
    assert payload["_source_provenance"] == "local_fallback"


def test_theme_only_uses_source_row_validation_for_unknown_states_and_bad_rows(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        plugin,
        "_fetch_live_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )
    bad_state = plugin._fixture_payload(now)
    bad_state["status"]["sources"]["skills"] = "remote"
    bad_rows = plugin._fixture_payload(now)
    bad_rows["skills"] = ["bad row"]

    for invalid in (bad_state, bad_rows):
        plugin._write_json(plugin._cache_dir() / "aggregate.json", invalid)
        payload = plugin._payload({"_theme_render_only": True}, FakeDeviceConfig(), now)

        assert payload["status"]["sources"]["skills"] == "fixture"
        assert payload["skills"]


def test_github_history_cap_preserves_current_delta_baselines(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    state = {
        f"dormant-{index}": [{"captured_at": (now - timedelta(hours=25)).isoformat(), "stars": index}]
        for index in range(300)
    }
    state["1"] = [{"captured_at": (now - timedelta(hours=24)).isoformat(), "stars": 100}]
    state["2"] = [{"captured_at": (now - timedelta(hours=24)).isoformat(), "stars": 200}]
    plugin._write_json(plugin._data_dir() / "github-stars.json", state)
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": [
            {"id": 1, "full_name": "org/one", "stars": 137},
            {"id": 2, "full_name": "org/two", "stars": 230},
        ],
    )

    rows = plugin._fetch_github_with_snapshots(FakeDeviceConfig(), now)
    saved = json.loads((plugin._data_dir() / "github-stars.json").read_text(encoding="utf-8"))

    assert {row["full_name"]: row["stars_24h"] for row in rows} == {"org/one": 37, "org/two": 30}
    assert len(saved) == 256
    assert {"1", "2"}.issubset(saved)


def test_github_candidate_catalog_retains_missing_rows_without_fake_observations(
    tmp_path, monkeypatch
):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._data_dir = lambda create=True: tmp_path / "state"
    first_at = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    current = [
        {
            "id": 1,
            "full_name": "org/alpha",
            "description": "first search",
            "stars": 100,
            "forks": 4,
            "language": "Python",
            "topics": ["ai-agents"],
        }
    ]
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": current,
    )

    first = plugin._fetch_github_with_snapshots(FakeDeviceConfig(), first_at)
    current[:] = [
        {
            "id": 2,
            "full_name": "org/bravo",
            "description": "second search",
            "stars": 90,
            "forks": 3,
            "language": "Rust",
            "topics": ["mcp"],
        }
    ]
    second_at = first_at + timedelta(hours=1)
    second = plugin._fetch_github_with_snapshots(FakeDeviceConfig(), second_at)

    catalog = json.loads(
        (plugin._data_dir() / "github-candidates.json").read_text(encoding="utf-8")
    )
    stars = json.loads(
        (plugin._data_dir() / "github-stars.json").read_text(encoding="utf-8")
    )

    assert [row["full_name"] for row in first] == ["org/alpha"]
    assert {row["full_name"] for row in second} == {"org/alpha", "org/bravo"}
    assert {row["full_name"] for row in catalog["items"]} == {
        "org/alpha",
        "org/bravo",
    }
    assert len(stars["1"]) == 1
    assert len(stars["2"]) == 1
    assert all("last_seen" not in row for row in second)


def test_github_candidate_catalog_expires_caps_and_current_duplicate_wins(
    tmp_path, monkeypatch
):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    candidates = [
        {
            "id": index,
            "full_name": f"old/repo-{index}",
            "description": "retained",
            "stars": index,
            "forks": 0,
            "language": "Python",
            "topics": [],
            "last_seen": (now - timedelta(days=7, minutes=index)).isoformat(),
        }
        for index in range(1, 301)
    ]
    candidates.append(
        {
            "id": 999,
            "full_name": "expired/repo",
            "description": "expired",
            "stars": 1,
            "forks": 0,
            "language": "Python",
            "topics": [],
            "last_seen": (now - timedelta(days=9)).isoformat(),
        }
    )
    candidates.append(
        {
            "id": 42,
            "full_name": "old/repo-42",
            "description": "stale metadata",
            "stars": 42,
            "forks": 0,
            "language": "Python",
            "topics": [],
            "last_seen": (now - timedelta(days=1)).isoformat(),
        }
    )
    plugin._write_json(
        plugin._data_dir() / "github-candidates.json",
        {"schema": "ai-ecosystem-pulse-github-candidates-v1", "items": candidates},
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": [
            {
                "id": 42,
                "full_name": "current/repo",
                "description": "current metadata",
                "stars": 900,
                "forks": 8,
                "language": "Go",
                "topics": ["agent-skills"],
            }
        ],
    )

    plugin._fetch_github_with_snapshots(FakeDeviceConfig(), now)
    catalog = json.loads(
        (plugin._data_dir() / "github-candidates.json").read_text(encoding="utf-8")
    )

    assert len(catalog["items"]) == 256
    assert all(row["full_name"] != "expired/repo" for row in catalog["items"])
    current = next(row for row in catalog["items"] if row["id"] == 42)
    assert current["full_name"] == "current/repo"
    assert current["description"] == "current metadata"
    assert current["stars"] == 900


def test_github_candidate_internal_fields_never_enter_aggregate_cache(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_skills",
        lambda: [VALID_SKILL_ROW],
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_huggingface",
        lambda: [
            {
                "id": "org/model",
                "trending_score": 1,
                "likes": 2,
                "downloads_30d": 3,
            }
        ],
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": [
            {
                "id": 1,
                "full_name": "org/repo",
                "stars": 10,
                "forks": 1,
                "topics": [],
            }
        ],
    )

    payload = plugin._payload({"forceRefresh": True}, FakeDeviceConfig(), now)
    serialized = json.dumps(payload, ensure_ascii=False)
    aggregate = (plugin._cache_dir() / "aggregate.json").read_text(encoding="utf-8")

    assert "last_seen" not in serialized
    assert "last_seen" not in aggregate


def test_github_token_is_sent_only_in_authorization_header():
    sentinel = "SENTINEL_GITHUB_TOKEN_7f3c"

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {
                        "id": 1,
                        "full_name": "org/repo",
                        "stargazers_count": "10",
                        "forks_count": "2",
                    }
                ]
            }

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    rows = fetch_github(token=sentinel, session=session)

    assert rows[0]["full_name"] == "org/repo"
    assert len(session.calls) == 3
    for url, kwargs in session.calls:
        assert sentinel not in url
        assert sentinel not in json.dumps(kwargs["params"], sort_keys=True)
        assert sentinel not in repr(kwargs["timeout"])
        assert kwargs["headers"]["Authorization"] == f"Bearer {sentinel}"


def test_github_token_never_appears_in_logs_errors_or_serialized_state(
    tmp_path, monkeypatch, caplog
):
    sentinel = "SENTINEL_GITHUB_TOKEN_91ab"

    class TokenDevice(FakeDeviceConfig):
        def load_env_key(self, key):
            return sentinel if key == "GITHUB_SECRET" else ""

    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_skills",
        lambda: [VALID_SKILL_ROW],
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_huggingface",
        lambda: [
            {
                "id": "org/model",
                "trending_score": 1,
                "likes": 2,
                "downloads_30d": 3,
            }
        ],
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        lambda token="": (_ for _ in ()).throw(RuntimeError(f"denied {token}")),
    )

    payload = plugin._payload({"forceRefresh": True}, TokenDevice(), now)

    assert sentinel not in caplog.text
    assert sentinel not in json.dumps(payload, ensure_ascii=False)
    assert payload["status"]["errors"]["github"] == "RuntimeError"
    for path in tmp_path.rglob("*"):
        assert sentinel not in path.name
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes()


def test_successful_token_fetch_never_persists_token(tmp_path, monkeypatch):
    sentinel = "SENTINEL_GITHUB_TOKEN_43d2"
    observed = []

    class TokenDevice(FakeDeviceConfig):
        def load_env_key(self, key):
            return sentinel if key == "GITHUB_SECRET" else ""

    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_skills",
        lambda: [VALID_SKILL_ROW],
    )
    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_huggingface",
        lambda: [
            {
                "id": "org/model",
                "trending_score": 1,
                "likes": 2,
                "downloads_30d": 3,
            }
        ],
    )

    def github_fetch(token=""):
        observed.append(token)
        return [
            {
                "id": 1,
                "full_name": "org/repo",
                "stars": 10,
                "forks": 1,
                "topics": [],
            }
        ]

    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.fetch_github",
        github_fetch,
    )

    payload = plugin._payload({"forceRefresh": True}, TokenDevice(), now)

    assert observed == [sentinel]
    assert sentinel not in json.dumps(payload, ensure_ascii=False)
    for path in tmp_path.rglob("*"):
        assert sentinel not in path.name
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes()


def test_fixture_preview_stdout_and_files_never_expose_github_token(tmp_path):
    sentinel = "SENTINEL_GITHUB_TOKEN_c048"
    script = Path(__file__).resolve().parents[1] / "tools" / "preview_ai_ecosystem_pulse.py"
    output = tmp_path / "fixture.png"
    env = dict(os.environ)
    env["GITHUB_SECRET"] = sentinel
    env["GITHUB_TOKEN"] = sentinel

    result = subprocess.run(
        [sys.executable, str(script), "--mode", "fixture", "--output", str(output)],
        cwd=script.parent.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr
    for path in tmp_path.rglob("*"):
        assert sentinel not in path.name
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes()


def test_source_caches_reject_bad_hf_metrics_and_github_star_delta(tmp_path):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    fixture = plugin._fixture_payload(now)
    cases = [
        (
            "huggingface",
            {
                "id": "org/model",
                "trending_score": "bad",
                "likes": 1,
                "downloads_30d": 1,
            },
            fixture["models"],
        ),
        (
            "github",
            {"full_name": "org/repo", "stars": 100, "stars_24h": "bad"},
            fixture["repos"],
        ),
    ]

    for name, bad_row, fallback in cases:
        plugin._write_json(
            plugin._cache_dir() / f"{name}.json",
            {
                "schema": "ai-ecosystem-pulse-v1",
                "fetched_at": now.isoformat(),
                "items": [bad_row],
            },
        )
        rows, state, error = plugin._resolve_source(
            name,
            now,
            False,
            lambda: (_ for _ in ()).throw(RuntimeError("offline")),
            fallback,
            60,
        )

        assert rows == fallback
        assert state == "fixture"
        assert error == "RuntimeError"


def test_theme_only_rejects_bad_hf_metrics_and_github_star_delta(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        plugin,
        "_fetch_live_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network forbidden")),
    )
    bad_hf = plugin._fixture_payload(now)
    bad_hf["models"][0]["likes"] = "bad"
    bad_github = plugin._fixture_payload(now)
    bad_github["repos"][0]["stars_24h"] = "bad"

    for invalid in (bad_hf, bad_github):
        plugin._write_json(plugin._cache_dir() / "aggregate.json", invalid)
        payload = plugin._payload({"_theme_render_only": True}, FakeDeviceConfig(), now)

        assert payload["status"]["sources"]["huggingface"] == "fixture"
        assert payload["status"]["sources"]["github"] == "fixture"
        assert payload["models"][0]["likes"] == 942
        assert payload["repos"][0]["stars_24h"] is None


def test_fixture_render_is_colorful_readable_800x480(tmp_path, monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(plugin, "_payload", lambda *_args: plugin._fixture_payload(now))

    image = plugin.generate_image({"themeMode": "day"}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size == (800, 480)
    assert len(image.getcolors(maxcolors=1_000_000)) > 12
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK


def test_day_and_night_palettes_produce_distinct_renders():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    payload = plugin._fixture_payload(now)

    day = plugin._render_page((800, 480), payload, {"themeMode": "day"}, now)
    night = plugin._render_page((800, 480), payload, {"themeMode": "night"}, now)

    assert hashlib.sha256(day.tobytes()).hexdigest() != hashlib.sha256(night.tobytes()).hexdigest()


def test_img2_wordmark_asset_is_transparent_wide_and_chroma_free():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})

    logo = plugin._logo_source()
    alpha = logo.getchannel("A")
    visible_pixels = [
        pixel
        for pixel in logo.get_flattened_data()
        if pixel[3] > 16
    ]

    assert logo.mode == "RGBA"
    assert logo.width / logo.height > 7
    assert alpha.getbbox() == (0, 0, logo.width, logo.height)
    assert alpha.getpixel((0, 0)) == 0
    assert not any(
        red > 180 and blue > 180 and green < 100
        for red, green, blue, _alpha in visible_pixels
    )


def test_img2_plugin_icon_is_square_transparent_and_chroma_free():
    icon_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / PLUGIN_ID
        / "icon.png"
    )

    with Image.open(icon_path) as source:
        icon = source.convert("RGBA")

    alpha = icon.getchannel("A")
    visible_pixels = [
        pixel for pixel in icon.get_flattened_data() if pixel[3] > 16
    ]

    assert icon.width == icon.height
    assert icon.width >= 512
    assert alpha.getbbox() is not None
    assert all(
        alpha.getpixel(point) == 0
        for point in (
            (0, 0),
            (icon.width - 1, 0),
            (0, icon.height - 1),
            (icon.width - 1, icon.height - 1),
        )
    )
    assert not any(
        red > 180 and blue > 180 and green < 100
        for red, green, blue, _alpha in visible_pixels
    )


def test_header_uses_fitted_day_and_night_logo_variants():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    payload = plugin._fixture_payload(now)

    day_logo = plugin._prepare_header_logo(
        (244, 32), plugin._palette({"themeMode": "day"})
    )
    night_logo = plugin._prepare_header_logo(
        (244, 32), plugin._palette({"themeMode": "night"})
    )
    day = plugin._render_page((800, 480), payload, {"themeMode": "day"}, now)
    night = plugin._render_page((800, 480), payload, {"themeMode": "night"}, now)

    assert day_logo.mode == "RGBA"
    assert 0 < day_logo.width <= 244
    assert 0 < day_logo.height <= 32
    assert night_logo.size == day_logo.size
    assert hashlib.sha256(day_logo.tobytes()).hexdigest() != hashlib.sha256(
        night_logo.tobytes()
    ).hexdigest()
    title_box = (32, 24, 276, 56)
    day_title = day.crop(title_box)
    night_title = night.crop(title_box)
    assert any(
        blue > 130 and blue > red * 1.4 and blue > green * 1.3
        for red, green, blue in day_title.get_flattened_data()
    )
    assert (
        sum(
            1
            for red, green, blue in night_title.get_flattened_data()
            if min(red, green, blue) > 180
        )
        > 100
    )


def test_panel_geometry_keeps_all_required_regions_inside_canvas():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    boxes = plugin._panel_boxes(800, 480)

    assert boxes == {
        "header": (18, 16, 782, 64),
        "skills": (18, 76, 438, 446),
        "huggingface": (450, 76, 782, 254),
        "github": (450, 266, 782, 446),
    }
    for box in boxes.values():
        assert 0 <= box[0] < box[2] < 800
        assert 0 <= box[1] < box[3] < 480


def test_skills_table_partitions_all_six_rows_inside_panel():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})

    assert plugin._skills_row_edges((30, 124, 426, 434), 6) == [
        124,
        194,
        242,
        290,
        338,
        386,
        434,
    ]


def test_github_badge_is_new_without_snapshot_and_delta_with_snapshot():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})

    assert plugin._github_badge({"stars_24h": None}) == "NEW"
    assert plugin._github_badge({"stars_24h": 37}) == "+37 / 24H"


def test_fit_text_ellipsizes_long_source_names():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    image = Image.new("RGB", (200, 80), "white")
    draw = ImageDraw.Draw(image)
    font = plugin._font(14, bold=True)

    text = plugin._fit_text(
        draw,
        "an-extremely-long-owner/an-extremely-long-repository",
        font,
        110,
    )

    assert text.endswith("…")
    bounds = draw.textbbox((0, 0), text, font=font)
    assert bounds[2] - bounds[0] <= 110


def test_render_page_passes_selected_font_family_to_every_font_request(monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    requested_families = []
    real_font = plugin._font

    def recording_font(size, bold=False, family=None):
        requested_families.append(family)
        return real_font(size, bold=bold, family=family)

    monkeypatch.setattr(plugin, "_font", recording_font)

    plugin._render_page(
        (800, 480),
        plugin._fixture_payload(now),
        {"themeMode": "day", "fontFamily": "Jost"},
        now,
    )

    assert requested_families.count("Jost") >= 20
    assert set(requested_families) <= {"Jost", DEFAULT_FONT}


def test_bundled_font_family_selection_changes_render_hash():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    payload = plugin._fixture_payload(now)

    jost = plugin._render_page(
        (800, 480),
        payload,
        {"themeMode": "day", "fontFamily": "Jost"},
        now,
    )
    napoli = plugin._render_page(
        (800, 480),
        payload,
        {"themeMode": "day", "fontFamily": "Napoli"},
        now,
    )

    assert hashlib.sha256(jost.tobytes()).hexdigest() != hashlib.sha256(napoli.tobytes()).hexdigest()


def test_invalid_font_family_falls_back_to_default(monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    fallback_font = object()
    requested_families = []

    def fake_get_font(family, size, weight):
        del size, weight
        requested_families.append(family)
        return fallback_font if family == DEFAULT_FONT else None

    monkeypatch.setattr(
        "plugins.ai_ecosystem_pulse.ai_ecosystem_pulse.get_font",
        fake_get_font,
    )

    assert plugin._font(12, bold=True, family="not-a-real-font") is fallback_font
    assert requested_families == ["not-a-real-font", DEFAULT_FONT]


def test_right_panels_never_request_fonts_smaller_than_nine_pixels(monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    payload = plugin._fixture_payload(now)
    palette = plugin._palette({"themeMode": "day"})
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))
    real_font = plugin._font
    requested_sizes = []

    def recording_font(size, bold=False, family=None):
        requested_sizes.append(int(size))
        return real_font(size, bold=bold, family=family)

    monkeypatch.setattr(plugin, "_font", recording_font)

    plugin._draw_huggingface(
        draw,
        plugin._panel_boxes(800, 480)["huggingface"],
        payload["models"],
        palette,
    )
    huggingface_sizes = list(requested_sizes)
    requested_sizes.clear()
    plugin._draw_github(
        draw,
        plugin._panel_boxes(800, 480)["github"],
        payload["repos"],
        palette,
    )
    github_sizes = list(requested_sizes)

    assert huggingface_sizes and min(huggingface_sizes) >= 9
    assert github_sizes and min(github_sizes) >= 9


def test_list_item_title_fonts_are_two_pixels_larger(monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    payload = plugin._fixture_payload(now)
    palette = plugin._palette({"themeMode": "day"})
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))
    observed_sizes = {}
    real_fit_text = plugin._fit_text
    tracked = {
        str(payload["skills"][0]["name"]),
        str(payload["skills"][1]["name"]),
        str(payload["models"][0]["id"]),
        str(payload["repos"][0]["full_name"]),
    }

    def recording_fit(draw_context, text, font, max_width):
        value = str(text)
        if value in tracked:
            observed_sizes[value] = int(font.size)
        return real_fit_text(draw_context, text, font, max_width)

    monkeypatch.setattr(plugin, "_fit_text", recording_fit)
    boxes = plugin._panel_boxes(800, 480)
    plugin._draw_skills(draw, boxes["skills"], payload["skills"], palette)
    plugin._draw_huggingface(draw, boxes["huggingface"], payload["models"], palette)
    plugin._draw_github(draw, boxes["github"], payload["repos"], palette)

    assert observed_sizes[payload["skills"][0]["name"]] == 18
    assert observed_sizes[payload["skills"][1]["name"]] == 15
    assert observed_sizes[payload["models"][0]["id"]] == 12
    assert observed_sizes[payload["repos"][0]["full_name"]] == 12


def test_safe_complete_font_falls_back_for_jost_badge_and_dogica_title():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))

    badge_font = plugin._safe_complete_font(
        draw,
        "+128 / 24H",
        preferred_size=9,
        minimum_size=9,
        max_width=50,
        family="Jost",
    )
    badge_bounds = draw.textbbox((0, 0), "+128 / 24H", font=badge_font)
    title_font = plugin._safe_complete_font(
        draw,
        "AI ECOSYSTEM PULSE",
        preferred_size=22,
        minimum_size=18,
        max_width=244,
        family="Dogica",
    )
    title_bounds = draw.textbbox((0, 0), "AI ECOSYSTEM PULSE", font=title_font)
    star_font = plugin._safe_complete_font(
        draw,
        "★162,108",
        preferred_size=9,
        minimum_size=9,
        max_width=56,
        family="Jost",
    )

    assert badge_bounds[2] - badge_bounds[0] <= 50
    assert title_bounds[2] - title_bounds[0] <= 244
    assert "dogica" not in Path(getattr(title_font, "path", "")).name.casefold()
    assert star_font.getmask("★").getbbox() is not None


def test_fixed_labels_and_key_values_never_use_ellipsis(monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    payload = plugin._fixture_payload(now)
    payload["repos"][0]["stars_24h"] = 128
    fitted_values = []
    safe_values = []
    real_fit_text = plugin._fit_text
    real_safe_font = plugin._safe_complete_font

    def recording_fit(draw, text, font, max_width):
        fitted_values.append(str(text))
        return real_fit_text(draw, text, font, max_width)

    def recording_safe_font(
        draw,
        text,
        preferred_size,
        minimum_size,
        max_width,
        family,
        bold=True,
    ):
        safe_values.append(str(text))
        return real_safe_font(
            draw,
            text,
            preferred_size,
            minimum_size,
            max_width,
            family,
            bold=bold,
        )

    monkeypatch.setattr(plugin, "_fit_text", recording_fit)
    monkeypatch.setattr(plugin, "_safe_complete_font", recording_safe_font)

    plugin._render_page(
        (800, 480),
        payload,
        {"themeMode": "day", "fontFamily": "Jost"},
        now,
    )

    required_complete = {
        "TREND",
        "LIKES",
        "DL 30D",
        "1,045,182",
        "★162,108",
        "+128 / 24H",
        "21.9K",
    }
    assert required_complete.issubset(safe_values)
    assert required_complete.isdisjoint(fitted_values)


def test_panel_titles_use_ascii_separator_instead_of_mojibake(monkeypatch):
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    payload = plugin._fixture_payload(now)
    seen_labels = []
    real_safe_font = plugin._safe_complete_font

    def recording_safe_font(
        draw,
        text,
        preferred_size,
        minimum_size,
        max_width,
        family,
        bold=True,
    ):
        seen_labels.append(str(text))
        return real_safe_font(
            draw,
            text,
            preferred_size,
            minimum_size,
            max_width,
            family,
            bold=bold,
        )

    monkeypatch.setattr(plugin, "_safe_complete_font", recording_safe_font)

    plugin._render_page(
        (800, 480),
        payload,
        {"themeMode": "day", "fontFamily": "Jost"},
        now,
    )

    expected = {
        "AGENT SKILLS / 24H",
        "HUGGING FACE / TRENDING",
        "GITHUB / AI RISING",
    }
    panel_titles = {
        label
        for label in seen_labels
        if label.startswith(("AGENT SKILLS", "HUGGING FACE", "GITHUB"))
    }
    assert panel_titles == expected
    assert all(label.isascii() for label in panel_titles)


def test_every_available_font_renders_fixed_values_without_error():
    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
    payload = plugin._fixture_payload(now)
    payload["repos"][0]["stars_24h"] = 128
    available_fonts = plugin.generate_settings_template()["available_fonts"]

    assert available_fonts
    for family in available_fonts:
        image = plugin._render_page(
            (800, 480),
            payload,
            {"themeMode": "day", "fontFamily": family},
            now,
        )

        assert image.size == (800, 480)


def test_preview_script_exists_and_supports_fixture_and_live_modes():
    path = Path(__file__).resolve().parents[1] / "tools" / "preview_ai_ecosystem_pulse.py"
    text = path.read_text(encoding="utf-8")

    assert 'choices=("fixture", "live")' in text
    assert "AI_ECOSYSTEM_PULSE_CACHE_DIR" in text
    assert "image.save(output, format=\"PNG\")" in text
