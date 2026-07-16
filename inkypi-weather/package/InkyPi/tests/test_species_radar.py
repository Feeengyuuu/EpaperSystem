import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageChops, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.species_radar.species_radar as species_mod  # noqa: E402
from plugins.base_plugin.presentation import (  # noqa: E402
    PresentationMode,
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)
from runtime.runtime_state import PresentationCommitReceipt  # noqa: E402
from plugins.species_radar.species_radar import (  # noqa: E402
    CATEGORY_STYLES,
    COMIC_BLUE,
    COMIC_CYAN,
    COMIC_GREEN,
    COMIC_INK,
    COMIC_NIGHT_INK,
    COMIC_NIGHT_PAPER,
    COMIC_ORANGE,
    COMIC_PANEL_BLUE,
    COMIC_PANEL_GREEN,
    COMIC_PAPER,
    DEFAULT_CJK_FONT,
    DEFAULT_FONT,
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    DEFAULT_REFRESH_HOURS,
    MICROSOFT_YAHEI_FONT,
    SpeciesRadar,
    HEADER_PIXEL_BACKGROUND_DISPLAY_SIZE,
    HEADER_PIXEL_BACKGROUND_IMAGE,
    PIXEL_PLACEHOLDER_IMAGE,
    TITLE_WORDMARK_DISPLAY_SIZE,
    TITLE_WORDMARK_EMPTY_DISPLAY_SIZE,
    TITLE_WORDMARK_IMAGE,
)


class DummyPluginInstance:
    def __init__(self, plugin_id, settings):
        self.plugin_id = plugin_id
        self.settings = settings


class DummyPlaylist:
    def __init__(self, plugins):
        self.plugins = plugins


class DummyPlaylistManager:
    def __init__(self, playlists):
        self.playlists = playlists


class DummyDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", playlists=None, env=None):
        self.resolution = resolution
        self.orientation = orientation
        self.playlist_manager = DummyPlaylistManager(playlists or [])
        self.env = env or {}

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "timezone": "America/Los_Angeles"}
        if key is None:
            return values
        return values.get(key, default)

    def get_playlist_manager(self):
        return self.playlist_manager

    def load_env_key(self, key):
        return self.env.get(key, "")


def make_plugin(tmp_path):
    plugin = SpeciesRadar({"id": "species_radar"})
    plugin._cache_dir = lambda: tmp_path
    return plugin


def bound_species_settings(instance_uuid="species-instance", **overrides):
    return bind_presentation_instance_identity(
        {
            "locationSource": "manual",
            "latitude": "37.5485",
            "longitude": "-121.9886",
            "locationName": "Fremont, CA",
            "radiusKm": "25",
            "lookbackDays": "365",
            "limit": "50",
            "refreshHours": "6",
            "showObservationMap": "true",
            **overrides,
        },
        instance_uuid,
    )


def species_request(request_id, *, origin="origin-display"):
    return PresentationRequestContext(
        request_id=request_id,
        requested_at="2026-07-12T10:00:00+00:00",
        origin_display_commit_id=origin,
        last_receipt=None,
    )


def species_receipt(
    request_id,
    *,
    display="prepared-display",
    committed_at="2026-07-12T10:01:00+00:00",
):
    return PresentationCommitReceipt(
        request_id=request_id,
        committed_at=committed_at,
        display_commit_id=display,
        structural_generation=1,
        settings_revision=1,
        theme_mode="day",
    )


def species_theme(mode="day"):
    background = (15, 26, 14) if mode == "night" else (238, 245, 234)
    ink = (245, 245, 230) if mode == "night" else (18, 26, 18)
    return {
        "mode": mode,
        "palette": {
            "background": background,
            "panel": (28, 40, 27) if mode == "night" else (248, 251, 245),
            "ink": ink,
            "muted": (170, 185, 166) if mode == "night" else (91, 105, 88),
            "rule": (89, 105, 86) if mode == "night" else (184, 197, 180),
            "accent": (126, 214, 122) if mode == "night" else (61, 124, 58),
        },
    }


def bank_observation(index, *, location_name="Fremont, CA", bucket="2026-07-12T06:00:00+00:00"):
    return {
        "gbif_key": str(index),
        "taxon_key": str(3000 + index),
        "species_key": str(3000 + index),
        "scientific_name": f"Species example {index}",
        "species": f"Species example {index}",
        "display_name": f"Species {index}",
        "common_name_zh": f"物种{index}",
        "common_name_en": f"Species {index}",
        "category_label": "鸟类",
        "taxonomy_path": "动物界 / 鸟纲",
        "event_date": "2026-07-12",
        "latitude": 37.5 + index / 1000,
        "longitude": -121.9,
        "distance_km": float(index),
        "location": location_name,
        "radar_location_name": location_name,
        "radar_location_label": location_name,
        "radar_location_id": "primary",
        "image_url": f"https://inaturalist-open-data.s3.amazonaws.com/photos/{index}/medium.jpg",
        "photo_creator": "Observer",
        "photo_license": "CC-BY",
        "source_bucket": bucket,
    }


def _canonical_theme(mode, *, background, panel, ink, muted, rule, accent):
    palette = {
        "background": background,
        "panel": panel,
        "ink": ink,
        "muted": muted,
        "rule": rule,
        "accent": accent,
    }
    return {"mode": mode, "palette": palette, "css": {}}


def occurrence(**overrides):
    data = {
        "key": 123,
        "taxonKey": 3045818,
        "speciesKey": 3045818,
        "vernacularName": "milkmaids",
        "scientificName": "Cardamine californica (Nutt.) Greene",
        "species": "Cardamine californica",
        "kingdom": "Plantae",
        "class": "Magnoliopsida",
        "order": "Capparales",
        "family": "Brassicaceae",
        "genus": "Cardamine",
        "eventDate": "2026-01-01T14:33",
        "decimalLatitude": 37.55,
        "decimalLongitude": -121.99,
        "coordinateUncertaintyInMeters": 80,
        "gadm": {"level2": {"name": "Alameda"}},
        "stateProvince": "California",
        "datasetName": "iNaturalist research-grade observations",
        "recordedBy": "Observer",
        "identifiedBy": "Identifier",
        "references": "https://www.inaturalist.org/observations/123",
        "license": "http://creativecommons.org/licenses/by-nc/4.0/legalcode",
        "iucnRedListCategory": "LC",
        "media": [
            {
                "type": "StillImage",
                "format": "image/jpeg",
                "identifier": "https://example.com/photo.jpg",
                "creator": "Photo Creator",
                "rightsHolder": "Photo Rights",
                "license": "http://creativecommons.org/licenses/by-nc/4.0/",
                "references": "https://www.inaturalist.org/photos/123",
            }
        ],
    }
    data.update(overrides)
    return data


def test_category_labels_are_beginner_friendly_and_data_driven(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._category_for({"kingdom": "Plantae", "class": "Magnoliopsida"})["label"] == "植物"
    assert plugin._category_for({"kingdom": "Fungi", "class": "Agaricomycetes"})["label"] == "真菌"
    assert plugin._category_for({"kingdom": "Animalia", "class": "Aves"})["label"] == "鸟类"
    assert plugin._category_for({"kingdom": "Animalia", "class": "Insecta"})["label"] == "昆虫"
    assert plugin._category_for({"kingdom": "Animalia", "class": "Mammalia"})["label"] == "哺乳动物"
    assert plugin._category_for({"kingdom": "Animalia", "class": "Amphibia"})["label"] == "两栖动物"
    assert plugin._category_for({"kingdom": "Animalia", "class": "Gastropoda"})["label"] == "蜗牛/贝类"
    assert plugin._category_for({"kingdom": "Chromista", "class": "Phaeophyceae"})["label"] == "其他生物"


def test_occurrence_parser_extracts_taxonomy_media_license_and_distance(tmp_path):
    plugin = make_plugin(tmp_path)

    obs = plugin._observation_from_occurrence(
        occurrence(),
        {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"},
    )

    assert obs["gbif_key"] == "123"
    assert obs["category_label"] == "植物"
    assert obs["common_name"] == "milkmaids"
    assert obs["display_name"] == "milkmaids"
    assert obs["scientific_name"].startswith("Cardamine californica")
    assert obs["taxonomy_path"] == "植物 / Brassicaceae / Cardamine"
    assert obs["location"] == "Alameda, California"
    assert obs["dataset_name"] == "iNaturalist research-grade observations"
    assert obs["image_url"] == "https://example.com/photo.jpg"
    assert obs["photo_creator"] == "Photo Creator"
    assert obs["rights_holder"] == "Photo Rights"
    assert "by-nc/4.0" in obs["photo_license"]
    assert 0 <= obs["distance_km"] < 1


def test_common_names_keep_chinese_and_english_for_learning(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    obs = plugin._observation_from_occurrence(
        occurrence(vernacularName="milkmaids"),
        {"latitude": 37.55, "longitude": -121.99},
    )

    monkeypatch.setattr(plugin, "_fetch_vernacular_name_candidates", lambda _taxon_key: {"zh": "", "en": "milkmaids", "any": "milkmaids"})
    monkeypatch.setattr(plugin, "_fetch_wikidata_chinese_name", lambda _scientific_name: "加州碎米荠")
    plugin._enrich_common_names([obs])

    assert obs["common_name_zh"] == "加州碎米荠"
    assert obs["common_name_en"] == "milkmaids"
    assert obs["common_name"] == "加州碎米荠"
    assert obs["display_name"] == "加州碎米荠"
    assert plugin._name_lines(obs)[:2] == ("加州碎米荠", "milkmaids")
    assert plugin._compact_bilingual_name(obs) == "加州碎米荠 / milkmaids"


def test_traditional_chinese_common_names_are_simplified_for_display(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    obs = plugin._observation_from_occurrence(
        occurrence(
            vernacularName="Western Bluebird",
            scientificName="Sialia mexicana Swainson, 1832",
            species="Sialia mexicana",
            kingdom="Animalia",
            **{"class": "Aves"},
        ),
        {"latitude": 37.55, "longitude": -121.99},
    )

    monkeypatch.setattr(plugin, "_fetch_vernacular_name_candidates", lambda _taxon_key: {"zh": "西方藍鶇", "en": "Western Bluebird", "any": "西方藍鶇"})
    monkeypatch.setattr(plugin, "_fetch_wikidata_chinese_name", lambda _scientific_name: "")
    plugin._enrich_common_names([obs])

    assert obs["common_name_zh"] == "西方蓝鸫"
    assert obs["common_name_en"] == "Western Bluebird"
    assert obs["common_name"] == "西方蓝鸫"
    assert obs["display_name"] == "西方蓝鸫"
    assert plugin._name_lines(obs)[:2] == ("西方蓝鸫", "Western Bluebird")
    assert plugin._compact_bilingual_name(obs) == "西方蓝鸫 / Western Bluebird"


def test_occurrence_chinese_name_is_simplified_before_enrichment(tmp_path):
    plugin = make_plugin(tmp_path)
    obs = plugin._observation_from_occurrence(
        occurrence(vernacularName="西方藍鶇", scientificName="Sialia mexicana", species="Sialia mexicana", kingdom="Animalia", **{"class": "Aves"}),
        {"latitude": 37.55, "longitude": -121.99},
    )

    assert obs["common_name_zh"] == "西方蓝鸫"
    assert obs["common_name_en"] == ""
    assert obs["display_name"] == "西方蓝鸫"

def test_missing_common_name_uses_scientific_name(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    obs = plugin._observation_from_occurrence(occurrence(vernacularName=""), {"latitude": 37.55, "longitude": -121.99})

    monkeypatch.setattr(plugin, "_fetch_vernacular_name_candidates", lambda _taxon_key: {"zh": "", "en": "", "any": ""})
    monkeypatch.setattr(plugin, "_fetch_wikidata_chinese_name", lambda _scientific_name: "")
    plugin._enrich_common_names([obs])

    assert obs["common_name"] == ""
    assert obs["common_name_zh"] == ""
    assert obs["common_name_en"] == ""
    assert obs["display_name"].startswith("Cardamine californica")


def test_select_vernacular_names_prefers_simplified_chinese_then_english(tmp_path):
    plugin = make_plugin(tmp_path)
    names = plugin._select_vernacular_names([
        {"vernacularName": "California toothwort", "language": "eng"},
        {"vernacularName": "加州碎米薺", "language": "zh-Hant"},
        {"vernacularName": "加州碎米荠", "language": "zh-Hans"},
        {"vernacularName": "Cardamine", "language": "lat"},
    ])

    assert names == {"zh": "加州碎米荠", "en": "California toothwort", "any": "California toothwort"}
    assert plugin._select_vernacular_name([
        {"vernacularName": "California toothwort", "language": "eng"},
        {"vernacularName": "加州碎米荠", "language": "zh-Hans"},
    ]) == "加州碎米荠"

    traditional_only = plugin._select_vernacular_names([
        {"vernacularName": "西方藍鶇", "language": "zh-Hant"},
        {"vernacularName": "Western Bluebird", "language": "eng"},
    ])
    assert traditional_only == {"zh": "西方蓝鸫", "en": "Western Bluebird", "any": "西方蓝鸫"}


def test_wikidata_chinese_label_prefers_simplified_when_available(tmp_path):
    plugin = make_plugin(tmp_path)
    data = {
        "results": {
            "bindings": [
                {"label": {"value": "西方藍鶇", "xml:lang": "zh-hant"}},
                {"label": {"value": "西方蓝鸲", "xml:lang": "zh-hans"}},
            ]
        }
    }

    assert plugin._select_wikidata_chinese_label(data) == "西方蓝鸲"


def test_wikidata_traditional_chinese_label_is_simplified(tmp_path):
    plugin = make_plugin(tmp_path)
    data = {"results": {"bindings": [{"label": {"value": "西方藍鶇", "xml:lang": "zh-hant"}}]}}

    assert plugin._select_wikidata_chinese_label(data) == "西方蓝鸫"


def test_location_prefers_manual_then_weather_plugin_then_fremont_default(tmp_path):
    plugin = make_plugin(tmp_path)
    weather = DummyPluginInstance("weather", {"latitude": "37.6", "longitude": "-122.0", "customTitle": "Fremont Weather"})
    device = DummyDeviceConfig(playlists=[DummyPlaylist([weather])])

    manual = plugin._resolve_location({"latitude": "38.0", "longitude": "-121.5", "locationName": "Manual"}, device)
    inherited = plugin._resolve_location({}, device)
    fallback = plugin._resolve_location({}, DummyDeviceConfig())

    assert manual["source"] == "settings"
    assert manual["name"] == "Manual"
    assert manual["latitude"] == 38.0
    assert inherited["source"] == "weather"
    assert inherited["name"] == "Fremont Weather"
    assert inherited["longitude"] == -122.0
    assert fallback["source"] == "default"
    assert fallback["latitude"] == DEFAULT_LATITUDE
    assert fallback["longitude"] == DEFAULT_LONGITUDE

def test_location_specs_dedupes_named_fremont_and_keeps_luoyang_two_years(tmp_path):
    plugin = make_plugin(tmp_path)

    specs = plugin._location_specs(
        {},
        {"latitude": 37.61, "longitude": -122.04, "name": "Fremont Weather"},
    )

    labels = [spec["location"]["radar_location_label"] for spec in specs]
    assert labels == ["Fremont", "\u6d1b\u9633"]
    assert [spec["lookback_days"] for spec in specs] == [365, 730]

def test_fetch_live_payload_uses_gbif_media_and_date_filters(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    captured = {}

    def fake_get_json(url, params=None):
        captured["url"] = url
        captured["params"] = params
        return {"results": [occurrence()]}

    monkeypatch.setattr(plugin, "_get_json", fake_get_json)
    monkeypatch.setattr(plugin, "_fetch_vernacular_name_candidates", lambda _taxon_key: {"zh": "", "en": "", "any": ""})
    monkeypatch.setattr(plugin, "_fetch_wikidata_chinese_name", lambda _scientific_name: "")

    payload = plugin._fetch_live_payload(
        {"radiusKm": "25", "lookbackDays": "365", "includeLuoyang": "false"},
        datetime(2026, 6, 27, tzinfo=timezone.utc),
        {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"},
    )

    assert payload["observations"]
    assert captured["url"].endswith("/occurrence/search")
    assert captured["params"]["mediaType"] == "StillImage"
    assert captured["params"]["hasCoordinate"] == "true"
    assert captured["params"]["basisOfRecord"] == "HUMAN_OBSERVATION"
    assert captured["params"]["eventDate"] == "2025-06-27,2026-06-27"
    assert "decimalLatitude" in captured["params"]
    assert "decimalLongitude" in captured["params"]

def test_fetch_live_payload_adds_luoyang_two_year_pool_by_default(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    calls = []

    def fake_get_json(url, params=None):
        calls.append((url, dict(params or {})))
        is_luoyang = not str((params or {}).get("decimalLongitude") or "").startswith("-")
        if is_luoyang:
            return {
                "count": 7,
                "results": [
                    occurrence(
                        key=702,
                        taxonKey=702,
                        speciesKey=702,
                        vernacularName="",
                        scientificName="Tadorna ferruginea",
                        species="Tadorna ferruginea",
                        kingdom="Animalia",
                        **{"class": "Aves"},
                        decimalLatitude=34.62,
                        decimalLongitude=112.45,
                        stateProvince="Henan",
                    )
                ],
            }
        return {
            "count": 11,
            "results": [
                occurrence(
                    key=701,
                    taxonKey=701,
                    speciesKey=701,
                    vernacularName="Western Bluebird",
                    scientificName="Sialia mexicana",
                    species="Sialia mexicana",
                    kingdom="Animalia",
                    **{"class": "Aves"},
                    decimalLatitude=37.55,
                    decimalLongitude=-121.99,
                )
            ],
        }

    monkeypatch.setattr(plugin, "_get_json", fake_get_json)
    monkeypatch.setattr(plugin, "_fetch_vernacular_name_candidates", lambda _taxon_key: {"zh": "", "en": "", "any": ""})
    monkeypatch.setattr(plugin, "_fetch_wikidata_chinese_name", lambda _scientific_name: "")

    payload = plugin._fetch_live_payload(
        {"radiusKm": "25", "lookbackDays": "365", "limit": "5"},
        datetime(2026, 6, 27, tzinfo=timezone.utc),
        {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"},
    )

    assert len(calls) == 2
    assert calls[0][1]["eventDate"] == "2025-06-27,2026-06-27"
    assert calls[1][1]["eventDate"] == "2024-06-27,2026-06-27"
    assert payload["dual_location_mode"] is True
    assert "Fremont 1\u5e74" in payload["location_summary"]
    assert "\u6d1b\u9633 2\u5e74" in payload["location_summary"]
    assert payload["location_counts"] == {"Fremont": 1, "\u6d1b\u9633": 1}
    assert {item["radar_location_id"] for item in payload["observations"]} == {"primary", "luoyang"}
    luoyang_meta = next(item for item in payload["locations"] if item["id"] == "luoyang")
    assert luoyang_meta["lookback_days"] == 730
    assert luoyang_meta["event_date_range"] == "2024-06-27,2026-06-27"

def test_display_payload_rotates_daily_discovery_pool_without_repeating(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    records = [
        occurrence(key=201, taxonKey=201, speciesKey=201, vernacularName="Western Bluebird", scientificName="Sialia mexicana", species="Sialia mexicana", kingdom="Animalia", **{"class": "Aves"}, decimalLatitude=37.55, decimalLongitude=-121.99),
        occurrence(key=202, taxonKey=202, speciesKey=202, vernacularName="California Slender Salamander", scientificName="Batrachoseps attenuatus", species="Batrachoseps attenuatus", kingdom="Animalia", **{"class": "Amphibia"}, decimalLatitude=37.57, decimalLongitude=-122.01),
        occurrence(key=203, taxonKey=203, speciesKey=203, vernacularName="field mushroom", scientificName="Agaricus campestris", species="Agaricus campestris", kingdom="Fungi", **{"class": "Agaricomycetes"}, decimalLatitude=37.52, decimalLongitude=-121.97),
    ]
    observations = [plugin._observation_from_occurrence(record, location) for record in records]
    monkeypatch.setattr(plugin, "_ensure_display_common_name", lambda _observation: None)
    payload = {
        "schema": "species-radar-v1",
        "cache_key": "daily-pool-test",
        "observations": observations,
        "category_counts": plugin._category_counts(observations),
        "location": location,
    }

    monkeypatch.setattr(plugin, "_shuffled_display_indices", lambda values: list(reversed(range(values) if isinstance(values, int) else values)))

    views = [plugin._display_payload(payload, {}, now) for _ in range(3)]
    selected_keys = [view["observations"][0]["gbif_key"] for view in views]
    state = plugin._read_display_state()

    assert selected_keys == ["203", "202", "201"]
    assert payload["observations"][0]["gbif_key"] == observations[0]["gbif_key"]
    assert all(view["display_pool_size"] == 3 for view in views)
    assert state["available"] == []
    assert state["discarded"] == [2, 1, 0]

    reset_view = plugin._display_payload(payload, {}, now)
    reset_state = plugin._read_display_state()

    assert reset_view["observations"][0]["gbif_key"] == "202"
    assert reset_state["available"] == [0, 2]
    assert reset_state["discarded"] == [1]


def test_theme_only_redraw_keeps_random_bag_and_hero_while_palette_changes(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_write_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_ensure_display_common_name", lambda _observation: None)
    monkeypatch.setattr(plugin, "_shuffled_display_indices", lambda values: [1, 0, 2])
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    observations = [
        plugin._observation_from_occurrence(
            occurrence(
                key=501 + index,
                taxonKey=501 + index,
                speciesKey=501 + index,
                vernacularName=f"cached species {index}",
                scientificName=f"Species fixture {index}",
                species=f"Species fixture {index}",
                common_name_zh=f"缓存物种{index}",
            ),
            location,
        )
        for index in range(3)
    ]
    calls = {"provider": 0}

    def warm_payload(*_args):
        calls["provider"] += 1
        return {
            "schema": "species-radar-v2",
            "source": "GBIF",
            "location": location,
            "radius_km": 25,
            "observations": observations,
            "category_counts": plugin._category_counts(observations),
        }

    monkeypatch.setattr(plugin, "_fetch_live_payload", warm_payload)
    settings = {
        "latitude": str(location["latitude"]),
        "longitude": str(location["longitude"]),
        "locationName": location["name"],
        "googleMapsApiKey": "test-map-key",
        "themeMode": "night",
    }
    photo_color = (70, 130, 90)
    map_color = (28, 104, 150)
    image_payloads = {}
    for key, color, size in (
        ("photo", photo_color, (600, 400)),
        ("map", map_color, (390, 172)),
    ):
        buffer = BytesIO()
        Image.new("RGB", size, color).save(buffer, "PNG")
        image_payloads[key] = buffer.getvalue()

    class ImageResponse:
        headers = {}

        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size > 0
            yield self.data

        def close(self):
            return None

    warm_http_calls = []

    def warm_download(url, **_kwargs):
        warm_http_calls.append(url)
        key = "map" if str(url).startswith(species_mod.GOOGLE_STATIC_MAPS_URL) else "photo"
        return image_payloads[key]

    monkeypatch.setattr(plugin, "_download_provider_bytes", warm_download)
    # Exercise compatibility with a legacy warm cache directly; the public
    # unbound preview path is intentionally write-free under PREPARED_BANK.
    plugin._generate_stateless_preview(settings, DummyDeviceConfig())
    assert warm_http_calls
    photo_cache_files = list(tmp_path.rglob("photo_*.png"))
    map_cache_files = list(tmp_path.rglob("map_*.png"))
    assert photo_cache_files
    assert map_cache_files
    stale_time = time.time() - 8 * 24 * 60 * 60
    for cache_file in [*photo_cache_files, *map_cache_files]:
        os.utime(cache_file, (stale_time, stale_time))
    rotation_path = tmp_path / "display_rotation.json"
    rotation_before = rotation_path.read_bytes()
    selected_key = plugin._read_display_state()["selected_key"]

    def fail_provider(*_args, **_kwargs):
        calls["provider"] += 1
        raise AssertionError("theme-only redraw must not call a provider")

    monkeypatch.setattr(plugin, "_fetch_live_payload", fail_provider)
    theme_http_calls = []

    def fail_http():
        theme_http_calls.append("session")
        raise AssertionError("theme-only media must not acquire an HTTP session")

    monkeypatch.setattr(plugin, "_download_provider_bytes", lambda *_args, **_kwargs: fail_http())
    rendered_heroes = []
    original_render = plugin._render_page

    def record_render(dimensions, payload, render_settings, now, device_config=None):
        rendered_heroes.append(plugin._observation_identity(payload["observations"][0]))
        return original_render(dimensions, payload, render_settings, now, device_config)

    monkeypatch.setattr(plugin, "_render_page", record_render)
    day = _canonical_theme(
        "day",
        background=(241, 236, 225),
        panel=(221, 213, 196),
        ink=(19, 21, 23),
        muted=(73, 75, 79),
        rule=(128, 124, 116),
        accent=(180, 44, 58),
    )
    night = _canonical_theme(
        "night",
        background=(9, 11, 14),
        panel=(25, 29, 35),
        ink=(244, 246, 248),
        muted=(179, 183, 191),
        rule=(61, 67, 75),
        accent=(72, 186, 234),
    )

    day_image = plugin.generate_image({**settings, "_theme_render_only": True, "_inkypi_theme": day}, DummyDeviceConfig())
    night_image = plugin.generate_image({**settings, "_theme_render_only": True, "_inkypi_theme": night}, DummyDeviceConfig())

    assert calls == {"provider": 1}
    assert theme_http_calls == []
    assert rotation_path.read_bytes() == rotation_before
    assert rendered_heroes == [selected_key, selected_key]
    assert day_image.getpixel((0, 0)) == COMIC_PAPER
    assert night_image.getpixel((0, 0)) == night["palette"]["background"]
    assert hashlib.sha256(day_image.tobytes()).digest() != hashlib.sha256(night_image.tobytes()).digest()


def test_theme_only_render_uses_photo_and_map_placeholders_on_cold_or_corrupt_cache_without_http(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_write_context", lambda *_args, **_kwargs: None)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    observation = plugin._observation_from_occurrence(occurrence(), location)
    settings = {
        "latitude": str(location["latitude"]),
        "longitude": str(location["longitude"]),
        "locationName": location["name"],
        "googleMapsApiKey": "test-map-key",
        "_theme_render_only": True,
        "_inkypi_theme": _canonical_theme(
            "day",
            background=(241, 236, 225),
            panel=(221, 213, 196),
            ink=(19, 21, 23),
            muted=(73, 75, 79),
            rule=(128, 124, 116),
            accent=(180, 44, 58),
        ),
    }
    now = datetime.now(timezone.utc)
    cache_key = plugin._cache_key(settings, now, location)
    plugin._write_cache(
        {
            "schema": "species-radar-v2",
            "cache_key": cache_key,
            "generated_at": now.isoformat(),
            "payload": {
                "schema": "species-radar-v2",
                "cache_key": cache_key,
                "source": "GBIF",
                "location": location,
                "radius_km": 25,
                "observations": [observation],
                "category_counts": plugin._category_counts([observation]),
            },
        }
    )
    photo_digest = hashlib.sha1(observation["image_url"].encode("utf-8")).hexdigest()[:18]
    photo_dir = tmp_path / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    (photo_dir / f"photo_{photo_digest}.png").write_bytes(b"not an image")
    http_calls = []

    def fail_http():
        http_calls.append("session")
        raise AssertionError("theme-only media must not acquire an HTTP session")

    monkeypatch.setattr(plugin, "_download_provider_bytes", lambda *_args, **_kwargs: fail_http())

    image = plugin.generate_image(settings, DummyDeviceConfig())

    assert image.size == (800, 480)
    assert http_calls == []


def test_photo_download_survives_managed_cache_write_failure(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    buffer = BytesIO()
    Image.new("RGB", (80, 60), (70, 130, 90)).save(buffer, "PNG")

    class ImageResponse:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size > 0
            yield buffer.getvalue()

        def close(self):
            return None

    monkeypatch.setattr(
        plugin,
        "_download_provider_bytes",
        lambda *_args, **_kwargs: buffer.getvalue(),
    )
    monkeypatch.setattr(
        plugin,
        "_write_photo_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cache is read-only")),
    )

    image = plugin._download_image("https://example.com/cache-write-failure.png", (80, 60))

    assert image is not None
    assert image.size == (80, 60)


def test_theme_only_daily_cache_miss_fails_without_provider_calls(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    calls = {"provider": 0}

    def fake_provider(*_args):
        calls["provider"] += 1
        return {}

    monkeypatch.setattr(plugin, "_fetch_live_payload", fake_provider)

    with pytest.raises(RuntimeError, match="warm .*cache"):
        plugin._daily_payload(
            {"_theme_render_only": True},
            datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc),
            {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"},
        )

    assert calls == {"provider": 0}


def test_display_payload_resets_random_pool_when_cache_bucket_changes(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    observations = [
        plugin._observation_from_occurrence(occurrence(key=301 + index, taxonKey=301 + index, speciesKey=301 + index), location)
        for index in range(3)
    ]
    monkeypatch.setattr(plugin, "_ensure_display_common_name", lambda _observation: None)
    monkeypatch.setattr(plugin, "_shuffled_display_indices", lambda values: [1, 0, 2] if isinstance(values, int) else list(values))
    plugin._write_display_state({
        "schema": "species-radar-v2",
        "pool_key": "old-cache:3:abc",
        "count": 3,
        "available": [],
        "discarded": [2, 1, 0],
    })
    payload = {
        "schema": "species-radar-v1",
        "cache_key": "new-six-hour-cache",
        "observations": observations,
        "category_counts": plugin._category_counts(observations),
        "location": location,
    }

    display_payload = plugin._display_payload(payload, {}, now)
    state = plugin._read_display_state()

    assert display_payload["observations"][0]["gbif_key"] == "302"
    assert state["pool_key"].startswith("new-six-hour-cache:3:")
    assert state["available"] == [0, 2]
    assert state["discarded"] == [1]


def test_display_payload_enriches_rotated_hero_chinese_name(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    records = [
        occurrence(key=400 + index, taxonKey=400 + index, speciesKey=400 + index, vernacularName=f"sample species {index}")
        for index in range(8)
    ]
    records.append(
        occurrence(
            key=409,
            taxonKey=2490715,
            speciesKey=2490715,
            vernacularName="Western Bluebird",
            scientificName="Sialia mexicana Swainson, 1832",
            species="Sialia mexicana",
            kingdom="Animalia",
            **{"class": "Aves"},
        )
    )
    observations = [plugin._observation_from_occurrence(record, location) for record in records]
    payload = {
        "schema": "species-radar-v1",
        "cache_key": "daily-pool-name-test",
        "observations": observations,
        "category_counts": plugin._category_counts(observations),
        "location": location,
        "source_state": "cache",
    }

    monkeypatch.setattr(plugin, "_next_display_index", lambda _payload, _observations, _now: 8)
    monkeypatch.setattr(plugin, "_fetch_vernacular_name_candidates", lambda _taxon_key: {"zh": "", "en": "Western Bluebird", "any": "Western Bluebird"})
    monkeypatch.setattr(plugin, "_fetch_wikidata_chinese_name", lambda _scientific_name: "西方藍鶇")

    display_payload = plugin._display_payload(payload, {}, now)
    hero = display_payload["observations"][0]

    assert hero["gbif_key"] == "409"
    assert hero["common_name_zh"] == "西方蓝鸫"
    assert hero["common_name_en"] == "Western Bluebird"
    assert hero["common_name"] == "西方蓝鸫"
    assert hero["display_name"] == "西方蓝鸫"
    assert hero["common_name_lookup_attempted"] is True
    assert plugin._name_lines(hero)[:2] == ("西方蓝鸫", "Western Bluebird")

def test_rotated_display_payload_drives_observation_map_coordinates(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    records = [
        occurrence(key=211, taxonKey=211, speciesKey=211, vernacularName="Western Bluebird", scientificName="Sialia mexicana", species="Sialia mexicana", kingdom="Animalia", **{"class": "Aves"}, decimalLatitude=37.55, decimalLongitude=-121.99),
        occurrence(key=212, taxonKey=212, speciesKey=212, vernacularName="California Slender Salamander", scientificName="Batrachoseps attenuatus", species="Batrachoseps attenuatus", kingdom="Animalia", **{"class": "Amphibia"}, decimalLatitude=37.61, decimalLongitude=-122.03),
        occurrence(key=213, taxonKey=213, speciesKey=213, vernacularName="field mushroom", scientificName="Agaricus campestris", species="Agaricus campestris", kingdom="Fungi", **{"class": "Agaricomycetes"}, decimalLatitude=37.51, decimalLongitude=-121.96),
    ]
    observations = [plugin._observation_from_occurrence(record, location) for record in records]
    monkeypatch.setattr(plugin, "_ensure_display_common_name", lambda _observation: None)
    payload = {
        "schema": "species-radar-v1",
        "cache_key": "daily-map-test",
        "observations": observations,
        "category_counts": plugin._category_counts(observations),
        "location": location,
        "source_state": "cache",
    }
    display_payload = plugin._display_payload(payload, {}, now)
    hero = display_payload["observations"][0]
    map_calls = []

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (600, 400), (70, 130, 90)))

    def fake_map(_settings, _device_config, observation, target_size):
        map_calls.append((observation["gbif_key"], observation["latitude"], observation["longitude"], target_size))
        return Image.new("RGB", (target_size[0] * 2, target_size[1] * 2), (28, 104, 150))

    monkeypatch.setattr(plugin, "_load_observation_map", fake_map)

    image = plugin._render_page((800, 480), display_payload, {"showObservationMap": "true"}, now, DummyDeviceConfig())

    assert image.size == (800, 480)
    assert map_calls
    assert map_calls[0][0] == hero["gbif_key"]
    assert map_calls[0][1:3] == (hero["latitude"], hero["longitude"])


def test_daily_payload_uses_stale_cache_when_refresh_fails(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    live_payload = {
        "schema": "species-radar-v1",
        "observations": [plugin._observation_from_occurrence(occurrence(), location)],
        "category_counts": {"植物": 1},
        "location": location,
    }
    calls = {"count": 0}

    def fake_fetch(*_args):
        calls["count"] += 1
        if calls["count"] == 1:
            return dict(live_payload)
        raise RuntimeError("network down")

    monkeypatch.setattr(plugin, "_fetch_live_payload", fake_fetch)

    first = plugin._daily_payload({}, now, location)
    second = plugin._daily_payload({"forceRefresh": "true"}, now, location)

    assert first["source_state"] == "live"
    assert second["source_state"] == "cache"
    assert second["observations"][0]["display_name"] == first["observations"][0]["display_name"]


def test_daily_payload_snake_force_alias_overrides_inactive_camel_alias(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    live_payload = {
        "schema": "species-radar-v1",
        "observations": [plugin._observation_from_occurrence(occurrence(), location)],
        "category_counts": {"species": 1},
        "location": location,
    }
    calls = []
    monkeypatch.setattr(
        plugin,
        "_fetch_live_payload",
        lambda *_args: calls.append("provider") or dict(live_payload),
    )

    plugin._daily_payload({}, now, location)
    refreshed = plugin._daily_payload(
        {"forceRefresh": "false", "force_refresh": "true"},
        now,
        location,
    )

    assert calls == ["provider", "provider"]
    assert refreshed["source_state"] == "live"


def test_cache_key_uses_configurable_refresh_hour_buckets(tmp_path):
    plugin = make_plugin(tmp_path)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    settings = {"refreshHours": str(DEFAULT_REFRESH_HOURS)}

    key_early = plugin._cache_key(settings, datetime(2026, 6, 27, 1, 30, tzinfo=timezone.utc), location)
    key_same_bucket = plugin._cache_key(settings, datetime(2026, 6, 27, 5, 59, tzinfo=timezone.utc), location)
    key_next_bucket = plugin._cache_key(settings, datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc), location)
    key_two_hour_bucket = plugin._cache_key({"refreshHours": "2"}, datetime(2026, 6, 27, 5, 59, tzinfo=timezone.utc), location)

    assert key_same_bucket == key_early
    assert key_next_bucket != key_early
    assert key_two_hour_bucket != key_early

def test_google_observation_map_url_uses_marker_and_existing_key_alias(tmp_path):
    plugin = make_plugin(tmp_path)
    device_config = DummyDeviceConfig(env={"Google_KEY": "test-google-key"})
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    obs = plugin._observation_from_occurrence(occurrence(), location)

    assert plugin._google_maps_api_key({}, device_config) == "test-google-key"

    url = plugin._google_observation_map_url(
        {"googleMapType": "terrain", "observationMapZoom": "13"},
        "test-google-key",
        obs,
        (240, 56),
    )
    query = parse_qs(urlparse(url).query)

    assert query["key"] == ["test-google-key"]
    assert query["maptype"] == ["terrain"]
    assert query["zoom"] == ["13"]
    assert query["scale"] == ["2"]
    assert query["markers"][0].endswith("37.55000,-121.99000")


def test_palette_uses_original_day_roles_without_changing_category_tokens(tmp_path):
    plugin = make_plugin(tmp_path)
    theme = _canonical_theme(
        "day",
        background=(255, 255, 255),
        panel=(255, 255, 255),
        ink=(10, 12, 15),
        muted=(74, 78, 84),
        rule=(185, 188, 194),
        accent=(24, 92, 150),
    )
    palette = plugin._palette({"_inkypi_theme": theme})

    assert palette["paper"] == COMIC_PAPER
    assert palette["ink"] == COMIC_INK
    assert palette["accent"] == COMIC_BLUE
    assert CATEGORY_STYLES["植物"]["color"] == COMIC_GREEN
    assert CATEGORY_STYLES["植物"]["light"] == COMIC_PANEL_GREEN
    assert CATEGORY_STYLES["鸟类"]["color"] == COMIC_BLUE
    assert CATEGORY_STYLES["鸟类"]["light"] == COMIC_PANEL_BLUE
    assert CATEGORY_STYLES["昆虫"]["color"] == COMIC_ORANGE

def test_font_defaults_prefer_microsoft_yahei(tmp_path):
    plugin = make_plugin(tmp_path)

    assert DEFAULT_FONT == MICROSOFT_YAHEI_FONT
    assert DEFAULT_CJK_FONT == MICROSOFT_YAHEI_FONT
    normal_paths = plugin._preferred_font_paths(bold=False)
    bold_paths = plugin._preferred_font_paths(bold=True)
    normal_first = normal_paths[0].replace("\\", "/").lower()
    bold_first = bold_paths[0].replace("\\", "/").lower()
    assert normal_first.endswith("sports_dashboard/fonts/msyh.ttc")
    assert bold_first.endswith("sports_dashboard/fonts/msyhbd.ttc")
    assert "jost" not in "|".join(normal_paths + bold_paths).lower()
    assert "lxgw" not in "|".join(normal_paths + bold_paths).lower()

    fonts = [
        plugin._font(10),
        plugin._font(15),
        plugin._font(17),
        plugin._font(26, bold=True),
        plugin._font_for_text("Western Bluebird", plugin._font(17)),
        plugin._font_for_text("西方蓝鸫", plugin._font(26, bold=True)),
    ]
    if Path(normal_paths[0]).exists():
        assert all("msyh" in str(getattr(font, "path", "")).lower() for font in fonts)


def test_font_loader_uses_shared_base_ui_resolver(monkeypatch, tmp_path):
    plugin = make_plugin(tmp_path)
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        species_mod,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
        raising=False,
    )

    assert plugin._font(15) is sentinel
    assert plugin._font(26, bold=True) is sentinel
    assert calls == [(15, False), (26, True)]


def test_cjk_font_falls_back_when_shared_font_lacks_required_glyphs(
    monkeypatch, tmp_path
):
    plugin = make_plugin(tmp_path)
    shared_font = object()
    cjk_font = object()
    monkeypatch.setattr(
        species_mod,
        "get_base_ui_font",
        lambda _size, bold=False: shared_font,
    )
    monkeypatch.setattr(
        plugin,
        "_font_supports_text",
        lambda font, _text: font is cjk_font,
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "_preferred_font_paths",
        lambda bold=False: ("cjk-capable.ttf",),
    )
    monkeypatch.setattr(
        species_mod.ImageFont,
        "truetype",
        lambda _path, _size: cjk_font,
    )

    assert plugin._font(18, cjk=True) is cjk_font


def test_non_cjk_font_returns_shared_font_without_probing_fallback(
    monkeypatch, tmp_path
):
    plugin = make_plugin(tmp_path)
    shared_font = object()
    monkeypatch.setattr(
        species_mod,
        "get_base_ui_font",
        lambda _size, bold=False: shared_font,
    )
    monkeypatch.setattr(
        plugin,
        "_font_supports_text",
        lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected glyph probe")),
    )

    assert plugin._font(18) is shared_font


def test_cjk_font_keeps_shared_font_when_no_capable_fallback_exists(
    monkeypatch, tmp_path
):
    plugin = make_plugin(tmp_path)
    shared_font = object()
    monkeypatch.setattr(
        species_mod,
        "get_base_ui_font",
        lambda _size, bold=False: shared_font,
    )
    monkeypatch.setattr(plugin, "_font_supports_text", lambda *_args: False)
    monkeypatch.setattr(plugin, "_preferred_font_paths", lambda bold=False: ())
    monkeypatch.setattr(plugin, "_emergency_font_paths", lambda bold=False: ())

    assert plugin._font(18, cjk=True) is shared_font


def test_title_wordmark_asset_is_transparent_and_header_sized():
    path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "species_radar" / TITLE_WORDMARK_IMAGE

    with Image.open(path) as image:
        wordmark = image.convert("RGBA")

    assert wordmark.size == (172, 40)
    assert TITLE_WORDMARK_DISPLAY_SIZE == (150, 34)
    assert wordmark.getpixel((0, 0))[3] == 0
    assert wordmark.getchannel("A").getbbox() is not None


def test_header_pixel_background_asset_is_transparent():
    path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "species_radar" / HEADER_PIXEL_BACKGROUND_IMAGE

    with Image.open(path) as image:
        art = image.convert("RGBA")

    assert art.size == HEADER_PIXEL_BACKGROUND_DISPLAY_SIZE
    assert art.getpixel((0, 0))[3] == 0
    assert art.getpixel((art.width - 1, art.height - 1))[3] == 0
    assert art.getchannel("A").getbbox() is not None


def test_palette_supports_injected_night_theme_over_legacy_alias(tmp_path):
    plugin = make_plugin(tmp_path)
    theme = _canonical_theme(
        "night",
        background=(9, 11, 14),
        panel=(25, 29, 35),
        ink=(244, 246, 248),
        muted=(179, 183, 191),
        rule=(61, 67, 75),
        accent=(72, 186, 234),
    )
    palette = plugin._palette({"themeMode": "comic", "_inkypi_theme": theme})

    assert palette["night"] is True
    assert palette["paper"] == theme["palette"]["background"]
    assert palette["panel"] == theme["palette"]["panel"]
    assert palette["ink"] == theme["palette"]["ink"]
    assert palette["muted"] == theme["palette"]["muted"]
    assert palette["rule"] == theme["palette"]["rule"]
    assert palette["accent"] == theme["palette"]["accent"]


def test_palette_switches_from_resolved_canonical_context_without_playlist_duplication(tmp_path):
    plugin = make_plugin(tmp_path)
    day_theme = plugin.resolve_theme({"themeMode": "day"}, {"timezone": "UTC"}, now=datetime(2026, 6, 27, 12, tzinfo=timezone.utc))
    night_theme = plugin.resolve_theme({"themeMode": "night"}, {"timezone": "UTC"}, now=datetime(2026, 6, 27, 20, tzinfo=timezone.utc))
    day = plugin._palette({"_inkypi_theme": day_theme})
    night = plugin._palette({"_inkypi_theme": night_theme})

    assert day["night"] is False
    assert day["paper"] == COMIC_PAPER
    assert night["night"] is True
    assert night["paper"] == night_theme["palette"]["background"]

def test_render_page_draws_header_pixel_background(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    hero = plugin._observation_from_occurrence(occurrence(), location)
    payload = {
        "schema": "species-radar-v1",
        "observations": [hero],
        "category_counts": {"植物": 1},
        "location": location,
        "source_state": "live",
    }
    boxes = []

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (600, 400), (70, 130, 90)))
    monkeypatch.setattr(plugin, "_draw_header_pixel_background", lambda _canvas, box: boxes.append(tuple(int(value) for value in box)) or True)

    image = plugin._render_page(
        (800, 480),
        payload,
        {"_inkypi_theme": _canonical_theme(
            "day",
            background=(255, 255, 255),
            panel=(255, 255, 255),
            ink=(10, 12, 15),
            muted=(74, 78, 84),
            rule=(185, 188, 194),
            accent=(24, 92, 150),
        )},
        now,
    )

    assert image.size == (800, 480)
    assert boxes
    x0, y0, x1, y1 = boxes[0]
    assert 190 <= x0 <= 240
    assert y0 == 17
    assert 600 <= x1 <= 700
    assert y1 == 65

def test_pixel_placeholder_asset_is_horizontal_banner():
    path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "species_radar" / PIXEL_PLACEHOLDER_IMAGE

    with Image.open(path) as image:
        banner = image.convert("RGB")

    assert banner.size == (288, 52)
    assert banner.size[0] >= banner.size[1] * 4
    flat = Image.new("RGB", banner.size, banner.getpixel((0, 0)))
    assert ImageChops.difference(banner, flat).getbbox() is not None


def test_render_page_draws_right_panel_pixel_placeholder(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    records = [
        occurrence(key=301, taxonKey=301, speciesKey=301, vernacularName="Western Bluebird", scientificName="Sialia mexicana", species="Sialia mexicana", kingdom="Animalia", **{"class": "Aves"}),
        occurrence(key=302, taxonKey=302, speciesKey=302, vernacularName="field mushroom", kingdom="Fungi", **{"class": "Agaricomycetes"}),
        occurrence(key=303, taxonKey=303, speciesKey=303, vernacularName="milkmaids"),
    ]
    observations = [plugin._observation_from_occurrence(record, location) for record in records]
    payload = {
        "schema": "species-radar-v1",
        "observations": observations,
        "category_counts": plugin._category_counts(observations),
        "location": location,
        "source_state": "live",
    }
    boxes = []

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (600, 400), (70, 130, 90)))
    monkeypatch.setattr(plugin, "_load_observation_map", lambda *_args, **_kwargs: Image.new("RGB", (180, 60), (28, 104, 150)))

    def fake_placeholder(_canvas, _draw, box, _palette):
        boxes.append(tuple(int(value) for value in box))

    monkeypatch.setattr(plugin, "_draw_right_panel_pixel_placeholder", fake_placeholder)

    image = plugin._render_page((800, 480), payload, {"showObservationMap": "true"}, now, DummyDeviceConfig())

    assert image.size == (800, 480)
    assert boxes
    x0, y0, x1, y1 = boxes[0]
    assert x0 >= 480
    assert x1 - x0 >= 260
    assert 30 <= y1 - y0 <= 72

def test_render_page_uses_title_wordmark_asset(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    hero = plugin._observation_from_occurrence(occurrence(), location)
    payload = {
        "schema": "species-radar-v1",
        "observations": [hero],
        "category_counts": {"植物": 1},
        "location": location,
        "source_state": "live",
    }
    calls = []

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (600, 400), (70, 130, 90)))
    monkeypatch.setattr(plugin, "_draw_title_wordmark", lambda _canvas, x, y, size: calls.append((x, y, size)) or True)

    image = plugin._render_page(
        (800, 480),
        payload,
        {"_inkypi_theme": _canonical_theme(
            "day",
            background=(255, 255, 255),
            panel=(255, 255, 255),
            ink=(10, 12, 15),
            muted=(74, 78, 84),
            rule=(185, 188, 194),
            accent=(24, 92, 150),
        )},
        now,
    )

    assert image.size == (800, 480)
    assert calls[0][2] == TITLE_WORDMARK_DISPLAY_SIZE


def test_empty_page_uses_title_wordmark_asset(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    payload = {
        "schema": "species-radar-v1",
        "observations": [],
        "location": {"name": "Fremont, CA"},
        "source_state": "live",
    }
    calls = []

    monkeypatch.setattr(
        plugin,
        "_draw_title_wordmark",
        lambda _canvas, x, y, size: calls.append((x, y, size)) or True,
    )

    image = plugin._render_page(
        (800, 480),
        payload,
        {"_inkypi_theme": _canonical_theme(
            "day",
            background=(255, 255, 255),
            panel=(255, 255, 255),
            ink=(10, 12, 15),
            muted=(74, 78, 84),
            rule=(185, 188, 194),
            accent=(24, 92, 150),
        )},
        now,
    )

    assert image.size == (800, 480)
    assert calls[0][2] == TITLE_WORDMARK_EMPTY_DISPLAY_SIZE


def test_title_wordmark_visible_ink_is_left_aligned(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (220, 70), COMIC_PAPER)

    assert plugin._draw_title_wordmark(canvas, 20, 12, TITLE_WORDMARK_DISPLAY_SIZE)

    ink_pixels = [
        (x, y)
        for x in range(20, 20 + TITLE_WORDMARK_DISPLAY_SIZE[0])
        for y in range(12, 12 + TITLE_WORDMARK_DISPLAY_SIZE[1])
        if canvas.getpixel((x, y)) != COMIC_PAPER
    ]
    assert ink_pixels
    assert min(x for x, _y in ink_pixels) <= 20

def test_render_page_draws_english_common_name_when_gallery_is_visible(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    hero = plugin._observation_from_occurrence(
        occurrence(
            key=10,
            taxonKey=2490715,
            speciesKey=2490715,
            vernacularName="Western Bluebird",
            scientificName="Sialia mexicana Swainson, 1832",
            species="Sialia mexicana",
            kingdom="Animalia",
            **{"class": "Aves"},
        ),
        location,
    )
    hero.update({"common_name_zh": "西方蓝鸫", "common_name_en": "Western Bluebird", "common_name": "西方蓝鸫", "display_name": "西方蓝鸫"})
    records = [
        occurrence(key=11, taxonKey=11, speciesKey=11, vernacularName="milkmaids"),
        occurrence(key=12, taxonKey=12, speciesKey=12, kingdom="Fungi", **{"class": "Agaricomycetes"}, family="Agaricaceae", genus="Agaricus", vernacularName="field mushroom"),
        occurrence(key=13, taxonKey=13, speciesKey=13, kingdom="Animalia", **{"class": "Insecta"}, family="Apidae", genus="Bombus", vernacularName="bumble bee"),
        occurrence(key=14, taxonKey=14, speciesKey=14, kingdom="Animalia", **{"class": "Mammalia"}, family="Sciuridae", genus="Sciurus", vernacularName="squirrel"),
    ]
    observations = [hero] + [plugin._observation_from_occurrence(record, location) for record in records]
    payload = {
        "schema": "species-radar-v1",
        "observations": observations,
        "category_counts": plugin._category_counts(observations),
        "location": location,
        "source_state": "live",
    }
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def spy_text(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        drawn_text.append((xy, str(text), getattr(font, "size", None)))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_download_image", lambda _url, target_size: Image.new("RGB", target_size, (70, 130, 90)))
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy_text)

    image = plugin._render_page((800, 480), payload, {}, now)

    drawn = {text: (xy, size) for xy, text, size in drawn_text}

    assert image.size == (800, 480)
    assert "西方蓝鸫" in drawn
    assert "Western Bluebird" in drawn
    assert "Sialia mexicana" in drawn
    assert drawn["Western Bluebird"][0][0] > drawn["西方蓝鸫"][0][0]
    assert abs(drawn["Western Bluebird"][0][1] - drawn["西方蓝鸫"][0][1]) <= 18
    assert drawn["Western Bluebird"][1] == 17
    assert drawn["Sialia mexicana"][0][1] > drawn["西方蓝鸫"][0][1] + 20
    assert drawn["Sialia mexicana"][1] == 15
    assert [text for _xy, text, _size in drawn_text].count("鸟类") == 1

def test_render_page_uses_complete_english_primary_when_chinese_missing(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    hero = plugin._observation_from_occurrence(
        occurrence(
            key=31,
            taxonKey=31,
            speciesKey=31,
            vernacularName="California Slender Salamander",
            scientificName="Batrachoseps attenuatus",
            species="Batrachoseps attenuatus",
            kingdom="Animalia",
            **{"class": "Amphibia"},
        ),
        location,
    )
    hero.update({
        "common_name_zh": "",
        "common_name_en": "California Slender Salamander",
        "common_name": "California Slender Salamander",
        "display_name": "California Slender Salamander",
    })
    payload = {
        "schema": "species-radar-v1",
        "observations": [hero],
        "category_counts": {"两栖动物": 1},
        "location": location,
        "source_state": "live",
    }
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def spy_text(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        drawn_text.append((xy, str(text), getattr(font, "size", None)))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_download_image", lambda _url, target_size: Image.new("RGB", target_size, (70, 130, 90)))
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy_text)

    image = plugin._render_page((800, 480), payload, {}, now)

    assert image.size == (800, 480)
    texts = [text for _xy, text, _size in drawn_text]
    name_fragments = [text for text in texts if any(word in text for word in ("California", "Slender", "Salamander"))]
    assert name_fragments
    assert "中文名暂缺" not in texts
    assert not any("..." in text for text in name_fragments)
    joined = " ".join(name_fragments)
    assert "California" in joined
    assert "Slender" in joined
    assert "Salamander" in joined
    assert max(size for _xy, text, size in drawn_text if text in name_fragments) <= 27
    sci_entry = next((xy for xy, text, _size in drawn_text if text == "Batrachoseps attenuatus"), None)
    assert sci_entry is not None
    name_bottom = max(xy[1] + (size or 0) for xy, text, size in drawn_text if text in name_fragments)
    assert name_bottom < sci_entry[1]


def test_render_page_wraps_complete_english_when_chinese_is_primary(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    hero = plugin._observation_from_occurrence(
        occurrence(
            key=32,
            taxonKey=32,
            speciesKey=32,
            vernacularName="California Slender Salamander",
            scientificName="Batrachoseps attenuatus",
            species="Batrachoseps attenuatus",
            kingdom="Animalia",
            **{"class": "Amphibia"},
        ),
        location,
    )
    hero.update({
        "common_name_zh": "加州细长螈",
        "common_name_en": "California Slender Salamander",
        "common_name": "加州细长螈",
        "display_name": "加州细长螈",
    })
    payload = {
        "schema": "species-radar-v1",
        "observations": [hero],
        "category_counts": {"两栖动物": 1},
        "location": location,
        "source_state": "live",
    }
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def spy_text(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        drawn_text.append((xy, str(text), getattr(font, "size", None)))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_download_image", lambda _url, target_size: Image.new("RGB", target_size, (70, 130, 90)))
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy_text)

    image = plugin._render_page((800, 480), payload, {}, now)

    assert image.size == (800, 480)
    drawn = {text: xy for xy, text, _size in drawn_text}
    texts = [text for _xy, text, _size in drawn_text]
    assert "加州细长螈" in drawn
    english_fragments = [text for text in texts if any(word in text for word in ("California", "Slender", "Salamander"))]
    assert len(english_fragments) >= 2
    assert not any("..." in text for text in english_fragments)
    joined = " ".join(english_fragments)
    assert "California" in joined
    assert "Slender" in joined
    assert "Salamander" in joined
    assert min(xy[0] for xy, text, _size in drawn_text if text in english_fragments) > drawn["加州细长螈"][0]
    english_bottom = max(xy[1] + (size or 0) for xy, text, size in drawn_text if text in english_fragments)
    assert english_bottom <= drawn["加州细长螈"][1] + 34

def test_fit_wrapped_font_respects_height_limit(tmp_path):
    plugin = make_plugin(tmp_path)
    canvas = Image.new("RGB", (240, 120), COMIC_PAPER)
    draw = ImageDraw.Draw(canvas)

    tall_font, tall_lines = plugin._fit_wrapped_font(
        draw,
        "California Slender Salamander",
        170,
        max_lines=3,
        min_size=8,
        max_size=27,
        bold=True,
    )
    short_font, short_lines = plugin._fit_wrapped_font(
        draw,
        "California Slender Salamander",
        170,
        max_lines=3,
        min_size=8,
        max_size=27,
        bold=True,
        max_height=42,
    )

    assert plugin._wrapped_block_height(draw, short_lines, short_font, 1.02) <= 42
    assert getattr(short_font, "size", 0) < getattr(tall_font, "size", 99)
    assert " ".join(short_lines) == "California Slender Salamander"
    assert " ".join(tall_lines) == "California Slender Salamander"

def test_render_page_adds_diverse_thumbnail_strip_when_many_images(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    records = [
        occurrence(key=1, taxonKey=1, speciesKey=1, vernacularName="milkmaids"),
        occurrence(key=2, taxonKey=2, speciesKey=2, kingdom="Animalia", **{"class": "Aves"}, family="Anatidae", genus="Oxyura", vernacularName="ruddy duck"),
        occurrence(key=3, taxonKey=3, speciesKey=3, kingdom="Fungi", **{"class": "Agaricomycetes"}, family="Agaricaceae", genus="Agaricus", vernacularName="field mushroom"),
        occurrence(key=4, taxonKey=4, speciesKey=4, kingdom="Animalia", **{"class": "Insecta"}, family="Apidae", genus="Bombus", vernacularName="bumble bee"),
        occurrence(key=5, taxonKey=5, speciesKey=5, kingdom="Animalia", **{"class": "Mammalia"}, family="Sciuridae", genus="Sciurus", vernacularName="squirrel"),
        occurrence(key=6, taxonKey=6, speciesKey=6, kingdom="Animalia", **{"class": "Amphibia"}, family="Hylidae", genus="Pseudacris", vernacularName="chorus frog"),
    ]
    observations = [plugin._observation_from_occurrence(record, location) for record in records]
    payload = {
        "schema": "species-radar-v1",
        "observations": observations,
        "category_counts": plugin._category_counts(observations),
        "location": location,
        "source_state": "live",
    }
    colors = [(70, 130, 90), (160, 90, 80), (80, 120, 160), (160, 140, 60), (80, 150, 120)]
    calls = []

    def fake_download(_url, target_size):
        color = colors[min(len(calls), len(colors) - 1)]
        calls.append(target_size)
        return Image.new("RGB", target_size, color)

    monkeypatch.setattr(plugin, "_download_image", fake_download)

    image = plugin._render_page((800, 480), payload, {}, now)
    gallery_pixels = {
        image.getpixel((x, y))
        for x in range(24, 780, 8)
        for y in range(370, 435, 8)
    }

    assert len(calls) == 5
    assert [item["category_label"] for item in plugin._gallery_observations(observations, max_items=4)] == ["鸟类", "真菌", "昆虫", "哺乳动物"]
    assert any(color in gallery_pixels for color in colors[1:])

def test_render_page_does_not_draw_radar_after_right_panel_simplification(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    hero = plugin._observation_from_occurrence(occurrence(), location)
    bird = plugin._observation_from_occurrence(
        occurrence(key=124, speciesKey=2498305, kingdom="Animalia", **{"class": "Aves"}, family="Anatidae", genus="Oxyura", vernacularName="ruddy duck"),
        location,
    )
    payload = {
        "schema": "species-radar-v1",
        "observations": [hero, bird],
        "category_counts": {"植物": 1, "鸟类": 1},
        "location": location,
        "source_state": "live",
    }

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (600, 400), (70, 130, 90)))
    monkeypatch.setattr(plugin, "_draw_radar", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("radar should not render")))

    image = plugin._render_page((800, 480), payload, {}, now)

    assert image.size == (800, 480)

def test_render_page_restores_right_panel_category_count_chips(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    records = [
        occurrence(key=120, speciesKey=2490715, kingdom="Animalia", **{"class": "Aves"}, family="Turdidae", genus="Sialia", vernacularName="Western Bluebird"),
        occurrence(key=121, speciesKey=121, kingdom="Plantae", **{"class": "Magnoliopsida"}, family="Asteraceae", genus="Madia", vernacularName="tarweed"),
        occurrence(key=122, speciesKey=122, kingdom="Fungi", **{"class": "Agaricomycetes"}, family="Agaricaceae", genus="Agaricus", vernacularName="field mushroom"),
        occurrence(key=123, speciesKey=123, kingdom="Animalia", **{"class": "Insecta"}, family="Apidae", genus="Bombus", vernacularName="bumble bee"),
    ]
    observations = [plugin._observation_from_occurrence(record, location) for record in records]
    payload = {
        "schema": "species-radar-v1",
        "observations": observations,
        "category_counts": {"鸟类": 23, "植物": 5, "真菌": 4, "昆虫": 2},
        "location": location,
        "source_state": "live",
    }
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def spy_text(self, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (600, 400), (70, 130, 90)))
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy_text)

    image = plugin._render_page((800, 480), payload, {"showObservationMap": "true"}, now)

    assert image.size == (800, 480)
    assert "鸟类 23" in drawn_text
    assert "植物 5" in drawn_text
    assert "真菌 4" in drawn_text
    assert "昆虫 2" in drawn_text
    assert "附近类群" not in drawn_text
    assert "最近记录" not in drawn_text


def test_render_page_draws_observation_map_in_middle_slot(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    hero = plugin._observation_from_occurrence(occurrence(), location)
    bird = plugin._observation_from_occurrence(
        occurrence(key=124, speciesKey=2498305, kingdom="Animalia", **{"class": "Aves"}, family="Anatidae", genus="Oxyura", vernacularName="ruddy duck"),
        location,
    )
    payload = {
        "schema": "species-radar-v1",
        "observations": [hero, bird],
        "category_counts": {"植物": 1, "鸟类": 1},
        "location": location,
        "source_state": "live",
    }
    map_calls = []

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (600, 400), (70, 130, 90)))

    def fake_map(_settings, _device_config, observation, target_size):
        map_calls.append((observation["latitude"], observation["longitude"], target_size))
        return Image.new("RGB", (target_size[0] * 2, target_size[1] * 2), (28, 104, 150))

    monkeypatch.setattr(plugin, "_load_observation_map", fake_map)

    image = plugin._render_page((800, 480), payload, {"showObservationMap": "true"}, now, DummyDeviceConfig())
    map_pixels = {
        image.getpixel((x, y))
        for x in range(275, 465, 10)
        for y in range(360, 420, 8)
    }

    assert map_calls
    assert map_calls[0][0:2] == (37.55, -121.99)
    assert map_calls[0][2][0] <= 195
    assert (28, 104, 150) in map_pixels

def test_render_page_returns_nonblank_800x480_image(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    hero = plugin._observation_from_occurrence(occurrence(), location)
    bird = plugin._observation_from_occurrence(
        occurrence(key=124, speciesKey=2498305, kingdom="Animalia", **{"class": "Aves"}, family="Anatidae", genus="Oxyura", vernacularName="ruddy duck"),
        location,
    )
    payload = {
        "schema": "species-radar-v1",
        "observations": [hero, bird],
        "category_counts": {"植物": 1, "鸟类": 1},
        "location": location,
        "source_state": "live",
    }

    monkeypatch.setattr(plugin, "_download_image", lambda *_args, **_kwargs: Image.new("RGB", (600, 400), (70, 130, 90)))

    image = plugin._render_page((800, 480), payload, {}, now)
    diff = ImageChops.difference(image, Image.new("RGB", image.size, image.getpixel((0, 0))))

    assert image.size == (800, 480)
    assert diff.getbbox() is not None
    assert image.getpixel((220, 210)) != image.getpixel((0, 0))


# Prepared presentation-bank contract ---------------------------------------


def _make_species_bank(tmp_path, *, instance_uuid="species-instance", bucket="2026-07-12T06:00:00+00:00", settings=None):
    from plugins.species_radar.presentation_bank import (
        SpeciesPresentationBank,
        instance_profile_fingerprint,
        settings_fingerprint,
        settings_key,
    )

    settings = settings or bound_species_settings(instance_uuid)
    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    base = settings_fingerprint(settings, (800, 480), bucket, location)
    fingerprint = instance_profile_fingerprint(base, instance_uuid)
    return SpeciesPresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-photos",
        tmp_path / "presentation-maps",
        fingerprint=fingerprint,
        base_fingerprint=base,
        profile_settings_key=settings_key(settings),
        instance_uuid=instance_uuid,
        bucket_key=bucket,
    )


def _warm_species_bank(tmp_path, *, count=12, instance_uuid="species-instance"):
    bank = _make_species_bank(tmp_path, instance_uuid=instance_uuid)
    document, profile = bank.load_for_data()
    for index in range(1, count + 1):
        bank.ingest(
            profile,
            bank_observation(index),
            Image.new("RGB", (320, 240), (index * 13 % 255, 80, 30)),
            Image.new("RGB", (220, 90), (30, index * 11 % 255, 80)),
            fetched_at="2026-07-12T08:00:00+00:00",
        )
    current = bank.ensure_current(document, profile, bank.ready_records(profile, prune=True))
    bank.save(document)
    return bank, document, profile, current


def _species_profile(document, instance_uuid="species-instance"):
    return document["profiles"][document["instance_profiles"][instance_uuid]]


def _tree_digest(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _tree_snapshot(root):
    root = Path(root)
    if not root.exists():
        return {}
    snapshot = {".": ("dir", None)}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("link", os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("dir", None)
        elif path.is_file():
            snapshot[relative] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            snapshot[relative] = ("special", None)
    return snapshot


def test_species_manifest_mode_cadence_and_bank_limits_match_contract():
    from plugins.species_radar import presentation_bank

    manifest_path = Path(__file__).resolve().parents[1] / "src/plugins/species_radar/plugin-info.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["capabilities"]["supports_presentation_refresh"] is True
    assert SpeciesRadar({"id": "species_radar"}).presentation_mode({}) is PresentationMode.PREPARED_BANK
    assert DEFAULT_REFRESH_HOURS * 60 * 60 == 21600
    assert presentation_bank.READY_TARGET == 12
    assert presentation_bank.REFILL_THRESHOLD == 4
    assert presentation_bank.PHOTO_MAX_AGE_SECONDS == 30 * 24 * 60 * 60
    assert presentation_bank.PHOTO_MAX_FILES == 256
    assert presentation_bank.PHOTO_MAX_BYTES == 64 * 1024 * 1024
    assert presentation_bank.MAP_MAX_FILES == 64
    assert presentation_bank.MAP_MAX_BYTES == 64 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_OBJECT_BYTES == 12 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_DIMENSION == 8192
    assert presentation_bank.MEDIA_MAX_PIXELS == 32_000_000
    assert presentation_bank.MAX_STATE_BYTES == 4 * 1024 * 1024
    assert presentation_bank.MAX_PROFILES == 64
    assert presentation_bank.MAX_SEEN_IDS == 5000


def test_species_fingerprint_defaults_pixels_location_language_layout_and_exclusions():
    from plugins.species_radar.presentation_bank import settings_fingerprint

    location = {"latitude": 37.5485, "longitude": -121.9886, "name": "Fremont, CA"}
    explicit = {
        "locationSource": "weather",
        "radiusKm": 25,
        "lookbackDays": 365,
        "limit": 50,
        "refreshHours": 6,
        "includeFremont": True,
        "includeLuoyang": True,
        "luoyangRadiusKm": 25,
        "luoyangLookbackDays": 730,
        "luoyangLimit": 50,
        "showObservationMap": True,
        "observationMapZoom": 12,
        "googleMapType": "terrain",
        "language": "zh-CN",
        "layout": "default",
    }
    first = settings_fingerprint({}, (800, 480), "2026-07-12T06:00:00+00:00", location)
    second = settings_fingerprint(explicit, (800, 480), "2026-07-12T06:00:00+00:00", location)

    assert first == second
    for changed in (
        ({**explicit, "radiusKm": 30}, (800, 480), "2026-07-12T06:00:00+00:00", location),
        ({**explicit, "language": "en"}, (800, 480), "2026-07-12T06:00:00+00:00", location),
        ({**explicit, "layout": "compact"}, (800, 480), "2026-07-12T06:00:00+00:00", location),
        ({**explicit, "includeLuoyang": False}, (800, 480), "2026-07-12T06:00:00+00:00", location),
        ({**explicit, "luoyangLookbackDays": 365}, (800, 480), "2026-07-12T06:00:00+00:00", location),
        (explicit, (480, 800), "2026-07-12T06:00:00+00:00", location),
        (explicit, (800, 480), "2026-07-12T12:00:00+00:00", location),
        (explicit, (800, 480), "2026-07-12T06:00:00+00:00", {**location, "latitude": 34.6197}),
    ):
        assert first != settings_fingerprint(*changed)
    assert first == settings_fingerprint(
        {**explicit, "googleMapsApiKey": "secret", "forceRefresh": True, "_theme_render_only": True},
        (800, 480),
        "2026-07-12T06:00:00+00:00",
        location,
    )
    inherited = settings_fingerprint(
        {"radiusKm": 31, "limit": 61},
        (800, 480),
        "2026-07-12T06:00:00+00:00",
        location,
    )
    explicit_inheritance = settings_fingerprint(
        {
            "radiusKm": 31,
            "limit": 61,
            "luoyangRadiusKm": 31,
            "luoyangLimit": 61,
            "includeFremont": True,
            "includeLuoyang": True,
        },
        (800, 480),
        "2026-07-12T06:00:00+00:00",
        location,
    )
    assert inherited == explicit_inheritance


def test_species_instances_are_isolated_and_raw_json_cannot_spoof(tmp_path, monkeypatch):
    first = _make_species_bank(tmp_path, instance_uuid="first")
    second = _make_species_bank(tmp_path, instance_uuid="second")
    first_doc, first_profile = first.load_for_data()
    first.ingest(first_profile, bank_observation(1), Image.new("RGB", (80, 60), "red"), None)
    first.save(first_doc)
    second_doc, second_profile = second.load_for_data()
    second.ingest(second_profile, bank_observation(2), Image.new("RGB", (80, 60), "blue"), None)
    second.save(second_doc)
    state = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    assert state["instance_profiles"]["first"] != state["instance_profiles"]["second"]

    plugin = make_plugin(tmp_path)
    baseline = _tree_digest(tmp_path)
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *_args: plugin._fallback_payload({"name": "Fremont"}, "x"))
    spoof = {"_inkypi_presentation_instance_identity": {"instance_uuid": "first"}}
    plugin.generate_image(spoof, DummyDeviceConfig())
    assert _tree_digest(tmp_path) == baseline


def test_species_data_hydrates_without_advancing_bag_or_writing_displayed_context(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_species_settings()
    payload = {
        "source": "GBIF",
        "location": {"name": "Fremont, CA"},
        "location_summary": "Fremont, CA",
        "observations": [bank_observation(index) for index in range(1, 6)],
        "category_counts": {},
        "location_counts": {},
    }
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *_args: payload)
    monkeypatch.setattr(plugin, "_download_image_for_data", lambda *_args, **_kwargs: Image.new("RGB", (80, 60), "green"))
    monkeypatch.setattr(plugin, "_load_map_for_data", lambda *_args, **_kwargs: Image.new("RGB", (80, 40), "blue"))
    monkeypatch.setattr(plugin, "_render_page", lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"))
    monkeypatch.setattr(plugin, "_next_display_index", lambda *_args: pytest.fail("DATA advanced display bag"))
    monkeypatch.setattr(species_mod, "write_context", lambda *_args, **_kwargs: pytest.fail("DATA wrote displayed context"))

    image = plugin.generate_image(settings, DummyDeviceConfig())
    state = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    profile = _species_profile(state)

    assert image.size == (800, 480)
    assert len(profile["records"]) <= 12
    assert profile["seen_ids"] == []
    assert profile["pending_selection"] is None


def test_species_prepare_is_provider_free_and_context_commits_only_exact_receipt(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_species_settings()
    _warm_species_bank(tmp_path)
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *_args: pytest.fail("prepare fetched observations"))
    monkeypatch.setattr(plugin, "_download_provider_bytes", lambda *_args, **_kwargs: pytest.fail("prepare opened HTTP"))
    written = []
    monkeypatch.setattr(species_mod, "write_context", lambda plugin_id, payload, **kwargs: written.append((plugin_id, payload, kwargs)))

    prepared = plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=species_request("a" * 32),
        resolved_theme_context=species_theme("night"),
    )
    state_path = tmp_path / "presentation-state.json"
    pending_state = json.loads(state_path.read_text(encoding="utf-8"))
    pending = _species_profile(pending_state)["pending_selection"]
    pending_record = next(
        item for item in _species_profile(pending_state)["records"] if item["record_key"] == pending["record_key"]
    )

    assert prepared.changed is True
    assert prepared.image.info["inkypi_theme_mode"] == "night"
    assert written == []

    baseline = state_path.read_bytes()
    plugin.reconcile_presentation_receipt(settings, species_receipt("b" * 32))
    plugin.reconcile_presentation_receipt(bound_species_settings("foreign"), species_receipt("a" * 32))
    plugin.reconcile_presentation_receipt(settings, species_receipt("a" * 32, display="origin-display"))
    assert state_path.read_bytes() == baseline
    assert written == []

    plugin.reconcile_presentation_receipt(settings, species_receipt("a" * 32))
    committed = state_path.read_bytes()
    plugin.reconcile_presentation_receipt(settings, species_receipt("a" * 32))
    plugin.reconcile_presentation_receipt(
        settings,
        species_receipt("a" * 32, committed_at="2026-07-12T09:00:00+00:00"),
    )
    assert state_path.read_bytes() == committed
    assert len(written) == 1
    assert written[0][0] == "species_radar"
    assert written[0][1]["scientific_name"] == pending_record["observation"]["scientific_name"]
    final_profile = _species_profile(json.loads(committed.decode("utf-8")))
    assert final_profile["seen_ids"][-1] == pending_record["observation_id"]
    assert final_profile["displayed_context"]["observation_id"] == pending_record["observation_id"]


def test_species_banked_page_restores_recent_records_and_gallery_media(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    bank, _document, profile, current = _warm_species_bank(tmp_path, count=5)
    captured = {}

    def capture_render(_dimensions, payload, _settings, _now, _device_config=None):
        observations = payload["observations"]
        captured["ids"] = [item["gbif_key"] for item in observations]
        captured["photos"] = [
            plugin._download_image(item["image_url"], (80, 60))
            for item in observations
        ]
        return Image.new("RGB", (800, 480), "white")

    monkeypatch.setattr(plugin, "_render_page", capture_render)

    plugin._render_bank_selection(
        bank,
        profile,
        current,
        (800, 480),
        bound_species_settings(),
    )

    assert len(captured["ids"]) == 5
    assert captured["ids"][0] == next(
        record["observation"]["gbif_key"]
        for record in profile["records"]
        if record["record_key"] == current["record_key"]
    )
    assert len(set(captured["ids"])) == 5
    assert all(isinstance(photo, Image.Image) for photo in captured["photos"])


def test_species_pending_survives_restart_theme_location_and_bucket(tmp_path, monkeypatch):
    first = make_plugin(tmp_path)
    settings = bound_species_settings()
    _warm_species_bank(tmp_path)
    prepared = first.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=species_request("c" * 32),
        resolved_theme_context=species_theme("day"),
    )
    state_path = tmp_path / "presentation-state.json"
    pending = _species_profile(json.loads(state_path.read_text(encoding="utf-8")))["pending_selection"]

    restarted = make_plugin(tmp_path)
    monkeypatch.setattr(restarted, "_fetch_live_payload", lambda *_args: pytest.fail("restart used provider"))
    changed = bound_species_settings(latitude="34.6197", longitude="112.4540", locationName="Luoyang")
    second = restarted.prepare_presentation(
        changed,
        DummyDeviceConfig(),
        request=species_request("c" * 32),
        resolved_theme_context=species_theme("night"),
    )
    after = json.loads(state_path.read_text(encoding="utf-8"))
    pending_after = next(
        profile["pending_selection"]
        for profile in after["profiles"].values()
        if (profile.get("pending_selection") or {}).get("request_id") == "c" * 32
    )

    assert pending_after == pending
    assert prepared.image.info["inkypi_theme_mode"] == "day"
    assert second.image.info["inkypi_theme_mode"] == "night"
    assert second.image.info["inkypi_source_provenance"] == "stale_cache"


def test_species_location_and_bucket_mismatch_are_never_fresh(tmp_path):
    bank = _make_species_bank(tmp_path)
    document, profile = bank.load_for_data()
    record = bank.ingest(
        profile,
        bank_observation(1),
        Image.new("RGB", (80, 60), "green"),
        None,
        fetched_at=(datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(),
    )
    bank.save(document)
    assert bank.ready_records(profile, prune=False)[0]["provenance"] == "stale_cache"

    changed = _make_species_bank(
        tmp_path,
        bucket="2026-07-12T12:00:00+00:00",
        settings=bound_species_settings(latitude="34.6197", longitude="112.4540", locationName="Luoyang"),
    )
    _doc, changed_profile = changed.load_for_data()
    assert changed.ready_records(changed_profile, prune=False) == []
    assert record["bucket_key"] == "2026-07-12T06:00:00+00:00"


def test_species_media_limits_symlink_and_protected_cleanup_fail_closed(tmp_path):
    from plugins.species_radar import presentation_bank

    bank = _make_species_bank(tmp_path)
    document, profile = bank.load_for_data()
    with pytest.raises(RuntimeError, match="dimensions|pixels"):
        bank.ingest(
            profile,
            bank_observation(1),
            Image.new("RGB", (presentation_bank.MEDIA_MAX_DIMENSION + 1, 1), "red"),
            None,
        )
    record = bank.ingest(profile, bank_observation(2), Image.new("RGB", (80, 60), "blue"), None)
    current = bank.ensure_current(document, profile, bank.ready_records(profile, prune=False))
    bank.save(document)
    photo_path = bank.photo_path(record)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    photo_path.unlink()
    try:
        photo_path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")
    with pytest.raises(RuntimeError, match="regular|media|photo"):
        bank.load_photo(record)
    assert outside.read_bytes() == b"outside"

    photo_path.unlink()
    Image.new("RGB", (80, 60), "blue").save(photo_path)
    old = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
    os.utime(photo_path, (old, old))
    bank.cleanup(document, profile)
    assert current["record_key"] == record["record_key"]
    assert photo_path.is_file()


def test_species_cross_profile_admission_never_evicts_protected_media(tmp_path, monkeypatch):
    from plugins.species_radar import presentation_bank

    first = _make_species_bank(tmp_path, instance_uuid="first")
    first_document, first_profile = first.load_for_data()
    first_record = first.ingest(
        first_profile,
        bank_observation(1),
        Image.new("RGB", (80, 60), "red"),
        None,
    )
    first.ensure_current(first_document, first_profile, first.ready_records(first_profile, prune=False))
    first.save(first_document)
    protected_path = first.photo_path(first_record)

    second = _make_species_bank(tmp_path, instance_uuid="second")
    _second_document, second_profile = second.load_for_data()
    monkeypatch.setattr(presentation_bank, "PHOTO_MAX_FILES", 1)

    with pytest.raises(RuntimeError, match="protected|budget"):
        second.ingest(
            second_profile,
            bank_observation(2),
            Image.new("RGB", (80, 60), "blue"),
            None,
        )
    assert protected_path.is_file()


def test_species_theme_only_and_unbound_preview_do_not_mutate_cache_tree(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    _warm_species_bank(tmp_path)
    baseline = _tree_digest(tmp_path)
    monkeypatch.setattr(plugin, "_download_provider_bytes", lambda *_args, **_kwargs: pytest.fail("theme opened HTTP"))
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *_args: pytest.fail("theme fetched observations"))

    theme = plugin.generate_image(
        bound_species_settings(_theme_render_only=True),
        DummyDeviceConfig(),
    )
    assert theme.size == (800, 480)
    assert _tree_digest(tmp_path) == baseline

    preview_plugin = make_plugin(tmp_path)
    monkeypatch.setattr(preview_plugin, "_fetch_live_payload", lambda *_args: {
        "source": "GBIF",
        "location": {"name": "Fremont, CA"},
        "observations": [bank_observation(9)],
        "category_counts": {},
    })
    monkeypatch.setattr(preview_plugin, "_download_image", lambda *_args: Image.new("RGB", (80, 60), "green"))
    monkeypatch.setattr(preview_plugin, "_load_observation_map", lambda *_args: None)
    preview_plugin.generate_image({}, DummyDeviceConfig())
    assert _tree_digest(tmp_path) == baseline


def test_species_data_refill_caps_observations_photos_maps_and_continues(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_species_settings()
    payload = {
        "source": "GBIF",
        "location": {"name": "Fremont, CA"},
        "location_summary": "Fremont, CA",
        "observations": [bank_observation(index) for index in range(1, 21)],
        "category_counts": {},
        "location_counts": {},
    }
    photos = []
    maps = []
    monkeypatch.setattr(plugin, "_daily_payload", lambda *_args: payload)
    monkeypatch.setattr(
        plugin,
        "_download_image_for_data",
        lambda url, _size, **_kwargs: photos.append(url) or Image.new("RGB", (80, 60), "green"),
    )
    monkeypatch.setattr(
        plugin,
        "_load_map_for_data",
        lambda _settings, _device, observation, _size, **_kwargs: maps.append(observation["gbif_key"])
        or Image.new("RGB", (80, 40), "blue"),
    )
    monkeypatch.setattr(plugin, "_render_page", lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"))

    plugin.generate_image(settings, DummyDeviceConfig())
    first = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    first_profile = _species_profile(first)

    assert len(first_profile["records"]) == 1
    assert len(photos) == species_mod.MAX_PHOTO_FETCHES_PER_DATA_PASS
    assert len(maps) == species_mod.MAX_MAP_FETCHES_PER_DATA_PASS
    assert first_profile["seen_ids"] == []
    assert first_profile["refill_cursor"] == 1
    assert first_profile["refill_in_progress"] is True

    for _ in range(11):
        plugin.generate_image(settings, DummyDeviceConfig())
    second = json.loads((tmp_path / "presentation-state.json").read_text(encoding="utf-8"))
    second_profile = _species_profile(second)
    assert len(second_profile["records"]) == 12
    assert len(photos) == 12
    assert len(maps) == 12
    assert second_profile["refill_in_progress"] is False


@pytest.mark.parametrize("force_key", ["forceRefresh", "force_refresh"])
def test_species_force_refresh_attempts_provider_for_full_bank_without_consuming_selection(
    tmp_path,
    monkeypatch,
    force_key,
):
    plugin = make_plugin(tmp_path)
    settings = bound_species_settings()
    bank, _document, _profile, current = _warm_species_bank(tmp_path)
    monkeypatch.setattr(
        plugin,
        "_now_utc",
        lambda: datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        plugin,
        "_render_page",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    monkeypatch.setattr(
        plugin,
        "_daily_payload",
        lambda *_args: pytest.fail("ordinary full-bank rotation fetched provider"),
    )
    ordinary = plugin.generate_image(settings, DummyDeviceConfig())
    assert read_source_provenance(ordinary) is SourceProvenance.FRESH_CACHE
    calls = []
    payload = {
        "source": "GBIF",
        "source_state": "live",
        "cache_key": plugin._cache_key(settings, plugin._now_utc(), plugin._resolve_location(settings, DummyDeviceConfig())),
        "location": {"name": "Fremont, CA"},
        "observations": [bank_observation(99)],
        "category_counts": {},
        "location_counts": {},
    }
    monkeypatch.setattr(
        plugin,
        "_daily_payload",
        lambda *_args: calls.append("provider") or payload,
    )
    monkeypatch.setattr(
        plugin,
        "_download_image_for_data",
        lambda *_args, **_kwargs: Image.new("RGB", (80, 60), "green"),
    )
    monkeypatch.setattr(
        plugin,
        "_load_map_for_data",
        lambda *_args, **_kwargs: Image.new("RGB", (80, 40), "blue"),
    )

    image = plugin.generate_image({**settings, force_key: "true"}, DummyDeviceConfig())

    state = json.loads(bank.state_path.read_text(encoding="utf-8"))
    profile = _species_profile(state)
    assert calls == ["provider"]
    assert profile["last_provider_status"] == "success"
    assert datetime.fromisoformat(profile["last_provider_attempt_at"]).tzinfo is not None
    assert profile["current_selection"] == current
    assert profile["pending_selection"] is None
    assert any(record["observation"]["gbif_key"] == "99" for record in profile["records"])
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE


def test_species_force_refresh_provider_fallback_marks_warm_bank_stale_and_skips_cache(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = bound_species_settings()
    bank, _document, _profile, _current = _warm_species_bank(tmp_path)
    now = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(
        plugin,
        "_render_page",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )
    monkeypatch.setattr(
        plugin,
        "_daily_payload",
        lambda *_args: {
            "source": "GBIF",
            "source_state": "cache",
            "cache_key": plugin._cache_key(
                settings,
                now,
                plugin._resolve_location(settings, DummyDeviceConfig()),
            ),
            "location": {"name": "Fremont, CA"},
            "observations": [],
            "category_counts": {},
            "location_counts": {},
        },
    )

    image = plugin.generate_image(
        {**settings, "forceRefresh": "true"},
        DummyDeviceConfig(),
    )

    state = json.loads(bank.state_path.read_text(encoding="utf-8"))
    assert _species_profile(state)["last_provider_status"] == "error"
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True


def test_species_data_deadline_is_checked_between_every_provider_operation(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_species_settings()
    payload = {
        "source": "GBIF",
        "location": {"name": "Fremont, CA"},
        "observations": [bank_observation(index) for index in range(1, 20)],
        "category_counts": {},
    }
    clock = {"value": 0.0}
    calls = []
    monkeypatch.setattr(plugin, "_daily_payload", lambda *_args: payload)
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])

    def slow_photo(url, _size, *, deadline=None):
        assert deadline is not None
        assert clock["value"] < deadline
        calls.append(url)
        clock["value"] += species_mod.MAX_DATA_SECONDS + 1.0
        return Image.new("RGB", (80, 60), "green")

    monkeypatch.setattr(plugin, "_download_image_for_data", slow_photo)
    monkeypatch.setattr(plugin, "_load_map_for_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_render_page", lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"))
    baseline = _tree_snapshot(tmp_path)

    with pytest.raises(RuntimeError, match="deadline"):
        plugin.generate_image(settings, DummyDeviceConfig())

    assert len(calls) == species_mod.MAX_PHOTO_FETCHES_PER_DATA_PASS
    assert clock["value"] <= species_mod.MAX_DATA_SECONDS + 60.0
    assert _tree_snapshot(tmp_path) == baseline


def test_species_failed_photo_and_map_attempts_are_still_bounded(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    payload = {
        "source": "GBIF",
        "location": {"name": "Fremont, CA"},
        "observations": [bank_observation(index) for index in range(1, 20)],
        "category_counts": {},
    }
    attempts = {"photo": 0, "map": 0}
    monkeypatch.setattr(plugin, "_daily_payload", lambda *_args: payload)

    def fail_photo(*_args, **_kwargs):
        attempts["photo"] += 1
        raise RuntimeError("photo offline")

    def fail_map(*_args, **_kwargs):
        attempts["map"] += 1
        raise RuntimeError("map offline")

    monkeypatch.setattr(plugin, "_download_image_for_data", fail_photo)
    monkeypatch.setattr(plugin, "_load_map_for_data", fail_map)
    monkeypatch.setattr(plugin, "_render_page", lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"))

    with pytest.raises(RuntimeError, match="bank|unavailable"):
        plugin.generate_image(bound_species_settings(), DummyDeviceConfig())
    assert attempts == {
        "photo": species_mod.MAX_PHOTO_FETCHES_PER_DATA_PASS,
        "map": species_mod.MAX_MAP_FETCHES_PER_DATA_PASS,
    }


class SpeciesApprovedTarget:
    def __init__(self, url, *, host="api.gbif.org", address="93.184.216.34"):
        self.normalized_url = url
        self.scheme = urlparse(url).scheme
        self.hostname = host
        self.port = 443
        self.addresses = (address,)
        self.authority = host


class SpeciesRedirectResponse:
    def __init__(self, url, status=200, *, location=None, chunks=(b"{}",)):
        self.url = url
        self.status_code = status
        self.headers = {} if location is None else {"Location": location}
        self._chunks = chunks
        self.body_read = False

    def iter_content(self, chunk_size):
        del chunk_size
        self.body_read = True
        yield from self._chunks

    def close(self):
        return None


def test_species_redirect_private_and_unexpected_final_are_rejected_before_body(monkeypatch):
    plugin = SpeciesRadar({"id": "species_radar"})
    first = "https://api.gbif.org/v1/occurrence/search"
    redirect = SpeciesRedirectResponse(first, 302, location="http://127.0.0.1/private")
    final = SpeciesRedirectResponse("http://127.0.0.1/final", 200)
    responses = iter((redirect, final))
    calls = []

    class Policy:
        def resolve_and_validate(self, url):
            if "127.0.0.1" in url:
                raise RuntimeError("private address")
            return SpeciesApprovedTarget(url)

    monkeypatch.setattr(species_mod, "get_ssrf_policy", lambda: Policy())
    monkeypatch.setattr(
        plugin,
        "_request_approved_target",
        lambda approved, **kwargs: calls.append((approved, kwargs)) or next(responses),
    )

    with pytest.raises(RuntimeError, match="private"):
        plugin._download_provider_bytes(first, source="gbif", max_bytes=1024, timeout=5)
    assert len(calls) == 1
    assert redirect.body_read is False

    monkeypatch.setattr(plugin, "_request_approved_target", lambda *_args, **_kwargs: final)
    with pytest.raises(RuntimeError, match="private"):
        plugin._download_provider_bytes(first, source="gbif", max_bytes=1024, timeout=5)
    assert final.body_read is False


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "::ffff:127.0.0.1"])
def test_species_provider_target_rejects_private_dns_and_wrong_source(address):
    from plugins.species_radar.presentation_bank import validate_species_target

    with pytest.raises(RuntimeError, match="public|address"):
        validate_species_target(
            SpeciesApprovedTarget("https://api.gbif.org/v1/species/1", address=address),
            "gbif",
        )
    with pytest.raises(RuntimeError, match="source|authority"):
        validate_species_target(
            SpeciesApprovedTarget(
                "https://maps.googleapis.com/maps/api/staticmap",
                host="maps.googleapis.com",
            ),
            "gbif",
        )


def test_species_json_and_media_are_bounded_before_decode(tmp_path, monkeypatch):
    from plugins.species_radar import presentation_bank

    plugin = make_plugin(tmp_path)
    response = SpeciesRedirectResponse(
        "https://api.gbif.org/v1/occurrence/search",
        chunks=(b"{" + b"x" * presentation_bank.MAX_STATE_BYTES, b"}"),
    )
    monkeypatch.setattr(plugin, "_request_approved_target", lambda *_args, **_kwargs: response)
    with pytest.raises(RuntimeError, match="budget|size|exceeds"):
        plugin._get_json("https://api.gbif.org/v1/occurrence/search")

    bank = _make_species_bank(tmp_path)
    state_path = bank.state_path
    state_path.write_bytes(b"{" + b"x" * presentation_bank.MAX_STATE_BYTES + b"}")
    with pytest.raises(RuntimeError, match="size"):
        bank.load_for_data()


def test_species_old_location_fallback_cannot_be_promoted_as_fresh_data(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_species_settings()
    old = {
        "schema": "species-radar-v2",
        "cache_key": "old-location-and-bucket",
        "source_state": "cache",
        "source": "GBIF",
        "location": {"name": "Luoyang"},
        "location_summary": "Luoyang",
        "observations": [bank_observation(1, location_name="Luoyang", bucket="2026-07-11T18:00:00+00:00")],
        "category_counts": {},
    }
    monkeypatch.setattr(plugin, "_daily_payload", lambda *_args: old)
    monkeypatch.setattr(plugin, "_download_image_for_data", lambda *_args, **_kwargs: Image.new("RGB", (80, 60), "green"))
    monkeypatch.setattr(plugin, "_load_map_for_data", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="unavailable|fresh|bank"):
        plugin.generate_image(settings, DummyDeviceConfig())


def test_species_cold_stateless_fallback_is_explicit_placeholder(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")))

    image = plugin.generate_image({}, DummyDeviceConfig())

    assert image.info["inkypi_source_provenance"] == "placeholder"
    assert not (tmp_path / "daily.json").exists()


def test_species_data_recovers_exact_protected_media_or_leaves_state_atomic(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_species_settings()
    monkeypatch.setattr(
        plugin,
        "_now_utc",
        lambda: datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc),
    )
    bank, _document, profile, current = _warm_species_bank(tmp_path)
    record = next(item for item in profile["records"] if item["record_key"] == current["record_key"])
    bank.photo_path(record).unlink()
    monkeypatch.setattr(plugin, "_daily_payload", lambda *_args: pytest.fail("recovery refreshed source pool"))
    monkeypatch.setattr(plugin, "_download_image_for_data", lambda *_args, **_kwargs: Image.new("RGB", (320, 240), "purple"))
    monkeypatch.setattr(plugin, "_load_map_for_data", lambda *_args, **_kwargs: Image.new("RGB", (220, 90), "blue"))
    monkeypatch.setattr(plugin, "_render_page", lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"))

    plugin.generate_image(settings, DummyDeviceConfig())
    after = json.loads(bank.state_path.read_text(encoding="utf-8"))
    assert _species_profile(after)["current_selection"] == current
    assert bank.photo_path(record).is_file()

    bank.photo_path(record).unlink()
    baseline = bank.state_path.read_bytes()
    monkeypatch.setattr(plugin, "_download_image_for_data", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="protected|recovery"):
        plugin.generate_image(settings, DummyDeviceConfig())
    assert bank.state_path.read_bytes() == baseline


def test_species_state_symlink_is_not_followed_or_replaced(tmp_path):
    bank = _make_species_bank(tmp_path)
    outside = tmp_path / "outside-state.json"
    outside.write_text('{"sentinel":true}', encoding="utf-8")
    try:
        bank.state_path.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")

    with pytest.raises(RuntimeError, match="safe|regular|state|symbolic"):
        bank.load_for_data()
    assert outside.read_text(encoding="utf-8") == '{"sentinel":true}'


def test_species_vernacular_symlink_is_never_followed_or_replaced(tmp_path):
    plugin = make_plugin(tmp_path)
    outside = tmp_path / "outside-vernacular.json"
    outside.write_text('{"sentinel":true}', encoding="utf-8")
    target = plugin._vernacular_cache_path()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")

    assert plugin._read_vernacular_cache() == {}
    with pytest.raises(RuntimeError, match="safe|regular|state|symbolic"):
        plugin._write_json(target, {"unsafe": True})
    assert target.is_symlink()
    assert outside.read_text(encoding="utf-8") == '{"sentinel":true}'


def test_species_ready_rejects_missing_photo_url_or_media(tmp_path):
    bank = _make_species_bank(tmp_path)
    _document, profile = bank.load_for_data()

    missing_url = bank_observation(1)
    missing_url["image_url"] = ""
    with pytest.raises(RuntimeError, match="photo|URL|media"):
        bank.ingest(profile, missing_url, None, None)
    with pytest.raises(RuntimeError, match="photo|media"):
        bank.ingest(profile, bank_observation(2), None, None)
    assert bank.ready_records(profile, prune=False) == []


def test_species_ingest_deadline_crossing_rolls_back_profile_and_media(tmp_path, monkeypatch):
    bank = _make_species_bank(tmp_path)
    _document, profile = bank.load_for_data()
    baseline_profile = json.loads(json.dumps(profile))
    baseline_tree = _tree_snapshot(tmp_path)
    clock = {"value": 0.0}
    original_encode = bank._encode_image

    def delayed_encode(image):
        payload = original_encode(image)
        clock["value"] = 76.0
        return payload

    def check_deadline():
        if clock["value"] >= 75.0:
            raise RuntimeError("deadline exhausted")

    monkeypatch.setattr(bank, "_encode_image", delayed_encode)
    with pytest.raises(RuntimeError, match="deadline"):
        bank.ingest(
            profile,
            bank_observation(1),
            Image.new("RGB", (80, 60), "green"),
            Image.new("RGB", (80, 40), "blue"),
            deadline_check=check_deadline,
        )
    assert profile == baseline_profile
    assert _tree_snapshot(tmp_path) == baseline_tree


def test_species_photo_decode_cannot_cross_data_deadline(monkeypatch):
    plugin = SpeciesRadar({"id": "species_radar"})
    clock = {"value": 0.0}
    buffer = BytesIO()
    Image.new("RGB", (80, 60), "green").save(buffer, "PNG")

    def delayed_download(*_args, **_kwargs):
        clock["value"] = 76.0
        return buffer.getvalue()

    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    monkeypatch.setattr(plugin, "_download_provider_bytes", delayed_download)
    with pytest.raises(RuntimeError, match="deadline"):
        plugin._download_image_for_data(
            "https://inaturalist-open-data.s3.amazonaws.com/photos/1/medium.jpg",
            (800, 480),
            deadline=75.0,
        )


def test_species_photo_safe_decode_deadline_crossing_skips_rgb_convert(monkeypatch):
    plugin = SpeciesRadar({"id": "species_radar"})
    clock = {"value": 0.0}
    convert_calls = []

    class DecodedImage:
        def convert(self, mode):
            convert_calls.append(mode)
            return Image.new("RGB", (2, 2), "green")

    def delayed_safe_open(*_args, **_kwargs):
        clock["value"] = 76.0
        return DecodedImage()

    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    monkeypatch.setattr(plugin, "_download_provider_bytes", lambda *_args, **_kwargs: b"image")
    monkeypatch.setattr(species_mod, "safe_open_image", delayed_safe_open)

    with pytest.raises(RuntimeError, match="deadline"):
        plugin._download_image_for_data(
            "https://inaturalist-open-data.s3.amazonaws.com/photos/1/medium.jpg",
            (800, 480),
            deadline=75.0,
        )

    assert convert_calls == []


def test_species_data_photo_rejects_disallowed_bmp(monkeypatch):
    plugin = SpeciesRadar({"id": "species_radar"})
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "green").save(buffer, "BMP")
    monkeypatch.setattr(
        plugin,
        "_download_provider_bytes",
        lambda *_args, **_kwargs: buffer.getvalue(),
    )

    with pytest.raises(RuntimeError, match="decode|format|safety"):
        plugin._download_image_for_data(
            "https://inaturalist-open-data.s3.amazonaws.com/photos/1/medium.jpg",
            (800, 480),
            deadline=time.monotonic() + 20,
        )


def test_species_data_map_rejects_disallowed_bmp(monkeypatch):
    plugin = SpeciesRadar({"id": "species_radar"})
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(buffer, "BMP")
    monkeypatch.setattr(plugin, "_google_maps_api_key", lambda *_args: "test-key")
    monkeypatch.setattr(
        plugin,
        "_google_observation_map_url",
        lambda *_args: "https://maps.googleapis.com/maps/api/staticmap",
    )
    monkeypatch.setattr(
        plugin,
        "_download_provider_bytes",
        lambda *_args, **_kwargs: buffer.getvalue(),
    )

    with pytest.raises(RuntimeError, match="decode|format|safety"):
        plugin._load_map_for_data(
            {},
            DummyDeviceConfig(),
            bank_observation(1),
            (800, 480),
            deadline=time.monotonic() + 20,
        )


def test_species_map_safe_decode_deadline_crossing_skips_rgb_convert(monkeypatch):
    plugin = SpeciesRadar({"id": "species_radar"})
    clock = {"value": 0.0}
    convert_calls = []

    class DecodedImage:
        def convert(self, mode):
            convert_calls.append(mode)
            return Image.new("RGB", (2, 2), "blue")

    def delayed_safe_open(*_args, **_kwargs):
        clock["value"] = 76.0
        return DecodedImage()

    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    monkeypatch.setattr(plugin, "_google_maps_api_key", lambda *_args: "test-key")
    monkeypatch.setattr(
        plugin,
        "_google_observation_map_url",
        lambda *_args: "https://maps.googleapis.com/maps/api/staticmap",
    )
    monkeypatch.setattr(plugin, "_download_provider_bytes", lambda *_args, **_kwargs: b"image")
    monkeypatch.setattr(species_mod, "safe_open_image", delayed_safe_open)

    with pytest.raises(RuntimeError, match="deadline"):
        plugin._load_map_for_data(
            {},
            DummyDeviceConfig(),
            bank_observation(1),
            (800, 480),
            deadline=75.0,
        )

    assert convert_calls == []


def test_species_photo_success_map_failure_is_fully_atomic(tmp_path, monkeypatch):
    bank = _make_species_bank(tmp_path)
    _document, profile = bank.load_for_data()
    baseline_profile = json.loads(json.dumps(profile))
    baseline_tree = _tree_snapshot(tmp_path)
    monkeypatch.setattr(
        bank.maps,
        "put_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("map write failed")),
    )

    with pytest.raises(Exception, match="map write failed"):
        bank.ingest(
            profile,
            bank_observation(1),
            Image.new("RGB", (80, 60), "green"),
            Image.new("RGB", (80, 40), "blue"),
        )
    assert profile == baseline_profile
    assert _tree_snapshot(tmp_path) == baseline_tree


def test_species_failed_admission_restores_evicted_victims(tmp_path, monkeypatch):
    from plugins.species_radar import presentation_bank

    bank = _make_species_bank(tmp_path)
    document, profile = bank.load_for_data()
    bank.ingest(profile, bank_observation(1), Image.new("RGB", (80, 60), "red"), None)
    bank.save(document)
    baseline_profile = json.loads(json.dumps(profile))
    baseline_tree = _tree_snapshot(tmp_path)
    monkeypatch.setattr(presentation_bank, "PHOTO_MAX_FILES", 1)
    original_put = bank.photos.put_bytes

    def fail_new(key, payload, *, suffix=""):
        if key != profile["records"][0]["photo_key"]:
            raise OSError("new write failed")
        return original_put(key, payload, suffix=suffix)

    monkeypatch.setattr(bank.photos, "put_bytes", fail_new)
    with pytest.raises(Exception, match="new write failed"):
        bank.ingest(profile, bank_observation(2), Image.new("RGB", (80, 60), "blue"), None)
    assert profile == baseline_profile
    assert _tree_snapshot(tmp_path) == baseline_tree


def test_species_media_root_symlink_never_touches_external_sentinel(tmp_path):
    outside = tmp_path / "outside-media"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"unchanged")
    photo_root = tmp_path / "presentation-photos"
    try:
        photo_root.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink unavailable")
    bank = _make_species_bank(tmp_path)
    _document, profile = bank.load_for_data()

    with pytest.raises(RuntimeError, match="safe|root|link|reparse|directory"):
        bank.ingest(profile, bank_observation(1), Image.new("RGB", (80, 60), "green"), None)
    assert sentinel.read_bytes() == b"unchanged"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel.bin"]


def test_species_admission_counts_every_regular_file(tmp_path, monkeypatch):
    from plugins.species_radar import presentation_bank

    bank = _make_species_bank(tmp_path)
    _document, profile = bank.load_for_data()
    bank.photo_dir.mkdir(parents=True)
    (bank.photo_dir / "unexpected.bin").write_bytes(b"counts-too")
    monkeypatch.setattr(presentation_bank, "PHOTO_MAX_FILES", 1)

    bank.ingest(profile, bank_observation(1), Image.new("RGB", (80, 60), "green"), None)
    assert not (bank.photo_dir / "unexpected.bin").exists()
    assert len([path for path in bank.photo_dir.iterdir() if path.is_file()]) == 1


def test_species_unbound_preview_and_cold_theme_create_no_paths(tmp_path, monkeypatch):
    preview_root = tmp_path / "preview-cold"
    monkeypatch.setenv("INKYPI_SPECIES_RADAR_CACHE", str(preview_root))
    plugin = SpeciesRadar({"id": "species_radar"})
    payload = {
        "schema": "species-radar-v2",
        "source": "GBIF",
        "location": {"name": "Fremont, CA"},
        "observations": [bank_observation(1)],
        "category_counts": {},
    }
    buffer = BytesIO()
    Image.new("RGB", (80, 60), "green").save(buffer, "PNG")
    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda *_args: payload)
    monkeypatch.setattr(plugin, "_download_provider_bytes", lambda *_args, **_kwargs: buffer.getvalue())
    before = _tree_snapshot(preview_root)
    image = plugin.generate_image({}, DummyDeviceConfig())
    assert image.size == (800, 480)
    assert _tree_snapshot(preview_root) == before == {}

    theme_root = tmp_path / "theme-cold"
    monkeypatch.setenv("INKYPI_SPECIES_RADAR_CACHE", str(theme_root))
    cold = SpeciesRadar({"id": "species_radar"})
    theme_before = _tree_snapshot(theme_root)
    with pytest.raises(RuntimeError, match="warm|cold|bank"):
        cold.generate_image(
            bound_species_settings(_theme_render_only=True),
            DummyDeviceConfig(),
        )
    assert _tree_snapshot(theme_root) == theme_before == {}


def test_species_theme_only_recomputes_six_hour_freshness(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bucket = now.replace(hour=(now.hour // 6) * 6, minute=0, second=0).isoformat()
    settings = bound_species_settings()
    bank = _make_species_bank(tmp_path, bucket=bucket, settings=settings)
    document, profile = bank.load_for_data()
    bank.ingest(
        profile,
        bank_observation(1, bucket=bucket),
        Image.new("RGB", (80, 60), "green"),
        None,
        fetched_at=now.isoformat(),
    )
    bank.ensure_current(document, profile, bank.ready_records(profile, prune=False))
    bank.save(document)
    plugin = make_plugin(tmp_path)
    clock = {"now": now}
    monkeypatch.setattr(plugin, "_now_utc", lambda: clock["now"])
    monkeypatch.setattr(plugin, "_render_page", lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"))

    fresh = plugin.generate_image(
        bound_species_settings(_theme_render_only=True),
        DummyDeviceConfig(),
    )
    clock["now"] = now + timedelta(hours=7)
    stale = plugin.generate_image(
        bound_species_settings(_theme_render_only=True),
        DummyDeviceConfig(),
    )

    assert fresh.info["inkypi_source_provenance"] == "fresh_cache"
    assert stale.info["inkypi_source_provenance"] == "stale_cache"


def test_species_ensure_current_save_deadline_is_atomic(tmp_path):
    bank = _make_species_bank(tmp_path)
    document, profile = bank.load_for_data()
    bank.ingest(
        profile,
        bank_observation(1),
        Image.new("RGB", (80, 60), "green"),
        None,
    )
    bank.save(document)
    ready = bank.ready_records(profile, prune=False)
    state_before = bank.state_path.read_bytes()
    profile_before = json.loads(json.dumps(profile))
    document_before = json.loads(json.dumps(document))
    tree_before = _tree_snapshot(tmp_path)
    checks = {"count": 0}

    def cross_after_save():
        checks["count"] += 1
        if checks["count"] >= 5:
            raise RuntimeError("deadline exhausted after ensure save")

    with pytest.raises(RuntimeError, match="deadline"):
        bank.ensure_current(
            document,
            profile,
            ready,
            deadline_check=cross_after_save,
        )
    assert bank.state_path.read_bytes() == state_before
    assert profile == profile_before
    assert document == document_before
    assert _tree_snapshot(tmp_path) == tree_before


def test_species_selection_decode_deadline_returns_no_record(tmp_path, monkeypatch):
    bank, _document, profile, current = _warm_species_bank(tmp_path, count=1)
    state_before = bank.state_path.read_bytes()
    tree_before = _tree_snapshot(tmp_path)
    clock = {"value": 0.0}
    original_read = bank._read_media_payload

    def delayed_read(*args, **kwargs):
        payload = original_read(*args, **kwargs)
        clock["value"] = 76.0
        return payload

    def check():
        if clock["value"] >= 75.0:
            raise RuntimeError("deadline exhausted during selection decode")

    monkeypatch.setattr(bank, "_read_media_payload", delayed_read)
    with pytest.raises(RuntimeError, match="deadline"):
        bank.selection_record(
            profile,
            current,
            load_media=True,
            deadline_check=check,
        )
    assert bank.state_path.read_bytes() == state_before
    assert _tree_snapshot(tmp_path) == tree_before


def test_species_render_deadline_never_returns_image_or_mutates_state(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    bank, _document, profile, current = _warm_species_bank(tmp_path, count=1)
    state_before = bank.state_path.read_bytes()
    tree_before = _tree_snapshot(tmp_path)
    clock = {"value": 0.0}

    def delayed_render(*_args, **_kwargs):
        clock["value"] = 76.0
        return Image.new("RGB", (800, 480), "white")

    def check():
        if clock["value"] >= 75.0:
            raise RuntimeError("deadline exhausted during render")

    monkeypatch.setattr(plugin, "_render_page", delayed_render)
    with pytest.raises(RuntimeError, match="deadline"):
        plugin._render_bank_selection(
            bank,
            profile,
            current,
            (800, 480),
            bound_species_settings(),
            deadline_check=check,
        )
    assert bank.state_path.read_bytes() == state_before
    assert _tree_snapshot(tmp_path) == tree_before


def _expired_species_bank_with_photo_and_map(tmp_path):
    bank = _make_species_bank(tmp_path)
    document, profile = bank.load_for_data()
    bank.ingest(
        profile,
        bank_observation(1),
        Image.new("RGB", (80, 60), "green"),
        Image.new("RGB", (80, 40), "blue"),
        fetched_at=(datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
    )
    bank.save(document)
    return bank, document, profile


@pytest.mark.parametrize("crossing_unlink", [1, 2])
def test_species_cleanup_unlink_deadline_restores_everything(
    tmp_path,
    monkeypatch,
    crossing_unlink,
):
    bank, document, profile = _expired_species_bank_with_photo_and_map(tmp_path)
    state_before = bank.state_path.read_bytes()
    profile_before = json.loads(json.dumps(profile))
    document_before = json.loads(json.dumps(document))
    tree_before = _tree_snapshot(tmp_path)
    clock = {"value": 0.0}
    unlinks = {"count": 0}
    original_unlink = bank._safe_unlink

    def delayed_unlink(path, root):
        original_unlink(path, root)
        unlinks["count"] += 1
        if unlinks["count"] == crossing_unlink:
            clock["value"] = 76.0

    def check():
        if clock["value"] >= 75.0:
            raise RuntimeError("deadline exhausted during cleanup unlink")

    monkeypatch.setattr(bank, "_safe_unlink", delayed_unlink)
    with pytest.raises(RuntimeError, match="deadline"):
        bank.cleanup(document, profile, deadline_check=check)
    assert bank.state_path.read_bytes() == state_before
    assert profile == profile_before
    assert document == document_before
    assert _tree_snapshot(tmp_path) == tree_before


def test_species_cleanup_save_failure_restores_media_state_and_document(tmp_path, monkeypatch):
    bank, document, profile = _expired_species_bank_with_photo_and_map(tmp_path)
    state_before = bank.state_path.read_bytes()
    profile_before = json.loads(json.dumps(profile))
    document_before = json.loads(json.dumps(document))
    tree_before = _tree_snapshot(tmp_path)
    original_save = bank.save

    def save_then_fail(*args, **kwargs):
        original_save(*args, **kwargs)
        raise RuntimeError("save failed after publish")

    monkeypatch.setattr(bank, "save", save_then_fail)
    with pytest.raises(RuntimeError, match="save failed"):
        bank.cleanup(document, profile, deadline_check=lambda: None)
    assert bank.state_path.read_bytes() == state_before
    assert profile == profile_before
    assert document == document_before
    assert _tree_snapshot(tmp_path) == tree_before

def test_live_data_budget_fits_dual_location_provider_pipeline():
    assert species_mod.MAX_DATA_SECONDS == 150


def test_live_data_budget_keeps_optional_common_name_enrichment_off_critical_path():
    assert species_mod.MAX_COMMON_NAME_ENRICHMENTS_PER_DATA_PASS == 0


def test_live_data_pass_matches_legacy_single_observation_media_workload():
    assert species_mod.MAX_OBSERVATIONS_PER_DATA_PASS == 1
    assert species_mod.MAX_PHOTO_FETCHES_PER_DATA_PASS == 1
    assert species_mod.MAX_MAP_FETCHES_PER_DATA_PASS == 1


def test_live_payload_enriches_only_the_records_one_data_pass_can_ingest(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    observations = [bank_observation(index) for index in range(1, 11)]
    captured = []
    monkeypatch.setattr(
        plugin,
        "_location_specs",
        lambda *_args: [{
            "id": "primary",
            "location": {"name": "Fremont"},
            "radius_km": 25,
            "lookback_days": 365,
        }],
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_location_payload",
        lambda *_args: {
            "observations": observations,
            "location": {"id": "primary", "name": "Fremont"},
            "total_count": len(observations),
        },
    )
    monkeypatch.setattr(
        plugin,
        "_enrich_common_names",
        lambda values: captured.extend(values),
    )

    plugin._fetch_live_payload(
        {},
        datetime(2026, 7, 15, tzinfo=timezone.utc),
        {"name": "Fremont"},
    )

    assert len(captured) == species_mod.MAX_COMMON_NAME_ENRICHMENTS_PER_DATA_PASS
