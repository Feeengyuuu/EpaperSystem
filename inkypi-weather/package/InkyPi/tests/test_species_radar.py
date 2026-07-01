import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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


def test_palette_uses_comic_process_color_tokens(tmp_path):
    plugin = make_plugin(tmp_path)
    palette = plugin._palette({})

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
    assert plugin._preferred_font_families()[0] == MICROSOFT_YAHEI_FONT

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


def test_palette_supports_explicit_comic_night_theme(tmp_path):
    plugin = make_plugin(tmp_path)
    palette = plugin._palette({"themeMode": "night"})

    assert palette["night"] is True
    assert palette["paper"] == COMIC_NIGHT_PAPER
    assert palette["ink"] == COMIC_NIGHT_INK
    assert palette["accent"] == COMIC_CYAN


def test_palette_auto_switches_by_local_hour_without_playlist_duplication(tmp_path):
    plugin = make_plugin(tmp_path)
    day = plugin._palette({"themeMode": "auto", "timezone": "UTC"}, datetime(2026, 6, 27, 12, tzinfo=timezone.utc))
    night = plugin._palette({"themeMode": "auto", "timezone": "UTC"}, datetime(2026, 6, 27, 20, tzinfo=timezone.utc))

    assert day["night"] is False
    assert day["paper"] == COMIC_PAPER
    assert night["night"] is True
    assert night["paper"] == COMIC_NIGHT_PAPER

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

    image = plugin._render_page((800, 480), payload, {}, now)

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

    image = plugin._render_page((800, 480), payload, {}, now)

    assert image.size == (800, 480)
    assert calls[0][2] == TITLE_WORDMARK_DISPLAY_SIZE


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
