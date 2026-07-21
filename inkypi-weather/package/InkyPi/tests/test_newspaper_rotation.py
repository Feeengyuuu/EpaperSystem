import sys
import uuid
import os
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.newspaper.newspaper import DEFAULT_MEDIA_SOURCES, Newspaper
import plugins.newspaper.newspaper as newspaper_module
from plugins.newspaper import presentation_bank as newspaper_bank
from security.ssrf import ApprovedTarget, UnsafeTarget
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from runtime.runtime_state import PresentationCommitReceipt


TEST_STATE_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "newspaper_rotation_tests"


class DeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key, default=None):
        if key == "orientation":
            return "horizontal"
        return default


def make_plugin(name):
    plugin = Newspaper({"id": "newspaper"})
    base = TEST_STATE_ROOT / f"{name}-{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)

    def plugin_dir(path=None):
        return str(base / path) if path else str(base)

    plugin.get_plugin_dir = plugin_dir
    return plugin


def bound_settings(instance_uuid="newspaper-instance", **overrides):
    return bind_presentation_instance_identity(
        {
            "mediaRotationMode": "rotate",
            "mediaSources": DEFAULT_MEDIA_SOURCES,
            **overrides,
        },
        instance_uuid,
    )


def request(request_id="1" * 32, origin="origin-display"):
    return PresentationRequestContext(
        request_id=request_id,
        requested_at="2026-07-12T15:00:00+00:00",
        origin_display_commit_id=origin,
        last_receipt=None,
    )


def seed_legacy_wrong_size_bank(plugin, settings, color="white"):
    sources = plugin._sources_for_settings(settings)
    bank = plugin._presentation_bank(settings, sources, (800, 480))
    document, profile = bank.load_for_data()
    transaction = bank.transaction()
    record = bank.ingest(
        profile,
        sources[0],
        Image.new("RGB", (700, 1000), color),
        transaction=transaction,
    )
    profile["current_selection"] = {
        "record_key": record["record_key"],
        "request_id": None,
    }
    profile["refill_in_progress"] = False
    bank.save(document, transaction=transaction)
    return record


def receipt(request_id="1" * 32, display="prepared-display"):
    return PresentationCommitReceipt(
        request_id=request_id,
        committed_at="2026-07-12T15:01:00+00:00",
        display_commit_id=display,
        structural_generation=1,
        settings_revision=1,
        theme_mode=None,
    )


def tree_snapshot(root):
    root = Path(root)
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_parse_media_sources_accepts_urls_and_newspaper_slugs():
    plugin = make_plugin("parse")

    sources = plugin._parse_media_sources(
        """
        BBC News|url|https://www.bbc.com/news
        CNN|https://www.cnn.com
        China Daily|newspaper|chi_cd
        ny_nyt
        """
    )

    assert sources == [
        {
            "id": "url:https://www.bbc.com/news",
            "name": "BBC News",
            "type": "url",
            "value": "https://www.bbc.com/news",
        },
        {
            "id": "url:https://www.cnn.com",
            "name": "CNN",
            "type": "url",
            "value": "https://www.cnn.com",
        },
        {
            "id": "newspaper:CHI_CD",
            "name": "China Daily",
            "type": "newspaper",
            "value": "CHI_CD",
        },
        {
            "id": "newspaper:NY_NYT",
            "name": "NY_NYT",
            "type": "newspaper",
            "value": "NY_NYT",
        },
    ]


def test_parse_media_sources_accepts_luoyang_evening_news_source():
    plugin = make_plugin("parse-lywb")

    sources = plugin._parse_media_sources("Luoyang Evening News|lywb|A01")

    assert sources == [
        {
            "id": "lywb:A01",
            "name": "Luoyang Evening News",
            "type": "lywb",
            "value": "A01",
        }
    ]


def test_default_media_sources_include_luoyang_evening_news():
    plugin = make_plugin("default-lywb")

    sources = plugin._parse_media_sources(DEFAULT_MEDIA_SOURCES)

    assert any(source["id"] == "lywb:A01" for source in sources)


def test_newspaper_fingerprint_defaults_and_source_universe():
    plugin = make_plugin("fingerprint-defaults")
    sources = plugin._parse_media_sources(DEFAULT_MEDIA_SOURCES)

    omitted = newspaper_bank.settings_fingerprint({}, sources, (800, 480))
    explicit = newspaper_bank.settings_fingerprint(
        {"mediaRotationMode": "rotate", "newspaperSlug": ""},
        sources,
        (800, 480),
    )

    assert omitted == explicit
    assert omitted != newspaper_bank.settings_fingerprint({}, sources, (480, 800))
    assert omitted != newspaper_bank.settings_fingerprint({}, sources[:-1], (800, 480))


def test_select_next_source_persists_sequential_rotation():
    plugin = make_plugin("sequential")
    sources = plugin._parse_media_sources(
        """
        BBC News|url|https://www.bbc.com/news
        CNN|url|https://www.cnn.com
        Xinhua|url|https://www.xinhuanet.com/
        """
    )

    selected = [plugin._select_next_source(sources)["name"] for _ in range(4)]

    assert selected == ["BBC News", "CNN", "Xinhua", "BBC News"]


def test_rotating_image_skips_failed_source(monkeypatch):
    plugin = make_plugin("skip_failed")
    sources = plugin._parse_media_sources(
        """
        Broken|url|https://example.invalid
        Working|newspaper|chi_cd
        """
    )
    expected = Image.new("RGB", (10, 10), "white")

    def fake_fetch_source_image(source, device_config):
        if source["name"] == "Broken":
            return None
        return expected

    monkeypatch.setattr(plugin, "_fetch_source_image", fake_fetch_source_image)

    image = plugin._generate_rotating_image(sources, DeviceConfig())

    assert image is expected
    assert plugin._select_next_source(sources)["name"] == "Broken"


def test_url_source_returns_none_when_screenshot_fails(monkeypatch):
    plugin = make_plugin("url-no-fallback")
    source = plugin._parse_media_sources("BBC News|url|https://www.bbc.com/news")[0]

    monkeypatch.setattr(
        plugin,
        "_fetch_url_screenshot",
        lambda url, device_config, **_kwargs: None,
    )

    image = plugin._fetch_source_image(source, DeviceConfig())

    assert image is None


def test_newspaper_source_is_normalized_to_exact_display_dimensions(monkeypatch):
    plugin = make_plugin("exact-provider-dimensions")
    source = plugin._parse_media_sources("China Daily|newspaper|chi_cd")[0]
    raw = Image.new("RGB", (700, 1000), "white")

    monkeypatch.setattr(
        plugin,
        "_fetch_newspaper_cover",
        lambda *_args, **_kwargs: raw,
    )

    image = plugin._fetch_source_image(source, DeviceConfig())

    assert image.size == (800, 480)
    assert image.mode == "RGB"


def test_luoyang_evening_news_builds_a01_pdf_url():
    plugin = make_plugin("lywb-url")

    url = plugin._build_lywb_pdf_url(datetime(2026, 6, 3))

    assert url == ("https://lywb.lyd.com.cn/images2/2/2026-06/03/A01/20260603A01_pdf.pdf")


def test_luoyang_evening_news_fetches_pdf_front_page(monkeypatch):
    plugin = make_plugin("lywb-fetch")
    raw_page = Image.new("RGB", (700, 1000), "white")
    requested_urls = []

    monkeypatch.setattr(
        plugin,
        "_lywb_candidate_dates",
        lambda: [datetime(2026, 6, 3)],
    )
    monkeypatch.setattr(
        plugin,
        "_download_pdf",
        lambda url, **_kwargs: requested_urls.append(url) or b"%PDF-1.7 fake",
    )
    monkeypatch.setattr(
        plugin,
        "_render_pdf_first_page",
        lambda pdf_bytes, **_kwargs: raw_page,
    )

    image = plugin._fetch_luoyang_evening_news_cover(DeviceConfig())

    assert requested_urls == ["https://lywb.lyd.com.cn/images2/2/2026-06/03/A01/20260603A01_pdf.pdf"]
    assert image.size == raw_page.size
    assert image.mode == "RGB"


def test_render_pdf_first_page_returns_rgb_image():
    fitz = pytest.importorskip("fitz")
    plugin = make_plugin("lywb-render")
    document = fitz.open()
    page = document.new_page(width=100, height=120)
    page.insert_text((12, 24), "A01")
    pdf_bytes = document.tobytes()
    document.close()

    image = plugin._render_pdf_first_page(pdf_bytes)

    assert image.size == (200, 240)
    assert image.mode == "RGB"


def test_extract_headlines_from_frontpage_html_normalizes_simplified_chinese():
    plugin = make_plugin("extract-html")
    traditional_headline = "\u570b\u969b\u65b0\u805e\u767c\u4f48\u6700\u65b0\u7d93\u6fdf\u89c0\u5bdf\u5831\u544a"

    headlines = plugin._extract_headlines(
        f"""
        <html><body>
          <nav><a>Sign in</a><a>Weather</a></nav>
          <h1>China and US officials open new round of trade talks</h1>
          <a href="/story">{traditional_headline}</a>
          <script><a>Hidden fake headline should not appear</a></script>
        </body></html>
        """
    )

    assert headlines == [
        "China and US officials open new round of trade talks",
        "\u56fd\u9645\u65b0\u95fb\u53d1\u5e03\u6700\u65b0\u7ecf\u6d4e\u89c2\u5bdf\u62a5\u544a",
    ]


def test_clean_html_text_repairs_common_chinese_mojibake():
    plugin = make_plugin("mojibake")
    expected = "\u65b0\u534e\u793e\u53d1\u5e03\u6700\u65b0\u7ecf\u6d4e\u89c2\u5bdf\u62a5\u544a"
    mojibake = expected.encode("utf-8").decode("latin1")

    assert plugin._clean_html_text(mojibake) == expected


def test_render_headlines_page_supports_simplified_chinese_text():
    plugin = make_plugin("render-cn")
    source = plugin._parse_media_sources("Xinhua|url|https://www.xinhuanet.com/")[0]
    headlines = [
        "\u65b0\u534e\u793e\u53d1\u5e03\u6700\u65b0\u7ecf\u6d4e\u89c2\u5bdf\u62a5\u544a",
        "\u591a\u5730\u63a8\u51fa\u4fbf\u6c11\u670d\u52a1\u65b0\u4e3e\u63aa",
    ]

    image = plugin._render_headlines_page(source, headlines, DeviceConfig())

    assert image.size == (800, 480)
    assert image.mode == "RGB"


def test_newspaper_declares_prepared_bank_presentation():
    plugin = make_plugin("presentation-mode")

    assert plugin.presentation_mode({}) is PresentationMode.PREPARED_BANK


def test_newspaper_data_normalizes_legacy_wrong_size_current(monkeypatch):
    plugin = make_plugin("legacy-data-size")
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    seed_legacy_wrong_size_bank(plugin, settings)
    monkeypatch.setattr(plugin, "_fetch_source_image", lambda *_args, **_kwargs: None)

    image = plugin.generate_image(settings, DeviceConfig())

    assert image.size == (800, 480)


def test_newspaper_prepare_normalizes_legacy_wrong_size_selection():
    plugin = make_plugin("legacy-prepare-size")
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    seed_legacy_wrong_size_bank(plugin, settings)

    prepared = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request(),
        resolved_theme_context={
            "mode": "day",
            "palette": {"background": (255, 255, 255), "accent": (51, 51, 51)},
        },
    )

    assert prepared.image.size == (800, 480)


def test_newspaper_theme_only_normalizes_legacy_wrong_size_current():
    plugin = make_plugin("legacy-theme-size")
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    seed_legacy_wrong_size_bank(plugin, settings)
    themed_settings = {
        **settings,
        "_theme_render_only": True,
        "_inkypi_theme": {
            "mode": "day",
            "palette": {"background": (255, 255, 255), "accent": (51, 51, 51)},
        },
    }

    image = plugin.generate_image(themed_settings, DeviceConfig())

    assert image.size == (800, 480)


def test_newspaper_data_limits_browser_and_http_attempts(monkeypatch):
    plugin = make_plugin("bounded-attempts")
    settings = bound_settings(
        mediaSources="""
        Browser A|url|https://www.bbc.com/news
        Browser B|url|https://www.cnn.com
        Paper A|newspaper|chi_cd
        Paper B|newspaper|chi_pd
        Paper C|newspaper|ny_nyt
        Paper D|newspaper|dc_wp
        """
    )
    calls = []

    def capture(source, _device_config, **_kwargs):
        calls.append(source["type"])
        return Image.new("RGB", (800, 480), (len(calls) * 30, 40, 80))

    monkeypatch.setattr(plugin, "_fetch_source_image", capture)

    plugin.generate_image(settings, DeviceConfig())

    assert len(calls) == 4
    assert calls.count("url") <= 1
    assert sum(kind != "url" for kind in calls) <= 3


def test_newspaper_data_respects_total_time_budget(monkeypatch):
    plugin = make_plugin("total-budget")
    clock = {"value": 0.0}
    calls = []
    settings = bound_settings(mediaSources="\n".join(f"Paper {index}|newspaper|paper_{index}" for index in range(8)))

    def fail_after_work(source, _device_config, **_kwargs):
        calls.append(source["id"])
        clock["value"] += 31.0
        return None

    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"], raising=False)
    monkeypatch.setattr(plugin, "_fetch_source_image", fail_after_work)

    with pytest.raises(RuntimeError):
        plugin.generate_image(settings, DeviceConfig())

    assert clock["value"] <= 93.0
    assert len(calls) == 3


def test_newspaper_data_does_not_consume_source_rotation(monkeypatch):
    plugin = make_plugin("data-no-consume")
    settings = bound_settings(
        mediaSources="""
        Paper A|newspaper|chi_cd
        Paper B|newspaper|chi_pd
        Paper C|newspaper|ny_nyt
        """
    )
    sources = plugin._parse_media_sources(settings["mediaSources"])
    pool_key = plugin._source_pool_key(sources)
    initial = {
        pool_key: {
            "next_index": 1,
            "last_selected": sources[0]["id"],
            "pool_size": 3,
            "source_ids": [source["id"] for source in sources],
        }
    }
    plugin._write_rotation_state(initial)
    before = plugin._rotation_state_path().read_bytes()
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )

    plugin.generate_image(settings, DeviceConfig())

    assert plugin._rotation_state_path().read_bytes() == before


def test_newspaper_prepare_is_provider_free_and_receipt_advances_once(monkeypatch):
    plugin = make_plugin("provider-free-prepare")
    settings = bound_settings(
        mediaSources="""
        Paper A|newspaper|chi_cd
        Paper B|newspaper|chi_pd
        Paper C|newspaper|ny_nyt
        Paper D|newspaper|dc_wp
        """
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda source, *_args, **_kwargs: Image.new("RGB", (800, 480), (len(source["id"]) * 7 % 255, 40, 80)),
    )
    plugin.generate_image(settings, DeviceConfig())
    before_prepare = plugin._presentation_state_path().read_bytes()
    for name in (
        "_fetch_source_image",
        "_fetch_url_screenshot",
        "_fetch_web_headlines",
        "_download_pdf",
    ):
        monkeypatch.setattr(
            plugin,
            name,
            lambda *_args, _name=name, **_kwargs: pytest.fail(f"presentation called provider {_name}"),
        )

    prepared = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request(),
        resolved_theme_context={
            "mode": "day",
            "palette": {"background": (255, 255, 255), "accent": (51, 51, 51)},
        },
    )
    after_prepare = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(settings, receipt())
    after_receipt = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(settings, receipt())

    assert prepared.changed is True
    assert before_prepare != after_prepare
    assert after_prepare != after_receipt
    assert plugin._presentation_state_path().read_bytes() == after_receipt


def test_newspaper_wrong_instance_receipt_does_not_consume(monkeypatch):
    plugin = make_plugin("wrong-instance")
    first = bound_settings("instance-a", mediaSources="Paper A|newspaper|chi_cd")
    second = bound_settings("instance-b", mediaSources="Paper A|newspaper|chi_cd")
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    plugin.generate_image(first, DeviceConfig())
    plugin.prepare_presentation(
        first,
        DeviceConfig(),
        request=request(),
        resolved_theme_context=None,
    )
    before = plugin._presentation_state_path().read_bytes()

    plugin.reconcile_presentation_receipt(second, receipt())

    assert plugin._presentation_state_path().read_bytes() == before


def test_newspaper_pending_receipt_survives_same_instance_profile_switch(monkeypatch):
    plugin = make_plugin("profile-switch-receipt")
    first = bound_settings(
        "shared-instance",
        mediaSources="Paper A|newspaper|paper_a",
    )
    second = bound_settings(
        "shared-instance",
        mediaSources="Paper B|newspaper|paper_b",
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda source, *_args, **_kwargs: Image.new(
            "RGB", (800, 480), "red" if source["value"] == "PAPER_A" else "blue"
        ),
    )
    plugin.generate_image(first, DeviceConfig())
    plugin.prepare_presentation(
        first,
        DeviceConfig(),
        request=request(),
        resolved_theme_context=None,
    )
    plugin.generate_image(second, DeviceConfig())

    plugin.reconcile_presentation_receipt(first, receipt())

    state = newspaper_bank.read_state(plugin._presentation_state_path())
    committed = [
        profile for profile in state["profiles"].values() if profile.get("last_applied_request_id") == "1" * 32
    ]
    assert len(committed) == 1
    assert committed[0]["pending_selection"] is None


def test_newspaper_stale_media_is_not_reported_fresh(monkeypatch):
    plugin = make_plugin("stale-provenance")
    settings = bound_settings(mediaSources="Paper A|newspaper|chi_cd")
    stale_time = datetime.now(timezone.utc) - timedelta(hours=49)
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    monkeypatch.setattr(plugin, "_now_utc", lambda: stale_time, raising=False)
    plugin.generate_image(settings, DeviceConfig())
    monkeypatch.setattr(
        plugin,
        "_now_utc",
        lambda: stale_time + timedelta(hours=49),
        raising=False,
    )

    image = plugin.generate_image(
        {**settings, "_theme_render_only": True},
        DeviceConfig(),
    )

    assert image.info["inkypi_source_provenance"] == "stale_cache"


def test_newspaper_refill_cursor_continues_next_data_run(monkeypatch):
    plugin = make_plugin("refill-cursor")
    settings = bound_settings(
        mediaSources=(
            "Browser 0|url|https://www.bbc.com/news\n"
            + "\n".join(f"Paper {index}|newspaper|paper_{index}" for index in range(1, 6))
        )
    )
    calls = []

    def capture(source, *_args, **_kwargs):
        calls.append(source["id"])
        if len(calls) < 4:
            return None
        return Image.new("RGB", (800, 480), (len(calls) * 20 % 255, 30, 60))

    monkeypatch.setattr(plugin, "_fetch_source_image", capture)
    plugin.generate_image(settings, DeviceConfig())
    first_run = list(calls)
    plugin.generate_image(settings, DeviceConfig())
    second_run = calls[len(first_run) :]

    assert len(first_run) == 4
    assert second_run[0] == "newspaper:PAPER_4"


def test_newspaper_refill_in_progress_reaches_six_ready_records(monkeypatch):
    plugin = make_plugin("ready-target-six")
    settings = bound_settings(
        mediaSources=(
            "Browser 0|url|https://www.bbc.com/news\n"
            + "\n".join(f"Paper {index}|newspaper|paper_{index}" for index in range(1, 6))
        )
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda source, *_args, **_kwargs: Image.new("RGB", (800, 480), (len(source["id"]) * 11 % 255, 30, 70)),
    )

    plugin.generate_image(settings, DeviceConfig())
    plugin.generate_image(settings, DeviceConfig())

    state = newspaper_bank.read_state(plugin._presentation_state_path())
    fingerprint = state["instance_profiles"]["newspaper-instance"]
    profile = state["profiles"][fingerprint]
    assert len(profile["records"]) == newspaper_bank.READY_TARGET == 6
    assert profile["refill_in_progress"] is False


@pytest.mark.parametrize("force_key", ["forceRefresh", "force_refresh"])
def test_newspaper_force_refresh_attempts_provider_for_full_bank_without_consuming_selection(
    monkeypatch,
    force_key,
):
    plugin = make_plugin(f"force-full-{force_key}")
    settings = bound_settings(
        mediaSources="\n".join(
            f"Paper {index}|newspaper|paper_{index}" for index in range(6)
        )
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda source, *_args, **_kwargs: Image.new(
            "RGB", (800, 480), (len(source["id"]) * 11 % 255, 30, 70)
        ),
    )
    plugin.generate_image(settings, DeviceConfig())
    plugin.generate_image(settings, DeviceConfig())
    before = newspaper_bank.read_state(plugin._presentation_state_path())
    fingerprint = before["instance_profiles"]["newspaper-instance"]
    current = dict(before["profiles"][fingerprint]["current_selection"])
    calls = []

    def refreshed(source, *_args, **_kwargs):
        calls.append(source["id"])
        return Image.new("RGB", (800, 480), (200, len(calls) * 20, 80))

    monkeypatch.setattr(plugin, "_fetch_source_image", refreshed)

    image = plugin.generate_image({**settings, force_key: "true"}, DeviceConfig())

    after = newspaper_bank.read_state(plugin._presentation_state_path())
    profile = after["profiles"][fingerprint]
    assert calls
    assert profile["last_provider_status"] == "success"
    assert datetime.fromisoformat(profile["last_provider_attempt_at"]).tzinfo is not None
    assert profile["current_selection"] == current
    assert profile["pending_selection"] is None
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE


def test_newspaper_force_refresh_provider_error_marks_warm_bank_stale_and_skips_cache(
    monkeypatch,
):
    plugin = make_plugin("force-provider-error")
    settings = bound_settings(
        mediaSources="\n".join(
            f"Paper {index}|newspaper|paper_{index}" for index in range(6)
        )
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    plugin.generate_image(settings, DeviceConfig())
    plugin.generate_image(settings, DeviceConfig())
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider offline")),
    )

    image = plugin.generate_image(
        {**settings, "forceRefresh": "true"},
        DeviceConfig(),
    )

    state = newspaper_bank.read_state(plugin._presentation_state_path())
    fingerprint = state["instance_profiles"]["newspaper-instance"]
    assert state["profiles"][fingerprint]["last_provider_status"] == "error"
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True


def test_newspaper_metadata_placeholder_is_local_fallback(monkeypatch):
    plugin = make_plugin("metadata-fallback")
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    monkeypatch.setattr(plugin, "_fetch_source_image", lambda *_args, **_kwargs: None)

    image = plugin.generate_image(settings, DeviceConfig())

    assert image.info["inkypi_source_provenance"] == "local_fallback"
    state = newspaper_bank.read_state(plugin._presentation_state_path())
    fingerprint = state["instance_profiles"]["newspaper-instance"]
    assert state["profiles"][fingerprint]["records"] == []


def test_newspaper_deadline_rollback_leaves_tree_identical(monkeypatch):
    plugin = make_plugin("deadline-rollback")
    root = Path(plugin.get_plugin_dir())
    before = tree_snapshot(root)
    clock = {"value": 0.0}
    settings = bound_settings(mediaSources="\n".join(f"Paper {index}|newspaper|paper_{index}" for index in range(5)))

    def cross_deadline(*_args, **_kwargs):
        clock["value"] = 91.0
        return Image.new("RGB", (800, 480), "white")

    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    monkeypatch.setattr(plugin, "_fetch_source_image", cross_deadline)

    with pytest.raises(RuntimeError, match="deadline"):
        plugin.generate_image(settings, DeviceConfig())

    assert tree_snapshot(root) == before


def test_newspaper_state_write_failure_rolls_back_state_and_media(monkeypatch):
    plugin = make_plugin("state-write-rollback")
    settings = bound_settings(
        mediaSources=(
            "Browser 0|url|https://www.bbc.com/news\n"
            + "\n".join(f"Paper {index}|newspaper|paper_{index}" for index in range(1, 6))
        )
    )
    counter = {"value": 0}

    def capture(source, *_args, **_kwargs):
        counter["value"] += 1
        return Image.new(
            "RGB",
            (800, 480),
            (counter["value"] * 25 % 255, len(source["id"]) * 7 % 255, 80),
        )

    monkeypatch.setattr(plugin, "_fetch_source_image", capture)
    plugin.generate_image(settings, DeviceConfig())
    before = tree_snapshot(plugin.get_plugin_dir())
    original_write = newspaper_bank._secure_write_json

    def write_then_fail(path, document):
        original_write(path, document)
        raise OSError("state write failed after replace")

    monkeypatch.setattr(newspaper_bank, "_secure_write_json", write_then_fail)

    with pytest.raises(OSError, match="state write failed"):
        plugin.generate_image(settings, DeviceConfig())

    assert tree_snapshot(plugin.get_plugin_dir()) == before


def test_newspaper_failed_display_and_replay_do_not_double_consume(monkeypatch):
    plugin = make_plugin("failed-display")
    settings = bound_settings(mediaSources="\n".join(f"Paper {index}|newspaper|paper_{index}" for index in range(4)))
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda source, *_args, **_kwargs: Image.new("RGB", (800, 480), (len(source["id"]) * 9 % 255, 50, 90)),
    )
    plugin.generate_image(settings, DeviceConfig())
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request(),
        resolved_theme_context=None,
    )
    pending = plugin._presentation_state_path().read_bytes()

    plugin.reconcile_presentation_receipt(settings, None)
    assert plugin._presentation_state_path().read_bytes() == pending

    plugin.reconcile_presentation_receipt(settings, receipt())
    committed = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(settings, receipt())
    assert plugin._presentation_state_path().read_bytes() == committed


def test_newspaper_round_boundary_does_not_repeat_current(monkeypatch):
    plugin = make_plugin("round-boundary")
    settings = bound_settings(mediaSources="\n".join(f"Paper {index}|newspaper|paper_{index}" for index in range(4)))
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda source, *_args, **_kwargs: Image.new(
            "RGB", (800, 480), (int(source["value"].split("_")[-1]) * 40, 50, 90)
        ),
    )
    plugin.generate_image(settings, DeviceConfig())
    state = newspaper_bank.read_state(plugin._presentation_state_path())
    fingerprint = state["instance_profiles"]["newspaper-instance"]
    initial = state["profiles"][fingerprint]["current_selection"]["record_key"]

    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request("2" * 32),
        resolved_theme_context=None,
    )
    staged = newspaper_bank.read_state(plugin._presentation_state_path())
    selected = staged["profiles"][fingerprint]["pending_selection"]["record_key"]

    assert selected != initial


def test_newspaper_http_redirect_to_private_is_rejected_before_second_connect(monkeypatch):
    plugin = make_plugin("redirect-private")
    approved = ApprovedTarget(
        normalized_url="https://www.bbc.com/news",
        scheme="https",
        hostname="www.bbc.com",
        port=443,
        addresses=("93.184.216.34",),
    )

    class Policy:
        def resolve_and_validate(self, url):
            if "127.0.0.1" in url:
                raise UnsafeTarget("private")
            return approved

    class Redirect:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/private"}

        def close(self):
            return None

    calls = []
    monkeypatch.setattr(newspaper_module, "get_ssrf_policy", lambda: Policy())
    monkeypatch.setattr(
        newspaper_module._PinnedResponse,
        "open",
        lambda *_args, **_kwargs: calls.append("connect") or Redirect(),
    )

    with pytest.raises(UnsafeTarget):
        plugin._download_provider_bytes(
            approved.normalized_url,
            allowed_hosts=("www.bbc.com",),
            max_bytes=100,
            timeout=20,
        )

    assert calls == ["connect"]


def test_newspaper_final_document_outside_source_allowlist_is_rejected(monkeypatch):
    plugin = make_plugin("final-allowlist")
    first = ApprovedTarget(
        "https://www.bbc.com/news",
        "https",
        "www.bbc.com",
        443,
        ("93.184.216.34",),
    )
    foreign = ApprovedTarget(
        "https://example.com/final",
        "https",
        "example.com",
        443,
        ("93.184.216.34",),
    )

    class Policy:
        def resolve_and_validate(self, url):
            return foreign if "example.com" in url else first

    class Redirect:
        status_code = 302
        headers = {"Location": "https://example.com/final"}

        def close(self):
            return None

    monkeypatch.setattr(newspaper_module, "get_ssrf_policy", lambda: Policy())
    monkeypatch.setattr(
        newspaper_module._PinnedResponse,
        "open",
        lambda *_args, **_kwargs: Redirect(),
    )

    with pytest.raises(RuntimeError, match="allowlist"):
        plugin._download_provider_bytes(
            first.normalized_url,
            allowed_hosts=("www.bbc.com",),
            max_bytes=100,
            timeout=20,
        )


def test_newspaper_redirect_custom_port_is_rejected():
    plugin = make_plugin("redirect-port")
    approved = ApprovedTarget(
        "https://www.bbc.com:444/news",
        "https",
        "www.bbc.com",
        444,
        ("93.184.216.34",),
    )

    with pytest.raises(RuntimeError, match="port"):
        plugin._validate_approved_target(approved, ("www.bbc.com",))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "data:text/plain,x", "javascript:alert(1)"])
def test_newspaper_browser_rejects_non_http_schemes(url):
    plugin = make_plugin("browser-scheme")

    with pytest.raises(RuntimeError, match="allowlist"):
        plugin._allowed_hosts_for_url(url)


def test_newspaper_pdf_html_png_and_pixel_limits(monkeypatch):
    plugin = make_plugin("object-limits")
    with pytest.raises(RuntimeError, match="PDF"):
        plugin._render_pdf_first_page(b"%PDF" + b"x" * newspaper_module.MAX_PDF_BYTES)
    with pytest.raises(RuntimeError, match="image"):
        plugin._validate_image_dimensions((8192, 8192))
    with pytest.raises(RuntimeError, match="image"):
        plugin._decode_remote_image(b"x" * (newspaper_module.MAX_PNG_BYTES + 1))

    class TooManyPages:
        def __len__(self):
            return newspaper_module.MAX_PDF_PAGES + 1

        def close(self):
            return None

    fake_fitz = SimpleNamespace(open=lambda **_kwargs: TooManyPages())
    monkeypatch.setattr(plugin, "_import_pymupdf", lambda: fake_fitz)
    with pytest.raises(RuntimeError, match="page"):
        plugin._render_pdf_first_page(b"%PDF fake")


def test_newspaper_remote_image_rejects_disallowed_bmp():
    plugin = make_plugin("remote-image-format")
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, "BMP")

    with pytest.raises(RuntimeError, match="decode|format|safety"):
        plugin._decode_remote_image(buffer.getvalue())


def test_newspaper_media_root_symlink_never_touches_external_file(tmp_path):
    plugin = make_plugin("media-root-link")
    settings = bound_settings(mediaSources="Paper A|newspaper|chi_cd")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    media_root = plugin._presentation_media_dir()
    try:
        media_root.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink unavailable")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    try:
        with pytest.raises(RuntimeError, match="safe|link|root|directory"):
            plugin.generate_image(settings, DeviceConfig())
    finally:
        monkeypatch.undo()
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_newspaper_state_symlink_is_never_followed(tmp_path, monkeypatch):
    plugin = make_plugin("state-link")
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    outside = tmp_path / "outside-state.json"
    outside.write_text('{"sentinel": true}', encoding="utf-8")
    state_path = plugin._presentation_state_path()
    try:
        state_path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("state symlink unavailable")
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )

    with pytest.raises(RuntimeError, match="state|regular|safe|link"):
        plugin.generate_image(settings, DeviceConfig())

    assert outside.read_text(encoding="utf-8") == '{"sentinel": true}'


def test_newspaper_theme_and_preview_are_provider_free_and_tree_stable(monkeypatch):
    plugin = make_plugin("theme-preview-stable")
    root = Path(plugin.get_plugin_dir())
    preview_before = tree_snapshot(root)
    for name in ("_fetch_source_image", "_download_provider_bytes", "_fetch_url_screenshot"):
        monkeypatch.setattr(
            plugin,
            name,
            lambda *_args, _name=name, **_kwargs: pytest.fail(f"preview called {_name}"),
        )
    preview = plugin.generate_image({}, DeviceConfig())
    assert preview.info["inkypi_source_provenance"] == "local_fallback"
    assert tree_snapshot(root) == preview_before

    monkeypatch.undo()
    settings = bound_settings(mediaSources="Paper A|newspaper|chi_cd")
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    plugin.generate_image(settings, DeviceConfig())
    stable = tree_snapshot(root)
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: pytest.fail("theme called provider"),
    )
    themed = plugin.generate_image(
        {
            **settings,
            "_theme_render_only": True,
            "_inkypi_theme": {
                "mode": "night",
                "palette": {"background": (17, 17, 17), "accent": (215, 215, 215)},
            },
        },
        DeviceConfig(),
    )
    assert themed.info["inkypi_theme_mode"] == "night"
    assert tree_snapshot(root) == stable


def test_newspaper_cold_theme_creates_no_paths():
    plugin = make_plugin("cold-theme-stable")
    root = Path(plugin.get_plugin_dir())
    before = tree_snapshot(root)

    with pytest.raises(RuntimeError, match="cold|unavailable"):
        plugin.generate_image(
            {
                **bound_settings(mediaSources="Paper A|newspaper|chi_cd"),
                "_theme_render_only": True,
            },
            DeviceConfig(),
        )

    assert tree_snapshot(root) == before
    assert not plugin._presentation_media_dir().exists()


def test_newspaper_browser_html_is_network_closed():
    plugin = make_plugin("browser-sanitize")
    sanitized = plugin._sanitize_browser_html(
        """
        <script>location='https://example.com/'</script>
        <img src="https://example.com/a.png">
        <a href="https://example.com/download">download</a>
        <h1>Front page</h1>
        """,
        "https://www.bbc.com/news",
    )

    assert "Content-Security-Policy" in sanitized
    assert "<script" not in sanitized
    assert " src=" not in sanitized
    assert " href=" not in sanitized
    assert "Front page" in sanitized


def test_newspaper_content_length_limit_rejects_before_body(monkeypatch):
    plugin = make_plugin("content-length")
    approved = ApprovedTarget(
        "https://www.bbc.com/news",
        "https",
        "www.bbc.com",
        443,
        ("93.184.216.34",),
    )

    class Policy:
        def resolve_and_validate(self, _url):
            return approved

    class Oversized:
        status_code = 200
        headers = {"Content-Length": str(newspaper_module.MAX_HTML_BYTES + 1)}

        def iter_content(self, _chunk_size):
            pytest.fail("oversized body must not be read")

        def close(self):
            return None

    monkeypatch.setattr(newspaper_module, "get_ssrf_policy", lambda: Policy())
    monkeypatch.setattr(
        newspaper_module._PinnedResponse,
        "open",
        lambda *_args, **_kwargs: Oversized(),
    )

    with pytest.raises(RuntimeError, match="size"):
        plugin._download_provider_bytes(
            approved.normalized_url,
            allowed_hosts=("www.bbc.com",),
            max_bytes=newspaper_module.MAX_HTML_BYTES,
            timeout=20,
        )


def test_newspaper_pinned_transport_uses_numeric_address_sni_and_host(monkeypatch):
    approved = ApprovedTarget(
        "https://www.bbc.com/news",
        "https",
        "www.bbc.com",
        443,
        ("93.184.216.34",),
    )
    evidence = {"sent": b""}

    class Socket:
        def settimeout(self, value):
            evidence["timeout"] = value

        def sendall(self, payload):
            evidence["sent"] = payload

        def close(self):
            evidence["closed"] = True

    raw = Socket()

    class Context:
        def wrap_socket(self, source, *, server_hostname):
            assert source is raw
            evidence["sni"] = server_hostname
            return source

    class Response:
        status = 200
        headers = {}

        def __init__(self, source):
            assert source is raw

        def begin(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(
        newspaper_module.socket,
        "create_connection",
        lambda target, timeout: evidence.update({"connect": target, "connect_timeout": timeout}) or raw,
    )
    monkeypatch.setattr(
        newspaper_module.ssl,
        "create_default_context",
        lambda: Context(),
    )
    monkeypatch.setattr(newspaper_module.http.client, "HTTPResponse", Response)

    response = newspaper_module._PinnedResponse.open(
        approved,
        headers={"User-Agent": "test"},
        deadline=20.0,
        clock=lambda: 0.0,
        timeout=20.0,
    )
    response.close()

    assert evidence["connect"] == ("93.184.216.34", 443)
    assert evidence["sni"] == "www.bbc.com"
    assert b"Host: www.bbc.com\r\n" in evidence["sent"]


def test_newspaper_pinned_transport_header_crossing_deadline_fails(monkeypatch):
    approved = ApprovedTarget(
        "https://www.bbc.com/news",
        "https",
        "www.bbc.com",
        443,
        ("93.184.216.34",),
    )
    clock = {"value": 0.0}

    class Socket:
        def settimeout(self, _value):
            return None

        def sendall(self, _payload):
            return None

        def close(self):
            return None

    raw = Socket()

    class Context:
        def wrap_socket(self, source, *, server_hostname):
            assert server_hostname == "www.bbc.com"
            return source

    class Response:
        status = 200
        headers = {}

        def __init__(self, _source):
            return None

        def begin(self):
            clock["value"] = 21.0

        def close(self):
            return None

    monkeypatch.setattr(
        newspaper_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: raw,
    )
    monkeypatch.setattr(
        newspaper_module.ssl,
        "create_default_context",
        lambda: Context(),
    )
    monkeypatch.setattr(newspaper_module.http.client, "HTTPResponse", Response)

    with pytest.raises(RuntimeError, match="deadline"):
        newspaper_module._PinnedResponse.open(
            approved,
            headers={},
            deadline=20.0,
            clock=lambda: clock["value"],
            timeout=20.0,
        )


def test_newspaper_expired_receipt_is_negative(monkeypatch):
    plugin = make_plugin("expired-receipt")
    settings = bound_settings(mediaSources="Paper A|newspaper|chi_cd")
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    plugin.generate_image(settings, DeviceConfig())
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request(),
        resolved_theme_context=None,
    )
    before = plugin._presentation_state_path().read_bytes()
    expired = PresentationCommitReceipt(
        request_id="1" * 32,
        committed_at="2026-07-12T18:01:00+00:00",
        display_commit_id="late-display",
        structural_generation=1,
        settings_revision=1,
        theme_mode=None,
    )

    plugin.reconcile_presentation_receipt(settings, expired)

    assert plugin._presentation_state_path().read_bytes() == before


def test_newspaper_failed_media_write_restores_quarantined_victim(monkeypatch):
    plugin = make_plugin("media-write-rollback")
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a\nPaper B|newspaper|paper_b")
    sources = plugin._sources_for_settings(settings)
    bank = plugin._presentation_bank(settings, sources, (800, 480))
    document, profile = bank.load_for_data()
    first = bank.transaction()
    bank.ingest(
        profile,
        sources[0],
        Image.new("RGB", (800, 480), "red"),
        transaction=first,
    )
    bank.save(document, transaction=first)
    profile["records"] = []
    bank.save(document)
    before_profile = json.loads(json.dumps(profile))
    before_tree = tree_snapshot(plugin.get_plugin_dir())
    monkeypatch.setattr(newspaper_bank, "MEDIA_MAX_FILES", 1)
    monkeypatch.setattr(
        bank,
        "_write_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )
    second = bank.transaction()

    with pytest.raises(OSError, match="write failed"):
        try:
            bank.ingest(
                profile,
                sources[1],
                Image.new("RGB", (800, 480), "blue"),
                transaction=second,
            )
        except Exception:
            second.rollback()
            raise

    assert profile == before_profile
    assert tree_snapshot(plugin.get_plugin_dir()) == before_tree


def test_newspaper_protected_media_survives_cleanup(monkeypatch):
    plugin = make_plugin("protected-cleanup")
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    sources = plugin._sources_for_settings(settings)
    bank = plugin._presentation_bank(settings, sources, (800, 480))
    document, profile = bank.load_for_data()
    transaction = bank.transaction()
    record = bank.ingest(
        profile,
        sources[0],
        Image.new("RGB", (800, 480), "red"),
        transaction=transaction,
        downloaded_at=(datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
    )
    profile["current_selection"] = {"record_key": record["record_key"], "request_id": None}
    bank.save(document, transaction=transaction)
    media_path = bank.media_dir / f"{record['media_key']}.png"
    cleanup = bank.transaction()

    bank.cleanup(document, profile, transaction=cleanup)
    bank.save(document, transaction=cleanup)

    assert media_path.is_file()
    assert profile["current_selection"]["record_key"] == record["record_key"]


def test_newspaper_admission_counts_unexpected_regular_files(monkeypatch):
    plugin = make_plugin("ordinary-file-budget")
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    sources = plugin._sources_for_settings(settings)
    bank = plugin._presentation_bank(settings, sources, (800, 480))
    document, profile = bank.load_for_data()
    bank.media_dir.mkdir(parents=True)
    (bank.media_dir / "unexpected.bin").write_bytes(b"counts")
    monkeypatch.setattr(newspaper_bank, "MEDIA_MAX_FILES", 1)
    transaction = bank.transaction()

    bank.ingest(
        profile,
        sources[0],
        Image.new("RGB", (800, 480), "green"),
        transaction=transaction,
    )
    bank.save(document, transaction=transaction)

    assert not (bank.media_dir / "unexpected.bin").exists()
    assert len(list(bank.media_dir.iterdir())) == 1


def test_newspaper_refills_six_stale_records_using_fresh_ready_count(monkeypatch):
    plugin = make_plugin("stale-ready-refill")
    base = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    settings = bound_settings(mediaSources="\n".join(f"Paper {index}|newspaper|paper_{index}" for index in range(6)))
    monkeypatch.setattr(plugin, "_now_utc", lambda: base)
    sources = plugin._sources_for_settings(settings)
    bank = plugin._presentation_bank(settings, sources, (800, 480))
    document, profile = bank.load_for_data()
    transaction = bank.transaction()
    records = [
        bank.ingest(
            profile,
            source,
            Image.new("RGB", (800, 480), (index * 30, 20, 40)),
            transaction=transaction,
            downloaded_at=base.isoformat(),
        )
        for index, source in enumerate(sources)
    ]
    profile["current_selection"] = {
        "record_key": records[0]["record_key"],
        "request_id": None,
    }
    profile["refill_cursor"] = 0
    bank.save(document, transaction=transaction)

    calls = []
    monkeypatch.setattr(plugin, "_now_utc", lambda: base + timedelta(hours=49))

    def refresh(source, *_args, **_kwargs):
        calls.append(source["id"])
        return Image.new("RGB", (800, 480), (20, len(calls) * 30, 60))

    monkeypatch.setattr(plugin, "_fetch_source_image", refresh)
    plugin.generate_image(settings, DeviceConfig())

    assert calls == [
        "newspaper:PAPER_0",
        "newspaper:PAPER_1",
        "newspaper:PAPER_2",
    ]


def test_newspaper_prepare_keeps_current_display_when_bank_has_no_fresh_media(monkeypatch):
    plugin = make_plugin("stale-prepare")
    base = datetime(2026, 7, 10, 12, tzinfo=timezone.utc)
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    monkeypatch.setattr(plugin, "_now_utc", lambda: base)
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    plugin.generate_image(settings, DeviceConfig())
    monkeypatch.setattr(plugin, "_now_utc", lambda: base + timedelta(hours=49))
    before = plugin._presentation_state_path().read_bytes()

    prepared = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request(),
        resolved_theme_context=None,
    )

    assert prepared.changed is False
    assert prepared.image is None
    assert plugin._presentation_state_path().read_bytes() == before


@pytest.mark.parametrize("failure_mode", ["missing", "expired"])
def test_newspaper_protected_current_recovery_failure_is_byte_stable(monkeypatch, failure_mode):
    plugin = make_plugin(f"protected-current-{failure_mode}")
    base = datetime(2026, 7, 12, 14, tzinfo=timezone.utc)
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    monkeypatch.setattr(plugin, "_now_utc", lambda: base)
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "red"),
    )
    plugin.generate_image(settings, DeviceConfig())
    state = newspaper_bank.read_state(plugin._presentation_state_path())
    fingerprint = state["instance_profiles"]["newspaper-instance"]
    profile = state["profiles"][fingerprint]
    current_key = profile["current_selection"]["record_key"]
    current_record = next(record for record in profile["records"] if record["record_key"] == current_key)
    if failure_mode == "missing":
        (plugin._presentation_media_dir() / f"{current_record['media_key']}.png").unlink()
    else:
        current_record["downloaded_at"] = (base - timedelta(days=15)).isoformat()
        newspaper_bank.write_state(plugin._presentation_state_path(), state)
    before_state = plugin._presentation_state_path().read_bytes()
    before_tree = tree_snapshot(plugin.get_plugin_dir())
    before_history = list(profile["seen_ids"])
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )

    with pytest.raises(RuntimeError, match="protected"):
        plugin.generate_image(settings, DeviceConfig())

    assert plugin._presentation_state_path().read_bytes() == before_state
    assert tree_snapshot(plugin.get_plugin_dir()) == before_tree
    after = newspaper_bank.read_state(plugin._presentation_state_path())
    after_profile = after["profiles"][fingerprint]
    assert after_profile["current_selection"]["record_key"] == current_key
    assert after_profile["seen_ids"] == before_history


def test_newspaper_recovers_pending_exact_source_and_receipt_commits(monkeypatch):
    plugin = make_plugin("recover-pending")
    base = datetime(2026, 7, 12, 14, tzinfo=timezone.utc)
    settings = bound_settings(mediaSources=("Paper A|newspaper|paper_a\nPaper B|newspaper|paper_b"))
    monkeypatch.setattr(plugin, "_now_utc", lambda: base)
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda source, *_args, **_kwargs: Image.new(
            "RGB", (800, 480), "red" if source["value"] == "PAPER_A" else "blue"
        ),
    )
    plugin.generate_image(settings, DeviceConfig())
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request(),
        resolved_theme_context=None,
    )
    state = newspaper_bank.read_state(plugin._presentation_state_path())
    fingerprint = state["instance_profiles"]["newspaper-instance"]
    profile = state["profiles"][fingerprint]
    old_pending_key = profile["pending_selection"]["record_key"]
    pending_record = next(record for record in profile["records"] if record["record_key"] == old_pending_key)
    expected_source = dict(pending_record["source"])
    (plugin._presentation_media_dir() / f"{pending_record['media_key']}.png").unlink()
    calls = []

    def recover(source, *_args, **_kwargs):
        calls.append(dict(source))
        return Image.new("RGB", (800, 480), "green")

    monkeypatch.setattr(plugin, "_fetch_source_image", recover)
    plugin.generate_image(settings, DeviceConfig())
    recovered = newspaper_bank.read_state(plugin._presentation_state_path())
    recovered_profile = recovered["profiles"][fingerprint]
    new_pending_key = recovered_profile["pending_selection"]["record_key"]

    assert calls[0] == expected_source
    assert new_pending_key == old_pending_key
    plugin.reconcile_presentation_receipt(settings, receipt())
    committed = newspaper_bank.read_state(plugin._presentation_state_path())
    committed_profile = committed["profiles"][fingerprint]
    assert committed_profile["pending_selection"] is None
    assert committed_profile["current_selection"]["record_key"] == new_pending_key


def test_newspaper_recovers_expired_current_with_identical_media(monkeypatch):
    plugin = make_plugin("recover-identical-current")
    base = datetime(2026, 7, 12, 14, tzinfo=timezone.utc)
    settings = bound_settings(mediaSources="Paper A|newspaper|paper_a")
    monkeypatch.setattr(plugin, "_now_utc", lambda: base)
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "red"),
    )
    plugin.generate_image(settings, DeviceConfig())
    state = newspaper_bank.read_state(plugin._presentation_state_path())
    fingerprint = state["instance_profiles"]["newspaper-instance"]
    profile = state["profiles"][fingerprint]
    current_key = profile["current_selection"]["record_key"]
    current = next(record for record in profile["records"] if record["record_key"] == current_key)
    current["downloaded_at"] = (base - timedelta(days=15)).isoformat()
    newspaper_bank.write_state(plugin._presentation_state_path(), state)

    image = plugin.generate_image(settings, DeviceConfig())

    recovered = newspaper_bank.read_state(plugin._presentation_state_path())
    recovered_profile = recovered["profiles"][fingerprint]
    matching = [record for record in recovered_profile["records"] if record["record_key"] == current_key]
    assert image.info["inkypi_source_provenance"] == "live"
    assert len(matching) == 1
    assert matching[0]["downloaded_at"] == base.isoformat()


def test_newspaper_protected_browser_recovery_shares_data_quota_and_resumes(
    monkeypatch,
):
    plugin = make_plugin("protected-browser-shared-quota")
    base = datetime(2026, 7, 12, 14, tzinfo=timezone.utc)
    settings = bound_settings(
        mediaSources=(
            "Browser Current|url|https://www.bbc.com/news\n"
            "Browser Pending|url|https://www.cnn.com\n"
            "Paper 0|newspaper|paper_0\n"
            "Paper 1|newspaper|paper_1\n"
            "Paper 2|newspaper|paper_2\n"
            "Paper 3|newspaper|paper_3"
        )
    )
    monkeypatch.setattr(plugin, "_now_utc", lambda: base)
    sources = plugin._sources_for_settings(settings)
    bank = plugin._presentation_bank(settings, sources, (800, 480))
    document, profile = bank.load_for_data()
    transaction = bank.transaction()
    current = bank.ingest(
        profile,
        sources[0],
        Image.new("RGB", (800, 480), "red"),
        transaction=transaction,
        downloaded_at=base.isoformat(),
    )
    pending = bank.ingest(
        profile,
        sources[1],
        Image.new("RGB", (800, 480), "blue"),
        transaction=transaction,
        downloaded_at=base.isoformat(),
    )
    pending_request = request()
    profile["current_selection"] = {
        "record_key": current["record_key"],
        "request_id": None,
    }
    profile["pending_selection"] = {
        "request_id": pending_request.request_id,
        "origin_display_commit_id": pending_request.origin_display_commit_id,
        "requested_at": pending_request.requested_at,
        "record_key": pending["record_key"],
        "reset_seen": False,
        "profile_fingerprint": bank.fingerprint,
        "instance_uuid": "newspaper-instance",
    }
    profile["refill_cursor"] = 0
    profile["refill_in_progress"] = True
    bank.save(document, transaction=transaction)
    current_path = bank.media_dir / f"{current['media_key']}.png"
    pending_path = bank.media_dir / f"{pending['media_key']}.png"
    current_path.unlink()
    pending_path.unlink()
    before_state = plugin._presentation_state_path().read_bytes()
    before_profile = json.loads(json.dumps(profile))
    calls = []

    def recover_or_refill(source, *_args, **_kwargs):
        calls.append(source["id"])
        if source["id"] == sources[0]["id"]:
            return Image.new("RGB", (800, 480), "red")
        if source["id"] == sources[1]["id"]:
            return Image.new("RGB", (800, 480), "blue")
        return Image.new("RGB", (800, 480), (20, len(calls) * 20, 60))

    monkeypatch.setattr(plugin, "_fetch_source_image", recover_or_refill)

    with pytest.raises(RuntimeError, match="protected.*quota"):
        plugin.generate_image(settings, DeviceConfig())

    assert calls == [sources[0]["id"]]
    assert plugin._presentation_state_path().read_bytes() == before_state
    unchanged = newspaper_bank.read_state(plugin._presentation_state_path())
    unchanged_profile = unchanged["profiles"][bank.fingerprint]
    assert unchanged_profile == before_profile
    assert current_path.is_file()
    assert not pending_path.exists()

    calls.clear()
    plugin.generate_image(settings, DeviceConfig())

    assert calls == [
        sources[1]["id"],
        sources[2]["id"],
        sources[3]["id"],
        sources[4]["id"],
    ]
    assert sum(call.startswith("url:") for call in calls) == 1
    assert len(calls) == 4
    plugin.reconcile_presentation_receipt(settings, receipt())
    committed = newspaper_bank.read_state(plugin._presentation_state_path())
    committed_profile = committed["profiles"][bank.fingerprint]
    assert committed_profile["pending_selection"] is None
    assert committed_profile["current_selection"]["record_key"] == pending["record_key"]


def test_newspaper_http_quota_preserves_first_unattempted_cursor(monkeypatch):
    plugin = make_plugin("http-quota-cursor")
    settings = bound_settings(mediaSources="\n".join(f"Paper {index}|newspaper|paper_{index}" for index in range(6)))
    calls = []
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda source, *_args, **_kwargs: calls.append(source["id"]),
    )

    plugin.generate_image(settings, DeviceConfig())
    plugin.generate_image(settings, DeviceConfig())

    assert calls == [f"newspaper:PAPER_{index}" for index in range(6)]


def test_newspaper_browser_quota_preserves_first_unattempted_cursor(monkeypatch):
    plugin = make_plugin("browser-quota-cursor")
    settings = bound_settings(
        mediaSources=("Browser A|url|https://www.bbc.com/news\nBrowser B|url|https://www.cnn.com")
    )
    calls = []
    monkeypatch.setattr(
        plugin,
        "_fetch_source_image",
        lambda source, *_args, **_kwargs: calls.append(source["id"]),
    )

    plugin.generate_image(settings, DeviceConfig())
    plugin.generate_image(settings, DeviceConfig())

    assert calls == [
        "url:https://www.bbc.com/news",
        "url:https://www.cnn.com",
    ]


def test_newspaper_cleanup_keeps_media_referenced_by_another_profile(monkeypatch):
    plugin = make_plugin("shared-media-reference")
    now = datetime(2026, 7, 12, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    first_settings = bound_settings("instance-a", mediaSources="Paper A|newspaper|paper_a")
    second_settings = bound_settings("instance-b", mediaSources="Paper A|newspaper|paper_a")
    sources = plugin._sources_for_settings(first_settings)
    first_bank = plugin._presentation_bank(first_settings, sources, (800, 480))
    first_document, first_profile = first_bank.load_for_data()
    first_tx = first_bank.transaction()
    first_record = first_bank.ingest(
        first_profile,
        sources[0],
        Image.new("RGB", (800, 480), "red"),
        transaction=first_tx,
        downloaded_at=(now - timedelta(days=15)).isoformat(),
    )
    first_bank.save(first_document, transaction=first_tx)

    second_bank = plugin._presentation_bank(second_settings, sources, (800, 480))
    second_document, second_profile = second_bank.load_for_data()
    second_tx = second_bank.transaction()
    second_record = second_bank.ingest(
        second_profile,
        sources[0],
        Image.new("RGB", (800, 480), "red"),
        transaction=second_tx,
        downloaded_at=now.isoformat(),
    )
    second_bank.save(second_document, transaction=second_tx)
    assert first_record["media_key"] == second_record["media_key"]

    first_document, first_profile = first_bank.load_for_data()
    cleanup = first_bank.transaction()
    first_bank.cleanup(first_document, first_profile, transaction=cleanup)
    first_bank.save(first_document, transaction=cleanup)

    shared_path = first_bank.media_dir / f"{second_record['media_key']}.png"
    assert shared_path.is_file()
    second_document, second_profile = second_bank.load_warm()
    del second_document
    assert second_bank.load_media(second_profile["records"][0]).size == (800, 480)


def test_newspaper_dom_sanitizer_removes_all_active_navigation_vectors():
    plugin = make_plugin("strict-dom-sanitizer")
    sanitized = plugin._sanitize_browser_html(
        """
        <meta HTTP-EQUIV=refresh content='0;url=https://evil.example/meta'>
        <style>@import 'https://evil.example/style'; body{background:url(data:x)}</style>
        <svg><a href=https://evil.example/foreign>foreign</a></svg>
        <svg><br></br><p>foreign-malformed</p></svg>
        <form action=https://evil.example/form>
          <button formaction='javascript:alert(1)'>go</button>
        </form>
        <img src=https://evil.example/a srcset='https://evil.example/b 2x'
             onerror='location="https://evil.example/event"' style='background:url(file:///x)'>
        <a href=data:text/html,x download='x'>download</a>
        <math/>
        <h1>Front page</h1>
        """,
        "https://www.bbc.com/news",
    ).lower()

    for forbidden in (
        "http-equiv=refresh",
        "evil.example",
        "javascript:",
        "data:",
        "file:",
        "foreign-malformed",
        "<svg",
        "<style",
        " src=",
        " srcset=",
        " href=",
        " action=",
        " formaction=",
        " download=",
        " onerror=",
    ):
        assert forbidden not in sanitized
    assert "front page" in sanitized


def test_newspaper_browser_refuses_unsafe_html_before_chromium(monkeypatch):
    plugin = make_plugin("browser-preflight-guard")
    malicious = b"<img src=https://evil.example/a><h1>News</h1>"
    monkeypatch.setattr(plugin, "_allowed_hosts_for_url", lambda _url: ("www.bbc.com",))
    monkeypatch.setattr(
        plugin,
        "_download_provider_bytes",
        lambda *_args, **_kwargs: (
            malicious,
            "https://www.bbc.com/news",
            {"content-type": "text/html; charset=utf-8"},
        ),
    )
    monkeypatch.setattr(
        plugin,
        "_sanitize_browser_html",
        lambda html_text, _url: html_text,
    )

    class Renderer:
        def render_html(self, *_args, **_kwargs):
            pytest.fail("unsafe HTML reached Chromium")

    monkeypatch.setattr(newspaper_module, "get_browser_renderer", lambda: Renderer())

    with pytest.raises(RuntimeError, match="unsafe browser HTML"):
        plugin._fetch_url_screenshot(
            "https://www.bbc.com/news",
            DeviceConfig(),
            deadline=plugin._monotonic() + 10,
        )


def test_newspaper_deadline_after_quarantine_delete_rolls_back_everything(monkeypatch):
    plugin = make_plugin("post-commit-deadline")
    settings = bound_settings(mediaSources=("Paper A|newspaper|paper_a\nPaper B|newspaper|paper_b"))
    sources = plugin._sources_for_settings(settings)
    bank = plugin._presentation_bank(settings, sources, (800, 480))
    document, profile = bank.load_for_data()
    first = bank.transaction()
    bank.ingest(
        profile,
        sources[0],
        Image.new("RGB", (800, 480), "red"),
        transaction=first,
    )
    bank.save(document, transaction=first)
    profile["records"] = []
    bank.save(document)
    before_document = json.loads(json.dumps(document))
    before_profile = json.loads(json.dumps(profile))
    before_tree = tree_snapshot(plugin.get_plugin_dir())
    monkeypatch.setattr(newspaper_bank, "MEDIA_MAX_FILES", 1)
    second = bank.transaction()
    bank.ingest(
        profile,
        sources[1],
        Image.new("RGB", (800, 480), "blue"),
        transaction=second,
    )
    checks = {"count": 0}
    deleted = {"value": False}
    original_unlink = bank._safe_unlink

    def track_quarantine_delete(path):
        original_unlink(path)
        if str(path).endswith(".quarantine"):
            deleted["value"] = True

    monkeypatch.setattr(bank, "_safe_unlink", track_quarantine_delete)

    def cross_after_delete():
        checks["count"] += 1
        if checks["count"] == 5:
            raise RuntimeError("DATA deadline crossed after quarantine delete")

    with pytest.raises(RuntimeError, match="deadline"):
        bank.save(
            document,
            deadline_check=cross_after_delete,
            transaction=second,
        )

    assert document == before_document
    assert profile == before_profile
    assert deleted["value"] is True
    assert tree_snapshot(plugin.get_plugin_dir()) == before_tree
    assert not list(plugin._presentation_media_dir().glob("*.quarantine"))
